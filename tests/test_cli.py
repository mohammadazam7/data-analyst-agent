from pathlib import Path

import duckdb
import pytest

from data_analyst_agent import cli
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


@pytest.fixture
def small_warehouse(tmp_path: Path) -> Path:
    db_path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute("create schema marts")
    con.execute("create table marts.cases as select range as id from range(3)")
    con.close()
    return db_path


def test_main_builds_the_provider_from_the_environment_and_prints_the_answer(
    small_warehouse: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    replies = ScriptedProvider(
        ["```sql\nselect count(*) n from marts.cases\n```", "There are 3 cases."]
    )
    seen = {}

    def fake_provider(config: object) -> ScriptedProvider:
        seen["config"] = config
        return replies

    monkeypatch.setattr(cli, "OpenAICompatibleProvider", fake_provider)
    monkeypatch.setenv("ANALYST_BASE_URL", "http://localhost:9/v1")
    monkeypatch.setenv("ANALYST_MODEL", "local-model")

    status = cli.main(
        [
            "ask",
            "how many cases?",
            "--warehouse",
            f"duckdb:///{small_warehouse}",
            "--schema",
            "marts",
            "--trace-dir",
            str(tmp_path / "traces"),
        ]
    )

    out = capsys.readouterr().out
    assert status == 0
    assert out.startswith("There are 3 cases.\n")
    assert "SELECT COUNT(*) AS n FROM marts.cases" in out
    assert seen["config"].model == "local-model"  # type: ignore[attr-defined]


def test_main_reports_a_missing_model_endpoint_without_a_traceback(
    small_warehouse: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("ANALYST_BASE_URL", raising=False)
    monkeypatch.delenv("ANALYST_MODEL", raising=False)
    status = cli.main(
        ["ask", "q", "--warehouse", f"duckdb:///{small_warehouse}", "--schema", "marts"]
    )
    assert status == 1
    assert "ANALYST_BASE_URL" in capsys.readouterr().err
