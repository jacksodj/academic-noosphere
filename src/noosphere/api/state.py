"""Application state for the localhost API.

``AppState`` owns the long-lived objects (sidecar, graph, queue, settings,
spend meter, event bus). The integrator builds one per process and stores it
on ``app.state.noosphere``; routes reach it via ``request.app.state.noosphere``.

Settings persistence: non-credential fields round-trip through
``<data_dir>/settings.json``, merged over ``Settings`` defaults on load.
Credentials never touch this file — they live in the Keychain / env
(``noosphere.config.get_credential``).
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, AsyncIterator

from noosphere.config import CRED_KEYS, Settings
from noosphere.graph import GraphStore
from noosphere.llm.bedrock import SpendMeter
from noosphere.pipeline.queue import JobQueue
from noosphere.sidecar import Sidecar

SETTINGS_FILE = "settings.json"

# Belt-and-braces: anything credential-shaped is stripped from settings JSON
# in both directions, even if a future Settings grows an overlapping name.
_CREDENTIAL_KEYS = frozenset(CRED_KEYS) | frozenset(CRED_KEYS.values())


class EventBus:
    """Asyncio fan-out bus: every attached subscriber sees every event.

    ``publish`` must be called from the event loop thread (route handlers and
    job handlers both run there). Events published while nobody is subscribed
    are dropped — the SSE stream is a live feed, not a log.
    """

    def __init__(self) -> None:
        self._queues: set[asyncio.Queue[dict]] = set()

    def publish(self, event: dict) -> None:
        for q in tuple(self._queues):
            q.put_nowait(event)

    def attach(self) -> asyncio.Queue[dict]:
        """Register a subscriber queue (low-level; pair with ``detach``)."""
        q: asyncio.Queue[dict] = asyncio.Queue()
        self._queues.add(q)
        return q

    def detach(self, q: asyncio.Queue[dict]) -> None:
        self._queues.discard(q)

    def subscribe(self) -> AsyncIterator[dict]:
        """Async iterator of events; registration is eager (no missed events
        between calling subscribe() and the first ``anext``)."""
        q = self.attach()

        async def _events() -> AsyncIterator[dict]:
            try:
                while True:
                    yield await q.get()
            finally:
                self.detach(q)

        return _events()


SURVEY_STAGES = ("seeds", "expand", "relevance", "persist")


def progress_summary(checkpoint: dict[str, Any] | None) -> dict[str, Any]:
    """Compact, UI-safe view of a survey job checkpoint (no id lists)."""
    state = checkpoint or {}
    done = [s for s in SURVEY_STAGES if s in (state.get("done") or [])]
    current = next((s for s in SURVEY_STAGES if s not in done), None)
    return {
        "stages": list(SURVEY_STAGES),
        "done": done,
        "current": current,
        "counts": {
            "seeds": len(state.get("seed_ids") or []),
            "candidates": len(state.get("candidate_ids") or []),
            "kept": len(state.get("kept_ids") or []),
        },
        "error": state.get("error"),
    }


def settings_path(data_dir: Path) -> Path:
    return Path(data_dir) / SETTINGS_FILE


def settings_to_dict(settings: Settings) -> dict[str, Any]:
    """Non-credential settings fields as a JSON-safe dict."""
    return {k: v for k, v in asdict(settings).items() if k not in _CREDENTIAL_KEYS}


def merge_settings(base: Settings, data: dict[str, Any]) -> Settings:
    """New Settings with known, non-credential keys of ``data`` applied."""
    allowed = {f.name for f in fields(Settings)} - _CREDENTIAL_KEYS
    updates = {k: v for k, v in data.items() if k in allowed}
    return replace(base, **updates)


def load_settings(data_dir: Path, base: Settings | None = None) -> Settings:
    """Merge persisted settings.json (if readable) over ``base``/defaults."""
    merged = base if base is not None else Settings()
    path = settings_path(data_dir)
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return merged
    if not isinstance(data, dict):
        return merged
    return merge_settings(merged, data)


def save_settings(data_dir: Path, settings: Settings) -> None:
    path = settings_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings_to_dict(settings), indent=2, sort_keys=True))


@dataclass
class AppState:
    data_dir: Path
    settings: Settings
    sidecar: Sidecar
    graph: GraphStore
    queue: JobQueue
    meter: SpendMeter
    bus: EventBus

    @classmethod
    def build(cls, data_dir: Path, settings: Settings) -> "AppState":
        data_dir = Path(data_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        merged = load_settings(data_dir, settings)
        sidecar = Sidecar(data_dir / "sidecar.duckdb")
        graph = GraphStore(data_dir / "graph")
        graph.init_schema()
        bus = EventBus()
        # Live progress: every checkpoint save becomes an SSE event.
        queue = JobQueue(
            sidecar,
            on_checkpoint=lambda job_id, run_id, data: bus.publish(
                {
                    "type": "progress",
                    "run_id": run_id,
                    "progress": progress_summary(data),
                }
            ),
        )
        return cls(
            data_dir=data_dir,
            settings=merged,
            sidecar=sidecar,
            graph=graph,
            queue=queue,
            meter=SpendMeter(),
            bus=bus,
        )

    def update_settings(self, data: dict[str, Any]) -> Settings:
        """Apply a settings patch and persist to settings.json."""
        self.settings = merge_settings(self.settings, data)
        save_settings(self.data_dir, self.settings)
        return self.settings

    def close(self) -> None:
        self.sidecar.close()
