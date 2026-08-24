"""/api routes for the SPA (app/src/endpoints.ts is the client-side mirror).

Integrator contract: ``app.include_router(router)`` — the prefix is already
``/api`` — and set ``app.state.noosphere = AppState.build(...)`` before serving.
Survey/zoom/expand handlers are NOT registered here; the integrator passes
them to ``state.queue.worker({"coarse_survey": ..., "zoom_survey": ...,
"expand_gap": ...})`` at startup. This module only enqueues those job kinds.

Report endpoints lazy-import ``noosphere.reports.gaps`` (built in parallel)
and answer 501 until it provides
``assemble_report(run_id, sidecar, graph, weights) -> dict`` and
``to_markdown(report) -> str``.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import PlainTextResponse, StreamingResponse
from pydantic import BaseModel

from noosphere import config
from noosphere.api.state import AppState, progress_summary, settings_to_dict
from noosphere.models import (
    Gap,
    IdeonomyExpansion,
    Run,
    RunPhase,
    RunStatus,
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


@router.get("/runs/{run_id}/activity")
async def run_activity(
    request: Request, run_id: str, limit: int = 1000
) -> dict[str, Any]:
    """Persisted activity lines for a run, oldest-first (live tail via SSE)."""
    state = _state(request)
    if state.sidecar.get_run(run_id) is None:
        raise HTTPException(404, f"unknown run {run_id}")
    return {
        "run_id": run_id,
        "activities": state.sidecar.activities_for_run(run_id, limit=limit),
    }


@router.get("/runs/{run_id}/insights")
async def run_insights(request: Request, run_id: str) -> dict[str, Any]:
    """Corpus insights: most-cited works + recently-active topics.

    Pure graph reads, computed on demand off the event loop.
    """
    from noosphere.analysis.insights import corpus_insights

    state = _state(request)
    if state.sidecar.get_run(run_id) is None:
        raise HTTPException(404, f"unknown run {run_id}")
    return await asyncio.to_thread(
        corpus_insights, run_id, state.sidecar, state.graph
    )


@router.get("/runs/{run_id}/works")
async def run_works_list(
    request: Request,
    run_id: str,
    topic_id: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """Filtered, citation-ranked work listing (Sources explorer)."""
    from noosphere.analysis.insights import list_works

    state = _state(request)
    if state.sidecar.get_run(run_id) is None:
        raise HTTPException(404, f"unknown run {run_id}")
    return await asyncio.to_thread(
        list_works, run_id, state.sidecar, state.graph,
        topic_id=topic_id, year_from=year_from, year_to=year_to,
        q=q, limit=min(limit, 500), offset=max(offset, 0),
    )


@router.get("/runs/{run_id}/progress")
async def run_progress(request: Request, run_id: str) -> dict[str, Any]:
    """Stage-level progress for a run, derived from its job's checkpoint."""
    state = _state(request)
    if state.sidecar.get_run(run_id) is None:
        raise HTTPException(404, f"unknown run {run_id}")
    job = state.sidecar.job_for_run(run_id)
    if job is None:
        raise HTTPException(404, f"run {run_id} has no job")
    return {
        "run_id": run_id,
        "job_status": job["status"],
        "progress": progress_summary(job.get("checkpoint")),
    }


@router.post("/runs/{run_id}/redetect", status_code=202)
async def redetect_whitespace(request: Request, run_id: str) -> dict[str, str]:
    """Re-run whitespace detection over a coarse run's snapshot.

    Queued job: uses the current graph and adaptive Louvain resolution;
    already-zoomed candidates (confirmed / zooming / not_confirmed) are kept,
    pending ones are replaced. Progress lands in the run's activity feed and a
    "whitespace_updated" event fires when done.
    """
    state = _state(request)
    run = state.sidecar.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    if run.phase != RunPhase.COARSE:
        raise HTTPException(422, "whitespace detection runs on coarse runs")
    job_id = state.queue.submit("redetect_whitespace", {"run_id": run_id}, run_id=run_id)
    return {"job_id": job_id, "run_id": run_id}


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry_run(request: Request, run_id: str) -> Run:
    """Requeue a failed run's job; it resumes from its last checkpoint."""
    state = _state(request)
    run = state.sidecar.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"unknown run {run_id}")
    if state.queue.retry_run(run_id) is None:
        raise HTTPException(409, f"run {run_id} has no failed job to retry")
    state.sidecar.update_run(run_id, status=RunStatus.PENDING)
    state.bus.publish({"type": "run_requeued", "run_id": run_id})
    updated = state.sidecar.get_run(run_id)
    assert updated is not None
    return updated


# -- credentials (Keychain-backed; values are write-only) --------------------


class CredentialValue(BaseModel):
    value: str


@router.get("/credentials")
async def list_credentials() -> list[dict[str, Any]]:
    """Presence/source/hint for each known credential — never the values."""
    return config.credentials_status()


@router.put("/credentials/{name}")
async def put_credential(name: str, body: CredentialValue) -> dict[str, Any]:
    if name not in config.CRED_KEYS:
        raise HTTPException(404, f"unknown credential {name!r}")
    value = body.value.strip()
    if not value:
        raise HTTPException(422, "value must be non-empty")
    try:
        config.set_credential(name, value)
    except Exception as exc:  # Keychain locked / no backend
        raise HTTPException(500, f"could not write to Keychain: {exc}") from exc
    return config.credential_status(name)


