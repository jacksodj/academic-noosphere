"""Sidecar tests: real DuckDB in tmp_path."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

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
from noosphere.sidecar import Sidecar


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "sidecar.duckdb"


@pytest.fixture()
def sidecar(db_path: Path):
    s = Sidecar(db_path)
    yield s
    s.close()


def _run(run_id: str = "run-1", field_name: str = "memory for AI agents") -> Run:
    return Run(
        run_id=run_id,
        field_name=field_name,
        phase=RunPhase.COARSE,
        query_manifest_hash="abc123",
    )


class TestRuns:
    def test_run_lifecycle_and_snapshot_round_trip(self, sidecar: Sidecar) -> None:
        run = _run()
        sidecar.create_run(run)

        got = sidecar.get_run("run-1")
        assert got == run
        assert got is not None and got.status is RunStatus.PENDING

        started = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
        sidecar.update_run("run-1", status=RunStatus.RUNNING, started_at=started)
        got = sidecar.get_run("run-1")
        assert got is not None
        assert got.status is RunStatus.RUNNING
        assert got.started_at == started
        assert got.finished_at is None

        finished = datetime(2026, 8, 21, 13, 30, tzinfo=timezone.utc)
        sidecar.update_run("run-1", status=RunStatus.COMPLETED, finished_at=finished)
        got = sidecar.get_run("run-1")
        assert got is not None
        assert got.status is RunStatus.COMPLETED
        assert got.finished_at == finished

        sidecar.add_run_works("run-1", ["W3", "W1", "W2"])
        sidecar.add_run_works("run-1", ["W2", "W4"])  # idempotent overlap
        assert sidecar.get_run_works("run-1") == ["W1", "W2", "W3", "W4"]
        assert sidecar.get_run_works("no-such-run") == []

    def test_get_run_missing(self, sidecar: Sidecar) -> None:
        assert sidecar.get_run("nope") is None

    def test_list_runs_filters_by_field(self, sidecar: Sidecar) -> None:
        sidecar.create_run(_run("run-a", "field-one"))
        sidecar.create_run(_run("run-b", "field-two"))
        sidecar.create_run(_run("run-c", "field-one"))

        assert {r.run_id for r in sidecar.list_runs()} == {"run-a", "run-b", "run-c"}
        assert [r.run_id for r in sidecar.list_runs("field-one")] == ["run-a", "run-c"]
        assert sidecar.list_runs("field-none") == []

    def test_zoom_run_links_survive_round_trip(self, sidecar: Sidecar) -> None:
        zoom = Run(
            run_id="run-z",
            field_name="field-one",
            phase=RunPhase.ZOOM,
            parent_run_id="run-a",
            whitespace_id="ws-1",
        )
        sidecar.create_run(zoom)
        got = sidecar.get_run("run-z")
        assert got == zoom
        assert got is not None and got.phase is RunPhase.ZOOM


class TestCache:
    def test_cache_is_immutable(self, sidecar: Sidecar) -> None:
        key = "k1"
        sidecar.cache_put(key, "openalex", "https://api.example/works?q=x", "first body")
        sidecar.cache_put(key, "openalex", "https://api.example/works?q=x", "second body")
        assert sidecar.cache_get(key) == "first body"

    def test_cache_miss_returns_none(self, sidecar: Sidecar) -> None:
        assert sidecar.cache_get("absent") is None

    def test_distinct_keys_are_independent(self, sidecar: Sidecar) -> None:
        sidecar.cache_put("a", "openalex", "u1", "body-a")
        sidecar.cache_put("b", "s2", "u2", "body-b")
        assert sidecar.cache_get("a") == "body-a"
        assert sidecar.cache_get("b") == "body-b"


class TestJobs:
    def test_job_round_trip_and_checkpoint_update(self, sidecar: Sidecar) -> None:
        sidecar.job_put("j1", "resolve", {"ids": ["W1", "W2"]}, "pending", "run-1")

        job = sidecar.job_get("j1")
        assert job is not None
        assert job["kind"] == "resolve"
        assert job["payload"] == {"ids": ["W1", "W2"]}
        assert job["status"] == "pending"
        assert job["run_id"] == "run-1"
        assert job["checkpoint"] is None

        sidecar.job_update("j1", status="running", checkpoint={"done": 1})
        job = sidecar.job_get("j1")
        assert job is not None
        assert job["status"] == "running"
        assert job["checkpoint"] == {"done": 1}

        sidecar.job_update("j1", checkpoint={"done": 2})
        job = sidecar.job_get("j1")
        assert job is not None
        assert job["status"] == "running"  # untouched
        assert job["checkpoint"] == {"done": 2}

    def test_job_get_missing(self, sidecar: Sidecar) -> None:
        assert sidecar.job_get("nope") is None

    def test_jobs_pending_ordering_and_filtering(self, sidecar: Sidecar) -> None:
        sidecar.job_put("j1", "discover", {}, "pending", None)
        sidecar.job_put("j2", "resolve", {}, "pending", None)
        sidecar.job_put("j3", "analyze", {}, "pending", None)

        assert [j["job_id"] for j in sidecar.jobs_pending()] == ["j1", "j2", "j3"]

        sidecar.job_update("j2", status="running")  # still re-picked after crash
        sidecar.job_update("j1", status="completed")
        sidecar.job_update("j3", status="failed")
        assert [j["job_id"] for j in sidecar.jobs_pending()] == ["j2"]


def _evidence() -> list[EvidenceItem]:
    return [
        EvidenceItem(kind="work", work_id="W123"),
        EvidenceItem(
            kind="web",
            url="https://example.org/paper",
            retrieved_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
            quote="future work should examine X",
        ),
    ]


class TestWhitespaceGapsExpansions:
    def test_whitespace_round_trip_and_upsert(self, sidecar: Sidecar) -> None:
        w = WhitespaceCandidate(
            whitespace_id="ws-1",
            run_id="run-1",
            kind="bridge",
            description="sparse link between communities 3 and 7",
            community_a=3,
            community_b=7,
            sparsity_score=0.91,
            low_citedness_signal=0.4,
            evidence=_evidence(),
        )
        sidecar.put_whitespace(w)
        assert sidecar.list_whitespace("run-1") == [w]
        assert sidecar.list_whitespace("other-run") == []

        updated = w.model_copy(update={"status": "confirmed"})
        sidecar.put_whitespace(updated)
        got = sidecar.list_whitespace("run-1")
        assert got == [updated]
        assert got[0].status == "confirmed"

    def test_gap_round_trip_and_zoom_filter(self, sidecar: Sidecar) -> None:
        g1 = Gap(
            gap_id="gap-1",
            whitespace_id="ws-1",
            zoom_run_id="run-z1",
            kinds=[GapKind.STRUCTURAL, GapKind.NARRATIVE],
            statement="No work connects consolidation schedules to agent memory eviction.",
            evidence=_evidence(),
            scores={"sparsity": 0.9, "narrative_demand": 0.7},
            composite_score=0.82,
        )
        g2 = g1.model_copy(update={"gap_id": "gap-2", "zoom_run_id": "run-z2"})
        sidecar.put_gap(g1)
        sidecar.put_gap(g2)

        assert sidecar.list_gaps() == [g1, g2]
        assert sidecar.list_gaps("run-z1") == [g1]
        assert sidecar.list_gaps("run-z9") == []

    def test_expansion_round_trip_ordered_by_attempt(self, sidecar: Sidecar) -> None:
        def expansion(attempt: int) -> IdeonomyExpansion:
            return IdeonomyExpansion(
                gap_id="gap-1",
                attempt=attempt,
                tuple=IdeonomyTuple(
                    operators=["inversion", "hybridization"],
                    organon="organon-of-relations",
                    dimension_prompts=["scale", "medium", "agency"],
                    seed=f"run-z1:gap-1:{attempt}",
                ),
                ideas=[
                    IdeonomyIdea(
                        text="Invert eviction: consolidate what an agent forgets.",
                        operators=["inversion"],
                        organon_position="pole-2",
                        nearest_work_id="W123",
                    )
                ],
            )

        e2, e1 = expansion(2), expansion(1)
        sidecar.put_expansion(e2)
        sidecar.put_expansion(e1)
        assert sidecar.list_expansions("gap-1") == [e1, e2]
        assert sidecar.list_expansions("gap-none") == []


class TestPersistence:
    def test_reopen_is_idempotent_and_preserves_data(self, db_path: Path) -> None:
        s1 = Sidecar(db_path)
        s1.create_run(_run())
        s1.add_run_works("run-1", ["W1"])
        s1.cache_put("k", "openalex", "u", "body")
        s1.job_put("j1", "resolve", {"x": 1}, "pending", "run-1")
        s1.close()

        s2 = Sidecar(db_path)  # schema creation must be idempotent
        try:
            assert s2.get_run("run-1") == _run()
            assert s2.get_run_works("run-1") == ["W1"]
            assert s2.cache_get("k") == "body"
            assert [j["job_id"] for j in s2.jobs_pending()] == ["j1"]
        finally:
            s2.close()
