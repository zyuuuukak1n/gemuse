"""Google Workspace & Maps MCP connector supporting private & school dual accounts.

Services:
- Calendar v3 (`calendar_list_calendars`, `calendar_list_events`, `calendar_check_conflicts`,
  `calendar_create_event`, `calendar_update_event`, `calendar_delete_event`)
- Drive v3 (`drive_search_files`, `drive_read_text_file`, `drive_create_folder`, `drive_move_or_rename`)
- Docs v1 (`docs_create_document`, `docs_get_document`, `docs_append_text`, `docs_replace_text`)
- Sheets v4 (`sheets_create_spreadsheet`, `sheets_read_range`, `sheets_append_rows`, `sheets_update_values`,
  `sheets_format_table`, `sheets_apply_formatting`)
- Slides v1 (`slides_create_presentation`, `slides_get_presentation`, `slides_add_slide`)
- Tasks v1 (`tasks_list`, `tasks_create`, `tasks_complete`)
- Contacts / People v1 (`contacts_search`)
- Maps (`maps_search_places`, `maps_compute_route`)
"""

import asyncio
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar
from urllib.parse import quote

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger, mask_secrets
from app.core.security import ApprovalRequiredError, ApprovalService, wrap_untrusted_data
from app.mcp_hub.base import BaseMCPConnector, MCPTool

logger = get_logger(__name__)

GoogleAccountName = Literal["private", "school"]
GoogleAccountScope = Literal["private", "school", "all"]
TravelMode = Literal["DRIVE", "WALK", "BICYCLE", "TRANSIT"]

_T = TypeVar("_T")


def _extract_docs_text(doc: dict[str, Any]) -> str:
    """Recursively extract plain text from a Google Docs API v1 document body."""
    chunks: list[str] = []

    def _walk_elements(elements: list[dict[str, Any]]) -> None:
        for elem in elements:
            if "paragraph" in elem:
                para_parts = []
                for p_el in elem["paragraph"].get("elements") or []:
                    text_run = p_el.get("textRun")
                    if text_run and "content" in text_run:
                        para_parts.append(text_run["content"])
                if para_parts:
                    chunks.append("".join(para_parts))
            elif "table" in elem:
                for row in elem["table"].get("tableRows") or []:
                    row_cells: list[str] = []
                    for cell in row.get("tableCells") or []:
                        before = len(chunks)
                        _walk_elements(cell.get("content") or [])
                        cell_text = "".join(chunks[before:]).strip().replace("\n", " ")
                        del chunks[before:]
                        row_cells.append(cell_text)
                    if row_cells:
                        chunks.append(" | ".join(row_cells) + "\n")

    body = doc.get("body") or {}
    _walk_elements(body.get("content") or [])
    return "".join(chunks).strip()


