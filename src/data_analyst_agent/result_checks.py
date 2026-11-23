"""Warning signs in a query result, raised before the answer is written.

A query can run cleanly and still answer the question wrongly. These checks catch the
cheap, common signs of that and pass them to the model writing the answer, so the
answer says what is doubtful instead of stating a wrong number with confidence. They
are recorded in the trace and returned with the result; they never block an answer.

``empty``
    No rows. Often a filter on a value spelled differently in the data ("Delivered"
    against "delivered") or a join on the wrong key.
``all_null``
    A column whose every value is null, usually an aggregate over no rows or a join
    that matched nothing.
``truncated``
    The result stopped at the row limit, so totals computed from it would be wrong.
``duplicate_rows``
    Identical rows in a result that has no aggregate, a common sign of a join that
    fans out.
"""

from __future__ import annotations

from dataclasses import dataclass

from data_analyst_agent.warehouse import Result


@dataclass(frozen=True)
class ResultWarning:
    code: str
    message: str


def review(result: Result) -> list[ResultWarning]:
    warnings: list[ResultWarning] = []
    if not result.rows:
        warnings.append(
            ResultWarning(
                "empty",
                "The query returned no rows. Check filter values and join keys before "
                "concluding there is nothing.",
            )
        )
        return warnings
    for i, name in enumerate(result.columns):
        if all(row[i] is None for row in result.rows):
            warnings.append(ResultWarning("all_null", f"Every value of {name} is null."))
    if result.truncated:
        warnings.append(
            ResultWarning(
                "truncated",
                f"The result stopped at the row limit ({len(result.rows)} rows); anything "
                "computed from it covers only part of the data.",
            )
        )
    duplicates = len(result.rows) - len(set(result.rows))
    if duplicates and len(result.rows) > 1:
        warnings.append(
            ResultWarning(
                "duplicate_rows",
                f"{duplicates} rows repeat another row exactly; a join may be multiplying rows.",
            )
        )
    return warnings
