"""Thin REST routes for browser bootstrap and durable session reads."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from coding_agent.api.dependencies import (
    ApiDependencies,
    get_api_dependencies,
    require_process_token,
)
from coding_agent.api.schemas import (
    BootstrapDto,
    CreateSessionRequest,
    DirectoryEntryDto,
    DirectoryListingDto,
    HealthDto,
    SessionDto,
    SessionSnapshotDto,
)
from coding_agent.core.errors import StoreError

router = APIRouter(prefix="/api")


@router.get("/health", response_model=HealthDto)
def health() -> HealthDto:
    return HealthDto()


@router.get("/bootstrap", response_model=BootstrapDto)
def bootstrap(
    request: Request,
    response: Response,
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> BootstrapDto:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    scheme = "wss" if request.url.scheme == "https" else "ws"
    return BootstrapDto(
        csrf_token=dependencies.process_token,
        websocket_url=f"{scheme}://{request.headers['host']}/api/ws",
    )


@router.get("/config/public", response_model=dict[str, Any])
def public_config(
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> dict[str, object]:
    return _redact_mapping(dependencies.public_config)


@router.get("/directories", response_model=DirectoryListingDto)
def list_directories(path: str = Query(...)) -> DirectoryListingDto:
    canonical = _canonical_accessible_directory(path)
    try:
        children = sorted(
            (
                DirectoryEntryDto(name=child.name, path=str(child.resolve(strict=True)))
                for child in canonical.iterdir()
                if child.is_dir() and os.access(child, os.R_OK | os.X_OK)
            ),
            key=lambda child: (child.name.casefold(), child.name),
        )
    except OSError as error:
        raise _invalid_directory() from error
    return DirectoryListingDto(path=str(canonical), directories=children)


@router.post(
    "/sessions",
    response_model=SessionDto,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_process_token)],
)
def create_session(
    body: CreateSessionRequest,
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> SessionDto:
    canonical = _canonical_accessible_directory(body.workspace)
    session = dependencies.store.create_session(str(canonical), body.title)
    return SessionDto.from_domain(session)


@router.get("/sessions", response_model=list[SessionDto])
def list_sessions(
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> list[SessionDto]:
    return [SessionDto.from_domain(session) for session in dependencies.store.list_sessions()]


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_process_token)],
)
def delete_session(
    session_id: str,
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> Response:
    try:
        dependencies.store.delete_session(session_id)
    except StoreError as error:
        raise _store_http_error(error) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions/{session_id}/snapshot", response_model=SessionSnapshotDto)
def session_snapshot(
    session_id: str,
    dependencies: ApiDependencies = Depends(get_api_dependencies),
) -> SessionSnapshotDto:
    try:
        snapshot = dependencies.store.load_snapshot(session_id)
    except StoreError as error:
        raise _store_http_error(error) from error
    return SessionSnapshotDto.from_domain(snapshot)


def _canonical_accessible_directory(raw_path: str) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        raise _invalid_directory()
    try:
        canonical = path.resolve(strict=True)
    except OSError as error:
        raise _invalid_directory() from error
    if not canonical.is_dir() or not os.access(canonical, os.R_OK | os.X_OK):
        raise _invalid_directory()
    try:
        next(canonical.iterdir(), None)
    except OSError as error:
        raise _invalid_directory() from error
    return canonical


def _invalid_directory() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "code": "INVALID_DIRECTORY",
            "message": "path must be an accessible absolute directory",
        },
    )


def _store_http_error(error: StoreError) -> HTTPException:
    status_code = status.HTTP_404_NOT_FOUND if error.code.endswith("_NOT_FOUND") else 409
    return HTTPException(
        status_code=status_code,
        detail={"code": error.code, "message": str(error)},
    )


def _redact_mapping(value: object) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            if _secret_key(name):
                continue
            public_item = _redact_mapping(item)
            if public_item == {}:
                continue
            redacted[name] = public_item
        return redacted
    if isinstance(value, list | tuple):
        return [_redact_mapping(item) for item in value]
    return value


def _secret_key(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    return (
        "secret" in normalized
        or "authorization" in normalized
        or "api_key" in normalized
        or normalized == "token"
        or normalized.endswith("_token")
    )


__all__ = ["router"]
