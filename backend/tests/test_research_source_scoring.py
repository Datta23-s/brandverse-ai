"""Source scoring (app/research/source_scoring.py; Architecture.md §6.2; Memory.md §3.1).

Three factors from config, a factor that cannot be computed is left out and named, and an item
backed by several pages takes the maximum, which is monotone: adding a source never lowers it.
"""

import random
from datetime import date

import pytest
from pydantic import ValidationError

from app.memory.scoring import SourceScoring, load_scoring
from app.research.extraction import ExtractedPage
from app.research.source_scoring import (
    authority,
    evidence_score,
    recency,
    score_source,
    specificity,
)

CFG = load_scoring().source
TODAY = date(2026, 10, 25)


def page(url: str = "https://example.test/a", **kw: object) -> ExtractedPage:
    base: dict[str, object] = {
        "url": url,
        "title": "Seasoning cast iron",
        "text": "How to season a cast iron pan with oil and heat.",
        "published": date(2026, 10, 25),
        "author": None,
        "sitename": None,
        "language": None,
    }
    return ExtractedPage(**(base | kw))  # type: ignore[arg-type]


# ── Authority ────────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("url", "expected_class"),
    [
        ("https://fssai.gov.in/page", "government_or_regulator"),
        ("https://www.nasa.gov/", "government_or_regulator"),
        ("https://gov.uk/", "government_or_regulator"),  # the suffix itself
        ("https://cs.stanford.edu/x", "academic"),
        ("https://www.iitb.ac.in/x", "academic"),
        ("https://old.reddit.com/r/x", "social_or_user_generated"),
        ("https://REDDIT.COM/r/x", "social_or_user_generated"),
        ("https://example.test/a", "unknown"),
        ("https://fakegov.in/a", "unknown"),  # a suffix matches on a label boundary only
        ("https://gov.in.example.test/a", "unknown"),  # and only at the end of the host
        ("not a url", "unknown"),
        ("", "unknown"),
    ],
)
def test_authority_is_the_configured_class_of_the_host(url: str, expected_class: str) -> None:
    score, name = authority(url, CFG)
    assert name == expected_class
    classes = {c.name: c.score for c in CFG.authority.classes} | {"unknown": CFG.authority.unknown}
    assert score == classes[name]


def test_the_longest_matching_suffix_wins() -> None:
    cfg = CFG.model_copy(
        update={
            "authority": CFG.authority.model_copy(
                update={
                    "classes": [
                        CFG.authority.classes[0].model_copy(
                            update={"name": "broad", "score": 0.9, "suffixes": ["in"]}
                        ),
                        CFG.authority.classes[1].model_copy(
                            update={"name": "narrow", "score": 0.3, "suffixes": ["gov.in"]}
                        ),
                    ]
                }
            )
        }
    )
    assert authority("https://fssai.gov.in/", cfg) == (0.3, "narrow")
    assert authority("https://example.in/", cfg) == (0.9, "broad")


# ── Recency ──────────────────────────────────────────────────────────────────────────────────


def test_recency_halves_every_half_life_from_the_pages_own_date() -> None:
    half = CFG.recency_half_life_days
    assert recency(TODAY, TODAY, CFG) == 1.0
    assert recency(date.fromordinal(TODAY.toordinal() - int(half)), TODAY, CFG) == pytest.approx(
        0.5
    )
    assert recency(
        date.fromordinal(TODAY.toordinal() - 2 * int(half)), TODAY, CFG
    ) == pytest.approx(0.25)


def test_a_future_date_is_not_older_than_today_and_no_date_is_no_recency() -> None:
    assert recency(date(2027, 1, 1), TODAY, CFG) == 1.0
    assert recency(None, TODAY, CFG) is None


# ── Specificity ──────────────────────────────────────────────────────────────────────────────


def test_specificity_is_the_share_of_distinct_content_words_found_whole() -> None:
    q = "How do we season a cast iron pan"  # content words: season, cast, iron, pan
    assert specificity(q, "Seasoning", "Season the cast iron pan well", CFG) == 1.0
    assert specificity(q, None, "Season the cast pan", CFG) == 0.75
    assert specificity(q, None, "Seasoning pans and irons", CFG) == 0.0  # whole words only
    assert specificity(q, "Cast iron", "A pan to season", CFG) == 1.0  # title counts


def test_specificity_ignores_case_accents_of_punctuation_and_repeats() -> None:
    assert specificity("Cast-IRON, cast iron!", None, "iron and cast", CFG) == 1.0


def test_a_query_with_no_content_words_has_no_specificity() -> None:
    assert specificity("how do we", None, "anything at all", CFG) is None
    assert specificity("", None, "anything", CFG) is None


# ── The score ────────────────────────────────────────────────────────────────────────────────


def test_a_complete_score_is_the_weighted_sum_and_names_nothing_left_out() -> None:
    s = score_source(
        page("https://cs.stanford.edu/x"), "season cast iron pan", today=TODAY, cfg=CFG
    )
    w = CFG.weights
    assert s.components == {"authority": 0.85, "recency": 1.0, "specificity": 1.0}
    assert s.value == pytest.approx(w.authority * 0.85 + w.recency * 1.0 + w.specificity * 1.0)
    assert s.left_out == () and s.authority_class == "academic"


