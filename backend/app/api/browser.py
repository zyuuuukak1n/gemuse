"""Browser Take Control, /shared_files transfer, audit log, and authenticated noVNC proxy APIs."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
import uuid
from typing import Any

import httpx
import websockets
from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.browser_ctrl import BrowserController, BrowserRunResult
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.models import BrowserAuditLog
from app.db.session import get_db_session

logger = get_logger(__name__)

router = APIRouter(prefix="/api/browser", tags=["browser"])
ws_router = APIRouter(prefix="/api/browser", tags=["browser-ws"])


class BrowserRunRequest(BaseModel):
    goal: str = Field(..., min_length=1, max_length=4000)
    start_url: str | None = None
    max_steps: int = Field(default=10, ge=1, le=50)
    job_id: uuid.UUID | None = None


class PauseRequest(BaseModel):
    reason: str = "ユーザーによる手動介入 (Take Control)"
    operator: str = "user"
    job_id: uuid.UUID | None = None


class ResumeRequest(BaseModel):
    operator: str = "user"
    job_id: uuid.UUID | None = None


def _get_browser_controller(request: Request) -> BrowserController:
    ctrl = getattr(request.app.state, "browser_controller", None)
    if ctrl is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="BrowserController is not initialized",
        )
    return ctrl


@router.post("/sessions/{session_id}/run", response_model=BrowserRunResult)
async def run_browser_task(
    session_id: str,
    payload: BrowserRunRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> BrowserRunResult:
    """Execute a Computer Use browser task on an isolated sandbox context."""
    ctrl = _get_browser_controller(request)
    try:
        return await ctrl.run_task(
            db,
            goal=payload.goal,
            start_url=payload.start_url,
            session_id=session_id,
            max_steps=payload.max_steps,
            job_id=payload.job_id,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Browser sandbox unavailable: {exc}",
        ) from exc


@router.post("/sessions/{session_id}/pause")
async def pause_browser_session(
    session_id: str,
    payload: PauseRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Activate Take Control pause lock on a browser session."""
    ctrl = _get_browser_controller(request)
    return await ctrl.pause_session(
        db,
        session_id=session_id,
        reason=payload.reason,
        operator=payload.operator,
        job_id=payload.job_id,
    )


@router.post("/sessions/{session_id}/resume")
async def resume_browser_session(
    session_id: str,
    payload: ResumeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Release Take Control pause lock and hand control back to Gemuse."""
    ctrl = _get_browser_controller(request)
    return await ctrl.resume_session(
        db,
        session_id=session_id,
        operator=payload.operator,
        job_id=payload.job_id,
    )


@router.post("/sessions/{session_id}/emergency-stop")
async def emergency_stop_browser_session(
    session_id: str,
    payload: ResumeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Immediately stop automation and destroy the isolated browser context."""
    ctrl = _get_browser_controller(request)
    return await ctrl.emergency_stop(
        db,
        session_id=session_id,
        operator=payload.operator,
        job_id=payload.job_id,
    )


@router.get("/sessions/{session_id}/status")
async def get_browser_session_status(session_id: str, request: Request) -> dict[str, Any]:
    """Return pause lock state and current page metadata if active."""
    ctrl = _get_browser_controller(request)
    paused = await ctrl.is_paused(session_id)
    stopped = await ctrl.is_stopped(session_id)
    state_info: dict[str, Any] = {}
    try:
        snapshot = await ctrl.sandbox.get_state(session_id)
        state_info = {
            "url": snapshot.get("url", ""),
            "title": snapshot.get("title", ""),
        }
    except Exception:
        state_info = {"active": False}
    return {
        "session_id": session_id,
        "paused": bool(paused),
        "pause_info": paused,
        "stopped": stopped,
        "novnc_url": ctrl.novnc_path_for(session_id),
        "downloads": ctrl.list_downloads(session_id),
        **state_info,
    }


@router.post("/upload")
async def upload_shared_file(
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Save an uploaded file into /shared_files/uploads/ for browser attachment."""
    ctrl = _get_browser_controller(request)
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")
    content = await file.read()
    try:
        saved_path = ctrl.save_upload(file.filename, content)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {
        "filename": saved_path.name,
        "size_bytes": len(content),
        "shared_path": str(saved_path),
    }


@router.get("/uploads")
async def list_shared_uploads(request: Request) -> dict[str, Any]:
    ctrl = _get_browser_controller(request)
    return {"files": ctrl.list_uploads()}


@router.get("/downloads/{session_id}")
async def list_session_downloads(session_id: str, request: Request) -> dict[str, Any]:
    ctrl = _get_browser_controller(request)
    return {"session_id": session_id, "files": ctrl.list_downloads(session_id)}


@router.get("/downloads/{session_id}/{filename}")
async def fetch_session_download(session_id: str, filename: str, request: Request) -> FileResponse:
    """Download an artifact saved in /shared_files/downloads/{session_id}/."""
    ctrl = _get_browser_controller(request)
    try:
        file_path = ctrl.get_download_path(session_id, filename)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FileResponse(path=file_path, filename=file_path.name)


@router.get("/audit-logs")
async def list_browser_audit_logs(
    session_id: str | None = Query(default=None),
    job_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    db: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Query persistent BrowserAuditLog records."""
    stmt = select(BrowserAuditLog).order_by(desc(BrowserAuditLog.created_at)).limit(limit)
    if session_id:
        stmt = stmt.where(BrowserAuditLog.session_id == session_id)
    if job_id is not None:
        stmt = stmt.where(BrowserAuditLog.job_id == job_id)
    res = await db.execute(stmt)
    rows = res.scalars().all()
    return {
        "items": [
            {
                "id": str(row.id),
                "job_id": str(row.job_id) if row.job_id else None,
                "session_id": row.session_id,
                "action": row.action,
                "target_url": row.target_url,
                "screenshot_path": row.screenshot_path,
                "operator": row.operator,
                "details": row.details,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]
    }


@router.post("/artifacts/cleanup")
async def cleanup_browser_artifacts(
    request: Request,
    ttl_seconds: int | None = Query(default=None, ge=0),
) -> dict[str, Any]:
    """Remove expired browser screenshots and download artifacts beyond TTL."""
    ctrl = _get_browser_controller(request)
    removed = ctrl.cleanup_expired_artifacts(ttl_seconds=ttl_seconds)
    return {"removed_count": removed}


@router.get("/vnc/{session_id}/{asset_path:path}")
async def proxy_novnc_http(
    session_id: str,
    asset_path: str,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> Response:
    """Authenticated HTTP reverse proxy serving noVNC static assets from gemuse-browser."""
    target_path = asset_path.lstrip("/") or "vnc.html"
    if ".." in target_path:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid asset path")

    upstream_url = f"{settings.BROWSER_NOVNC_URL.rstrip('/')}/{target_path}"
    query = str(request.url.query or "").strip()
    if query:
        upstream_url = f"{upstream_url}?{query}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            upstream_resp = await client.get(upstream_url)
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"noVNC upstream unavailable: {exc}",
        ) from exc

    content_type = upstream_resp.headers.get("content-type", "application/octet-stream")
    return Response(
        content=upstream_resp.content,
        status_code=upstream_resp.status_code,
        media_type=content_type,
    )


def _issue_vnc_ticket(secret: str, session_id: str, ttl_seconds: int = 300) -> tuple[str, int]:
    expires_at = int(time.time()) + ttl_seconds
    msg = f"vnc:{session_id}:{expires_at}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{expires_at}.{sig}", expires_at


def _verify_vnc_ticket(secret: str, session_id: str, raw_ticket: str) -> bool:
    if not raw_ticket or "." not in raw_ticket or not secret:
        return False
    exp_str, provided_sig = raw_ticket.split(".", 1)
    try:
        expires_at = int(exp_str)
    except ValueError:
        return False
    if int(time.time()) > expires_at:
        return False
    msg = f"vnc:{session_id}:{expires_at}".encode("utf-8")
    expected_sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(provided_sig, expected_sig)


@router.post("/sessions/{session_id}/vnc-ticket")
async def create_vnc_ticket(
    session_id: str,
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Issue a short-lived, session-scoped HMAC ticket for direct WebSocket noVNC upgrade."""
    ticket, expires_at = _issue_vnc_ticket(settings.APPROVAL_HMAC_SECRET, session_id)
    return {
        "session_id": session_id,
        "ticket": ticket,
        "expires_at": expires_at,
    }


def _is_ws_authorized(websocket: WebSocket, settings: Settings, session_id: str) -> bool:
    ticket_param = websocket.query_params.get("ticket", "").strip()
    if ticket_param and _verify_vnc_ticket(settings.APPROVAL_HMAC_SECRET, session_id, ticket_param):
        return True

    auth_header = websocket.headers.get("authorization", "").strip()
    token_param = websocket.query_params.get("token", "").strip()
    candidate = ""
    if auth_header.lower().startswith("bearer "):
        candidate = auth_header[7:].strip()
    elif token_param:
        candidate = token_param
    if not candidate or not settings.API_SECRET_TOKEN:
        return False
    return hmac.compare_digest(candidate, settings.API_SECRET_TOKEN)


@ws_router.websocket("/vnc/{session_id}/websockify")
async def proxy_novnc_websocket(websocket: WebSocket, session_id: str) -> None:
    """Authenticated WebSocket proxy forwarding noVNC RFB frames to gemuse-browser."""
    settings: Settings = getattr(websocket.app.state, "settings", get_settings())
    if not _is_ws_authorized(websocket, settings, session_id):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    subprotocols = websocket.headers.get("sec-websocket-protocol", "")
    selected_subprotocol = "binary" if "binary" in subprotocols else None
    await websocket.accept(subprotocol=selected_subprotocol)

    novnc_http = settings.BROWSER_NOVNC_URL.rstrip("/")
    novnc_ws = novnc_http.replace("https://", "wss://").replace("http://", "ws://")
    upstream_ws_url = f"{novnc_ws}/websockify"

    try:
        async with websockets.connect(
            upstream_ws_url,
            subprotocols=["binary"] if selected_subprotocol else None,
        ) as upstream:

            async def _client_to_upstream() -> None:
                while True:
                    msg = await websocket.receive()
                    if msg["type"] == "websocket.disconnect":
                        break
                    if msg.get("bytes") is not None:
                        await upstream.send(msg["bytes"])
                    elif msg.get("text") is not None:
                        await upstream.send(msg["text"])

            async def _upstream_to_client() -> None:
                async for data in upstream:
                    if isinstance(data, bytes):
                        await websocket.send_bytes(data)
                    else:
                        await websocket.send_text(data)

            await asyncio.gather(_client_to_upstream(), _upstream_to_client())
    except WebSocketDisconnect:
        logger.info("noVNC WebSocket disconnected session_id=%s", session_id)
    except Exception as exc:
        logger.warning("noVNC WebSocket proxy error session_id=%s: %s", session_id, exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