@router.delete("/credentials/{name}")
async def remove_credential(name: str) -> dict[str, Any]:
    if name not in config.CRED_KEYS:
        raise HTTPException(404, f"unknown credential {name!r}")
    try:
        config.delete_credential(name)
    except Exception as exc:
        raise HTTPException(500, f"could not delete from Keychain: {exc}") from exc
    return config.credential_status(name)


# -- embedding model (first-run download, ticket #22) ------------------------


def _embedding_status() -> dict[str, Any]:
    from noosphere.pipeline.embed import OnnxSpecter2Embedder, onnx_model_dir
    from noosphere.pipeline.model_fetch import HF_REPO, fetcher

    present = OnnxSpecter2Embedder.available()
    if present:
        kind = "onnx"
    else:
        try:
            import sentence_transformers  # noqa: F401

            kind = "sentence-transformers"
        except ImportError:
            kind = "stub"
    return {
        "present": present,
        "embedder": kind,
        "hf_repo": HF_REPO,
        "dir": str(onnx_model_dir()),
        "download": fetcher.snapshot(),
    }


@router.get("/models/embedding")
async def embedding_model_status() -> dict[str, Any]:
    """Whether real (non-stub) embeddings are available, and download state."""
    return _embedding_status()


@router.post("/models/embedding/download", status_code=202)
async def embedding_model_download() -> dict[str, Any]:
    from noosphere.pipeline.embed import OnnxSpecter2Embedder, onnx_model_dir
    from noosphere.pipeline.model_fetch import fetcher

    if not OnnxSpecter2Embedder.available():
        fetcher.start(onnx_model_dir())
    return _embedding_status()


@router.post("/aws/check")
async def aws_check(request: Request) -> dict[str, Any]:
    """STS identity check so onboarding/settings can confirm AWS access.

    Runs in a thread (boto3 is blocking); short timeouts so a missing network
    answers in seconds, not minutes.
    """
    import os

    region = _state(request).settings.aws_region

    def _check() -> dict[str, Any]:
        import boto3
        from botocore.config import Config as BotoConfig

        sts = boto3.client(
            "sts",
            region_name=region,
            config=BotoConfig(
                connect_timeout=5, read_timeout=10, retries={"max_attempts": 1}
            ),
        )
        ident = sts.get_caller_identity()
        return {"account": ident["Account"], "arn": ident["Arn"]}

    profile = os.environ.get("AWS_PROFILE")
    try:
        ident = await asyncio.to_thread(_check)
    except Exception as exc:
        return {"ok": False, "profile": profile, "error": str(exc)}
    return {"ok": True, "profile": profile, **ident}


# -- whitespace & zoom -------------------------------------------------------


@router.get("/runs/{run_id}/whitespace")
async def list_whitespace(request: Request, run_id: str) -> list[WhitespaceCandidate]:
    return _state(request).sidecar.list_whitespace(run_id)


@router.post("/whitespace/{whitespace_id}/zoom")
async def zoom_whitespace(
    request: Request, whitespace_id: str, req: ZoomRequest
) -> dict:
    state = _state(request)
    parent = state.sidecar.get_run(req.run_id)
    if parent is None:
        raise HTTPException(status_code=404, detail=f"run {req.run_id!r} not found")
    candidates = state.sidecar.list_whitespace(req.run_id)
    candidate = next(
        (w for w in candidates if w.whitespace_id == whitespace_id), None
    )
    if candidate is None:
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
    candidate.status = "zooming"
    state.sidecar.put_whitespace(candidate)
    return {"run": run, "candidate": candidate}


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
    return {"job_id": job_id, "gap_id": gap_id, "attempt": str(attempt)}


# -- reports -----------------------------------------------------------------


def _reports_module() -> Any:
    try:
        from noosphere.reports import gaps as reports_gaps
    except ImportError as exc:
        raise HTTPException(
            status_code=501,
            detail=f"report generation not built yet (noosphere.reports.gaps unavailable: {exc})",
        )
    for name in ("assemble_report", "to_markdown"):
        if not hasattr(reports_gaps, name):
            raise HTTPException(
                status_code=501,
                detail=f"noosphere.reports.gaps lacks {name}(); report endpoint not wired",
            )
    return reports_gaps


def _assemble_report(state: AppState, run_id: str) -> dict:
    if state.sidecar.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id!r} not found")
    reports_gaps = _reports_module()
    return reports_gaps.assemble_report(
        run_id, state.sidecar, state.graph, state.settings.ranking_weights
    )


@router.get("/runs/{run_id}/report")
async def run_report(request: Request, run_id: str) -> dict:
    return _assemble_report(_state(request), run_id)


@router.get("/runs/{run_id}/report.md")
async def run_report_markdown(request: Request, run_id: str) -> PlainTextResponse:
    state = _state(request)
    report = _assemble_report(state, run_id)
    reports_gaps = _reports_module()
    return PlainTextResponse(
        reports_gaps.to_markdown(report), media_type="text/markdown"
    )


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
