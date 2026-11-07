"""SEQUENCE INTEGRITY: a Proposed campaign that wrote no memory BLOCKS the next round for its brand
(owner, 7 November 2026; decision-log 2.53).

Why this outranks the classification of a failure. The Proposed arm's campaign N+1 is supposed to
read what campaign N's approval wrote. If campaign N wrote nothing — an agent failed, the run ended
`partial` on the budget, a gate blocked it — then campaign N+1 is a COLD START wearing the Proposed
arm's label, and the brand has quietly become a no-memory arm inside the arm under test. Nothing
downstream reveals it: the depth would be high for the honest reason that there is no memory, the
decomposition would attribute no reduction, and the write-up would report it as the mechanism's
behaviour. **An approval is therefore NOT enough to release the next round.**

The rule is the one the stage-2 runner already enforced (`scripts/phase7_stage2.campaign_problem`,
5 November, after the provider answered 503 to every agent call and the interim was computed on
empty inputs). That file is registered and frozen, so it is not edited to import this one; instead
`tests/test_sequence_integrity.py` feeds both the same table of cases and fails if they ever
disagree. This module is the one the evaluation driver uses.

THE ONE CASE THAT WRITES NOTHING AND IS CORRECT: a campaign the ENGINE ran at D0. A2 does not run at
D0, so no market memory is written, by design (decision-log 2.38, 2.46). That is a pass, and the
reason it must be named here rather than left to a human reading a log is that it is exactly the
case a blanket "no memory means broken" rule would reject.
"""

from collections.abc import Mapping, Sequence
from typing import Any

# A campaign that ends in any of these has not produced what the next round needs.
TERMINAL_OK = "completed"


def campaign_problem(
    status: str | None,
    gaps: Sequence[Mapping[str, Any]],
    *,
    memory_items_written: int,
    depth: str | None,
    engine_chose_depth: bool,
) -> str | None:
    """Why this Proposed campaign cannot release the next round for its brand, or None.

    `memory_items_written`: rows this run's own write-back committed (`provenance = 'run:<id>'`), a
    count read from the database and never a counter. `engine_chose_depth`: the run used the
    necessity engine (the Proposed arm always does), which is what makes a D0 campaign's empty
    write-back correct rather than a failure."""
    if status != TERMINAL_OK:
        return f"the run ended {status!r}, not {TERMINAL_OK!r}"
    failed = sorted({str(g["agent"]) for g in gaps if g.get("agent")})
    if failed:
        errors = sorted({str(g.get("error")) for g in gaps if g.get("agent")})
        return (
            f"agents failed ({', '.join(failed)}; {', '.join(errors)}): the campaign has no "
            "complete copy and writes no memory"
        )
    if engine_chose_depth and depth == "D0":
        return None  # A2 does not run at D0: no memory BY DESIGN (decision-log 2.38)
    if memory_items_written <= 0:
        return (
            f"it wrote no memory (depth {depth}), so the brand's next campaign would be a cold "
            "start wearing the Proposed arm's label"
        )
    return None
