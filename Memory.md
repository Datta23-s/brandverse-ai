# BrandverseAI — Marketing Memory

| Field | Value |
|---|---|
| Document | Memory.md |
| Version | 1.0 |
| Date | 14 October 2026 |
| Depends on | `Architecture.md` §6, §9 |

---

## 1. Why memory is the thesis

Strip away the agents, the 3D site and the routing, and one claim remains:

> **A marketing intelligence system that remembers what it learned gets cheaper, faster and better with every campaign. One that does not, pays full price forever.**

Baseline A and Baseline B both have zero memory. Every metric in the evaluation — ARR, SRR, CR, LR — moves because of this subsystem. If memory does not work, the project has no finding. Build it carefully and measure it honestly.

---

## 2. What is a memory item

A memory item is **a reusable, attributed unit of marketing knowledge**. Not a chat log. Not a cached response.

| Kind | Example | Typical freshness |
|---|---|---|
| `persona` | "Price-sensitive first-time buyers, 22–30, discover via Instagram Reels, objection: durability" | 180 days |
| `competitor` | "Competitor X positions on warranty length, not price" | 60 days |
| `insight` | "In this category, founder-story copy outperforms spec-sheet copy" | 365 days |
| `claim` | "Product is IP67 rated — verified on manufacturer spec sheet" | 365 days |
| `creative` | "Hook 'Built for the monsoon' scored 0.91 brand consistency and was approved" | 180 days |
| `sector` | "Health supplements in India require FSSAI disclaimer on all claims" | 365 days |

**Rule:** if a fact is true only for one campaign, it is not a memory item. Memory stores what generalises.

---

## 3. Retrieval scoring

### 3.1 The M-score

```
M = wₛS + w_pP + w_cC + w_mA + w_fF + wₑE
```

> **Naming note.** The source diagram labels both the total score and the "market/sector alignment" component `M`. That collision is resolved here: the component is renamed **`A`** (alignment). Use `A` in all code and in the dissertation. Flag this in the viva as a correction you made to your own design — examiners reward that.

| Symbol | Component | Computed as | Default weight |
|---|---|---|---:|
| `S` | Semantic similarity | Cosine similarity of query embedding to item embedding, normalised to [0,1] | 0.30 |
| `P` | Product match | 1.0 same product, 0.7 same brand, 0.4 same category, 0.1 otherwise | 0.20 |
| `C` | Customer match | Cosine similarity of target persona vector to item's persona context | 0.15 |
| `A` | Market / sector alignment | 1.0 same sector, 0.6 adjacent sector, 0.2 unrelated | 0.15 |
| `F` | Freshness | Decay function, §3.3 | 0.10 |
| `E` | Evidence quality | Stored source score × confidence | 0.10 |

Weights live in `config/scoring.yaml`. **Tune them in Phase 10 against a labelled relevance set and report both the defaults and the tuned values.** Publishing hand-picked weights as if they were derived is the kind of thing that gets a thesis questioned.

### 3.2 Reuse decision bands

| `M` | Decision | Behaviour |
|---|---|---|
| **≥ 0.90** | Reuse directly | Inject verbatim as established knowledge. No re-research of this slot. |
| **0.80 – 0.89** | Reuse with minor adaptation | Inject, but an agent may rephrase for the current product |
| **0.65 – 0.79** | Supporting context | Inject as background only. Cannot be the sole basis for a claim. |
| **< 0.65** | Perform fresh analysis | Discard. Mark the knowledge slot as a research gap. |

These bands feed the Research Necessity Engine directly: `memory_coverage` is the fraction of required knowledge slots filled at `M ≥ 0.80`.

### 3.3 Freshness decay

```
F = exp(−λ · age_days / freshness_days)
```

with `λ = 0.7`. An item at exactly its `freshness_days` scores `F ≈ 0.50`; at twice that age, `F ≈ 0.25`. Nothing is deleted by age — it simply stops winning retrieval, and a fresher fact displaces it naturally.

Items with `kind='claim'` and `claim_type='verified'` are exempt from decay until their source URL fails revalidation. **A verified spec does not become false because it is old.**

---

## 4. Hybrid retrieval

Vector search alone misses exact terms — model numbers, regulation codes, brand names. Keyword search alone misses paraphrase. Run both and fuse.

### 4.1 Reciprocal Rank Fusion

```
RRF(d) = Σ  1 / (k + rank_i(d))        k = 60
```

### 4.2 Retrieval function

