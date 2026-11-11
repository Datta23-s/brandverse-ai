"""Print the evaluation's figures: ARR, SRR, CR, LR, the depth pair with its attribution, the
scheduling defects and the sample declaration. ZERO model calls — it only reads.

    PYTHONPATH=. uv run python -m scripts.evaluation_metrics --database-url <url> \
        [--seconds-per-search 3.41] [--snapshot C:/BrandverseData/evaluation-snapshot] [--json out]

The organisations are the evaluation's own, found by name (`ApiRunner.org_name`), so practice,
tuning and test organisations cannot reach a figure. Everything printed comes from stored rows and
the frozen configuration; nothing is recomputed from a model.

The unit costs for LR's derived parts are read from the RECORDING (`--snapshot`), never from the
evaluation's own replayed runs, because replay removed the web by construction. Without `--snapshot`
the LR lines say so and the other three figures are printed as usual.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

import psycopg
from psycopg import AsyncConnection

from app.evaluation.metrics import (
    UnitCosts,
    build_report,
    evaluation_invalidations,
    run_records,
    scheduling_defects,
    schema_failures,
)
from app.evaluation.necessity_decomposition import Bands
from app.memory.scoring import load_scoring

# The recording's measured per-search duration (session 2, n = 63: median 3.41 s, mean 3.54 s;
# decision-log and evaluation-plan). Passed in, and printed with the figure it produces.
DEFAULT_SECONDS_PER_SEARCH = 3.41


def evaluation_org_ids(url: str) -> list[UUID]:
    """The evaluation's organisations: named `EVAL <brand> <arm>` by the harness, never practice
    or test. An empty list is an error the caller sees, not an empty report."""
    with psycopg.connect(url, autocommit=True) as c:
        rows = c.execute(
            "select id from organisations "
            "where name like 'EVAL %' and not is_practice and not is_test order by name"
        ).fetchall()
    return [UUID(str(r[0])) for r in rows]


async def main_async(args: argparse.Namespace) -> int:
    org_ids = evaluation_org_ids(args.database_url)
    if not org_ids:
        print("no evaluation organisations in this database: nothing to report")
        return 1
    print(f"{len(org_ids)} evaluation organisation(s)")
    cfg = load_scoring().necessity
    units = None
    if args.snapshot is not None:
        units = UnitCosts.from_snapshot(
            Path(args.snapshot) / "committed.jsonl", args.seconds_per_search
        )
    async with await AsyncConnection.connect(args.database_url) as conn:
        records = await run_records(conn, org_ids)
        defects = await scheduling_defects(conn, org_ids)
        invalid = await evaluation_invalidations(conn, org_ids)
        schema = await schema_failures(conn, org_ids)
    report = build_report(
        records,
        bands=Bands(cfg.d0_below, cfg.d2_from),
        weights=cfg.weights,
        unit_costs=units,
        defects=defects,
        invalidations=invalid,
        schema=schema,
    )
    print(f"\nbands D0 below {cfg.d0_below}, D2 from {cfg.d2_from}; weights {cfg.weights}")
    print("(the frozen configuration: if it changes, every figure below is recomputed)\n")
    for line in report.lines():
        print(line)
    if args.json is not None:
        Path(args.json).write_text(  # noqa: ASYNC240 - one local file, after every read
            json.dumps(
                {
                    "paired_requests": [p.request_id for p in report.paired],
                    "incomplete": list(report.incomplete),
                    "excluded": [asdict(e) for e in report.excluded],
                    "linkage_disagreements": list(report.linkage_disagreements),
                    "lines": report.lines(),
                },
                indent=1,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        print("\nwritten:", args.json)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--database-url", required=True)
    p.add_argument("--snapshot", default=None, help="the recording, for LR's unit costs")
    p.add_argument("--seconds-per-search", type=float, default=DEFAULT_SECONDS_PER_SEARCH)
    p.add_argument("--json", default=None, help="also write the report as JSON")
    args = p.parse_args()
    return asyncio.run(main_async(args), loop_factory=asyncio.SelectorEventLoop)


if __name__ == "__main__":
    sys.exit(main())
