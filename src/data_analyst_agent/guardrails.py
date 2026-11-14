"""Decide whether a model-written query may run, and make it safe to run.

The model is untrusted. Everything it writes goes through ``check`` before it reaches
the warehouse: the text must parse as exactly one read-only statement, it may only read
from allowed schemas, and it gets a row limit if it has none. The read-only database
connection is the second line of defence, not the first.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# Statement types that only read. Anything else (DML, DDL, PRAGMA, SET, COPY, ATTACH)
# is refused, including when it hides inside a CTE or a subquery.
_READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
_WRITE_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter,
    exp.Command, exp.Copy, exp.Pragma, exp.Set, exp.TruncateTable, exp.Attach, exp.Detach,
    exp.Use,
)  # fmt: skip
# Functions that read files or other databases from inside a SELECT.
_BLOCKED_FUNCTIONS = frozenset(
    {
        "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto",
        "read_text", "read_blob", "glob", "sniff_csv", "query_table", "sqlite_scan",
        "postgres_scan", "mysql_scan", "httpfs", "load", "install",
    }
)  # fmt: skip


class GuardrailError(ValueError):
    """The query was refused; the message says why and is shown to the model."""


@dataclass(frozen=True)
class Policy:
    allowed_schemas: frozenset[str]
    max_rows: int = 1000
    dialect: str = "duckdb"

    @classmethod
    def for_schemas(
        cls, schemas: Iterable[str], max_rows: int = 1000, dialect: str = "duckdb"
    ) -> Policy:
        return cls(frozenset(s.lower() for s in schemas), max_rows, dialect)


@dataclass(frozen=True)
class CheckedQuery:
    sql: str
    tables: tuple[str, ...]
    limit_added: bool


def check(sql: str, policy: Policy) -> CheckedQuery:
    """Return a safe version of ``sql`` or raise ``GuardrailError``."""
    try:
        statements = [s for s in sqlglot.parse(sql, dialect=policy.dialect) if s is not None]
    except ParseError as err:
        raise GuardrailError(f"could not parse the query: {err.errors[0]['description']}") from err
    if len(statements) != 1:
        raise GuardrailError(f"expected exactly one statement, got {len(statements)}")
    tree = statements[0]

    if not isinstance(tree, _READ_ROOTS):
        raise GuardrailError(f"only SELECT queries are allowed, got {tree.key.upper()}")
    for node in tree.walk():
        if isinstance(node, _WRITE_NODES):
            raise GuardrailError(f"{node.key.upper()} is not allowed inside a query")
        if isinstance(node, exp.Func) and node.name.lower() in _BLOCKED_FUNCTIONS:
            raise GuardrailError(f"function {node.name} is not allowed")
        if isinstance(node, exp.Anonymous) and node.name.lower() in _BLOCKED_FUNCTIONS:
            raise GuardrailError(f"function {node.name} is not allowed")

    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tables = []
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not table.db and name in ctes:
            continue
        if not table.name:
            # A table function such as range(10) or a string path like 'x.csv'.
            raise GuardrailError("reading from table functions or file paths is not allowed")
        schema = table.db.lower()
        if not schema:
            raise GuardrailError(f"qualify table {table.name} with its schema")
        if schema not in policy.allowed_schemas:
            raise GuardrailError(f"schema {table.db} is not one of the allowed schemas")
        tables.append(f"{schema}.{name}")

    limit_added = False
    existing = tree.args.get("limit") if isinstance(tree, exp.Select) else None
    if isinstance(tree, exp.Select) and existing is None:
        tree = tree.limit(policy.max_rows)
        limit_added = True
    elif isinstance(tree, exp.Select) and existing is not None:
        value = existing.expression
        if not (isinstance(value, exp.Literal) and value.is_int):
            raise GuardrailError("LIMIT must be a plain number")
        if int(value.this) > policy.max_rows:
            tree = tree.limit(policy.max_rows)
            limit_added = True
    elif not isinstance(tree, exp.Select):
        # Set operations: wrap so the limit applies to the whole result.
        tree = exp.select("*").from_(tree.subquery("q")).limit(policy.max_rows)
        limit_added = True

    return CheckedQuery(
        sql=tree.sql(dialect=policy.dialect),
        tables=tuple(sorted(set(tables))),
        limit_added=limit_added,
    )
