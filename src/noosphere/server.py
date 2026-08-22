"""Localhost API for the Tauri shell / React SPA.

Contract from ticket #3: bind to 127.0.0.1 on a random free port, require a
per-launch bearer token on every request, and print both to stdout as one JSON
line so the spawning shell (Tauri, or a dev browser session) can connect.
"""

import asyncio
import json
import os
import secrets
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from noosphere import __version__
from noosphere.api import AppState, router
from noosphere.config import Settings, data_dir, get_credential
from noosphere.models import RunStatus
from noosphere.pipeline.queue import Checkpoint

_token: str = ""

VENDOR_IDEONOMY = Path(__file__).resolve().parents[2] / "vendor" / "ideonomy"


def _build_handlers(state: AppState) -> dict:
    """Job handlers wiring the queue to the survey/analysis/ideonomy machinery.

    Heavy collaborators are constructed lazily per job so that server startup
    never requires network, AWS credentials, or the embedding model.
    """
    from noosphere.analysis.confirm import confirm_candidate
    from noosphere.analysis.whitespace import detect_whitespace
    from noosphere.ideonomy.expand import expand_gap as run_expand
    from noosphere.llm import LlmClient
    from noosphere.pipeline.embed import default_embedder
    from noosphere.pipeline.survey import SurveyService
    from noosphere.sources.openalex import OpenAlexClient
    from noosphere.sources.ratelimit import RateLimiter

    def note_activity(run_id: str, message: str) -> None:
        # Persisted for history (run detail view) + published live over SSE.
        row = state.sidecar.activity_put(run_id, message)
        state.bus.publish({"type": "activity", **row})

    def note_stage_progress(run_id: str, payload: dict) -> None:
        # Transient sub-stage ticks (embed batch counts + ETA); SSE only.
        state.bus.publish({"type": "stage_progress", "run_id": run_id, **payload})

    def make_service() -> SurveyService:
        openalex = OpenAlexClient(
            state.sidecar,
            api_key=get_credential("openalex_api_key"),
            rate=RateLimiter(10.0),
            mailto=get_credential("crossref_mailto"),
        )
        websearch = None
        if state.settings.web_search_enabled and state.settings.gateway_url:
            from noosphere.sources.websearch import WebSearchClient
            websearch = WebSearchClient(state.settings.gateway_url, state.settings.aws_region)
        return SurveyService(
            state.sidecar, state.graph, openalex, default_embedder(),
            state.settings, websearch=websearch, on_activity=note_activity,
            on_stage_progress=note_stage_progress,
        )

    def llm() -> "LlmClient":
        return LlmClient(
            state.settings.aws_region,
            state.meter,
            haiku_model=state.settings.haiku_model,
            opus_model=state.settings.opus_model,
        )

    def note_missing_credentials(run_id: str) -> None:
        if not get_credential("openalex_api_key") and not get_credential("crossref_mailto"):
            note_activity(
                run_id,
                "Warning: no OpenAlex API key or contact email set — requests run "
                "in the anonymous pool and may be throttled or stalled. Add them "
                "in Settings → API credentials.",
            )

    async def coarse_survey(payload: dict, checkpoint: Checkpoint) -> None:
        run = state.sidecar.get_run(payload["run_id"])
        if run is None:
            raise ValueError(f"unknown run {payload['run_id']}")
        note_missing_credentials(run.run_id)
        await make_service().run_coarse(run, payload["seed_queries"], checkpoint)
        note_activity(run.run_id, "Detecting whitespace (community structure + thin cells)")
        # Pure compute over graph reads (Louvain etc.) — off-loop like persist.
        candidates = await asyncio.to_thread(
            detect_whitespace, run.run_id, state.graph, state.sidecar
        )
        note_activity(run.run_id, f"Whitespace detection complete: {len(candidates)} candidates")
        state.bus.publish({"type": "coarse_completed", "run_id": run.run_id,
                           "whitespace_count": len(candidates)})
        state.bus.publish({"type": "spend", "spend": state.meter.totals()})

    async def zoom_survey(payload: dict, checkpoint: Checkpoint) -> None:
        run = state.sidecar.get_run(payload["run_id"])
        if run is None:
            raise ValueError(f"unknown run {payload['run_id']}")
        note_missing_credentials(run.run_id)
        parent_ws = [w for w in state.sidecar.list_whitespace(payload["parent_run_id"])
                     if w.whitespace_id == payload["whitespace_id"]]
        if not parent_ws:
            raise ValueError(f"unknown whitespace {payload['whitespace_id']}")
        candidate = parent_ws[0]
        await make_service().run_zoom(run, candidate, checkpoint)
        note_activity(run.run_id,
                      f"Confirming candidate — gap synthesis via Bedrock "
                      f"({state.settings.haiku_model} / {state.settings.opus_model})")
        try:
            gap = await confirm_candidate(
                candidate, run.run_id, state.graph, state.sidecar, llm()
            )
        except Exception as exc:
            # The survey half already marked the run completed; without this the
            # failure is invisible (blank gaps, candidate stuck "zooming").
            note_activity(run.run_id, f"Gap confirmation failed: {type(exc).__name__}: {exc}")
            candidate.status = "candidate"
            state.sidecar.put_whitespace(candidate)
            state.sidecar.update_run(run.run_id, status=RunStatus.FAILED)
            raise
        note_activity(
            run.run_id,
            f"Candidate {'confirmed — gap recorded' if gap else 'not confirmed'}"
        )
        state.bus.publish({"type": "zoom_completed", "run_id": run.run_id,
                           "whitespace_id": candidate.whitespace_id,
                           "confirmed": gap is not None,
                           "gap_id": gap.gap_id if gap else None})
        state.bus.publish({"type": "spend", "spend": state.meter.totals()})

    async def redetect_whitespace(payload: dict, checkpoint: Checkpoint) -> None:
        run = state.sidecar.get_run(payload["run_id"])
        if run is None:
            raise ValueError(f"unknown run {payload['run_id']}")
        note_activity(run.run_id,
                      "Re-detecting whitespace (adaptive community resolution)")
        existing = state.sidecar.list_whitespace(run.run_id)
        kept = [w for w in existing if w.status != "candidate"]
        stale = [w for w in existing if w.status == "candidate"]
        next_index = 1 + max(
            (int(w.whitespace_id.rsplit("ws", 1)[1]) for w in existing), default=-1
        )
        fresh = await asyncio.to_thread(
            detect_whitespace, run.run_id, state.graph, state.sidecar,
            start_index=next_index,
        )
        # Drop re-detections of thin cells that were already zoomed (same
        # topic); community numbering isn't stable across partitions, so
        # bridges are always kept as new candidates.
        zoomed_topics = {w.topic_id for w in kept if w.topic_id}
        dropped = 0
        for w in fresh:
            if w.kind == "thin_cell" and w.topic_id in zoomed_topics:
                state.sidecar.delete_whitespace(w.whitespace_id)
                dropped += 1
        for w in stale:
            state.sidecar.delete_whitespace(w.whitespace_id)
        note_activity(
            run.run_id,
            f"Re-detection complete: {len(fresh) - dropped} new candidates "
            f"({len(kept)} zoomed kept, {len(stale)} stale replaced)",
        )
        state.bus.publish({"type": "whitespace_updated", "run_id": run.run_id})

    async def expand_gap(payload: dict, checkpoint: Checkpoint) -> None:
        gaps = [g for g in state.sidecar.list_gaps() if g.gap_id == payload["gap_id"]]
        if not gaps:
            raise ValueError(f"unknown gap {payload['gap_id']}")
        gap = gaps[0]
        note_activity(gap.zoom_run_id,
                      f"Ideonomy Expansion attempt {payload['attempt']} started (Opus)")
        try:
            expansion = await run_expand(
                gap, gap.zoom_run_id, payload["attempt"], state.graph, llm(), VENDOR_IDEONOMY,
            )
        except Exception as exc:
            note_activity(gap.zoom_run_id,
                          f"Ideonomy Expansion failed: {type(exc).__name__}: {exc}")
            state.bus.publish({"type": "expansion_failed", "gap_id": gap.gap_id,
                               "attempt": payload["attempt"], "error": str(exc)})
            raise
        state.sidecar.put_expansion(expansion)
        note_activity(gap.zoom_run_id,
                      f"Ideonomy Expansion attempt {expansion.attempt} ready "
                      f"({len(expansion.ideas)} ideas)")
        state.bus.publish({"type": "expansion_ready", "gap_id": gap.gap_id,
                           "attempt": expansion.attempt})
        state.bus.publish({"type": "spend", "spend": state.meter.totals()})

    return {"coarse_survey": coarse_survey, "zoom_survey": zoom_survey,
            "expand_gap": expand_gap, "redetect_whitespace": redetect_whitespace}


