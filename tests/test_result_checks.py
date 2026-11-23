import json
from pathlib import Path

import duckdb

from data_analyst_agent.agent import ask
from data_analyst_agent.guardrails import Policy
from data_analyst_agent.providers import ScriptedProvider
from data_analyst_agent.result_checks import review
from data_analyst_agent.warehouse import DuckDBWarehouse, Result


def _codes(result: Result) -> list[str]:
    return [w.code for w in review(result)]


def test_a_plain_result_raises_nothing() -> None:
    assert _codes(Result(("state", "n"), (("SP", 3), ("RJ", 2)), False)) == []


def test_an_empty_result_is_flagged_alone() -> None:
    assert _codes(Result(("n",), (), False)) == ["empty"]


def test_a_column_of_nulls_is_flagged() -> None:
    warnings = review(Result(("avg_score",), ((None,),), False))
    assert [w.code for w in warnings] == ["all_null"]
    assert "avg_score" in warnings[0].message


def test_a_truncated_result_is_flagged() -> None:
    rows = tuple((i,) for i in range(5))
    assert _codes(Result(("id",), rows, True)) == ["truncated"]


def test_repeated_rows_are_flagged_with_a_count() -> None:
    warnings = review(Result(("order_id",), (("o1",), ("o1",), ("o1",), ("o2",)), False))
    assert [w.code for w in warnings] == ["duplicate_rows"]
    assert warnings[0].message.startswith("2 rows repeat")


def _warehouse(tmp_path: Path) -> DuckDBWarehouse:
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute("create table marts.orders as select 'delivered' as status")
    con.close()
    return DuckDBWarehouse(path, ["marts"])


def test_the_answer_prompt_carries_the_question_and_the_warnings(tmp_path: Path) -> None:
    provider = ScriptedProvider(
        [
            "```sql\nselect * from marts.orders where status = 'Delivered'\n```",
            "No delivered orders were found, but the filter may not match the data.",
        ]
    )
    trace = tmp_path / "t.jsonl"
    result = ask(
        "How many delivered orders?",
        provider,
        _warehouse(tmp_path),
        Policy.for_schemas(["marts"]),
        trace,
    )
    assert [w.code for w in result.warnings] == ["empty"]
    prompt = provider.calls[1][1]["content"]
    assert prompt.startswith("Question: How many delivered orders?")
    assert "The query returned no rows." in prompt
    steps = [json.loads(line)["step"] for line in trace.read_text().splitlines()]
    assert "warnings" in steps


def test_a_clean_result_adds_no_warning_text(tmp_path: Path) -> None:
    provider = ScriptedProvider(["```sql\nselect count(*) from marts.orders\n```", "One."])
    result = ask(
        "How many orders?",
        provider,
        _warehouse(tmp_path),
        Policy.for_schemas(["marts"]),
        tmp_path / "t.jsonl",
    )
    assert result.warnings == ()
    assert "flagged" not in provider.calls[1][1]["content"]
