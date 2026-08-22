"""JobQueue tests against an in-memory fake of the Sidecar job API."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any, Callable

import pytest

from noosphere.pipeline.queue import Checkpoint, JobQueue


class FakeSidecar:
    """In-memory stand-in matching the Sidecar job-API contract."""

    def __init__(self) -> None:
        self.jobs: dict[str, dict[str, Any]] = {}
        self._seq = 0

    def job_put(
        self,
        job_id: str,
        kind: str,
        payload: dict[str, Any],
        status: str,
        run_id: str | None,
    ) -> None:
        self._seq += 1
        self.jobs[job_id] = {
            "job_id": job_id,
            "kind": kind,
            "payload": payload,
            "status": status,
            "run_id": run_id,
            "checkpoint": None,
            "seq": self._seq,
        }

    def job_update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> None:
        job = self.jobs[job_id]
        if status is not None:
            job["status"] = status
        if checkpoint is not None:
            job["checkpoint"] = checkpoint

    def job_get(self, job_id: str) -> dict[str, Any] | None:
        job = self.jobs.get(job_id)
        return dict(job) if job is not None else None

    def jobs_pending(self) -> list[dict[str, Any]]:
        live = (j for j in self.jobs.values() if j["status"] in ("pending", "running"))
        return [dict(j) for j in sorted(live, key=lambda j: j["seq"])]

    def job_failed_for_run(self, run_id: str) -> dict[str, Any] | None:
        failed = [
            j
            for j in self.jobs.values()
            if j["status"] == "failed" and j["run_id"] == run_id
        ]
        if not failed:
            return None
        return dict(max(failed, key=lambda j: j["seq"]))


async def wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@contextlib.asynccontextmanager
async def running_worker(queue: JobQueue, handlers: dict):
    task = asyncio.create_task(queue.worker(handlers, poll_s=0.01))
    try:
        yield task
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


async def test_submit_and_execute_happy_path() -> None:
    sidecar = FakeSidecar()
    queue = JobQueue(sidecar)
    seen: list[dict[str, Any]] = []

    async def handle(payload: dict[str, Any], checkpoint: Checkpoint) -> None:
        seen.append(payload)

    job_id = queue.submit("ingest", {"work_id": "W1"}, run_id="run-1")
    assert sidecar.jobs[job_id]["status"] == "pending"
    assert sidecar.jobs[job_id]["run_id"] == "run-1"

    async with running_worker(queue, {"ingest": handle}):
        await wait_until(lambda: sidecar.jobs[job_id]["status"] == "done")

    assert seen == [{"work_id": "W1"}]


async def test_handler_failure_marks_failed_and_worker_continues() -> None:
    sidecar = FakeSidecar()
    queue = JobQueue(sidecar)

    async def boom(payload: dict[str, Any], checkpoint: Checkpoint) -> None:
        raise ValueError("bad payload")

    async def ok(payload: dict[str, Any], checkpoint: Checkpoint) -> None:
        pass

    bad_id = queue.submit("boom", {})
    good_id = queue.submit("ok", {})

    async with running_worker(queue, {"boom": boom, "ok": ok}):
        await wait_until(lambda: sidecar.jobs[good_id]["status"] == "done")

    failed = sidecar.jobs[bad_id]
    assert failed["status"] == "failed"
    assert "ValueError" in failed["checkpoint"]["error"]
    assert "bad payload" in failed["checkpoint"]["error"]


async def test_checkpoint_save_get_round_trip_mid_handler() -> None:
    sidecar = FakeSidecar()
    queue = JobQueue(sidecar)
    observed: list[dict[str, Any] | None] = []

    async def handle(payload: dict[str, Any], checkpoint: Checkpoint) -> None:
        observed.append(checkpoint.get())
        checkpoint.save({"step": 1, "done_items": ["a"]})
        observed.append(checkpoint.get())

    job_id = queue.submit("stepper", {})
    async with running_worker(queue, {"stepper": handle}):
        await wait_until(lambda: sidecar.jobs[job_id]["status"] == "done")

    assert observed == [None, {"step": 1, "done_items": ["a"]}]
    assert sidecar.jobs[job_id]["checkpoint"] == {"step": 1, "done_items": ["a"]}


async def test_crashed_running_job_resumes_from_checkpoint() -> None:
    sidecar = FakeSidecar()
    queue = JobQueue(sidecar)

    sidecar.job_put("job-crashed", "batch", {"items": ["a", "b"]}, "running", None)
    sidecar.job_update("job-crashed", checkpoint={"completed": ["a"]})

    processed: list[str] = []
    seen_checkpoints: list[dict[str, Any] | None] = []

    async def handle(payload: dict[str, Any], checkpoint: Checkpoint) -> None:
        state = checkpoint.get()
        seen_checkpoints.append(state)
        completed = set((state or {}).get("completed", []))
        for item in payload["items"]:
            if item in completed:
                continue
            processed.append(item)
            completed.add(item)
            checkpoint.save({"completed": sorted(completed)})

    async with running_worker(queue, {"batch": handle}):
        await wait_until(lambda: sidecar.jobs["job-crashed"]["status"] == "done")

    assert seen_checkpoints == [{"completed": ["a"]}]
    assert processed == ["b"]
    assert sidecar.jobs["job-crashed"]["checkpoint"] == {"completed": ["a", "b"]}


async def test_cancellation_returns_in_flight_job_to_pending() -> None:
    sidecar = FakeSidecar()
    queue = JobQueue(sidecar)
    entered = asyncio.Event()

    async def stall(payload: dict[str, Any], checkpoint: Checkpoint) -> None:
        checkpoint.save({"phase": "started"})
        entered.set()
        await asyncio.Event().wait()

    job_id = queue.submit("stall", {})
    task = asyncio.create_task(queue.worker({"stall": stall}, poll_s=0.01))
    await entered.wait()
    await wait_until(lambda: sidecar.jobs[job_id]["status"] == "running")

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sidecar.jobs[job_id]["status"] == "pending"
    assert sidecar.jobs[job_id]["checkpoint"] == {"phase": "started"}


async def test_retry_run_requeues_failed_job_from_checkpoint() -> None:
    sidecar = FakeSidecar()
    queue = JobQueue(sidecar)
    attempts: list[dict[str, Any] | None] = []

    async def flaky(payload: dict[str, Any], checkpoint: Checkpoint) -> None:
        attempts.append(checkpoint.get())
        if len(attempts) == 1:
            checkpoint.save({"step": 2})
            raise RuntimeError("transient")

    job_id = queue.submit("flaky", {}, run_id="run-1")

    async with running_worker(queue, {"flaky": flaky}):
        await wait_until(lambda: sidecar.jobs[job_id]["status"] == "failed")
        assert sidecar.jobs[job_id]["checkpoint"] == {"step": 2, "error": "RuntimeError: transient"}

        requeued = queue.retry_run("run-1")
        assert requeued is not None and requeued["status"] == "pending"
        await wait_until(lambda: sidecar.jobs[job_id]["status"] == "done")

    # second attempt resumed from the checkpoint, error key stripped
    assert attempts == [None, {"step": 2}]
    assert queue.retry_run("run-1") is None  # nothing failed anymore
    assert queue.retry_run("no-such-run") is None
