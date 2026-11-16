from pathlib import Path

import duckdb
import pytest

from data_analyst_agent.cli import CLIError, build_arg_parser, parse_duckdb_uri, run_ask
from data_analyst_agent.providers import ScriptedProvider


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("duckdb:///support.duckdb", Path("support.duckdb")),
        ("duckdb:////abs/path/support.duckdb", Path("/abs/path/support.duckdb")),
    ],
)
def test_parses_a_duckdb_uri(uri: str, expected: Path) -> None:
    assert parse_duckdb_uri(uri) == expected


@pytest.mark.parametrize("uri", ["postgres:///x", "duckdb://", "support.duckdb"])
def test_rejects_an_unusable_warehouse_uri(uri: str) -> None:
    with pytest.raises(CLIError):
        parse_duckdb_uri(uri)


def test_ask_subcommand_requires_question_warehouse_and_schema() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(
        ["ask", "how many cases?", "--warehouse", "duckdb:///x.duckdb", "--schema", "marts"]
    )
    assert args.question == "how many cases?"
    assert args.schemas == ["marts"]
    assert args.max_rows == 1000
    assert args.max_repairs == 2


def test_schema_can_be_repeated() -> None:
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "ask",
            "q",
            "--warehouse",
            "duckdb:///x.duckdb",
            "--schema",
            "marts",
            "--schema",
            "staging",
        ]
    )
    assert args.schemas == ["marts", "staging"]


def test_run_ask_wires_warehouse_policy_and_agent_together(tmp_path: Path) -> None:
    db_path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("create schema marts")
    con.execute("create table marts.cases as select range as id from range(3)")
    con.close()

    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "ask",
            "how many cases?",
            "--warehouse",
            f"duckdb:///{db_path}",
            "--schema",
            "marts",
            "--trace-dir",
            str(tmp_path / "traces"),
        ]
    )
    provider = ScriptedProvider(
        ["```sql\nselect count(*) n from marts.cases\n```", "There are 3 cases."]
    )

    result = run_ask(args, provider)

    assert result.answer == "There are 3 cases."
    assert result.result.rows == ((3,),)
    assert list((tmp_path / "traces").glob("trace-*.jsonl"))


def test_run_ask_refuses_a_missing_warehouse_file(tmp_path: Path) -> None:
    parser = build_arg_parser()
    args = parser.parse_args(
        [
            "ask",
            "q",
            "--warehouse",
            f"duckdb:///{tmp_path / 'missing.duckdb'}",
            "--schema",
            "marts",
        ]
    )
    with pytest.raises(CLIError, match="no warehouse file"):
        run_ask(args, ScriptedProvider([]))
