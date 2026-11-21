from pathlib import Path

import duckdb
import pytest

from data_analyst_agent import cli
from data_analyst_agent.bench import (
    GoldProvider,
    SuiteError,
    format_results,
    load_suite,
    parse_suite,
    run_suite,
)
from data_analyst_agent.providers import ScriptedProvider
from data_analyst_agent.warehouse import DuckDBWarehouse

SUITES = Path(__file__).resolve().parents[1] / "benchmarks"


def _suite(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "name": "t",
        "schemas": ["marts"],
        "question": [{"id": "q1", "question": "How many?", "gold": "select 1 from marts.t"}],
    }
    data.update(overrides)
    return data


def test_the_commerce_suite_loads_and_every_gold_query_passes_the_guardrails() -> None:
    suite = load_suite(SUITES / "commerce" / "questions.toml")
    assert suite.name == "commerce"
    assert suite.schemas == ("marts",)
    assert len(suite.questions) == 12
    assert len({q.id for q in suite.questions}) == 12
    assert all(q.gold.lower().startswith("select") for q in suite.questions)


def test_defaults_for_tags_and_order() -> None:
    [q] = parse_suite(_suite()).questions
    assert (q.tags, q.ordered) == ((), False)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": ""}, "needs a name"),
        ({"schemas": []}, "schemas"),
        ({"question": []}, "no \\[\\[question\\]\\]"),
        ({"question": [{"id": "q1", "question": "x"}]}, "'gold'"),
        ({"question": [{"id": "q1", "question": "x", "gold": "select 1", "level": 2}]}, "unknown"),
        (
            {"question": [{"id": "q1", "question": "x", "gold": "select 1", "ordered": "yes"}]},
            "ordered",
        ),
        (
            {"question": [{"id": "a", "question": "x", "gold": "select 1"}] * 2},
            "duplicate question ids",
        ),
        (
            {"question": [{"id": "q1", "question": "x", "gold": "delete from marts.t"}]},
            "gold query of q1 is refused",
        ),
        (
            {"question": [{"id": "q1", "question": "x", "gold": "select * from raw.t"}]},
            "gold query of q1 is refused",
        ),
    ],
)
def test_bad_suites_are_refused(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(SuiteError, match=message):
        parse_suite(_suite(**overrides))


def test_a_file_that_is_not_toml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "q.toml"
    path.write_text("name = ")
    with pytest.raises(SuiteError, match=r"q\.toml"):
        load_suite(path)


# Running suites against a small warehouse.

SMALL_SUITE = """
name = "small"
schemas = ["marts"]

[[question]]
id = "s1"
tags = ["count"]
question = "How many orders are there?"
gold = "select count(*) from marts.orders"

[[question]]
id = "s2"
tags = ["group"]
question = "How many orders are there per state?"
gold = "select state, count(*) from marts.orders group by state"

[[question]]
id = "s3"
tags = ["group", "order"]
question = "List the states by number of orders, most first."
gold = "select state from marts.orders group by state order by count(*) desc"
ordered = true
"""


@pytest.fixture
def warehouse_file(tmp_path: Path) -> Path:
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute(
        "create table marts.orders as select * from (values "
        "(1, 'SP'), (2, 'SP'), (3, 'SP'), (4, 'RJ'), (5, 'RJ'), (6, 'MG')) t(id, state)"
    )
    con.close()
    return path


@pytest.fixture
def small_suite(tmp_path: Path) -> Path:
    path = tmp_path / "small.toml"
    path.write_text(SMALL_SUITE)
    return path


def test_the_gold_provider_scores_every_question(
    warehouse_file: Path, small_suite: Path, tmp_path: Path
) -> None:
    suite = load_suite(small_suite)
    warehouse = DuckDBWarehouse(warehouse_file, suite.schemas)
    result = run_suite(suite, GoldProvider(suite), warehouse, tmp_path)
    assert result.accuracy == 1.0
    assert [r.attempts for r in result.results] == [1, 1, 1]
    assert (tmp_path / "small-s1.jsonl").exists()


def test_wrong_answers_and_failures_are_scored_with_reasons(
    warehouse_file: Path, small_suite: Path, tmp_path: Path
) -> None:
    suite = load_suite(small_suite)
    warehouse = DuckDBWarehouse(warehouse_file, suite.schemas)
    ticks = iter(range(100))
    provider = ScriptedProvider(
        [
            # s1: counts distinct states instead of orders
            "```sql\nselect count(distinct state) from marts.orders\n```",
            "There are 3.",
            # s2: right answer, columns the other way round and named
            "```sql\nselect count(*) as n, state from marts.orders group by state\n```",
            "Per state.",
            # s3: the right states in the wrong order; then nothing left for repairs
            "```sql\nselect state from marts.orders group by state order by count(*)\n```",
            "Ascending.",
        ]
    )
    result = run_suite(suite, provider, warehouse, tmp_path, clock=lambda: float(next(ticks)))
    by_id = {r.id: r for r in result.results}
    assert (by_id["s1"].correct, by_id["s1"].reason) == (False, "different values")
    assert (by_id["s2"].correct, by_id["s2"].reason) == (True, "same values")
    assert by_id["s3"].correct is False
    assert result.by_tag() == {"count": (0, 1), "group": (1, 2), "order": (0, 1)}
    assert all(r.seconds == 1.0 for r in result.results)


def test_a_provider_failure_is_a_wrong_answer_not_a_crash(
    warehouse_file: Path, small_suite: Path, tmp_path: Path
) -> None:
    suite = load_suite(small_suite)
    warehouse = DuckDBWarehouse(warehouse_file, suite.schemas)
    result = run_suite(suite, ScriptedProvider([]), warehouse, tmp_path)
    assert result.accuracy == 0.0
    assert all(r.reason.startswith("no answer:") for r in result.results)


def test_the_report_states_accuracy_tags_and_hardware(
    warehouse_file: Path, small_suite: Path, tmp_path: Path
) -> None:
    suite = load_suite(small_suite)
    warehouse = DuckDBWarehouse(warehouse_file, suite.schemas)
    result = run_suite(suite, GoldProvider(suite), warehouse, tmp_path)
    text = format_results(result, model="gold queries", hardware="2 vCPUs")
    assert text.startswith("# small suite: gold queries\n")
    assert "Execution accuracy: **3 of 3** (100%). 2 vCPUs." in text
    assert "| group | 2 of 2 |" in text


def test_bench_command_with_gold_queries(
    warehouse_file: Path,
    small_suite: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    out = tmp_path / "results.md"
    status = cli.main(
        [
            "bench",
            str(small_suite),
            "--warehouse",
            f"duckdb:///{warehouse_file}",
            "--gold",
            "--trace-dir",
            str(tmp_path / "traces"),
            "--out",
            str(out),
        ]
    )
    assert status == 0
    assert "**3 of 3**" in capsys.readouterr().out
    assert "**3 of 3**" in out.read_text()


def test_bench_command_reports_a_bad_suite(
    tmp_path: Path, warehouse_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('name = "x"\nschemas = []\n')
    status = cli.main(["bench", str(bad), "--warehouse", f"duckdb:///{warehouse_file}", "--gold"])
    assert status == 1
    assert "schemas" in capsys.readouterr().err
