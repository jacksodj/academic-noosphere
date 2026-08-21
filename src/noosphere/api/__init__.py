"""Localhost API layer: application state wiring and the /api router.

Integrator contract (server.py):
- build the state:      ``app.state.noosphere = AppState.build(data_dir, settings)``
- mount the router:     ``app.include_router(router)``  (routes carry the /api prefix)
- start the worker:     ``asyncio.create_task(state.queue.worker(handlers))``
Routes read the state exclusively from ``request.app.state.noosphere``.
"""

from noosphere.api.routes import router
from noosphere.api.state import AppState, EventBus, load_settings, save_settings

__all__ = ["AppState", "EventBus", "load_settings", "save_settings", "router"]
