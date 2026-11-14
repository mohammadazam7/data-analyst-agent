from pathlib import Path

import duckdb
import pytest

from data_analyst_agent.guardrails import Policy, check
from data_analyst_agent.warehouse import DuckDBWarehouse, QueryTimeout


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "wh.duckdb"
    con = duckdb.connect(str(path))
    con.execute("create schema marts")
    con.execute("create schema raw")
    con.execute(
        "create table marts.cases as select range as id, "
        "case when range % 2 = 0 then 'east' else 'west' end as region from range(10)"
    )
    con.execute("create table raw.secret as select 'x' as v")
    con.close()
    return path


def test_lists_only_allowed_schemas(db: Path) -> None:
    wh = DuckDBWarehouse(db, ["marts"])
    [table] = wh.tables()
    assert table.describe() == "marts.cases(id BIGINT, region VARCHAR)"


def test_runs_a_checked_query_and_flags_truncation(db: Path) -> None:
    wh = DuckDBWarehouse(db, ["marts"])
    small = wh.run(
        check(
            "select region, count(*) n from marts.cases group by 1 order by 1",
            Policy.for_schemas(["marts"]),
        )
    )
    assert small.columns == ("region", "n") and small.rows == (("east", 5), ("west", 5))
    assert not small.truncated
    capped = wh.run(check("select * from marts.cases", Policy.for_schemas(["marts"], max_rows=3)))
    assert len(capped.rows) == 3 and capped.truncated
    assert "... 1 more rows" in capped.preview(2)


def test_connection_is_read_only(db: Path) -> None:
    wh = DuckDBWarehouse(db, ["marts"])
    with pytest.raises(duckdb.Error):
        wh._con.execute("delete from marts.cases")


def test_long_queries_are_interrupted(db: Path) -> None:
    wh = DuckDBWarehouse(db, ["marts"], timeout_s=0.2)
    slow = check(
        "select count(*) from marts.cases a, marts.cases b, marts.cases c, marts.cases d, "
        "marts.cases e, marts.cases f, marts.cases g, marts.cases h, marts.cases i, marts.cases j "
        "where a.id + b.id + c.id + d.id + e.id + f.id + g.id + h.id + i.id + j.id > -1",
        Policy.for_schemas(["marts"]),
    )
    with pytest.raises(QueryTimeout):
        wh.run(slow)
    wh.close()
