"""Unified multi-mail MCP connector: Outlook (Graph), iCloud (IMAP/SMTP), and Gmail."""

import asyncio
import base64
import email as email_stdlib
import imaplib
import smtplib
from collections.abc import Callable
from datetime import datetime, timezone
from email.header import decode_header
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger, mask_secrets
from app.core.security import ApprovalRequiredError, ApprovalService, wrap_untrusted_data
from app.mcp_hub.base import BaseMCPConnector, MCPTool

logger = get_logger(__name__)

MailProviderName = Literal["outlook", "icloud", "gmail"]


class UnifiedEmail(BaseModel):
    """Normalized email representation across Outlook, iCloud, and Gmail."""

    provider: MailProviderName
    message_id: str
    subject: str = "(No Subject)"
    sender: str = ""
    recipients: list[str] = Field(default_factory=list)
    snippet: str = ""
    body: str = ""
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    thread_id: str = ""


class MailProviderClient(Protocol):
    """Protocol implemented by each mail backend (or mock in tests)."""

    provider_name: MailProviderName

    def is_configured(self) -> bool: ...

    async def search_messages(self, query: str, limit: int = 10) -> list[UnifiedEmail]: ...

    async def get_detail(self, message_id: str) -> UnifiedEmail: ...

    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]: ...

    async def send_email(self, to: str, subject: str, body: str) -> dict[str, Any]: ...


def _parse_iso_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return datetime.now(timezone.utc)


def _decode_mime_header(raw_val: str | None) -> str:
    if not raw_val:
        return ""
    parts: list[str] = []
    for chunk, charset in decode_header(raw_val):
        if isinstance(chunk, bytes):
            parts.append(chunk.decode(charset or "utf-8", errors="replace"))
        else:
            parts.append(chunk)
    return "".join(parts)


