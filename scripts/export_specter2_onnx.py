# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "sentence-transformers>=3.0",
#   "torch>=2.2",
#   "onnx>=1.16",
#   "onnxscript>=0.1",
#   "onnxruntime>=1.19",
#   "tokenizers>=0.20",
#   "numpy>=1.26",
#   "httpx>=0.27",
# ]
# ///
"""Export SPECTER2 (allenai/specter2_base) to fp32 ONNX and run the parity gate.

Ticket #22 (self-contained .app). The torch stack stays the dev-side oracle;
this produces the artifact the packaged app's OnnxSpecter2Embedder consumes.
Owner constraint: the existing corpus embeddings are never rebuilt — parity is
an ACCEPTANCE gate. If it fails, the export is not shipped.

  uv run scripts/export_specter2_onnx.py [--out DIR]

Writes to DIR (default ~/Library/Application Support/academic-noosphere/models/
specter2-onnx): model.onnx, tokenizer.json, recipe.json (pooling + truncation +
sha256), parity-report.json. Exit 0 only if the gate passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

PASS_MIN_COSINE = 0.99999

SAMPLE_TEXTS = [
    "Attention Is All You Need",
    "We introduce a new architecture for sequence transduction based solely on attention.",
    "Episodic memory consolidation during sleep supports hippocampal replay in rodents.",
    "A survey of retrieval-augmented generation for large language model agents.",
    "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
    "Working memory capacity predicts fluid intelligence across the adult lifespan.",
    "Graph neural networks for citation network analysis: methods and benchmarks.",
    "短期記憶と長期記憶の神経基盤",  # unicode / non-Latin
    "x",  # degenerate short input
    ("Transformer-based agents with external memory stores " * 40).strip(),  # >512 tokens
]


def fetch_openalex_abstracts(n: int = 100) -> list[str]:
    """Best-effort sample of real abstracts (identifier-safe: nothing persisted)."""
    import httpx

    try:
        resp = httpx.get(
            "https://api.openalex.org/works",
            params={"filter": "has_abstract:true", "per-page": n, "sample": n, "seed": 42},
            timeout=30.0,
        )
        resp.raise_for_status()
        texts = []
        for work in resp.json().get("results", []):
            inv = work.get("abstract_inverted_index") or {}
            slots: dict[int, str] = {}
            for token, positions in inv.items():
                for p in positions:
                    slots[p] = token
            if slots:
                texts.append(" ".join(slots[i] for i in sorted(slots)))
        return texts
    except Exception as e:  # network optional; bundled samples still gate
        print(f"OpenAlex sample unavailable ({e}); using bundled texts only")
        return []


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out",
        type=Path,
        default=Path.home()
        / "Library/Application Support/academic-noosphere/models/specter2-onnx",
    )
    args = parser.parse_args()
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=True)

    import numpy as np
    import torch
    from sentence_transformers import SentenceTransformer

    print("loading torch oracle (exactly as the app's Specter2Embedder does)…")
    st = SentenceTransformer("allenai/specter2_base")
    transformer = st[0].auto_model.eval()
    tokenizer = st.tokenizer
    max_seq = int(st.max_seq_length)
    pooling_cfg = st[1].get_config_dict()
    # Two config shapes across sentence-transformers versions: a single
    # "pooling_mode": "mean" string, or legacy pooling_mode_*_tokens booleans.
    mode = pooling_cfg.get("pooling_mode")
    is_mean = (
        mode == "mean"
        if mode
        else pooling_cfg.get("pooling_mode_mean_tokens") is True
    )
    print(f"max_seq_length={max_seq} pooling_cfg={pooling_cfg}")
    if not is_mean:
        print("FATAL: expected mean-token pooling; recipe assumption broken")
        return 1

    class LastHidden(torch.nn.Module):
        def __init__(self, inner):
            super().__init__()
            self.inner = inner

        def forward(self, input_ids, attention_mask, token_type_ids):
            return self.inner(
                input_ids=input_ids,
                attention_mask=attention_mask,
                token_type_ids=token_type_ids,
                return_dict=False,
            )[0]

    print("exporting fp32 ONNX (opset 17)…")
    # Trace on a CPU copy; the oracle keeps its own device (MPS on this Mac —
    # the same execution path that produced the corpus vectors being protected).
    import copy

    export_model = copy.deepcopy(transformer).to("cpu").eval()
    sample = tokenizer(["export tracer text"], return_tensors="pt")
    dyn = {0: "batch", 1: "seq"}
    torch.onnx.export(
        LastHidden(export_model),
        (sample["input_ids"], sample["attention_mask"], sample["token_type_ids"]),
        str(out / "model.onnx"),
        input_names=["input_ids", "attention_mask", "token_type_ids"],
        output_names=["last_hidden_state"],
        dynamic_axes={
            "input_ids": dyn,
            "attention_mask": dyn,
            "token_type_ids": dyn,
            "last_hidden_state": dyn,
        },
        opset_version=17,
        dynamo=False,  # legacy exporter: proven with BERT-family graphs
    )
    tokenizer.save_pretrained(out)  # tokenizer.json + vocab for the fast tokenizer

    sha = hashlib.sha256((out / "model.onnx").read_bytes()).hexdigest()
    recipe = {
        "source_model": "allenai/specter2_base",
        "format": "onnx-fp32-opset17",
        "max_seq_length": max_seq,
        "pooling": "mean_tokens_over_attention_mask",
        "normalize": True,
        "embed_dim": 768,
        "model_sha256": sha,
    }
    (out / "recipe.json").write_text(json.dumps(recipe, indent=2))

    # ---- parity gate: ONNX-side embed implemented independently (numpy only),
    # mirroring what OnnxSpecter2Embedder in noosphere.pipeline.embed does.
    import onnxruntime as ort
    from tokenizers import Tokenizer

    tok = Tokenizer.from_file(str(out / "tokenizer.json"))
    tok.enable_truncation(max_length=max_seq)
    tok.enable_padding()
    sess = ort.InferenceSession(str(out / "model.onnx"), providers=["CPUExecutionProvider"])

    def onnx_embed(texts: list[str]) -> np.ndarray:
        enc = tok.encode_batch(texts)
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        types = np.array([e.type_ids for e in enc], dtype=np.int64)
        hidden = sess.run(
            ["last_hidden_state"],
            {"input_ids": ids, "attention_mask": mask, "token_type_ids": types},
        )[0]
        m = mask[..., None].astype(np.float32)
        pooled = (hidden * m).sum(axis=1) / np.clip(m.sum(axis=1), 1e-9, None)
        return pooled / np.clip(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12, None)

    texts = SAMPLE_TEXTS + fetch_openalex_abstracts()
    print(f"parity gate over {len(texts)} texts…")
    oracle = np.asarray(st.encode(texts, normalize_embeddings=True), dtype=np.float32)
    candidate = np.zeros_like(oracle)
    for i in range(0, len(texts), 16):  # small batches: padding length varies like real use
        candidate[i : i + 16] = onnx_embed(texts[i : i + 16])

    cosines = (oracle * candidate).sum(axis=1)
    report = {
        "n_texts": len(texts),
        "min_cosine": float(cosines.min()),
        "mean_cosine": float(cosines.mean()),
        "max_abs_component_diff": float(np.abs(oracle - candidate).max()),
        "threshold_min_cosine": PASS_MIN_COSINE,
        "passed": bool(cosines.min() >= PASS_MIN_COSINE),
    }
    (out / "parity-report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if not report["passed"]:
        print("PARITY GATE FAILED — do not ship this export (owner rule: never re-embed)")
        return 1
    print(f"parity gate PASSED — artifact ready in {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
