# BrandverseAI — Engineering Rules

| Field | Value |
|---|---|
| Document | Rules.md |
| Version | 1.0 |
| Date | 14 October 2026 |
| Audience | Any AI coding agent or human contributor working in this repository |

---

## 0. Precedence

When documents conflict, follow this order:

```
Rules.md  >  PRD.md  >  Architecture.md  >  Memory.md  >  Design.md  >  Phases.md
```

If a rule here blocks a requirement elsewhere, **stop and ask**. Do not resolve the conflict silently.

---

## 1. Rules for the AI coding agent

These are binding. A violation is a defect regardless of whether tests pass.

### 1.1 Never invent

- **Never invent an API that you have not verified exists.** If unsure of a library's current signature, read its installed source or its docs. Do not write code from memory of an older version.
- **Never invent a model ID.** Model IDs come from `config/models.yaml` and nowhere else.
- **Never fabricate data, metrics or benchmark results.** If a number is not measured, it does not go in a document, a chart, or a slide. Write `not measured`.
- **Never stub a function and report the feature complete.** A `TODO` that returns a hardcoded value is an unfinished feature; say so.

### 1.2 Stop and ask when

- A requirement is ambiguous and the wrong guess is expensive to undo
- A schema change would drop a column holding data
- A change touches authentication, tenant isolation, or the claim-validation gate
- A dependency must be added that is not already in the lockfile
- The work would exceed the phase's declared scope

### 1.3 Scope discipline

Build exactly the phase in `Phases.md`. Do not build Phase 7 while implementing Phase 3 because it "will be needed anyway". Unused abstraction written early is the main source of waste in student projects.

### 1.4 Report honestly

When summarising work, state what was built, what was stubbed, and what was not attempted. Never describe intended behaviour as achieved behaviour.

---

## 2. Hard prohibitions

| # | Never | Why |
|---|---|---|
| P1 | Commit a secret, key or token | Irreversible once pushed; rotate immediately if it happens |
| P2 | Use the Supabase **service key** in client-side code | Bypasses RLS entirely — total tenant compromise |
| P3 | Query a tenant table without an `org_id` filter | Cross-tenant leak |
| P4 | Create a table with `org_id` and no RLS policy in the same migration | The policy will be forgotten |
| P5 | Hardcode a model ID outside `config/models.yaml` | Provider IDs, prices and dimensionality drift without notice; scattered IDs make a migration impossible |
| P6 | Call an LLM without passing through the LiteLLM gateway | Bypasses budget tracking; makes the evaluation invalid |
| P7 | Call an LLM without a pre-flight budget check | Breaks the PRD's hard cost cap |
| P8 | Write to `memory_items` without human approval | Memory poisoning |
| P9 | Emit a factual product claim with no source and no `deduced` label | The product's core safety property |
| P10 | Store raw consumer PII anywhere | Out of scope by design; also a legal exposure |
| P11 | Persist raw fetched page bodies to Postgres | Zero-retention policy |
| P12 | `console.log` / `print` as production logging | Use the structured logger with `run_id` |
| P13 | Swallow an exception without logging it | Silent failure in a 15-step pipeline is undebuggable |
| P14 | Block the event loop with a synchronous HTTP or DB call in async code | Kills throughput under concurrency |
| P15 | Add a dependency to work around a 20-line function | Dependency weight is a real cost |

---

## 3. Code conventions

### 3.1 Python (backend)

| Rule | Detail |
|---|---|
| Version | 3.12 |
| Formatting | `ruff format`; line length 100 |
| Linting | `ruff check` must be clean before commit |
| Typing | Full annotations on every public function; `mypy --strict` on `app/core` and `app/agents` |
| Validation | Pydantic v2 models at every boundary — API in, API out, agent out, LLM structured output |
| Async | `async def` for anything touching I/O. Use `httpx.AsyncClient`, never `requests`. |
| Errors | Domain exceptions in `app/errors.py`. Never raise bare `Exception`. |
| Config | `pydantic-settings`, loaded once. No `os.environ` reads scattered through modules. |
| Layout | `app/{api,core,agents,memory,research,llm,models,workers}` |

**Every agent returns a validated Pydantic model. No agent returns a raw string.** An LLM that returns prose where a schema was expected must be retried with a repair prompt, then fail loudly.

### 3.2 TypeScript (frontend)

| Rule | Detail |
|---|---|
| Version | TS 5.x strict, `noUncheckedIndexedAccess: true` |
| Framework | Next.js 15 App Router, React 19 |
| `any` | Banned. Use `unknown` and narrow. |
| Components | Server Components by default; `'use client'` only when state, effects or WebGL require it |
| Styling | Tailwind utilities; design tokens from `Design.md` as CSS custom properties |
| State | Server state via TanStack Query; local UI state via `useState`. No global store until genuinely needed. |
| Forms | react-hook-form + zod, schema shared with the backend contract |
| 3D | All Three.js inside `'use client'` components, dynamically imported with `ssr: false` |

### 3.3 SQL

- Migrations only — never edit a table through the dashboard
- One logical change per migration, named `NNNN_description.sql`
- Every migration is reversible or documents why it is not
- Indexes created `concurrently` on anything that will hold production data
- **Editing an existing migration in place is permitted only until the Supabase project is created
  and the migrations are first applied to it.** That event is the trigger, not a date. From then
  on, every schema change is a new migration — no exceptions, including fixes to a migration that
  has not yet reached production data. Before the trigger, in-place edits are recorded in the
  commit message (which migrations, and why)