```sql
create or replace function match_memory(
  p_org_id      uuid,
  p_query_embed vector(768),
  p_query_text  text,
  p_brand_id    uuid default null,
  p_sector      text default null,
  p_limit       int  default 20
) returns table (id uuid, content text, kind text, source_url text,
                 provenance text, confidence numeric, created_at timestamptz,
                 freshness_days int, vec_rank int, txt_rank int, rrf numeric)
language sql stable as $$
with scoped as (
  select * from memory_items
  where org_id = p_org_id
    and not revoked and approved
    and (p_brand_id is null or brand_id = p_brand_id or brand_id is null)
),
vec as (
  select id, row_number() over (order by embedding <=> p_query_embed) as r
  from scoped where embedding is not null
  order by embedding <=> p_query_embed limit 60
),
txt as (
  select id, row_number() over (
    order by ts_rank_cd(to_tsvector('english', content),
                        websearch_to_tsquery('english', p_query_text)) desc) as r
  from scoped
  where to_tsvector('english', content) @@ websearch_to_tsquery('english', p_query_text)
  limit 60
)
select s.id, s.content, s.kind, s.source_url, s.provenance, s.confidence,
       s.created_at, s.freshness_days,
       v.r::int, t.r::int,
       coalesce(1.0/(60 + v.r), 0) + coalesce(1.0/(60 + t.r), 0) as rrf
from scoped s
left join vec v on v.id = s.id
left join txt t on t.id = s.id
where v.id is not null or t.id is not null
order by rrf desc
limit p_limit;
$$;
```

RRF produces the **candidate set**. The `M`-score is then computed in the application layer over those candidates, because `P`, `C` and `A` need request context the database does not hold. Do not try to compute `M` in SQL.

