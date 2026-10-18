"""Load the pinned local encoder and print what its load-time checks found (Memory.md §5).

    uv sync --group embeddings
    uv run --group embeddings python -m scripts.check_encoder

Run on the laptop, not in CI (CI does not install sentence-transformers). The first run downloads
the pinned weights into the Hugging Face cache. Loading performs the checks in
app/memory/embeddings.py — weights size and SHA-256 at the pinned revision, a final Normalize
module, the configured dimension — and refuses on any failure, so reaching the report means they
passed. The two similarities printed are a smoke test that queries and documents are embedded into
one space, not a measurement of retrieval quality.
"""

import math
import sys
import time

from app.memory.embeddings import default_encoder, load_embedding_config

QUERY = "What should a monsoon earphone campaign lead with?"
DOCUMENTS = (
    "IPX7 rating keeps the rain out, so the earphones survive a downpour.",
    "Pre-seasoned cast iron needs oiling after every wash.",
)


def main() -> int:
    cfg = load_embedding_config()
    started = time.perf_counter()
    encoder = default_encoder()
    loaded = time.perf_counter() - started
    print(f"model {encoder.model} @ {encoder.version}")
    print(
        f"weights {cfg.weights_file}: {cfg.weights_bytes} bytes, sha256 {cfg.weights_sha256} (ok)"
    )
    print(
        f"pipeline ends in Normalize (ok); dimension {encoder.dimensions} (ok); load {loaded:.1f}s"
    )

    started = time.perf_counter()
    docs = encoder.embed_documents(DOCUMENTS)
    (query,) = encoder.embed_queries([QUERY])
    took = time.perf_counter() - started
    for name, v in [("query", query), *((f"doc {i}", d) for i, d in enumerate(docs))]:
        print(f"{name}: {len(v)} dims, norm {math.sqrt(sum(x * x for x in v)):.6f}")
    for text, d in zip(DOCUMENTS, docs, strict=True):
        cosine = sum(a * b for a, b in zip(query, d, strict=True))
        print(f"cosine(query, {text[:40]!r}...) = {cosine:.4f}")
    print(f"embedded 3 texts in {took:.2f}s on CPU")
    return 0


if __name__ == "__main__":
    sys.exit(main())
