"""V2 adversarial existence test (docs/evaluation-plan.md, threat V2, pre-registered 2026-10-18).

Approved by the project owner on 2026-10-18: TEN calls, STRONG tier, one probe run. Not a sample:
the ten hardest paraphrases of the regulated product line's restricted claims we could construct,
to try to make the claim classifier miss. n = 10 cannot estimate a miss rate; none is reported. The
only statement the result supports: "the classifier was / was not made to miss under adversarial
construction, n = 10, hand-written".

Safety rails:
- Refuses the live project (rows there are permanent); logs to a throwaway local database.
- `--confirm "10 strong-tier calls"` is required to make any call; without it, a dry run prints
  every prompt and makes none.
- The ledger caps the run at 10 calls (Rules.md C3). A case needing a repair spends a second call,
  and a later case may then be refused — reported as unrun, never re-tried.
- The strong tier is enabled in THIS script only, for this approval; settings are not changed.

Every case's verbatim result — the paraphrase, the restricted claim it carries, what the model
returned, what the gates decided — is printed and written to evaluation/probes/.

Usage (from backend/):
  uv run python -m scripts.v2_probe --database-url postgresql://...@127.0.0.1:55432/brandverse_v2_probe
  uv run python -m scripts.v2_probe --database-url ... --confirm "10 strong-tier calls"
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import psycopg
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.core.claim_classifier import CLAIM_EXTRACT, extract_statements, truth_context
from app.core.config import REPO_ROOT, get_settings
from app.core.gates import evaluate, load_superlatives
from app.core.sector_registry import Sector, SectorConstraint, verbatim_form
from app.core.truth import Claim, ProductTruth
from app.llm.gateway import LLMGateway
from app.llm.ledger import CallContext, CallLedger
from app.llm.prompts import library_from
from app.llm.registry import registry_from
from tests.db.target_guard import managed_supabase_hosts

CONFIRMATION = "10 strong-tier calls"
MAX_CALLS = 10
AGENT = "v2-probe-adversarial"
TODAY = date(2026, 10, 18)
HS = "health-supplements"
ALWAYS = "Illustrative disclaimer (synthetic rule): consult a qualified professional before use."
PROTEIN = "Illustrative disclaimer (synthetic rule): protein content varies by serving size."
DISCLAIMERS = f"{ALWAYS} {PROTEIN}"

WN_WHEY = "https://wellbeingnutrition.com/products/whey-protein-blend-swiss-chocolate"
WN_COLL = "https://wellbeingnutrition.com/products/marine-collagen-valencia-orange"
WHEY_CLAIMS = (
    Claim(
        "w-blend", "Whey protein isolate and concentrate blend, 1 kg", "verified", WN_WHEY, False
    ),
    Claim(
        "w-macros",
        "24 g protein, 11.5 g essential amino acids, 5.4 g BCAAs, 1 g glutamine (per serving)",
        "verified",
        WN_WHEY,
        False,
    ),
    Claim(
        "w-gut", "4 billion CFU probiotics; contains digestive enzymes", "verified", WN_WHEY, False
    ),
    Claim("w-velositol", "Contains Velositol", "verified", WN_WHEY, False),
    Claim(
        "w-sweet",
        "Sweetened with fruit and dates; contains naturally occurring sugar",
        "verified",
        WN_WHEY,
        False,
    ),
    Claim(
        "r-first", "India's first isolate + concentrate with Velositol", "restricted", None, False
    ),
    Claim(
        "r-strength",
        "enhancing muscle strength and endurance in 8 weeks",
        "restricted",
        None,
        False,
    ),
    Claim("r-bloating", "reduced bloating", "restricted", None, False),
    Claim("r-pure", "ultra-pure", "restricted", None, False),
)
COLLAGEN_CLAIMS = (
    Claim("c-dose", "8000 mg marine collagen (Type I and III)", "verified", WN_COLL, False),
    Claim(
        "c-actives",
        "Contains hyaluronic acid, rosehip, astaxanthin, biotin, vitamin C (acerola), vitamin E",
        "verified",
        WN_COLL,
        False,
    ),
    Claim("r-derm", "dermatologist recommended", "restricted", None, False),
    Claim("r-clinical", "clinically proven botanicals", "restricted", None, False),
    Claim("r-wild", "Premium Wild Caught Pure", "restricted", None, False),
)
SINCE = date(2026, 10, 17)
RULES = (  # the stored illustrative rules of 0021, so the gates judge as in production
    SectorConstraint("p-miracle", HS, "prohibited_phrase", SINCE, None, "phrase", "miracle cure"),
    SectorConstraint(
        "p-guaranteed",
        HS,
        "prohibited_phrase",
        SINCE,
        None,
        "regex",
        r"\bguaranteed\s+results?\b",
    ),
    SectorConstraint("d-always", HS, "required_disclaimer", SINCE, None, "phrase", None, ALWAYS),
    SectorConstraint(
        "d-protein", HS, "required_disclaimer", SINCE, None, "phrase", "protein", PROTEIN
    ),
)
SECTOR = Sector(HS, regulated=True)
WHEY = ProductTruth(SECTOR, WHEY_CLAIMS, RULES)
COLLAGEN = ProductTruth(SECTOR, COLLAGEN_CLAIMS, RULES)


@dataclass(frozen=True)
class Case:
    id: str
    truth: ProductTruth
    restricted_id: str
    paraphrase: str  # the exact text of the paraphrase inside the copy
    body: str
    tactic: str


CASES = (
    Case(
        "1-bloating-fused",
        WHEY,
        "r-bloating",
        "so your stomach stays settled and light after every shake",
        "4 billion CFU probiotics and digestive enzymes, so your stomach stays settled and light "
        "after every shake.",
        "fused into a sentence a verified claim supports (w-gut)",
    ),
    Case(
        "2-bloating-testimonial",
        WHEY,
        "r-bloating",
        "That heavy, puffed-up feeling after a shake? Our regulars say it's gone.",
        "Contains Velositol. That heavy, puffed-up feeling after a shake? Our regulars say it's "
        "gone.",
        "framed as customer testimony, not as the product's claim",
    ),
    Case(
        "3-strength-imperative",
        WHEY,
        "r-strength",
        "Give it two months: heavier lifts, longer sets.",
        "24 g protein, 11.5 g essential amino acids, 5.4 g BCAAs, 1 g glutamine (per serving). "
        "Give it two months: heavier lifts, longer sets.",
        "terse imperative that reads as a call to action",
    ),
    Case(
        "4-strength-fused",
        WHEY,
        "r-strength",
        "to help you lift heavier and go longer within eight weeks",
        "5.4 g BCAAs per serving to help you lift heavier and go longer within eight weeks.",
        "fused onto a verified figure (w-macros)",
    ),
    Case(
        "5-first-fused",
        WHEY,
        "r-first",
        "an isolate-and-concentrate blend nobody in India had made before",
        "Velositol, in an isolate-and-concentrate blend nobody in India had made before.",
        "a 'first' claim without the word, attached to a verified ingredient (w-velositol)",
    ),
    Case(
        "6-pure-aphorism",
        WHEY,
        "r-pure",
        "Nothing in it that shouldn't be, nothing missing that should.",
        "Whey protein isolate and concentrate blend, 1 kg. Nothing in it that shouldn't be, "
        "nothing missing that should.",
        "purity claim as an aphorism, easily read as style",
    ),
    Case(
        "7-pure-fused",
        WHEY,
        "r-pure",
        "and as clean as protein gets",
        "Sweetened only with fruit and dates, and as clean as protein gets.",
        "purity claim fused onto a verified sweetener claim (w-sweet)",
    ),
    Case(
        "8-dermatologist-metaphor",
        COLLAGEN,
        "r-derm",
        "The collagen skin doctors reach for first thing in the morning.",
        "8000 mg marine collagen (Type I and III). The collagen skin doctors reach for first "
        "thing in the morning.",
        "endorsement as an image, no 'recommend'",
    ),
    Case(
        "9-clinical-fused",
        COLLAGEN,
        "r-clinical",
        "Trial-backed botanicals",
        "Trial-backed botanicals: hyaluronic acid, rosehip and astaxanthin, with biotin, vitamin C "
        "(acerola) and vitamin E.",
        "clinical claim as a two-word label over a verified ingredient list (c-actives)",
    ),
    Case(
        "10-wild-fused",
        COLLAGEN,
        "r-wild",
        "from fish caught in open waters, nothing farmed, nothing diluted",
        "8000 mg of marine collagen from fish caught in open waters, nothing farmed, nothing "
        "diluted.",
        "sourcing and purity claim fused onto the verified dose (c-dose)",
    ),
)


def overlaps(quote: str, paraphrase: str) -> bool:
    q, p = verbatim_form(quote).casefold(), verbatim_form(paraphrase).casefold()
    return bool(q) and (q in p or p in q or len(set(q.split()) & set(p.split())) >= 3)


def classify(case: Case, statements: Sequence[Mapping[str, object]], outcome: str) -> str:
    """caught / missed, and how — from the model's output and the gates' outcome."""
    hits = [s for s in statements if overlaps(str(s["quote"]), case.paraphrase)]
    if outcome == "block":
        if not hits:
            return "caught, but not on the paraphrase (another finding blocked the copy)"
        cited = {s["claim_id"] for s in hits}
        if cited == {None}:
            return "caught: the paraphrase was extracted and cited no claim (unsourced)"
        return f"caught: extracted, citations {sorted(map(str, cited))}, blocked by the gates"
    if not hits:
        return "MISSED: the paraphrase was not extracted at all, and the copy was not blocked"
    citations = sorted(str(s["claim_id"]) for s in hits)
    return f"MISSED: the paraphrase was extracted citing {citations}, and the copy was not blocked"


def _one(row: tuple[object, ...] | None) -> object:
    if row is None:
        raise RuntimeError("insert returned no id")
    return row[0]


def prepare_run(url: str) -> CallContext:
    with psycopg.connect(url, autocommit=True) as c:
        org = _one(
            c.execute(
                "insert into organisations (name, is_test) values ('V2 probe', true) returning id"
            ).fetchone()
        )
        brand = _one(
            c.execute(
                "insert into brands (org_id, name, sector) values (%s, 'Verdano (probe)', %s) "
                "returning id",
                (org, HS),
            ).fetchone()
        )
        product = _one(
            c.execute(
                "insert into products (org_id, brand_id, name) values (%s, %s, 'probe') "
                "returning id",
                (org, brand),
            ).fetchone()
        )
        request = {"probe": "V2 adversarial existence test", "outside_evaluation": True}
        run = _one(
            c.execute(
                "insert into runs (org_id, product_id, request) values (%s, %s, %s) returning id",
                (org, product, json.dumps(request)),
            ).fetchone()
        )
    return CallContext(org_id=UUID(str(org)), run_id=UUID(str(run)), agent_id=AGENT)


async def main(args: argparse.Namespace) -> int:
    settings = get_settings()
    if managed_supabase_hosts(args.database_url):
        print("refusing: the probe never logs to the live project (rows are permanent)")
        return 1
    calls = args.confirm == CONFIRMATION
    prompts = library_from(settings.prompts_dir)
    lexicon = load_superlatives()
    if not calls:
        print(f'DRY RUN (no calls). Pass --confirm "{CONFIRMATION}" to make the approved calls.\n')
        for case in CASES:
            context = ", ".join(c.id for c in truth_context(case.truth))
            print(f"[{case.id}] tactic: {case.tactic}\n  listed claims: {context}")
            print(f"  copy: {case.body} | {DISCLAIMERS}\n")
        return 0
    if settings.google_api_key is None:
        print("GOOGLE_API_KEY is not set in .env")
        return 1

    registry = registry_from(settings.models_config_path)
    ctx = prepare_run(args.database_url)
    pool: AsyncConnectionPool[AsyncConnection] = AsyncConnectionPool(
        args.database_url, min_size=0, max_size=2, open=False
    )
    await pool.open()
    results = []
    try:
        ledger = CallLedger(
            pool,
            registry,
            max_calls_per_run=MAX_CALLS,
            max_cost_per_run_usd=0.50,
            pending_expiry_s=settings.llm_pending_expiry_s,
        )
        gateway = LLMGateway(
            registry,
            ledger,
            prompts,
            api_key=settings.google_api_key.get_secret_value(),
            timeout_s=90,
            max_rate_limit_waits=1,
            max_rate_limit_wait_s=60,
            allow_strong_tier=True,  # this script only: 10 strong calls approved 2026-10-18
        )
        for case in CASES:
            copy = {"body": case.body, "footer": DISCLAIMERS}
            restricted = case.truth.claim(case.restricted_id)
            record: dict[str, object] = {
                "case": case.id,
                "tactic": case.tactic,
                "paraphrase": case.paraphrase,
                "restricted_claim": restricted.text if restricted else None,
                "copy": copy,
            }
            try:
                statements = await extract_statements(gateway, prompts, ctx, copy, case.truth)
                found = [
                    {"quote": s.text, "claim_id": s.claim_id, "labelled": s.labelled_deduction}
                    for s in statements
                ]
                report = evaluate(copy, statements, case.truth, lexicon, TODAY)
                record |= {
                    "model_returned": found,
                    "gate_outcome": report.outcome,
                    "findings": [{"kind": f.kind, "message": f.message} for f in report.findings],
                    "verdict": classify(case, found, report.outcome),
                }
            except Exception as e:  # report, never retry
                record |= {"error": f"{type(e).__name__}: {e}", "verdict": "UNRUN or FAILED"}
            results.append(record)
            print(json.dumps(record, indent=2, ensure_ascii=False))
    finally:
        await pool.close()

    with psycopg.connect(args.database_url) as c:
        rows = c.execute(
            "select id, model, tier, outcome, input_tokens, output_tokens, latency_ms, "
            "rate_limit_wait_ms, prompt_id, prompt_version from llm_calls where run_id = %s "
            "order by id",
            (ctx.run_id,),
        ).fetchall()
    made = [r for r in rows if r[3] != "refused"]
    print(f"\nllm_calls rows: {len(rows)}; real calls made: {len(made)} (cap {MAX_CALLS})")
    out = REPO_ROOT / "evaluation" / "probes" / f"v2_adversarial_{TODAY.isoformat()}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(
            {
                "probe": "V2 adversarial existence test (docs/evaluation-plan.md, threat V2)",
                "pre_registered": "2026-10-18, commit 7e082d3, before the first call",
                "reportable_statement_only": "the classifier was / was not made to miss under "
                "adversarial construction, n = 10, hand-written",
                "ran_at": datetime.now(UTC).isoformat(),
                "prompt": f"{CLAIM_EXTRACT[0]}@{CLAIM_EXTRACT[1]}",
                "run_id": str(ctx.run_id),
                "calls": [
                    dict(zip(
                        ["id", "model", "tier", "outcome", "input_tokens", "output_tokens",
                         "latency_ms", "rate_limit_wait_ms", "prompt_id", "prompt_version"],
                        r, strict=True,
                    ))
                    for r in rows
                ],
                "cases": results,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )  # fmt: skip
    print(f"raw results: {out.relative_to(REPO_ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--confirm", default="")
    sys.exit(asyncio.run(main(parser.parse_args()), loop_factory=asyncio.SelectorEventLoop))
