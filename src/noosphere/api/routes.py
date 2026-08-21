"""/api routes for the SPA (app/src/endpoints.ts is the client-side mirror).

Integrator contract: ``app.include_router(router)`` — the prefix is already
``/api`` — and set ``app.state.noosphere = AppState.build(...)`` before serving.
Survey/zoom/expand handlers are NOT registered here; the integrator passes
them to ``state.queue.worker({"coarse_survey": ..., "zoom_survey": ...,
"expand_gap": ...})`` at startup. This module only enqueues those job kinds.

Report endpoints lazy-import ``noosphere.reports.gaps`` (built in parallel)
and answer 501 until it exists; expected interface:
``report_json(sidecar, run_id) -> dict`` and
``report_markdown(sidecar, run_id) -> str``
(``build_report`` / ``render_markdown`` accepted as alternate names).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator, Callable

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from noosphere.api.state import AppState, settings_to_dict
from noosphere.models import (
    Gap,
    IdeonomyExpansion,
    Run,
    RunPhase,
    WhitespaceCandidate,
)

HEARTBEAT_S = 15.0

router = APIRouter(prefix="/api")


def _state(request: Request) -> AppState:
    return request.app.state.noosphere


class NewSurveyRequest(BaseModel):
    field_name: str
    seed_queries: list[str]


class ZoomRequest(BaseModel):
    run_id: str


# -- runs & surveys ----------------------------------------------------------


@router.get("/runs")
async def list_runs(request: Request) -> list[Run]:
    return _state(request).sidecar.list_runs()


@router.post("/surveys")
async def create_survey(request: Request, req: NewSurveyRequest) -> Run:
    state = _state(request)
    run = Run(
        run_id=str(uuid.uuid4()),
        field_name=req.field_name,
        phase=RunPhase.COARSE,
    )
    state.sidecar.create_run(run)
    job_id = state.queue.submit(
        "coarse_survey",
        {
            "run_id": run.run_id,
            "field_name": req.field_name,
            "seed_queries": req.seed_queries,
        },
        run_id=run.run_id,
    )
    state.bus.publish(
        {
            "type": "run_created",
            "run_id": run.run_id,
            "phase": run.phase.value,
            "field_name": run.field_name,
            "job_id": job_id,
        }
    )
    return run


# -- settings ----------------------------------------------------------------


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    return settings_to_dict(_state(request).settings)


@router.put("/settings")
async def put_settings(
    request: Request, body: dict[str, Any] = Body(...)
) -> dict[str, Any]:
    return settings_to_dict(_state(request).update_settings(body))


# -- whitespace & zoom -------------------------------------------------------


@router.get("/runs/{run_id}/whitespace")
async def list_whitespace(request: Request, run_id: str) -> list[WhitespaceCandidate]:
    return _state(request).sidecar.list_whitespace(run_id)


@router.post("/whitespace/{whitespace_id}/zoom")
async def zoom_whitespace(
    request: Request, whitespace_id: str, req: ZoomRequest
) -> Run:
    state = _state(request)
    parent = state.sidecar.get_run(req.run_id)
    if parent is None:
        raise HTTPException(status_code=404, detail=f"run {req.run_id!r} not found")
    candidates = state.sidecar.list_whitespace(req.run_id)
    if not any(w.whitespace_id == whitespace_id for w in candidates):
        raise HTTPException(
            status_code=404,
            detail=f"whitespace {whitespace_id!r} not found on run {req.run_id!r}",
        )
    run = Run(
        run_id=str(uuid.uuid4()),
        field_name=parent.field_name,
        phase=RunPhase.ZOOM,
        parent_run_id=parent.run_id,
        whitespace_id=whitespace_id,
    )
    state.sidecar.create_run(run)
    job_id = state.queue.submit(
        "zoom_survey",
        {
            "run_id": run.run_id,
            "parent_run_id": parent.run_id,
            "whitespace_id": whitespace_id,
        },
        run_id=run.run_id,
    )
    state.bus.publish(
        {
            "type": "run_created",
            "run_id": run.run_id,
            "phase": run.phase.value,
            "field_name": run.field_name,
            "parent_run_id": parent.run_id,
            "whitespace_id": whitespace_id,
            "job_id": job_id,
        }
    )
    return run


# -- gaps & expansions -------------------------------------------------------


@router.get("/gaps")
async def list_gaps(request: Request, zoom_run_id: str | None = None) -> list[Gap]:
    return _state(request).sidecar.list_gaps(zoom_run_id)


@router.get("/gaps/{gap_id}/expansions")
async def list_expansions(request: Request, gap_id: str) -> list[IdeonomyExpansion]:
    return _state(request).sidecar.list_expansions(gap_id)


@router.post("/gaps/{gap_id}/expand", status_code=202)
async def expand_gap(request: Request, gap_id: str) -> dict[str, str]:
    state = _state(request)
    gap = next((g for g in state.sidecar.list_gaps() if g.gap_id == gap_id), None)
    if gap is None:
        raise HTTPException(status_code=404, detail=f"gap {gap_id!r} not found")
    attempt = len(state.sidecar.list_expansions(gap_id)) + 1
    job_id = state.queue.submit(
        "expand_gap",
        {"gap_id": gap_id, "attempt": attempt},
        run_id=gap.zoom_run_id,
    )
    state.bus.publish(
        {
            "type": "expansion_queued",
            "gap_id": gap_id,
            "attempt": attempt,
            "job_id": job_id,
        }
    )
    return {"job_id": job_id}


# -- reports -----------------------------------------------------------------


def _report_fn(*names: str) -> Callable[..., Any]:
    try:
        from noosphere.reports import gaps as reports_gaps
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"report generation not built yet (noosphere.reports.gaps unavailable: {exc})",
        )
    for name in names:
        if (fn := getattr(reports_gaps, name, None)) is not None:
            return fn
    raise HTTPException(
        status_code=501,
        detail=(
            "noosphere.reports.gaps present but exposes none of "
            f"{names!r}; report endpoint not wired"
        ),
    )


def _require_run(state: AppState, run_id: str) -> Run:
    run = state.sidecar.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    return run


@router.get("/runs/{run_id}/report")
async def run_report(request: Request, run_id: str) -> dict:
    state = _state(request)
    _require_run(state, run_id)
    fn = _report_fn("report_json", "build_report")
    return fn(state.sidecar, run_id)


@router.get("/runs/{run_id}/report.md")
async def run_report_markdown(request: Request, run_id: str) -> PlainTextResponse:
    state = _state(request)
    _require_run(state, run_id)
    fn = _report_fn("report_markdown", "render_markdown")
    return PlainTextResponse(fn(state.sidecar, run_id), media_type="text/markdown")


# -- spend & events ----------------------------------------------------------


@router.get("/spend")
async def spend(request: Request) -> dict:
    return _state(request).meter.totals()


@router.get("/events")
async def events(request: Request) -> StreamingResponse:
    """SSE feed of the event bus; one JSON object per ``data:`` line, comment
    heartbeat every HEARTBEAT_S. EventSource clients authenticate via
    ``?token=`` (handled by server.py's middleware, not here)."""
    state = _state(request)

    async def stream() -> AsyncIterator[str]:
        queue = state.bus.attach()
        try:
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
                except asyncio.TimeoutError:
                    yield ": heartbeat\n\n"
                else:
                    yield f"data: {json.dumps(event)}\n\n"
        finally:
            state.bus.detach(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )
