"""Localhost API for the Tauri shell / React SPA.

Contract from ticket #3: bind to 127.0.0.1 on a random free port, require a
per-launch bearer token on every request, and print both to stdout as one JSON
line so the spawning shell (Tauri, or a dev browser session) can connect.
"""

import asyncio
import json
import secrets
import socket
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from noosphere import __version__
from noosphere.api import AppState, router
from noosphere.config import Settings, data_dir, get_credential
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
            state.settings, websearch=websearch,
        )

    def llm() -> "LlmClient":
        return LlmClient(state.settings.aws_region, state.meter)

    async def coarse_survey(payload: dict, checkpoint: Checkpoint) -> None:
        run = state.sidecar.get_run(payload["run_id"])
        if run is None:
            raise ValueError(f"unknown run {payload['run_id']}")
        await make_service().run_coarse(run, payload["seed_queries"], checkpoint)
        candidates = detect_whitespace(run.run_id, state.graph, state.sidecar)
        state.bus.publish({"type": "coarse_completed", "run_id": run.run_id,
                           "whitespace_count": len(candidates)})
        state.bus.publish({"type": "spend", "spend": state.meter.totals()})

    async def zoom_survey(payload: dict, checkpoint: Checkpoint) -> None:
        run = state.sidecar.get_run(payload["run_id"])
        if run is None:
            raise ValueError(f"unknown run {payload['run_id']}")
        parent_ws = [w for w in state.sidecar.list_whitespace(payload["parent_run_id"])
                     if w.whitespace_id == payload["whitespace_id"]]
        if not parent_ws:
            raise ValueError(f"unknown whitespace {payload['whitespace_id']}")
        candidate = parent_ws[0]
        await make_service().run_zoom(run, candidate, checkpoint)
        gap = await confirm_candidate(candidate, run.run_id, state.graph, state.sidecar, llm())
        state.bus.publish({"type": "zoom_completed", "run_id": run.run_id,
                           "whitespace_id": candidate.whitespace_id,
                           "confirmed": gap is not None,
                           "gap_id": gap.gap_id if gap else None})
        state.bus.publish({"type": "spend", "spend": state.meter.totals()})

    async def expand_gap(payload: dict, checkpoint: Checkpoint) -> None:
        gaps = [g for g in state.sidecar.list_gaps() if g.gap_id == payload["gap_id"]]
        if not gaps:
            raise ValueError(f"unknown gap {payload['gap_id']}")
        gap = gaps[0]
        expansion = await run_expand(
            gap, gap.zoom_run_id, payload["attempt"], state.graph, llm(), VENDOR_IDEONOMY,
        )
        state.sidecar.put_expansion(expansion)
        state.bus.publish({"type": "expansion_ready", "gap_id": gap.gap_id,
                           "attempt": expansion.attempt})
        state.bus.publish({"type": "spend", "spend": state.meter.totals()})

    return {"coarse_survey": coarse_survey, "zoom_survey": zoom_survey,
            "expand_gap": expand_gap}


@asynccontextmanager
async def lifespan(app: FastAPI):
    state = AppState.build(data_dir(), Settings.load())
    app.state.noosphere = state
    worker = asyncio.create_task(state.queue.worker(_build_handlers(state)))
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
    # Handshake line for the spawning shell; single line, JSON, stdout.
    print(json.dumps({"port": port, "token": _token}), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False)


if __name__ == "__main__":
    main()
