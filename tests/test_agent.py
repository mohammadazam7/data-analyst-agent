import json
from pathlib import Path

import duckdb
import pytest

from data_analyst_agent.agent import AgentError, ask, new_trace_path
from data_analyst_agent.guardrails import CheckedQuery, Policy
from data_analyst_agent.providers import ScriptedProvider
from data_analyst_agent.warehouse import DuckDBWarehouse, QueryTimeout, Result


@pytest.fixture
def warehouse(tmp_path: Path) -> DuckDBWarehouse:
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute(
        "create table marts.cases as select range as id, "
        "case when range % 2 = 0 then 'east' else 'west' end as region from range(10)"
    )
    con.close()
    return DuckDBWarehouse(path, ["marts"])


@pytest.fixture
def policy() -> Policy:
    return Policy.for_schemas(["marts"], max_rows=500)


def test_asks_runs_and_answers_on_the_first_try(
    warehouse: DuckDBWarehouse, policy: Policy, tmp_path: Path
) -> None:
    provider = ScriptedProvider(
        [
            "```sql\nselect count(*) n from marts.cases\n```",
            "There are 10 cases.",
        ]
    )
    trace_path = new_trace_path(tmp_path / "traces")

    result = ask("how many cases?", provider, warehouse, policy, trace_path)

    assert result.sql == "SELECT COUNT(*) AS n FROM marts.cases LIMIT 500"
    assert result.result.rows == ((10,),)
    assert result.answer == "There are 10 cases."
    assert result.attempts == 1

    lines = [json.loads(line) for line in trace_path.read_text().splitlines()]
    steps = [line["step"] for line in lines]
    assert steps == ["question", "model_reply", "attempt", "ran", "model_reply", "answer"]
    assert all(line["question"] == "how many cases?" for line in lines)


def test_repairs_a_refused_query_using_the_feedback(
    warehouse: DuckDBWarehouse, policy: Policy, tmp_path: Path
) -> None:
    provider = ScriptedProvider(
        [
            "```sql\nselect * from raw.secrets\n```",
            "```sql\nselect count(*) n from marts.cases\n```",
            "There are 10 cases.",
        ]
    )
    trace_path = new_trace_path(tmp_path / "traces")

    result = ask("how many cases?", provider, warehouse, policy, trace_path, max_repairs=2)

    assert result.attempts == 2
    # The refusal reason was fed back as the next user message.
    second_call = provider.calls[1]
    assert second_call[-1]["role"] == "user"
    assert "refused" in second_call[-1]["content"]
    assert "not one of the allowed schemas" in second_call[-1]["content"]

    steps = [json.loads(line)["step"] for line in trace_path.read_text().splitlines()]
    assert steps == [
        "question",
        "model_reply",
        "attempt",
        "refused",
        "model_reply",
        "attempt",
        "ran",
        "model_reply",
        "answer",
    ]


def test_gives_up_after_the_repair_budget_is_spent(
    warehouse: DuckDBWarehouse, policy: Policy, tmp_path: Path
) -> None:
    provider = ScriptedProvider(["no sql here"] * 2)
    trace_path = new_trace_path(tmp_path / "traces")

    with pytest.raises(AgentError, match="gave up after 2 attempts"):
        ask("how many cases?", provider, warehouse, policy, trace_path, max_repairs=1)

    steps = [json.loads(line)["step"] for line in trace_path.read_text().splitlines()]
    assert steps[-1] == "failed"


class TimesOutOnce(DuckDBWarehouse):
    """Fails the first query the way a real timeout does, without depending on timing."""

    def __init__(self, path: Path) -> None:
        super().__init__(path, ["marts"])
        self.runs = 0

    def run(self, query: CheckedQuery) -> Result:
        self.runs += 1
        if self.runs == 1:
            raise QueryTimeout("query ran longer than 30 s")
        return super().run(query)


def test_feeds_back_a_query_timeout_as_a_repair(
    warehouse: DuckDBWarehouse, policy: Policy, tmp_path: Path
) -> None:
    slow = TimesOutOnce(warehouse.path)
    provider = ScriptedProvider(
        [
            "```sql\nselect count(*) n from marts.cases, marts.cases b, marts.cases c\n```",
            "```sql\nselect count(*) n from marts.cases\n```",
            "There are 10 cases.",
        ]
    )
    trace_path = new_trace_path(tmp_path / "traces")

    result = ask("how many cases?", provider, slow, policy, trace_path, max_repairs=2)

    assert result.attempts == 2
    assert "longer than 30 s" in provider.calls[1][-1]["content"]
    steps = [json.loads(line)["step"] for line in trace_path.read_text().splitlines()]
    assert "query_error" in steps