class OutlookMailClient:
    """Microsoft Graph API mail client using OAuth2 refresh token."""

    provider_name: MailProviderName = "outlook"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        refresh_token: str,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._tenant_id = tenant_id.strip()
        self._refresh_token = refresh_token.strip()
        self._http_client_factory = http_client_factory

    def is_configured(self) -> bool:
        return bool(
            self._client_id and self._client_secret and self._tenant_id and self._refresh_token
        )

    def _client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(timeout=15.0)

    async def _get_access_token(self, client: httpx.AsyncClient) -> str:
        token_url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        resp = await client.post(
            token_url,
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
                "scope": "https://graph.microsoft.com/Mail.ReadWrite https://graph.microsoft.com/Mail.Send",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def search_messages(self, query: str, limit: int = 10) -> list[UnifiedEmail]:
        async with self._client() as client:
            token = await self._get_access_token(client)
            resp = await client.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers={"Authorization": f"Bearer {token}"},
                params={"$search": f'"{query}"', "$top": max(1, min(50, limit))},
            )
            resp.raise_for_status()
            items = resp.json().get("value", [])

        emails: list[UnifiedEmail] = []
        for item in items:
            sender_addr = ((item.get("from") or {}).get("emailAddress") or {}).get("address", "")
            recipients = [
                ((r.get("emailAddress") or {}).get("address", ""))
                for r in (item.get("toRecipients") or [])
            ]
            emails.append(
                UnifiedEmail(
                    provider="outlook",
                    message_id=str(item.get("id", "")),
                    subject=item.get("subject") or "(No Subject)",
                    sender=sender_addr,
                    recipients=[r for r in recipients if r],
                    snippet=item.get("bodyPreview") or "",
                    body=((item.get("body") or {}).get("content") or ""),
                    received_at=_parse_iso_dt(item.get("receivedDateTime")),
                    thread_id=str(item.get("conversationId", "")),
                )
            )
        return emails

    async def get_detail(self, message_id: str) -> UnifiedEmail:
        async with self._client() as client:
            token = await self._get_access_token(client)
            resp = await client.get(
                f"https://graph.microsoft.com/v1.0/me/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            item = resp.json()

        sender_addr = ((item.get("from") or {}).get("emailAddress") or {}).get("address", "")
        return UnifiedEmail(
            provider="outlook",
            message_id=str(item.get("id", message_id)),
            subject=item.get("subject") or "(No Subject)",
            sender=sender_addr,
            snippet=item.get("bodyPreview") or "",
            body=((item.get("body") or {}).get("content") or item.get("bodyPreview") or ""),
            received_at=_parse_iso_dt(item.get("receivedDateTime")),
            thread_id=str(item.get("conversationId", "")),
        )

    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        async with self._client() as client:
            token = await self._get_access_token(client)
            resp = await client.post(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "subject": subject,
                    "body": {"contentType": "Text", "content": body},
                    "toRecipients": [{"emailAddress": {"address": to}}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            return {"provider": "outlook", "draft_id": data.get("id", ""), "status": "draft_created"}

    async def send_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        async with self._client() as client:
            token = await self._get_access_token(client)
            resp = await client.post(
                "https://graph.microsoft.com/v1.0/me/sendMail",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "message": {
                        "subject": subject,
                        "body": {"contentType": "Text", "content": body},
                        "toRecipients": [{"emailAddress": {"address": to}}],
                    }
                },
            )
            resp.raise_for_status()
            return {"provider": "outlook", "to": to, "status": "sent"}


class ICloudMailClient:
    """iCloud Mail IMAP/SMTP client executed in worker threads."""

    provider_name: MailProviderName = "icloud"

    def __init__(
        self,
        *,
        email_address: str,
        app_password: str,
        imap_host: str = "imap.mail.me.com",
        imap_port: int = 993,
        smtp_host: str = "smtp.mail.me.com",
        smtp_port: int = 587,
    ) -> None:
        self._email = email_address.strip()
        self._app_password = app_password.strip()
        self._imap_host = imap_host
        self._imap_port = imap_port
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port

    def is_configured(self) -> bool:
        return bool(self._email and self._app_password)

    def _search_sync(self, query: str, limit: int) -> list[UnifiedEmail]:
        results: list[UnifiedEmail] = []
        with imaplib.IMAP4_SSL(self._imap_host, self._imap_port) as imap:
            imap.login(self._email, self._app_password)
            imap.select("INBOX", readonly=True)
            safe_q = query.replace('"', "")
            status_code, data = imap.search(None, f'(TEXT "{safe_q}")')
            if status_code != "OK" or not data or not data[0]:
                return []

            ids = data[0].split()[-limit:]
            for raw_id in reversed(ids):
                msg_id = raw_id.decode("utf-8", errors="ignore")
                res_code, msg_data = imap.fetch(raw_id, "(RFC822)")
                if res_code != "OK" or not msg_data:
                    continue
                raw_bytes = msg_data[0][1] if isinstance(msg_data[0], tuple) else b""
                if not raw_bytes:
                    continue
                parsed = email_stdlib.message_from_bytes(raw_bytes)
                body_text = self._extract_body(parsed)
                dt = datetime.now(timezone.utc)
                if parsed.get("Date"):
                    try:
                        parsed_dt = parsedate_to_datetime(parsed["Date"])
                        dt = parsed_dt if parsed_dt.tzinfo else parsed_dt.replace(tzinfo=timezone.utc)
                    except Exception:
                        pass
                results.append(
                    UnifiedEmail(
                        provider="icloud",
                        message_id=msg_id,
                        subject=_decode_mime_header(parsed.get("Subject")) or "(No Subject)",
                        sender=_decode_mime_header(parsed.get("From")),
                        recipients=[_decode_mime_header(parsed.get("To"))],
                        snippet=body_text[:200],
                        body=body_text,
                        received_at=dt,
                    )
                )
        return results

    @staticmethod
    def _extract_body(msg: email_stdlib.message.Message) -> str:
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain" and not part.get("Content-Disposition"):
                    payload = part.get_payload(decode=True)
                    if payload:
                        charset = part.get_content_charset() or "utf-8"
                        return payload.decode(charset, errors="replace")
            return ""
        payload = msg.get_payload(decode=True)
        if not payload:
            return str(msg.get_payload() or "")
        charset = msg.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")

    async def search_messages(self, query: str, limit: int = 10) -> list[UnifiedEmail]:
        return await asyncio.to_thread(self._search_sync, query, limit)

    def _detail_sync(self, message_id: str) -> UnifiedEmail:
        with imaplib.IMAP4_SSL(self._imap_host, self._imap_port) as imap:
            imap.login(self._email, self._app_password)
            imap.select("INBOX", readonly=True)
            res_code, msg_data = imap.fetch(message_id.encode("utf-8"), "(RFC822)")
            if res_code != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                raise ValueError(f"iCloud message '{message_id}' not found")
            parsed = email_stdlib.message_from_bytes(msg_data[0][1])
            body_text = self._extract_body(parsed)
            return UnifiedEmail(
                provider="icloud",
                message_id=message_id,
                subject=_decode_mime_header(parsed.get("Subject")) or "(No Subject)",
                sender=_decode_mime_header(parsed.get("From")),
                recipients=[_decode_mime_header(parsed.get("To"))],
                snippet=body_text[:200],
                body=body_text,
            )

    async def get_detail(self, message_id: str) -> UnifiedEmail:
        return await asyncio.to_thread(self._detail_sync, message_id)

    def _draft_sync(self, to: str, subject: str, body: str) -> dict[str, Any]:
        msg = EmailMessage()
        msg["From"] = self._email
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with imaplib.IMAP4_SSL(self._imap_host, self._imap_port) as imap:
            imap.login(self._email, self._app_password)
            imap.append("Drafts", "\\Draft", None, msg.as_bytes())
        return {"provider": "icloud", "to": to, "status": "draft_created"}

    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._draft_sync, to, subject, body)

    def _send_sync(self, to: str, subject: str, body: str) -> dict[str, Any]:
        msg = EmailMessage()
        msg["From"] = self._email
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(self._smtp_host, self._smtp_port, timeout=15) as smtp:
            smtp.starttls()
            smtp.login(self._email, self._app_password)
            smtp.send_message(msg)
        return {"provider": "icloud", "to": to, "status": "sent"}

    async def send_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        return await asyncio.to_thread(self._send_sync, to, subject, body)


class GmailMailClient:
    """Google Gmail REST API client using OAuth2 refresh token."""

    provider_name: MailProviderName = "gmail"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._refresh_token = refresh_token.strip()
        self._http_client_factory = http_client_factory

    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret and self._refresh_token)

    def _client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(timeout=15.0)

    async def _get_access_token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": self._refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    @staticmethod
    def _parse_gmail_message(data: dict[str, Any]) -> UnifiedEmail:
        payload = data.get("payload") or {}
        headers = {
            h.get("name", "").lower(): h.get("value", "")
            for h in (payload.get("headers") or [])
        }
        internal_ms = int(data.get("internalDate") or 0)
        received_at = (
            datetime.fromtimestamp(internal_ms / 1000.0, tz=timezone.utc)
            if internal_ms
            else datetime.now(timezone.utc)
        )
        body_data = (payload.get("body") or {}).get("data", "")
        decoded_body = ""
        if body_data:
            padded = body_data + "=" * (-len(body_data) % 4)
            decoded_body = base64.urlsafe_b64decode(padded.encode("utf-8")).decode(
                "utf-8", errors="replace"
            )
        return UnifiedEmail(
            provider="gmail",
            message_id=str(data.get("id", "")),
            subject=headers.get("subject") or "(No Subject)",
            sender=headers.get("from", ""),
            recipients=[headers.get("to", "")] if headers.get("to") else [],
            snippet=data.get("snippet", ""),
            body=decoded_body or data.get("snippet", ""),
            received_at=received_at,
            thread_id=str(data.get("threadId", "")),
        )

    async def search_messages(self, query: str, limit: int = 10) -> list[UnifiedEmail]:
        async with self._client() as client:
            token = await self._get_access_token(client)
            auth_headers = {"Authorization": f"Bearer {token}"}
            list_resp = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                headers=auth_headers,
                params={"q": query, "maxResults": max(1, min(50, limit))},
            )
            list_resp.raise_for_status()
            refs = list_resp.json().get("messages") or []

            async def _fetch_msg(msg_id: str) -> UnifiedEmail:
                r = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{msg_id}",
                    headers=auth_headers,
                )
                r.raise_for_status()
                return self._parse_gmail_message(r.json())

            fetched = await asyncio.gather(
                *(_fetch_msg(ref["id"]) for ref in refs if ref.get("id")),
                return_exceptions=True,
            )
        return [item for item in fetched if isinstance(item, UnifiedEmail)]

    async def get_detail(self, message_id: str) -> UnifiedEmail:
        async with self._client() as client:
            token = await self._get_access_token(client)
            resp = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            return self._parse_gmail_message(resp.json())

    @staticmethod
    def _encode_raw_message(to: str, subject: str, body: str) -> str:
        msg = EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    async def create_draft(self, to: str, subject: str, body: str) -> dict[str, Any]:
        raw = self._encode_raw_message(to, subject, body)
        async with self._client() as client:
            token = await self._get_access_token(client)
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/drafts",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": {"raw": raw}},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"provider": "gmail", "draft_id": data.get("id", ""), "status": "draft_created"}

    async def send_email(self, to: str, subject: str, body: str) -> dict[str, Any]:
        raw = self._encode_raw_message(to, subject, body)
        async with self._client() as client:
            token = await self._get_access_token(client)
            resp = await client.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {token}"},
                json={"raw": raw},
            )
            resp.raise_for_status()
            data = resp.json()
            return {"provider": "gmail", "message_id": data.get("id", ""), "status": "sent"}