def _extract_slides_text(presentation: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract ordered per-slide text blocks from a Google Slides API v1 presentation."""
    extracted: list[dict[str, Any]] = []
    for idx, slide in enumerate(presentation.get("slides") or [], start=1):
        slide_texts: list[str] = []
        for page_el in slide.get("pageElements") or []:
            shape = page_el.get("shape") or {}
            text_obj = shape.get("text") or {}
            parts = [
                te["textRun"]["content"]
                for te in (text_obj.get("textElements") or [])
                if isinstance(te.get("textRun"), dict) and te["textRun"].get("content")
            ]
            combined = "".join(parts).strip()
            if combined:
                slide_texts.append(combined)
        extracted.append(
            {
                "slide_number": idx,
                "object_id": slide.get("objectId", ""),
                "texts": slide_texts,
            }
        )
    return extracted


def _build_google_web_url(file_id: str, mime_type: str = "") -> str:
    """Return canonical Google Workspace web URL matching a Drive MIME type."""
    if mime_type == "application/vnd.google-apps.document":
        return f"https://docs.google.com/document/d/{file_id}/edit"
    if mime_type == "application/vnd.google-apps.spreadsheet":
        return f"https://docs.google.com/spreadsheets/d/{file_id}/edit"
    if mime_type == "application/vnd.google-apps.presentation":
        return f"https://docs.google.com/presentation/d/{file_id}/edit"
    if mime_type == "application/vnd.google-apps.folder":
        return f"https://drive.google.com/drive/folders/{file_id}"
    return f"https://drive.google.com/file/d/{file_id}/view"


def _build_preview_slides(slides: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build compact title + summary previews for each slide."""
    previews: list[dict[str, Any]] = []
    for slide in slides:
        texts = slide.get("texts") or []
        title_text = texts[0] if texts else ""
        body_summary = " / ".join(texts[1:]) if len(texts) > 1 else ""
        previews.append(
            {
                "slide_number": slide.get("slide_number", len(previews) + 1),
                "object_id": slide.get("object_id", ""),
                "title": title_text,
                "summary": body_summary or title_text or "(空スライド)",
            }
        )
    return previews


def _build_create_slide_requests(
    title: str,
    body: str,
    *,
    insertion_index: int | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Build Google Slides batchUpdate requests creating a slide with optional title & body."""
    suffix = uuid.uuid4().hex[:10]
    slide_id = f"gemuse_slide_{suffix}"
    title_id = f"gemuse_title_{suffix}"
    body_id = f"gemuse_body_{suffix}"

    create_slide: dict[str, Any] = {
        "objectId": slide_id,
        "slideLayoutReference": {"predefinedLayout": "TITLE_AND_BODY"},
        "placeholderIdMappings": [
            {"layoutPlaceholder": {"type": "TITLE", "index": 0}, "objectId": title_id},
            {"layoutPlaceholder": {"type": "BODY", "index": 0}, "objectId": body_id},
        ],
    }
    if insertion_index is not None:
        create_slide["insertionIndex"] = max(0, insertion_index)

    requests: list[dict[str, Any]] = [{"createSlide": create_slide}]
    if title:
        requests.append({"insertText": {"objectId": title_id, "text": title}})
    if body:
        requests.append({"insertText": {"objectId": body_id, "text": body}})
    return slide_id, requests


_A1_CELL_RE = re.compile(r"^\$?([A-Za-z]+)?\$?(\d+)?$")


def _hex_to_sheets_color(hex_color: str) -> dict[str, float]:
    """Convert `#RRGGBB` or `#RGB` hex color code to Google Sheets RGB color dict."""
    cleaned = hex_color.strip().lstrip("#")
    if len(cleaned) == 3:
        cleaned = "".join(ch * 2 for ch in cleaned)
    if len(cleaned) != 6 or any(ch not in "0123456789abcdefABCDEF" for ch in cleaned):
        raise ValueError(f"Invalid hex color code: {hex_color!r}")
    return {
        "red": round(int(cleaned[0:2], 16) / 255.0, 4),
        "green": round(int(cleaned[2:4], 16) / 255.0, 4),
        "blue": round(int(cleaned[4:6], 16) / 255.0, 4),
    }


def _col_letters_to_index(letters: str) -> int:
    """Convert 1-based Excel/Sheets column letters (`A` -> 0, `AA` -> 26)."""
    idx = 0
    for ch in letters.upper():
        idx = idx * 26 + (ord(ch) - ord("A") + 1)
    return idx - 1


def _split_sheet_and_range(range_a1: str) -> tuple[str | None, str]:
    """Split `'Sheet 1'!A1:B2` into `(sheet_name, cell_range)`."""
    raw = range_a1.strip()
    if "!" not in raw:
        return None, raw
    sheet_part, cell_part = raw.split("!", 1)
    sheet_part = sheet_part.strip()
    if sheet_part.startswith("'") and sheet_part.endswith("'") and len(sheet_part) >= 2:
        sheet_part = sheet_part[1:-1].replace("''", "'")
    return sheet_part, cell_part.strip()


def _a1_to_grid_range(
    range_a1: str,
    sheet_id_by_title: dict[str, int] | None = None,
    *,
    default_sheet_id: int = 0,
) -> dict[str, Any]:
    """
    Convert an A1 notation string (`A8:K20`, `シート1!A8:K20`, etc.) into a Google Sheets `GridRange`.

    Row and column bounds follow Google Sheets API semantics (0-indexed start, exclusive end).
    """
    sheet_name, cell_expr = _split_sheet_and_range(range_a1)
    if sheet_name is not None:
        if not sheet_id_by_title or sheet_name not in sheet_id_by_title:
            raise ValueError(f"Sheet '{sheet_name}' not found in spreadsheet metadata")
        sheet_id = sheet_id_by_title[sheet_name]
    else:
        sheet_id = default_sheet_id

    if ":" in cell_expr:
        start_token, end_token = cell_expr.split(":", 1)
    else:
        start_token, end_token = cell_expr, cell_expr

    m_start = _A1_CELL_RE.match(start_token.strip())
    m_end = _A1_CELL_RE.match(end_token.strip())
    if not m_start or not m_end or (not m_start.group(1) and not m_start.group(2)):
        raise ValueError(f"Invalid A1 range notation: {range_a1!r}")

    start_col_str, start_row_str = m_start.group(1), m_start.group(2)
    end_col_str, end_row_str = m_end.group(1), m_end.group(2)

    grid_range: dict[str, Any] = {"sheetId": sheet_id}
    if start_row_str:
        grid_range["startRowIndex"] = max(0, int(start_row_str) - 1)
    if end_row_str:
        grid_range["endRowIndex"] = max(1, int(end_row_str))
    elif start_row_str and ":" not in cell_expr:
        grid_range["endRowIndex"] = int(start_row_str)

    if start_col_str:
        grid_range["startColumnIndex"] = _col_letters_to_index(start_col_str)
    if end_col_str:
        grid_range["endColumnIndex"] = _col_letters_to_index(end_col_str) + 1
    elif start_col_str and ":" not in cell_expr:
        grid_range["endColumnIndex"] = _col_letters_to_index(start_col_str) + 1

    return grid_range


def _build_format_table_requests(
    grid_range: dict[str, Any],
    *,
    table_name: str,
    include_native_table: bool,
    include_basic_filter: bool,
    header_bg_hex: str,
    header_text_hex: str,
    zebra_striping: bool,
    add_borders: bool,
    auto_resize_columns: bool,
    wrap_text: bool,
    freeze_header_row: bool,
) -> list[dict[str, Any]]:
    """Construct Google Sheets batchUpdate requests for table creation, styling, borders, and sizing."""
    requests: list[dict[str, Any]] = []
    sheet_id = int(grid_range["sheetId"])
    start_row = int(grid_range.get("startRowIndex", 0))
    end_row = grid_range.get("endRowIndex")
    start_col = int(grid_range.get("startColumnIndex", 0))
    end_col = grid_range.get("endColumnIndex")

    if include_native_table:
        safe_name = re.sub(r"\s+", "_", table_name.strip())[:50] or "DataTable"
        requests.append(
            {
                "addTable": {
                    "table": {
                        "name": safe_name,
                        "range": grid_range,
                    }
                }
            }
        )

    header_range: dict[str, Any] = {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": start_row + 1,
    }
    if start_col is not None:
        header_range["startColumnIndex"] = start_col
    if end_col is not None:
        header_range["endColumnIndex"] = end_col

    header_format: dict[str, Any] = {
        "backgroundColor": _hex_to_sheets_color(header_bg_hex),
        "textFormat": {
            "bold": True,
            "foregroundColor": _hex_to_sheets_color(header_text_hex),
        },
        "horizontalAlignment": "CENTER",
        "verticalAlignment": "MIDDLE",
        "wrapStrategy": "WRAP" if wrap_text else "OVERFLOW_CELL",
    }
    requests.append(
        {
            "repeatCell": {
                "range": header_range,
                "cell": {"userEnteredFormat": header_format},
                "fields": "userEnteredFormat(backgroundColor,textFormat,horizontalAlignment,verticalAlignment,wrapStrategy)",
            }
        }
    )

    if end_row is None or int(end_row) > start_row + 1:
        data_range: dict[str, Any] = {
            "sheetId": sheet_id,
            "startRowIndex": start_row + 1,
        }
        if end_row is not None:
            data_range["endRowIndex"] = int(end_row)
        if start_col is not None:
            data_range["startColumnIndex"] = start_col
        if end_col is not None:
            data_range["endColumnIndex"] = end_col
        requests.append(
            {
                "repeatCell": {
                    "range": data_range,
                    "cell": {
                        "userEnteredFormat": {
                            "verticalAlignment": "MIDDLE",
                            "wrapStrategy": "WRAP" if wrap_text else "CLIP",
                        }
                    },
                    "fields": "userEnteredFormat(verticalAlignment,wrapStrategy)",
                }
            }
        )

    if zebra_striping and not include_native_table:
        requests.append(
            {
                "addBanding": {
                    "bandedRange": {
                        "range": grid_range,
                        "rowProperties": {
                            "headerColor": _hex_to_sheets_color(header_bg_hex),
                            "firstBandColor": {"red": 1.0, "green": 1.0, "blue": 1.0},
                            "secondBandColor": {"red": 0.945, "green": 0.961, "blue": 0.976},
                        },
                    }
                }
            }
        )

    if include_basic_filter and not include_native_table:
        requests.append({"setBasicFilter": {"filter": {"range": grid_range}}})

    if add_borders:
        outer = {"style": "SOLID_MEDIUM", "width": 2, "color": {"red": 0.392, "green": 0.455, "blue": 0.545}}
        inner = {"style": "SOLID", "width": 1, "color": {"red": 0.796, "green": 0.835, "blue": 0.882}}
        requests.append(
            {
                "updateBorders": {
                    "range": grid_range,
                    "top": outer,
                    "bottom": outer,
                    "left": outer,
                    "right": outer,
                    "innerHorizontal": inner,
                    "innerVertical": inner,
                }
            }
        )

    if freeze_header_row:
        requests.append(
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": start_row + 1},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            }
        )

    if auto_resize_columns:
        dim_range: dict[str, Any] = {
            "sheetId": sheet_id,
            "dimension": "COLUMNS",
            "startIndex": start_col,
        }
        if end_col is not None:
            dim_range["endIndex"] = int(end_col)
        requests.append({"autoResizeDimensions": {"dimensions": dim_range}})

    return requests


class GoogleWorkspaceMCPConnector(BaseMCPConnector):
    """Async Google Workspace & Maps connector with private/school 2-account support."""

    connector_name = "google_workspace"

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str = "",
        calendar_id: str = "primary",
        refresh_token_school: str = "",
        calendar_id_school: str = "primary",
        maps_api_key: str = "",
        approval_service: ApprovalService,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._refresh_tokens: dict[GoogleAccountName, str] = {
            "private": refresh_token.strip(),
            "school": refresh_token_school.strip(),
        }
        self._calendar_ids: dict[GoogleAccountName, str] = {
            "private": calendar_id.strip() or "primary",
            "school": calendar_id_school.strip() or "primary",
        }
        self._maps_api_key = maps_api_key.strip()
        self._approval_service = approval_service
        self._session_factory = session_factory
        self._http_client_factory = http_client_factory

    def _has_oauth_account(self, account: GoogleAccountName) -> bool:
        return bool(self._client_id and self._client_secret and self._refresh_tokens.get(account))

    def has_oauth_configured(self) -> bool:
        return self._has_oauth_account("private") or self._has_oauth_account("school")

    def is_configured(self) -> bool:
        return bool(self.has_oauth_configured() or self._maps_api_key)

    def _create_client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(timeout=15.0)

    def _resolve_single_account(self, account: str) -> GoogleAccountName:
        normalized = account.lower().strip() or "private"
        if normalized not in ("private", "school"):
            raise ValueError(f"Invalid Google account '{account}'. Expected 'private' or 'school'.")
        acct: GoogleAccountName = normalized  # type: ignore[assignment]
        if not self._has_oauth_account(acct):
            raise RuntimeError(f"Google Workspace account '{acct}' is not configured")
        return acct

    def _resolve_scope_accounts(self, account: str) -> list[GoogleAccountName]:
        normalized = account.lower().strip() or "all"
        if normalized == "all":
            active = [acct for acct in ("private", "school") if self._has_oauth_account(acct)]  # type: ignore[misc]
            if not active:
                raise RuntimeError("No Google Workspace OAuth account is configured")
            return active  # type: ignore[return-value]
        return [self._resolve_single_account(normalized)]

    async def _get_access_token(
        self,
        client: httpx.AsyncClient,
        account: GoogleAccountName = "private",
    ) -> str:
        refresh_token = self._refresh_tokens.get(account, "")
        if not (self._client_id and self._client_secret and refresh_token):
            raise RuntimeError(f"Google Workspace account '{account}' is not configured")
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def _run_across_accounts(
        self,
        account: str,
        worker: Callable[[GoogleAccountName], Awaitable[_T]],
    ) -> list[tuple[GoogleAccountName, _T]]:
        targets = self._resolve_scope_accounts(account)
        outcomes = await asyncio.gather(
            *(worker(acct) for acct in targets),
            return_exceptions=True,
        )
        results: list[tuple[GoogleAccountName, _T]] = []
        errors: list[Exception] = []
        for acct, res in zip(targets, outcomes):
            if isinstance(res, Exception):
                logger.warning(
                    "Google account operation failed",
                    extra={"account": acct, "error": mask_secrets(str(res))},
                )
                errors.append(res)
            else:
                results.append((acct, res))
        if not results and errors:
            raise errors[0]
        return results

    async def _verify_and_consume_approval(
        self,
        action_type: str,
        approval_token: str,
    ) -> None:
        if self._session_factory is None:
            raise ApprovalRequiredError(
                f"Cannot verify approval_token for {action_type} without DB session",
                action_type=action_type,
            )
        async with self._session_factory() as db:
            await self._approval_service.verify_and_consume_by_token(
                db,
                action_type=action_type,
                raw_token=approval_token,
            )

    # ------------------------------------------------------------------
    # Google Calendar v3
    # ------------------------------------------------------------------

    async def _fetch_calendar_list(
        self,
        client: httpx.AsyncClient,
        token: str,
    ) -> list[dict[str, Any]]:
        """Fetch all registered calendars (My Calendars & Other Calendars) via calendarList."""
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        while True:
            params = {"pageToken": page_token} if page_token else None
            resp = await client.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
            )
            resp.raise_for_status()
            payload = resp.json()
            items.extend(payload.get("items") or [])
            page_token = payload.get("nextPageToken")
            if not page_token:
                break
        return items

    async def _fetch_account_calendars(
        self,
        account: GoogleAccountName,
    ) -> list[dict[str, Any]]:
        async with self._create_client() as client:
            token = await self._get_access_token(client, account)
            raw_items = await self._fetch_calendar_list(client, token)
        return [
            {
                "account": account,
                "id": cal.get("id", ""),
                "summary": cal.get("summary") or cal.get("summaryOverride") or cal.get("id", ""),
                "primary": bool(cal.get("primary", False)),
                "accessRole": cal.get("accessRole", ""),
                "selected": bool(cal.get("selected", False)),
            }
            for cal in raw_items
            if cal.get("id")
        ]

    async def calendar_list_calendars(
        self,
        account: GoogleAccountScope = "all",
    ) -> str:
        """List all registered calendars (My Calendars & Other Calendars) across accounts."""
        per_account = await self._run_across_accounts(
            account,
            self._fetch_account_calendars,
        )
        lines: list[str] = []
        for _acct, calendars in per_account:
            for cal in calendars:
                lines.append(
                    f"- [{cal['account']}] [MCP: calendar] {cal['summary']} "
                    f"(id={cal['id']}, primary={cal['primary']}, "
                    f"accessRole={cal['accessRole']}, selected={cal['selected']})"
                )
        if not lines:
            return wrap_untrusted_data(
                "google_calendar:calendars",
                "[MCP: calendar] 登録されたカレンダーはありません。",
            )
        return wrap_untrusted_data("google_calendar:calendars", "\n".join(lines))

    async def _fetch_events(
        self,
        client: httpx.AsyncClient,
        token: str,
        calendar_id: str,
        time_min: str,
        time_max: str,
    ) -> list[dict[str, Any]]:
        encoded_cal = quote(calendar_id, safe="")
        resp = await client.get(
            f"https://www.googleapis.com/calendar/v3/calendars/{encoded_cal}/events",
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

    async def _resolve_target_calendars_for_events(
        self,
        client: httpx.AsyncClient,
        token: str,
        account: GoogleAccountName,
        calendar_id: str,
    ) -> list[tuple[str, str]]:
        """Resolve (calendar_id, calendar_name) pairs to query for events."""
        normalized_cal = calendar_id.strip() or "all"
        if normalized_cal != "all":
            return [(normalized_cal, normalized_cal)]

        try:
            cal_items = await self._fetch_calendar_list(client, token)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                fallback_id = self._calendar_ids[account]
                fallback_name = "primary" if fallback_id == "primary" else fallback_id
                return [(fallback_id, fallback_name)]
            raise

        targets: list[tuple[str, str]] = []
        for cal in cal_items:
            cid = cal.get("id", "")
            if not cid or cal.get("selected") is not True:
                continue
            cname = cal.get("summary") or cal.get("summaryOverride") or cid
            targets.append((cid, cname))
        return targets

    async def _fetch_account_events(
        self,
        account: GoogleAccountName,
        time_min: str,
        time_max: str,
        calendar_id: str = "all",
    ) -> list[tuple[str, str, dict[str, Any]]]:
        async with self._create_client() as client:
            token = await self._get_access_token(client, account)
            targets = await self._resolve_target_calendars_for_events(
                client, token, account, calendar_id
            )
            if not targets:
                return []

            outcomes = await asyncio.gather(
                *(
                    self._fetch_events(client, token, cid, time_min, time_max)
                    for cid, _ in targets
                ),
                return_exceptions=True,
            )

        tagged_events: list[tuple[str, str, dict[str, Any]]] = []
        errors: list[Exception] = []
        for (cid, cname), outcome in zip(targets, outcomes):
            if isinstance(outcome, Exception):
                logger.warning(
                    "Google sub-calendar fetch failed",
                    extra={"account": account, "calendar_id": cid, "error": mask_secrets(str(outcome))},
                )
                errors.append(outcome)
            else:
                for ev in outcome:
                    tagged_events.append((cid, cname, ev))

        if not tagged_events and errors and len(errors) == len(targets):
            raise errors[0]
        return tagged_events

    @staticmethod
    def _normalize_event(
        ev: dict[str, Any],
        account: GoogleAccountName,
        calendar_id: str = "primary",
        calendar_name: str = "primary",
    ) -> dict[str, Any]:
        start = (ev.get("start") or {}).get("dateTime") or (ev.get("start") or {}).get("date", "")
        end = (ev.get("end") or {}).get("dateTime") or (ev.get("end") or {}).get("date", "")
        return {
            "id": ev.get("id", ""),
            "account": account,
            "calendar_id": calendar_id,
            "calendar_name": calendar_name,
            "summary": ev.get("summary") or "(No Title)",
            "start": start,
            "end": end,
        }

    async def list_events_structured(
        self,
        time_min: str,
        time_max: str,
        account: GoogleAccountScope = "all",
        calendar_id: str = "all",
    ) -> list[dict[str, Any]]:
        """Return normalized structured calendar events across requested accounts and calendars."""
        per_account = await self._run_across_accounts(
            account,
            lambda acct: self._fetch_account_events(acct, time_min, time_max, calendar_id=calendar_id),
        )
        merged: list[dict[str, Any]] = []
        for acct, tagged_events in per_account:
            merged.extend(
                self._normalize_event(ev, acct, calendar_id=cid, calendar_name=cname)
                for cid, cname, ev in tagged_events
            )
        merged.sort(key=lambda item: item["start"])
        return merged

    async def calendar_list_events(
        self,
        time_min: str,
        time_max: str,
        account: GoogleAccountScope = "all",
        calendar_id: str = "all",
    ) -> str:
        """List Google Calendar events across accounts and selected sub-calendars in [time_min, time_max]."""
        events = await self.list_events_structured(
            time_min, time_max, account=account, calendar_id=calendar_id
        )
        if not events:
            return wrap_untrusted_data(
                "google_calendar:events", "[MCP: calendar] 該当期間の予定はありません。"
            )

        lines = [
            (
                f"- [{ev['account']} / {ev['calendar_name']}] [MCP: calendar] "
                f"{ev['start']} ~ {ev['end']}: {ev['summary']} (id={ev['id']})"
            )
            for ev in events
        ]
        return wrap_untrusted_data("google_calendar:events", "\n".join(lines))

    async def calendar_check_conflicts(
        self,
        start_time: str,
        end_time: str,
        account: GoogleAccountScope = "all",
        calendar_id: str = "all",
    ) -> str:
        """Check for overlapping calendar events across requested accounts and calendars."""
        conflicts = await self.list_events_structured(
            start_time, end_time, account=account, calendar_id=calendar_id
        )
        payload = {
            "citation": "[MCP: calendar]",
            "account_scope": account,
            "calendar_id": calendar_id,
            "has_conflict": len(conflicts) > 0,
            "conflict_count": len(conflicts),
            "conflicts": conflicts,
        }
        return wrap_untrusted_data(
            "google_calendar:conflicts",
            json.dumps(payload, ensure_ascii=False),
        )

    @staticmethod
    def _match_calendar_by_name(
        cal_items: list[dict[str, Any]],
        calendar_name: str,
    ) -> tuple[str, str] | None:
        wanted = calendar_name.strip()
        if not wanted:
            return None
        for cal in cal_items:
            cid = cal.get("id", "")
            if not cid:
                continue
            candidates = [
                str(cal.get("summary") or "").strip(),
                str(cal.get("summaryOverride") or "").strip(),
            ]
            if wanted in candidates:
                return cid, candidates[0] or candidates[1] or cid
        wanted_lower = wanted.lower()
        for cal in cal_items:
            cid = cal.get("id", "")
            if not cid:
                continue
            candidates = [
                str(cal.get("summary") or "").strip(),
                str(cal.get("summaryOverride") or "").strip(),
            ]
            if any(c.lower() == wanted_lower for c in candidates if c):
                return cid, candidates[0] or candidates[1] or cid
        return None

    async def calendar_create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
        approval_token: str = "",
        account: GoogleAccountName = "private",
        calendar_id: str = "primary",
        calendar_name: str = "",
    ) -> str:
        """Create a Google Calendar event after verifying one-time approval_token (defaults to private/primary)."""
        await self._verify_and_consume_approval("calendar_create_event", approval_token)
        acct = self._resolve_single_account(account)

        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            target_cal_id = calendar_id.strip() or "primary"
            target_cal_name = target_cal_id
            if calendar_name.strip():
                cal_items = await self._fetch_calendar_list(client, token)
                matched = self._match_calendar_by_name(cal_items, calendar_name)
                if matched is None:
                    raise ValueError(
                        f"Google Calendar '{calendar_name}' was not found for account '{acct}'."
                    )
                target_cal_id, target_cal_name = matched

            cal_id_encoded = quote(target_cal_id, safe="")
            existing = await self._fetch_events(
                client, token, target_cal_id, start_time, end_time
            )
            resp = await client.post(
                f"https://www.googleapis.com/calendar/v3/calendars/{cal_id_encoded}/events",
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
            "citation": "[MCP: calendar]",
            "status": "created",
            "account": acct,
            "calendar_id": target_cal_id,
            "calendar_name": target_cal_name,
            "event_id": created.get("id", ""),
            "summary": created.get("summary", summary),
            "overlap_warning": len(existing) > 0,
            "overlapping_count": len(existing),
        }
        return wrap_untrusted_data(
            "google_calendar:create",
            json.dumps(result, ensure_ascii=False),
        )

    async def calendar_update_event(
        self,
        event_id: str,
        summary: str | None = None,
        start_time: str | None = None,
        end_time: str | None = None,
        description: str | None = None,
        approval_token: str = "",
        account: GoogleAccountName = "private",
        calendar_id: str = "primary",
    ) -> str:
        """Update an existing Google Calendar event after verifying one-time approval_token."""
        await self._verify_and_consume_approval("calendar_update_event", approval_token)
        acct = self._resolve_single_account(account)
        cal_id = quote(calendar_id.strip() or self._calendar_ids[acct], safe="")

        patch_body: dict[str, Any] = {}
        if summary is not None:
            patch_body["summary"] = summary
        if description is not None:
            patch_body["description"] = description
        if start_time is not None:
            patch_body["start"] = {"dateTime": start_time}
        if end_time is not None:
            patch_body["end"] = {"dateTime": end_time}

        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.patch(
                f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events/{quote(event_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
                json=patch_body,
            )
            resp.raise_for_status()
            updated = resp.json()

        result = {
            "citation": "[MCP: calendar]",
            "status": "updated",
            "account": acct,
            "event_id": updated.get("id", event_id),
            "summary": updated.get("summary", summary or ""),
        }
        return wrap_untrusted_data(
            "google_calendar:update",
            json.dumps(result, ensure_ascii=False),
        )

    async def calendar_delete_event(
        self,
        event_id: str,
        approval_token: str = "",
        account: GoogleAccountName = "private",
        calendar_id: str = "primary",
    ) -> str:
        """Delete a Google Calendar event after verifying one-time approval_token."""
        await self._verify_and_consume_approval("calendar_delete_event", approval_token)
        acct = self._resolve_single_account(account)
        cal_id = quote(calendar_id.strip() or self._calendar_ids[acct], safe="")

        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.delete(
                f"https://www.googleapis.com/calendar/v3/calendars/{cal_id}/events/{quote(event_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()

        result = {
            "citation": "[MCP: calendar]",
            "status": "deleted",
            "account": acct,
            "event_id": event_id,
        }
        return wrap_untrusted_data(
            "google_calendar:delete",
            json.dumps(result, ensure_ascii=False),
        )

    # ------------------------------------------------------------------
    # Google Drive v3
    # ------------------------------------------------------------------

    async def _search_drive_account(
        self,
        account: GoogleAccountName,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        escaped_q = query.replace("'", "\\'")
        async with self._create_client() as client:
            token = await self._get_access_token(client, account)
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
            return resp.json().get("files") or []

    async def drive_search_files(
        self,
        query: str,
        limit: int = 10,
        account: GoogleAccountScope = "all",
    ) -> str:
        """Search files in Google Drive matching `query` across requested accounts."""
        per_account = await self._run_across_accounts(
            account,
            lambda acct: self._search_drive_account(acct, query, limit),
        )
        lines: list[str] = []
        for acct, files in per_account:
            for f in files:
                fid = f.get("id", "")
                mime = f.get("mimeType", "")
                web_link = f.get("webViewLink") or _build_google_web_url(fid, mime)
                lines.append(
                    f"- [{acct}] [MCP: drive] {f.get('name')} "
                    f"(id={fid}, mime={mime}, modified={f.get('modifiedTime', '')}, "
                    f"webViewLink={web_link}, url={web_link})"
                )

        if not lines:
            return wrap_untrusted_data(
                "google_drive:search",
                f"[MCP: drive] '{query}' に一致するファイルはありません。",
            )
        return wrap_untrusted_data("google_drive:search", "\n".join(lines))

    async def drive_read_text_file(
        self,
        file_id: str,
        account: GoogleAccountName = "private",
    ) -> str:
        """Read text contents of a Google Doc or plain-text Drive file."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            auth_headers = {"Authorization": f"Bearer {token}"}
            meta_resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers=auth_headers,
                params={"fields": "id, name, mimeType, webViewLink"},
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

        web_link = meta.get("webViewLink") or _build_google_web_url(file_id, mime_type)
        header = (
            f"[{acct}] [MCP: drive] {meta.get('name', file_id)} "
            f"(id={file_id}, url={web_link})\n"
        )
        return wrap_untrusted_data(f"google_drive:{file_id}", header + text)

    async def drive_create_folder(
        self,
        name: str,
        parent_id: str | None = None,
        account: GoogleAccountName = "private",
    ) -> str:
        """Create a folder in Google Drive (defaults to private account)."""
        acct = self._resolve_single_account(account)
        payload: dict[str, Any] = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            payload["parents"] = [parent_id]

        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.post(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {token}"},
                params={"fields": "id, name, mimeType, webViewLink"},
                json=payload,
            )
            resp.raise_for_status()
            created = resp.json()

        folder_id = created.get("id", "")
        mime_type = created.get("mimeType") or "application/vnd.google-apps.folder"
        web_link = created.get("webViewLink") or _build_google_web_url(folder_id, mime_type)
        result = {
            "citation": "[MCP: drive]",
            "status": "created",
            "account": acct,
            **created,
            "webViewLink": web_link,
            "url": web_link,
        }
        return wrap_untrusted_data("google_drive:create_folder", json.dumps(result, ensure_ascii=False))

    async def drive_move_or_rename(
        self,
        file_id: str,
        new_name: str | None = None,
        add_parent_id: str | None = None,
        remove_parent_id: str | None = None,
        account: GoogleAccountName = "private",
    ) -> str:
        """Rename and/or move a Google Drive file between folders."""
        acct = self._resolve_single_account(account)
        params: dict[str, str] = {"fields": "id, name, mimeType, parents, webViewLink"}
        if add_parent_id:
            params["addParents"] = add_parent_id
        if remove_parent_id:
            params["removeParents"] = remove_parent_id
        body: dict[str, Any] = {}
        if new_name is not None:
            body["name"] = new_name

        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.patch(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params=params,
                json=body,
            )
            resp.raise_for_status()
            updated = resp.json()

        fid = updated.get("id", file_id)
        web_link = updated.get("webViewLink") or _build_google_web_url(fid, updated.get("mimeType", ""))
        result = {
            "citation": "[MCP: drive]",
            "status": "updated",
            "account": acct,
            **updated,
            "webViewLink": web_link,
            "url": web_link,
        }
        return wrap_untrusted_data("google_drive:move_or_rename", json.dumps(result, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Google Docs v1
    # ------------------------------------------------------------------

    async def _fetch_doc_preview_text(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        document_id: str,
        fallback_text: str = "",
    ) -> str:
        """Fetch latest document text and return the first 500 characters."""
        try:
            resp = await client.get(
                f"https://docs.googleapis.com/v1/documents/{document_id}",
                headers=headers,
            )
            resp.raise_for_status()
            return _extract_docs_text(resp.json())[:500]
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return fallback_text[:500]
            raise

    async def docs_create_document(
        self,
        title: str,
        initial_text: str = "",
        account: GoogleAccountName = "private",
    ) -> str:
        """Create a Google Doc and optionally insert initial text."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.post(
                "https://docs.googleapis.com/v1/documents",
                headers=headers,
                json={"title": title},
            )
            resp.raise_for_status()
            doc = resp.json()
            doc_id = doc.get("documentId", "")

            if initial_text:
                batch_resp = await client.post(
                    f"https://docs.googleapis.com/v1/documents/{doc_id}:batchUpdate",
                    headers=headers,
                    json={
                        "requests": [
                            {"insertText": {"location": {"index": 1}, "text": initial_text}}
                        ]
                    },
                )
                batch_resp.raise_for_status()

        result = {
            "citation": "[MCP: docs]",
            "status": "created",
            "account": acct,
            "document_id": doc_id,
            "title": doc.get("title", title),
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "preview_text": initial_text[:500],
        }
        return wrap_untrusted_data("google_docs:create", json.dumps(result, ensure_ascii=False))

    async def docs_get_document(
        self,
        document_id: str,
        account: GoogleAccountName = "private",
    ) -> str:
        """Fetch a Google Doc and return its structured plain text, URL, and preview excerpt."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.get(
                f"https://docs.googleapis.com/v1/documents/{document_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            doc = resp.json()

        resolved_id = doc.get("documentId", document_id)
        extracted = _extract_docs_text(doc)
        result = {
            "citation": "[MCP: docs]",
            "account": acct,
            "document_id": resolved_id,
            "title": doc.get("title", document_id),
            "revision_id": doc.get("revisionId", ""),
            "url": f"https://docs.google.com/document/d/{resolved_id}/edit",
            "preview_text": extracted[:500],
            "content": extracted,
        }
        return wrap_untrusted_data(f"google_docs:{document_id}", json.dumps(result, ensure_ascii=False))

    async def docs_append_text(
        self,
        document_id: str,
        text: str,
        account: GoogleAccountName = "private",
    ) -> str:
        """Append text to the end of a Google Doc body."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.post(
                f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate",
                headers=headers,
                json={
                    "requests": [
                        {
                            "insertText": {
                                "endOfSegmentLocation": {"segmentId": ""},
                                "text": text,
                            }
                        }
                    ]
                },
            )
            resp.raise_for_status()
            data = resp.json()
            doc_id = data.get("documentId", document_id)
            preview_text = await self._fetch_doc_preview_text(
                client, headers, doc_id, fallback_text=text
            )

        result = {
            "citation": "[MCP: docs]",
            "status": "appended",
            "account": acct,
            "document_id": doc_id,
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "preview_text": preview_text,
        }
        return wrap_untrusted_data("google_docs:append", json.dumps(result, ensure_ascii=False))

    async def docs_replace_text(
        self,
        document_id: str,
        find_text: str,
        replace_text: str,
        match_case: bool = True,
        account: GoogleAccountName = "private",
    ) -> str:
        """Replace all occurrences of `find_text` with `replace_text` inside a Google Doc."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.post(
                f"https://docs.googleapis.com/v1/documents/{document_id}:batchUpdate",
                headers=headers,
                json={
                    "requests": [
                        {
                            "replaceAllText": {
                                "containsText": {
                                    "text": find_text,
                                    "matchCase": match_case,
                                },
                                "replaceText": replace_text,
                            }
                        }
                    ]
                },
            )
            resp.raise_for_status()
            data = resp.json()
            doc_id = data.get("documentId", document_id)
            preview_text = await self._fetch_doc_preview_text(
                client, headers, doc_id, fallback_text=replace_text
            )

        replies = data.get("replies") or [{}]
        occurrences = (replies[0].get("replaceAllText") or {}).get("occurrencesChanged", 0)
        result = {
            "citation": "[MCP: docs]",
            "status": "replaced",
            "account": acct,
            "document_id": doc_id,
            "url": f"https://docs.google.com/document/d/{doc_id}/edit",
            "occurrences_changed": occurrences,
            "preview_text": preview_text,
        }
        return wrap_untrusted_data("google_docs:replace", json.dumps(result, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Google Sheets v4
    # ------------------------------------------------------------------

    async def sheets_create_spreadsheet(
        self,
        title: str,
        sheet_name: str = "Sheet1",
        initial_rows: list[list[Any]] | None = None,
        account: GoogleAccountName = "private",
    ) -> str:
        """Create a Google Spreadsheet with optional initial rows."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.post(
                "https://sheets.googleapis.com/v4/spreadsheets",
                headers=headers,
                json={
                    "properties": {"title": title},
                    "sheets": [{"properties": {"title": sheet_name}}],
                },
            )
            resp.raise_for_status()
            created = resp.json()
            spreadsheet_id = created.get("spreadsheetId", "")

            updated_cells = 0
            if initial_rows:
                encoded_range = quote(f"{sheet_name}!A1", safe="!:'")
                val_resp = await client.put(
                    f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
                    headers=headers,
                    params={"valueInputOption": "USER_ENTERED"},
                    json={"values": initial_rows},
                )
                val_resp.raise_for_status()
                updated_cells = val_resp.json().get("updatedCells", 0)

        result = {
            "citation": "[MCP: sheets]",
            "status": "created",
            "account": acct,
            "spreadsheet_id": spreadsheet_id,
            "title": title,
            "sheet_name": sheet_name,
            "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "updated_cells": updated_cells,
            "preview_rows": (initial_rows or [])[:10],
        }
        return wrap_untrusted_data("google_sheets:create", json.dumps(result, ensure_ascii=False))

    async def sheets_read_range(
        self,
        spreadsheet_id: str,
        range_a1: str,
        account: GoogleAccountName = "private",
    ) -> str:
        """Read cell values from a Google Sheet using A1 notation."""
        acct = self._resolve_single_account(account)
        encoded_range = quote(range_a1, safe="!:'")
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.get(
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()

        values = data.get("values") or []
        payload = {
            "citation": "[MCP: sheets]",
            "account": acct,
            "spreadsheet_id": spreadsheet_id,
            "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "range": data.get("range", range_a1),
            "values": values,
            "preview_rows": values[:10],
        }
        return wrap_untrusted_data("google_sheets:read", json.dumps(payload, ensure_ascii=False))

    async def sheets_append_rows(
        self,
        spreadsheet_id: str,
        range_a1: str,
        rows: list[list[Any]],
        account: GoogleAccountName = "private",
    ) -> str:
        """Append rows after a table in a Google Sheet."""
        acct = self._resolve_single_account(account)
        encoded_range = quote(range_a1, safe="!:'")
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.post(
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}:append",
                headers={"Authorization": f"Bearer {token}"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"values": rows},
            )
            resp.raise_for_status()
            data = resp.json()

        sp_id = data.get("spreadsheetId", spreadsheet_id)
        updates = data.get("updates") or {}
        result = {
            "citation": "[MCP: sheets]",
            "status": "appended",
            "account": acct,
            "spreadsheet_id": sp_id,
            "url": f"https://docs.google.com/spreadsheets/d/{sp_id}/edit",
            "updated_range": updates.get("updatedRange", range_a1),
            "updated_rows": updates.get("updatedRows", len(rows)),
            "preview_rows": rows[:10],
        }
        return wrap_untrusted_data("google_sheets:append", json.dumps(result, ensure_ascii=False))

    async def sheets_update_values(
        self,
        spreadsheet_id: str,
        range_a1: str,
        values: list[list[Any]],
        account: GoogleAccountName = "private",
    ) -> str:
        """Overwrite cells at `range_a1` in a Google Sheet."""
        acct = self._resolve_single_account(account)
        encoded_range = quote(range_a1, safe="!:'")
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.put(
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{encoded_range}",
                headers={"Authorization": f"Bearer {token}"},
                params={"valueInputOption": "USER_ENTERED"},
                json={"values": values},
            )
            resp.raise_for_status()
            data = resp.json()

        sp_id = data.get("spreadsheetId", spreadsheet_id)
        result = {
            "citation": "[MCP: sheets]",
            "status": "updated",
            "account": acct,
            "spreadsheet_id": sp_id,
            "url": f"https://docs.google.com/spreadsheets/d/{sp_id}/edit",
            "updated_range": data.get("updatedRange", range_a1),
            "updated_cells": data.get("updatedCells", 0),
            "preview_rows": values[:10],
        }
        return wrap_untrusted_data("google_sheets:update", json.dumps(result, ensure_ascii=False))

    async def _resolve_grid_range(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        spreadsheet_id: str,
        range_a1: str,
    ) -> dict[str, Any]:
        """Fetch spreadsheet sheetId metadata and resolve A1 notation to GridRange."""
        meta_resp = await client.get(
            f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}",
            headers=headers,
            params={"fields": "sheets(properties(sheetId,title))"},
        )
        meta_resp.raise_for_status()
        sheets_meta = meta_resp.json().get("sheets") or []

        sheet_id_by_title: dict[str, int] = {}
        default_sheet_id = 0
        for idx, sheet in enumerate(sheets_meta):
            props = sheet.get("properties") or {}
            if "sheetId" in props:
                sid = int(props["sheetId"])
                if idx == 0:
                    default_sheet_id = sid
                title = props.get("title")
                if isinstance(title, str) and title:
                    sheet_id_by_title[title] = sid

        return _a1_to_grid_range(
            range_a1,
            sheet_id_by_title,
            default_sheet_id=default_sheet_id,
        )

    async def sheets_format_table(
        self,
        spreadsheet_id: str,
        range_a1: str,
        table_name: str = "DataTable",
        convert_to_native_table: bool = True,
        header_bg_hex: str = "#1E3A5F",
        header_text_hex: str = "#FFFFFF",
        zebra_striping: bool = True,
        add_borders: bool = True,
        auto_resize_columns: bool = True,
        wrap_text: bool = True,
        freeze_header_row: bool = False,
        account: GoogleAccountName = "private",
    ) -> str:
        """
        Convert a sheet range into a styled Google Sheets table in one batchUpdate transaction.

        Falls back gracefully to banding + basic filter + formatting if `addTable` fails
        (e.g., an overlapping table already exists).
        """
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            headers = {"Authorization": f"Bearer {token}"}
            grid_range = await self._resolve_grid_range(client, headers, spreadsheet_id, range_a1)

            primary_reqs = _build_format_table_requests(
                grid_range,
                table_name=table_name,
                include_native_table=convert_to_native_table,
                include_basic_filter=False,
                header_bg_hex=header_bg_hex,
                header_text_hex=header_text_hex,
                zebra_striping=zebra_striping,
                add_borders=add_borders,
                auto_resize_columns=auto_resize_columns,
                wrap_text=wrap_text,
                freeze_header_row=freeze_header_row,
            )

            batch_url = f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate"
            resp = await client.post(batch_url, headers=headers, json={"requests": primary_reqs})

            native_applied = convert_to_native_table
            fallback_used = False

            if resp.status_code >= 400 and convert_to_native_table:
                logger.info(
                    "Native addTable failed; falling back to banding + basic filter formatting",
                    extra={"spreadsheet_id": spreadsheet_id, "status_code": resp.status_code},
                )
                fallback_used = True
                native_applied = False
                fallback_reqs = _build_format_table_requests(
                    grid_range,
                    table_name=table_name,
                    include_native_table=False,
                    include_basic_filter=True,
                    header_bg_hex=header_bg_hex,
                    header_text_hex=header_text_hex,
                    zebra_striping=zebra_striping,
                    add_borders=add_borders,
                    auto_resize_columns=auto_resize_columns,
                    wrap_text=wrap_text,
                    freeze_header_row=freeze_header_row,
                )
                resp = await client.post(batch_url, headers=headers, json={"requests": fallback_reqs})
                if resp.status_code >= 400:
                    # Final defensive fallback without banding/filter if those already exist too
                    minimal_reqs = _build_format_table_requests(
                        grid_range,
                        table_name=table_name,
                        include_native_table=False,
                        include_basic_filter=False,
                        header_bg_hex=header_bg_hex,
                        header_text_hex=header_text_hex,
                        zebra_striping=False,
                        add_borders=add_borders,
                        auto_resize_columns=auto_resize_columns,
                        wrap_text=wrap_text,
                        freeze_header_row=freeze_header_row,
                    )
                    resp = await client.post(
                        batch_url, headers=headers, json={"requests": minimal_reqs}
                    )

            resp.raise_for_status()
            batch_data = resp.json()

        result = {
            "citation": "[MCP: sheets]",
            "status": "formatted",
            "account": acct,
            "spreadsheet_id": spreadsheet_id,
            "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "range": range_a1,
            "grid_range": grid_range,
            "table_name": table_name,
            "native_table_applied": native_applied,
            "fallback_used": fallback_used,
            "replies_count": len(batch_data.get("replies") or []),
        }
        return wrap_untrusted_data("google_sheets:format_table", json.dumps(result, ensure_ascii=False))

    async def sheets_apply_formatting(
        self,
        spreadsheet_id: str,
        range_a1: str,
        background_hex: str | None = None,
        text_hex: str | None = None,
        bold: bool | None = None,
        font_size: int | None = None,
        horizontal_alignment: Literal["LEFT", "CENTER", "RIGHT"] | None = None,
        vertical_alignment: Literal["TOP", "MIDDLE", "BOTTOM"] | None = None,
        wrap_strategy: Literal["OVERFLOW_CELL", "CLIP", "WRAP"] | None = None,
        add_borders: bool = False,
        column_width_px: int | None = None,
        conditional_rules: list[dict[str, Any]] | None = None,
        account: GoogleAccountName = "private",
    ) -> str:
        """
        Apply cell styles, borders, column pixel width, and conditional formatting rules via batchUpdate.

        `conditional_rules` accepts a list of dicts, e.g.:
        `[{"text_contains": "選考中", "background_hex": "#DCFCE7", "text_hex": "#166534", "bold": True}]`
        """
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            headers = {"Authorization": f"Bearer {token}"}
            grid_range = await self._resolve_grid_range(client, headers, spreadsheet_id, range_a1)

            requests: list[dict[str, Any]] = []
            cell_format: dict[str, Any] = {}
            field_paths: list[str] = []

            if background_hex:
                cell_format["backgroundColor"] = _hex_to_sheets_color(background_hex)
                field_paths.append("userEnteredFormat.backgroundColor")

            text_format: dict[str, Any] = {}
            if text_hex:
                text_format["foregroundColor"] = _hex_to_sheets_color(text_hex)
                field_paths.append("userEnteredFormat.textFormat.foregroundColor")
            if bold is not None:
                text_format["bold"] = bool(bold)
                field_paths.append("userEnteredFormat.textFormat.bold")
            if font_size is not None:
                text_format["fontSize"] = max(6, min(72, int(font_size)))
                field_paths.append("userEnteredFormat.textFormat.fontSize")
            if text_format:
                cell_format["textFormat"] = text_format

            if horizontal_alignment:
                cell_format["horizontalAlignment"] = horizontal_alignment
                field_paths.append("userEnteredFormat.horizontalAlignment")
            if vertical_alignment:
                cell_format["verticalAlignment"] = vertical_alignment
                field_paths.append("userEnteredFormat.verticalAlignment")
            if wrap_strategy:
                cell_format["wrapStrategy"] = wrap_strategy
                field_paths.append("userEnteredFormat.wrapStrategy")

            if cell_format and field_paths:
                requests.append(
                    {
                        "repeatCell": {
                            "range": grid_range,
                            "cell": {"userEnteredFormat": cell_format},
                            "fields": ",".join(field_paths),
                        }
                    }
                )

            if add_borders:
                border_style = {
                    "style": "SOLID",
                    "width": 1,
                    "color": {"red": 0.58, "green": 0.639, "blue": 0.722},
                }
                requests.append(
                    {
                        "updateBorders": {
                            "range": grid_range,
                            "top": border_style,
                            "bottom": border_style,
                            "left": border_style,
                            "right": border_style,
                            "innerHorizontal": border_style,
                            "innerVertical": border_style,
                        }
                    }
                )

            if column_width_px is not None:
                start_col = int(grid_range.get("startColumnIndex", 0))
                dim_range: dict[str, Any] = {
                    "sheetId": int(grid_range["sheetId"]),
                    "dimension": "COLUMNS",
                    "startIndex": start_col,
                }
                if "endColumnIndex" in grid_range:
                    dim_range["endIndex"] = int(grid_range["endColumnIndex"])
                requests.append(
                    {
                        "updateDimensionProperties": {
                            "range": dim_range,
                            "properties": {"pixelSize": max(20, min(1000, int(column_width_px)))},
                            "fields": "pixelSize",
                        }
                    }
                )

            for rule_idx, rule in enumerate(conditional_rules or []):
                if not isinstance(rule, dict):
                    continue
                exact_val = rule.get("exact_text")
                contains_val = rule.get("text_contains") or rule.get("contains") or rule.get("value")
                if exact_val is not None and str(exact_val) != "":
                    cond_type = "TEXT_EQ"
                    cond_val = str(exact_val)
                elif contains_val is not None and str(contains_val) != "":
                    cond_type = "TEXT_CONTAINS"
                    cond_val = str(contains_val)
                else:
                    continue

                cond_fmt: dict[str, Any] = {}
                if rule.get("background_hex"):
                    cond_fmt["backgroundColor"] = _hex_to_sheets_color(str(rule["background_hex"]))
                cond_tf: dict[str, Any] = {}
                if rule.get("text_hex"):
                    cond_tf["foregroundColor"] = _hex_to_sheets_color(str(rule["text_hex"]))
                if "bold" in rule and rule["bold"] is not None:
                    cond_tf["bold"] = bool(rule["bold"])
                if cond_tf:
                    cond_fmt["textFormat"] = cond_tf
                if not cond_fmt:
                    continue

                requests.append(
                    {
                        "addConditionalFormatRule": {
                            "rule": {
                                "ranges": [grid_range],
                                "booleanRule": {
                                    "condition": {
                                        "type": cond_type,
                                        "values": [{"userEnteredValue": cond_val}],
                                    },
                                    "format": cond_fmt,
                                },
                            },
                            "index": rule_idx,
                        }
                    }
                )

            if not requests:
                raise ValueError(
                    "At least one formatting option, column width, border, or conditional rule must be specified"
                )

            resp = await client.post(
                f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}:batchUpdate",
                headers=headers,
                json={"requests": requests},
            )
            resp.raise_for_status()
            batch_data = resp.json()

        result = {
            "citation": "[MCP: sheets]",
            "status": "formatting_applied",
            "account": acct,
            "spreadsheet_id": spreadsheet_id,
            "url": f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit",
            "range": range_a1,
            "grid_range": grid_range,
            "applied_requests_count": len(requests),
            "replies_count": len(batch_data.get("replies") or []),
        }
        return wrap_untrusted_data(
            "google_sheets:apply_formatting", json.dumps(result, ensure_ascii=False)
        )

    # ------------------------------------------------------------------
    # Google Slides v1
    # ------------------------------------------------------------------

    async def slides_create_presentation(
        self,
        title: str,
        initial_slide_title: str = "",
        initial_slide_body: str = "",
        account: GoogleAccountName = "private",
    ) -> str:
        """Create a Google Slides presentation and optionally populate an initial slide."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.post(
                "https://slides.googleapis.com/v1/presentations",
                headers=headers,
                json={"title": title},
            )
            resp.raise_for_status()
            pres = resp.json()
            pres_id = pres.get("presentationId", "")

            created_slide_id: str | None = None
            if initial_slide_title or initial_slide_body:
                created_slide_id, reqs = _build_create_slide_requests(
                    initial_slide_title, initial_slide_body
                )
                batch_resp = await client.post(
                    f"https://slides.googleapis.com/v1/presentations/{pres_id}:batchUpdate",
                    headers=headers,
                    json={"requests": reqs},
                )
                batch_resp.raise_for_status()

        preview_slides: list[dict[str, Any]] = []
        if initial_slide_title or initial_slide_body:
            preview_slides.append(
                {
                    "slide_number": 1,
                    "object_id": created_slide_id or "",
                    "title": initial_slide_title,
                    "summary": initial_slide_body or initial_slide_title,
                }
            )
        elif pres.get("slides"):
            preview_slides = _build_preview_slides(_extract_slides_text(pres))

        result = {
            "citation": "[MCP: slides]",
            "status": "created",
            "account": acct,
            "presentation_id": pres_id,
            "title": pres.get("title", title),
            "url": f"https://docs.google.com/presentation/d/{pres_id}/edit",
            "initial_slide_id": created_slide_id,
            "preview_slides": preview_slides,
        }
        return wrap_untrusted_data("google_slides:create", json.dumps(result, ensure_ascii=False))

    async def slides_get_presentation(
        self,
        presentation_id: str,
        account: GoogleAccountName = "private",
    ) -> str:
        """Fetch a Google Slides presentation and return URL + structured slide previews."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.get(
                f"https://slides.googleapis.com/v1/presentations/{presentation_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            pres = resp.json()

        resolved_id = pres.get("presentationId", presentation_id)
        slides = _extract_slides_text(pres)
        result = {
            "citation": "[MCP: slides]",
            "account": acct,
            "presentation_id": resolved_id,
            "title": pres.get("title", presentation_id),
            "url": f"https://docs.google.com/presentation/d/{resolved_id}/edit",
            "preview_slides": _build_preview_slides(slides),
            "slides": slides,
        }
        return wrap_untrusted_data(f"google_slides:{presentation_id}", json.dumps(result, ensure_ascii=False))

    async def slides_add_slide(
        self,
        presentation_id: str,
        title: str = "",
        body: str = "",
        account: GoogleAccountName = "private",
    ) -> str:
        """Append a new TITLE_AND_BODY slide to a Google Slides presentation."""
        acct = self._resolve_single_account(account)
        slide_id, reqs = _build_create_slide_requests(title, body)
        preview_slides: list[dict[str, Any]] = []
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            headers = {"Authorization": f"Bearer {token}"}
            resp = await client.post(
                f"https://slides.googleapis.com/v1/presentations/{presentation_id}:batchUpdate",
                headers=headers,
                json={"requests": reqs},
            )
            resp.raise_for_status()

            try:
                get_resp = await client.get(
                    f"https://slides.googleapis.com/v1/presentations/{presentation_id}",
                    headers=headers,
                )
                get_resp.raise_for_status()
                preview_slides = _build_preview_slides(_extract_slides_text(get_resp.json()))
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise

        if not any(s.get("object_id") == slide_id for s in preview_slides):
            preview_slides.append(
                {
                    "slide_number": len(preview_slides) + 1,
                    "object_id": slide_id,
                    "title": title,
                    "summary": body or title or "(空スライド)",
                }
            )

        result = {
            "citation": "[MCP: slides]",
            "status": "slide_added",
            "account": acct,
            "presentation_id": presentation_id,
            "url": f"https://docs.google.com/presentation/d/{presentation_id}/edit",
            "slide_id": slide_id,
            "preview_slides": preview_slides,
        }
        return wrap_untrusted_data("google_slides:add_slide", json.dumps(result, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Google Tasks v1
    # ------------------------------------------------------------------

    async def _fetch_account_tasks(
        self,
        account: GoogleAccountName,
        tasklist_id: str,
        show_completed: bool,
    ) -> list[dict[str, Any]]:
        async with self._create_client() as client:
            token = await self._get_access_token(client, account)
            resp = await client.get(
                f"https://tasks.googleapis.com/tasks/v1/lists/{quote(tasklist_id, safe='')}/tasks",
                headers={"Authorization": f"Bearer {token}"},
                params={"showCompleted": "true" if show_completed else "false"},
            )
            resp.raise_for_status()
            return resp.json().get("items") or []

    async def tasks_list(
        self,
        tasklist_id: str = "@default",
        show_completed: bool = False,
        account: GoogleAccountScope = "all",
    ) -> str:
        """List Google Tasks items across private and/or school accounts."""
        per_account = await self._run_across_accounts(
            account,
            lambda acct: self._fetch_account_tasks(acct, tasklist_id, show_completed),
        )
        lines: list[str] = []
        for acct, items in per_account:
            for item in items:
                lines.append(
                    f"- [{acct}] [MCP: tasks] [{item.get('status', 'needsAction')}] "
                    f"{item.get('title', '(No Title)')} (id={item.get('id', '')}, due={item.get('due', '')})"
                )
        if not lines:
            return wrap_untrusted_data("google_tasks:list", "[MCP: tasks] タスクはありません。")
        return wrap_untrusted_data("google_tasks:list", "\n".join(lines))

    async def tasks_create(
        self,
        title: str,
        notes: str = "",
        due: str | None = None,
        tasklist_id: str = "@default",
        account: GoogleAccountName = "private",
    ) -> str:
        """Create a Google Task (defaults to private account)."""
        acct = self._resolve_single_account(account)
        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            body["due"] = due

        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.post(
                f"https://tasks.googleapis.com/tasks/v1/lists/{quote(tasklist_id, safe='')}/tasks",
                headers={"Authorization": f"Bearer {token}"},
                json=body,
            )
            resp.raise_for_status()
            created = resp.json()

        result = {
            "citation": "[MCP: tasks]",
            "status": "created",
            "account": acct,
            "task_id": created.get("id", ""),
            "title": created.get("title", title),
        }
        return wrap_untrusted_data("google_tasks:create", json.dumps(result, ensure_ascii=False))

    async def tasks_complete(
        self,
        task_id: str,
        tasklist_id: str = "@default",
        account: GoogleAccountName = "private",
    ) -> str:
        """Mark a Google Task as completed (defaults to private account)."""
        acct = self._resolve_single_account(account)
        async with self._create_client() as client:
            token = await self._get_access_token(client, acct)
            resp = await client.patch(
                f"https://tasks.googleapis.com/tasks/v1/lists/{quote(tasklist_id, safe='')}/tasks/{quote(task_id, safe='')}",
                headers={"Authorization": f"Bearer {token}"},
                json={"status": "completed"},
            )
            resp.raise_for_status()
            updated = resp.json()

        result = {
            "citation": "[MCP: tasks]",
            "status": updated.get("status", "completed"),
            "account": acct,
            "task_id": updated.get("id", task_id),
        }
        return wrap_untrusted_data("google_tasks:complete", json.dumps(result, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Google Contacts (People API v1)
    # ------------------------------------------------------------------

    async def _search_account_contacts(
        self,
        account: GoogleAccountName,
        query: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        async with self._create_client() as client:
            token = await self._get_access_token(client, account)
            resp = await client.get(
                "https://people.googleapis.com/v1/people:searchContacts",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "query": query,
                    "readMask": "names,emailAddresses,phoneNumbers,organizations",
                    "pageSize": max(1, min(30, int(limit))),
                },
            )
            resp.raise_for_status()
            return resp.json().get("results") or []

    async def contacts_search(
        self,
        query: str,
        limit: int = 10,
        account: GoogleAccountScope = "all",
    ) -> str:
        """Search Google Contacts by name or email address across requested accounts."""
        per_account = await self._run_across_accounts(
            account,
            lambda acct: self._search_account_contacts(acct, query, limit),
        )
        lines: list[str] = []
        for acct, results in per_account:
            for item in results:
                person = item.get("person") or {}
                names = [n.get("displayName", "") for n in (person.get("names") or []) if n.get("displayName")]
                emails = [e.get("value", "") for e in (person.get("emailAddresses") or []) if e.get("value")]
                phones = [p.get("value", "") for p in (person.get("phoneNumbers") or []) if p.get("value")]
                display = names[0] if names else "(No Name)"
                lines.append(
                    f"- [{acct}] [MCP: contacts] {display} | emails={', '.join(emails)} | phones={', '.join(phones)}"
                )
        if not lines:
            return wrap_untrusted_data(
                "google_contacts:search",
                f"[MCP: contacts] '{query}' に一致する連絡先はありません。",
            )
        return wrap_untrusted_data("google_contacts:search", "\n".join(lines))

    # ------------------------------------------------------------------
    # Google Maps (Places API New & Routes API)
    # ------------------------------------------------------------------

    async def maps_search_places(
        self,
        query: str,
        max_results: int = 5,
        language_code: str = "ja",
    ) -> str:
        """Search places via Google Places API (New) using GOOGLE_MAPS_API_KEY."""
        if not self._maps_api_key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY is not configured")

        async with self._create_client() as client:
            resp = await client.post(
                "https://places.googleapis.com/v1/places:searchText",
                headers={
                    "X-Goog-Api-Key": self._maps_api_key,
                    "X-Goog-FieldMask": (
                        "places.id,places.displayName,places.formattedAddress,"
                        "places.rating,places.googleMapsUri"
                    ),
                },
                json={
                    "textQuery": query,
                    "maxResultCount": max(1, min(20, int(max_results))),
                    "languageCode": language_code,
                },
            )
            resp.raise_for_status()
            places = resp.json().get("places") or []

        if not places:
            return wrap_untrusted_data(
                "google_maps:places", f"[MCP: maps] '{query}' に一致する場所は見つかりませんでした。"
            )

        lines = []
        for place in places:
            name = (place.get("displayName") or {}).get("text", "(No Name)")
            addr = place.get("formattedAddress", "")
            rating = place.get("rating", "-")
            uri = place.get("googleMapsUri", "")
            lines.append(f"- [MCP: maps] {name} ({addr}) rating={rating} url={uri}")
        return wrap_untrusted_data("google_maps:places", "\n".join(lines))

    async def maps_compute_route(
        self,
        origin: str,
        destination: str,
        travel_mode: TravelMode = "TRANSIT",
    ) -> str:
        """Compute travel duration and distance between `origin` and `destination` via Routes API."""
        if not self._maps_api_key:
            raise RuntimeError("GOOGLE_MAPS_API_KEY is not configured")

        async with self._create_client() as client:
            resp = await client.post(
                "https://routes.googleapis.com/directions/v2:computeRoutes",
                headers={
                    "X-Goog-Api-Key": self._maps_api_key,
                    "X-Goog-FieldMask": (
                        "routes.duration,routes.distanceMeters,routes.localizedValues"
                    ),
                },
                json={
                    "origin": {"address": origin},
                    "destination": {"address": destination},
                    "travelMode": travel_mode,
                },
            )
            resp.raise_for_status()
            routes = resp.json().get("routes") or []

        if not routes:
            return wrap_untrusted_data(
                "google_maps:route",
                f"[MCP: maps] {origin} -> {destination} ({travel_mode}) のルートが見つかりませんでした。",
            )

        top = routes[0]
        payload = {
            "citation": "[MCP: maps]",
            "origin": origin,
            "destination": destination,
            "travel_mode": travel_mode,
            "distance_meters": top.get("distanceMeters"),
            "duration": top.get("duration"),
            "localized": top.get("localizedValues") or {},
        }
        return wrap_untrusted_data("google_maps:route", json.dumps(payload, ensure_ascii=False))

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------

    def get_tools(self) -> list[MCPTool]:
        tools: list[MCPTool] = []

        if self.has_oauth_configured():
            tools.extend(
                [
                    MCPTool(
                        name="calendar_list_calendars",
                        description="List all registered Google Calendars (My Calendars & Other Calendars) across private/school/all accounts.",
                        handler=self.calendar_list_calendars,
                        requires_approval=False,
                        source_type="google_calendar",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="calendar_list_events",
                        description="List Google Calendar events across private/school/all accounts and selected sub-calendars (calendar_id='all').",
                        handler=self.calendar_list_events,
                        requires_approval=False,
                        source_type="google_calendar",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="calendar_check_conflicts",
                        description="Check overlapping calendar events across private/school/all accounts and selected sub-calendars (calendar_id='all').",
                        handler=self.calendar_check_conflicts,
                        requires_approval=False,
                        source_type="google_calendar",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="calendar_create_event",
                        description="Create a Google Calendar event on primary or named sub-calendar (requires approval_token; defaults to private/primary).",
                        handler=self.calendar_create_event,
                        requires_approval=True,
                        source_type="google_calendar",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="calendar_update_event",
                        description="Update a Google Calendar event (requires approval_token; defaults to private).",
                        handler=self.calendar_update_event,
                        requires_approval=True,
                        source_type="google_calendar",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="calendar_delete_event",
                        description="Delete a Google Calendar event (requires approval_token; defaults to private).",
                        handler=self.calendar_delete_event,
                        requires_approval=True,
                        source_type="google_calendar",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="drive_search_files",
                        description="Search Google Drive files across private/school/all accounts.",
                        handler=self.drive_search_files,
                        requires_approval=False,
                        source_type="google_drive",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="drive_read_text_file",
                        description="Read text content from a Google Doc or Drive text file (defaults to private).",
                        handler=self.drive_read_text_file,
                        requires_approval=False,
                        source_type="google_drive",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="drive_create_folder",
                        description="Create a folder in Google Drive (defaults to private).",
                        handler=self.drive_create_folder,
                        requires_approval=False,
                        source_type="google_drive",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="drive_move_or_rename",
                        description="Move or rename a Google Drive file (defaults to private).",
                        handler=self.drive_move_or_rename,
                        requires_approval=False,
                        source_type="google_drive",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="docs_create_document",
                        description="Create a new Google Doc with optional initial text (defaults to private).",
                        handler=self.docs_create_document,
                        requires_approval=False,
                        source_type="google_docs",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="docs_get_document",
                        description="Retrieve structured text contents from a Google Doc (defaults to private).",
                        handler=self.docs_get_document,
                        requires_approval=False,
                        source_type="google_docs",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="docs_append_text",
                        description="Append text at the end of a Google Doc (defaults to private).",
                        handler=self.docs_append_text,
                        requires_approval=False,
                        source_type="google_docs",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="docs_replace_text",
                        description="Replace all matching text inside a Google Doc (defaults to private).",
                        handler=self.docs_replace_text,
                        requires_approval=False,
                        source_type="google_docs",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="sheets_create_spreadsheet",
                        description="Create a new Google Spreadsheet with optional initial rows (defaults to private).",
                        handler=self.sheets_create_spreadsheet,
                        requires_approval=False,
                        source_type="google_sheets",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="sheets_read_range",
                        description="Read cell values from a Google Sheet range in A1 notation (defaults to private).",
                        handler=self.sheets_read_range,
                        requires_approval=False,
                        source_type="google_sheets",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="sheets_append_rows",
                        description="Append rows to a Google Sheet range (defaults to private).",
                        handler=self.sheets_append_rows,
                        requires_approval=False,
                        source_type="google_sheets",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="sheets_update_values",
                        description="Update cell values at an A1 range in a Google Sheet (defaults to private).",
                        handler=self.sheets_update_values,
                        requires_approval=False,
                        source_type="google_sheets",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="sheets_format_table",
                        description="Convert a spreadsheet range into a native Google Sheets Table and apply header styling, borders, zebra striping, wrap text, freeze header, and auto column sizing (defaults to private).",
                        handler=self.sheets_format_table,
                        requires_approval=False,
                        source_type="google_sheets",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="sheets_apply_formatting",
                        description="Apply cell colors, bold/font size, alignment, borders, column widths, or text-matching conditional formatting rules to a Google Sheet range (defaults to private).",
                        handler=self.sheets_apply_formatting,
                        requires_approval=False,
                        source_type="google_sheets",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="slides_create_presentation",
                        description="Create a Google Slides presentation with an optional initial slide (defaults to private).",
                        handler=self.slides_create_presentation,
                        requires_approval=False,
                        source_type="google_slides",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="slides_get_presentation",
                        description="Read text contents from all slides in a presentation (defaults to private).",
                        handler=self.slides_get_presentation,
                        requires_approval=False,
                        source_type="google_slides",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="slides_add_slide",
                        description="Add a new slide with title and body text to a presentation (defaults to private).",
                        handler=self.slides_add_slide,
                        requires_approval=False,
                        source_type="google_slides",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="tasks_list",
                        description="List Google Tasks across private/school/all accounts.",
                        handler=self.tasks_list,
                        requires_approval=False,
                        source_type="google_tasks",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="tasks_create",
                        description="Create a task in Google Tasks (defaults to private).",
                        handler=self.tasks_create,
                        requires_approval=False,
                        source_type="google_tasks",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="tasks_complete",
                        description="Mark a Google Task as completed (defaults to private).",
                        handler=self.tasks_complete,
                        requires_approval=False,
                        source_type="google_tasks",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="contacts_search",
                        description="Search Google Contacts by name or email across private/school/all accounts.",
                        handler=self.contacts_search,
                        requires_approval=False,
                        source_type="google_contacts",
                        already_wrapped=True,
                    ),
                ]
            )

        if self._maps_api_key:
            tools.extend(
                [
                    MCPTool(
                        name="maps_search_places",
                        description="Search places via Google Maps Places API (New).",
                        handler=self.maps_search_places,
                        requires_approval=False,
                        source_type="google_maps",
                        already_wrapped=True,
                    ),
                    MCPTool(
                        name="maps_compute_route",
                        description="Compute travel time and distance between origin and destination via Google Maps Routes API.",
                        handler=self.maps_compute_route,
                        requires_approval=False,
                        source_type="google_maps",
                        already_wrapped=True,
                    ),
                ]
            )

        return tools