async def _supervised_worker(state: "AppState") -> None:
    """Run the job worker forever, restarting it if it dies.

    An exception escaping the worker task would otherwise be swallowed until
    shutdown, leaving a healthy-looking API with a permanently dead queue
    (jobs stuck in "running", no recovery, no visible error).
    """
    import traceback

    while True:
        try:
            await state.queue.worker(_build_handlers(state))
        except asyncio.CancelledError:
            raise
        except Exception:
            print("job worker crashed; restarting in 5s", file=sys.stderr)
            traceback.print_exc()
            await asyncio.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = AppState.build(data_dir(), Settings.load())
    app.state.noosphere = state
    worker = asyncio.create_task(_supervised_worker(state))
    try:
        yield
    finally:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass
        state.close()


app = FastAPI(title="noosphere-core", version=__version__, lifespan=lifespan)
app.include_router(router)


@app.middleware("http")
async def require_token(request: Request, call_next):
    # EventSource can't set headers, so SSE clients pass ?token= instead.
    presented = request.headers.get("authorization", "")
    if not presented and (qt := request.query_params.get("token")):
        presented = f"Bearer {qt}"
    if not secrets.compare_digest(presented, f"Bearer {_token}"):
        return JSONResponse({"detail": "missing or invalid token"}, status_code=401)
    return await call_next(request)