class MailMCPConnector(BaseMCPConnector):
    """Unified multi-provider mail connector normalizing Outlook, iCloud, and Gmail."""

    connector_name = "mail"

    def __init__(
        self,
        *,
        providers: dict[MailProviderName, MailProviderClient],
        approval_service: ApprovalService,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._providers = providers
        self._approval_service = approval_service
        self._session_factory = session_factory

    def is_configured(self) -> bool:
        return any(client.is_configured() for client in self._providers.values())

    def _get_active_provider(self, provider: str) -> MailProviderClient:
        key = provider.lower().strip()
        if key not in self._providers:
            raise ValueError(f"Unknown mail provider '{provider}'")
        client = self._providers[key]  # type: ignore[index]
        if not client.is_configured():
            raise RuntimeError(f"Mail provider '{provider}' is not configured")
        return client

    @staticmethod
    def _wrap_email(email_item: UnifiedEmail) -> str:
        raw_text = (
            f"[Email: {email_item.provider} / {email_item.message_id}]\n"
            f"Date: {email_item.received_at.isoformat()}\n"
            f"From: {email_item.sender}\n"
            f"Subject: {email_item.subject}\n"
            f"Body: {email_item.body or email_item.snippet}"
        )
        return wrap_untrusted_data(
            f"email:{email_item.provider}:{email_item.message_id}",
            raw_text,
        )

    async def mail_search_all(
        self,
        query: str,
        providers: list[str] | None = None,
        limit_per_provider: int = 10,
    ) -> str:
        """Search emails in parallel across configured providers and sort newest-first."""
        target_names = (
            [p.lower().strip() for p in providers]
            if providers
            else list(self._providers.keys())
        )
        active_clients = [
            self._providers[name]  # type: ignore[index]
            for name in target_names
            if name in self._providers and self._providers[name].is_configured()  # type: ignore[index]
        ]
        if not active_clients:
            return wrap_untrusted_data("email:search", "有効なメールプロバイダーがありません。")

        results = await asyncio.gather(
            *(c.search_messages(query, limit=limit_per_provider) for c in active_clients),
            return_exceptions=True,
        )

        merged: list[UnifiedEmail] = []
        for client, res in zip(active_clients, results):
            if isinstance(res, Exception):
                logger.warning(
                    "Mail provider search failed",
                    extra={"provider": client.provider_name, "error": mask_secrets(str(res))},
                )
            elif isinstance(res, list):
                merged.extend(res)

        merged.sort(key=lambda item: item.received_at, reverse=True)
        if not merged:
            return wrap_untrusted_data("email:search", f"'{query}' に一致するメールはありません。")

        return "\n\n".join(self._wrap_email(item) for item in merged)

    async def mail_get_detail(self, provider: str, message_id: str) -> str:
        """Fetch full details of a specific email and isolate inside untrusted tags."""
        client = self._get_active_provider(provider)
        email_item = await client.get_detail(message_id)
        return self._wrap_email(email_item)

    async def mail_create_draft(self, provider: str, to: str, subject: str, body: str) -> str:
        """Create an email draft safely (no approval required)."""
        client = self._get_active_provider(provider)
        result = await client.create_draft(to=to, subject=subject, body=body)
        return wrap_untrusted_data(f"email:{provider}:draft", str(result))

    async def mail_send_email(
        self,
        provider: str,
        to: str,
        subject: str,
        body: str,
        approval_token: str = "",
    ) -> str:
        """Send an email after verifying and consuming a one-time approval_token."""
        if self._session_factory is None:
            raise ApprovalRequiredError(
                "Cannot verify approval_token for mail_send_email without DB session",
                action_type="mail_send_email",
            )
        async with self._session_factory() as db:
            await self._approval_service.verify_and_consume_by_token(
                db,
                action_type="mail_send_email",
                raw_token=approval_token,
            )

        client = self._get_active_provider(provider)
        result = await client.send_email(to=to, subject=subject, body=body)
        return wrap_untrusted_data(f"email:{provider}:sent", str(result))

    def get_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="mail_search_all",
                description="Search emails in parallel across Outlook, iCloud, and Gmail.",
                handler=self.mail_search_all,
                requires_approval=False,
                source_type="email",
                already_wrapped=True,
            ),
            MCPTool(
                name="mail_get_detail",
                description="Fetch full email body and headers from a specific provider.",
                handler=self.mail_get_detail,
                requires_approval=False,
                source_type="email",
                already_wrapped=True,
            ),
            MCPTool(
                name="mail_create_draft",
                description="Save an email draft without sending (safe action).",
                handler=self.mail_create_draft,
                requires_approval=False,
                source_type="email",
                already_wrapped=True,
            ),
            MCPTool(
                name="mail_send_email",
                description="Send an email via Outlook, iCloud, or Gmail (requires approval_token).",
                handler=self.mail_send_email,
                requires_approval=True,
                source_type="email",
                already_wrapped=True,
            ),
        ]

