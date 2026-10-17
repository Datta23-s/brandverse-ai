"""The declared "verbatim" rule for required disclaimers (app/core/sector_registry.py docstring)."""

import pytest

from app.core.sector_registry import verbatim_form

DISCLAIMER = "Consult a qualified professional before use."


@pytest.mark.parametrize(
    "copy",
    [
        DISCLAIMER,
        f"{DISCLAIMER} ",  # a trailing space never fails the gate
        f"  {DISCLAIMER}\n",
        "Consult a qualified\nprofessional before use.",  # wrapped
        "Consult  a qualified\tprofessional before use.",  # doubled, tab, non-breaking space
    ],
)
def test_layout_differences_are_not_wording_differences(copy: str) -> None:
    assert verbatim_form(DISCLAIMER) in verbatim_form(copy)


def test_canonically_equivalent_encodings_compare_equal() -> None:
    assert verbatim_form("Café") == verbatim_form("Café")


@pytest.mark.parametrize(
    "copy",
    [
        "consult a qualified professional before use.",  # case
        "Consult a qualified professional before use",  # punctuation
        "Consult a qualified professional prior to use.",  # wording
    ],
)
def test_case_punctuation_and_wording_are_part_of_the_disclaimer(copy: str) -> None:
    assert verbatim_form(DISCLAIMER) not in verbatim_form(copy)
