"""Benchmark suites: questions with gold queries, loaded from TOML.

A suite file has a ``name``, the ``schemas`` the agent may read, and a list of
``[[question]]`` tables, each with an ``id``, the ``question`` in plain words, the
``gold`` SQL and optional ``tags`` and ``ordered`` (true when the question asks for an
order, so row order is scored). See ``benchmarks/commerce/questions.toml``.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_analyst_agent.guardrails import GuardrailError, Policy, check


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

