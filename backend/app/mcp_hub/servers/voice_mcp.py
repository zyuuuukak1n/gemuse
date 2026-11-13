"""VoiceMCPConnector: guarded Twilio outbound voice call tool using ApprovalService."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from twilio.rest import Client as TwilioRestClient

from app.agent.voice_bridge import VoiceBridgeRegistry, validate_e164_phone
from app.core.logging import get_logger
from app.core.security import ApprovalRequiredError, ApprovalService, wrap_untrusted_data
from app.mcp_hub.base import (
    BaseMCPConnector,
    MCPTool,
    approval_already_verified_ctx,
)

logger = get_logger(__name__)


class VoiceMCPConnector(BaseMCPConnector):
    """MCP connector for initiating live AI-mediated phone calls via Twilio."""

    connector_name = "voice"

    def __init__(
        self,
        *,
        account_sid: str = "",
        auth_token: str = "",
        from_number: str = "",
        public_base_url: str = "",
        bridge_registry: VoiceBridgeRegistry,
        approval_service: ApprovalService,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        twilio_client: Any | None = None,
    ) -> None:
        self._account_sid = account_sid.strip()
        self._auth_token = auth_token.strip()
        self._from_number = from_number.strip()
        self._public_base_url = public_base_url.strip().rstrip("/")
        self._bridge_registry = bridge_registry
        self._approval_service = approval_service
        self._session_factory = session_factory
        self._twilio_client = twilio_client

    def is_configured(self) -> bool:
        if self._twilio_client is not None:
            return bool(self._from_number)
        return bool(self._account_sid and self._auth_token and self._from_number)

    def _get_twilio_client(self) -> Any:
        if self._twilio_client is not None:
            return self._twilio_client
        if not (self._account_sid and self._auth_token and self._from_number):
            raise RuntimeError("Twilio credentials are not configured")
        return TwilioRestClient(self._account_sid, self._auth_token)

    async def voice_make_call(
        self,
        to_number: str,
        purpose: str,
        system_instruction: str = "",
        approval_token: str = "",
    ) -> str:
        """
        Place an outbound phone call via Twilio connected to Gemini Live API.

        Physical approval lock is always enforced before dialing.
        """
        if not approval_already_verified_ctx.get():
            if self._session_factory is None:
                raise ApprovalRequiredError(
                    "Cannot verify approval_token for voice_make_call without DB session",
                    action_type="voice_make_call",
                )
            async with self._session_factory() as db:
                await self._approval_service.verify_and_consume_by_token(
                    db,
                    action_type="voice_make_call",
                    raw_token=approval_token,
                )

        normalized_to = validate_e164_phone(to_number)
        call_ref = f"call-{uuid.uuid4().hex[:16]}"
        instruction = system_instruction.strip() or (
            "あなたはGemuseの音声通話エージェントです。丁寧かつ簡潔な日本語で話し、"
            f"次の目的を達成してください: {purpose}"
        )
        ctx = self._bridge_registry.register_call(
            call_ref=call_ref,
            to_number=normalized_to,
            purpose=purpose,
            system_instruction=instruction,
        )

        client = self._get_twilio_client()
        base_url = self._public_base_url or "https://localhost:8005"
        twiml_url = f"{base_url}/api/voice/twiml?call_ref={call_ref}"
        status_url = f"{base_url}/api/voice/status?call_ref={call_ref}"

        call = await asyncio.to_thread(
            client.calls.create,
            to=normalized_to,
            from_=self._from_number,
            url=twiml_url,
            status_callback=status_url,
            status_callback_event=["initiated", "ringing", "answered", "completed"],
        )
        call_sid = str(getattr(call, "sid", "") or "")
        ctx.call_sid = call_sid
        logger.info("Initiated Twilio outbound call call_ref=%s call_sid=%s", call_ref, call_sid)

        result = {
            "status": "initiated",
            "call_sid": call_sid,
            "call_ref": call_ref,
            "to": normalized_to,
            "purpose": purpose,
        }
        return wrap_untrusted_data("voice:call", str(result))

    def get_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="voice_make_call",
                description=(
                    "Place an outbound voice call via Twilio and handle the conversation "
                    "using Gemini Live API (requires E.164 phone number and approval_token)."
                ),
                handler=self.voice_make_call,
                requires_approval=True,
                source_type="voice",
                already_wrapped=True,
            )
        ]