**As built (migration 0022, 2026-10-18):** `app.match_memory` follows this specification with
deliberate corrections — `SECURITY INVOKER` and a pinned `search_path` (the caller's row-level
security applies on top of the `org_id` filter); seed memory excluded unless `p_include_seed`
(opt-in); the full-text branch ordered before its `LIMIT 60` (above, it had no `ORDER BY`, so which
60 matches survived was arbitrary); `p_sector` dropped (declared above but never used — sector
alignment is the `M`-score's `A`); and the cosine similarity, product category and backing claim's
type returned for the scorer. `hnsw.ef_search` is set by the caller, not by the function (§4.3).
The `M`-score is `backend/app/memory/scoring.py`, weights in
`config/scoring.yaml` (defaults, untuned): a component that cannot be computed yet — `C` until
persona vectors (Phase 6), `E` until source scores (Phase 5) — is left out and the remaining
weights renormalised, never filled with a guess, and the breakdown names it.

### 4.3 Index strategy

```sql
create index on memory_items using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);
create index on memory_items using gin (to_tsvector('english', content));
create index on memory_items (org_id, brand_id, kind) where not revoked;
```

Set `hnsw.ef_search = 100` per session for recall. **As built (2026-10-18):** the CALLER sets it,
transaction-local, before calling `app.match_memory` (`backend/app/memory/retrieval.py`); the
default lives in `config/scoring.yaml` (`retrieval.hnsw_ef_search`) beside the `M`-score weights,
and the value in effect is returned and logged with every retrieval. It is deliberately not part
of the function definition: it is a search-time quality-versus-speed parameter, so evaluation
reports retrieval quality against it and Phase 6 reports latency against it as a curve, not one
frozen operating point (an earlier draft of 0022 set it on the function, which also failed on the
live project: `Architecture.md` §11). Below roughly 10,000 rows HNSW adds little over a sequential scan — build the index anyway so behaviour at scale is the behaviour you tested.

---

## 5. Embeddings

**Decision 2026-10-18 (project owner): embeddings are computed LOCALLY by a pinned
sentence-transformer, never by a hosted API, and are never billed.** It replaces the Gemini
embedding endpoint below. Reasons, in order of weight: (a) **reproducibility** — a hosted model can
change under us with no version bump and every retrieval number would silently stop being
reproducible; a pinned local model with a recorded revision and weights hash is reproducible years
later; (b) **quota** — embeddings are the highest-volume call (every memory write, every query) and
must not compete with the strong tier's 20/day; (c) **it is not the contribution** — the claim is
about hybrid retrieval, the `M`-score and the reuse bands; the encoder is a parameter, and the
thesis says so.

| Decision | Value |
|---|---|
| Model | **`BAAI/bge-base-en-v1.5`**, Hugging Face revision **`a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`** (last modified 2024-02-21), MIT licence; weights `model.safetensors`, 437,955,512 bytes, SHA-256 **`c7c1988aae201f80cf91a5dbbd5866409503b89dcaba877ca6dba7dd0a5167d7`** (read from the Hub 2026-10-18). Loaded with `sentence-transformers` at that exact revision; the hash is checked on load. |
| Dimension | **768** — equal to `memory_items.embedding vector(768)` (migration 0006), so the column is unchanged and the Phase 0 storage measurement (9,495 B/row, `Architecture.md` §11) stands. A test asserts the configured dimension equals the column's. |
| Normalisation | The model's own pipeline ends in a `Normalize` module: every vector is unit length, so cosine distance is exact. Short retrieval queries get BAAI's query instruction ("Represent this sentence for searching relevant passages: "); stored items do not. |
| Where | Computed on the laptop (CPU). No API, no `llm_calls` row, no quota, no cost. Max 512 tokens per input. |
| ~~Model (superseded 2026-10-18)~~ | ~~`gemini-embedding-2`, called with `output_dimensionality=768` (native output is 3072). **Normalisation verified 2026-10-14** against ai.google.dev/gemini-api/docs/embeddings: "Gemini Embedding 2 also auto-normalizes truncated dimensions (e.g., 768, 1536)" — no manual normalisation (unlike `gemini-embedding-001`, which requires it). Several texts in one request are aggregated into a single embedding unless each is wrapped in its own `Content` object. Free tier: 100 RPM / 30K TPM / 1000 RPD. Model ID lives only in `config/models.yaml`, never here or in an env var.~~ |
| Chunking | Semantic, 200–400 tokens, 15% overlap |
| Normalisation | Lowercase, strip boilerplate, collapse whitespace. Never strip numbers or units. |
| Storage | `vector(768)`, cosine distance |
| Re-embedding | Required if the model changes. Store `embedding_model` and `embedding_version` on each row so a migration is possible. |

**Store the model version from day one.** Every row records the encoder's id and revision
(`embedding_model`, `embedding_version`) because a vector embedded by a different model, or a
different revision of this one, cannot be compared with a vector embedded by this one; and the
relevance pool is frozen against a specific encoder revision, recorded in `pool.lock`.

**As built (Phase 4 chunk B, 2026-10-18):** `backend/app/memory/embeddings.py`, configured by
`config/models.yaml` `embedding:` (the only place the model id appears in code, enforced by the
Rules.md P5 test). Loading refuses unless the weights file at the pinned revision has the recorded
size and SHA-256 (hashed as raw bytes), the pipeline ends in `Normalize`, and the output dimension
equals the configured 768; every batch is checked for unit length. `embed_queries` adds the query
instruction and `embed_documents` does not — separate methods, so one cannot be used for the
other. A row's `embedding_model` and `embedding_version` are the encoder's id and revision. A
database test reads the dimension of `memory_items.embedding` and `brands.voice_embedding` from
the migrated schema and asserts it equals the configured one. `sentence-transformers` is in the
optional `embeddings` dependency group (approved 2026-10-18), which CI does not install; tests use
stand-ins, and `scripts/check_encoder.py` checks the real model on the laptop. The relevance pool
uses the same encoder and records its id and revision in `pool.lock`.

---

## 6. Write-back — step 15

The strictest part of the system. **Writing a wrong fact to memory poisons every future campaign for that brand.**

### 6.1 Eligibility

A fact is written to memory only if **all** hold:

1. The campaign containing it reached `status = 'approved'` by a human.
2. It passed the claim gate (`verified`, or `deduced` with a scored source).
3. It generalises — it is not specific to one campaign's dates, discount or slogan.
4. It is not a near-duplicate of an existing item (cosine > 0.95 → update `last_used_at` and raise `confidence` instead of inserting).

### 6.2 What is never written

- Anything from a rejected or revised-away campaign
- Raw research page bodies (zero-retention — Redis TTL only)
- Any consumer PII
- Model speculation with no source (`deduced` with `evidence_url is null`)
- Campaign-specific ephemera: prices, dates, promo codes

### 6.3 Confidence update on reuse

When an item is retrieved at `M ≥ 0.90` and appears in an approved campaign:

```
confidence ← min(0.99, confidence + 0.02)
last_used_at ← now()
```

When an item is flagged by the validation engine or edited out by a human reviewer:

```
confidence ← confidence − 0.15
if confidence < 0.30: revoked ← true
```

This gives memory a cheap, auditable correction mechanism without a retraining loop.

### 6.4 Revocation

`revoked = true` removes an item from all retrieval permanently. Rows are never hard-deleted — the audit trail is part of the contribution. **Stated limitation (2026-10-14):** since migrations 0010 and 0011 no application role can delete or truncate `memory_items`, delete the organisation that would cascade into it, or delete a brand that has memory items (`memory_items.brand_id` is `on delete no action`). What remains is the table owner: nothing in the database binds `postgres` on managed Supabase. The audit trail is tamper-resistant for application code, not tamper-proof. See `Architecture.md` §9, "Stated limitation". A brand manager must be able to revoke from the UI, and the system must record who revoked what and when.

---

## 7. Cold start

A brand's first campaign has zero memory. This is correct and must not be hidden.

| Run | Expected behaviour |
|---|---|
| 1st for a brand | `memory_coverage ≈ 0` → `R` high → **D2** deep research, ~15–20 searches, highest cost |
| 2nd, related product | Brand/sector items hit at `P = 0.7`, `A = 1.0` → **D1** focused |
| 3rd+, same product family | Most slots filled at `M ≥ 0.80` → **D0** or shallow D1 |

**The evaluation must include this progression.** A test set of 30 unrelated cold-start requests will show a memory benefit of approximately zero and will destroy the thesis. Design the set as: 10 brands × 3 campaigns each, where campaigns 2 and 3 are related to campaign 1.

### 7.1 Seeding

Ship seed memory for the demo: 3 brands, ~40 curated items each, marked `provenance='seed'`. Seeded items are **excluded from evaluation runs** — include them and the memory advantage is manufactured. Enforce this with a filter in the harness, not with discipline.

---

## 8. Measuring the memory subsystem

Report these independently of the end-to-end metrics:

| Metric | Definition | Target |
|---|---|---|
| **Hit rate** | Runs with ≥ 1 item at `M ≥ 0.80` ÷ total runs | > 60% by run 3 per brand |
| **Reuse depth** | Mean items injected per run | 4–12 |
| **Coverage** | Knowledge slots filled from memory ÷ required slots | > 0.5 by run 3 |
| **Precision@10** | Human-judged relevance of top 10 retrieved | > 0.7 |
| **Staleness incidents** | Retrieved items a human marked outdated | < 5% |
| **Poisoning incidents** | Wrong facts that reached a campaign via memory | 0 — investigate every one |

Precision@10 needs a hand-labelled relevance set. Build it in Phase 4 with 50 queries, not in Phase 10 under deadline pressure.

**Who labels it — decided 2026-10-18 (project owner): Kundan labels the set by hand, blind, before
any tuning.** Not the developer (whose retrieval is judged against it) and not a model (the system
would be measured against another model's opinion). The task is built so the labels cannot be
contaminated: a fixed candidate pool per query, drawn from the union of lexical and vector
retrieval so it is not shaped by the ranker under test; candidate order randomised per query; no
scores, ranks or retriever shown; three judgments — relevant, partially relevant, not relevant —
each defined in one sentence in the task; 5 unmarked duplicate queries to measure the labeller's
own consistency, which is reported. Labels are timestamped and frozen before any ranker tuning; if
tuning later suggests a label was wrong, the label stands and the disagreement is reported
(`docs/evaluation-plan.md` §7).

---

## 9. Failure modes

| Mode | Symptom | Defence |
|---|---|---|
| **Poisoning** | A hallucinated fact is approved once, then reused forever as established | Approval gate + provenance + revocation + confidence decay |
| **Echo chamber** | System keeps recommending what it recommended before; originality score falls | Cap injected items; force ≥ 1 fresh research slot on every run regardless of `R`; track originality over time |
| **Staleness** | Competitor data 9 months old presented as current | Freshness decay + scheduled revalidation (C4) |
| **Over-retrieval** | 40 items injected, context diluted, quality drops | Hard cap of 12 injected items, ranked by `M` |
| **Tenant leak** | Org A's insight surfaces for Org B | RLS + `org_id` filter inside `match_memory` + an automated cross-tenant test that must stay green |
| **Embedding drift** | Model upgraded, old and new vectors incomparable | `embedding_model` + `embedding_version` columns and a re-embed migration |

> The cross-tenant leakage test is not optional. For an agency user managing competing brands, a leak is a commercial injury, not a bug.

---

## 10. Implementation checklist

- [ ] `vector` and `pg_trgm` extensions enabled
- [ ] `memory_items` created with RLS policy in the same migration
- [ ] HNSW and GIN indexes built
- [ ] `match_memory` function deployed and unit-tested
- [ ] `M`-score calculator with component breakdown returned to the API
- [ ] Reuse bands wired into the Research Necessity Engine
- [ ] Freshness decay applied at scoring time, not at write time
- [ ] Write-back gated on human approval
- [ ] Near-duplicate detection at cosine > 0.95
- [ ] Revocation endpoint and UI control
- [ ] Seed data flagged and excluded from the harness
- [ ] 50-query relevance set labelled
- [ ] Cross-tenant leakage test green in CI
