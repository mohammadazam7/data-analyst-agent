"""Benchmark suites: questions with gold queries, loaded from TOML.

A suite file has a ``name``, the ``schemas`` the agent may read, and a list of
``[[question]]`` tables, each with an ``id``, the ``question`` in plain words, the
``gold`` SQL and optional ``tags`` and ``ordered`` (true when the question asks for an
order, so row order is scored). See ``benchmarks/commerce/questions.toml``.
"""

from __future__ import annotations

import time
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_analyst_agent.agent import AgentError, ask
from data_analyst_agent.grounding import DbtDocs
from data_analyst_agent.guardrails import GuardrailError, Policy, check
from data_analyst_agent.providers import Message, Provider, ProviderError
from data_analyst_agent.scoring import Comparison, compare
from data_analyst_agent.warehouse import DuckDBWarehouse


class SuiteError(ValueError):
    pass


@dataclass(frozen=True)
class Question:
    id: str
    question: str
    gold: str
    tags: tuple[str, ...] = ()
    ordered: bool = False


@dataclass(frozen=True)
class Suite:
    name: str
    schemas: tuple[str, ...]
    questions: tuple[Question, ...]

    def policy(self, max_rows: int = 1000) -> Policy:
        return Policy.for_schemas(self.schemas, max_rows=max_rows)


def _question(raw: Any, index: int) -> Question:
    if not isinstance(raw, dict):
        raise SuiteError(f"question {index} is not a table")
    for key in ("id", "question", "gold"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise SuiteError(f"question {index} needs a non-empty {key!r}")
    tags = raw.get("tags", [])
    if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
        raise SuiteError(f"question {raw['id']}: tags must be a list of strings")
    ordered = raw.get("ordered", False)
    if not isinstance(ordered, bool):
        raise SuiteError(f"question {raw['id']}: ordered must be true or false")
    unknown = set(raw) - {"id", "question", "gold", "tags", "ordered"}
    if unknown:
        raise SuiteError(f"question {raw['id']}: unknown keys {sorted(unknown)}")
    return Question(raw["id"], raw["question"].strip(), raw["gold"].strip(), tuple(tags), ordered)


def parse_suite(data: dict[str, Any]) -> Suite:
    name = data.get("name")
    schemas = data.get("schemas")
    questions = data.get("question")
    if not isinstance(name, str) or not name:
        raise SuiteError("the suite needs a name")
    if not isinstance(schemas, list) or not schemas or not all(isinstance(s, str) for s in schemas):
        raise SuiteError("the suite needs a non-empty list of schemas")
    if not isinstance(questions, list) or not questions:
        raise SuiteError("the suite has no [[question]] entries")
    parsed = tuple(_question(q, i) for i, q in enumerate(questions, start=1))
    ids = [q.id for q in parsed]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise SuiteError(f"duplicate question ids: {duplicates}")
    suite = Suite(name, tuple(schemas), parsed)
    check_gold(suite)
    return suite


def load_suite(path: Path) -> Suite:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise SuiteError(f"{path}: {error}") from error
    return parse_suite(data)


def check_gold(suite: Suite, questions: Sequence[Question] | None = None) -> None:
    """Every gold query must pass the same guardrails the agent's queries do."""
    policy = suite.policy()
    for q in questions or suite.questions:
        try:
            check(q.gold, policy)
        except GuardrailError as error:
            raise SuiteError(f"gold query of {q.id} is refused: {error}") from error


# Running a suite.


@dataclass(frozen=True)
class QuestionResult:
    id: str
    tags: tuple[str, ...]
    correct: bool
    reason: str
    seconds: float
    attempts: int
    sql: str | None


@dataclass(frozen=True)
class SuiteResult:
    suite: str
    results: tuple[QuestionResult, ...]

    @property
    def accuracy(self) -> float:
        return sum(r.correct for r in self.results) / len(self.results)

    def by_tag(self) -> dict[str, tuple[int, int]]:
        """Correct and total per tag."""
        counts: dict[str, list[int]] = {}
        for r in self.results:
            for tag in r.tags:
                entry = counts.setdefault(tag, [0, 0])
                entry[0] += r.correct
                entry[1] += 1
        return {tag: (c, n) for tag, (c, n) in sorted(counts.items())}


class GoldProvider:
    """Answers each suite question with its own gold query.

    Running a suite with it must score 100%: anything less means the harness, the
    guardrails or the warehouse disagree with the suite, not that a model is wrong.
    """

    def __init__(self, suite: Suite) -> None:
        self._gold = {q.question: q.gold for q in suite.questions}
        self._pending: str | None = None

    def complete(self, messages: Sequence[Message]) -> str:
        if self._pending is not None:
            self._pending = None
            return "Answered with the gold query."
        question = next(m["content"] for m in messages if m["role"] == "user")
        self._pending = question
        return f"```sql\n{self._gold[question]}\n```"


def run_suite(
    suite: Suite,
    provider: Provider,
    warehouse: DuckDBWarehouse,
    trace_dir: Path,
    *,
    max_repairs: int = 2,
    clock: Callable[[], float] = time.perf_counter,
    docs: DbtDocs | None = None,
) -> SuiteResult:
    """Ask every question, run its gold query, and score the agent's result against it."""
    policy = suite.policy()
    results = []
    for q in suite.questions:
        gold = warehouse.run(check(q.gold, policy))
        started = clock()
        try:
            answer = ask(
                q.question,
                provider,
                warehouse,
                policy,
                trace_dir / f"{suite.name}-{q.id}.jsonl",
                max_repairs=max_repairs,
                docs=docs,
            )
        except (AgentError, ProviderError) as error:
            results.append(
                QuestionResult(
                    q.id, q.tags, False, f"no answer: {error}", clock() - started, 0, None
                )
            )
            continue
        seconds = clock() - started
        if answer.result.truncated:
            verdict = Comparison(False, "result was cut at the row limit")
        else:
            verdict = compare(gold.rows, answer.result.rows, ordered=q.ordered)
        results.append(
            QuestionResult(
                q.id, q.tags, verdict.match, verdict.reason, seconds, answer.attempts, answer.sql
            )
        )
    return SuiteResult(suite.name, tuple(results))


def format_results(result: SuiteResult, *, model: str, hardware: str) -> str:
    """A Markdown report: the headline, accuracy per tag, and every question."""
    correct = sum(r.correct for r in result.results)
    total = len(result.results)
    lines = [
        f"# {result.suite} suite: {model}",
        "",
        f"Execution accuracy: **{correct} of {total}** ({result.accuracy:.0%}). {hardware}.",
        "",
        "| Tag | Correct |",
        "|---|---|",
    ]
    lines += [f"| {tag} | {c} of {n} |" for tag, (c, n) in result.by_tag().items()]
    lines += ["", "| Question | Result | Attempts | Seconds | Why |", "|---|---|---|---|---|"]
    for r in result.results:
        mark = "correct" if r.correct else "wrong"
        lines.append(f"| {r.id} | {mark} | {r.attempts} | {r.seconds:.2f} | {r.reason} |")
    return "\n".join(lines) + "\n"
