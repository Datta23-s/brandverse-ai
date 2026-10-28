"""How many DISTINCT research inputs the 30 requests give the query planner (no network).

    PYTHONPATH=. uv run python scripts/planner_variety.py

Two requests that share a category produce the same queries for every facet that does not read the
audience, and so identical recorded evidence. This counts what is distinct, so the recording budget
and what the evaluation can claim about evidence variety are numbers, not impressions.
"""

from collections import defaultdict

from app.evaluation.test_set import plan_requests
from app.memory.scoring import load_scoring
from app.research.planner import plan_queries
from app.research.search import normalise_query


def main() -> None:
    cfg = load_scoring().research
    reqs = plan_requests()
    plans = {
        rid: plan_queries(
            req,
            queries_per_facet=cfg.planner_queries_per_facet,
            locale=cfg.planner_locale,
            max_pages_per_query=cfg.max_pages_per_query,
        )
        for rid, req in reqs.items()
    }
    lists = {rid: tuple(normalise_query(q.text) for q in p.queries) for rid, p in plans.items()}
    slots = [q for lst in lists.values() for q in lst]
    d1 = {
        rid: tuple(normalise_query(q.text) for q in p.for_depth("D1")) for rid, p in plans.items()
    }

    # The "core" is every query that does not read the audience: it depends on category and sector.
    def is_core(q: object) -> bool:
        return q.facet != "audience_needs" or q.angle == 2  # type: ignore[attr-defined]

    core = {
        rid: tuple(normalise_query(q.text) for q in p.queries if is_core(q))
        for rid, p in plans.items()
    }
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for rid, req in reqs.items():
        groups[(req.sector, req.category)].append(rid)
    d1_texts = {q for v in d1.values() for q in v}
    categories = {r.category for r in reqs.values()}
    shared = {k: v for k, v in groups.items() if len(v) > 1}
    duplicated = sum(len(v) - 1 for v in shared.values())
    print(f"requests: {len(reqs)}")
    print(f"distinct ordered query lists: {len(set(lists.values()))}")
    print(f"query slots: {len(slots)}; distinct query texts: {len(set(slots))}")
    print(
        f"distinct D1 (first-pass) lists: {len(set(d1.values()))}; "
        f"D1 slots {sum(map(len, d1.values()))}, distinct D1 texts {len(d1_texts)}"
    )
    print(f"distinct categories: {len(categories)}; distinct (sector, category): {len(groups)}")
    print(f"distinct audience-free cores (7 of 8 queries): {len(set(core.values()))}")
    print(f"requests sharing a core with an earlier request: {duplicated}")
    for (sector, category), rids in sorted(shared.items()):
        print(f"  {sector} / {category}: {', '.join(rids)}")


if __name__ == "__main__":
    main()
