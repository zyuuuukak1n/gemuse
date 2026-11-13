"""Tests for Step 3 Twilio Voice Bridge (pure-Python u-law/PCM transcoding, approval gate, signatures, WS)."""

import asyncio
import base64
import json
import math
from array import array
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from twilio.request_validator import RequestValidator

from app.agent.voice_bridge import (
    GeminiLiveOutputChunk,
    VoiceBridgeRegistry,
    gemini_pcm16_to_twilio_ulaw_b64,
    pcm16_to_ulaw,
    resample_pcm16,
    twilio_ulaw_b64_to_gemini_pcm16,
    ulaw_to_pcm16,
    validate_e164_phone,
)
from app.core.config import Settings
from app.core.security import ApprovalRequiredError, ApprovalService
from app.db.models import EpisodeMemory
from app.db.session import get_db_session
from app.main import create_app
from app.mcp_hub.base import ToolRegistry
from app.mcp_hub.servers.voice_mcp import VoiceMCPConnector
from tests.conftest import TEST_HMAC_SECRET


class FakeGeminiLiveSession:
    """Deterministic fake Gemini Live session capturing inbound PCM and yielding audio + transcript."""

    def __init__(self, responses: list[GeminiLiveOutputChunk]) -> None:
        self.received_audio: list[bytes] = []
        self.received_prompts: list[str] = []
        self.responses = list(responses)
        self.closed = False
        self.audio_event = asyncio.Event()
        self.stop_event = asyncio.Event()

    async def send_audio_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> None:
        self.received_audio.append(pcm_bytes)
        self.audio_event.set()

    async def send_text_prompt(self, text: str) -> None:
        self.received_prompts.append(text)

    async def receive_chunks(self):
        await self.audio_event.wait()
        for chunk in self.responses:
            yield chunk
        await self.stop_event.wait()

    async def close(self) -> None:
        self.stop_event.set()
        self.closed = True


def _make_sine_pcm16(sample_rate: int, duration_ms: int = 20, freq_hz: float = 440.0) -> bytes:
    n_samples = int(sample_rate * duration_ms / 1000)
    vals = array(
        "h",
        (
            int(12000 * math.sin(2.0 * math.pi * freq_hz * i / sample_rate))
            for i in range(n_samples)
        ),
    )
    return vals.tobytes()


def test_pure_python_ulaw_and_resampling_transcoding() -> None:
    # 1. 8kHz PCM16 -> u-law -> PCM16 preserves signal polarity & approximates amplitude
    pcm8k = _make_sine_pcm16(8000, duration_ms=20)
    ulaw = pcm16_to_ulaw(pcm8k)
    assert len(ulaw) == len(pcm8k) // 2
    decoded = ulaw_to_pcm16(ulaw)
    assert len(decoded) == len(pcm8k)

    orig_arr = array("h")
    orig_arr.frombytes(pcm8k)
    dec_arr = array("h")
    dec_arr.frombytes(decoded)
    max_err = max(abs(a - b) for a, b in zip(orig_arr, dec_arr))
    assert max_err < 700  # ITU-T G.711 logarithmic quantization tolerance

    # 2. Resampling 8kHz -> 16kHz doubles byte length; 24kHz -> 8kHz reduces to 1/3
    pcm16k = twilio_ulaw_b64_to_gemini_pcm16(base64.b64encode(ulaw).decode("ascii"))
    assert len(pcm16k) == len(pcm8k) * 2

    pcm24k = _make_sine_pcm16(24000, duration_ms=20)
    tw_b64 = gemini_pcm16_to_twilio_ulaw_b64(pcm24k, source_rate=24000)
    assert len(base64.b64decode(tw_b64)) == 160  # 20ms at 8kHz = 160 u-law bytes


