"""Build the blind presentation for a sitting: the packets, the order, the seed and the key.

    PYTHONPATH=. uv run python -m scripts.blind_presentation --seed 20261116 --rating
    PYTHONPATH=. uv run python -m scripts.blind_presentation --seed 20261112 --approval

Reads the campaigns that are ready to be seen from the evaluation database and writes, to
`evaluation/rating/`:

* `<name>.sheet.txt`   what the reader sees: a label, the product, the audience, the objective and
                       the copy. No arm, no depth, no strategy, no evidence, no gaps — `Presented`
                       has no such field (`app/evaluation/blind.py`).
* `<name>.packets.json` the same packets as data, for a renderer. No unblinding key in it.
* `<name>.order.json`  the SEED, the redraw attempt, the order per batch and the UNBLINDING KEY, so
                       the scores can be joined back to the arms afterwards and the order can be
                       reproduced. This file is the one the reader must not see.

`--rating` is the 16 November round: the registered 15-request subset, and the three arms of one
request are additionally kept non-adjacent (decision-log 2.18's "interleaved"). `--approval` is the
approval queue exactly as the rubric registers it.
"""

import argparse
import json
import sys
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "evaluation" / "rating"
URL = "postgresql://postgres:postgres@127.0.0.1:55432/brandverse_evaluation"

# A campaign is ready to be SEEN when its run reached an outcome a human may look at. A run an
# infrastructure failure invalidated is excluded here, which is the point of invalidation: nobody
# reads the copy of a run that is going to be re-run.
READY_SQL = """
select c.id::text, r.arm, r.request->>'brief' as brief, c.creative, r.status,
       r.failure ->> 'invalidated' as invalidated
from public.campaigns c
join public.runs r on r.id = c.run_id and r.org_id = c.org_id
join public.organisations o on o.id = c.org_id
where not o.is_practice and not o.is_test
  and c.creative is not null
  and r.status in ('awaiting_approval', 'completed', 'partial')
order by r.created_at
"""


def load_campaigns(url: str, only_requests: set[str] | None):  # type: ignore[no-untyped-def]
    from app.evaluation.blind import Campaign
    from app.evaluation.test_set import request_index, request_inputs

    inputs, index = request_inputs(), request_index()
    by_brief = {i.brief: rid for rid, i in inputs.items()}
    with psycopg.connect(url, autocommit=True) as c:
        rows = c.execute(READY_SQL).fetchall()
    out: list[Campaign] = []
    skipped: list[str] = []
    for campaign_id, arm, brief, creative, _status, invalidated in rows:
        request_id = by_brief.get(brief)
        if request_id is None:
            raise ValueError(f"campaign {campaign_id}: its brief is in no registered request")
        if invalidated:
            skipped.append(f"{request_id}:{arm} (invalidated, awaiting a re-run)")
            continue
        if only_requests is not None and request_id not in only_requests:
            skipped.append(f"{request_id}:{arm} (not in the rating subset)")
            continue
        meta, inp = index[request_id], inputs[request_id]
        out.append(
            Campaign(
                campaign_id=campaign_id,
                request_id=request_id,
                brand_id=meta.brand_id,
                position=meta.position,
                arm=arm,
                product=inp.product,
                audience=inp.audience,
                objective=inp.brief.split(". Audience:")[0],
                copy=dict(creative["copy"]),
            )
        )
    return out, skipped


def main() -> int:
    from app.evaluation.blind import present, presented_json, render_sheet
    from app.evaluation.rating_subset import rating_subset

    p = argparse.ArgumentParser()
    p.add_argument("--seed", type=int, required=True, help="logged with the order; pick the date")
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--rating", action="store_true", help="the 16 November round (the subset)")
    mode.add_argument("--approval", action="store_true", help="the approval queue (the rubric's)")
    p.add_argument("--database-url", default=URL)
    p.add_argument("--name", default=None)
    args = p.parse_args()

    only = set(rating_subset()) if args.rating else None
    campaigns, skipped = load_campaigns(args.database_url, only)
    print(f"campaigns ready to be seen: {len(campaigns)}")
    for line in skipped:
        print(f"  skipped {line}")
    if not campaigns:
        print("nothing to present")
        return 1
    presentation = present(
        campaigns, seed=args.seed, forbid_same_request_adjacent=bool(args.rating)
    )
    print(
        f"seed {presentation.seed}, redraw attempt {presentation.attempt}, "
        f"{len(presentation.batches)} sitting(s) of at most {presentation.session_cap}"
    )
    name = args.name or ("rating" if args.rating else "approval") + f"_{args.seed}"
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / f"{name}.sheet.txt").write_text(render_sheet(presentation), encoding="utf-8")
    (OUT_DIR / f"{name}.packets.json").write_text(presented_json(presentation), encoding="utf-8")
    (OUT_DIR / f"{name}.order.json").write_text(
        json.dumps(presentation.record(), indent=1) + "\n", encoding="utf-8"
    )
    print(f"written: {OUT_DIR / name}.sheet.txt / .packets.json / .order.json")
    print(
        "the .order.json holds the seed, the order and the UNBLINDING KEY: it is not for a reader"
    )
    for batch in presentation.batches:
        print(
            f"  sitting {batch.number}: {len(batch.campaigns)} campaigns, labels "
            f"{batch.labels[0]}..{batch.labels[-1]}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
