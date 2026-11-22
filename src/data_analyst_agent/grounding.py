"""Model and column descriptions from a dbt project, for the prompt.

Column names and types are often not enough to answer correctly: ``customer_id`` in the
Olist marts is per order, and ``tier`` in a type 2 dimension is only current on rows
where ``is_current`` is true. dbt projects already document such things, so the agent
reads them from the project's ``target/manifest.json`` (written by ``dbt build``,
``dbt run`` or ``dbt docs generate``) and puts them next to the tables they describe.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from data_analyst_agent.warehouse import Table


class GroundingError(ValueError):
    pass


@dataclass(frozen=True)
class TableDoc:
    description: str
    columns: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DbtDocs:
    tables: dict[tuple[str, str], TableDoc]

    @classmethod
    def from_manifest(cls, path: Path) -> DbtDocs:
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise GroundingError(f"cannot read the dbt manifest at {path}: {error}") from error
        nodes = manifest.get("nodes") if isinstance(manifest, dict) else None
        if not isinstance(nodes, dict):
            raise GroundingError(f"{path} has no nodes; is it a dbt manifest.json?")
        tables: dict[tuple[str, str], TableDoc] = {}
        for node in nodes.values():
            if not isinstance(node, dict) or node.get("resource_type") not in {
                "model",
                "seed",
                "snapshot",
            }:
                continue
            key = (
                str(node.get("schema", "")).lower(),
                str(node.get("alias") or node.get("name", "")).lower(),
            )
            columns = {
                str(name).lower(): _clean(col.get("description", ""))
                for name, col in _dict(node.get("columns")).items()
                if isinstance(col, dict) and _clean(col.get("description", ""))
            }
            description = _clean(node.get("description", ""))
            if description or columns:
                tables[key] = TableDoc(description, columns)
        return cls(tables)

    def describe(self, table: Table) -> str:
        """The table with its description, one column per line with any description."""
        doc = self.tables.get((table.schema.lower(), table.name.lower()))
        if doc is None:
            return table.describe()
        head = f"{table.schema}.{table.name}"
        lines = [f"{head}: {doc.description}" if doc.description else head]
        for column in table.columns:
            note = doc.columns.get(column.name.lower())
            lines.append(f"  {column.name} {column.type}" + (f" -- {note}" if note else ""))
        return "\n".join(lines)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean(text: Any) -> str:
    return " ".join(str(text).split()) if text else ""
