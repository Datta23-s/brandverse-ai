"""robots.txt matching (app/research/robots.py): RFC 9309's algorithm, not the standard library's.

The first test group is the reason the module exists: where `urllib.robotparser` (first matching
rule wins) and RFC 9309 (longest match wins, Allow wins a tie) disagree.
"""

import urllib.robotparser

import pytest

from app.research.robots import RobotsRules, agent_token

AGENT = "BrandverseResearch"
HOST = "https://shop.example.test"


def rules(text: str, agent: str = AGENT) -> RobotsRules:
    return RobotsRules.parse(text, agent)


def allowed(text: str, path: str, agent: str = AGENT) -> bool:
    return rules(text, agent).allows(HOST + path)


# ── Where the longest match, not the first, decides ──────────────────────────────────────────

DISALLOW_ALL_BUT_PUBLIC = "User-agent: *\nDisallow: /\nAllow: /public/\n"


def test_the_longest_matching_rule_wins_not_the_first() -> None:
    assert allowed(DISALLOW_ALL_BUT_PUBLIC, "/public/page") is True
    assert allowed(DISALLOW_ALL_BUT_PUBLIC, "/private/page") is False
    # The standard library reads the same file the other way round: this is why it is not used.
    stdlib = urllib.robotparser.RobotFileParser()
    stdlib.parse(DISALLOW_ALL_BUT_PUBLIC.splitlines())
    assert stdlib.can_fetch(AGENT, HOST + "/public/page") is False


def test_allow_wins_a_tie_of_equal_length() -> None:
    assert allowed("User-agent: *\nDisallow: /page\nAllow: /page\n", "/page") is True
    assert allowed("User-agent: *\nAllow: /page\nDisallow: /page\n", "/page") is True


def test_a_longer_disallow_beats_a_shorter_allow() -> None:
    text = "User-agent: *\nAllow: /shop/\nDisallow: /shop/cart/\n"
    assert allowed(text, "/shop/kadai") is True
    assert allowed(text, "/shop/cart/1") is False


# ── Which group applies ──────────────────────────────────────────────────────────────────────


def test_our_own_group_beats_the_wildcard_group_entirely() -> None:
    text = "User-agent: *\nDisallow: /\n\nUser-agent: BrandverseResearch\nDisallow: /private/\n"
    assert allowed(text, "/products") is True  # the wildcard's Disallow: / does not leak in
    assert allowed(text, "/private/x") is False


def test_a_site_can_block_us_by_name_and_allow_everyone_else() -> None:
    text = "User-agent: brandverseresearch\nDisallow: /\n\nUser-agent: *\nDisallow:\n"
    assert allowed(text, "/anything") is False  # the token is matched case-insensitively
    assert allowed(text, "/anything", agent="SomeoneElse") is True


def test_the_wildcard_group_applies_when_none_names_us() -> None:
    text = "User-agent: Googlebot\nDisallow: /g/\n\nUser-agent: *\nDisallow: /w/\n"
    assert allowed(text, "/w/x") is False and allowed(text, "/g/x") is True


def test_several_user_agent_lines_share_one_group() -> None:
    text = "User-agent: Googlebot\nUser-agent: BrandverseResearch\nDisallow: /shared/\n"
    assert allowed(text, "/shared/x") is False and allowed(text, "/y") is True


def test_two_groups_naming_us_are_merged() -> None:
    text = (
        "User-agent: BrandverseResearch\nDisallow: /a/\n\n"
        "User-agent: Other\nDisallow: /b/\n\n"
        "User-agent: BrandverseResearch\nDisallow: /c/\n"
    )
    assert (allowed(text, "/a/x"), allowed(text, "/b/x"), allowed(text, "/c/x")) == (
        False,
        True,
        False,
    )


def test_no_group_for_us_and_no_wildcard_allows_everything() -> None:
    assert allowed("User-agent: Googlebot\nDisallow: /\n", "/x") is True


# ── The pattern language ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("/fish", "/fish", False),
        ("/fish", "/fish.html", False),  # a prefix match
        ("/fish", "/fishy/x", False),
        ("/fish", "/Fish", True),  # paths are case-sensitive
        ("/fish/", "/fish", True),  # the trailing slash is part of the pattern
        ("/*.php", "/index.php", False),
        ("/*.php", "/a/b/index.php?x=1", False),
        ("/*.php", "/index.html", True),
        ("/*.php$", "/index.php", False),
        ("/*.php$", "/index.php?x=1", True),  # $ anchors: the query makes it not end in .php
        ("/a*b*c", "/aXXbYYc", False),
        ("/a*b*c", "/aXXbYY", True),
        ("/*?session=", "/p?session=1", False),
        ("/", "/anything", False),
        ("", "/anything", True),  # an empty Disallow allows everything
    ],
)
def test_pattern_matching(pattern: str, path: str, expected: bool) -> None:
    assert allowed(f"User-agent: *\nDisallow: {pattern}\n", path) is expected


def test_the_query_string_is_part_of_what_is_matched() -> None:
    text = "User-agent: *\nDisallow: /search?q=\n"
    assert allowed(text, "/search?q=kadai") is False
    assert allowed(text, "/search") is True


def test_percent_escapes_are_normalised_before_comparing() -> None:
    assert allowed("User-agent: *\nDisallow: /~private/\n", "/%7Eprivate/x") is False
    assert allowed("User-agent: *\nDisallow: /%7Eprivate/\n", "/~private/x") is False
    assert allowed("User-agent: *\nDisallow: /a/b\n", "/a%2Fb") is True  # %2F is not a slash


def test_robots_txt_itself_is_always_allowed() -> None:
    assert allowed("User-agent: *\nDisallow: /\n", "/robots.txt") is True


def test_the_root_path_of_a_bare_origin() -> None:
    assert rules("User-agent: *\nDisallow: /\n").allows("https://shop.example.test") is False


# ── The file format ──────────────────────────────────────────────────────────────────────────


def test_comments_blank_lines_case_and_unknown_fields_are_tolerated() -> None:
    text = (
        "# a comment\n\nUSER-AGENT: *   # everyone\ndisallow: /x/  # no\n"
        "Crawl-delay: 10\nSitemap: https://shop.example.test/sitemap.xml\nHost: shop.example.test\n"
    )
    assert allowed(text, "/x/y") is False and allowed(text, "/z") is True


def test_a_rule_before_any_user_agent_line_is_ignored() -> None:
    assert allowed("Disallow: /\nUser-agent: *\nDisallow: /x/\n", "/y") is True


def test_garbage_is_no_rules_not_a_crash() -> None:
    for junk in ("", "<html><body>404 Not Found</body></html>", "\x00\x01\x02", ":::\n::"):
        assert rules(junk).allows(HOST + "/anything") is True


def test_allow_all_allows_everything() -> None:
    assert RobotsRules.allow_all().allows(HOST + "/anything") is True


# ── The agent token ──────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("user_agent", "token"),
    [
        ("BrandverseResearch/0.1 (academic research project)", "BrandverseResearch"),
        ("BrandverseResearch", "BrandverseResearch"),
        ("  BrandverseResearch/2  ", "BrandverseResearch"),
        ("Mozilla/5.0 (X11)", "Mozilla"),
    ],
)
def test_the_agent_token_is_the_product_name(user_agent: str, token: str) -> None:
    assert agent_token(user_agent) == token
