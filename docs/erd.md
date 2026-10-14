# BrandverseAI — Data Model (ERD draft)

| Field | Value |
|---|---|
| Phase | 0 — accepted 2026-10-14 |
| Status | **Canonical.** `Architecture.md` §9 is reconciled to this document and must change in the same commit as it. |
| Date | 14 October 2026 |

This is the canonical data model. The full DDL is in `Architecture.md` §9; the Phase 1
migrations are cut from it.
Composite `on delete set null (col)` (§3) requires PostgreSQL 15+.

## 1. Diagram

```mermaid
erDiagram
  sectors ||--o{ sector_constraints : "has rules"
  sectors ||--o{ brands : classifies
  sectors ||--o{ memory_items : classifies
  organisations ||--o{ org_members : "has members"
  org_members ||--o{ campaigns : "approves (approver role)"
  org_members ||--o{ approvals : "decides (approver role)"
  organisations ||--o{ brands : owns
  organisations ||--o{ runs : owns
  organisations ||--o{ memory_items : owns
  organisations ||--o{ products : "owns (denormalised)"
  organisations ||--o{ product_claims : "owns (denormalised)"
  organisations ||--o{ llm_calls : "owns (denormalised)"
  organisations ||--o{ campaigns : "owns (denormalised)"
  organisations ||--o{ approvals : "owns (denormalised)"
  brands ||--o{ products : has
  brands ||--o{ memory_items : scopes
  products ||--o{ product_claims : has
  products ||--o{ runs : "subject of"
  products ||--o{ memory_items : "optionally scopes"
  product_claims ||--o{ memory_items : "optionally backs"
  runs ||--o{ llm_calls : logs
  runs ||--o{ campaigns : produces
  campaigns ||--o{ approvals : "decided by"

  sectors {
    text id PK
    text label
    boolean regulated
    timestamptz created_at
  }
  sector_constraints {
    uuid id PK
    text sector_id FK
    text jurisdiction
    text kind "required_disclaimer|prohibited_phrase"
    text match_type "phrase|regex"
    text pattern
    text disclaimer_text
    text source_url
    text source_note
    date effective_from
    date retired_at
    uuid created_by FK "auth.users - human only"
    timestamptz created_at
  }
  organisations {
    uuid id PK
    text name
    numeric monthly_budget_cap_usd
    int rate_limit_runs_per_hour
    int rate_limit_requests_per_minute
    timestamptz created_at
  }
  org_members {
    uuid org_id PK
    uuid user_id PK "auth.users"
    text role PK "owner|editor|approver|viewer"
    uuid granted_by
    timestamptz granted_at
    uuid revoked_by
    timestamptz revoked_at
  }
  audit_log {
    bigint id PK
    timestamptz occurred_at
    uuid org_id "no FK - outlives org"
    text actor_type "user|service|agent"
    text actor_id
    text action
    text entity_type
    text entity_id
    jsonb details
  }
  brands {
    uuid id PK
    uuid org_id FK
    text name
    text voice_guidelines
    vector_768 voice_embedding
    text voice_embedding_model
    text voice_embedding_version
    text sector FK
    timestamptz created_at
  }
  products {
    uuid id PK
    uuid brand_id FK
    uuid org_id FK
    text name
    text category
    jsonb attributes
    timestamptz created_at
  }
  product_claims {
    uuid id PK
    uuid product_id FK
    uuid org_id FK
    text claim
    text claim_type "verified|deduced|restricted"
    text evidence_url
    text evidence_note
    numeric confidence
    text created_by
    timestamptz created_at
  }
  runs {
    uuid id PK
    uuid org_id FK
    uuid product_id FK
    text arm "baseline_a|baseline_b|proposed"
    text status
    jsonb request
    text depth
    numeric research_score
    text_array selected_agents
    int memory_hit_count
    int search_count
    int llm_call_count
    numeric total_cost_usd
    int latency_ms
    boolean budget_exhausted
    timestamptz created_at
    timestamptz completed_at
  }
  llm_calls {
    bigint id PK
    uuid run_id FK
    uuid org_id FK
    text agent_id
    text model
    text tier
    text route_reason
    int input_tokens
    int output_tokens
    numeric cost_usd
    int latency_ms
    timestamptz created_at
  }
  memory_items {
    uuid id PK
    uuid org_id FK
    uuid brand_id FK
    uuid product_id FK
    uuid claim_id FK
    text kind
    text content
    vector_768 embedding
    text embedding_model
    text embedding_version
    text sector FK
    text source_url
    text provenance
    numeric confidence
    int freshness_days
    boolean approved
    boolean revoked
    uuid revoked_by
    timestamptz revoked_at
    text revoked_reason
    boolean is_seed
    timestamptz created_at
    timestamptz last_used_at
  }
  campaigns {
    uuid id PK
    uuid run_id FK
    uuid org_id FK
    jsonb strategy
    jsonb creative
    jsonb validation
    text status "draft|awaiting_approval|approved|rejected"
    uuid approved_by FK
    text approver_role "generated: approver"
    timestamptz approved_at
    jsonb quality_scores "blind raters only"
  }
  approvals {
    uuid id PK
    uuid campaign_id FK
    uuid org_id FK
    uuid approver_id FK
    text approver_role "generated: approver"
    text outcome "approve|revise|reject|undecided"
    text failed_gate
    text reason
    int seconds_spent
    boolean arm_inferred
    text arm_inference_note
    timestamptz decided_at
  }
```

