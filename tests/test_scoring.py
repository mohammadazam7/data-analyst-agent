import datetime as dt
import decimal

import pytest

from data_analyst_agent.scoring import compare


def test_identical_results_match() -> None:
    assert compare([(1, "a"), (2, "b")], [(1, "a"), (2, "b")]).match


def test_row_order_is_ignored_unless_the_question_asks_for_one() -> None:
    gold = [(1, "a"), (2, "b")]
    flipped = [(2, "b"), (1, "a")]
    assert compare(gold, flipped).match
    result = compare(gold, flipped, ordered=True)
    assert not result.match
    assert result.reason == "different values"


def test_column_order_is_ignored() -> None:
    assert compare([("north", 10), ("south", 3)], [(3, "south"), (10, "north")]).match


def test_numbers_match_within_tolerance_and_across_types() -> None:
    assert compare([(0.3,)], [(0.1 + 0.2,)]).match
    assert compare([(12,)], [(12.0,)]).match
    assert compare([(decimal.Decimal("2.50"),)], [(2.5,)]).match
    assert not compare([(0.3,)], [(0.31,)]).match


def test_tolerant_numbers_still_pair_rows_in_any_order() -> None:
    gold = [("a", 0.3), ("b", 1.0 / 3)]
    predicted = [("b", 0.333333333333), ("a", 0.1 + 0.2)]
    assert compare(gold, predicted).match


def test_duplicates_count() -> None:
    assert not compare([(1,), (1,), (2,)], [(1,), (2,), (2,)]).match


def test_dates_compare_by_value() -> None:
    assert compare([(dt.date(2018, 3, 1),)], [(dt.date(2018, 3, 1),)]).match
    assert not compare([(dt.date(2018, 3, 1),)], [(dt.date(2018, 3, 2),)]).match


def test_null_matches_only_null() -> None:
    assert compare([(None,)], [(None,)]).match
    assert not compare([(None,)], [(0,)]).match
    assert not compare([(None,)], [("",)]).match


@pytest.mark.parametrize(
    ("gold", "predicted", "reason"),
    [
        ([(1,)], [(1, 2)], "1 gold columns, 2 predicted"),
        ([(1,), (2,)], [(1,)], "2 gold rows, 1 predicted"),
        ([(1,)], [], "1 gold rows, 0 predicted"),
    ],
)
def test_shape_mismatches_say_what_differs(
    gold: list[tuple[object, ...]], predicted: list[tuple[object, ...]], reason: str
) -> None:
    result = compare(gold, predicted)
    assert not result.match
    assert result.reason == reason


def test_two_empty_results_match() -> None:
    assert compare([], []).match
