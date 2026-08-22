"""Resumable async job queue persisting all state through the Sidecar job API.

Jobs move pending -> running -> done|failed. A worker cancelled mid-handler puts
the in-flight job back to pending; a job left in "running" by a crash is
recovered to pending on the next worker start. Handlers must be idempotent from
their checkpoint: on resume they receive the last saved checkpoint dict and are
expected to skip work already recorded there.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Awaitable, Callable, Protocol


class SidecarJobs(Protocol):
    """The slice of the Sidecar contract the queue depends on.

    `jobs_pending()` must return jobs with status in {"pending", "running"},
    oldest-first, each row carrying at least job_id/kind/payload/status.
    """

    def job_put(
        self,
        job_id: str,
        kind: str,
        payload: dict[str, Any],
        status: str,
        run_id: str | None,
    ) -> None: ...

    def job_update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        checkpoint: dict[str, Any] | None = None,
    ) -> None: ...

    def job_get(self, job_id: str) -> dict[str, Any] | None: ...

    def jobs_pending(self) -> list[dict[str, Any]]: ...

    def job_failed_for_run(self, run_id: str) -> dict[str, Any] | None: ...


# (job_id, run_id, checkpoint_data) — called after every checkpoint save so the
# integrator can publish live progress (e.g. onto the SSE bus).
CheckpointListener = Callable[[str, str | None, dict], None]


class Checkpoint:
    """Handler-facing view of one job's persisted checkpoint."""

    def __init__(
        self,
        sidecar: SidecarJobs,
        job_id: str,
        on_save: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self._sidecar = sidecar
        self._job_id = job_id
        self._on_save = on_save

    def get(self) -> dict[str, Any] | None:
        job = self._sidecar.job_get(self._job_id)
        if job is None:
            return None
        return job.get("checkpoint")

    def save(self, data: dict[str, Any]) -> None:
        self._sidecar.job_update(self._job_id, checkpoint=data)
        if self._on_save is not None:
            self._on_save(data)


Handler = Callable[[dict[str, Any], Checkpoint], Awaitable[None]]


class JobQueue:
    def __init__(
        self,
        sidecar: SidecarJobs,
        on_checkpoint: CheckpointListener | None = None,
    ) -> None:
        self._sidecar = sidecar
        self.on_checkpoint = on_checkpoint

    def submit(
        self, kind: str, payload: dict[str, Any], run_id: str | None = None
    ) -> str:
        job_id = str(uuid.uuid4())
        self._sidecar.job_put(job_id, kind, payload, "pending", run_id)
        return job_id

    def retry_run(self, run_id: str) -> dict[str, Any] | None:
        """Requeue the newest failed job for ``run_id``.

        The stored checkpoint (minus its "error" key) is kept, so the handler
        resumes from where it got to — handlers are idempotent from their
        checkpoint by contract. Returns the requeued job, or None if the run
        has no failed job. The live worker picks it up within its poll tick.
        """
        job = self._sidecar.job_failed_for_run(run_id)
        if job is None:
            return None
        checkpoint = dict(job.get("checkpoint") or {})
        checkpoint.pop("error", None)
        self._sidecar.job_update(job["job_id"], status="pending", checkpoint=checkpoint)
        return {**job, "status": "pending", "checkpoint": checkpoint}

    async def worker(
        self, handlers: dict[str, Handler], poll_s: float = 0.5
    ) -> None:
        """Process jobs until cancelled.

        On start, jobs stranded in "running" (a previous worker crashed or was
        killed) are reset to "pending" so they get re-picked-up.
        """
        self._recover_stranded()
        while True:
            job = self._next_pending()
            if job is None:
                await asyncio.sleep(poll_s)
                continue
            await self._run_job(job, handlers)

    def _recover_stranded(self) -> None:
        for job in self._sidecar.jobs_pending():
            if job.get("status") == "running":
                self._sidecar.job_update(job["job_id"], status="pending")

    def _next_pending(self) -> dict[str, Any] | None:
        for job in self._sidecar.jobs_pending():
            if job.get("status") == "pending":
                return job
        return None

    async def _run_job(
        self, job: dict[str, Any], handlers: dict[str, Handler]
    ) -> None:
        job_id: str = job["job_id"]
        self._sidecar.job_update(job_id, status="running")
        on_save = None
        if self.on_checkpoint is not None:
            listener = self.on_checkpoint
            run_id = job.get("run_id")
            on_save = lambda data: listener(job_id, run_id, data)  # noqa: E731
        checkpoint = Checkpoint(self._sidecar, job_id, on_save=on_save)
        handler = handlers.get(job["kind"])
        if handler is None:
            self._fail(job_id, checkpoint, f"no handler for kind {job['kind']!r}")
            return
        try:
            await handler(job["payload"], checkpoint)
        except asyncio.CancelledError:
            self._sidecar.job_update(job_id, status="pending")
            raise
        except Exception as exc:
            self._fail(job_id, checkpoint, f"{type(exc).__name__}: {exc}")
        else:
            self._sidecar.job_update(job_id, status="done")

    def _fail(self, job_id: str, checkpoint: Checkpoint, error: str) -> None:
        data = checkpoint.get() or {}
        data["error"] = error
        self._sidecar.job_update(job_id, status="failed", checkpoint=data)
