"""Twilio Media Streams (8kHz u-law) <-> Gemini Live API (PCM16) bidirectional voice bridge."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from array import array
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from fastapi import WebSocket
from google import genai
from google.genai import types
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory import MemoryService
from app.core.logging import get_logger

logger = get_logger(__name__)

E164_REGEX = re.compile(r"^\+[1-9]\d{6,14}$")

# Standard ITU-T G.711 u-law bias & clip
_ULAW_BIAS = 0x84
_ULAW_CLIP = 32635


def _build_ulaw_decode_table() -> tuple[int, ...]:
    table: list[int] = []
    for byte_val in range(256):
        u = (~byte_val) & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        magnitude = ((mantissa << 3) + _ULAW_BIAS) << exponent
        sample = magnitude - _ULAW_BIAS
        table.append(-sample if sign else sample)
    return tuple(table)


_ULAW_DECODE_TABLE = _build_ulaw_decode_table()


def _linear_sample_to_ulaw_byte(sample: int) -> int:
    sign = 0
    if sample < 0:
        sign = 0x80
        sample = -sample
    if sample > _ULAW_CLIP:
        sample = _ULAW_CLIP
    sample += _ULAW_BIAS

    exponent = 7
    mask = 0x4000
    while exponent > 0 and not (sample & mask):
        exponent -= 1
        mask >>= 1

    mantissa = (sample >> (exponent + 3)) & 0x0F
    return (~(sign | (exponent << 4) | mantissa)) & 0xFF


def ulaw_to_pcm16(ulaw_bytes: bytes) -> bytes:
    """Decode 8-bit G.711 u-law bytes into little-endian 16-bit signed linear PCM."""
    if not ulaw_bytes:
        return b""
    samples = array("h", (_ULAW_DECODE_TABLE[b] for b in ulaw_bytes))
    return samples.tobytes()


def pcm16_to_ulaw(pcm_bytes: bytes) -> bytes:
    """Encode little-endian 16-bit signed linear PCM into 8-bit G.711 u-law bytes."""
    if not pcm_bytes:
        return b""
    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    samples = array("h")
    samples.frombytes(pcm_bytes[:usable])
    return bytes(_linear_sample_to_ulaw_byte(s) for s in samples)


def resample_pcm16(pcm_bytes: bytes, src_rate: int, dst_rate: int) -> bytes:
    """Resample 16-bit signed little-endian mono PCM via linear interpolation (pure Python)."""
    if not pcm_bytes or src_rate == dst_rate:
        return pcm_bytes
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError("Sample rates must be positive integers")

    usable = len(pcm_bytes) - (len(pcm_bytes) % 2)
    src = array("h")
    src.frombytes(pcm_bytes[:usable])
    n_in = len(src)
    if n_in == 0:
        return b""
    if n_in == 1:
        n_out = max(1, round(dst_rate / src_rate))
        return array("h", [src[0]] * n_out).tobytes()

    n_out = max(1, int(round(n_in * dst_rate / src_rate)))
    out = array("h")
    ratio = src_rate / dst_rate
    for i in range(n_out):
        pos = i * ratio
        idx = int(pos)
        if idx >= n_in - 1:
            out.append(src[-1])
        else:
            frac = pos - idx
            val = src[idx] * (1.0 - frac) + src[idx + 1] * frac
            clamped = max(-32768, min(32767, int(round(val))))
            out.append(clamped)
    return out.tobytes()


def twilio_ulaw_b64_to_gemini_pcm16(payload_b64: str, target_rate: int = 16000) -> bytes:
    """Convert Twilio base64 8kHz u-law audio payload into Gemini Live 16kHz PCM16."""
    if not payload_b64:
        return b""
    ulaw = base64.b64decode(payload_b64)
    pcm8k = ulaw_to_pcm16(ulaw)
    return resample_pcm16(pcm8k, 8000, target_rate)


def gemini_pcm16_to_twilio_ulaw_b64(pcm_bytes: bytes, source_rate: int = 24000) -> str:
    """Convert Gemini Live PCM16 audio into Twilio base64 8kHz u-law payload."""
    if not pcm_bytes:
        return ""
    pcm8k = resample_pcm16(pcm_bytes, source_rate, 8000)
    ulaw = pcm16_to_ulaw(pcm8k)
    return base64.b64encode(ulaw).decode("ascii")


def validate_e164_phone(number: str) -> str:
    cleaned = number.strip()
    if not E164_REGEX.match(cleaned):
        raise ValueError(f"Invalid E.164 phone number format: {number}")
    return cleaned


@dataclass
class GeminiLiveOutputChunk:
    """Normalized output message from a Gemini Live streaming session."""

    audio_pcm: bytes | None = None
    sample_rate: int = 24000
    text_transcript: str | None = None
    speaker: str = "assistant"
    interrupted: bool = False
    turn_complete: bool = False


class GeminiLiveSessionProtocol(Protocol):
    """Protocol abstracting a live bidirectional Gemini voice session."""

    async def send_audio_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> None:
        ...

    async def send_text_prompt(self, text: str) -> None:
        ...

    def receive_chunks(self) -> AsyncIterator[GeminiLiveOutputChunk]:
        ...

    async def close(self) -> None:
        ...


class GoogleGenAILiveSession:
    """Wraps google.genai Live API WebSocket session."""

    def __init__(self, session_cm: Any, session: Any) -> None:
        self._cm = session_cm
        self._session = session

    async def send_audio_pcm(self, pcm_bytes: bytes, sample_rate: int = 16000) -> None:
        if not pcm_bytes:
            return
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm_bytes, mime_type=f"audio/pcm;rate={sample_rate}")
        )

    async def send_text_prompt(self, text: str) -> None:
        await self._session.send_client_content(
            turns=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
            turn_complete=True,
        )

    async def receive_chunks(self) -> AsyncIterator[GeminiLiveOutputChunk]:
        async for response in self._session.receive():
            server_content = getattr(response, "server_content", None)
            if server_content is None:
                continue
            interrupted = bool(getattr(server_content, "interrupted", False))
            turn_complete = bool(getattr(server_content, "turn_complete", False))

            input_tx = getattr(server_content, "input_transcription", None)
            if input_tx and getattr(input_tx, "text", None):
                yield GeminiLiveOutputChunk(
                    text_transcript=input_tx.text,
                    speaker="callee",
                )

            output_tx = getattr(server_content, "output_transcription", None)
            if output_tx and getattr(output_tx, "text", None):
                yield GeminiLiveOutputChunk(
                    text_transcript=output_tx.text,
                    speaker="assistant",
                )

            model_turn = getattr(server_content, "model_turn", None)
            if model_turn and getattr(model_turn, "parts", None):
                for part in model_turn.parts:
                    inline_data = getattr(part, "inline_data", None)
                    if inline_data and getattr(inline_data, "data", None):
                        yield GeminiLiveOutputChunk(
                            audio_pcm=bytes(inline_data.data),
                            sample_rate=24000,
                            interrupted=interrupted,
                            turn_complete=turn_complete,
                        )
                    elif getattr(part, "text", None):
                        yield GeminiLiveOutputChunk(
                            text_transcript=part.text,
                            speaker="assistant",
                            interrupted=interrupted,
                            turn_complete=turn_complete,
                        )
            elif interrupted or turn_complete:
                yield GeminiLiveOutputChunk(
                    interrupted=interrupted,
                    turn_complete=turn_complete,
                )

    async def close(self) -> None:
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception as exc:
            logger.debug("Ignoring Gemini Live session close warning: %s", exc)


LiveSessionFactory = Callable[[str], Awaitable[GeminiLiveSessionProtocol]]


@dataclass
class VoiceCallContext:
    """In-memory metadata tracked for an active or pending outbound/inbound call."""

    call_ref: str
    to_number: str
    purpose: str
    system_instruction: str = ""
    call_sid: str = ""


@dataclass
class VoiceBridgeRegistry:
    """Tracks outbound call contexts and creates Gemini Live sessions."""

    api_key: str = ""
    live_model: str = "gemini-4-hydrogen-live"
    live_session_factory: LiveSessionFactory | None = None
    _contexts: dict[str, VoiceCallContext] = field(default_factory=dict)

    def register_call(
        self,
        *,
        call_ref: str,
        to_number: str,
        purpose: str,
        system_instruction: str = "",
    ) -> VoiceCallContext:
        ctx = VoiceCallContext(
            call_ref=call_ref,
            to_number=to_number,
            purpose=purpose,
            system_instruction=system_instruction,
        )
        self._contexts[call_ref] = ctx
        return ctx

    def get_call_context(self, call_ref: str) -> VoiceCallContext | None:
        return self._contexts.get(call_ref)

    async def open_live_session(self, system_instruction: str) -> GeminiLiveSessionProtocol:
        if self.live_session_factory is not None:
            return await self.live_session_factory(system_instruction)
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini Live voice bridge")

        client = genai.Client(api_key=self.api_key)
        config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=system_instruction,
        )
        cm = client.aio.live.connect(model=self.live_model, config=config)
        session = await cm.__aenter__()
        return GoogleGenAILiveSession(cm, session)


class TwilioVoiceBridgeSession:
    """Runs the bidirectional WebSocket loop between Twilio Media Streams and Gemini Live."""

    def __init__(
        self,
        *,
        live_session: GeminiLiveSessionProtocol,
        call_context: VoiceCallContext | None = None,
        memory_service: MemoryService | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self.live_session = live_session
        self.call_context = call_context
        self.memory_service = memory_service
        self.session_factory = session_factory
        self.stream_sid: str = ""
        self.call_sid: str = call_context.call_sid if call_context else ""
        self.transcript_lines: list[str] = []

    async def run(self, websocket: WebSocket) -> dict[str, Any]:
        """Accept and relay audio/transcripts until Twilio sends stop or closes socket."""
        stop_event = asyncio.Event()
        started_event = asyncio.Event()

        async def _twilio_to_gemini() -> None:
            while not stop_event.is_set():
                try:
                    raw_msg = await websocket.receive_text()
                except Exception:
                    started_event.set()
                    stop_event.set()
                    break

                try:
                    data = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                event_type = data.get("event")
                if event_type == "start":
                    start_info = data.get("start") or {}
                    self.stream_sid = str(start_info.get("streamSid") or data.get("streamSid") or "")
                    self.call_sid = str(start_info.get("callSid") or self.call_sid or "")
                    started_event.set()
                    if self.call_context and self.call_context.purpose:
                        await self.live_session.send_text_prompt(
                            f"通話が接続されました。用件に沿って相手へ簡潔かつ丁寧に話しかけてください: {self.call_context.purpose}"
                        )
                elif event_type == "media":
                    payload = ((data.get("media") or {}).get("payload")) or ""
                    pcm16k = twilio_ulaw_b64_to_gemini_pcm16(payload, target_rate=16000)
                    if pcm16k:
                        await self.live_session.send_audio_pcm(pcm16k, sample_rate=16000)
                elif event_type == "stop":
                    started_event.set()
                    stop_event.set()
                    await self.live_session.close()
                    break

        async def _gemini_to_twilio() -> None:
            try:
                await started_event.wait()
                async for chunk in self.live_session.receive_chunks():
                    if stop_event.is_set():
                        break
                    if chunk.interrupted and self.stream_sid:
                        await websocket.send_text(
                            json.dumps({"event": "clear", "streamSid": self.stream_sid})
                        )
                    if chunk.text_transcript:
                        self.transcript_lines.append(f"{chunk.speaker}: {chunk.text_transcript}")
                    if chunk.audio_pcm and self.stream_sid:
                        b64_ulaw = gemini_pcm16_to_twilio_ulaw_b64(
                            chunk.audio_pcm, source_rate=chunk.sample_rate
                        )
                        if b64_ulaw:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "event": "media",
                                        "streamSid": self.stream_sid,
                                        "media": {"payload": b64_ulaw},
                                    }
                                )
                            )
            except Exception as exc:
                logger.warning("Gemini Live receive loop ended: %s", exc)
            finally:
                stop_event.set()

        await asyncio.gather(_twilio_to_gemini(), _gemini_to_twilio())
        await self.live_session.close()

        saved_episode_id = await self.persist_call_summary()
        return {
            "call_sid": self.call_sid,
            "stream_sid": self.stream_sid,
            "transcript_turns": len(self.transcript_lines),
            "episode_id": saved_episode_id,
        }

    async def persist_call_summary(self, db: AsyncSession | None = None) -> str | None:
        """Store voice call transcript summary into Layer 2 EpisodeMemory."""
        if self.memory_service is None:
            return None
        if not self.transcript_lines and not self.call_context:
            return None

        purpose = self.call_context.purpose if self.call_context else "電話通話"
        to_number = self.call_context.to_number if self.call_context else "unknown"
        transcript_text = "\n".join(self.transcript_lines) if self.transcript_lines else "(文字起こしなし)"
        summary = (
            f"【音声通話ログ】\n"
            f"相手番号: {to_number}\n"
            f"通話目的: {purpose}\n"
            f"CallSID: {self.call_sid or 'N/A'}\n"
            f"会話内容:\n{transcript_text}"
        )

        async def _save(session: AsyncSession) -> str:
            ep = await self.memory_service.add_episode(
                session,
                content=summary,
                source_type="voice_call",
                source_ref=f"twilio:{self.call_sid or 'unknown'}",
                importance_score=0.85,
                deduplicate=False,
            )
            return str(ep.id)

        if db is not None:
            return await _save(db)
        if self.session_factory is not None:
            async with self.session_factory() as session:
                return await _save(session)
        return None
