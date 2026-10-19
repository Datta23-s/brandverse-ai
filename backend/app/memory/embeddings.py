"""Text embeddings for memory (Memory.md §5): a pinned local encoder, never a hosted API.

Decision 2026-10-18 (project owner): a sentence-transformer at a pinned Hugging Face revision, run
on the laptop's CPU. No API, no `llm_calls` row, no quota, no cost. The model id, revision,
weights hash, dimension and query instruction live in config/models.yaml `embedding:` (Rules.md
P5: model ids appear nowhere else in code).

Loading refuses, rather than warns, unless:
1. the weights file at the pinned revision has the recorded size and SHA-256;
2. the model's pipeline ends in a `Normalize` module, and every batch it returns is unit length
   (so cosine distance is exact);
3. its output dimension equals the configured one — which a database test asserts equals
   `memory_items.embedding` and `brands.voice_embedding` (migrations 0004, 0006).

Queries get BAAI's instruction prefix; stored items do not. `Encoder` exposes the two as separate
methods, so a caller cannot embed a query as a document or the reverse.

sentence-transformers is in the optional `embeddings` dependency group (approved by the project
owner 2026-10-18): CI never installs it and uses `HashingEncoder`, a deterministic stand-in with the
same shape and none of the meaning.
"""

import hashlib
import math
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import REPO_ROOT

MODELS_PATH: Path = REPO_ROOT / "config" / "models.yaml"
_UNIT_TOLERANCE = 1e-3


class EmbeddingConfig(BaseModel):
    """config/models.yaml `embedding:`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["local"]
    library: Literal["sentence-transformers"]
    id: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")  # a commit, never a branch name
    weights_file: str = Field(min_length=1)
    weights_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    weights_bytes: int = Field(gt=0)
    dimensions: int = Field(gt=0)
    normalised: Literal[True]
    query_instruction: str = Field(min_length=1)
    max_tokens: int = Field(gt=0)
    licence: str = Field(min_length=1)


def load_embedding_config(path: Path = MODELS_PATH) -> EmbeddingConfig:
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    return EmbeddingConfig.model_validate(document["embedding"])


class Encoder(Protocol):
    """What memory and the relevance pool need from an encoder."""

    model: str  # recorded in memory_items.embedding_model
    version: str  # recorded in memory_items.embedding_version
    dimensions: int

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...


class EncoderRefused(RuntimeError):
    """The encoder failed a load-time or output check; nothing is embedded."""


def sha256_file(path: Path) -> str:
    """SHA-256 of the file's bytes, as stored (a binary file: no newline normalising)."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_weights(path: Path, cfg: EmbeddingConfig) -> None:
    size = path.stat().st_size
    if size != cfg.weights_bytes:
        raise EncoderRefused(f"{cfg.weights_file}: {size} bytes, expected {cfg.weights_bytes}")
    actual = sha256_file(path)
    if actual != cfg.weights_sha256:
        raise EncoderRefused(f"{cfg.weights_file}: sha256 {actual}, expected {cfg.weights_sha256}")


class LocalEncoder:
    """The pinned sentence-transformer, after its load-time checks."""

    def __init__(self, st_model: Any, cfg: EmbeddingConfig) -> None:
        modules = list(st_model)
        if not modules or type(modules[-1]).__name__ != "Normalize":
            raise EncoderRefused(f"{cfg.id}: the pipeline does not end in a Normalize module")
        dimension = st_model.get_embedding_dimension()
        if dimension != cfg.dimensions:
            raise EncoderRefused(f"{cfg.id}: output dimension {dimension}, config {cfg.dimensions}")
        self._st = st_model
        self._instruction = cfg.query_instruction
        self.model = cfg.id
        self.version = cfg.revision
        self.dimensions = cfg.dimensions

    @classmethod
    def load(
        cls,
        cfg: EmbeddingConfig,
        download: Callable[..., str] | None = None,
        construct: Callable[..., Any] | None = None,
    ) -> "LocalEncoder":
        """Fetch (or find in the Hugging Face cache) the pinned weights, check them, then load."""
        if download is None:
            from huggingface_hub import hf_hub_download

            download = hf_hub_download
        if construct is None:
            from sentence_transformers import SentenceTransformer

            construct = SentenceTransformer
        weights = Path(download(repo_id=cfg.id, filename=cfg.weights_file, revision=cfg.revision))
        verify_weights(weights, cfg)
        return cls(construct(cfg.id, revision=cfg.revision, device="cpu"), cfg)

    def _encode(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = self._st.encode(
            list(texts), batch_size=32, show_progress_bar=False, convert_to_numpy=True
        ).tolist()
        for v in vectors:
            if abs(math.sqrt(sum(x * x for x in v)) - 1.0) > _UNIT_TOLERANCE:
                raise EncoderRefused(f"{self.model}: returned a vector that is not unit length")
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return self._encode([self._instruction + t for t in texts])

    def count_tokens(self, text: str) -> int:
        """Tokens the model's own tokenizer makes of `text` as a document (no special tokens)."""
        return len(self._st.tokenizer(text, add_special_tokens=False)["input_ids"])


def default_encoder() -> LocalEncoder:
    """The configured local encoder (`--encoder app.memory.embeddings:default_encoder`)."""
    return LocalEncoder.load(load_embedding_config())


_TOKEN = re.compile(r"\w+")


class HashingEncoder:
    """A deterministic stand-in for tests and CI: a hashed bag of words, unit length. It shares
    the real encoder's shape, not its meaning — overlapping words, not similar ideas, score high."""

    model = "hashing-test-encoder"
    version = "1"

    def __init__(self, dimensions: int = 768) -> None:
        self.dimensions = dimensions

    def _vector(self, text: str) -> list[float]:
        v = [0.0] * self.dimensions
        for token in _TOKEN.findall(text.casefold()):
            h = hashlib.sha256(token.encode()).digest()
            v[int.from_bytes(h[:4], "big") % self.dimensions] += 1.0 if h[4] % 2 else -1.0
        norm = math.sqrt(sum(x * x for x in v))
        if norm == 0:
            v[0], norm = 1.0, 1.0  # an empty text still gets a unit vector
        return [x / norm for x in v]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def count_tokens(self, text: str) -> int:
        return len(_TOKEN.findall(text))
