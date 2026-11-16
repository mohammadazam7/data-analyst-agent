import pytest

from data_analyst_agent.guardrails import GuardrailError, Policy, check

POLICY = Policy.for_schemas(["marts"], max_rows=500)


@pytest.mark.parametrize(
    ("sql", "reason"),
    [
        ("drop table marts.fct_cases", "only SELECT"),
        ("insert into marts.a select 1", "only SELECT"),
        ("delete from marts.a", "only SELECT"),
        ("create table marts.b as select 1", "only SELECT"),
        ("copy marts.a to 'out.csv'", "only SELECT"),
        ("attach 'other.db'", "only SELECT"),
        ("pragma table_info('a')", "only SELECT"),
        ("select 1 from marts.a; drop table marts.a", "exactly one statement"),
        ("select 1 from marts.a; -- harmless\n; delete from marts.a", "exactly one statement"),
        ("select * from raw.customers", "not one of the allowed schemas"),
        ("select * from main.secrets", "not one of the allowed schemas"),
        ("select * from fct_cases", "qualify table"),
        ("select * from read_csv('/etc/passwd')", "table functions or file paths"),
        ("select * from 'data.parquet'", "qualify table"),
        ("select * from marts.a limit (select 10)", "plain number"),
        ("selec * from marts.a", "could not parse"),
        ("with x as (select * from raw.c) select * from x", "not one of the allowed schemas"),
        ("select * from marts.a where id in (select id from raw.b)", "not one of the allowed"),
    ],
)
def test_refused(sql: str, reason: str) -> None:
    with pytest.raises(GuardrailError, match=reason):
        check(sql, POLICY)


def test_adds_a_limit_and_reports_tables() -> None:
    q = check("select region, count(*) from marts.fct_cases group by region", POLICY)
    assert q.sql.endswith("LIMIT 500")
    assert q.limit_added and q.tables == ("marts.fct_cases",)


def test_keeps_a_smaller_limit_and_caps_a_larger_one() -> None:
    assert check("select * from marts.a limit 5", POLICY).sql.endswith("LIMIT 5")
    capped = check("select * from marts.a limit 100000", POLICY)
    assert capped.sql.endswith("LIMIT 500") and capped.limit_added


def test_ctes_and_set_operations_are_allowed() -> None:
    q = check("with t as (select * from marts.a) select * from t", POLICY)
    assert q.tables == ("marts.a",)
    union = check("select id from marts.a union all select id from marts.b", POLICY)
    assert union.tables == ("marts.a", "marts.b")
    assert union.sql.endswith("LIMIT 500")


def test_schema_names_are_case_insensitive() -> None:
    assert check("select * from MARTS.A", POLICY).tables == ("marts.a",)
