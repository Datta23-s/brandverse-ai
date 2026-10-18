"""The quantity check behind the `unrepeated_quantity` flag (app/core/quantities.py;
docs/evaluation-plan.md, threat V4). Each test is one point of the module's contract."""

from decimal import Decimal

import pytest

from app.core.quantities import Quantity, quantities, unrepeated_quantities

CLAIM_60H = "Up to 60 hours playback (at 60% volume)"  # the claim V4 was found with


def dropped(claim: str, statement: str) -> list[str]:
    return [q.text for q in unrepeated_quantities(claim, statement)]


def test_the_v4_example_is_flagged_with_both_quantities_named() -> None:
    assert dropped(CLAIM_60H, "Lasts all week.") == ["60 hours", "60%"]


@pytest.mark.parametrize(
    "statement",
    [
        "Lasts 60 hours at 60% volume.",
        "60h of playback at 60 percent volume.",
        "A 60-hour battery, at 60% volume.",
        "Up to 60 HOURS at 60 % volume",
    ],
)
def test_the_same_quantities_in_other_spellings_are_repeated(statement: str) -> None:
    assert dropped(CLAIM_60H, statement) == []


def test_every_quantity_must_be_repeated_so_a_dropped_condition_is_flagged() -> None:
    assert dropped(CLAIM_60H, "Lasts up to 60 hours.") == ["60%"]


def test_a_different_number_or_a_different_unit_does_not_repeat_it() -> None:
    assert dropped("Up to 60 hours playback", "Lasts 80 hours.") == ["60 hours"]
    assert dropped("Up to 60 hours playback", "Charges in 60 minutes.") == ["60 hours"]


def test_numbers_inside_words_are_not_quantities() -> None:
    assert quantities("IPX7 rating on the Tidewell Loop N2, firmware v1.2") == ()


def test_numbers_compare_by_value() -> None:
    assert dropped("Rated for 1,000 cycles", "Rated for 1000 cycles") == []
    assert dropped("Trusted by 1,00,000 users", "100000 users") == []
    assert dropped("Weighs 2.50 kg", "Just 2.5 kg") == []


def test_only_listed_words_are_units_so_a_bare_number_matches_with_or_without_one() -> None:
    assert quantities("Bluetooth 5.3 connectivity") == (Quantity(Decimal("5.3"), None, "5.3"),)
    assert dropped("Bluetooth 5.3 connectivity", "Bluetooth 5.3 keeps you paired") == []


def test_a_claim_with_no_quantity_is_untouched() -> None:
    assert dropped("Clinically shown to support recovery", "Proven to transform recovery") == []
    assert dropped("Upper made of recycled mesh", "Made from waste.") == []


def test_number_words_are_not_read_so_a_restatement_in_words_is_flagged() -> None:
    # Stated in V4: the reviewer clears these.
    assert dropped("Up to 60 hours playback", "Two and a half days of playback.") == ["60 hours"]


def test_a_qualifier_in_words_dropped_from_a_repeated_number_is_not_caught() -> None:
    # Stated in V4: "up to" is not a quantity, so dropping it passes.
    assert dropped("Up to 60 hours playback", "Lasts 60 hours.") == []
