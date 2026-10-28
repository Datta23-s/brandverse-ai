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

**What `E` takes — decided 2026-10-25 (project owner).** An item backed by several sources carries
**the maximum** of their source scores as its `E` input. The deciding property is monotonicity:
adding a source must never lower an item's evidential score. The mean fails it (corroborating a
strong source with a mediocre one makes the item worse) and so does the minimum (one weak source
destroys an item backed by a good one); each makes finding more evidence hurt. The maximum cannot.
**Corroboration is not modelled.** Three independent decent sources arguably beat one decent source,
and the maximum does not capture that; the fix is a bonus term with no principled value, which is
the kind of constant this project avoids. It is named here as future work and left out, and the
source score of one page likewise omits the "corroboration count" that Architecture.md §6.2 first
listed. **Storage:** the item stores both the aggregate (its `E` input) and the per-source scores,
so the rule can be changed later without refetching anything. Build order: the scorer
(`app/research/source_scoring.py`, standalone), then the migration, then `app.match_memory`
returning the stored score, then the caller in Phase 6.

**Two named weaknesses of the source model (recorded 2026-10-25).** (1) *The authority prior is an
untuned judgement of the developer*: the classes and their values (`config/scoring.yaml` `source:`)
have no citation behind them. The evaluation reports its headline result with the prior as
configured and with it flattened to 0.5 for every host, so that the prior cannot carry the claim
unseen (`docs/evaluation-plan.md` V5). (2) *The scorer conflates authority with independence* (ruled 2026-10-26). A brand's own domain
is high authority (it knows) and zero independence (it has an interest) about its own product, yet
scores as an ordinary unknown host (0.50), above forum posts (0.25). The rule: a first-party source
is acceptable evidence for a factual property of the brand's own product, and not acceptable as the
sole evidence for a comparative or efficacy claim (a claim that trips the superlative list, or one
in a regulated sector). Excluding first-party sources would be wrong. A brand's own domain can
back a claim by two paths: `deduced` claims once the Phase 6 caller wires `source_score` in, and
`verified` claims **now**, because `product_claims.evidence_url` has no host check, so such a claim
is unmarked and indistinguishable from independent evidence (a live gap). The fix needs new data,
a brand domain, so it is not a scoring tweak: Phase 5 carries an unpopulated `first_party` marker
on every search result; Phase 6 adds the domain field, the rule and an independence factor
(`docs/evaluation-plan.md` V6).

**A defect found and fixed, 2026-10-26: a throttled search is not an absence of evidence.** Memory
items rest on evidence the search layer fetches, and the real SearXNG answers HTTP 200 with an empty
result list, naming the failed engines in `unresponsive_engines`, when its upstream engines are
throttled. The provider returned that as an empty list, indistinguishable from a search that found
nothing; a topic would have been recorded as having no web evidence, no item would have been staged
for it, and a claim needing it would have been blocked for lack of evidence when the cause was a
throttled IP. It is refused now (a failed search, not an absence), and a result set returned while
some engines failed carries the failed engines and reasons into the run record and report, so a run
that saw one engine is told from one that saw three. The mechanism and what it means for the
evaluation are in `docs/evaluation-plan.md` ("A defect found and fixed"); the engines and the
observed throttling are in `docs/searxng.md`.

**A defect found and fixed, 2026-10-26: the stored source score arrived nowhere, so `E` could never
enter `M`.** 0028 wrote `memory_items.source_score` (the derived maximum of an item's per-source
rows) and `app.match_memory` did not return it, so `Candidate.source_score` was `None` in the
application **whatever the database held**. `E` is left out when it is None and the remaining
weights are renormalised, so the omission was silent by construction: once sources were populated
the scores would have looked complete, every `M` would still have been computed from four
components, and the dissertation would have reported a six-component `M` that was always four. An
earlier audit recorded `E` and `C` as honestly left out while unbuilt; this is the layer underneath
it, the value arriving nowhere. Fixed by 0031 (the function returns it beside the `confidence` it
multiplies). **The guard that matters is not the fix but the test**: a db test takes the fields of
`Candidate` and the column names the function actually returns and asserts the first is a subset of
the second, so a rename on either side fails the database suite, which is merge-blocking. Checked by
mutation in both directions (2026-10-26): renaming the dataclass field fails it, naming the field;
renaming the returned column fails it, naming the column.

