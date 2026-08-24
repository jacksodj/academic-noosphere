"""OnnxSpecter2Embedder: artifact detection, fallback order, and (when the
exported model is present locally) real-inference invariants.

The full torch-vs-ONNX parity gate lives in scripts/export_specter2_onnx.py and
runs at export time; these tests stay light enough for every suite run.
"""

import math

import pytest

from noosphere.pipeline.embed import (
    EMBED_DIM,
    OnnxSpecter2Embedder,
    StubEmbedder,
    default_embedder,
    onnx_model_dir,
)


def test_available_false_for_empty_dir(tmp_path):
    assert OnnxSpecter2Embedder.available(tmp_path) is False


def test_available_requires_both_files(tmp_path):
    (tmp_path / "model.onnx").write_bytes(b"x")
    assert OnnxSpecter2Embedder.available(tmp_path) is False
    (tmp_path / "tokenizer.json").write_text("{}")
    assert OnnxSpecter2Embedder.available(tmp_path) is True


def test_default_embedder_skips_onnx_without_artifact(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "noosphere.pipeline.embed.onnx_model_dir", lambda: tmp_path / "nope"
    )
    emb = default_embedder()
    assert not isinstance(emb, OnnxSpecter2Embedder)


_HAVE_ARTIFACT = OnnxSpecter2Embedder.available()
_HAVE_RUNTIME = True
try:
    import onnxruntime  # noqa: F401
    import tokenizers  # noqa: F401
except ImportError:
    _HAVE_RUNTIME = False

needs_onnx = pytest.mark.skipif(
    not (_HAVE_ARTIFACT and _HAVE_RUNTIME),
    reason="exported ONNX artifact or onnx extra not present",
)


@needs_onnx
def test_onnx_embed_shape_norm_determinism():
    emb = OnnxSpecter2Embedder()
    texts = ["Attention Is All You Need", "hippocampal replay during sleep", "x"]
    a = emb.embed(texts)
    b = emb.embed(texts)
    assert len(a) == 3 and all(len(v) == EMBED_DIM for v in a)
    for v in a:
        assert math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-4)
    assert a == b  # deterministic
    # distinct inputs must not collapse to one vector
    cos = sum(x * y for x, y in zip(a[0], a[1]))
    assert cos < 0.999


@needs_onnx
def test_default_embedder_prefers_onnx():
    assert isinstance(default_embedder(), OnnxSpecter2Embedder)


def test_stub_still_unit_norm():
    v = StubEmbedder().embed(["anything"])[0]
    assert math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-6)
    assert onnx_model_dir().name == "specter2-onnx"
