"""Localhost API for the Tauri shell / React SPA.

Contract from ticket #3: bind to 127.0.0.1 on a random free port, require a
per-launch bearer token on every request, and print both to stdout as one JSON
line so the spawning shell (Tauri, or a dev browser session) can connect.
"""

import json
import secrets
import socket

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from noosphere import __version__

app = FastAPI(title="noosphere-core", version=__version__)

_token: str = ""


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
