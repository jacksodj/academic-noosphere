"""Sidecar store (DuckDB): runs, Run Snapshots, immutable response cache,
resumable job state, and whitespace/gap/expansion persistence.

One file per data dir (`sidecar.duckdb`). All methods synchronous. Timestamps
are stored as ISO-8601 strings throughout.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import duckdb

from noosphere.models import (
    Gap,
    IdeonomyExpansion,
    Run,
    RunStatus,
    WhitespaceCandidate,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id              VARCHAR PRIMARY KEY,
    field_name          VARCHAR NOT NULL,
    phase               VARCHAR NOT NULL,
    parent_run_id       VARCHAR,
    whitespace_id       VARCHAR,
    query_manifest_hash VARCHAR,
    status              VARCHAR NOT NULL,
    started_at          VARCHAR,
    finished_at         VARCHAR
);
CREATE INDEX IF NOT EXISTS idx_runs_field ON runs (field_name);

CREATE TABLE IF NOT EXISTS run_works (
    run_id  VARCHAR NOT NULL,
    work_id VARCHAR NOT NULL,
    PRIMARY KEY (run_id, work_id)
);
CREATE INDEX IF NOT EXISTS idx_run_works_run ON run_works (run_id);

CREATE TABLE IF NOT EXISTS cache (
    key        VARCHAR PRIMARY KEY,
    api        VARCHAR NOT NULL,
    url        VARCHAR NOT NULL,
    body       VARCHAR NOT NULL,
    created_at VARCHAR NOT NULL
);

CREATE SEQUENCE IF NOT EXISTS jobs_seq;
CREATE TABLE IF NOT EXISTS jobs (
    job_id     VARCHAR PRIMARY KEY,
    seq        BIGINT  NOT NULL,
    kind       VARCHAR NOT NULL,
    payload    VARCHAR NOT NULL,
    status     VARCHAR NOT NULL,
    run_id     VARCHAR,
    checkpoint VARCHAR,
    created_at VARCHAR NOT NULL,
    updated_at VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

CREATE SEQUENCE IF NOT EXISTS activities_seq;
CREATE TABLE IF NOT EXISTS activities (
    run_id  VARCHAR NOT NULL,
    seq     BIGINT  NOT NULL,
    ts      VARCHAR NOT NULL,
    message VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activities_run ON activities (run_id);

CREATE TABLE IF NOT EXISTS whitespace (
    whitespace_id VARCHAR PRIMARY KEY,
    run_id        VARCHAR NOT NULL,
    body          VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_whitespace_run ON whitespace (run_id);

CREATE TABLE IF NOT EXISTS gaps (
    gap_id        VARCHAR PRIMARY KEY,
    whitespace_id VARCHAR NOT NULL,
    zoom_run_id   VARCHAR NOT NULL,
    body          VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gaps_zoom_run ON gaps (zoom_run_id);

CREATE TABLE IF NOT EXISTS expansions (
    gap_id  VARCHAR NOT NULL,
    attempt BIGINT  NOT NULL,
    body    VARCHAR NOT NULL,
    PRIMARY KEY (gap_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_expansions_gap ON expansions (gap_id);
"""

_JOB_PENDING_STATUSES = ("pending", "running")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