**Three instances now of something silently absent from a mechanism that claimed to include it**
(the pattern, worth naming in the write-up): **V4**, where the claim gate confirms a cited claim
exists, belongs to the product and is verified but never that it *entails* the statement citing it,
so an overstatement passes a check that looks like it covered this (`docs/evaluation-plan.md` V4,
still open); **the throttled empty 200** read as an absence of evidence (above, closed); and **this
one**, a stored trust input no reader could reach (closed). In each case the output looked complete:
nothing was wrong in it, something was missing from it, and the mechanism's own report could not
show the difference. The lesson applied here is the standing rule that a mechanism meant to cover
everything gets a constraint or a test rather than a documented gap — V4 is the one still carried as
a stated limitation, because entailment needs a detector that does not exist, not a check that was
forgotten.

**Stated limitation (quantified 2026-10-28): PDF sources are not read.** PDFs are skipped; they were 15% of URLs in a measured search (12 of 79, 2026-10-28), and the regulated-sector share by extension alone was 8.3%.
The 12 of 79 is ONE search ("FSSAI honey adulteration standards", a regulated-sector query, 22-engine
set, `evaluation/fetch_cost/fetch-20261028T033217Z.jsonl`) and the 8.3% is a floor from nine searches
(10 of 120 distinct regulated-group URLs ending `.pdf`; 1 of 96 general). A PDF is skipped visibly
(`FetchUnsupported` kind `pdf`), never silently, so evidence that lives only in a PDF (a regulator's
circular, a lab report) is absent from E, not marked weak. Support is deferred, not decided against:
Phase 6 and an end-to-end system come first (owner, 2026-10-28), and the decision to defer now rests
on this number. The sampled HEAD estimate (`docs/evaluation-plan.md` section 8) has still not been run.

### 3.2 Reuse decision bands

| `M` | Decision | Behaviour |
|---|---|---|
| **≥ 0.90** | Reuse directly | Inject verbatim as established knowledge. No re-research of this slot. |
| **0.80 – 0.89** | Reuse with minor adaptation | Inject, but an agent may rephrase for the current product |
| **0.65 – 0.79** | Supporting context | Inject as background only. Cannot be the sole basis for a claim. |
| **< 0.65** | Perform fresh analysis | Discard. Mark the knowledge slot as a research gap. |

These bands feed the Research Necessity Engine directly: `memory_coverage` is the fraction of required knowledge slots filled at `M ≥ 0.80`.

**The bands are PROVISIONAL — recorded 2026-10-25, before anything else touches them.** The
0.90 / 0.80 / 0.65 bands and the write-back gate at `M ≥ 0.90` (§6.3) were set as defaults and
have never been calibrated against a six-component `M`. They are applied today to a four-component
`M`: `S`, `P`, `A` and `F` renormalised over their 0.75 of the weight, because `C` (Phase 6) and `E`
(Phase 5, then the migration) do not exist yet. When `E` and `C` arrive, identical items score
differently (a perfect item with `E` = 0.6 falls from 1.0 to about 0.95), so the same thresholds
mean something else. **Nothing banded before `E` exists is comparable with anything banded after.**
Band tuning happens only after `E` and `C` exist, not before; no figure derived from a
four-component `M` is reported anywhere.

