"""Threat V6 (chunk D): whose page is it, and what the claim gate does when the brand's own page is
the only evidence for a claim that needs more.

The rule (owner, 2026-10-26): a first-party source is acceptable evidence for a FACTUAL PROPERTY of
the brand's own product, and is not the sole evidence for a COMPARATIVE or EFFICACY claim. The
triggers are mechanical (comparative = the claim's wording trips the superlative list; efficacy = a
regulated sector), so a block cannot fire on a misclassification. With no brand domain
registered the answer is unknown, never "independent", and the rule does not fire.
"""

import pytest

from app.core.first_party import host_of, is_first_party, normalise_domain
from app.core.gates import evaluate, load_superlatives
from app.core.sector_registry import Sector
from app.core.truth import Claim, ProductTruth
from tests.test_claim_classifier import TODAY

pytestmark = pytest.mark.anyio
LEXICON = load_superlatives()
OWN = "https://shop.neemans.com/products/begin-walk-lite"
THEIRS = "https://www.consumer-reviews.example/neemans-begin-walk-lite"


# ── The matcher ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("typed", "stored"),
    [
        ("neemans.com", "neemans.com"),
        ("  Neemans.COM ", "neemans.com"),
        ("https://www.Neemans.com/shop?x=1", "neemans.com"),
        ("www.neemans.com", "neemans.com"),
        ("shop.neemans.com:443/path", "shop.neemans.com"),
        ("neemans.co.in.", "neemans.co.in"),
    ],
)
def test_what_an_owner_types_becomes_a_bare_lowercase_hostname(typed: str, stored: str) -> None:
    assert normalise_domain(typed) == stored


@pytest.mark.parametrize("bad", ["neemans", "", "https://", "a b.com", "-x.com", "x..com", "/shop"])
def test_what_is_not_a_domain_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="not a domain"):
        normalise_domain(bad)


def test_a_host_matches_on_a_label_boundary_and_www_is_not_significant() -> None:
    d = ["neemans.com"]
    assert is_first_party("https://neemans.com/a", d) is True
    assert is_first_party("https://www.neemans.com/a", d) is True
    assert is_first_party("https://SHOP.Neemans.com:8080/a", d) is True
    assert is_first_party("https://notneemans.com/a", d) is False
    assert is_first_party("https://neemans.com.evil.example/a", d) is False
    assert is_first_party("https://example.com/neemans.com", d) is False
    assert host_of("https://www.example.org/x") == "example.org"


def test_unknown_is_never_independent() -> None:
    assert is_first_party("https://neemans.com/a", []) is None  # no domain registered
    assert is_first_party(None, ["neemans.com"]) is None  # no URL
    assert is_first_party("", ["neemans.com"]) is None


# ── The claim gate ───────────────────────────────────────────────────────────────────────────


def truth(
    claim_text: str,
    url: str,
    *,
    regulated: bool,
    domains: tuple[str, ...] = ("neemans.com",),
) -> ProductTruth:
    sector = Sector("health-supplements" if regulated else "footwear", regulated=regulated)
    return ProductTruth(sector, (Claim("c1", claim_text, "verified", url, False),), (), domains)


def gate(t: ProductTruth, quote: str):  # type: ignore[no-untyped-def]
    from app.core.gates import Statement

    return evaluate({"body": quote}, [Statement(quote, "c1")], t, LEXICON, TODAY)


def kinds(report) -> list[str]:  # type: ignore[no-untyped-def]
    """The finding kinds, without `registry_coverage`: these fixtures have a regulated sector but no
    registry rules, which fails closed for a reason that has nothing to do with this rule."""
    return [f.kind for f in report.findings if f.kind != "registry_coverage"]


def test_a_factual_property_from_the_brands_own_page_is_acceptable() -> None:
    report = gate(truth("Knit upper", OWN, regulated=False), "Knit upper")
    assert report.findings == () and len(report.resolutions) == 1


def test_in_a_regulated_sector_a_composition_fact_from_the_brands_own_page_is_acceptable() -> None:
    """The efficacy trigger (regulated sector) was dropped, 2026-10-31: "Contains Velositol" or
    "24 g protein" is a composition fact whose only correct source is the manufacturer, and the
    trigger blocked all of B07's verified claims while catching no effect claim."""
    report = gate(truth("Contains Velositol", OWN, regulated=True), "Contains Velositol")
    assert kinds(report) == [] and len(report.resolutions) == 1


def test_the_gap_the_dropped_trigger_leaves_an_effect_claim_mislabelled_verified_passes() -> None:
    """Stated, not hidden: an effect claim entered as `verified` (it should be `restricted`) and
    resting on the brand's own page is not caught by anything here. The principled fix is the
    claim's nature as an audited field (decision-log V6, option c), deferred."""
    claim = "Reduces bloating within a week"
    report = gate(truth(claim, OWN, regulated=True), claim)
    assert kinds(report) == []  # nothing in this rule objects


