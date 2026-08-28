"""Explicit process-local dependencies and browser request guards."""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from fastapi import HTTPException, Request, status

from coding_agent.runtime.coordinator import RunCoordinator
from coding_agent.runtime.publisher import EventPublisher
from coding_agent.storage.sqlite import SQLiteStore

PROCESS_TOKEN_HEADER = "X-CSRF-Token"


@dataclass(slots=True)
class ApiDependencies:
    """State shared by one FastAPI application process, never by module globals."""

    store: SQLiteStore
    coordinator: RunCoordinator | None
    event_publisher: EventPublisher
    public_config: Mapping[str, object]
    server_port: int
    development_origin: str | None = None
    process_token: str = field(default_factory=lambda: secrets.token_urlsafe(32), repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.server_port <= 65_535:
            raise ValueError("server port must be between 1 and 65535")
        if self.development_origin is not None:
            self.development_origin = _canonical_development_origin(self.development_origin)

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"})

    @property
    def allowed_origins(self) -> frozenset[str]:
        origins = {
            f"http://127.0.0.1:{self.server_port}",
            f"http://localhost:{self.server_port}",
        }
        if self.development_origin is not None:
            origins.add(self.development_origin)
        return frozenset(origins)

    def token_matches(self, candidate: str | None) -> bool:
        return candidate is not None and hmac.compare_digest(candidate, self.process_token)


def get_api_dependencies(request: Request) -> ApiDependencies:
    return request.app.state.api_dependencies


def require_process_token(request: Request) -> None:
    dependencies = get_api_dependencies(request)
    if not dependencies.token_matches(request.headers.get(PROCESS_TOKEN_HEADER)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PROCESS_TOKEN_INVALID",
                "message": "process token is missing, invalid, or expired",
            },
        )
    origin = request.headers.get("origin")
    if origin is not None and origin not in dependencies.allowed_origins:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ORIGIN_FORBIDDEN", "message": "request origin is not allowed"},
        )


def _canonical_development_origin(origin: str) -> str:
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or parsed.port is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("development origin must be an explicit loopback HTTP origin")
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"


__all__ = [
    "ApiDependencies",
    "PROCESS_TOKEN_HEADER",
    "get_api_dependencies",
    "require_process_token",
]
