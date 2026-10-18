"""Quantities a cited claim states that the citing statement must repeat (docs/evaluation-plan.md,
threat V4: citation does not imply entailment).

The claim gate checks that a cited claim exists, belongs to the product, is active and is
verified; it does not check that the claim says what the statement says. This is the mechanical
partial mitigation: when a cited claim states a quantity and the statement citing it does not
repeat it, the gate FLAGS the statement for the reviewer (`unrepeated_quantity`). It never blocks
and costs no model call.

The contract, in full:

1. A **quantity** is a number written in digits that stands on its own — not inside a word such as
   "IPX7" or "N2" — with the unit that follows it, if any: "60 hours", "60h", "60-hour", "10 mm",
   "60%". Digits only: "ten" or "two and a half days" is not read as a quantity.
2. **Numbers compare by value.** Commas are thousands separators ("1,000" and "1,00,000" read as
   1000 and 100000); "2.50" equals "2.5".
3. **A unit is a word in `UNITS`** (or "%") immediately after the number, separated by nothing, a
   space or a hyphen; its spellings compare as one ("h", "hrs", "hour", "hours"). Any other
   following word is not a unit: "Bluetooth 5.3 connectivity" is the bare number 5.3.
4. A claim's quantity **with a unit** is repeated only by the same value with the same unit; one
   **without a unit**, by the same value with or without one.
5. **Every** quantity in the claim must be repeated. "Up to 60 hours playback (at 60% volume)"
   cited for "Lasts 60 hours." is flagged: the 60% condition was dropped.

Not caught (V4): overstatement where the claim states no number ("clinically shown" cited to one
small study, "loved by thousands" cited to forty reviews), and qualifiers in words ("up to")
dropped from a repeated number. Those stay with the approver.
"""

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal
from types import MappingProxyType

# Canonical unit -> its spellings (compared after casefolding). Only these are units.
_UNIT_SPELLINGS: dict[str, tuple[str, ...]] = {
    "second": ("sec", "secs", "second", "seconds"),
    "minute": ("min", "mins", "minute", "minutes"),
    "hour": ("h", "hr", "hrs", "hour", "hours"),
    "day": ("day", "days"),
    "week": ("wk", "wks", "week", "weeks"),
    "month": ("month", "months"),
    "year": ("yr", "yrs", "year", "years"),
    "%": ("%", "percent"),
    "mm": ("mm",),
    "cm": ("cm",),
    "m": ("m", "metre", "metres", "meter", "meters"),
    "km": ("km",),
    "mg": ("mg",),
    "g": ("g", "gram", "grams"),
    "kg": ("kg", "kilogram", "kilograms"),
    "ml": ("ml",),
    "l": ("l", "litre", "litres", "liter", "liters"),
    "mah": ("mah",),
    "w": ("w", "watt", "watts"),
    "v": ("v", "volt", "volts"),
    "hz": ("hz",),
    "khz": ("khz",),
    "db": ("db",),
    "mb": ("mb",),
    "gb": ("gb",),
    "tb": ("tb",),
    "x": ("x",),
}
UNITS = MappingProxyType(
    {spelling: unit for unit, spellings in _UNIT_SPELLINGS.items() for spelling in spellings}
)

# A number not preceded by a word character, a point or a comma; then, optionally, a following
# token separated by nothing, a space or a hyphen. Whether that token is a unit is decided by UNITS.
_QUANTITY = re.compile(r"(?<![\w.,])(\d+(?:,\d+)*(?:\.\d+)?)(?:\s?-?\s?(%|[^\W\d_]+))?")


@dataclass(frozen=True, slots=True)
class Quantity:
    value: Decimal
    unit: str | None
    text: str = field(compare=False)  # as written, for the reviewer's message


def quantities(text: str) -> tuple[Quantity, ...]:
    """The quantities in `text`, in order (contract above)."""
    found = []
    for m in _QUANTITY.finditer(unicodedata.normalize("NFKC", text)):
        number, token = m.group(1), m.group(2)
        unit = UNITS.get(token.casefold()) if token else None
        written = m.group(0) if unit else number
        found.append(Quantity(Decimal(number.replace(",", "")).normalize(), unit, written))
    return tuple(found)


def unrepeated_quantities(claim: str, statement: str) -> tuple[Quantity, ...]:
    """The claim's quantities the statement does not repeat (contract, points 4 and 5)."""
    said = quantities(statement)
    return tuple(
        q
        for q in quantities(claim)
        if not any(s.value == q.value and (q.unit is None or s.unit == q.unit) for s in said)
    )