class Sidecar:
    def __init__(self, db_path: Path):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = duckdb.connect(str(self._db_path))
        self._con.execute(_SCHEMA)

    def close(self) -> None:
        self._con.close()

    # -- runs & snapshots ----------------------------------------------------

    def create_run(self, run: Run) -> None:
        self._con.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                run.run_id,
                run.field_name,
                run.phase.value,
                run.parent_run_id,
                run.whitespace_id,
                run.query_manifest_hash,
                run.status.value,
                _iso(run.started_at),
                _iso(run.finished_at),
            ],
        )

    def update_run(
        self,
        run_id: str,
        *,
        status: RunStatus | None = None,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        query_manifest_hash: str | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            params.append(status.value)
        if started_at is not None:
            sets.append("started_at = ?")
            params.append(started_at.isoformat())
        if finished_at is not None:
            sets.append("finished_at = ?")
            params.append(finished_at.isoformat())
        if query_manifest_hash is not None:
            sets.append("query_manifest_hash = ?")
            params.append(query_manifest_hash)
        if not sets:
            return
        params.append(run_id)
        self._con.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", params)

    def get_run(self, run_id: str) -> Run | None:
        row = self._con.execute(
            "SELECT * FROM runs WHERE run_id = ?", [run_id]
        ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs(self, field_name: str | None = None) -> list[Run]:
        if field_name is not None:
            rows = self._con.execute(
                "SELECT * FROM runs WHERE field_name = ? ORDER BY run_id",
                [field_name],
            ).fetchall()
        else:
            rows = self._con.execute("SELECT * FROM runs ORDER BY run_id").fetchall()
        return [self._run_from_row(r) for r in rows]

    @staticmethod
    def _run_from_row(row: tuple) -> Run:
        return Run(
            run_id=row[0],
            field_name=row[1],
            phase=row[2],
            parent_run_id=row[3],
            whitespace_id=row[4],
            query_manifest_hash=row[5],
            status=row[6],
            started_at=row[7],
            finished_at=row[8],
        )

    def add_run_works(self, run_id: str, work_ids: list[str]) -> None:
        if not work_ids:
            return
        self._con.executemany(
            "INSERT OR IGNORE INTO run_works VALUES (?, ?)",
            [[run_id, w] for w in work_ids],
        )

    def get_run_works(self, run_id: str) -> list[str]:
        rows = self._con.execute(
            "SELECT work_id FROM run_works WHERE run_id = ? ORDER BY work_id",
            [run_id],
        ).fetchall()
        return [r[0] for r in rows]

    # -- immutable response cache (#11) --------------------------------------

    def cache_get(self, key: str) -> str | None:
        row = self._con.execute(
            "SELECT body FROM cache WHERE key = ?", [key]
        ).fetchone()
        return row[0] if row is not None else None

    def cache_put(self, key: str, api: str, url: str, body: str) -> None:
        self._con.execute(
            "INSERT OR IGNORE INTO cache VALUES (?, ?, ?, ?, ?)",
            [key, api, url, body, _now_iso()],
        )

    # -- job state for the resumable queue -----------------------------------

    def job_put(
        self,
        job_id: str,
        kind: str,
        payload: dict,
        status: str,
        run_id: str | None,
    ) -> None:
        now = _now_iso()
        self._con.execute(
            "INSERT OR REPLACE INTO jobs "
            "VALUES (?, nextval('jobs_seq'), ?, ?, ?, ?, NULL, ?, ?)",
            [job_id, kind, json.dumps(payload), status, run_id, now, now],
        )

    def job_update(
        self,
        job_id: str,
        *,
        status: str | None = None,
        checkpoint: dict | None = None,
    ) -> None:
        sets = ["updated_at = ?"]
        params: list[Any] = [_now_iso()]
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if checkpoint is not None:
            sets.append("checkpoint = ?")
            params.append(json.dumps(checkpoint))
        params.append(job_id)
        self._con.execute(f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", params)

    def job_get(self, job_id: str) -> dict | None:
        row = self._con.execute(
            "SELECT * FROM jobs WHERE job_id = ?", [job_id]
        ).fetchone()
        return self._job_from_row(row) if row is not None else None

    def activity_put(self, run_id: str, message: str) -> dict:
        """Append one activity line for a run; returns the stored row."""
        ts = _now_iso()
        seq = self._con.execute("SELECT nextval('activities_seq')").fetchone()[0]
        self._con.execute(
            "INSERT INTO activities VALUES (?, ?, ?, ?)", [run_id, seq, ts, message]
        )
        return {"run_id": run_id, "seq": seq, "ts": ts, "message": message}

    def activities_for_run(self, run_id: str, limit: int = 1000) -> list[dict]:
        """Activity lines for a run, oldest-first (last ``limit`` entries)."""
        rows = self._con.execute(
            "SELECT run_id, seq, ts, message FROM ("
            "  SELECT * FROM activities WHERE run_id = ? ORDER BY seq DESC LIMIT ?"
            ") ORDER BY seq",
            [run_id, limit],
        ).fetchall()
        return [
            {"run_id": r[0], "seq": r[1], "ts": r[2], "message": r[3]} for r in rows
        ]

    def job_for_run(self, run_id: str) -> dict | None:
        """Newest job for a run, any status (progress reporting)."""
        row = self._con.execute(
            "SELECT * FROM jobs WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
            [run_id],
        ).fetchone()
        return self._job_from_row(row) if row is not None else None

    def job_failed_for_run(self, run_id: str) -> dict | None:
        """Newest failed job for a run (retry target)."""
        row = self._con.execute(
            "SELECT * FROM jobs WHERE run_id = ? AND status = 'failed' "
            "ORDER BY seq DESC LIMIT 1",
            [run_id],
        ).fetchone()
        return self._job_from_row(row) if row is not None else None

    def jobs_pending(self) -> list[dict]:
        rows = self._con.execute(
            "SELECT * FROM jobs WHERE status IN (?, ?) ORDER BY seq",
            list(_JOB_PENDING_STATUSES),
        ).fetchall()
        return [self._job_from_row(r) for r in rows]

    @staticmethod
    def _job_from_row(row: tuple) -> dict:
        return {
            "job_id": row[0],
            "kind": row[2],
            "payload": json.loads(row[3]),
            "status": row[4],
            "run_id": row[5],
            "checkpoint": json.loads(row[6]) if row[6] is not None else None,
            "created_at": row[7],
            "updated_at": row[8],
        }

    # -- whitespace + gaps + expansions --------------------------------------

    def put_whitespace(self, w: WhitespaceCandidate) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO whitespace VALUES (?, ?, ?)",
            [w.whitespace_id, w.run_id, w.model_dump_json()],
        )

    def list_whitespace(self, run_id: str) -> list[WhitespaceCandidate]:
        rows = self._con.execute(
            "SELECT body FROM whitespace WHERE run_id = ? ORDER BY whitespace_id",
            [run_id],
        ).fetchall()
        return [WhitespaceCandidate.model_validate_json(r[0]) for r in rows]

    def put_gap(self, g: Gap) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO gaps VALUES (?, ?, ?, ?)",
            [g.gap_id, g.whitespace_id, g.zoom_run_id, g.model_dump_json()],
        )

    def list_gaps(self, zoom_run_id: str | None = None) -> list[Gap]:
        if zoom_run_id is not None:
            rows = self._con.execute(
                "SELECT body FROM gaps WHERE zoom_run_id = ? ORDER BY gap_id",
                [zoom_run_id],
            ).fetchall()
        else:
            rows = self._con.execute(
                "SELECT body FROM gaps ORDER BY gap_id"
            ).fetchall()
        return [Gap.model_validate_json(r[0]) for r in rows]

    def put_expansion(self, e: IdeonomyExpansion) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO expansions VALUES (?, ?, ?)",
            [e.gap_id, e.attempt, e.model_dump_json()],
        )

    def list_expansions(self, gap_id: str) -> list[IdeonomyExpansion]:
        rows = self._con.execute(
            "SELECT body FROM expansions WHERE gap_id = ? ORDER BY attempt",
            [gap_id],
        ).fetchall()
        return [IdeonomyExpansion.model_validate_json(r[0]) for r in rows]
