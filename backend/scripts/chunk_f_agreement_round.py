"""Generate the ten campaigns for the 8 November RATER AGREEMENT round (owner, 1 November 2026).

The round measures whether two humans apply the quality rubric alike
(`docs/rating/rubric-amendment-1.md`). It uses the CURRENT, pre-Phase-7 system: valid for rater
agreement, INVALID for band calibration, which stays in chunk G on the post-Phase-7 system. No
blinding is needed: there is one system and one arm, and both raters score identical items.

Ten fictional products (`evaluation/rater_agreement/products.yaml`), each run through the
product's own path (`POST /api/v1/runs` -> the real worker -> parked), at depth D0: the practice
products have no recorded snapshot, and live search is held for the recording, so there is no
research and A2 is skipped. STANDARD tier only. Each campaign is left PARKED, not approved, so no
memory is written. Writes:

- `evaluation/rater_agreement/campaigns.html`: what the raters read per campaign (the objective,
  the audience, the product brief, the copy) and nothing else: no gate findings, no advisory;
- `evaluation/rater_agreement/campaigns.json`: the same plus run ids and the gates' outcome.

Logs to a THROWAWAY database (`brandverse_agreement`, recreated). The approver's decisions on these
ten are made by the human approver on paper (the outcome column of the scoring sheet) and their
revision rate is read from those marks (`docs/decision-log.md` 2.19); this script decides nothing.

Usage (from backend/):  PYTHONPATH=. uv run python -m scripts.chunk_f_agreement_round
"""

import argparse
import asyncio
import html
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
import psycopg
import yaml
from pydantic import SecretStr

from app.api.main import create_app
from app.core.config import get_settings
from app.evaluation.llm_attempts import llm_calls_by_attempt, totals
from scripts.chunk_f_first_campaign import wait_for_campaign
from scripts.e2e_prepare_db import main as prepare_database
from tests.auth_keys import TestIssuer
from tests.db.target_guard import managed_supabase_hosts

ROOT = Path(__file__).resolve().parents[2]
PRODUCTS = ROOT / "evaluation" / "rater_agreement" / "products.yaml"
OUT = ROOT / "evaluation" / "rater_agreement"
ADMIN_URL = "postgresql://postgres:postgres@127.0.0.1:55432/postgres"
DB_NAME = "brandverse_agreement"
REDIS_URL = "redis://127.0.0.1:6379/12"
MAX_CALLS = 12


def seed(url: str, items: list[dict[str, Any]]) -> tuple[UUID, UUID, dict[str, UUID]]:
    """One practice organisation, one user (owner, editor, approver), a brand and product each."""
    from uuid import uuid4

    user = uuid4()
    products: dict[str, UUID] = {}
    with psycopg.connect(url, autocommit=True) as c:

        def one(query: str, params: tuple[object, ...]) -> UUID:
            row = c.execute(query, params).fetchone()
            if row is None:
                raise RuntimeError("insert returned no row")
            value: UUID = row[0]
            return value

        c.execute("insert into auth.users (id) values (%s)", (user,))
        org = one(
            "insert into organisations (name, is_practice) values ('Agreement round', true) "
            "returning id",
            (),
        )
        for role in ("owner", "editor", "approver"):
            c.execute(
                "insert into org_members (org_id, user_id, role) values (%s, %s, %s)",
                (org, user, role),
            )
        for item in items:
            brand = one(
                "insert into brands (org_id, name, sector) values (%s, %s, %s) returning id",
                (org, item["brand"], item["sector"]),
            )
            product = one(
                "insert into products (org_id, brand_id, name, category) "
                "values (%s, %s, %s, %s) returning id",
                (org, brand, item["product"], item["category"]),
            )
            products[item["id"]] = product
            with c.transaction():
                c.execute(
                    "select set_config('role', 'authenticated', true), "
                    "set_config('request.jwt.claims', %s, true)",
                    (json.dumps({"sub": str(user), "role": "authenticated"}),),
                )
                for claim in item["claims"]:
                    c.execute(
                        "insert into product_claims (org_id, product_id, claim, claim_type, "
                        "evidence_url, created_by) values (%s, %s, %s, 'verified', "
                        "'https://example.invalid/spec', 'human')",
                        (org, product, claim),
                    )
    return org, user, products


def render(items: list[dict[str, Any]], views: dict[str, dict[str, Any]]) -> str:
    esc = html.escape
    cards = []
    for item in items:
        view = views.get(item["id"])
        copy = (view or {}).get("copy_parts", {})
        parts = (
            "".join(
                f'<p><span class="lab">{esc(label)}</span> {esc(copy[key])}</p>'
                for key, label in (
                    ("headline", "Headline"),
                    ("body", "Body"),
                    ("call_to_action", "Call to action"),
                    ("footer", "Footer"),
                )
                if key in copy
            )
            or "<p><em>(no copy was produced)</em></p>"
        )
        facts = "".join(f"<li>{esc(c)}</li>" for c in item["claims"])
        cards.append(
            f'<section class="card"><h2>{esc(item["id"])}</h2>'
            f'<p class="meta"><b>Product:</b> {esc(item["product"])} ({esc(item["brand"])})<br>'
            f"<b>Objective:</b> {esc(item['objective'])}<br>"
            f"<b>Audience:</b> {esc(item['audience'])}</p>"
            f'<div class="facts"><b>Product brief (verified facts)</b><ul>{facts}</ul></div>'
            f'<div class="copy">{parts}</div></section>'
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        "<title>Agreement round: campaigns</title><style>"
        "body{font:11pt/1.45 system-ui,Segoe UI,Arial,sans-serif;max-width:190mm;margin:0 auto;"
        "padding:8mm;color:#111;background:#fff}"
        ".card{border:1pt solid #111;padding:4mm;margin:5mm 0;break-inside:avoid}"
        ".facts{background:#f3f3f3;padding:2mm 3mm;font-size:10pt}"
        ".copy{border-left:3pt solid #111;padding:1mm 4mm;margin-top:3mm}"
        ".lab{font-size:8.5pt;text-transform:uppercase;color:#555;display:block}"
        ".meta{font-size:10pt}h2{margin:0 0 2mm}"
        "@page{size:A4;margin:14mm}</style></head><body>"
        "<h1>Agreement round: ten campaigns</h1>"
        "<p><b>Current system, pre-Phase-7 output. Valid for rater agreement; invalid for band "
        "calibration.</b> One system, one arm: nothing to conceal. Score each campaign on the "
        "scoring sheet. Do not rewrite the copy.</p>" + "".join(cards) + "</body></html>"
    )