## 2. Issues found against `Architecture.md` §9

IDs are the ones assigned when this list was first drafted.

| # | Issue | Why it matters | Resolution | Status |
|---|---|---|---|---|
| E1 | `products`, `product_claims`, `llm_calls`, `campaigns` have no `org_id` | Rules.md P3/P4 and §9's RLS rule are written against `org_id`; without it RLS needs join-based policies | Denormalised `org_id uuid not null` on all four, RLS on the row's own `org_id` — no parent-table walking. Since E8 the policy also checks the caller's role (`app.has_role`). Consistency enforced by composite FKs — see §3. | **Accepted 2026-10-14** |
| E2 | `memory_items` has no `embedding_model` / `embedding_version` | `Memory.md` §5 requires them from day one | Add both, `text`. Nullable because `embedding` is nullable; check constraint: both non-null whenever `embedding` is non-null. | **Accepted 2026-10-14** |
| E3 | `brands.voice_embedding` has no model/version columns | Same drift risk as E2, for the brand-voice gate | Add `voice_embedding_model`, `voice_embedding_version` (`text`); check constraint: both non-null whenever `voice_embedding` is non-null | **Accepted 2026-10-14** |
| E4 | No revocation audit columns | `Memory.md` §6.4 requires who revoked what and when | Add `revoked_by uuid`, `revoked_at timestamptz`, `revoked_reason text` to `memory_items`; check constraint: when `revoked`, `revoked_by` and `revoked_at` are non-null | **Accepted 2026-10-14** |
| E5 | No link from `memory_items` (`kind='claim'`) to `product_claims` | Decay exemption in `Memory.md` §3.3 keys on `claim_type`, which lives only on `product_claims` | Add nullable `memory_items.claim_id`, composite FK `(claim_id, org_id) → product_claims (id, org_id)`, `on delete set null (claim_id)` | **Accepted 2026-10-14** |
| E6 | `provenance = 'seed'` is the only seed marker | `Rules.md` A3 requires seed exclusion enforced in code; free text is fragile | Add `is_seed boolean not null default false` | **Accepted 2026-10-14** |
| E7 | `runs.product_id`, `memory_items.brand_id`, `memory_items.product_id` reference parents without an `org_id` check | A run or memory item in Org A can point at Org B's product or brand; RLS on the child does not catch it | Composite FKs to the parents' `unique (id, org_id)`, same pattern as E1 — see §3 | **Accepted 2026-10-14** |

