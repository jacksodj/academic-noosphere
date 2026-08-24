"""Embedding layer for relevance scoring (768-dim SPECTER2 by default).

Three interchangeable implementations behind the `Embedder` protocol:
`OnnxSpecter2Embedder` (torch-free, consumes the parity-gated fp32 export from
scripts/export_specter2_onnx.py — the packaged-app path, ticket #22),
`Specter2Embedder` (sentence-transformers, optional `embed` extra — the dev
path and parity oracle), and `StubEmbedder` (deterministic fallback for tests
and model-less environments). All heavy imports are lazy.

Vector compatibility is load-bearing: the graph holds torch-produced vectors
and the ONNX export is only shipped when the parity gate passes (min cosine
>= 0.99999 vs the torch oracle), so implementations may be mixed freely.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from pathlib import Path
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

EMBED_DIM = 768
SPECTER2_MODEL = "allenai/specter2_base"


def onnx_model_dir() -> Path:
    from noosphere.config import data_dir

    return data_dir() / "models" / "specter2-onnx"


@runtime_checkable
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]: ...


class Specter2Embedder:
    """SPECTER2 sentence-transformers embedder (requires the `embed` extra).

    sentence_transformers is imported on first `embed()` call, never at module
    import time.
    """

    def __init__(self, model_name: str = SPECTER2_MODEL) -> None:
        self._model_name = model_name
        self._model = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self._model_name)
        return self._model

    def embed(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(texts, normalize_embeddings=True)
        return [[float(x) for x in v] for v in vectors]


class OnnxSpecter2Embedder:
    """SPECTER2 via ONNX Runtime (optional `onnx` extra) — no torch.

    Consumes the artifact directory written by scripts/export_specter2_onnx.py
    (model.onnx + tokenizer.json + recipe.json). The recipe is authoritative
    for truncation length; pooling is mean-over-attention-mask + L2 normalize,
    matching the sentence-transformers oracle.
    """

    def __init__(self, model_dir: Path | None = None) -> None:
        self._dir = model_dir or onnx_model_dir()
        self._session = None
        self._tokenizer = None

    @staticmethod
    def available(model_dir: Path | None = None) -> bool:
        d = model_dir or onnx_model_dir()
        return (d / "model.onnx").is_file() and (d / "tokenizer.json").is_file()

    def _load(self):
        if self._session is None:
            import onnxruntime as ort
            from tokenizers import Tokenizer

            recipe = json.loads((self._dir / "recipe.json").read_text())
            self._tokenizer = Tokenizer.from_file(str(self._dir / "tokenizer.json"))
            self._tokenizer.enable_truncation(max_length=int(recipe["max_seq_length"]))
            self._tokenizer.enable_padding()
            self._session = ort.InferenceSession(
                str(self._dir / "model.onnx"), providers=["CPUExecutionProvider"]
            )
        return self._session, self._tokenizer

    def embed(self, texts: list[str]) -> list[list[float]]:
        import numpy as np

        session, tokenizer = self._load()
        enc = tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        types = np.array([e.type_ids for e in enc], dtype=np.int64)
        hidden = session.run(
            ["last_hidden_state"],
            {"input_ids": ids, "attention_mask": mask, "token_type_ids": types},
        )[0]
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        pooled /= np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)
        return [[float(x) for x in v] for v in pooled]


class StubEmbedder:
    """Deterministic stand-in: sha256(text) seeds a random unit vector."""

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self._dim = dim

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def _vector(self, text: str) -> list[float]:
        seed = int.from_bytes(hashlib.sha256(text.encode("utf-8")).digest()[:8], "big")
        rng = random.Random(seed)
        raw = [rng.gauss(0.0, 1.0) for _ in range(self._dim)]
        norm = math.sqrt(sum(x * x for x in raw)) or 1.0
        return [x / norm for x in raw]


def default_embedder() -> Embedder:
    """Preference order: local ONNX artifact, sentence-transformers, stub."""
    if OnnxSpecter2Embedder.available():
        try:
            import onnxruntime  # noqa: F401
            import tokenizers  # noqa: F401

            logger.info("using ONNX SPECTER2 embedder (%s)", onnx_model_dir())
            return OnnxSpecter2Embedder()
        except ImportError:
            logger.warning(
                "ONNX model present but onnxruntime/tokenizers not installed "
                "(optional `onnx` extra); trying sentence-transformers"
            )
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        logger.warning(
            "no ONNX artifact and sentence-transformers is not installed; "
            "falling back to the deterministic StubEmbedder — relevance scores "
            "will not be semantically meaningful"
        )
        return StubEmbedder()
    return Specter2Embedder()
