"""The local encoder's configuration and load-time checks (app/memory/embeddings.py; Memory.md §5).

No model is loaded here: sentence-transformers is an optional dependency CI does not install. The
checks are exercised with stand-ins for the download and the model; the real model is checked by
scripts/check_encoder.py on the laptop.
"""

import hashlib
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.memory.embeddings import (
    EmbeddingConfig,
    EncoderRefused,
    HashingEncoder,
    LocalEncoder,
    load_embedding_config,
    sha256_file,
)

CFG = load_embedding_config()


class Normalize:  # named like sentence-transformers' module; only the name is checked
    pass


class Pooling:
    pass


class StubModel:
    """Stands in for a SentenceTransformer: a module list, a dimension, and encode()."""

    def __init__(self, modules: Sequence[object], dimension: int = 768, unit: bool = True) -> None:
        self._modules = list(modules)
        self._dimension = dimension
        self._unit = unit
        self.seen: list[str] = []

    def __iter__(self) -> Any:
        return iter(self._modules)

    def get_embedding_dimension(self) -> int:
        return self._dimension

    def encode(self, texts: list[str], **_: object) -> Any:
        self.seen += texts
        value = 1.0 if self._unit else 2.0
        rows = [[value] + [0.0] * (self._dimension - 1) for _ in texts]

        class Array(list):  # what .tolist() is called on
            def tolist(self) -> list[list[float]]:
                return list(self)

        return Array(rows)


def test_the_configured_encoder_is_pinned_to_a_commit_with_a_weights_hash() -> None:
    assert CFG.provider == "local" and CFG.normalised is True
    assert len(CFG.revision) == 40 and len(CFG.weights_sha256) == 64
    assert CFG.dimensions == 768
    assert CFG.query_instruction.endswith(": ")


def test_a_branch_name_is_not_a_revision() -> None:
    with pytest.raises(ValidationError):
        EmbeddingConfig.model_validate(CFG.model_dump() | {"revision": "main"})


def test_queries_get_the_instruction_and_documents_do_not() -> None:
    model = StubModel([Pooling(), Normalize()])
    encoder = LocalEncoder(model, CFG)
    encoder.embed_documents(["IPX7 keeps the rain out"])
    encoder.embed_queries(["what should a monsoon campaign lead with"])
    assert model.seen == [
        "IPX7 keeps the rain out",
        CFG.query_instruction + "what should a monsoon campaign lead with",
    ]
    assert (encoder.model, encoder.version) == (CFG.id, CFG.revision)


def test_a_pipeline_without_a_final_normalize_is_refused() -> None:
    with pytest.raises(EncoderRefused, match="Normalize"):
        LocalEncoder(StubModel([Normalize(), Pooling()]), CFG)


def test_a_dimension_other_than_the_configured_one_is_refused() -> None:
    with pytest.raises(EncoderRefused, match="dimension 384"):
        LocalEncoder(StubModel([Pooling(), Normalize()], dimension=384), CFG)


def test_a_vector_that_is_not_unit_length_is_refused() -> None:
    encoder = LocalEncoder(StubModel([Pooling(), Normalize()], unit=False), CFG)
    with pytest.raises(EncoderRefused, match="unit length"):
        encoder.embed_documents(["x"])


def _weights(tmp_path: Path, content: bytes) -> tuple[Path, EmbeddingConfig]:
    path = tmp_path / "model.safetensors"
    path.write_bytes(content)
    cfg = CFG.model_copy(
        update={
            "weights_bytes": len(content),
            "weights_sha256": hashlib.sha256(content).hexdigest(),
        }
    )
    return path, cfg


def test_load_checks_the_weights_before_constructing_the_model(tmp_path: Path) -> None:
    path, cfg = _weights(tmp_path, b"\x00weights\r\n\x01")  # binary: hashed as stored
    calls: dict[str, object] = {}

    def download(**kw: object) -> str:
        calls["download"] = kw
        return str(path)

    def construct(name: str, **kw: object) -> StubModel:
        calls["construct"] = (name, kw)
        return StubModel([Pooling(), Normalize()])

    LocalEncoder.load(cfg, download=download, construct=construct)
    assert calls["download"] == {
        "repo_id": cfg.id, "filename": cfg.weights_file, "revision": cfg.revision
    }  # fmt: skip
    assert calls["construct"] == (cfg.id, {"revision": cfg.revision, "device": "cpu"})


def test_tampered_weights_are_refused_and_the_model_is_never_constructed(tmp_path: Path) -> None:
    path, cfg = _weights(tmp_path, b"original")
    path.write_bytes(b"tampered")  # same size, different bytes

    def construct(*_: object, **__: object) -> StubModel:
        raise AssertionError("constructed despite a bad hash")

    with pytest.raises(EncoderRefused, match="sha256"):
        LocalEncoder.load(cfg, download=lambda **_: str(path), construct=construct)
    path.write_bytes(b"longer than before")
    with pytest.raises(EncoderRefused, match="bytes"):
        LocalEncoder.load(cfg, download=lambda **_: str(path), construct=construct)


def test_sha256_file_hashes_raw_bytes(tmp_path: Path) -> None:
    path = tmp_path / "f.bin"
    path.write_bytes(b"a\r\nb")
    assert sha256_file(path) == hashlib.sha256(b"a\r\nb").hexdigest()


def test_the_hashing_stand_in_is_deterministic_unit_length_and_768_wide() -> None:
    enc = HashingEncoder()
    a, b = enc.embed_documents(["waterproof monsoon earphones", "waterproof monsoon earphones"])
    assert a == b and len(a) == 768
    assert math.isclose(math.sqrt(sum(x * x for x in a)), 1.0)
    assert math.isclose(math.sqrt(sum(x * x for x in enc.embed_queries([""])[0])), 1.0)