async def main(args: argparse.Namespace) -> int:
    base = get_settings()
    if base.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1
    items: list[dict[str, Any]] = yaml.safe_load(PRODUCTS.read_text(encoding="utf-8"))["campaigns"]
    url = prepare_database(ADMIN_URL, DB_NAME)
    if managed_supabase_hosts(url):
        print("refusing to log to the live project")
        return 1
    org, user, products = seed(url, items)
    settings = base.model_copy(
        update={
            "database_url": SecretStr(url),
            "redis_url": REDIS_URL,
            "environment": "development",
            "pipeline_agents": "real",
            "research_snapshot_dir": None,
            "max_llm_calls_per_run": args.max_calls,
            "llm_allow_strong_tier": False,
            "llm_strong_tier_org_id": None,
            "run_events_block_ms": 200,
        }
    )
    issuer = TestIssuer()
    headers = {"Authorization": f"Bearer {issuer.token(user)}"}
    app = create_app(settings, verifier=issuer.verifier())
    log = Path(tempfile.gettempdir()) / "agreement_worker.log"
    worker = subprocess.Popen(  # noqa: S603, ASYNC220 - a fixed command, started once
        [sys.executable, "-m", "app.workers"],
        env={
            **os.environ,
            "DATABASE_URL": url,
            "REDIS_URL": REDIS_URL,
            "ENVIRONMENT": "development",
            "PIPELINE_AGENTS": "real",
            "MAX_LLM_CALLS_PER_RUN": str(args.max_calls),
            "LLM_ALLOW_STRONG_TIER": "false",
            "LLM_STRONG_TIER_ORG_ID": "",
        },
        stdout=log.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    views: dict[str, dict[str, Any]] = {}
    runs: dict[str, UUID] = {}
    try:
        for _ in range(90):
            await asyncio.sleep(2)
            if "pipeline ready" in log.read_text(  # noqa: ASYNC240 - a small log, polled
                encoding="utf-8", errors="replace"
            ):
                break
            if worker.poll() is not None:
                print("the worker exited at start-up:", log.read_text(errors="replace")[-800:])
                return 1
        async with app.router.lifespan_context(app):
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://test", timeout=60
            ) as client:
                for item in items:
                    r = await client.post(
                        "/api/v1/runs",
                        json={
                            "org_id": str(org),
                            "product_id": str(products[item["id"]]),
                            "brief": item["objective"],
                            "audience": item["audience"],
                            "depth": "D0",
                        },
                        headers=headers,
                    )
                    if r.status_code != 202:
                        print(item["id"], "POST ->", r.status_code, r.text[:200])
                        continue
                    run_id = UUID(r.json()["run_id"])
                    runs[item["id"]] = run_id
                    view = await wait_for_campaign(client, run_id, headers)
                    if view is None:
                        print(item["id"], "never parked")
                        continue
                    views[item["id"]] = view
                    print(
                        f"{item['id']} parked: gates={view['gate_outcome']} "
                        f"system revision rounds={view['revision_round']}"
                    )
    finally:
        worker.terminate()
        worker.wait(timeout=30)

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "campaigns.html").write_text(render(items, views), encoding="utf-8", newline="\n")
    record = {
        "round": "2026-11-08 rater agreement (pre-Phase-7 system, depth D0, A2 skipped)",
        "campaigns": [
            {
                "id": i["id"],
                "run_id": str(runs.get(i["id"])),
                "gate_outcome": (views.get(i["id"]) or {}).get("gate_outcome"),
                "system_revision_rounds": (views.get(i["id"]) or {}).get("revision_round"),
                "copy": (views.get(i["id"]) or {}).get("copy_parts"),
            }
            for i in items
        ],
    }
    (OUT / "campaigns.json").write_text(
        json.dumps(record, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    async with await psycopg.AsyncConnection.connect(url) as conn:
        rows = await llm_calls_by_attempt(conn, [org])
    t = totals([r for r in rows if r.run_id in set(runs.values())])
    standard = sum(1 for r in rows if r.outcome != "refused")
    print(f"\nparked {len(views)} of {len(items)}; model calls made (spend): {t.spend_calls}")
    print(
        f"work {t.work_calls}, re-executions {t.resume_duplicate_calls}, "
        f"orphaned {t.orphaned_calls}"
    )
    print(f"TALLY for Architecture.md section 4: cheap 0, standard {standard}, strong 0")
    return 0 if len(views) == len(items) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    # psycopg's async mode refuses Windows' default ProactorEventLoop (Architecture.md section 11).
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
