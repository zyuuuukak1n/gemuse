"""Twilio voice webhooks (TwiML / Status) and Media Streams WebSocket endpoint."""

from __future__ import annotations

import hashlib
import hmac
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import quoteattr

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, WebSocket, status
from twilio.request_validator import RequestValidator

from app.agent.voice_bridge import TwilioVoiceBridgeSession, VoiceBridgeRegistry
from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.session import get_session_factory

logger = get_logger(__name__)

router = APIRouter(prefix="/api/voice", tags=["voice"])


def sign_stream_token(auth_token: str, call_ref: str) -> str:
    """Create an HMAC-SHA256 signature binding a Media Stream WebSocket to call_ref."""
    return hmac.new(
        auth_token.encode("utf-8"),
        f"stream:{call_ref}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


async def _verify_twilio_http_signature(
    request: Request,
    settings: Settings,
) -> dict[str, str]:
    """Validate Twilio X-Twilio-Signature against request URL (or PUBLIC_BASE_URL) + form params."""
    if not settings.TWILIO_AUTH_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Twilio voice integration is not configured",
        )
    signature = request.headers.get("X-Twilio-Signature", "").strip()
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing X-Twilio-Signature header",
        )

    form_data = await request.form()
    params = {str(k): str(v) for k, v in form_data.items()}
    validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)

    candidate_urls = [str(request.url)]
    if settings.PUBLIC_BASE_URL:
        base = settings.PUBLIC_BASE_URL.rstrip("/")
        path_with_query = request.url.path
        if request.url.query:
            path_with_query = f"{path_with_query}?{request.url.query}"
        candidate_urls.insert(0, f"{base}{path_with_query}")

    for url in candidate_urls:
        if validator.validate(url, params, signature):
            return params

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Invalid Twilio webhook signature",
    )


def _get_voice_registry(request_or_ws: Request | WebSocket) -> VoiceBridgeRegistry:
    registry = getattr(request_or_ws.app.state, "voice_bridge_registry", None)
    if registry is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VoiceBridgeRegistry is not initialized",
        )
    return registry


@router.post("/twiml")
async def twilio_twiml_webhook(
    request: Request,
    call_ref: str = Query(default=""),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Verify Twilio webhook signature and return TwiML linking the call to Media Streams WS."""
    await _verify_twilio_http_signature(request, settings)

    base = (settings.PUBLIC_BASE_URL or str(request.base_url)).rstrip("/")
    ws_base = base.replace("https://", "wss://").replace("http://", "ws://")
    stream_token = sign_stream_token(settings.TWILIO_AUTH_TOKEN, call_ref)
    stream_url = (
        f"{ws_base}/api/voice/stream?call_ref={quote(call_ref)}&stream_token={quote(stream_token)}"
    )

    xml_body = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        "<Response>\n"
        "  <Connect>\n"
        f"    <Stream url={quoteattr(stream_url)} />\n"
        "  </Connect>\n"
        "</Response>\n"
    )
    return Response(content=xml_body, media_type="application/xml")


@router.post("/status")
async def twilio_status_webhook(
    request: Request,
    call_ref: str = Query(default=""),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """Receive Twilio call lifecycle callbacks after verifying Twilio signature."""
    params = await _verify_twilio_http_signature(request, settings)
    logger.info(
        "Twilio call status update call_ref=%s call_sid=%s status=%s",
        call_ref,
        params.get("CallSid", ""),
        params.get("CallStatus", ""),
    )
    return {
        "received": True,
        "call_ref": call_ref,
        "call_sid": params.get("CallSid", ""),
        "call_status": params.get("CallStatus", ""),
    }


@router.websocket("/stream")
async def twilio_media_stream(websocket: WebSocket) -> None:
    """Bidirectional WebSocket handling Twilio Media Streams <-> Gemini Live audio."""
    settings: Settings = getattr(websocket.app.state, "settings", get_settings())
    call_ref = websocket.query_params.get("call_ref", "")
    stream_token = websocket.query_params.get("stream_token", "")

    if settings.TWILIO_AUTH_TOKEN:
        expected = sign_stream_token(settings.TWILIO_AUTH_TOKEN, call_ref)
        twilio_sig = websocket.headers.get("X-Twilio-Signature", "").strip()
        token_valid = bool(stream_token) and hmac.compare_digest(stream_token, expected)
        sig_valid = False
        if twilio_sig:
            validator = RequestValidator(settings.TWILIO_AUTH_TOKEN)
            ws_url = str(websocket.url)
            sig_valid = validator.validate(ws_url, {}, twilio_sig)
            if not sig_valid and settings.PUBLIC_BASE_URL:
                base = settings.PUBLIC_BASE_URL.rstrip("/").replace("https://", "wss://").replace("http://", "ws://")
                q = f"?{websocket.url.query}" if websocket.url.query else ""
                sig_valid = validator.validate(f"{base}{websocket.url.path}{q}", {}, twilio_sig)
        if not (token_valid or sig_valid):
            await websocket.close(code=1008, reason="Unauthorized stream connection")
            return

    await websocket.accept()
    registry: VoiceBridgeRegistry = getattr(websocket.app.state, "voice_bridge_registry")
    memory_service = getattr(websocket.app.state, "memory_service", None)
    call_ctx = registry.get_call_context(call_ref) if call_ref else None
    instruction = (
        call_ctx.system_instruction
        if call_ctx and call_ctx.system_instruction
        else "あなたはGemuseの音声通話エージェントです。簡潔かつ丁寧に応対してください。"
    )

    try:
        live_session = await registry.open_live_session(instruction)
    except Exception as exc:
        logger.error("Failed to open Gemini Live session: %s", exc)
        await websocket.close(code=1011, reason="Gemini Live unavailable")
        return

    session_factory = getattr(websocket.app.state, "session_factory", None) or get_session_factory()
    bridge = TwilioVoiceBridgeSession(
        live_session=live_session,
        call_context=call_ctx,
        memory_service=memory_service,
        session_factory=session_factory,
    )
    await bridge.run(websocket)
    try:
        await websocket.close(code=1000)
    except Exception:
        pass

