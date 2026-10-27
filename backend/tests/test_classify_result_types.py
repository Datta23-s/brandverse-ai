"""The PDF-share estimator (scripts/classify_result_types.py): the arithmetic, with no network.

It exists to answer a secondary question cheaply (owner, 2026-10-26), so what matters is that the
estimate is honest about its uncertainty and that the plan never asks for more than it says.
"""

import json
from collections import Counter
from pathlib import Path

import pytest

from scripts.classify_result_types import estimate, plan, urls_by_group, wilson


def test_wilson_interval_brackets_the_sample_rate_and_widens_when_the_sample_is_small() -> None:
    low, high = wilson(5, 50)
    assert low < 5 / 50 < high and 0.0 <= low and high <= 1.0
    small_low, small_high = wilson(1, 10)
    assert (small_high - small_low) > (high - low)  # a smaller sample is less sure


def test_wilson_with_nothing_sampled_is_total_ignorance_not_zero() -> None:
    assert wilson(0, 0) == (0.0, 1.0)


@pytest.mark.parametrize(("k", "n"), [(0, 20), (20, 20)])
def test_wilson_never_collapses_to_a_point_at_zero_or_all(k: int, n: int) -> None:
    low, high = wilson(k, n)
    assert high - low > 0.05  # zero PDFs in twenty does not mean zero PDFs


def test_the_share_adds_the_free_extension_count_to_the_sampled_rate_over_the_rest() -> None:
    # 100 URLs: 10 end in .pdf (no request). Of the other 90, 20 were asked:
    # 4 PDF, 14 html, 2 unknown.
    answers = Counter({"pdf": 4, "html": 14, "unknown: FetchFailed:timeout": 2})
    point, low, high = estimate(100, 10, answers)
    rate = 4 / 18  # unknown samples are left out of the rate, not counted as non-PDF
    assert point == pytest.approx((10 + rate * 90) / 100)
    assert low < point < high


def test_a_group_with_no_sample_gets_the_whole_range_not_just_its_free_floor() -> None:
    point, low, high = estimate(100, 10, Counter())
    assert point == pytest.approx(0.10)  # only the certain ones count in the point estimate
    assert low == pytest.approx(0.10) and high == pytest.approx(1.0)  # and the rest is unknown


def test_the_extension_floor_is_exact_and_costs_no_request() -> None:
    urls = [f"https://a.test/{n}.pdf" for n in range(3)] + [
        f"https://a.test/{n}" for n in range(97)
    ]
    by_extension, asked, unasked = plan(urls, sample=10, seed=1)
    assert len(by_extension) == 3 and len(asked) == 10 and len(unasked) == 87
    assert not set(by_extension) & set(asked)  # a .pdf URL is never asked
    assert set(asked) | set(unasked) | set(by_extension) == set(urls)


def test_the_plan_never_asks_for_more_than_it_has_and_never_twice() -> None:
    urls = [f"https://a.test/{n}" for n in range(5)]
    _, asked, unasked = plan(urls, sample=50, seed=1)
    assert sorted(asked) == sorted(urls) and unasked == []
    assert len(set(asked)) == len(asked)


def test_the_sample_is_random_but_fixed_by_the_seed() -> None:
    urls = [f"https://a.test/{n}" for n in range(200)]
    first = plan(urls, 50, seed=7)[1]
    assert first == plan(urls, 50, seed=7)[1]  # reproducible
    assert first != plan(urls, 50, seed=8)[1]  # and not just the first fifty
    assert first != urls[:50]


def test_a_url_with_pdf_in_its_name_but_not_its_path_end_is_not_counted_free() -> None:
    urls = ["https://a.test/pdf-guide", "https://a.test/x.pdf.html", "https://a.test/y.PDF?v=1"]
    by_extension, asked, _ = plan(urls, 10, seed=1)
    assert by_extension == ["https://a.test/y.PDF?v=1"]  # case-insensitive, the query is not path
    assert len(asked) == 2


def test_the_logs_are_read_for_urls_only_and_each_url_counts_once_per_group(tmp_path: Path) -> None:
    def search(group: str, *urls: str) -> str:
        return json.dumps({"kind": "search", "group": group, "urls": [[u, "brave"] for u in urls]})

    log = tmp_path / "probe.jsonl"
    log.write_text(
        "\n".join(
            [
                json.dumps({"kind": "header"}),
                search("regulated", "https://a.test/1", "https://a.test/2"),
                search("regulated", "https://a.test/2", "https://a.test/3"),  # 2 repeated
                search("general", "https://a.test/2"),  # same URL, other group: counted there too
                json.dumps({"kind": "event", "event": "break"}),
            ]
        ),
        encoding="utf-8",
    )
    got = urls_by_group([log])
    assert got["regulated"] == ["https://a.test/1", "https://a.test/2", "https://a.test/3"]
    assert got["general"] == ["https://a.test/2"]