---

## 4. Cost governance rules

**Zero-rupee constraint (2026-10-14).** The project spends nothing: every service runs on a free
tier (`Architecture.md` §11). **Tokens are the primary cost unit.** USD figures are computed from
providers' list prices; they are a safety limit and a comparison aid, not money paid.

| # | Rule |
|---|---|
| C0 | No paid service, paid tier, or pay-as-you-go overage is used. A design that needs one is changed, not paid for. |
| C1 | Estimate cost **before** a call; refuse if it breaches the cap |
| C2 | Record every call in `llm_calls` with model, tier, route reason, tokens, cost, latency. Tokens are the primary measure; `cost_usd` is list-price-derived. |
| C3 | Hard caps: 20 LLM calls, 20 searches, 2 revision rounds, 4 media generations, $2.50 list-price-equivalent. The USD cap stays as a safety limit even though actual spend is zero. |
| C4 | On breach, return a partial result flagged `budget_exhausted` — never truncate silently |
| C5 | Default to the cheapest tier that can do the job; escalate on failure, do not start high |
| C6 | Cache embeddings by content hash. Re-embedding identical text is pure waste. |
| C7 | Cache search results by normalised query for the TTL window |
| C8 | Never retry a failed LLM call more than twice, and never with the same prompt. A rate-limit wait (`429`) is **not** a failed call: it does not count toward this cap (C10). |
| C9 | **Per-day budget.** Track daily usage per model against its free-tier RPD (`config/models.yaml`; resets midnight Pacific; per Google Cloud project, so every call with the key counts). Before a call, refuse or downgrade it if it would breach its model's RPD, and log the reason in `llm_calls.route_reason`. A run degrades to a lower tier with a recorded note rather than failing. Exception: a call T4 or the high-risk routing rule requires on the strong tier is deferred until the quota resets, never downgraded. Applies alongside the per-run caps (C3), not instead of them. |
| C10 | **Rate-limit-aware scheduling.** Every model call goes through a per-model scheduler sized to its RPM/TPM. A `429` is waited out (Google's `retryDelay`, else exponential backoff with jitter) and the same request re-sent. Waits are logged separately from latency and never count toward C8. |

---

## 5. Truth and safety rules

| # | Rule |
|---|---|
| T1 | Every factual claim in output carries `claim_type` and a source reference |
| T2 | A `restricted` claim never reaches generated copy under any instruction |
| T3 | Superlatives ("best", "number one", "fastest") require a cited source or are removed |
| T4 | In regulated sectors (health, finance, children's products), claim verification and risk assessment performed on regulated-sector output run on the strong tier, whichever agent performs them; all other calls in a regulated-sector run keep their default tier; the sector constraint registry applies to everything regardless of tier. Where A4 is built, it is additionally required. On the free tier the strong tier is a Flash model (`Architecture.md` §4.1); if its daily quota is exhausted, a T4 call is deferred, never downgraded (C9). |
| T5 | The 8 predatory anti-patterns (`Architecture.md` §8) are blocked, not warned |
| T6 | A campaign cannot reach `approved` without a recorded human action |
| T7 | Generated creative is watermarked in metadata as AI-assisted |
| T8 | The system never generates content impersonating a real person or a real brand it does not own |

---

## 6. Testing rules

| Layer | Requirement |
|---|---|
| Unit | Every scoring function (`R`, `M`, freshness, routing) has tests at boundaries and just inside/outside each band |
| Integration | Full pipeline against a mocked LLM and a mocked SearXNG — must run offline in CI |
| Security | Cross-tenant access test per tenant table. **Must stay green; a failure blocks merge.** The table-posture test (`backend/tests/db/test_table_posture.py`) is merge-blocking on the same terms: every table's delete and truncate posture for the API roles is stated there by hand, and a new table fails the suite until its posture is stated. |
| Memory | Retrieval precision test against the 50-query labelled set |
| Cost | Assert a run that would exceed the cap aborts cleanly with partial output |
| Frontend | Vitest for logic; Playwright for the run-creation and approval flows |
| 3D | Smoke test that the scene mounts and the no-WebGL fallback renders |

Coverage target 80% on `app/core`, `app/memory`, `app/llm`. **Do not quote a coverage number you have not run.**

Mock every external call in CI. A test suite that needs a live Gemini key is not a test suite.

---

## 7. Git and review

- Branch per phase: `phase-04-marketing-memory`
- Conventional commits: `feat(memory): add RRF hybrid retrieval`
- No direct commits to `main`
- CI must pass: lint, type check, tests, build
- A PR description states what changed, what was tested, and what is still stubbed

---

## 8. Documentation rules

- Changing behaviour described in a doc means updating that doc in the same PR
- `Phases.md` exit criteria are ticked only when demonstrably true
- Measured numbers carry the date and the conditions they were measured under
- Any departure from `PRD.md` §1.2 is added to that table with its reason

---

## 9. Academic integrity

| # | Rule |
|---|---|
| A1 | Every reported metric traces to a raw log in the repository |
| A2 | Failed and capped runs are reported, not dropped |
| A3 | Seed memory is excluded from evaluation runs, enforced in code |
| A4 | AI-assisted code is disclosed; every team member can explain any file they submit |
| A5 | Tuned parameters are reported alongside their defaults |
| A6 | Claims in the dissertation match claims in this repository. If the code does not do it, the thesis does not say it. |

> **A5 and A6 are where student projects usually fail the viva.** A number in the slide deck that no log supports is the fastest way to lose a panel's trust.
