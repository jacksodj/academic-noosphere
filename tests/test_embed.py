"""Embedder tests: deterministic stub, lazy SPECTER2 import, default selection."""

from __future__ import annotations

import importlib.util
import logging
import math
import sys

import pytest

from noosphere.pipeline.embed import (
    EMBED_DIM,
    Embedder,
    Specter2Embedder,
    StubEmbedder,
    default_embedder,
)

_HAS_ST = importlib.util.find_spec("sentence_transformers") is not None


def test_stub_is_deterministic_across_instances() -> None:
    a = StubEmbedder().embed(["episodic memory for agents"])
    b = StubEmbedder().embed(["episodic memory for agents"])
    assert a == b


def test_stub_dim_and_unit_norm() -> None:
    [vec] = StubEmbedder().embed(["some text"])
    assert len(vec) == EMBED_DIM
    assert math.isclose(math.sqrt(sum(x * x for x in vec)), 1.0, rel_tol=1e-9)


def test_stub_custom_dim() -> None:
    [vec] = StubEmbedder(dim=16).embed(["some text"])
    assert len(vec) == 16


def test_stub_different_texts_differ() -> None:
    v1, v2 = StubEmbedder().embed(["memory consolidation", "graph databases"])
    assert v1 != v2


def test_stub_batch_order_matches_input() -> None:
    emb = StubEmbedder()
    batch = emb.embed(["a", "b"])
    assert batch == [emb.embed(["a"])[0], emb.embed(["b"])[0]]


def test_embedder_protocol_satisfied() -> None:
    assert isinstance(StubEmbedder(), Embedder)
    assert isinstance(Specter2Embedder(), Embedder)


@pytest.mark.skipif(_HAS_ST, reason="sentence-transformers installed")
def test_specter2_construction_does_not_import_sentence_transformers() -> None:
    Specter2Embedder()
    assert "sentence_transformers" not in sys.modules


@pytest.mark.skipif(_HAS_ST, reason="sentence-transformers installed")
def test_specter2_embed_raises_without_dependency() -> None:
    with pytest.raises(ModuleNotFoundError):
        Specter2Embedder().embed(["text"])


@pytest.mark.skipif(_HAS_ST, reason="sentence-transformers installed")
def test_default_embedder_falls_back_to_stub_with_warning(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    # Isolate from any real ONNX artifact on this machine — this test is about
    # the last-resort tier of the chain (no ONNX, no sentence-transformers).
    monkeypatch.setattr(
        "noosphere.pipeline.embed.onnx_model_dir", lambda: tmp_path / "absent"
    )
    with caplog.at_level(logging.WARNING, logger="noosphere.pipeline.embed"):
        embedder = default_embedder()
    assert isinstance(embedder, StubEmbedder)
    assert any("StubEmbedder" in r.message for r in caplog.records)