@pytest.mark.asyncio
async def test_voice_make_call_enforces_approval_token_and_e164(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    approval_service = ApprovalService(TEST_HMAC_SECRET, default_ttl_seconds=600)
    registry = VoiceBridgeRegistry()

    fake_twilio = MagicMock()
    fake_twilio.calls.create.return_value = SimpleNamespace(sid="CA1234567890abcdef")

    connector = VoiceMCPConnector(
        account_sid="AC_TEST",
        auth_token="twilio_test_auth_token",
        from_number="+15551234567",
        public_base_url="https://gemuse.example.com",
        bridge_registry=registry,
        approval_service=approval_service,
        session_factory=db_session_factory,
        twilio_client=fake_twilio,
    )
    assert connector.is_configured() is True

    tool_registry = ToolRegistry(approval_service=approval_service)
    for tool in connector.get_tools():
        tool_registry.register_tool(tool)

    # 1. Calling via ToolRegistry without approval_token physically blocks
    async with db_session_factory() as db:
        with pytest.raises(ApprovalRequiredError):
            await tool_registry.execute_tool(
                "voice_make_call",
                {"to_number": "+819012345678", "purpose": "予約確認"},
                db=db,
            )

        # 2. Invalid E.164 fails even after valid token
        req1, raw_tok1 = await approval_service.create_request(
            db, action_type="voice_make_call", payload={"to_number": "090-1234-5678"}
        )
        await approval_service.resolve_request(
            db, approval_id=req1.id, decision="approved", raw_token=raw_tok1
        )
        with pytest.raises(ValueError, match="Invalid E.164"):
            await tool_registry.execute_tool(
                "voice_make_call",
                {"to_number": "090-1234-5678", "purpose": "予約確認", "approval_token": raw_tok1},
                db=db,
            )

        # 3. Valid token + E.164 dials Twilio once
        req2, raw_tok2 = await approval_service.create_request(
            db, action_type="voice_make_call", payload={"to_number": "+819012345678"}
        )
        await approval_service.resolve_request(
            db, approval_id=req2.id, decision="approved", raw_token=raw_tok2
        )
        exec_res = await tool_registry.execute_tool(
            "voice_make_call",
            {
                "to_number": "+819012345678",
                "purpose": "明日の歯医者予約を15時に確認する",
                "approval_token": raw_tok2,
            },
            db=db,
        )
        assert exec_res.success is True
        assert "CA1234567890abcdef" in exec_res.wrapped_output
        fake_twilio.calls.create.assert_called_once()

        # 4. Reusing consumed approval_token is rejected
        with pytest.raises(ApprovalRequiredError):
            await tool_registry.execute_tool(
                "voice_make_call",
                {"to_number": "+819012345678", "purpose": "再送信", "approval_token": raw_tok2},
                db=db,
            )


@pytest.mark.asyncio
async def test_twilio_signature_verification_and_websocket_bridge_saves_episode(
    test_settings: Settings,
    db_session_factory: async_sessionmaker[AsyncSession],
    memory_service,
    mock_llm_provider,
) -> None:
    twilio_token = "twilio_secret_token_for_hmac_tests"
    settings = test_settings.model_copy(
        update={
            "TWILIO_ACCOUNT_SID": "AC_TEST",
            "TWILIO_AUTH_TOKEN": twilio_token,
            "TWILIO_PHONE_NUMBER": "+15551234567",
            "PUBLIC_BASE_URL": "https://gemuse.example.com",
        }
    )

    pcm24k_reply = _make_sine_pcm16(24000, duration_ms=20)
    fake_live = FakeGeminiLiveSession(
        [
            GeminiLiveOutputChunk(
                audio_pcm=pcm24k_reply,
                sample_rate=24000,
                text_transcript="お電話ありがとうございます。予約の件で確認です。",
                speaker="assistant",
            ),
            GeminiLiveOutputChunk(
                text_transcript="はい、明日15時でお待ちしております。",
                speaker="callee",
                turn_complete=True,
            ),
        ]
    )

    async def _live_factory(_instruction: str):
        return fake_live

    voice_registry = VoiceBridgeRegistry(live_session_factory=_live_factory)
    voice_registry.register_call(
        call_ref="call-demo-1",
        to_number="+819012345678",
        purpose="明日の15時予約確認",
        system_instruction="丁寧に予約確認してください。",
    )

    app = create_app(
        settings_override=settings,
        llm_provider_override=mock_llm_provider,
        memory_service_override=memory_service,
        voice_bridge_registry_override=voice_registry,
        enable_redis=False,
    )

    async def _override_db():
        async with db_session_factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = _override_db

    # 1. HTTP Twilio Webhook signature verification
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://testserver") as client:
            bad = await client.post("/api/voice/twiml?call_ref=call-demo-1", data={"CallSid": "CA99"})
            assert bad.status_code == 403

            validator = RequestValidator(twilio_token)
            params = {"CallSid": "CA99"}
            sig = validator.compute_signature(
                "https://gemuse.example.com/api/voice/twiml?call_ref=call-demo-1", params
            )
            ok = await client.post(
                "/api/voice/twiml?call_ref=call-demo-1",
                data=params,
                headers={"X-Twilio-Signature": sig},
            )
            assert ok.status_code == 200
            assert "wss://gemuse.example.com/api/voice/stream" in ok.text
            assert "stream_token=" in ok.text

    # Extract stream_token from returned TwiML XML
    import re

    m = re.search(r"stream_token=([^\"&<]+)", ok.text)
    assert m is not None
    stream_token = m.group(1)

    # 2. WebSocket Media Stream test (bidirectional transcoding + EpisodeMemory saving)
    inbound_ulaw = pcm16_to_ulaw(_make_sine_pcm16(8000, duration_ms=20))
    inbound_b64 = base64.b64encode(inbound_ulaw).decode("ascii")

    with TestClient(app) as sync_client:
        with sync_client.websocket_connect(
            f"/api/voice/stream?call_ref=call-demo-1&stream_token={stream_token}"
        ) as ws:
            ws.send_text(
                json.dumps(
                    {
                        "event": "start",
                        "start": {"streamSid": "MZ123", "callSid": "CA99"},
                    }
                )
            )
            ws.send_text(
                json.dumps(
                    {
                        "event": "media",
                        "streamSid": "MZ123",
                        "media": {"payload": inbound_b64},
                    }
                )
            )
            outbound_msg = json.loads(ws.receive_text())
            assert outbound_msg["event"] == "media"
            assert outbound_msg["streamSid"] == "MZ123"
            assert len(base64.b64decode(outbound_msg["media"]["payload"])) == 160
            ws.send_text(json.dumps({"event": "stop", "streamSid": "MZ123"}))
            try:
                ws.receive_text()
            except Exception:
                pass

    assert len(fake_live.received_audio) == 1
    assert fake_live.closed is True

    # Confirm transcript summary saved into EpisodeMemory
    async with db_session_factory() as db:
        episodes = (
            await db.execute(select(EpisodeMemory).where(EpisodeMemory.source_type == "voice_call"))
        ).scalars().all()
        assert len(episodes) == 1
        assert "明日の15時予約確認" in episodes[0].content
        assert "明日15時でお待ちしております" in episodes[0].content