*Audit, 2026-10-25.* `score()` has no caller outside the unit tests; no real `M` has ever been
computed; and no recorded figure depends on `E`. The `m` that write-back compares with the reuse
band is supplied by its callers and set by hand in its tests. This was established by searching the
code and documents by pattern (`score(`, `source_score`, `left_out`, the module's imports and the
recorded figures), not by reading every line.

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
embedding endpoint below. Reasons, in order of weight: (a) **reproducibility** — a pinned local model
gives a recorded revision and a weights hash that can be checked on load; a hosted endpoint gives
neither, so only the local model ties a retrieval number to the exact model that produced its
vectors; (b) **quota** — embeddings are the highest-volume call (every memory write, every query) and
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
| Unit | **One memory item is one fact and is never split.** An item over 400 tokens is refused (`writeback.max_item_tokens`, counted by the encoder's own tokenizer). Decision below, 2026-10-19. |
| ~~Chunking (superseded 2026-10-19)~~ | ~~Semantic, 200–400 tokens, 15% overlap~~ |
| Normalisation | Collapse whitespace; never strip numbers or units; the stored text is exactly the text embedded, case kept. Case and accents are then handled by each path's own analyser, which do not fully agree — below. |
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

**Decision 2026-10-19 (project owner): a memory item is one fact and is never split; an item over
400 tokens is refused.** Chunking with overlap is a document-retrieval technique for prose.
`memory_items` hold facts, and a fact split across two chunks becomes two half-facts, each
retrievable and citable on its own: half a fact can be retrieved and cited as if it were whole. A
qualifier left behind in the other half is V4 (`docs/evaluation-plan.md` §4) again, self-inflicted.
The 200–400-token chunking row above is superseded for memory. A refusal is never silent (§6.1):
it is logged and recorded in `audit_log` against the run, and the author splits the content into
separate facts upstream. Boilerplate stripping is not built: a candidate is one statement, not a
page.

**Normalisation by path (checked 2026-10-19).** The stored text keeps its case; each path that
reads it applies its own analyser, checked on the pinned versions rather than assumed:

| Path | Analyser | Case | Accents |
|---|---|---|---|
| Vector half of `match_memory`, and near-duplicate detection (§6.1 rule 4, cosine on the same vectors) | the pinned tokenizer: `BertTokenizer`, `"do_lower_case": true`, `strip_accents` unset (so it follows lowercasing) in the revision's `tokenizer_config.json` | insensitive: "Induction-Safe pan" and "induction-safe pan" give identical token ids | insensitive: "Café crème" and "cafe creme" give identical token ids |
| Lexical half of `match_memory` (0022): `to_tsvector('english', content) @@ websearch_to_tsquery('english', query)` | PostgreSQL 17.11 `english` text search configuration | insensitive: identical lexemes, and the query matches either way | **sensitive**: `'café'` and `'cafe'` are different lexemes; neither matches the other |

So the two halves agree that "Induction-Safe" and "induction-safe" are the same string, and
lowercasing before either would change nothing. They disagree on accents: for "café" the vector
half finds "cafe" and the lexical half does not, so RRF fuses a match in one list with a miss in
the other.

**Open, and inert for the relevance-set results by a checked condition — ruled 2026-10-20 (project
owner).** The gap is left open. It changes a result only if an accented character occurs in the
text the result is computed from:

- *Relevance set (Precision@10 and the rest of §8's relevance figures).* Scanned 2026-10-20 for
  every character whose canonical decomposition carries a combining mark (what the tokenizer's
  accent stripping removes): none in `evaluation/relevance/brief.md`, `evaluation/test_set.yaml`,
  `evaluation/tuning_brands.yaml` or the V2 probe file (their only non-ASCII characters are em
  dashes and section signs). `backend/tests/test_evaluation_text_unaccented.py` makes it a checked
  fact rather than a remembered one: it scans everything under `evaluation/` and fails on any
  accented character.
  While it passes, the gap is **demonstrably inert for every relevance-set result, because no
  accented character appears in the corpus or the query set**. **Scanned 2026-10-25 over the
  finished corpus (120 items, redrafted blind and frozen), the 50 queries (frozen) and the pool
  built from them, rescanned after the redraft: 20 files under `evaluation/` (which include the
  superseded first corpus and its pool, kept as evidence), none with an accented character.**
  `corpus.yaml`, `queries.yaml`, the pool files and the V2 probe are pure ASCII; the only other
  non-ASCII characters are section signs and em dashes in headings and notes (`brief.md`,
  `queries.md`, `test_set.yaml`, `tuning_brands.yaml`) and one middle dot in the labelling page's
  own template, none in any text a retriever reads. So the claim holds for the set as it will be
  labelled and measured. If the
  test ever fails, the owner decides before the corpus or the pool goes further.
- *The main experiment's memory.* Items written during evaluation runs come from model output,
  not from a committed file, so the test cannot cover them in advance. Whether any accented
  character reached `memory_items.content` in the evaluation organisations is checked after the
  runs and reported with the results; until then the claim is not made for them.

**If it is ever fixed, it is fixed before the relevance pool is built, never after.** The fix is
the `unaccent` extension, a text search configuration built on it, and the GIN index rebuilt on
that configuration (a migration; the index built `CONCURRENTLY`, Rules.md §3.3). That changes
what the lexical half retrieves, and the frozen pool is tied to retrieval behaviour: each query's
candidates are drawn from the union of lexical and vector retrieval (`docs/evaluation-plan.md`
§7, "Pool"; frozen in `pool.lock`), so a pool built before the change would no longer be the pool
the system retrieves against. There is no duplicate hash: near-duplicates are found by cosine on the encoder's vectors, so they follow the
vector row.

---

## 6. Write-back — step 15

The strictest part of the system. **A wrong fact written to memory can be retrieved in any later
campaign for that brand, and the design compounds an error nobody notices.** Each reuse at
`M ≥ 0.90` in an approved campaign raises the item's `confidence` (§6.3), and `confidence` enters
the `M`-score through `E` (source score × confidence, §3), so the item ranks higher and is more
likely to be retrieved again. The counterweight depends on someone noticing: a validation flag or a
reviewer's edit lowers `confidence` (§6.3), and revocation removes the item (§6.4). Until source
scores exist (Phase 5) `E` is left out of `M`, so `confidence` does not yet affect retrieval; the
compounding starts then. Hence the eligibility rules below.

**The governing principle — decided 2026-10-19 (project owner): approval gates what adds trust,
never what reduces it.** Human approval gates anything that ADDS or REINFORCES trust in memory:
new facts, reuse bumps (§6.3), the reinforcement of a near-duplicate (§6.1 rule 4). Corrections
that REDUCE trust — confidence decrements and revocations — commit regardless of the campaign's
outcome. A case not listed here is decided by which side of that line it falls on. The reason is
the asymmetry of the two errors: trust added without approval is how memory gets poisoned (§9);
a correction withheld because the campaign was rejected leaves in memory the very item that
misled it, and a rejected campaign is where an edit-out matters most.

**Corrections and the attempt rule.** Corrections are still bound by the attempt rule
(`docs/evaluation-plan.md` §2): they commit at the end of any attempt that terminated without
infrastructure failure, whatever its status. **A run whose status is `failed` is not thereby
"not the primary attempt".** A run that ends `failed` because its commit was refused — something
adding trust was staged and no campaign was approved — terminated without infrastructure failure,
so it is the primary attempt and its corrections commit with the `failed` status. The same holds
for `partial` (stopped by the budget) and `failed` by any other error. Only an infrastructure
failure (rate limit, quota, network, process crash or cancellation) discards corrections, as it
discards everything. Built in `backend/app/core/budgeted_run.py`; tested end to end in
`backend/tests/db/test_memory_staging.py`.

### 6.1 Eligibility

A fact is written to memory only if **all** hold:

1. The campaign containing it reached `status = 'approved'` by a human.
2. It passed the claim gate (`verified`, or `deduced` with a scored source).
3. It generalises — it is not specific to one campaign's dates, discount or slogan.
4. It is not a near-duplicate of an existing item (cosine > 0.95 → update `last_used_at` and raise `confidence` instead of inserting).
5. It is **one fact, at most 400 tokens** (§5, decided 2026-10-19). Write-back never splits a
   candidate. Content that holds more than one fact, or runs over the limit, must be split by its
   author — the agent or step that proposes it — into separate facts upstream, each a candidate
   of its own that meets rules 1–4. Write-back refuses anything else and records the refusal.

Every refused candidate is visible: a WARNING log line naming the run and the reasons when it is
screened, and one `audit_log` row per run (`memory.candidates_refused`) listing each refused
candidate with its reasons and token count. A candidate refused as personal data has its content
withheld from both, since copying it would store the PII the refusal kept out.

**As built (Phase 4 chunk C, 2026-10-19):** `backend/app/memory/writeback.py`; its module
docstring has each rule, where it is checked (when a candidate is staged, or at commit inside the
run's terminal transaction) and what is NOT detected. In short: rule 1 is checked at commit, and a
run without a human-approved campaign has its whole commit refused (the run ends `failed`; its
corrections still commit, per the principle above). Rule 2
requires a verified, active claim the gate resolved with no finding against it; `deduced` is
refused until Phase 5 scores sources. Rule 3 and §6.2 are covered only in the forms the screens
name (prices, discounts, dates, promo codes, email addresses, phone-number-shaped digit runs);
anything else campaign-specific or personal stays with the approver. Rule 4 is an exact scan
within the same organisation, brand, kind and encoder revision, seed items excluded; a revoked
near-duplicate is never written again, and one write-back counts a fact once. Rule 5 is checked
when a candidate is staged (reason `longer_than_one_fact`, with the token count); that a candidate
under the limit holds only one fact is NOT detected, and stays with its author. Every commit
writes one `audit_log` row. Tests: `backend/tests/test_writeback.py`,
`backend/tests/db/test_writeback_commit.py`, `backend/tests/db/test_memory_staging.py`.

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

**As built (Phase 4, 2026-10-19):** `backend/app/memory/writeback.py`, the numbers in
`config/scoring.yaml` `writeback:`. Inside the run's terminal transaction, corrections apply first,
then reuse bumps, then new items, so an item corrected in a run is neither bumped nor reinforced by
it.

- *Reuse bump* — an addition: staged by the step-15 node for each retrieved item the approved
  campaign used, at `M ≥ 0.90` (the node refuses one below the band; the commit skips it), and the
  same bump applies when write-back meets a near-duplicate. One run bumps an item once. Which
  retrieved items a campaign used is recorded by campaign assembly (Phase 6); the bump takes that
  list as given.
- *Decrement* — a correction: staged where it is observed (`Correction.flagged` at validation,
  `Correction.edited_out` at approval, Phase 6 wiring). One run corrects an item once; further
  corrections of it in that run, and corrections of an already revoked item, are skipped and kept
  in the run's `memory.writeback` audit row.
- *Revocation below 0.30* — a revocation by rule (§6.4): `revoked_by` null, and `revoked_reason`
  the decay record migration 0024 requires.
- **Every item has a confidence — decided 2026-10-20 (project owner), migration 0025.** An item
  with none would have nothing to lower, so it could never decay out: immortal by omission,
  exempt from the mechanism meant to cover every item. The exemption is made impossible rather
  than remembered: `memory_items.confidence` is `NOT NULL` in the database, with no default (a
  default would be a guessed value standing in for a missing one), so no path can write such an
  item; and write-back's `Candidate.confidence` is required, stated by whoever proposes the fact.
  Done while `memory_items` held 0 rows on the live project: once data exists, a backfill would be
  a decision about what the missing values were, which nobody would know. Applied 2026-10-25.
- **Revocation tests the value, not the transition — decided 2026-10-25 (project owner),
  migration 0026.** The rule as first built ran only when a decrement happened, so an item
  written at 0.20 was live and retrievable until some correction found it: exempt from the
  mechanism by how it entered, the pattern of the previous bullet. Now `memory_items` refuses
  `confidence` outside [0, 1] and refuses a live item below 0.30 (`revoked or confidence >=
  0.30`; the number is `writeback.revoke_below`, and a test fails if the two differ). Write-back
  refuses a candidate below the threshold at staging (`confidence_below_the_revocation_threshold`,
  recorded like any refusal), treats a confidence outside [0, 1] as a defect, and clamps a
  decrement at 0 — a confidence below zero is not a quantity, whatever happens to the item
  afterwards. Migration 0027 removes the optional minus from 0024's decay pattern, which only
  the unclamped decrement could have needed.
  **Consequence: raising the threshold after calibration is a migration, in this order** — first
  revoke, by rule, every live item the new value would strand (recording each as a decay), then
  replace `memory_items_live_confidence` with the new number, and change `revoke_below` in the same
  commit; the test that compares the two fails otherwise. That is the price of making "live" and
  "above the threshold" one fact in the schema: a decrement below the threshold cannot land
  without the revocation in the same statement. Lowering it needs only the constraint and the
  config.

### 6.4 Revocation

`revoked = true` removes an item from all retrieval permanently. Rows are never hard-deleted — the audit trail is part of the contribution. **Stated limitation (2026-10-14):** since migrations 0010 and 0011 no application role can delete or truncate `memory_items`, delete the organisation that would cascade into it, or delete a brand that has memory items (`memory_items.brand_id` is `on delete no action`). What remains is the table owner: nothing in the database binds `postgres` on managed Supabase. The audit trail is tamper-resistant for application code, not tamper-proof. See `Architecture.md` §9, "Stated limitation". A brand manager must be able to revoke from the UI, and the system must record who revoked what and when.

**Revocation by rule — decided 2026-10-19 (project owner), migration 0024.** An item revoked by
§6.3's decay was not revoked by a person, and naming one would be made up. Such a revocation has
`revoked_by` null and a constrained `revoked_reason`:

```
confidence_decay: confidence <before> -> <after> below <threshold>; cause <cause>; run <run id>
```

`<cause>` is `validation_flag:<finding kind>` or `reviewer_edit:<reviewer's user id>`; `<after>`
must equal the row's `confidence`, and be below `<threshold>`. So every decayed item records the
confidence at revocation, what caused the crossing and the run it happened in — why that item
died, answerable later from the row alone. The database enforces it: `revoked_by` is null if and
only if the reason is a well-formed decay record (so a revocation is by rule or by a person, never
both and never unattributed), the confidence of a decayed item is frozen with its revocation, and
a signed-in user cannot write a decay record. `revoked_at` is set by the server for both kinds
(0023). How many items died each way is an evaluation result: `docs/evaluation-plan.md` §4,
"Memory revocations by kind".

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
