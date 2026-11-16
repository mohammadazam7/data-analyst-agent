"""The loop that turns a question into a checked query and an answer.

One turn: ask the model for a query, check it with the guardrails, run it on the
warehouse, then ask the model to answer from the result. A refusal or a query error
goes back to the model as the next message, up to a fixed number of repairs, so a bad
first query does not end the attempt. Every step is appended to a JSONL trace: what was
asked, what came back, what happened to it.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data_analyst_agent.guardrails import GuardrailError, Policy, check
from data_analyst_agent.providers import Message, Provider, ProviderError
from data_analyst_agent.warehouse import DuckDBWarehouse, QueryTimeout, Result

_SQL_BLOCK = re.compile(r"```(?:sql)?\s*(.+?)\s*```", re.DOTALL | re.IGNORECASE)

SYSTEM_PROMPT = """\
You answer questions about a data warehouse by writing one read-only SQL query.

Tables you may read from:
{tables}

Rules:
- Write exactly one SELECT query in a single ```sql fenced code block.
- Qualify every table with its schema, exactly as listed above.
- Do not write anything that is not a SELECT.
If a query is refused or fails, you will be told why; fix it and answer again with a
single corrected ```sql block and nothing else.
"""

ANSWER_PROMPT = """\
The query

```sql
{sql}
```

returned:

{preview}

Answer the original question in one or two plain sentences, using only this data.
"""


class AgentError(RuntimeError):
    """No usable query was produced within the repair budget."""


@dataclass(frozen=True)
class AgentResult:
    question: str
    sql: str
    result: Result
    answer: str
    attempts: int


def _extract_sql(reply: str) -> str:
    match = _SQL_BLOCK.search(reply)
    if not match:
        raise GuardrailError("no ```sql fenced block found in the reply")
    return match.group(1).strip()


@dataclass
class Trace:
    """Appends one JSON object per step to ``path``, each on its own line."""

    path: Path
    _question: str = field(default="", init=False)

    def question(self, question: str) -> None:
        self._question = question
        self._write({"step": "question", "question": question})

    def model_reply(self, phase: str, reply: str) -> None:
        self._write({"step": "model_reply", "phase": phase, "reply": reply})

    def attempt(self, attempt: int, sql: str) -> None:
        self._write({"step": "attempt", "attempt": attempt, "sql": sql})

    def refused(self, attempt: int, reason: str) -> None:
        self._write({"step": "refused", "attempt": attempt, "reason": reason})

    def query_error(self, attempt: int, reason: str) -> None:
        self._write({"step": "query_error", "attempt": attempt, "reason": reason})

    def ran(self, attempt: int, result: Result) -> None:
        self._write(
            {
                "step": "ran",
                "attempt": attempt,
                "row_count": len(result.rows),
                "truncated": result.truncated,
            }
        )

    def answer(self, text: str) -> None:
        self._write({"step": "answer", "answer": text})

    def failed(self, reason: str) -> None:
        self._write({"step": "failed", "reason": reason})

    def _write(self, record: dict[str, Any]) -> None:
        record = {"question": self._question, **record}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")


def ask(
    question: str,
    provider: Provider,
    warehouse: DuckDBWarehouse,
    policy: Policy,
    trace_path: Path,
    max_repairs: int = 2,
) -> AgentResult:
    """Answer ``question`` against ``warehouse``, writing each step to ``trace_path``."""
    trace = Trace(trace_path)
    trace.question(question)

    tables = "\n".join(t.describe() for t in warehouse.tables())
    messages: list[Message] = [
        {"role": "system", "content": SYSTEM_PROMPT.format(tables=tables)},
        {"role": "user", "content": question},
    ]

    last_reason = "no attempt was made"
    for attempt in range(1, max_repairs + 2):
        try:
            reply = provider.complete(messages)
        except ProviderError as err:
            trace.failed(str(err))
            raise AgentError(f"model could not be reached: {err}") from err
        trace.model_reply("sql", reply)
        messages.append({"role": "assistant", "content": reply})

        try:
            sql = _extract_sql(reply)
            trace.attempt(attempt, sql)
            checked = check(sql, policy)
        except GuardrailError as err:
            last_reason = str(err)
            trace.refused(attempt, last_reason)
            messages.append(
                {"role": "user", "content": f"That query was refused: {last_reason}. Try again."}
            )
            continue

        try:
            result = warehouse.run(checked)
        except QueryTimeout as err:
            last_reason = str(err)
            trace.query_error(attempt, last_reason)
            messages.append(
                {"role": "user", "content": f"That query failed: {last_reason}. Try again."}
            )
            continue

        trace.ran(attempt, result)
        answer_messages: list[Message] = [
            {"role": "system", "content": "You summarise query results plainly."},
            {
                "role": "user",
                "content": ANSWER_PROMPT.format(sql=checked.sql, preview=result.preview()),
            },
        ]
        try:
            answer_text = provider.complete(answer_messages)
        except ProviderError as err:
            trace.failed(str(err))
            raise AgentError(f"model could not be reached: {err}") from err
        trace.model_reply("answer", answer_text)
        trace.answer(answer_text)
        return AgentResult(question, checked.sql, result, answer_text, attempt)

    trace.failed(last_reason)
    raise AgentError(f"gave up after {max_repairs + 1} attempts: {last_reason}")


def new_trace_path(directory: Path) -> Path:
    """A fresh trace file in ``directory``, named so concurrent runs never collide."""
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"trace-{uuid.uuid4().hex}.jsonl"
