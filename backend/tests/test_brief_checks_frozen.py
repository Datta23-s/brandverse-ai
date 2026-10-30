"""The evaluation's frozen brief checks (app/evaluation/brief_checks.py): one record per request.

The brief check is a property of the REQUEST; it is computed once and every arm reads that record,
so the arms cannot be flagged differently by a model's variation. These tests pin the storage: the
file and its lock, a lock that does not match is refused, a record made from other inputs is stale,
and the evaluation's brief is built in one place. They make no model call; what the 30 records say
is whatever the one precompute run found (docs/evaluation-plan.md).
"""

from pathlib import Path

import pytest

from app.core.brief_check import assess, brief_sha256, claims_sha256
from app.core.gates import Statement
from app.evaluation import brief_checks
from app.evaluation.test_set import request_inputs


def record_for(rid: str, unbacked: tuple[str, ...] = ()) -> dict[str, object]:
    request = request_inputs()[rid]
    return {
        "status": "checked",
        "unbacked": list(unbacked),
        "backed": len(request.claims),
        "prompt": "claim_extract@v2",
        "request": rid,
        "brief_sha256": brief_sha256(request.brief),
        "claims_sha256": claims_sha256(request.claims),
    }


def test_all_thirty_requests_have_one_brief_and_a_nonempty_claim_list() -> None:
    inputs = request_inputs()
    assert list(inputs) == [f"R{i:02d}" for i in range(1, 31)]
    for r in inputs.values():
        assert r.brief.endswith(f"Audience: {r.audience}.") and r.claims
    assert request_inputs()["R10"].brief == (
        "Launch awareness for the lightweight walking shoe. Audience: Office workers who walk to "
        "and from transit."
    )


def test_freeze_then_load_round_trips_and_the_lock_binds_the_file(tmp_path: Path) -> None:
    file, lock = tmp_path / "b.json", tmp_path / "b.lock"
    brief_checks.freeze({"R10": record_for("R10", ("lightweight walking shoe",))}, file, lock)
    assert brief_checks.load(file, lock)["R10"]["unbacked"] == ["lightweight walking shoe"]
    file.write_text(file.read_text(encoding="utf-8").replace("lightweight", "heavy"))  # edited
    with pytest.raises(brief_checks.FrozenBriefChecksError, match="does not match"):
        brief_checks.load(file, lock)


def test_a_missing_file_or_lock_is_refused_not_treated_as_no_flags(tmp_path: Path) -> None:
    with pytest.raises(brief_checks.FrozenBriefChecksError, match="missing"):
        brief_checks.load(tmp_path / "none.json", tmp_path / "none.lock")


def test_a_record_made_from_other_inputs_is_stale_and_a_missing_one_is_too() -> None:
    records = {"R10": record_for("R10"), "R11": {**record_for("R11"), "brief_sha256": "0" * 64}}
    stale = brief_checks.stale_requests(records)
    assert "R10" not in stale and "R11" in stale and "R01" in stale  # R01: no record at all
    assert len(stale) == 29


def test_a_frozen_record_is_what_a_run_gets_and_it_is_a_copy() -> None:
    records = {"R10": record_for("R10", ("lightweight walking shoe",))}
    got = brief_checks.for_request("R10", records)
    got["unbacked"] = []  # type: ignore[index]
    assert records["R10"]["unbacked"] == ["lightweight walking shoe"]
    with pytest.raises(brief_checks.FrozenBriefChecksError, match="no brief check"):
        brief_checks.for_request("R11", records)


def test_the_record_a_run_assesses_matches_the_one_the_evaluation_hashes() -> None:
    """`assess` (a run's own check) and the frozen record hash the same brief and claims."""
    from tests.test_v11_audience_and_run_columns import R10_TRUTH

    request = request_inputs()["R10"]
    made = assess(request.brief, R10_TRUTH, [Statement("lightweight walking shoe", None)])
    assert made["brief_sha256"] == brief_sha256(request.brief)
    assert made["claims_sha256"] == claims_sha256(request.claims)  # R10's claims, by text


def test_if_the_frozen_file_exists_it_matches_its_lock_and_covers_every_request() -> None:
    if not brief_checks.FILE.is_file():
        pytest.skip("not precomputed yet (needs the owner's approval of the calls)")
    records = brief_checks.load()
    assert brief_checks.stale_requests(records) == []
