"""Google Workspace MCP connector: Calendar (list/conflicts/create) & Drive (search/read)."""

import json
from collections.abc import Callable
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.security import ApprovalRequiredError, ApprovalService, wrap_untrusted_data
from app.mcp_hub.base import BaseMCPConnector, MCPTool


class GoogleWorkspaceMCPConnector(BaseMCPConnector):
    """Async Google Calendar v3 & Google Drive v3 connector."""

    connector_name = "google_workspace"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        calendar_id: str = "primary",
        approval_service: ApprovalService,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._refresh_token = refresh_token.strip()
        self._calendar_id = calendar_id.strip() or "primary"
        self._approval_service = approval_service
        self._session_factory = session_factory
        self._http_client_factory = http_client_factory

    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret and self._refresh_token)

    def _create_client(self) -> httpx.AsyncClient:
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

    async def _fetch_events(
        self, client: httpx.AsyncClient, token: str, time_min: str, time_max: str
    ) -> list[dict[str, Any]]:
        resp = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{self._calendar_id}/events",
            headers={"Authorization": f"Bearer {token}"},
            params={
                "timeMin": time_min,
                "timeMax": time_max,
                "singleEvents": "true",
                "orderBy": "startTime",
            },
        )
        resp.raise_for_status()
        return resp.json().get("items") or []

    async def calendar_list_events(self, time_min: str, time_max: str) -> str:
        """List Google Calendar events in [time_min, time_max] ISO-8601 range."""
        async with self._create_client() as client:
            token = await self._get_access_token(client)
            events = await self._fetch_events(client, token, time_min, time_max)

        if not events:
            return wrap_untrusted_data("google_calendar:events", "該当期間の予定はありません。")

        lines: list[str] = []
        for ev in events:
            start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date", "")
            end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date", "")
            summary = ev.get("summary") or "(No Title)"
            lines.append(f"- [MCP: calendar] {start} ~ {end}: {summary} (id={ev.get('id', '')})")
        return wrap_untrusted_data("google_calendar:events", "\n".join(lines))

    async def calendar_check_conflicts(self, start_time: str, end_time: str) -> str:
        """Check for overlapping calendar events in [start_time, end_time]."""
        async with self._create_client() as client:
            token = await self._get_access_token(client)
            events = await self._fetch_events(client, token, start_time, end_time)

        conflicts = [
            {
                "id": ev.get("id", ""),
                "summary": ev.get("summary") or "(No Title)",
                "start": (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date", ""),
                "end": (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date", ""),
            }
            for ev in events
        ]
        payload = {
            "has_conflict": len(conflicts) > 0,
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
        }
        return wrap_untrusted_data(
            "google_calendar:conflicts",
            json.dumps(payload, ensure_ascii=False),
        )

    async def calendar_create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        approval_token: str = "",
    ) -> str:
        """Create a Google Calendar event after verifying one-time approval_token."""
        if self._session_factory is None:
            raise ApprovalRequiredError(
                "Cannot verify approval_token for calendar_create_event without DB session",
                action_type="calendar_create_event",
            )
        async with self._session_factory() as db:
            await self._approval_service.verify_and_consume_by_token(
                db,
                action_type="calendar_create_event",
                raw_token=approval_token,
            )

        async with self._create_client() as client:
            token = await self._get_access_token(client)
            existing = await self._fetch_events(client, token, start_time, end_time)
            resp = await client.post(
                f"https://www.googleapis.com/calendar/v3/calendars/{self._calendar_id}/events",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "summary": summary,
                    "description": description,
                    "start": {"dateTime": start_time},
                    "end": {"dateTime": end_time},
                },
            )
            resp.raise_for_status()
            created = resp.json()

        result = {
            "status": "created",
            "event_id": created.get("id", ""),
            "summary": created.get("summary", summary),
            "overlap_warning": len(existing) > 0,
            "overlapping_count": len(existing),
        }
        return wrap_untrusted_data(
            "google_calendar:create",
            json.dumps(result, ensure_ascii=False),
        )

    async def drive_search_files(self, query: str, limit: int = 10) -> str:
        """Search files in Google Drive matching `query`."""
        escaped_q = query.replace("'", "\\'")
        async with self._create_client() as client:
            token = await self._get_access_token(client)
            resp = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": f"name contains '{escaped_q}' and trashed = false",
                    "pageSize": max(1, min(50, int(limit))),
                    "fields": "files(id, name, mimeType, modifiedTime, webViewLink)",
                },
            )
            resp.raise_for_status()
            files = resp.json().get("files") or []

        if not files:
            return wrap_untrusted_data("google_drive:search", f"'{query}' に一致するファイルはありません。")

        lines = [
            f"- [MCP: drive] {f.get('name')} (id={f.get('id')}, mime={f.get('mimeType')}, modified={f.get('modifiedTime', '')})"
            for f in files
        ]
        return wrap_untrusted_data("google_drive:search", "\n".join(lines))

    async def drive_read_text_file(self, file_id: str) -> str:
        """Read text contents of a Google Doc or plain-text Drive file."""
        async with self._create_client() as client:
            token = await self._get_access_token(client)
            auth_headers = {"Authorization": f"Bearer {token}"}
            meta_resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers=auth_headers,
                params={"fields": "id, name, mimeType"},
            )
            meta_resp.raise_for_status()
            meta = meta_resp.json()
            mime_type = meta.get("mimeType", "")

            if mime_type == "application/vnd.google-apps.document":
                content_resp = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                    headers=auth_headers,
                    params={"mimeType": "text/plain"},
                )
            else:
                content_resp = await client.get(
                    f"https://www.googleapis.com/drive/v3/files/{file_id}",
                    headers=auth_headers,
                    params={"alt": "media"},
                )
            content_resp.raise_for_status()
            text = content_resp.text

        header = f"[MCP: drive] {meta.get('name', file_id)} (id={file_id})\n"
        return wrap_untrusted_data(f"google_drive:{file_id}", header + text)

    def get_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="calendar_list_events",
                description="List Google Calendar events between time_min and time_max (ISO-8601).",
                handler=self.calendar_list_events,
                requires_approval=False,
                source_type="google_calendar",
                already_wrapped=True,
            ),
            MCPTool(
                name="calendar_check_conflicts",
                description="Check for overlapping events on Google Calendar.",
                handler=self.calendar_check_conflicts,
                requires_approval=False,
                source_type="google_calendar",
                already_wrapped=True,
            ),
            MCPTool(
                name="calendar_create_event",
                description="Create a Google Calendar event (requires approval_token).",
                handler=self.calendar_create_event,
                requires_approval=True,
                source_type="google_calendar",
                already_wrapped=True,
            ),
            MCPTool(
                name="drive_search_files",
                description="Search Google Drive files by keyword.",
                handler=self.drive_search_files,
                requires_approval=False,
                source_type="google_drive",
                already_wrapped=True,
            ),
            MCPTool(
                name="drive_read_text_file",
                description="Read text content from a Google Doc or Drive text file.",
                handler=self.drive_read_text_file,
                requires_approval=False,
                source_type="google_drive",
                already_wrapped=True,
            ),
        ]

