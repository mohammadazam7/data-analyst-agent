from pathlib import Path

import pytest

from data_analyst_agent.bench import SuiteError, load_suite, parse_suite

SUITES = Path(__file__).resolve().parents[1] / "benchmarks"


def _suite(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "name": "t",
        "schemas": ["marts"],
        "question": [{"id": "q1", "question": "How many?", "gold": "select 1 from marts.t"}],
    }
    data.update(overrides)
    return data


def test_the_commerce_suite_loads_and_every_gold_query_passes_the_guardrails() -> None:
    suite = load_suite(SUITES / "commerce" / "questions.toml")
    assert suite.name == "commerce"
    assert suite.schemas == ("marts",)
    assert len(suite.questions) == 12
    assert len({q.id for q in suite.questions}) == 12
    assert all(q.gold.lower().startswith("select") for q in suite.questions)


def test_defaults_for_tags_and_order() -> None:
    [q] = parse_suite(_suite()).questions
    assert (q.tags, q.ordered) == ((), False)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"name": ""}, "needs a name"),
        ({"schemas": []}, "schemas"),
        ({"question": []}, "no \\[\\[question\\]\\]"),
        ({"question": [{"id": "q1", "question": "x"}]}, "'gold'"),
        ({"question": [{"id": "q1", "question": "x", "gold": "select 1", "level": 2}]}, "unknown"),
        (
            {"question": [{"id": "q1", "question": "x", "gold": "select 1", "ordered": "yes"}]},
            "ordered",
        ),
        (
            {"question": [{"id": "a", "question": "x", "gold": "select 1"}] * 2},
            "duplicate question ids",
        ),
        (
            {"question": [{"id": "q1", "question": "x", "gold": "delete from marts.t"}]},
            "gold query of q1 is refused",
        ),
        (
            {"question": [{"id": "q1", "question": "x", "gold": "select * from raw.t"}]},
            "gold query of q1 is refused",
        ),
    ],
)
def test_bad_suites_are_refused(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(SuiteError, match=message):
        parse_suite(_suite(**overrides))


def test_a_file_that_is_not_toml_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "q.toml"
    path.write_text("name = ")
    with pytest.raises(SuiteError, match=r"q\.toml"):
        load_suite(path)

