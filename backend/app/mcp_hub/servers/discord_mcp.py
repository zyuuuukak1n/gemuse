"""Multi-server Discord REST connector with strict YAML permission whitelisting."""

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

import httpx
import yaml
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.logging import get_logger, mask_secrets
from app.core.security import ApprovalRequiredError, ApprovalService, wrap_untrusted_data
from app.mcp_hub.base import BaseMCPConnector, MCPTool

logger = get_logger(__name__)

PermissionLevel = Literal["disabled", "read_only", "read_write"]

CHANNEL_TYPE_LABELS: dict[int, str] = {
    0: "text",
    5: "announcement",
    11: "public_thread",
    12: "private_thread",
    15: "forum",
    16: "media",
}
READABLE_CHANNEL_TYPES = tuple(CHANNEL_TYPE_LABELS.keys())
FORUM_CHANNEL_TYPES = (15, 16)
MAX_THREAD_FETCH_CONCURRENCY = 5
MAX_ARCHIVED_THREAD_PAGES = 50
FORUM_THREAD_MESSAGE_LIMIT = 10
DEFAULT_FORUM_MAX_CHARS = 24000
MAX_RATE_LIMIT_RETRIES = 3
BULK_THREAD_CREATE_INTERVAL_SECONDS = 0.6


class GuildPermission(BaseModel):
    """Per-guild permission entry parsed from config/discord_permissions.yaml."""

    guild_id: str = Field(..., min_length=1)
    name: str = ""
    permission: PermissionLevel = "disabled"
    allowed_channels: list[str] = Field(default_factory=list)


class DiscordPermissionsConfig(BaseModel):
    """Parsed structure of config/discord_permissions.yaml."""

    version: str = "1.0.0"
    default_policy: PermissionLevel = "disabled"
    servers: list[GuildPermission] = Field(default_factory=list)


def load_discord_permissions(path: Path) -> DiscordPermissionsConfig:
    """Load and validate Discord server permission policies from YAML."""
    if not path.exists():
        return DiscordPermissionsConfig()

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    normalized_servers: list[dict[str, Any]] = []
    for item in raw.get("servers") or []:
        entry = dict(item)
        if "permission" not in entry and "read_only" in entry:
            entry["permission"] = "read_only" if entry["read_only"] else "read_write"
        entry["guild_id"] = str(entry.get("guild_id", ""))
        entry["allowed_channels"] = [str(c) for c in (entry.get("allowed_channels") or [])]
        normalized_servers.append(entry)

    default_pol = raw.get("default_policy", "disabled")
    if default_pol not in ("disabled", "read_only", "read_write"):
        default_pol = "disabled"

    return DiscordPermissionsConfig(
        version=str(raw.get("version", "1.0.0")),
        default_policy=default_pol,
        servers=[GuildPermission.model_validate(s) for s in normalized_servers],
    )


def _match_guild_name(candidate: str, guilds: list[tuple[str, str]]) -> str | None:
    """Resolve a guild name against (guild_id, name) pairs via exact then partial match."""
    target = candidate.strip().lower()
    if not target:
        return None
    for gid, name in guilds:
        if name and name.strip().lower() == target:
            return gid
    for gid, name in guilds:
        norm_name = name.strip().lower()
        if norm_name and (target in norm_name or norm_name in target):
            return gid
    return None


def _extract_searchable_text(msg: dict[str, Any]) -> str:
    """Collect text across message content and embeds for case-insensitive matching."""
    parts: list[str] = []
    content = msg.get("content")
    if isinstance(content, str) and content:
        parts.append(content)
    for emb in msg.get("embeds") or []:
        if isinstance(emb, dict):
            for key in ("title", "description"):
                val = emb.get(key)
                if isinstance(val, str) and val:
                    parts.append(val)
    return "\n".join(parts)


def _build_forum_tag_map(channel_data: dict[str, Any] | None) -> dict[str, str]:
    """Map forum tag_id -> formatted tag display name ('<emoji> <name>' or '<name>')."""
    if not isinstance(channel_data, dict):
        return {}
    tag_map: dict[str, str] = {}
    for raw_tag in channel_data.get("available_tags") or []:
        if not isinstance(raw_tag, dict):
            continue
        tag_id = str(raw_tag.get("id", "")).strip()
        name = str(raw_tag.get("name", "")).strip()
        emoji_name = str(raw_tag.get("emoji_name") or "").strip()
        if not tag_id or not name:
            continue
        tag_map[tag_id] = f"{emoji_name} {name}".strip() if emoji_name else name
    return tag_map


def _format_thread_tags(thread: dict[str, Any], tag_map: dict[str, str]) -> str:
    """Resolve thread applied_tags IDs via tag_map into a comma-separated string."""
    raw_ids = thread.get("applied_tags") or []
    resolved: list[str] = []
    for raw_id in raw_ids:
        tid = str(raw_id).strip()
        if tid in tag_map:
            resolved.append(tag_map[tid])
    return ", ".join(resolved) if resolved else "なし"


_CHANNEL_TYPE_NAME_TO_INT: dict[str, int] = {
    "text": 0,
    "voice": 2,
    "category": 4,
    "announcement": 5,
    "news": 5,
    "forum": 15,
    "media": 16,
}


