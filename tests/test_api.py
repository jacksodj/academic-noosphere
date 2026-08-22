"""API layer tests: real AppState.build in tmp_path, no auth middleware.

The SSE stream itself is exercised only for headers (TestClient streaming of
an infinite feed is awkward); the event-bus fan-out is unit-tested directly.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from noosphere.api.routes import events, router
from noosphere.api.state import AppState, EventBus, settings_to_dict
from noosphere.config import CRED_KEYS, Settings
from noosphere.models import (
    EvidenceItem,
    Gap,
    GapKind,
    IdeonomyExpansion,
    IdeonomyIdea,
    IdeonomyTuple,
    Run,
    RunPhase,
    RunStatus,
    WhitespaceCandidate,
)

NOW = datetime(2026, 8, 21, 12, 0, 0, tzinfo=timezone.utc)

REPORTS_BUILT = importlib.util.find_spec("noosphere.reports") is not None


@pytest.fixture
def state(tmp_path: Path) -> AppState:
    s = AppState.build(tmp_path, Settings())
    yield s
    s.close()


@pytest.fixture
def client(state: AppState) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.noosphere = state
    with TestClient(app) as c:
        yield c


def make_run(run_id: str, **kwargs) -> Run:
    defaults = {"field_name": "memory for AI agents", "phase": RunPhase.COARSE}
    defaults.update(kwargs)
    return Run(run_id=run_id, **defaults)


def make_whitespace(whitespace_id: str, run_id: str) -> WhitespaceCandidate:
    return WhitespaceCandidate(
        whitespace_id=whitespace_id,
        run_id=run_id,
        kind="bridge",
        description="communities 1 and 2 barely cite each other",
        community_a=1,
        community_b=2,
        sparsity_score=0.9,
    )


def make_gap(gap_id: str, whitespace_id: str, zoom_run_id: str) -> Gap:
    return Gap(
        gap_id=gap_id,
        whitespace_id=whitespace_id,
        zoom_run_id=zoom_run_id,
        kinds=[GapKind.STRUCTURAL],
        statement="No work bridges X and Y [W1].",
        evidence=[EvidenceItem(kind="work", work_id="W1")],
        scores={"sparsity": 0.9},
        composite_score=0.9,
    )


# -- runs & surveys ----------------------------------------------------------


def test_runs_empty(client: TestClient) -> None:
    r = client.get("/api/runs")
    assert r.status_code == 200
    assert r.json() == []


def test_create_survey_lists_run_and_queues_job(
    client: TestClient, state: AppState
) -> None:
    r = client.post(
        "/api/surveys",
        json={"field_name": "memory for AI agents", "seed_queries": ["agent memory"]},
    )
    assert r.status_code == 200
    run = r.json()
    assert run["field_name"] == "memory for AI agents"
    assert run["phase"] == "coarse"
    assert run["status"] == "pending"
    assert run["parent_run_id"] is None

    listed = client.get("/api/runs").json()
    assert [x["run_id"] for x in listed] == [run["run_id"]]

    jobs = state.sidecar.jobs_pending()
    assert len(jobs) == 1
    job = jobs[0]
    assert job["kind"] == "coarse_survey"
    assert job["status"] == "pending"
    assert job["run_id"] == run["run_id"]
    assert job["payload"]["run_id"] == run["run_id"]
    assert job["payload"]["seed_queries"] == ["agent memory"]


# -- settings ----------------------------------------------------------------


def test_settings_roundtrip_persists_and_excludes_credentials(
    client: TestClient, state: AppState, tmp_path: Path
) -> None:
    defaults = client.get("/api/settings").json()
    assert defaults == settings_to_dict(Settings())
    assert defaults["aws_region"] == "us-east-1"

    patch = dict(defaults)
    patch["aws_region"] = "us-west-2"
    patch["coarse_corpus_target"] = 5000
    patch["openalex_api_key"] = "sk-should-never-persist"  # credential: dropped
    patch["unknown_field"] = "ignored"

    r = client.put("/api/settings", json=patch)
    assert r.status_code == 200
    updated = r.json()
    assert updated["aws_region"] == "us-west-2"
    assert updated["coarse_corpus_target"] == 5000
    assert "openalex_api_key" not in updated
    assert "unknown_field" not in updated
    assert client.get("/api/settings").json() == updated

    settings_file = tmp_path / "settings.json"
    assert settings_file.exists()
    on_disk = json.loads(settings_file.read_text())
    assert on_disk["aws_region"] == "us-west-2"
    for cred in (*CRED_KEYS, *CRED_KEYS.values()):
        assert cred not in on_disk
    assert "sk-should-never-persist" not in settings_file.read_text()


def test_settings_json_merges_over_defaults_on_build(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text(json.dumps({"aws_region": "eu-west-1"}))
    s = AppState.build(tmp_path, Settings())
    try:
        assert s.settings.aws_region == "eu-west-1"
        assert s.settings.coarse_corpus_target == Settings().coarse_corpus_target
    finally:
        s.close()


# -- whitespace & zoom -------------------------------------------------------


def test_whitespace_roundtrip(client: TestClient, state: AppState) -> None:
    state.sidecar.create_run(make_run("run-1"))
    state.sidecar.put_whitespace(make_whitespace("ws-1", "run-1"))

    assert client.get("/api/runs/run-1/whitespace").json()[0]["whitespace_id"] == "ws-1"
    assert client.get("/api/runs/other/whitespace").json() == []


def test_zoom_creates_linked_run_and_job(client: TestClient, state: AppState) -> None:
    state.sidecar.create_run(make_run("run-1"))
    state.sidecar.put_whitespace(make_whitespace("ws-1", "run-1"))

    r = client.post("/api/whitespace/ws-1/zoom", json={"run_id": "run-1"})
    assert r.status_code == 200
    body = r.json()
    zoom = body["run"]
    assert zoom["phase"] == "zoom"
    assert zoom["parent_run_id"] == "run-1"
    assert zoom["whitespace_id"] == "ws-1"
    assert zoom["field_name"] == "memory for AI agents"
    assert state.sidecar.get_run(zoom["run_id"]) is not None
    # SPA contract: candidate returned flipped to "zooming" and persisted so.
    assert body["candidate"]["status"] == "zooming"
    persisted = state.sidecar.list_whitespace("run-1")[0]
    assert persisted.status == "zooming"

    jobs = [j for j in state.sidecar.jobs_pending() if j["kind"] == "zoom_survey"]
    assert len(jobs) == 1
    assert jobs[0]["run_id"] == zoom["run_id"]
    assert jobs[0]["payload"] == {
        "run_id": zoom["run_id"],
        "parent_run_id": "run-1",
        "whitespace_id": "ws-1",
    }


def test_zoom_404s(client: TestClient, state: AppState) -> None:
    assert (
        client.post("/api/whitespace/ws-1/zoom", json={"run_id": "nope"}).status_code
        == 404
    )
    state.sidecar.create_run(make_run("run-1"))
    assert (
        client.post("/api/whitespace/ws-x/zoom", json={"run_id": "run-1"}).status_code
        == 404
    )


# -- gaps & expansions -------------------------------------------------------


def test_gaps_and_expansions_roundtrip(client: TestClient, state: AppState) -> None:
    state.sidecar.put_gap(make_gap("gap-1", "ws-1", "zoom-1"))
    state.sidecar.put_gap(make_gap("gap-2", "ws-2", "zoom-2"))

    assert {g["gap_id"] for g in client.get("/api/gaps").json()} == {"gap-1", "gap-2"}
    filtered = client.get("/api/gaps", params={"zoom_run_id": "zoom-1"}).json()
    assert [g["gap_id"] for g in filtered] == ["gap-1"]

    expansion = IdeonomyExpansion(
        gap_id="gap-1",
        attempt=1,
        tuple=IdeonomyTuple(
            operators=["invert"],
            organon="analogion",
            dimension_prompts=["scale"],
            seed="zoom-1:gap-1:1",
        ),
        ideas=[
            IdeonomyIdea(
                text="speculative idea",
                operators=["invert"],
                organon_position="pole",
                nearest_work_id="W1",
            )
        ],
    )
    state.sidecar.put_expansion(expansion)
    got = client.get("/api/gaps/gap-1/expansions").json()
    assert len(got) == 1
    assert got[0]["attempt"] == 1
    assert got[0]["tuple"]["seed"] == "zoom-1:gap-1:1"
    assert client.get("/api/gaps/gap-2/expansions").json() == []


def test_expand_gap_queues_job(client: TestClient, state: AppState) -> None:
    state.sidecar.put_gap(make_gap("gap-1", "ws-1", "zoom-1"))

    r = client.post("/api/gaps/gap-1/expand")
    assert r.status_code == 202
    job_id = r.json()["job_id"]
    job = state.sidecar.job_get(job_id)
    assert job is not None
    assert job["kind"] == "expand_gap"
    assert job["payload"] == {"gap_id": "gap-1", "attempt": 1}
    assert job["run_id"] == "zoom-1"

    assert client.post("/api/gaps/nope/expand").status_code == 404


# -- reports -----------------------------------------------------------------


def test_report_404_for_unknown_run(client: TestClient) -> None:
    assert client.get("/api/runs/nope/report").status_code == 404
    assert client.get("/api/runs/nope/report.md").status_code == 404


@pytest.mark.skipif(
    REPORTS_BUILT, reason="noosphere.reports exists; 501 fallback no longer applies"
)
def test_report_501_until_reports_module_lands(
    client: TestClient, state: AppState
) -> None:
    state.sidecar.create_run(make_run("run-1"))
    r = client.get("/api/runs/run-1/report")
    assert r.status_code == 501
    assert "report" in r.json()["detail"].lower()
    assert client.get("/api/runs/run-1/report.md").status_code == 501


@pytest.mark.skipif(not REPORTS_BUILT, reason="noosphere.reports not built yet")
def test_report_roundtrip(client: TestClient, state: AppState) -> None:
    state.sidecar.create_run(
        make_run(
            "zoom-1",
            phase=RunPhase.ZOOM,
            parent_run_id="run-1",
            whitespace_id="ws-1",
        )
    )
    state.sidecar.put_gap(make_gap("gap-1", "ws-1", "zoom-1"))

    r = client.get("/api/runs/zoom-1/report")
    assert r.status_code == 200
    body = r.json()
    assert body["run"]["run_id"] == "zoom-1"
    assert [g["gap_id"] for g in body["gaps"]] == ["gap-1"]

    md = client.get("/api/runs/zoom-1/report.md")
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "gap" in md.text.lower()


# -- spend -------------------------------------------------------------------


def test_spend_shape(client: TestClient, state: AppState) -> None:
    state.meter.record("anthropic.claude-haiku-4-5", 1_000_000, 100_000)
    body = client.get("/api/spend").json()
    assert set(body) == {"models", "total", "note"}
    haiku = body["models"]["anthropic.claude-haiku-4-5"]
    assert haiku["input"] == 1_000_000
    assert haiku["est_usd"] == pytest.approx(1.5)
    assert body["total"]["est_usd"] == pytest.approx(1.5)


# -- events ------------------------------------------------------------------


async def test_event_bus_fanout() -> None:
    bus = EventBus()
    sub_a = bus.subscribe()
    sub_b = bus.subscribe()
    bus.publish({"type": "one"})
    bus.publish({"type": "two"})

    for sub in (sub_a, sub_b):
        got = [
            await asyncio.wait_for(anext(sub), timeout=1.0),
            await asyncio.wait_for(anext(sub), timeout=1.0),
        ]
        assert got == [{"type": "one"}, {"type": "two"}]

    await sub_a.aclose()
    bus.publish({"type": "three"})  # only sub_b still attached; no error
    assert await asyncio.wait_for(anext(sub_b), timeout=1.0) == {"type": "three"}
    await sub_b.aclose()
    assert bus._queues == set()


async def test_events_endpoint_streams_bus_events(state: AppState) -> None:
    # TestClient cannot close an infinite SSE stream, so drive the endpoint
    # coroutine directly with a bare Request.
    app = FastAPI()
    app.state.noosphere = state
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/events",
        "headers": [],
        "query_string": b"",
        "app": app,
    }
    response = await events(Request(scope, receive=lambda: asyncio.Future()))
    assert response.media_type == "text/event-stream"

    body = response.body_iterator
    first = asyncio.create_task(anext(body))
    await asyncio.sleep(0.05)  # let the generator attach to the bus
    state.bus.publish({"type": "ping"})
    chunk = await asyncio.wait_for(first, timeout=2.0)
    assert chunk == 'data: {"type": "ping"}\n\n'
    await body.aclose()
    assert state.bus._queues == set()


def test_survey_event_published(client: TestClient, state: AppState) -> None:
    queue = state.bus.attach()
    try:
        client.post(
            "/api/surveys", json={"field_name": "f", "seed_queries": []}
        ).raise_for_status()
        event = queue.get_nowait()
        assert event["type"] == "run_created"
        assert event["phase"] == "coarse"
    finally:
        state.bus.detach(queue)


class TestRetryRun:
    def test_retry_requeues_failed_job(self, client: TestClient, state: AppState) -> None:
        state.sidecar.create_run(make_run("run-f", status=RunStatus.FAILED))
        state.sidecar.job_put(
            "job-f", "coarse_survey", {"run_id": "run-f"}, "failed", "run-f"
        )
        state.sidecar.job_update(
            "job-f", checkpoint={"step": 3, "error": "RuntimeError: boom"}
        )

        res = client.post("/api/runs/run-f/retry")
        assert res.status_code == 202
        assert res.json()["status"] == "pending"

        job = state.sidecar.job_get("job-f")
        assert job is not None
        assert job["status"] == "pending"
        assert job["checkpoint"] == {"step": 3}  # error stripped, progress kept

    def test_retry_without_failed_job_conflicts(
        self, client: TestClient, state: AppState
    ) -> None:
        state.sidecar.create_run(make_run("run-ok"))
        assert client.post("/api/runs/run-ok/retry").status_code == 409

    def test_retry_unknown_run_404s(self, client: TestClient) -> None:
        assert client.post("/api/runs/nope/retry").status_code == 404


class TestRunProgress:
    def test_progress_from_checkpoint(self, client: TestClient, state: AppState) -> None:
        state.sidecar.create_run(make_run("run-p", status=RunStatus.RUNNING))
        state.sidecar.job_put("job-p", "coarse_survey", {}, "running", "run-p")
        state.sidecar.job_update(
            "job-p",
            checkpoint={
                "done": ["seeds", "expand"],
                "seed_ids": ["W1", "W2"],
                "candidate_ids": ["W1", "W2", "W3"],
            },
        )
        body = client.get("/api/runs/run-p/progress").json()
        assert body["job_status"] == "running"
        assert body["progress"]["done"] == ["seeds", "expand"]
        assert body["progress"]["current"] == "relevance"
        assert body["progress"]["counts"] == {"seeds": 2, "candidates": 3, "kept": 0}
        # no raw id lists leak into the summary
        assert "seed_ids" not in str(body)

    def test_progress_404s(self, client: TestClient, state: AppState) -> None:
        assert client.get("/api/runs/nope/progress").status_code == 404
        state.sidecar.create_run(make_run("run-nojob"))
        assert client.get("/api/runs/run-nojob/progress").status_code == 404

    def test_checkpoint_saves_publish_progress_events(self, state: AppState) -> None:
        events: list[dict] = []
        state.bus.publish = events.append  # type: ignore[method-assign]
        assert state.queue.on_checkpoint is not None
        state.sidecar.job_put("job-e", "coarse_survey", {}, "running", "run-e")
        from noosphere.pipeline.queue import Checkpoint

        cp = Checkpoint(
            state.sidecar,
            "job-e",
            on_save=lambda data: state.queue.on_checkpoint("job-e", "run-e", data),
        )
        cp.save({"done": ["seeds"], "seed_ids": ["W1"]})
        assert events == [
            {
                "type": "progress",
                "run_id": "run-e",
                "progress": {
                    "stages": ["seeds", "expand", "relevance", "persist"],
                    "done": ["seeds"],
                    "current": "expand",
                    "counts": {"seeds": 1, "candidates": 0, "kept": 0},
                    "error": None,
                },
            }
        ]