def test_the_same_claim_on_an_independent_page_is_fine_in_a_regulated_sector() -> None:
    report = gate(truth("Contains Velositol", THEIRS, regulated=True), "Contains Velositol")
    assert kinds(report) == [] and len(report.resolutions) == 1


def test_a_comparative_claim_on_the_brands_own_page_is_blocked() -> None:
    claim = "The best selling walking shoe in its class"
    report = gate(truth(claim, OWN, regulated=False), claim)
    assert "first_party_sole_evidence" in kinds(report) and report.outcome == "block"
    (f,) = [f for f in report.findings if f.kind == "first_party_sole_evidence"]
    assert "superlative list" in f.message


def test_a_comparative_claim_on_an_independent_page_does_not_trip_this_rule() -> None:
    claim = "The best selling walking shoe in its class"
    assert "first_party_sole_evidence" not in kinds(
        gate(truth(claim, THEIRS, regulated=False), claim)
    )


def test_with_no_brand_domain_registered_first_party_is_unknown_and_nothing_fires() -> None:
    claim = "The best selling walking shoe in its class"
    t = truth(claim, OWN, regulated=False, domains=())
    assert t.first_party_evidence(t.claims[0]) is None
    assert "first_party_sole_evidence" not in kinds(gate(t, claim))


def test_a_subdomain_of_a_registered_domain_is_the_brands_own() -> None:
    t = truth("Contains Velositol", "https://blog.neemans.com/x", regulated=True)
    assert t.first_party_evidence(t.claims[0]) is True


def test_a_lookalike_domain_is_not_the_brands_own() -> None:
    t = truth("Contains Velositol", "https://neemans.com.evil.example/x", regulated=True)
    assert t.first_party_evidence(t.claims[0]) is False
    assert kinds(gate(t, "Contains Velositol")) == []


# ── The research driver populates first_party on its findings ────────────────────────────────


async def test_findings_carry_first_party_from_the_brands_domains() -> None:
    from app.research.driver import Mode, research
    from app.research.search import OfflineSearch
    from tests.test_research_driver import (
        CFG,
        EXTRACTOR,
        GOOD,
        Pages,
        plan,
        result,
    )
    from tests.test_research_driver import (
        TODAY as DRIVER_TODAY,
    )

    first = plan().queries[0].text
    search = OfflineSearch({first: [result(OWN), result(THEIRS)]})
    pages = Pages({OWN: GOOD, THEIRS: GOOD})
    kw = dict(
        mode=Mode.PRODUCTION,
        search=search,
        pages=pages,
        extractor=EXTRACTOR,
        source_cfg=CFG.source,
        today=DRIVER_TODAY,
    )
    known = await research(plan(), "D1", brand_domains=("neemans.com",), **kw)  # type: ignore[arg-type]
    by_url = {f.page.url: f.first_party for f in known.findings}
    assert by_url == {OWN: True, THEIRS: False}
    unknown = await research(plan(), "D1", **kw)  # type: ignore[arg-type]
    assert {f.first_party for f in unknown.findings} == {None}  # none registered: unknown


# ── THE DEFLATING RESULT: on the evaluation's own claims this rule binds nothing ─────────────


def test_on_the_evaluations_128_verified_claims_the_sole_evidence_rule_fires_on_none() -> None:
    """Every one of the 30 requests' verified claims rests on its brand's own page, and the brands'
    domains are registered, so first-party MARKING is complete (the claim gate, A2 and the findings
    all see it). But the rule needs a COMPARATIVE claim to act on, none of the 128 trips the
    superlative list, and the efficacy trigger was dropped. The mechanism exists, is tested above,
    and has no instance in this evaluation to act on. Do not read the rule's presence as a check
    that fired anywhere: it did not."""
    from app.core.gates import _first_party_sole
    from app.evaluation.test_set import plan_requests, request_inputs

    regulated = {rid: p.regulated for rid, p in plan_requests().items()}
    marked = bound = total = 0
    for rid, r in request_inputs().items():
        t = ProductTruth(
            Sector(r.sector, regulated=regulated[rid]),
            tuple(
                Claim(f"{rid}:{i}", text, "verified", r.evidence_url, False)
                for i, text in enumerate(r.claims)
            ),
            (),
            r.domains,
        )
        for c in t.claims:
            total += 1
            marked += t.first_party_evidence(c) is True
            bound += _first_party_sole(c, t, LEXICON) is not None
    assert (total, marked, bound) == (128, 128, 0)
