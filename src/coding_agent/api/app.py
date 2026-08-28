"""FastAPI application factory with process-local security state."""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from coding_agent.api.dependencies import ApiDependencies
from coding_agent.api.routes import router
from coding_agent.api.websocket import router as websocket_router
from coding_agent.runtime.coordinator import RunCoordinator
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore


def create_app(
    store: SQLiteStore,
    coordinator: RunCoordinator | None,
    public_config: Mapping[str, object],
    *,
    event_publisher: EventPublisher | None = None,
    server_port: int = 8000,
    development_origin: str | None = None,
    web_dist: Path | None = None,
    recover_on_startup: bool = True,
) -> FastAPI:
    """Build one independently injectable app without module-global mutable state."""
    dependencies = ApiDependencies(
        store=store,
        coordinator=coordinator,
        event_publisher=(
            event_publisher
            or (coordinator.event_publisher if coordinator is not None else EventPublisher())
        ),
        public_config=dict(public_config),
        server_port=server_port,
        development_origin=development_origin,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if recover_on_startup:
            store.recover_interrupted_runs()
        yield

    app = FastAPI(lifespan=lifespan)
    app.state.api_dependencies = dependencies
    if dependencies.development_origin is not None:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[dependencies.development_origin],
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "X-CSRF-Token"],
        )

    @app.middleware("http")
    async def enforce_loopback_host(request: Request, call_next):
        if request.headers.get("host") not in dependencies.allowed_hosts:
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={
                    "detail": {
                        "code": "HOST_FORBIDDEN",
                        "message": "request Host must match the local service endpoint",
                    }
                },
            )
        return await call_next(request)

    app.include_router(router)
    app.include_router(websocket_router)
    if web_dist is not None:
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


__all__ = ["create_app"]
