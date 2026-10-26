"""Read a searxng_rate_probe.py log and report what it measured (docs/evaluation-plan.md section 8).

    uv run python scripts/summarise_search_rate.py ../evaluation/search_rate/probe-<start>.jsonl

Reports, from the log alone and with nothing assumed:
- searches made, and each as clean (answered, no engine named as failed), degraded (answered with a
  failed engine named) or refused (rate-limited / unavailable / error);
- time from the first search to the first clean one (recovery from the throttled state it started
  in), and the longest run of consecutive clean searches;
- per engine: in how many searches it was named as failed, with which reasons, and in how many it
  contributed rows;
- for every candidate declared engine set (each subset of the enabled engines), how many searches
  were complete with respect to it, the rate, and the hours to record 30 x q searches for q = 1, 2,
  3, 5. The rate at one fixed spacing is a lower bound on what the engines sustain (a shorter
  spacing may also be complete); the figure is only as good as that and says so. The table informs
  the declaration; it does not make it (docs/evaluation-plan.md section 8).
"""

import json
import sys
from collections import Counter
from datetime import datetime
from itertools import combinations
from pathlib import Path


def load(path: Path) -> tuple[dict[str, object], list[dict[str, object]]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    header = next(r for r in rows if r["kind"] == "header")
    return header, [r for r in rows if r["kind"] == "search"]


def kind_of(search: dict[str, object]) -> str:
    if search["outcome"] != "ok":
        return "refused"
    return "degraded" if search["unresponsive"] else "clean"


def complete_for(search: dict[str, object], declared: tuple[str, ...]) -> bool:
    """Complete with respect to a declared set: SearXNG itself answered (HTTP 200), and none of the
    set's engines is among those it named as unresponsive. Results are not required: a search that
    the declared engines answered and that found nothing is a genuine absence, not a failure."""
    if search["http_status"] != 200:
        return False
    named = {str(u[0]).casefold() for u in search["unresponsive"]}  # type: ignore[attr-defined]
    return not named & {e.casefold() for e in declared}


def hours_between(a: object, b: object) -> float:
    return (datetime.fromisoformat(str(b)) - datetime.fromisoformat(str(a))).total_seconds() / 3600


def main(path: Path) -> None:
    header, searches = load(path)
    print(f"{path.name}: engines {header['engines_enabled']}, SearXNG {header['searxng_version']}")
    print(f"spacing {header['interval_min']} min, planned {header['hours']} h")
    if not searches:
        print("no searches logged yet")
        return
    kinds = [kind_of(s) for s in searches]
    counts = Counter(kinds)
    first, last = searches[0]["utc"], searches[-1]["utc"]
    window_h = max(hours_between(first, last), 0)
    print(f"\n{len(searches)} searches over {window_h:.1f} h: {dict(counts)}")

    clean_at = next((s["utc"] for s, k in zip(searches, kinds, strict=True) if k == "clean"), None)
    if clean_at is None:
        print("never clean: no recovery from the throttled start within the log")
    else:
        print(f"first clean search {hours_between(first, clean_at) * 60:.0f} min after the first")
    longest = run = 0
    for k in kinds:
        run = run + 1 if k == "clean" else 0
        longest = max(longest, run)
    print(f"longest run of consecutive clean searches: {longest}")

    print("\nper engine (named as failed / contributed rows, of all searches):")
    for engine in header["engines_enabled"]:  # type: ignore[attr-defined]
        reasons = Counter(
            str(u[1])
            for s in searches
            for u in s["unresponsive"]  # type: ignore[attr-defined]
            if u[0] == engine
        )
        rows = sum(1 for s in searches if s["per_engine_rows"].get(engine))  # type: ignore[attr-defined]
        print(f"  {engine}: failed {sum(reasons.values())}, rows in {rows}  {dict(reasons)}")

    elapsed = window_h + float(header["interval_min"]) / 60  # type: ignore[arg-type]
    engines: list[str] = list(header["engines_enabled"])  # type: ignore[call-overload]
    print(
        f"\nSearches complete with respect to each candidate declared engine set"
        f"\n(complete = SearXNG answered and none of the set's engines was named as failed;"
        f"\nrate = per hour over {elapsed:.1f} h at {header['interval_min']}-minute spacing):"
    )
    searches_needed = (30, 60, 90, 150)  # 30 requests x q, q = 1, 2, 3, 5 (section 8)
    print(
        f"  {'declared set':36} {'complete':>9} {'rate/h':>7}   hours to record 30 x q, q = 1/2/3/5"
    )
    for size in range(1, len(engines) + 1):
        for subset in combinations(engines, size):
            n = sum(1 for s in searches if complete_for(s, subset))
            rate = n / elapsed if elapsed else 0.0
            hours = (
                "/".join(f"{needed / rate:.0f}" for needed in searches_needed)
                if rate > 0
                else "no complete search: no duration follows"
            )
            print(f"  {'+'.join(subset):36} {n:>4}/{len(searches):<4} {rate:>7.2f}   {hours}")
    print(
        "\nThe set is declared from what actually answers, not from this table alone: a smaller"
        "\nset is narrower coverage, and if its engine throttles mid-recording the recording stops"
        "\n(docs/evaluation-plan.md section 8). Early rates over a few searches are not rates."
    )


if __name__ == "__main__":
    main(Path(sys.argv[1]))