| E8 | No role-based access: any org member could do anything | An approver gate (T6) is meaningless if every member can approve; viewers must not edit product truth | `org_members (org_id, user_id, role)` with roles owner/editor/approver/viewer; roles revoked, never deleted; every tenant RLS policy checks role via `app.has_role` (§4) | **Accepted 2026-10-14** |
| E9 | No general audit trail | Who changed what is needed for approvals, revocations and registry edits; retrofitting loses history | `audit_log`, append-only: update/delete/truncate revoked from all app roles **and** blocked by trigger for the table owner | **Accepted 2026-10-14** |
| E10 | Only a per-run cost cap; `budget_cap_usd` had no stated period | One organisation could start unbounded runs, each under the per-run cap | `monthly_budget_cap_usd`, `rate_limit_runs_per_hour`, `rate_limit_requests_per_minute` on `organisations`; checked alongside the per-run cap (`Architecture.md` §9). `budget_cap_usd` renamed — no data existed. | **Accepted 2026-10-14** |
| E11 | Regulated-sector safety depended on an agent (A4) not built in Phase 6 | Required disclaimers / prohibited phrasings must not depend on generation | `sector_constraints` registry (human-authored, versioned, never edited in place or deleted) and a `sectors` table that `brands.sector` and `memory_items.sector` reference, so a misspelled sector cannot bypass the registry | **Accepted 2026-10-14** |
| E12 | RLS required an `org_id` claim in the JWT (one active org per token, set by a custom access token hook) | A single org claim forces one organisation per user, contradicting the agency persona (`PRD.md` §3) and any consultant working across client orgs; it also needed a hook to maintain | No org claim. Membership is resolved from `org_members` keyed on `auth.uid()`, always written `(select auth.uid())` so it is evaluated once per statement. Reads: `org_id in (select app.member_org_ids())`, likewise evaluated once per statement; role-specific writes: `app.has_role(org_id, roles)`; both stable `security definer`. **The custom access token hook is no longer required and is not built.** The org a request is for is an explicit API parameter, filtered on per `Rules.md` P3 | **Accepted 2026-10-14** |
| E13 | Approval decisions had nowhere to live | `campaigns` records only an approval (`approved_by`), not revise / reject / undecided, time spent or arm inference (`docs/approval-rubric.md` §5); `quality_scores` holds the blind raters' scores and must not mix in the approver's decision | `approvals` (migration 0009), one append-only row per decision; composite FK to `campaigns (id, org_id)`; approver constrained as for `campaigns.approved_by` (role FK + active-approver trigger); reason required unless `approve`; no failed gate on `approve`; a note required when `arm_inferred`. Added now rather than before Phase 6, so it is never retrofitted onto populated tables | **Accepted 2026-10-14** |

`campaigns.approved_by` is additionally constrained (E8 + T6): FK on
`(org_id, approved_by, approver_role)` → `org_members (org_id, user_id, role)` with
`approver_role` generated as `'approver'`, plus a trigger rejecting a revoked approver.

## 3. Keeping `org_id` consistent across references (E1, E5, E7)

A copied `org_id` can disagree with its parent — e.g. a product whose `org_id` is Org B under a
brand owned by Org A. That would be a tenant-isolation hole that RLS cannot see. It is prevented in
the schema, not in application code:

| Child | Constraint |
|---|---|
| `brands` | `unique (id, org_id)` (target for the FK below) |
| `products` | `foreign key (brand_id, org_id) references brands (id, org_id)` |
| `products` | `unique (id, org_id)` |
| `product_claims` | `foreign key (product_id, org_id) references products (id, org_id)` |
| `runs` | `unique (id, org_id)` |
| `llm_calls` | `foreign key (run_id, org_id) references runs (id, org_id)` |
| `campaigns` | `foreign key (run_id, org_id) references runs (id, org_id)` |
| `product_claims` | `unique (id, org_id)` (target for E5) |
| `runs` | `foreign key (product_id, org_id) references products (id, org_id)` (E7) |
| `memory_items` | `foreign key (brand_id, org_id) references brands (id, org_id) on delete cascade` (E7) |
| `memory_items` | `foreign key (product_id, org_id) references products (id, org_id) on delete set null (product_id)` (E7) |
| `memory_items` | `foreign key (claim_id, org_id) references product_claims (id, org_id) on delete set null (claim_id)` (E5) |
| `campaigns` | `unique (id, org_id)` (target for E13; added in 0009) |
| `approvals` | `foreign key (campaign_id, org_id) references campaigns (id, org_id) on delete cascade` (E13) |

`on delete set null (col)` nulls only the named column. A plain `on delete set null` on a composite
FK would also null `org_id`, which is `not null`, and the parent delete would fail.

Nullable reference columns use the default `MATCH SIMPLE`: when the reference column is null the
FK is not checked, which is intended for org-wide memory items and product-less runs.

