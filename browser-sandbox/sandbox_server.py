"""Internal Playwright RPC server running inside the isolated gemuse-browser container."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from playwright.async_api import (
    Browser,
    BrowserContext,
    Download,
    Page,
    Playwright,
    async_playwright,
)
from pydantic import BaseModel, Field

logger = logging.getLogger("gemuse.browser_sandbox")
logging.basicConfig(level=logging.INFO)

PROFILE_DIR = Path(os.getenv("BROWSER_PROFILE_DIR", "/browser_profile"))
STORAGE_STATE_FILE = PROFILE_DIR / "storage_state.json"
SHARED_UPLOADS_DIR = Path(os.getenv("SHARED_UPLOADS_DIR", "/shared_files/uploads"))
SHARED_DOWNLOADS_DIR = Path(os.getenv("SHARED_DOWNLOADS_DIR", "/shared_files/downloads"))
VIEWPORT_WIDTH = int(os.getenv("SCREEN_WIDTH", "1280"))
VIEWPORT_HEIGHT = int(os.getenv("SCREEN_HEIGHT", "800"))


class CreateSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    load_storage_state: bool = True


class NavigateRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=4096)
    wait_until: str = "domcontentloaded"
    timeout_ms: int = Field(default=30000, ge=1000, le=120000)


class ActionRequest(BaseModel):
    action_type: str = Field(..., min_length=1, max_length=64)
    selector: str | None = None
    x: float | None = None
    y: float | None = None
    text: str | None = None
    key: str | None = None
    url: str | None = None
    delta_x: float = 0
    delta_y: float = 400
    file_names: list[str] = Field(default_factory=list)
    timeout_ms: int = Field(default=15000, ge=500, le=60000)


class ManagedSession:
    """Single isolated BrowserContext + active Page tied to a Gemuse session_id."""

    def __init__(self, session_id: str, context: BrowserContext, page: Page) -> None:
        self.session_id = session_id
        self.context = context
        self.page = page
        self.lock = asyncio.Lock()
        self.downloads: list[dict[str, Any]] = []
        self.last_used_at = time.time()


class SandboxPlaywrightManager:
    """Manages one Chromium process with multiple isolated BrowserContexts."""

    def __init__(self) -> None:
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._sessions: dict[str, ManagedSession] = {}
        self._global_lock = asyncio.Lock()

    async def startup(self) -> None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        SHARED_UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
        SHARED_DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=False,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                f"--window-size={VIEWPORT_WIDTH},{VIEWPORT_HEIGHT}",
            ],
        )
        logger.info("Chromium browser launched in headful Xvfb sandbox")

    async def shutdown(self) -> None:
        async with self._global_lock:
            for sid in list(self._sessions.keys()):
                await self._close_session_unlocked(sid, save_state=True)
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._pw is not None:
                await self._pw.stop()
                self._pw = None

    async def get_or_create_session(
        self, session_id: str, *, load_storage_state: bool = True
    ) -> ManagedSession:
        async with self._global_lock:
            if session_id in self._sessions:
                session = self._sessions[session_id]
                session.last_used_at = time.time()
                return session

            if self._browser is None:
                raise RuntimeError("Chromium browser is not initialized")

            context_kwargs: dict[str, Any] = {
                "viewport": {"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
                "accept_downloads": True,
            }
            if load_storage_state and STORAGE_STATE_FILE.exists():
                context_kwargs["storage_state"] = str(STORAGE_STATE_FILE)

            context = await self._browser.new_context(**context_kwargs)
            page = await context.new_page()
            session = ManagedSession(session_id=session_id, context=context, page=page)

            def _on_download(download: Download) -> None:
                asyncio.create_task(self._handle_download(session, download))

            page.on("download", _on_download)
            self._sessions[session_id] = session
            logger.info("Created isolated BrowserContext session_id=%s", session_id)
            return session

    async def _handle_download(self, session: ManagedSession, download: Download) -> None:
        try:
            suggested = Path(download.suggested_filename or f"download_{int(time.time())}.bin").name
            target_dir = SHARED_DOWNLOADS_DIR / session.session_id
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = (target_dir / suggested).resolve()
            if not str(target_path).startswith(str(target_dir.resolve())):
                target_path = target_dir / f"download_{int(time.time())}.bin"
            await download.save_as(str(target_path))
            session.downloads.append(
                {
                    "filename": target_path.name,
                    "path": str(target_path),
                    "url": download.url,
                    "saved_at": time.time(),
                }
            )
            logger.info("Saved download for session=%s file=%s", session.session_id, target_path)
        except Exception as exc:  # pragma: no cover - defensive runtime catch
            logger.warning("Download save failed session=%s: %s", session.session_id, exc)

    async def save_storage_state(self, session: ManagedSession) -> None:
        try:
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            await session.context.storage_state(path=str(STORAGE_STATE_FILE))
        except Exception as exc:
            logger.warning("Failed to persist storage_state: %s", exc)

    async def _close_session_unlocked(self, session_id: str, *, save_state: bool = True) -> bool:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        if save_state:
            await self.save_storage_state(session)
        try:
            await session.context.close()
        except Exception as exc:
            logger.warning("Error closing BrowserContext session=%s: %s", session_id, exc)
        return True

    async def close_session(self, session_id: str) -> bool:
        async with self._global_lock:
            return await self._close_session_unlocked(session_id, save_state=True)

    def get_session(self, session_id: str) -> ManagedSession | None:
        session = self._sessions.get(session_id)
        if session is not None:
            session.last_used_at = time.time()
        return session

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            {
                "session_id": sid,
                "url": s.page.url,
                "last_used_at": s.last_used_at,
                "downloads_count": len(s.downloads),
            }
            for sid, s in self._sessions.items()
        ]


manager = SandboxPlaywrightManager()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await manager.startup()
    try:
        yield
    finally:
        await manager.shutdown()


app = FastAPI(title="Gemuse Browser Sandbox RPC", version="1.0.0", lifespan=lifespan)


async def _collect_page_snapshot(session: ManagedSession) -> dict[str, Any]:
    page = session.page
    screenshot_bytes = await page.screenshot(type="png", full_page=False)
    screenshot_b64 = base64.b64encode(screenshot_bytes).decode("ascii")
    title = await page.title()
    dom_summary = await page.evaluate(
        """() => {
            const bodyText = (document.body?.innerText || "").replace(/\\s+/g, " ").trim().slice(0, 6000);
            const hasPassword = !!document.querySelector('input[type="password"]');
            const hasOtp = !!document.querySelector(
                'input[autocomplete="one-time-code"], input[name*="otp" i], input[name*="2fa" i], input[id*="otp" i]'
            );
            const hasCaptcha = !!document.querySelector(
                'iframe[src*="recaptcha"], iframe[src*="hcaptcha"], iframe[src*="turnstile"], .g-recaptcha, [data-sitekey]'
            );
            const interactive = Array.from(
                document.querySelectorAll('a, button, input, select, textarea, [role="button"]')
            ).slice(0, 40).map((el, idx) => {
                const rect = el.getBoundingClientRect();
                return {
                    index: idx,
                    tag: el.tagName.toLowerCase(),
                    type: el.getAttribute('type') || '',
                    text: (el.innerText || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('value') || '').trim().slice(0, 80),
                    name: el.getAttribute('name') || '',
                    id: el.id || '',
                    x: Math.round(rect.x + rect.width / 2),
                    y: Math.round(rect.y + rect.height / 2),
                    visible: rect.width > 0 && rect.height > 0,
                };
            });
            return {
                body_text: bodyText,
                has_password_input: hasPassword,
                has_otp_input: hasOtp,
                has_captcha_widget: hasCaptcha,
                interactive_elements: interactive,
            };
        }"""
    )
    return {
        "session_id": session.session_id,
        "url": page.url,
        "title": title,
        "screenshot_base64": screenshot_b64,
        "dom_summary": dom_summary,
        "downloads": list(session.downloads),
    }


@app.get("/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "active_sessions": len(manager.list_sessions()),
        "shared_uploads_dir": str(SHARED_UPLOADS_DIR),
        "shared_downloads_dir": str(SHARED_DOWNLOADS_DIR),
    }


@app.get("/sessions")
async def list_sessions() -> dict[str, Any]:
    return {"sessions": manager.list_sessions()}


@app.post("/sessions")
async def create_session(payload: CreateSessionRequest) -> dict[str, Any]:
    session = await manager.get_or_create_session(
        payload.session_id, load_storage_state=payload.load_storage_state
    )
    return {
        "session_id": session.session_id,
        "url": session.page.url,
        "status": "ready",
    }


@app.delete("/sessions/{session_id}")
async def delete_session(session_id: str) -> dict[str, Any]:
    closed = await manager.close_session(session_id)
    if not closed:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "closed", "session_id": session_id}


@app.post("/sessions/{session_id}/navigate")
async def navigate(session_id: str, payload: NavigateRequest) -> dict[str, Any]:
    if not payload.url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Only http/https URLs are supported")
    session = await manager.get_or_create_session(session_id)
    async with session.lock:
        try:
            await session.page.goto(
                payload.url, wait_until=payload.wait_until, timeout=payload.timeout_ms
            )
            await session.page.wait_for_timeout(400)
            await manager.save_storage_state(session)
            return await _collect_page_snapshot(session)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Navigation failed: {exc}") from exc


@app.get("/sessions/{session_id}/state")
async def get_state(session_id: str) -> dict[str, Any]:
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    async with session.lock:
        return await _collect_page_snapshot(session)


@app.post("/sessions/{session_id}/action")
async def execute_action(session_id: str, payload: ActionRequest) -> dict[str, Any]:
    session = manager.get_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    async with session.lock:
        page = session.page
        action = payload.action_type.lower()
        try:
            if action == "navigate":
                if not payload.url or not payload.url.startswith(("http://", "https://")):
                    raise HTTPException(status_code=400, detail="Valid http/https url required")
                await page.goto(payload.url, wait_until="domcontentloaded", timeout=payload.timeout_ms)
            elif action == "click":
                if payload.selector:
                    await page.locator(payload.selector).first.click(timeout=payload.timeout_ms)
                elif payload.x is not None and payload.y is not None:
                    await page.mouse.click(payload.x, payload.y)
                else:
                    raise HTTPException(status_code=400, detail="selector or x/y required for click")
            elif action == "type":
                if payload.text is None:
                    raise HTTPException(status_code=400, detail="text required for type")
                if payload.selector:
                    await page.locator(payload.selector).first.fill(
                        payload.text, timeout=payload.timeout_ms
                    )
                else:
                    if payload.x is not None and payload.y is not None:
                        await page.mouse.click(payload.x, payload.y)
                    await page.keyboard.type(payload.text, delay=20)
            elif action == "press":
                if not payload.key:
                    raise HTTPException(status_code=400, detail="key required for press")
                await page.keyboard.press(payload.key)
            elif action == "scroll":
                await page.mouse.wheel(payload.delta_x, payload.delta_y)
            elif action == "wait":
                await page.wait_for_timeout(min(payload.timeout_ms, 10000))
            elif action == "upload_files":
                if not payload.selector or not payload.file_names:
                    raise HTTPException(
                        status_code=400, detail="selector and file_names required for upload_files"
                    )
                resolved_paths: list[str] = []
                uploads_root = SHARED_UPLOADS_DIR.resolve()
                for raw_name in payload.file_names:
                    safe_name = Path(raw_name).name
                    candidate = (uploads_root / safe_name).resolve()
                    if not str(candidate).startswith(str(uploads_root)) or not candidate.is_file():
                        raise HTTPException(
                            status_code=404,
                            detail=f"Upload file not found in /shared_files/uploads: {safe_name}",
                        )
                    resolved_paths.append(str(candidate))
                await page.locator(payload.selector).first.set_input_files(
                    resolved_paths, timeout=payload.timeout_ms
                )
            elif action == "back":
                await page.go_back(wait_until="domcontentloaded", timeout=payload.timeout_ms)
            else:
                raise HTTPException(status_code=400, detail=f"Unsupported action_type: {action}")

            await page.wait_for_timeout(450)
            await manager.save_storage_state(session)
            return await _collect_page_snapshot(session)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Browser action failed: {exc}") from exc

