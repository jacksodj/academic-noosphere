"""Embedding layer for relevance scoring (768-dim SPECTER2 by default).

`Specter2Embedder` wraps sentence-transformers (optional `embed` extra) and is
lazy-imported so the core package works without it. `StubEmbedder` is a
deterministic hash-seeded fallback used in tests and in environments without
the model.
"""

from __future__ import annotations

import hashlib
import logging
import math
import random
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)

EMBED_DIM = 768
SPECTER2_MODEL = "allenai/specter2_base"


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
    """SPECTER2 when sentence-transformers is importable, else the stub."""
    try:
        import sentence_transformers  # noqa: F401
    except ImportError:
        logger.warning(
            "sentence-transformers is not installed (optional `embed` extra); "
            "falling back to the deterministic StubEmbedder — relevance scores "
            "will not be semantically meaningful"
        )
        return StubEmbedder()
    return Specter2Embedder()
