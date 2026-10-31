"""Whose page is it: the brand's own, or someone else's (threat V6, Phases.md Phase 6 chunk D).

A brand's own domain is HIGH authority about its own product (does this source know?) and ZERO
independence (does it have an interest in the answer?), and those are different properties. The rule
(owner, 2026-10-26): a first-party source is acceptable evidence for a FACTUAL PROPERTY of the
brand's own product; it is not acceptable as the SOLE evidence for a comparative or efficacy claim.

This module answers only the first question, mechanically: does a URL's host belong to one of the
brand's domains? The brand's domains are data about the brand (`brands.domains`, migration 0039),
set by the brand's owner and audited, not guessed. With no domain known the answer is None, never
False:
"no domain registered" must not read as "independent".

A host matches a domain on a LABEL BOUNDARY, as the authority classes do: `shop.neemans.com` and
`neemans.com` match `neemans.com`; `notneemans.com` does not; a leading `www.` is not significant.
It is a string comparison on registrable names the owner typed. It does not know that two different
domains belong to one company (a brand's separate shop or press domain), so every domain the brand
uses must be listed, and a domain left off is read as independent.
"""

import re
from collections.abc import Collection
from urllib.parse import urlsplit

MAX_DOMAINS = 10
_LABEL = r"[a-z0-9]([a-z0-9-]*[a-z0-9])?"
DOMAIN_PATTERN = re.compile(rf"^(?!www\.)({_LABEL}\.)+{_LABEL}$")


def normalise_domain(value: str) -> str:
    """A bare lowercase hostname from what an owner typed: `https://www.Neemans.com/shop` becomes
    `neemans.com`. Raises ValueError for anything that is not a hostname of two or more labels."""
    text = value.strip().casefold()
    if "//" in text:
        text = urlsplit(text).hostname or ""
    text = text.split("/", 1)[0].split(":", 1)[0].removeprefix("www.").rstrip(".")
    if not DOMAIN_PATTERN.match(text):
        raise ValueError(f"{value!r} is not a domain name (expected something like example.com)")
    return text


def host_of(url_or_host: str) -> str | None:
    text = url_or_host.strip()
    host = (
        urlsplit(text).hostname if "//" in text else text.split("/", 1)[0].split(":", 1)[0]
    ) or ""
    host = host.casefold().rstrip(".").removeprefix("www.")
    return host or None


def is_first_party(url: str | None, domains: Collection[str]) -> bool | None:
    """True if the URL's host is one of the brand's domains or a subdomain of one; False if it is
    not; None when no domain is known (or there is no URL): "unknown" is not "independent"."""
    if not domains or not url:
        return None
    host = host_of(url)
    if host is None:
        return None
    return any(host == d or host.endswith("." + d) for d in domains)
