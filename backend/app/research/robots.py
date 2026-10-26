"""robots.txt, as RFC 9309 reads it (the matching only: fetching it is the fetcher's job).

Why not `urllib.robotparser`: the standard library applies the FIRST rule that matches, in file
order. RFC 9309, and the crawlers the files are written for, apply the LONGEST matching rule and let
`Allow` win a tie. A file that says `Disallow: /` then `Allow: /public/` is read by one as "nothing"
and by the other as "/public/ only", so inheriting the difference would mean obeying sites
wrongly in one direction or the other. This is the RFC's algorithm, short enough to test in full.

Rules, in short:
- a group is one or more `User-agent` lines followed by `Allow` / `Disallow` lines. Field names are
  case-insensitive, `#` starts a comment, unknown fields are ignored;
- the groups whose agent token equals ours (case-insensitively) apply, and are merged if there are
  several; only if none matches do the `*` groups apply; with none of either, everything is allowed;
- a path matches a rule if the rule's pattern matches its start. `*` matches any run of characters
  and a trailing `$` anchors the end. The longest pattern (by length as written) wins, `Allow`
  wins a tie, an empty `Disallow` allows everything, and `/robots.txt` itself is always allowed;
- the path compared is the URL's path plus its query, with percent-escapes normalised.
"""

import re
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

_FIELD = re.compile(r"^\s*([A-Za-z-]+)\s*:\s*(.*?)\s*$")


@dataclass(frozen=True, slots=True)
class _Rule:
    allow: bool
    pattern: str
    regex: re.Pattern[str]


def _compile(pattern: str) -> re.Pattern[str]:
    anchored = pattern.endswith("$")
    body = pattern[:-1] if anchored else pattern
    regex = ".*".join(re.escape(part) for part in body.split("*"))
    return re.compile(regex + ("$" if anchored else ""), re.DOTALL)


def _normal(path: str) -> str:
    """Percent-escapes decoded, so `%7Efoo` and `~foo` compare equal. Reserved characters that
    carry meaning (`%2F`) are left as they are."""
    return re.sub(r"%(?!2[fF])([0-9A-Fa-f]{2})", lambda m: unquote(m.group(0)), path)


class RobotsRules:
    """The rules that apply to one agent token."""

    def __init__(self, rules: list[_Rule]) -> None:
        self._rules = rules

    @classmethod
    def allow_all(cls) -> "RobotsRules":
        return cls([])

    @classmethod
    def parse(cls, text: str, agent: str) -> "RobotsRules":
        token = agent.casefold()
        groups: list[tuple[list[str], list[_Rule]]] = []
        agents: list[str] = []
        rules: list[_Rule] = []
        in_rules = False
        for raw in text.splitlines():
            line = raw.split("#", 1)[0]
            match = _FIELD.match(line)
            if not match:
                continue
            field, value = match.group(1).casefold(), match.group(2)
            if field == "user-agent":
                if in_rules:  # a new group begins
                    groups.append((agents, rules))
                    agents, rules, in_rules = [], [], False
                agents.append(value.casefold())
            elif field in ("allow", "disallow") and agents:
                in_rules = True
                if value:  # an empty Disallow allows everything: no rule at all
                    rules.append(_Rule(field == "allow", value, _compile(_normal(value))))
        if agents:
            groups.append((agents, rules))
        own = [g for g in groups if token in g[0]]
        chosen = own or [g for g in groups if "*" in g[0]]
        return cls([rule for _, group_rules in chosen for rule in group_rules])

    def allows(self, url: str) -> bool:
        parts = urlsplit(url)
        path = parts.path or "/"
        if path == "/robots.txt":
            return True
        target = _normal(path + (f"?{parts.query}" if parts.query else ""))
        best: _Rule | None = None
        for rule in self._rules:
            if rule.regex.match(target) and (
                best is None
                or len(rule.pattern) > len(best.pattern)
                or (len(rule.pattern) == len(best.pattern) and rule.allow and not best.allow)
            ):
                best = rule
        return best is None or best.allow


def agent_token(user_agent: str) -> str:
    """The product token of a User-Agent header (`BrandverseResearch/0.1 (...)`: the first word)."""
    return user_agent.strip().split("/", 1)[0].split()[0]