def test_a_page_with_no_date_leaves_recency_out_and_renormalises() -> None:
    s = score_source(page(published=None), "season cast iron pan", today=TODAY, cfg=CFG)
    w = CFG.weights
    assert s.left_out == ("recency",) and "recency" not in s.components
    expected = (w.authority * 0.5 + w.specificity * 1.0) / (w.authority + w.specificity)
    assert s.value == pytest.approx(expected)


def test_an_off_topic_old_page_on_an_unknown_host_is_not_accepted() -> None:
    old = page(
        published=date(2019, 1, 1), title="Holiday deals", text="Cheap flights and hotel offers"
    )
    s = score_source(old, "season cast iron pan", today=TODAY, cfg=CFG)
    assert s.value < CFG.accept_at_or_above and not s.accepted


def test_acceptance_is_at_or_above_the_configured_threshold() -> None:
    p = page(published=None, text="season cast iron pan")
    exact = CFG.model_copy(
        update={
            "accept_at_or_above": score_source(
                p, "season cast iron pan", today=TODAY, cfg=CFG
            ).value
        }
    )
    assert score_source(p, "season cast iron pan", today=TODAY, cfg=exact).accepted


def test_the_score_is_in_the_unit_interval_whatever_the_page() -> None:
    rng = random.Random(1)  # noqa: S311 - a seeded test generator
    words = ["season", "cast", "iron", "pan", "oil", "flight", "hotel"]
    for _ in range(200):
        text = " ".join(rng.choices(words, k=rng.randint(1, 12)))
        p = page(
            rng.choice(["https://a.gov/x", "https://reddit.com/x", "https://e.test/x"]),
            text=text,
            published=rng.choice([None, date(2010, 1, 1), TODAY, date(2030, 1, 1)]),
        )
        s = score_source(p, "season cast iron pan", today=TODAY, cfg=CFG)
        assert 0.0 <= s.value <= 1.0


def test_scoring_a_recorded_page_end_to_end() -> None:
    from pathlib import Path

    from app.research.extraction import Extractor
    from tests.test_research_extraction import recorded

    fixtures = Path(__file__).parent / "fixtures" / "extraction"
    html = (fixtures / "article_with_boilerplate.html").read_text(encoding="utf-8")
    url = "https://fixtures.example.test/article_with_boilerplate.html"
    extracted = Extractor(recorded, min_chars=200, max_chars=20000).extract(html, url)
    s = score_source(extracted, "how to season cast iron", today=date(2026, 3, 15), cfg=CFG)
    assert s.authority_class == "unknown" and s.components["specificity"] == 1.0
    assert s.components["recency"] == pytest.approx(0.5 ** (1 / CFG.recency_half_life_days))
    assert s.accepted


# ── Several sources: the maximum ─────────────────────────────────────────────────────────────


def test_an_item_with_no_sources_has_no_score() -> None:
    assert evidence_score([]) is None


def test_the_maximum_of_the_sources_is_the_items_score() -> None:
    assert evidence_score([0.3, 0.9, 0.5]) == 0.9


def test_adding_a_source_never_lowers_the_items_score() -> None:
    rng = random.Random(2026)  # noqa: S311 - a seeded test generator
    for _ in range(500):
        sources = [rng.random() for _ in range(rng.randint(1, 6))]
        before = evidence_score(sources)
        assert before is not None
        for added in (0.0, rng.random(), 1.0):
            after = evidence_score([*sources, added])
            assert after is not None and after >= before


def test_the_mean_and_the_minimum_would_fail_what_the_maximum_passes() -> None:
    # The reason for the rule (Memory.md §3.1): a strong source corroborated by a mediocre one.
    strong, mediocre = 0.9, 0.5
    assert (strong + mediocre) / 2 < strong  # the mean lowers the item
    assert min(strong, mediocre) < strong  # the minimum lowers it more
    ev = evidence_score([strong, mediocre])
    assert ev is not None and ev >= strong


# ── The configuration ────────────────────────────────────────────────────────────────────────


def source_with(**changes: object) -> dict[str, object]:
    return CFG.model_dump() | changes


def test_the_weights_must_sum_to_one() -> None:
    with pytest.raises(ValidationError, match="sum to 1"):
        SourceScoring.model_validate(
            source_with(weights={"authority": 0.5, "recency": 0.5, "specificity": 0.5})
        )


def test_a_suffix_may_not_appear_in_two_classes_or_be_written_loosely() -> None:
    cls = CFG.authority.classes[0].model_dump()
    twice = {"unknown": 0.5, "classes": [cls, {**cls, "name": "again"}]}
    with pytest.raises(ValidationError, match="repeats"):
        SourceScoring.model_validate(source_with(authority=twice))
    loose = {"unknown": 0.5, "classes": [{**cls, "suffixes": [".GOV"]}]}
    with pytest.raises(ValidationError, match="lower-case"):
        SourceScoring.model_validate(source_with(authority=loose))


def test_every_number_is_a_probability_or_a_positive_duration() -> None:
    for field, bad in (("recency_half_life_days", 0), ("accept_at_or_above", 1.5)):
        with pytest.raises(ValidationError):
            SourceScoring.model_validate(source_with(**{field: bad}))
