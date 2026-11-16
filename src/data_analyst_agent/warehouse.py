"""Read-only access to the warehouse: schema for the prompt, and query execution.

DuckDB only for now. The connection is opened read-only, so even a query that slipped
past the guardrails could not change data; queries are interrupted after a time budget.
"""

from __future__ import annotations

import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from data_analyst_agent.guardrails import CheckedQuery


class QueryTimeout(RuntimeError):
    pass


@dataclass(frozen=True)
class Column:
    name: str
    type: str


@dataclass(frozen=True)
class Table:
    schema: str
    name: str
    columns: tuple[Column, ...]

    def describe(self) -> str:
        cols = ", ".join(f"{c.name} {c.type}" for c in self.columns)
        return f"{self.schema}.{self.name}({cols})"


@dataclass(frozen=True)
class Result:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool

    def preview(self, n: int = 20) -> str:
        """A plain text table of the first ``n`` rows, for the model and the terminal."""
        lines = [" | ".join(self.columns)]
        lines += [" | ".join("" if v is None else str(v) for v in row) for row in self.rows[:n]]
        if len(self.rows) > n:
            lines.append(f"... {len(self.rows) - n} more rows")
        return "\n".join(lines)


class DuckDBWarehouse:
    def __init__(self, path: Path, schemas: Sequence[str], timeout_s: float = 30.0) -> None:
        self.path = path
        self.schemas = tuple(schemas)
        self.timeout_s = timeout_s
        self._con = duckdb.connect(str(path), read_only=True)

    def tables(self) -> list[Table]:
        rows = self._con.execute(
            "select table_schema, table_name, column_name, data_type "
            "from information_schema.columns "
            "where table_schema in (select unnest(?)) "
            "order by table_schema, table_name, ordinal_position",
            [list(self.schemas)],
        ).fetchall()
        tables: dict[tuple[str, str], list[Column]] = {}
        for schema, name, col, typ in rows:
            tables.setdefault((schema, name), []).append(Column(col, typ))
        return [Table(s, n, tuple(cols)) for (s, n), cols in tables.items()]

    def run(self, query: CheckedQuery) -> Result:
        timer = threading.Timer(self.timeout_s, self._con.interrupt)
        timer.start()
        try:
            cursor = self._con.execute(query.sql)
            names = tuple(d[0] for d in cursor.description or ())
            rows = tuple(tuple(r) for r in cursor.fetchall())
        except duckdb.InterruptException as err:
            raise QueryTimeout(f"query ran longer than {self.timeout_s:g} s") from err
        finally:
            timer.cancel()
        # A result that fills the row cap may have been cut short by it.
        return Result(names, rows, truncated=len(rows) >= query.row_cap)

    def close(self) -> None:
        self._con.close()
