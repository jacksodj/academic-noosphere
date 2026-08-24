/**
 * Embedding-model panel (shared by Onboarding and Settings): shows whether
 * real SPECTER2 embeddings are available and drives the one-time ~420 MB
 * download from Hugging Face. Without the model the core falls back to a
 * stub embedder — fine for browsing, useless for survey relevance.
 */

import { useEffect, useRef, useState } from "react";
import { getEmbeddingModel, startEmbeddingModelDownload } from "./endpoints";
import type { EmbeddingModelStatus } from "./types";

function mb(bytes: number): string {
  return `${Math.round(bytes / 1048576)} MB`;
}

export function EmbeddingModelPanel() {
  const [status, setStatus] = useState<EmbeddingModelStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<number | null>(null);

  function refresh() {
    getEmbeddingModel()
      .then(setStatus)
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }

  useEffect(() => {
    refresh();
    return () => {
      if (timer.current !== null) window.clearTimeout(timer.current);
    };
  }, []);

  // Poll while a download is in flight.
  useEffect(() => {
    if (status?.download.status !== "downloading") return;
    timer.current = window.setTimeout(refresh, 2000);
  }, [status]);

  if (error) return <p className="error">{error}</p>;
  if (!status) return <p className="muted">Checking embedding model…</p>;

  const dl = status.download;
  if (status.present) {
    return (
      <p className="model-status ok">
        ✓ SPECTER2 embedding model installed ({status.embedder === "onnx" ? "ONNX" : status.embedder}
        ) — surveys use real relevance scoring.
      </p>
    );
  }
  if (dl.status === "downloading") {
    const pct = dl.total_bytes > 0 ? Math.round((100 * dl.done_bytes) / dl.total_bytes) : 0;
    return (
      <div className="model-status">
        <p className="muted">
          Downloading SPECTER2 from Hugging Face… {mb(dl.done_bytes)}
          {dl.total_bytes > 0 && ` of ${mb(dl.total_bytes)}`}
        </p>
        <div className="insight-bar">
          <span className="insight-bar-fill" style={{ width: `${pct}%` }} />
          <span className="insight-bar-value mono">{pct}%</span>
        </div>
      </div>
    );
  }
  return (
    <div className="model-status">
      <p className="muted">
        {status.embedder === "stub"
          ? "No embedding model installed — surveys would fall back to a stub embedder with meaningless relevance scores."
          : "Using the sentence-transformers dev path; the ONNX model removes that dependency."}
      </p>
      {dl.status === "failed" && <p className="error">Download failed: {dl.error}</p>}
      <button
        type="button"
        className="primary"
        onClick={() => {
          startEmbeddingModelDownload().then(setStatus).catch((e: unknown) =>
            setError(e instanceof Error ? e.message : String(e)),
          );
        }}
      >
        {dl.status === "failed" ? "Retry download" : "Download model (~420 MB)"}
      </button>
      <p className="muted small">
        One-time, from <span className="mono">{status.hf_repo}</span>; checksum-verified, stored
        in Application Support.
      </p>
    </div>
  );
}
