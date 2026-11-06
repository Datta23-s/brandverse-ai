"""What one run can cost in model calls, derived from the registered pipeline and not from a
measurement (decision-log 2.36 item 5, the cap audit).

Three caps bit this project in a week (the per-run call cap, the attempt cap, the A2 page limit) and
each was an unexamined constant. Every cap on the evaluation path now has a derivation here or in
tests/test_cap_audit.py: the registered parameter it bounds, that parameter's worst case, and a
test that the cap exceeds it.

The pipeline's logical model calls (each is one `structured` request; a repair or a retry is an
EXTRA row, counted separately):

- step 3: the brief check, 1 (ADMISSION);
- step 7: A1, A2 and A3, 3 (A2 does not run at D0, so 3 is the worst case);
- steps 10 and 11, per draft: A6 writes, the claim classifier extracts the statements, A7 reads
  the risk, 3; a run has `1 + max_revision_rounds` drafts.
"""

BRIEF_CHECK_CALLS = 1
FAN_OUT_CALLS = 3
CALLS_PER_DRAFT = 3


def logical_calls(max_revision_rounds: int) -> int:
    """The calls a run makes with no repair and no retry, at its most: every revision round used."""
    return BRIEF_CHECK_CALLS + FAN_OUT_CALLS + CALLS_PER_DRAFT * (1 + max_revision_rounds)


def worst_case_calls(max_revision_rounds: int, unavailable_total_cap: int) -> int:
    """Logical calls plus the rows the gateway may spend on provider-unavailable retries. A retry
    is its own row, and the per-run total cap bounds them (the streak alone missed a flaky one)."""
    return logical_calls(max_revision_rounds) + unavailable_total_cap


def sequential_model_calls(max_revision_rounds: int) -> int:
    """Calls that run one after another (the fan-out of A1, A2, A3 runs in parallel, so it counts
    once): the brief check, one fan-out slot, and three per draft."""
    return BRIEF_CHECK_CALLS + 1 + CALLS_PER_DRAFT * (1 + max_revision_rounds)
