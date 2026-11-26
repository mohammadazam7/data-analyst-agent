import json
from pathlib import Path

import duckdb
import pytest

from data_analyst_agent import cli
from data_analyst_agent.agent import ask
from data_analyst_agent.grounding import DbtDocs, GroundingError
from data_analyst_agent.guardrails import Policy
from data_analyst_agent.providers import ScriptedProvider
from data_analyst_agent.warehouse import Column, DuckDBWarehouse, Table

MANIFEST = {
    "nodes": {
        "model.commerce.fct_orders": {
            "resource_type": "model",
            "schema": "marts",
            "name": "fct_orders",
            "alias": "fct_orders",
            "description": "One row per order.\n  Kept even with no items.",
            "columns": {
                "customer_unique_id": {"description": "The person; customer_id is per order."},
                "paid": {"description": ""},
            },
        },
        "test.commerce.unique_fct_orders_order_id": {
            "resource_type": "test",
            "schema": "marts",
            "name": "unique_fct_orders_order_id",
            "description": "should be skipped",
        },
        "model.commerce.stg_orders": {
            "resource_type": "model",
            "schema": "staging",
            "name": "stg_orders",
            "description": "",
            "columns": {},
        },
    }
}

TABLE = Table(
    "marts",
    "fct_orders",
    (
        Column("order_id", "VARCHAR"),
        Column("customer_unique_id", "VARCHAR"),
        Column("paid", "DOUBLE"),
    ),
)


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(MANIFEST))
    return path


def test_reads_model_and_column_descriptions(manifest: Path) -> None:
    docs = DbtDocs.from_manifest(manifest)
    assert list(docs.tables) == [("marts", "fct_orders")]
    doc = docs.tables[("marts", "fct_orders")]
    assert doc.description == "One row per order. Kept even with no items."
    assert doc.columns == {"customer_unique_id": "The person; customer_id is per order."}


def test_describes_a_documented_table_one_column_per_line(manifest: Path) -> None:
    text = DbtDocs.from_manifest(manifest).describe(TABLE)
    assert text.splitlines() == [
        "marts.fct_orders: One row per order. Kept even with no items.",
        "  order_id VARCHAR",
        "  customer_unique_id VARCHAR -- The person; customer_id is per order.",
        "  paid DOUBLE",
    ]


def test_an_undocumented_table_keeps_the_short_form(manifest: Path) -> None:
    table = Table("marts", "dim_customers", (Column("id", "VARCHAR"),))
    assert DbtDocs.from_manifest(manifest).describe(table) == "marts.dim_customers(id VARCHAR)"


@pytest.mark.parametrize("content", ["not json", "[]", '{"metadata": {}}'])
def test_a_file_that_is_not_a_manifest_is_refused(tmp_path: Path, content: str) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(content)
    with pytest.raises(GroundingError):
        DbtDocs.from_manifest(path)


def _warehouse(tmp_path: Path) -> Path:
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute(
        "create table marts.fct_orders as select 'o1' as order_id, 'u1' as customer_unique_id, "
        "10.0 as paid"
    )
    con.close()
    return path


def test_the_descriptions_reach_the_prompt(tmp_path: Path, manifest: Path) -> None:
    provider = ScriptedProvider(["```sql\nselect count(*) from marts.fct_orders\n```", "One."])
    warehouse = DuckDBWarehouse(_warehouse(tmp_path), ["marts"])
    ask(
        "How many orders?",
        provider,
        warehouse,
        Policy.for_schemas(["marts"]),
        tmp_path / "t.jsonl",
        docs=DbtDocs.from_manifest(manifest),
    )
    system = provider.calls[0][0]["content"]
    assert "marts.fct_orders: One row per order." in system
    assert "customer_id is per order" in system


def test_ask_reports_an_unreadable_manifest(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANALYST_BASE_URL", "http://localhost:9/v1")
    monkeypatch.setenv("ANALYST_MODEL", "m")
    status = cli.main(
        [
            "ask",
            "q",
            "--warehouse",
            f"duckdb:///{_warehouse(tmp_path)}",
            "--schema",
            "marts",
            "--dbt-manifest",
            str(tmp_path / "missing.json"),
        ]
    )
    assert status == 1
    assert "cannot read the dbt manifest" in capsys.readouterr().err