# The SPA is cross-origin to this server both in dev (vite on localhost:<port>)
# and in the Tauri shell (tauri://localhost / https://tauri.localhost on macOS).
# Added after require_token so CORSMiddleware wraps outermost and answers
# OPTIONS preflights before the token check can 401 them.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=(
        r"^(https?://(localhost|127\.0\.0\.1)(:\d+)?"
        r"|tauri://localhost|https://tauri\.localhost)$"
    ),
    allow_methods=["*"],
    allow_headers=["authorization", "content-type"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    global _token
    _token = secrets.token_urlsafe(32)
    port = _free_port()
    handshake = json.dumps({"port": port, "token": _token, "pid": os.getpid()})
    # Handshake line for the spawning shell; single line, JSON, stdout.
    print(handshake, flush=True)
    # Also drop it (0600) in the data dir so local tooling — debugging, health
    # checks, CLIs — can reach this instance without restarting it. Localhost
    # bind + per-launch token; same-user readable only.
    # Atomic write (tmp + rename): watchers poll for this file appearing and
    # must never observe it created-but-empty.
    hs_path = data_dir() / "handshake.json"
    tmp_path = hs_path.with_suffix(".json.tmp")
    fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(handshake + "\n")
    os.replace(tmp_path, hs_path)
    try:
        uvicorn.run(
            app, host="127.0.0.1", port=port, access_log=False,
            # SIGTERM must finish promptly even with SSE streams open, so the
            # lifespan shutdown (DB close -> WAL checkpoint) actually runs.
            timeout_graceful_shutdown=5,
        )
    finally:
        hs_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