def _resolve_forum_tag_ids(
    requested_tags: list[str] | None,
    available_tags: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """
    Resolve tag names (with or without emoji) or tag IDs against a forum's `available_tags`.

    Returns `(resolved_tag_ids, resolved_tag_labels)` preserving order and deduplicating.
    """
    if not requested_tags or not available_tags:
        return [], []

    entries: list[tuple[str, str, str, str]] = []
    for raw in available_tags:
        if not isinstance(raw, dict):
            continue
        tid = str(raw.get("id", "")).strip()
        name = str(raw.get("name", "")).strip()
        emoji = str(raw.get("emoji_name") or "").strip()
        if not tid or not name:
            continue
        display = f"{emoji} {name}".strip() if emoji else name
        entries.append((tid, name.lower(), display.lower(), display))

    matched_ids: list[str] = []
    matched_labels: list[str] = []
    seen_ids: set[str] = set()

    for raw_req in requested_tags:
        q = str(raw_req).strip().lower()
        if not q:
            continue
        chosen: tuple[str, str] | None = None
        # 1. Exact match on tag_id, plain name, or emoji+name
        for tid, name_l, disp_l, display in entries:
            if q in (tid.lower(), name_l, disp_l):
                chosen = (tid, display)
                break
        # 2. Substring partial match (emoji-agnostic)
        if chosen is None:
            for tid, name_l, disp_l, display in entries:
                if q in name_l or name_l in q or q in disp_l:
                    chosen = (tid, display)
                    break
        if chosen is not None and chosen[0] not in seen_ids:
            seen_ids.add(chosen[0])
            matched_ids.append(chosen[0])
            matched_labels.append(chosen[1])

    return matched_ids, matched_labels


def _split_discord_message(content: str, max_len: int = 2000) -> list[str]:
    """Split a long Discord message into <= max_len chunks, preferring newline boundaries."""
    if len(content) <= max_len:
        return [content]
    chunks: list[str] = []
    remaining = content
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        split_at = remaining.rfind("\n", 0, max_len)
        if split_at <= max_len // 3:
            split_at = remaining.rfind(" ", 0, max_len)
        if split_at <= 0:
            split_at = max_len
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip("\n")
    return chunks


def _titles_match_similar(target_title: str, existing_title: str) -> bool:
    """Detect duplicate/similar thread titles (normalized exact or >=3-char containment)."""
    a = " ".join(target_title.replace("\u3000", " ").split()).lower()
    b = " ".join(existing_title.replace("\u3000", " ").split()).lower()
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return len(shorter) >= 3 and shorter in longer


class _RateLimitRetryClient:
    """AsyncClient wrapper retrying HTTP 429 Too Many Requests using Discord `retry_after`."""

    def __init__(
        self,
        inner: httpx.AsyncClient,
        *,
        max_retries: int = MAX_RATE_LIMIT_RETRIES,
    ) -> None:
        self._inner = inner
        self._max_retries = max_retries

    async def __aenter__(self) -> "_RateLimitRetryClient":
        await self._inner.__aenter__()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self._inner.__aexit__(*args)

    async def request(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        resp = await self._inner.request(method, url, **kwargs)
        for attempt in range(self._max_retries):
            if resp.status_code != 429:
                return resp
            delay = 1.0
            try:
                body = resp.json()
                if isinstance(body, dict) and body.get("retry_after") is not None:
                    delay = float(body["retry_after"])
            except Exception:
                header_val = resp.headers.get("Retry-After")
                if header_val:
                    try:
                        delay = float(header_val)
                    except ValueError:
                        pass
            delay = max(0.0, min(30.0, delay))
            logger.warning(
                "Discord API rate limited (429); sleeping before retry",
                extra={"url": url, "retry_after": delay, "attempt": attempt + 1},
            )
            await asyncio.sleep(delay)
            resp = await self._inner.request(method, url, **kwargs)
        return resp

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("GET", url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("POST", url, **kwargs)

    async def patch(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PATCH", url, **kwargs)

    async def put(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("PUT", url, **kwargs)

    async def delete(self, url: str, **kwargs: Any) -> httpx.Response:
        return await self.request("DELETE", url, **kwargs)


class DiscordMCPConnector(BaseMCPConnector):
    """
    Discord REST API v10 connector managing multiple servers via a single Bot token.

    Enforces `disabled` / `read_only` / `read_write` guild whitelists and
    requires valid ApprovalService tokens for all write actions.
    """

    connector_name = "discord"

    def __init__(
        self,
        *,
        bot_token: str,
        permissions_path: Path,
        approval_service: ApprovalService,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        api_base_url: str = "https://discord.com/api/v10",
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        max_forum_output_chars: int = DEFAULT_FORUM_MAX_CHARS,
        bulk_create_interval_seconds: float = BULK_THREAD_CREATE_INTERVAL_SECONDS,
    ) -> None:
        self._bot_token = bot_token.strip()
        self._permissions_path = Path(permissions_path)
        self._approval_service = approval_service
        self._session_factory = session_factory
        self._api_base_url = api_base_url.rstrip("/")
        self._http_client_factory = http_client_factory
        self._max_forum_output_chars = max(500, int(max_forum_output_chars))
        self._bulk_create_interval_seconds = max(0.0, float(bulk_create_interval_seconds))
        self._permissions = load_discord_permissions(self._permissions_path)

    def is_configured(self) -> bool:
        return bool(self._bot_token and not self._bot_token.startswith("your-"))

    def reload_permissions(self) -> DiscordPermissionsConfig:
        self._permissions = load_discord_permissions(self._permissions_path)
        return self._permissions

    def get_permissions_summary(self) -> dict[str, Any]:
        cfg = self.reload_permissions()
        return cfg.model_dump()

    def _get_guild_policy(self, guild_id: str) -> GuildPermission:
        gid = str(guild_id)
        for server in self._permissions.servers:
            if server.guild_id == gid:
                return server
        return GuildPermission(
            guild_id=gid,
            name=gid,
            permission=self._permissions.default_policy,
        )

    async def _resolve_guild_id(
        self,
        guild_id_or_name: str,
        client: httpx.AsyncClient | None = None,
    ) -> str:
        """
        Resolve a numeric guild_id or server name to a canonical numeric guild_id.

        Checks `discord_permissions.yaml` first (exact ID, exact name, partial name),
        and falls back to `GET /users/@me/guilds` if not matched locally.
        """
        raw = str(guild_id_or_name).strip()
        if not raw:
            return ""

        for server in self._permissions.servers:
            if server.guild_id == raw:
                return raw

        if raw.isdigit():
            return raw

        local_pairs = [(s.guild_id, s.name) for s in self._permissions.servers if s.name]
        matched = _match_guild_name(raw, local_pairs)
        if matched:
            return matched

        try:
            if client is not None:
                resp = await client.get("/users/@me/guilds")
            else:
                async with self._create_client() as temp_client:
                    resp = await temp_client.get("/users/@me/guilds")
            if resp.status_code == 200:
                api_guilds = [
                    (str(g.get("id", "")), str(g.get("name", "")))
                    for g in (resp.json() or [])
                    if isinstance(g, dict) and g.get("id")
                ]
                api_match = _match_guild_name(raw, api_guilds)
                if api_match:
                    return api_match
        except Exception as exc:
            logger.debug(
                "Failed to query /users/@me/guilds during guild resolution",
                extra={"query": raw, "error": mask_secrets(str(exc))},
            )

        return raw

    def _check_access(
        self,
        guild_id: str,
        channel_id: str | None = None,
        *,
        require_write: bool = False,
        parent_channel_id: str | None = None,
    ) -> GuildPermission:
        policy = self._get_guild_policy(guild_id)
        if policy.permission == "disabled":
            raise PermissionError(
                f"Discord guild '{guild_id}' is disabled or not in whitelist"
            )
        if require_write and policy.permission != "read_write":
            raise PermissionError(
                f"Discord guild '{guild_id}' is '{policy.permission}'; writes are blocked"
            )
        if channel_id and policy.allowed_channels:
            allowed_set = set(policy.allowed_channels)
            cid_ok = str(channel_id) in allowed_set
            parent_ok = bool(parent_channel_id and str(parent_channel_id) in allowed_set)
            if not cid_ok and not parent_ok:
                raise PermissionError(
                    f"Discord channel '{channel_id}' is not whitelisted for guild '{guild_id}'"
                )
        return policy

    def _create_client(self) -> _RateLimitRetryClient:
        inner = (
            self._http_client_factory()
            if self._http_client_factory is not None
            else httpx.AsyncClient(
                base_url=self._api_base_url,
                headers={
                    "Authorization": f"Bot {self._bot_token}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
        )
        return _RateLimitRetryClient(inner)

    async def _require_approval(self, action_type: str, approval_token: str) -> None:
        if self._session_factory is None:
            raise ApprovalRequiredError(
                f"Cannot verify approval token for '{action_type}' without DB session",
                action_type=action_type,
            )
        async with self._session_factory() as db:
            await self._approval_service.verify_and_consume_by_token(
                db,
                action_type=action_type,
                raw_token=approval_token,
            )

    async def _collect_forum_threads(
        self,
        client: httpx.AsyncClient,
        guild_id: str,
        forum_channel_id: str,
    ) -> list[dict[str, Any]]:
        """Gather active threads and paginate through all public archived forum threads."""
        target_parent = str(forum_channel_id)
        threads_by_id: dict[str, dict[str, Any]] = {}

        async def _collect_archived_paginated() -> None:
            before: str | None = None
            seen_cursors: set[str] = set()
            for _ in range(MAX_ARCHIVED_THREAD_PAGES):
                params: dict[str, Any] = {"limit": 100}
                if before:
                    params["before"] = before
                resp = await client.get(
                    f"/channels/{target_parent}/threads/archived/public",
                    params=params,
                )
                if resp.status_code != 200:
                    break
                payload = resp.json()
                raw_archived = (
                    payload.get("threads", [])
                    if isinstance(payload, dict)
                    else (payload or [])
                )
                for th in raw_archived:
                    if isinstance(th, dict):
                        tid = str(th.get("id", ""))
                        if tid and tid not in threads_by_id:
                            threads_by_id[tid] = {**th, "_thread_state": "archived"}

                has_more = bool(payload.get("has_more")) if isinstance(payload, dict) else False
                if not has_more or not raw_archived:
                    break

                last_item = raw_archived[-1] if isinstance(raw_archived[-1], dict) else {}
                last_meta = last_item.get("thread_metadata") or {}
                next_before = (
                    last_meta.get("archive_timestamp")
                    if isinstance(last_meta, dict)
                    else None
                )
                if not next_before or str(next_before) in seen_cursors:
                    break
                seen_cursors.add(str(next_before))
                before = str(next_before)

        active_resp, archived_err = await asyncio.gather(
            client.get(f"/guilds/{guild_id}/threads/active"),
            _collect_archived_paginated(),
            return_exceptions=True,
        )
        if isinstance(archived_err, Exception):
            logger.warning(
                "Failed to paginate archived forum threads",
                extra={"channel_id": target_parent, "error": mask_secrets(str(archived_err))},
            )

        if isinstance(active_resp, httpx.Response) and active_resp.status_code == 200:
            payload = active_resp.json()
            raw_active = payload.get("threads", []) if isinstance(payload, dict) else payload
            active_entries: dict[str, dict[str, Any]] = {}
            for th in raw_active or []:
                if isinstance(th, dict) and str(th.get("parent_id", "")) == target_parent:
                    tid = str(th.get("id", ""))
                    if tid and tid not in active_entries:
                        active_entries[tid] = {**th, "_thread_state": "active"}
            # Keep active threads ordered before archived threads without duplicates
            merged: dict[str, dict[str, Any]] = dict(active_entries)
            for tid, th in threads_by_id.items():
                if tid not in merged:
                    merged[tid] = th
            return list(merged.values())

        return list(threads_by_id.values())

    def _render_forum_blocks_with_truncation(
        self,
        summary_block: str,
        thread_entries: list[dict[str, Any]],
    ) -> str:
        """
        Render forum summary + thread blocks within `_max_forum_output_chars`.

        Guarantees all thread titles, tags, and at least the latest message per thread
        are preserved whenever possible before trimming older messages.
        """
        full_blocks = [summary_block]
        for entry in thread_entries:
            lines = [entry["header"]]
            if entry["msg_lines"]:
                lines.extend(entry["msg_lines"])
            else:
                lines.append(entry["empty_line"])
            full_blocks.append("\n".join(lines))

        full_text = "\n\n".join(full_blocks)
        if len(full_text) <= self._max_forum_output_chars:
            return full_text

        # First keep header + latest 1 message for every thread
        extra_counts = [max(0, len(entry["msg_lines"]) - 1) for entry in thread_entries]
        kept_counts = [min(1, len(entry["msg_lines"])) for entry in thread_entries]

        def _assemble(counts: list[int]) -> str:
            parts = [summary_block]
            for entry, keep_n in zip(thread_entries, counts):
                lines = [entry["header"]]
                if entry["msg_lines"]:
                    lines.extend(entry["msg_lines"][:keep_n])
                    omitted = len(entry["msg_lines"]) - keep_n
                    if omitted > 0:
                        lines.append(f"  ... (古いメッセージ {omitted} 件を省略)")
                else:
                    lines.append(entry["empty_line"])
                parts.append("\n".join(lines))
            return "\n\n".join(parts)

        base_text = _assemble(kept_counts)
        if len(base_text) >= self._max_forum_output_chars:
            # Even 1 message per thread exceeds cap: preserve summary + all headers + trimmed latest previews
            minimal_parts = [summary_block]
            for entry in thread_entries:
                if entry["msg_lines"]:
                    latest_preview = entry["msg_lines"][0]
                    if len(latest_preview) > 140:
                        latest_preview = f"{latest_preview[:137]}..."
                    omitted = len(entry["msg_lines"]) - 1
                    block_lines = [entry["header"], latest_preview]
                    if omitted > 0:
                        block_lines.append(f"  ... (古いメッセージ {omitted} 件を省略)")
                    minimal_parts.append("\n".join(block_lines))
                else:
                    minimal_parts.append(f"{entry['header']}\n{entry['empty_line']}")
            return "\n\n".join(minimal_parts)

        # Greedily add older messages within remaining character budget
        current_text = base_text
        for idx, entry in enumerate(thread_entries):
            if extra_counts[idx] <= 0:
                continue
            for next_count in range(2, len(entry["msg_lines"]) + 1):
                candidate_counts = list(kept_counts)
                candidate_counts[idx] = next_count
                candidate_text = _assemble(candidate_counts)
                if len(candidate_text) <= self._max_forum_output_chars:
                    kept_counts[idx] = next_count
                    current_text = candidate_text
                else:
                    break
        return current_text

    async def _read_forum_history(
        self,
        client: httpx.AsyncClient,
        policy: GuildPermission,
        channel_id: str,
        channel_name: str,
        channel_data: dict[str, Any] | None = None,
    ) -> str:
        """Expand all active/archived threads in a forum/media channel with resolved tags."""
        guild_label = policy.name or policy.guild_id
        tag_map = _build_forum_tag_map(channel_data)
        threads = await self._collect_forum_threads(client, policy.guild_id, channel_id)
        if not threads:
            return wrap_untrusted_data(
                f"discord:{guild_label}/#{channel_name}",
                f"[Discord: {guild_label} / #{channel_name} (Forum)] スレッドが見つかりませんでした。",
            )

        sem = asyncio.Semaphore(MAX_THREAD_FETCH_CONCURRENCY)

        async def _fetch_thread_messages(th: dict[str, Any]) -> tuple[list[str], str]:
            tid = str(th.get("id", ""))
            async with sem:
                resp = await client.get(
                    f"/channels/{tid}/messages",
                    params={"limit": FORUM_THREAD_MESSAGE_LIMIT},
                )
            if resp.status_code != 200:
                return [], f"  メッセージ取得失敗 (HTTP {resp.status_code})"

            msgs = resp.json() or []
            if not msgs:
                return [], "  メッセージはありません。"

            msg_lines: list[str] = []
            for m in msgs:
                author = (m.get("author") or {}).get("username", "unknown")
                ts = m.get("timestamp", "")
                text = _extract_searchable_text(m)
                msg_lines.append(f"  ({ts}) @{author} [msg_id={m.get('id')}]: {text}")
            return msg_lines, ""

        fetched = await asyncio.gather(
            *(_fetch_thread_messages(th) for th in threads),
            return_exceptions=True,
        )

        summary_lines = [
            f"[Discord: {guild_label} / #{channel_name} (Forum, guild_id={policy.guild_id})] 合計 {len(threads)} スレッド",
            "【全スレッド一覧サマリー】",
        ]
        thread_entries: list[dict[str, Any]] = []

        for th, res in zip(threads, fetched):
            tid = str(th.get("id", ""))
            title = str(th.get("name", tid))
            state = str(th.get("_thread_state", "active"))
            tags_str = _format_thread_tags(th, tag_map)
            summary_lines.append(f"- {title} | Tags: {tags_str} (status={state}, thread_id={tid})")

            header = (
                f"[Thread: {title} | Tags: {tags_str}] "
                f"(thread_id={tid}, status={state}, guild_id={policy.guild_id})"
            )
            if isinstance(res, Exception):
                thread_entries.append(
                    {
                        "header": header,
                        "msg_lines": [],
                        "empty_line": f"  取得エラー: {mask_secrets(str(res))}",
                    }
                )
            else:
                msg_lines, empty_line = res
                thread_entries.append(
                    {
                        "header": header,
                        "msg_lines": msg_lines,
                        "empty_line": empty_line,
                    }
                )

        rendered_body = self._render_forum_blocks_with_truncation(
            "\n".join(summary_lines),
            thread_entries,
        )
        return wrap_untrusted_data(f"discord:{guild_label}/#{channel_name}", rendered_body)

    async def discord_list_channels(self, guild_id: str | None = None) -> str:
        """List accessible channels across permitted Discord guilds."""
        async with self._create_client() as client:
            if guild_id:
                resolved_gid = await self._resolve_guild_id(guild_id, client=client)
                target_guilds = [self._check_access(resolved_gid)]
            else:
                target_guilds = [s for s in self._permissions.servers if s.permission != "disabled"]

            if not target_guilds:
                return wrap_untrusted_data("discord:channels", "アクセス許可されたDiscordサーバーはありません。")

            async def _fetch_guild(policy: GuildPermission) -> str:
                resp = await client.get(f"/guilds/{policy.guild_id}/channels")
                resp.raise_for_status()
                channels = resp.json()
                lines: list[str] = []
                guild_label = policy.name or policy.guild_id
                for ch in channels:
                    cid = str(ch.get("id", ""))
                    if policy.allowed_channels and cid not in policy.allowed_channels:
                        continue
                    cname = ch.get("name", "unknown")
                    ctype = ch.get("type", 0)
                    if ctype in READABLE_CHANNEL_TYPES:
                        type_label = CHANNEL_TYPE_LABELS.get(ctype, f"type_{ctype}")
                        lines.append(
                            f"- [Discord: {guild_label} / #{cname}] "
                            f"(guild_id={policy.guild_id}, channel_id={cid}, type={type_label}, perm={policy.permission})"
                        )
                return "\n".join(lines)

            results = await asyncio.gather(
                *(_fetch_guild(g) for g in target_guilds),
                return_exceptions=True,
            )

        output_parts: list[str] = []
        for g, res in zip(target_guilds, results):
            if isinstance(res, Exception):
                logger.warning(
                    "Failed to list Discord channels",
                    extra={"guild_id": g.guild_id, "error": mask_secrets(str(res))},
                )
            elif res:
                output_parts.append(res)

        body = "\n".join(output_parts) or "チャンネルが見つかりませんでした。"
        return wrap_untrusted_data("discord:channels", body)

    async def discord_read_history(
        self,
        guild_id: str,
        channel_id: str,
        limit: int = 30,
    ) -> str:
        """Read recent messages from an allowed Discord channel or expand forum threads."""
        clamped_limit = max(1, min(100, int(limit)))

        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            policy = self._check_access(resolved_guild_id, channel_id, require_write=False)

            ch_resp = await client.get(f"/channels/{channel_id}")
            channel_name = channel_id
            channel_type = 0
            ch_data: dict[str, Any] | None = None
            if ch_resp.status_code == 200:
                ch_data = ch_resp.json()
                channel_name = ch_data.get("name", channel_id)
                channel_type = int(ch_data.get("type", 0) or 0)

            if channel_type in FORUM_CHANNEL_TYPES:
                return await self._read_forum_history(
                    client,
                    policy,
                    str(channel_id),
                    str(channel_name),
                    channel_data=ch_data,
                )

            msg_resp = await client.get(
                f"/channels/{channel_id}/messages",
                params={"limit": clamped_limit},
            )
            msg_resp.raise_for_status()
            messages = msg_resp.json()

        guild_label = policy.name or policy.guild_id
        citation = f"[Discord: {guild_label} / #{channel_name}]"
        formatted = []
        for m in messages:
            author = (m.get("author") or {}).get("username", "unknown")
            ts = m.get("timestamp", "")
            text = _extract_searchable_text(m)
            formatted.append(f"{citation} ({ts}) @{author} [msg_id={m.get('id')}]: {text}")

        body = "\n".join(formatted) if formatted else f"{citation} メッセージはありません。"
        return wrap_untrusted_data(f"discord:{guild_label}/#{channel_name}", body)

    async def discord_search_messages(
        self,
        query: str,
        guild_ids: list[str] | None = None,
    ) -> str:
        """Search messages across permitted Discord channels and forum threads using Bot-supported endpoints."""
        cleaned_query = query.strip().lower()
        if not cleaned_query:
            return wrap_untrusted_data("discord:search", "検索クエリが空です。")

        async with self._create_client() as client:
            if guild_ids:
                resolved_ids = await asyncio.gather(
                    *(self._resolve_guild_id(gid, client=client) for gid in guild_ids)
                )
                policies = [self._check_access(gid, require_write=False) for gid in resolved_ids]
            else:
                policies = [s for s in self._permissions.servers if s.permission != "disabled"]

            if not policies:
                return wrap_untrusted_data("discord:search", "検索対象の許可済みサーバーがありません。")

            sem = asyncio.Semaphore(MAX_THREAD_FETCH_CONCURRENCY)

            async def _search_message_target(
                target_id: str,
                citation: str,
            ) -> list[str]:
                async with sem:
                    resp = await client.get(
                        f"/channels/{target_id}/messages",
                        params={"limit": 50},
                    )
                if resp.status_code != 200:
                    return []
                hits: list[str] = []
                for msg in resp.json() or []:
                    text = _extract_searchable_text(msg)
                    if cleaned_query in text.lower():
                        author = (msg.get("author") or {}).get("username", "unknown")
                        hits.append(
                            f"{citation} ({msg.get('timestamp', '')}) @{author}: {text}"
                        )
                return hits

            async def _scan_guild(policy: GuildPermission) -> list[str]:
                ch_resp = await client.get(f"/guilds/{policy.guild_id}/channels")
                ch_resp.raise_for_status()
                allowed_channels = [
                    ch
                    for ch in ch_resp.json()
                    if ch.get("type", 0) in READABLE_CHANNEL_TYPES
                    and (
                        not policy.allowed_channels
                        or str(ch.get("id", "")) in policy.allowed_channels
                    )
                ]

                guild_label = policy.name or policy.guild_id
                scan_targets: list[tuple[str, str]] = []

                forum_channels = [
                    ch for ch in allowed_channels if ch.get("type", 0) in FORUM_CHANNEL_TYPES
                ]
                regular_channels = [
                    ch for ch in allowed_channels if ch.get("type", 0) not in FORUM_CHANNEL_TYPES
                ]

                for ch in regular_channels[:15]:
                    cid = str(ch.get("id", ""))
                    cname = str(ch.get("name", cid))
                    scan_targets.append((cid, f"[Discord: {guild_label} / #{cname}]"))

                if forum_channels:
                    forum_thread_lists = await asyncio.gather(
                        *(
                            self._collect_forum_threads(client, policy.guild_id, str(fc.get("id", "")))
                            for fc in forum_channels
                        ),
                        return_exceptions=True,
                    )
                    for fc, th_list in zip(forum_channels, forum_thread_lists):
                        if not isinstance(th_list, list):
                            continue
                        fc_name = str(fc.get("name", fc.get("id", "")))
                        fc_tag_map = _build_forum_tag_map(fc)
                        for th in th_list:
                            tid = str(th.get("id", ""))
                            tname = str(th.get("name", tid))
                            tag_suffix = f" | Tags: {_format_thread_tags(th, fc_tag_map)}" if fc_tag_map else ""
                            scan_targets.append(
                                (tid, f"[Discord: {guild_label} / #{fc_name} > Thread: {tname}{tag_suffix}]")
                            )

                target_results = await asyncio.gather(
                    *(_search_message_target(tid, cit) for tid, cit in scan_targets),
                    return_exceptions=True,
                )
                collected: list[str] = []
                for tr in target_results:
                    if isinstance(tr, list):
                        collected.extend(tr)
                return collected

            guild_results = await asyncio.gather(
                *(_scan_guild(p) for p in policies),
                return_exceptions=True,
            )

        all_hits: list[str] = []
        for g, res in zip(policies, guild_results):
            if isinstance(res, Exception):
                logger.warning(
                    "Discord guild search failed",
                    extra={"guild_id": g.guild_id, "error": mask_secrets(str(res))},
                )
            elif isinstance(res, list):
                all_hits.extend(res)

        body = "\n".join(all_hits) if all_hits else f"'{query}' に一致するメッセージは見つかりませんでした。"
        return wrap_untrusted_data("discord:search", body)

    async def discord_post_message(
        self,
        guild_id: str,
        channel_id: str,
        content: str,
        approval_token: str = "",
        thread_id: str | None = None,
    ) -> str:
        """Post a message to a read_write channel or thread, splitting chunks > 2000 chars."""
        target_id = (thread_id or channel_id).strip()
        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            parent_id: str | None = channel_id if thread_id else None
            if parent_id is None:
                ch_resp = await client.get(f"/channels/{target_id}")
                if ch_resp.status_code == 200:
                    parent_id = ch_resp.json().get("parent_id")
            policy = self._check_access(
                resolved_guild_id,
                target_id,
                require_write=True,
                parent_channel_id=str(parent_id) if parent_id else None,
            )
            await self._require_approval("discord_post_message", approval_token)

            chunks = _split_discord_message(content, max_len=2000)
            created_ids: list[str] = []
            for chunk in chunks:
                resp = await client.post(
                    f"/channels/{target_id}/messages",
                    json={"content": chunk},
                )
                resp.raise_for_status()
                created_ids.append(str(resp.json().get("id", "")))

        guild_label = policy.name or policy.guild_id
        msg_id = created_ids[0] if created_ids else ""
        extra_note = f" (分割送信 {len(created_ids)}件: {', '.join(created_ids)})" if len(created_ids) > 1 else ""
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            f"[Discord: {guild_label} / #{target_id}] 投稿完了 (message_id={msg_id}){extra_note}",
        )

    async def _post_forum_thread(
        self,
        client: _RateLimitRetryClient,
        channel_id: str,
        *,
        title: str,
        content: str,
        tags: list[str] | None,
        available_tags: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create a single forum thread with resolved tags and >2000-char chunk splitting."""
        tag_ids, tag_labels = _resolve_forum_tag_ids(tags, available_tags)
        chunks = _split_discord_message(content, max_len=2000)
        first_chunk = chunks[0] if chunks else ""
        payload: dict[str, Any] = {
            "name": title.strip()[:100],
            "applied_tags": tag_ids,
            "message": {"content": first_chunk},
        }
        post_resp = await client.post(f"/channels/{channel_id}/threads", json=payload)
        post_resp.raise_for_status()
        created_thread = post_resp.json() or {}
        created_thread_id = str(created_thread.get("id", ""))

        for extra_chunk in chunks[1:]:
            if not created_thread_id:
                break
            ext_resp = await client.post(
                f"/channels/{created_thread_id}/messages",
                json={"content": extra_chunk},
            )
            ext_resp.raise_for_status()

        return {
            "thread_id": created_thread_id,
            "title": str(created_thread.get("name", title.strip()[:100])),
            "applied_tag_ids": tag_ids,
            "applied_tags": tag_labels,
        }

    async def discord_create_forum_thread(
        self,
        guild_id: str,
        channel_id: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        skip_if_exists: bool = True,
        approval_token: str = "",
    ) -> str:
        """Create a new Discord forum thread with resolved tag IDs and duplicate protection."""
        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            policy = self._check_access(resolved_guild_id, channel_id, require_write=True)
            if approval_token.strip():
                await self._require_approval("discord_create_forum_thread", approval_token)

            ch_resp = await client.get(f"/channels/{channel_id}")
            ch_resp.raise_for_status()
            forum_data = ch_resp.json() or {}
            available_tags = forum_data.get("available_tags") or []
            tag_map = _build_forum_tag_map(forum_data)

            if skip_if_exists:
                existing_threads = await self._collect_forum_threads(
                    client, policy.guild_id, channel_id
                )
                for th in existing_threads:
                    existing_name = str(th.get("name", ""))
                    if _titles_match_similar(title, existing_name):
                        result = {
                            "citation": f"[Discord: {policy.name or policy.guild_id} / #{forum_data.get('name', channel_id)}]",
                            "status": "skipped_existing",
                            "skipped_existing": True,
                            "guild_id": policy.guild_id,
                            "channel_id": channel_id,
                            "thread_id": str(th.get("id", "")),
                            "title": existing_name,
                            "tags": _format_thread_tags(th, tag_map),
                        }
                        return wrap_untrusted_data(
                            f"discord:{policy.name or policy.guild_id}",
                            json.dumps(result, ensure_ascii=False),
                        )

            created_info = await self._post_forum_thread(
                client,
                channel_id,
                title=title,
                content=content,
                tags=tags,
                available_tags=available_tags,
            )

        guild_label = policy.name or policy.guild_id
        result = {
            "citation": f"[Discord: {guild_label} / #{forum_data.get('name', channel_id)}]",
            "status": "created",
            "skipped_existing": False,
            "guild_id": policy.guild_id,
            "channel_id": channel_id,
            **created_info,
        }
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            json.dumps(result, ensure_ascii=False),
        )

    async def discord_bulk_create_forum_threads(
        self,
        guild_id: str,
        channel_id: str,
        threads: list[dict[str, Any]],
        skip_if_exists: bool = True,
        approval_token: str = "",
    ) -> str:
        """
        Batch-create multiple forum threads in a single tool call with upfront duplicate check.

        Fetches active + all archived threads once, skips duplicates when `skip_if_exists=True`,
        and paces consecutive creations by `bulk_create_interval_seconds` (default 0.6s).
        """
        if not threads:
            raise ValueError("`threads` must contain at least one thread definition")

        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            policy = self._check_access(resolved_guild_id, channel_id, require_write=True)
            if approval_token.strip():
                await self._require_approval("discord_bulk_create_forum_threads", approval_token)

            ch_resp = await client.get(f"/channels/{channel_id}")
            ch_resp.raise_for_status()
            forum_data = ch_resp.json() or {}
            available_tags = forum_data.get("available_tags") or []
            tag_map = _build_forum_tag_map(forum_data)
            forum_name = str(forum_data.get("name", channel_id))

            existing_threads: list[dict[str, Any]] = []
            if skip_if_exists:
                existing_threads = await self._collect_forum_threads(
                    client, policy.guild_id, channel_id
                )

            created_items: list[dict[str, Any]] = []
            skipped_items: list[dict[str, Any]] = []
            failed_items: list[dict[str, Any]] = []
            created_count_since_start = 0

            for idx, item in enumerate(threads):
                if not isinstance(item, dict):
                    failed_items.append({"index": idx, "error": "Invalid thread object"})
                    continue
                raw_title = str(item.get("title", "")).strip()
                raw_content = str(item.get("content", ""))
                raw_tags = item.get("tags")
                if not raw_title:
                    failed_items.append({"index": idx, "error": "Missing thread title"})
                    continue

                if skip_if_exists:
                    dup_match = next(
                        (
                            th
                            for th in existing_threads
                            if _titles_match_similar(raw_title, str(th.get("name", "")))
                        ),
                        None,
                    )
                    if dup_match is not None:
                        skipped_items.append(
                            {
                                "requested_title": raw_title,
                                "existing_title": str(dup_match.get("name", "")),
                                "thread_id": str(dup_match.get("id", "")),
                                "tags": _format_thread_tags(dup_match, tag_map),
                            }
                        )
                        continue

                if created_count_since_start > 0 and self._bulk_create_interval_seconds > 0:
                    await asyncio.sleep(self._bulk_create_interval_seconds)

                try:
                    tag_list = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else None
                    created_info = await self._post_forum_thread(
                        client,
                        channel_id,
                        title=raw_title,
                        content=raw_content,
                        tags=tag_list,
                        available_tags=available_tags,
                    )
                    created_count_since_start += 1
                    created_items.append(created_info)
                    existing_threads.append(
                        {
                            "id": created_info["thread_id"],
                            "name": created_info["title"],
                            "applied_tags": created_info["applied_tag_ids"],
                        }
                    )
                except Exception as exc:
                    failed_items.append(
                        {"title": raw_title, "error": mask_secrets(str(exc)) or "Create failed"}
                    )

        guild_label = policy.name or policy.guild_id
        summary = {
            "citation": f"[Discord: {guild_label} / #{forum_name}]",
            "status": "completed",
            "guild_id": policy.guild_id,
            "channel_id": channel_id,
            "created_count": len(created_items),
            "skipped_count": len(skipped_items),
            "failed_count": len(failed_items),
            "created": created_items,
            "skipped": skipped_items,
            "failed": failed_items,
        }
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            json.dumps(summary, ensure_ascii=False),
        )

    async def discord_update_thread(
        self,
        guild_id: str,
        thread_id: str,
        title: str | None = None,
        tags: list[str] | None = None,
        archived: bool | None = None,
        locked: bool | None = None,
        approval_token: str = "",
    ) -> str:
        """Update thread title, forum tags, archived status, or locked state."""
        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            th_resp = await client.get(f"/channels/{thread_id}")
            th_resp.raise_for_status()
            th_data = th_resp.json() or {}
            parent_id = str(th_data.get("parent_id", "") or "")

            policy = self._check_access(
                resolved_guild_id,
                thread_id,
                require_write=True,
                parent_channel_id=parent_id or None,
            )
            if approval_token.strip():
                await self._require_approval("discord_update_thread", approval_token)

            patch_payload: dict[str, Any] = {}
            resolved_labels: list[str] = []
            if title is not None and title.strip():
                patch_payload["name"] = title.strip()[:100]
            if archived is not None:
                patch_payload["archived"] = bool(archived)
            if locked is not None:
                patch_payload["locked"] = bool(locked)
            if tags is not None:
                available_tags: list[dict[str, Any]] = []
                if parent_id:
                    parent_resp = await client.get(f"/channels/{parent_id}")
                    if parent_resp.status_code == 200:
                        available_tags = (parent_resp.json() or {}).get("available_tags") or []
                tag_ids, resolved_labels = _resolve_forum_tag_ids(tags, available_tags)
                patch_payload["applied_tags"] = tag_ids

            if not patch_payload:
                raise ValueError("At least one thread update field (title, tags, archived, locked) is required")

            patch_resp = await client.patch(f"/channels/{thread_id}", json=patch_payload)
            patch_resp.raise_for_status()
            updated = patch_resp.json() or {}

        guild_label = policy.name or policy.guild_id
        result = {
            "citation": f"[Discord: {guild_label}]",
            "status": "updated",
            "guild_id": policy.guild_id,
            "thread_id": str(updated.get("id", thread_id)),
            "title": str(updated.get("name", title or th_data.get("name", ""))),
            "applied_tags": resolved_labels,
            "applied_tag_ids": updated.get("applied_tags", patch_payload.get("applied_tags", [])),
            "archived": updated.get("thread_metadata", {}).get("archived", archived),
            "locked": updated.get("thread_metadata", {}).get("locked", locked),
        }
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            json.dumps(result, ensure_ascii=False),
        )

    async def discord_edit_message(
        self,
        guild_id: str,
        channel_id: str,
        message_id: str,
        content: str,
        approval_token: str = "",
    ) -> str:
        """Edit an existing Discord message in a read_write channel or thread."""
        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            ch_resp = await client.get(f"/channels/{channel_id}")
            parent_id = str(ch_resp.json().get("parent_id", "")) if ch_resp.status_code == 200 else None
            policy = self._check_access(
                resolved_guild_id,
                channel_id,
                require_write=True,
                parent_channel_id=parent_id or None,
            )
            if approval_token.strip():
                await self._require_approval("discord_edit_message", approval_token)

            resp = await client.patch(
                f"/channels/{channel_id}/messages/{message_id}",
                json={"content": content[:2000]},
            )
            resp.raise_for_status()
            edited = resp.json() or {}

        guild_label = policy.name or policy.guild_id
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            f"[Discord: {guild_label} / #{channel_id}] メッセージ編集完了 (message_id={edited.get('id', message_id)})",
        )

    async def discord_delete_message(
        self,
        guild_id: str,
        channel_id: str,
        message_id: str,
        approval_token: str = "",
    ) -> str:
        """Delete a Discord message (destructive action; strictly requires valid approval token)."""
        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            ch_resp = await client.get(f"/channels/{channel_id}")
            parent_id = str(ch_resp.json().get("parent_id", "")) if ch_resp.status_code == 200 else None
            policy = self._check_access(
                resolved_guild_id,
                channel_id,
                require_write=True,
                parent_channel_id=parent_id or None,
            )
            await self._require_approval("discord_delete_message", approval_token)

            resp = await client.delete(f"/channels/{channel_id}/messages/{message_id}")
            resp.raise_for_status()

        guild_label = policy.name or policy.guild_id
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            f"[Discord: {guild_label} / #{channel_id}] メッセージ削除完了 (message_id={message_id})",
        )

    async def discord_add_reaction(
        self,
        guild_id: str,
        channel_id: str,
        message_id: str,
        emoji: str,
    ) -> str:
        """Add an emoji reaction to a Discord message."""
        clean_emoji = emoji.strip()
        if not clean_emoji:
            raise ValueError("Reaction emoji must not be empty")
        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            ch_resp = await client.get(f"/channels/{channel_id}")
            parent_id = str(ch_resp.json().get("parent_id", "")) if ch_resp.status_code == 200 else None
            policy = self._check_access(
                resolved_guild_id,
                channel_id,
                require_write=True,
                parent_channel_id=parent_id or None,
            )
            encoded_emoji = quote(clean_emoji, safe="")
            resp = await client.put(
                f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded_emoji}/@me"
            )
            resp.raise_for_status()

        guild_label = policy.name or policy.guild_id
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            f"[Discord: {guild_label} / #{channel_id}] リアクション追加完了 ({clean_emoji}, message_id={message_id})",
        )

    async def discord_pin_message(
        self,
        guild_id: str,
        channel_id: str,
        message_id: str,
        unpin: bool = False,
    ) -> str:
        """Pin or unpin a Discord message in a read_write channel."""
        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            ch_resp = await client.get(f"/channels/{channel_id}")
            parent_id = str(ch_resp.json().get("parent_id", "")) if ch_resp.status_code == 200 else None
            policy = self._check_access(
                resolved_guild_id,
                channel_id,
                require_write=True,
                parent_channel_id=parent_id or None,
            )
            url = f"/channels/{channel_id}/pins/{message_id}"
            resp = await (client.delete(url) if unpin else client.put(url))
            resp.raise_for_status()

        guild_label = policy.name or policy.guild_id
        action_text = "ピン留め解除完了" if unpin else "ピン留め完了"
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            f"[Discord: {guild_label} / #{channel_id}] {action_text} (message_id={message_id})",
        )

    async def discord_create_channel(
        self,
        guild_id: str,
        name: str,
        channel_type: str | int = "text",
        category_id: str = "",
        topic: str = "",
        approval_token: str = "",
    ) -> str:
        """Create a text, forum, announcement, voice, or category channel in a read_write server."""
        if isinstance(channel_type, int):
            type_int = channel_type
        else:
            raw_type = str(channel_type).strip().lower()
            if raw_type.isdigit():
                type_int = int(raw_type)
            else:
                type_int = _CHANNEL_TYPE_NAME_TO_INT.get(raw_type, 0)

        async with self._create_client() as client:
            resolved_guild_id = await self._resolve_guild_id(guild_id, client=client)
            policy = self._check_access(resolved_guild_id, require_write=True)
            if approval_token.strip():
                await self._require_approval("discord_create_channel", approval_token)

            payload: dict[str, Any] = {
                "name": name.strip()[:100],
                "type": type_int,
            }
            if category_id.strip():
                payload["parent_id"] = category_id.strip()
            if topic.strip():
                payload["topic"] = topic.strip()[:1024]

            resp = await client.post(
                f"/guilds/{policy.guild_id}/channels",
                json=payload,
            )
            resp.raise_for_status()
            created = resp.json() or {}

        guild_label = policy.name or policy.guild_id
        result = {
            "citation": f"[Discord: {guild_label}]",
            "status": "created",
            "guild_id": policy.guild_id,
            "channel_id": str(created.get("id", "")),
            "name": str(created.get("name", name)),
            "type": created.get("type", type_int),
        }
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            json.dumps(result, ensure_ascii=False),
        )

    async def discord_update_pinned_summary(
        self,
        guild_id: str,
        channel_id: str,
        message_id: str | None = None,
        new_content: str = "",
        approval_token: str = "",
    ) -> str:
        """Edit an existing pinned summary message or post and pin a new summary message."""
        resolved_guild_id = await self._resolve_guild_id(guild_id)
        policy = self._check_access(resolved_guild_id, channel_id, require_write=True)
        await self._require_approval("discord_update_pinned_summary", approval_token)

        async with self._create_client() as client:
            if message_id:
                resp = await client.patch(
                    f"/channels/{channel_id}/messages/{message_id}",
                    json={"content": new_content},
                )
                resp.raise_for_status()
                target_id = resp.json().get("id", message_id)
                action_desc = "ピン留めメッセージを更新しました"
            else:
                post_resp = await client.post(
                    f"/channels/{channel_id}/messages",
                    json={"content": new_content},
                )
                post_resp.raise_for_status()
                target_id = post_resp.json()["id"]
                pin_resp = await client.put(f"/channels/{channel_id}/pins/{target_id}")
                pin_resp.raise_for_status()
                action_desc = "新規投稿してピン留めしました"

        guild_label = policy.name or policy.guild_id
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            f"[Discord: {guild_label} / #{channel_id}] {action_desc} (message_id={target_id})",
        )

    def get_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="discord_list_channels",
                description="List permitted Discord servers and channels including numeric guild_id, channel_id, and channel type.",
                handler=self.discord_list_channels,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_read_history",
                description="Read recent messages from a Discord channel (accepts numeric guild_id or server name; auto-expands active/archived threads for forum channels).",
                handler=self.discord_read_history,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_search_messages",
                description="Search messages across permitted Discord channels and forum threads using Bot-supported REST endpoints (accepts numeric guild_ids or server names).",
                handler=self.discord_search_messages,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_post_message",
                description="Post a message to a read_write Discord channel or thread, auto-splitting >2000 chars (requires approval_token).",
                handler=self.discord_post_message,
                requires_approval=True,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_create_forum_thread",
                description="Create a new thread in a read_write Discord forum channel, resolving tag names to tag IDs and checking skip_if_exists for duplicates.",
                handler=self.discord_create_forum_thread,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_bulk_create_forum_threads",
                description="Batch-create multiple forum threads in a single call for a read_write Discord forum channel, skipping existing threads once upfront and pacing creations to avoid rate limits.",
                handler=self.discord_bulk_create_forum_threads,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_update_thread",
                description="Update an existing Discord thread's title, forum tags (by tag name), archived, or locked state in a read_write server.",
                handler=self.discord_update_thread,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_edit_message",
                description="Edit an existing message in a read_write Discord channel or thread.",
                handler=self.discord_edit_message,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_delete_message",
                description="Delete a message from a read_write Discord channel or thread (strictly requires approval_token).",
                handler=self.discord_delete_message,
                requires_approval=True,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_add_reaction",
                description="Add an emoji reaction to a message in a read_write Discord server.",
                handler=self.discord_add_reaction,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_pin_message",
                description="Pin or unpin a message in a read_write Discord channel.",
                handler=self.discord_pin_message,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_create_channel",
                description="Create a new text, forum, announcement, voice, or category channel in a read_write Discord server.",
                handler=self.discord_create_channel,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_update_pinned_summary",
                description="Edit a pinned summary message or post and pin a new one (requires approval_token).",
                handler=self.discord_update_pinned_summary,
                requires_approval=True,
                source_type="discord",
                already_wrapped=True,
            ),
        ]
