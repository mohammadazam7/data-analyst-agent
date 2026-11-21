"""Execution accuracy: does the agent's query return the same answer as the gold query?

Comparing SQL text is useless (there are many right ways to write a query), so the
benchmark runs both queries and compares what they return. Two results match when:

- they have the same number of columns; names are ignored, because ``count(*)`` and
  ``count(*) AS n`` are the same answer;
- each column of the gold result can be paired with a different column of the
  predicted result holding the same values, so column order does not matter;
- rows are compared as a multiset, unless the gold question asks for an order
  (``ordered=True``), in which case row order must match too;
- numbers match within a relative tolerance of 1e-6 (and an absolute one of 1e-9), so
  ``0.1 + 0.2`` equals ``0.3`` and an integer count equals the same count as a float;
- dates and timestamps are compared as text after conversion, and ``None`` matches
  only ``None``.

This is the usual execution-accuracy definition with two leniencies, column names and
column order, both of which a person reading the answer would also ignore.
"""

from __future__ import annotations

import datetime as dt
import decimal
import itertools
import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

REL_TOL = 1e-6
ABS_TOL = 1e-9
# Beyond this many columns, trying every pairing is too slow; fall back to positional.
MAX_PERMUTED_COLUMNS = 7


@dataclass(frozen=True)
class Comparison:
    match: bool
    reason: str


def _normalise(value: Any) -> Any:
    """A hashable stand-in for ``value`` that equal answers share."""
    if value is None:
        return None
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int | float | decimal.Decimal):
        number = float(value)
        if math.isnan(number):
            return ("nan",)
        return ("num", number)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return ("text", value.isoformat())
    return ("text", str(value))


def _same(a: Any, b: Any) -> bool:
    if a == b:
        return True
    if isinstance(a, tuple) and isinstance(b, tuple) and a[0] == b[0] == "num":
        return math.isclose(a[1], b[1], rel_tol=REL_TOL, abs_tol=ABS_TOL)
    return False


def _key(value: Any) -> Any:
    """Rounded key used to bucket rows; exact matching is done with ``_same`` after."""
    if isinstance(value, tuple) and value[0] == "num":
        return ("num", round(value[1], 6))
    return value


def _rows_match(
    gold: list[tuple[Any, ...]], predicted: list[tuple[Any, ...]], ordered: bool
) -> bool:
    if len(gold) != len(predicted):
        return False
    if ordered:
        return all(
            all(_same(a, b) for a, b in zip(g, p, strict=True))
            for g, p in zip(gold, predicted, strict=True)
        )
    # Multiset comparison. Bucket by rounded key, then pair rows inside a bucket
    # with the tolerant comparison, so 0.30000000000000004 and 0.3 still pair.
    if Counter(tuple(map(_key, r)) for r in gold) == Counter(
        tuple(map(_key, r)) for r in predicted
    ):
        return True
    remaining = list(predicted)
    for row in gold:
        for i, candidate in enumerate(remaining):
            if all(_same(a, b) for a, b in zip(row, candidate, strict=True)):
                del remaining[i]
                break
        else:
            return False
    return True


def compare(
    gold: Sequence[Sequence[Any]],
    predicted: Sequence[Sequence[Any]],
    *,
    ordered: bool = False,
) -> Comparison:
    """Compare two result sets given as rows of values."""
    gold_rows = [tuple(_normalise(v) for v in row) for row in gold]
    pred_rows = [tuple(_normalise(v) for v in row) for row in predicted]
    gold_width = len(gold_rows[0]) if gold_rows else None
    pred_width = len(pred_rows[0]) if pred_rows else None
    if not gold_rows or not pred_rows:
        if not gold_rows and not pred_rows:
            return Comparison(True, "both empty")
        return Comparison(False, f"{len(gold_rows)} gold rows, {len(pred_rows)} predicted")
    if gold_width != pred_width:
        return Comparison(False, f"{gold_width} gold columns, {pred_width} predicted")
    if len(gold_rows) != len(pred_rows):
        return Comparison(False, f"{len(gold_rows)} gold rows, {len(pred_rows)} predicted")

    assert gold_width is not None
    orders: Any
    if gold_width <= MAX_PERMUTED_COLUMNS:
        orders = itertools.permutations(range(gold_width))
    else:
        orders = [tuple(range(gold_width))]
    for order in orders:
        reordered = [tuple(row[i] for i in order) for row in pred_rows]
        if _rows_match(gold_rows, reordered, ordered):
            return Comparison(True, "same values")
    return Comparison(False, "different values")