### Required before the first migration

- [ ] **Confirm the Supabase project runs PostgreSQL 15 or later** (`select version();`), and
  record the version and date here. `on delete set null (product_id)` and
  `on delete set null (claim_id)` on `memory_items` depend on it; on PostgreSQL 14 or earlier the
  migration fails. No migration is applied until this box is ticked.

The cross-tenant test in Phase 1 must include an attempt to insert a child row whose `org_id`
differs from its parent's, and assert it is rejected.

## 4. Role matrix (enforced by RLS)

Roles are held per organisation; a user who belongs to several organisations has, in each, only
the roles granted there (E12).

| Table | viewer | approver | editor | owner | Backend (service role) |
|---|---|---|---|---|---|
| `organisations` | read | read | read | read, update | all |
| `org_members` | read | read | read | read, add, revoke | all |
| `brands`, `products`, `product_claims` | read | read | read, insert, update | + delete¹ | all |
| `runs` | read | read | read, start | read, start | status updates |
| `llm_calls` | read | read | read | read | insert |
| `memory_items` | read | read | read, **revoke only** | read, **revoke only** | insert (after approval, `Rules.md` P8) |
| `campaigns` | read | read | read | read | write; approval must name an active approver (FK + trigger) |
| `approvals` | read | read | read | read | **insert only**; approver must be an active approver (FK + trigger) |
| `audit_log` | — | — | — | read own org | **insert only** |
| `sectors`, `sector_constraints` | read | read | read | read | admin path writes |

¹ A product (and so its brand) that has runs cannot be deleted by anyone: `runs` are the
evaluation's raw logs (`Rules.md` A1) and the `runs → products` FK has no delete action.

"Revoke only": `authenticated` holds column-level `update` on the four revocation columns of
`memory_items` and nothing else, and the policy requires
`revoked_by = (select auth.uid())`.

## 5. Schema test record

The schema is tested by `backend/tests/db/` (merge-blocking, `Rules.md` §6), which applies
`supabase/migrations/` **unmodified** to `pgvector/pgvector:pg17` (pinned by digest in CI) after a
test-only platform stub (`backend/tests/db/supabase_platform_stub.sql`): Supabase's API roles and
default grants, a minimal `auth.users`, and `auth.uid()` / `auth.jwt()` copied verbatim from
Supabase's own auth migrations.
Requests are simulated the way PostgREST does it: `set local role
authenticated` plus the `request.jwt.claims` GUC. Policy text under test is the deployed text.

| Date | Environment | Result |
|---|---|---|
| 2026-10-14 | Scratch harness, PostgreSQL 16.2 (embedded), pgvector/pg_trgm stripped | 39/39 — superseded: the harness pre-created schema `app` with grants, masking finding F1 |
| 2026-10-14 | `backend/tests/db/` on PostgreSQL 16.2 (embedded) with migrations copied to a scratch dir and pgvector/pg_trgm/HNSW stripped (logic check only, not the gate) | 55 passed; 2 failed as expected (extension and HNSW tests need real pgvector) |
| 2026-10-14 | After E12/E13: same embedded PostgreSQL 16.2 logic check (pgvector, pg_trgm and HNSW stripped from a scratch copy) | 80 passed; 2 failed as expected (extension and HNSW tests need real pgvector) |
| — | `backend/tests/db/` on `pgvector/pgvector:pg17` (CI / Docker) | **not yet run** — no GitHub remote, and Docker's engine was not running locally |

Findings from moving the schema into migrations:

- **F1 (fixed).** No migration granted the API roles `USAGE` on schema `app`, so every RLS policy
  calling `app.has_role()` would have failed on Supabase with "permission denied for schema app".
  The first harness hid this by creating the schema itself. Fixed in `0001_extensions.sql`.
- **F2 (test corrected, schema unchanged).** A product with runs cannot be deleted (see ¹ in §4);
  the original test assumed it could.
- All `app.*` functions now pin `search_path = ''` and schema-qualify table references.

Not covered by these tests: Supabase's real `auth` service (no custom access token hook is
needed: E12 removed the org claim); whether pgvector is installed in Supabase's `extensions` schema (the
migrations create it in the default schema); and application code beyond the two Phase 1
application-layer checks.
