"""Multi-server Discord REST connector with strict YAML permission whitelisting."""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

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
    ) -> None:
        self._bot_token = bot_token.strip()
        self._permissions_path = Path(permissions_path)
        self._approval_service = approval_service
        self._session_factory = session_factory
        self._api_base_url = api_base_url.rstrip("/")
        self._http_client_factory = http_client_factory
        self._max_forum_output_chars = max(500, int(max_forum_output_chars))
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
        if channel_id and policy.allowed_channels and str(channel_id) not in policy.allowed_channels:
            raise PermissionError(
                f"Discord channel '{channel_id}' is not whitelisted for guild '{guild_id}'"
            )
        return policy

    def _create_client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(
            base_url=self._api_base_url,
            headers={
                "Authorization": f"Bot {self._bot_token}",
                "Content-Type": "application/json",
            },
            timeout=15.0,
        )

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
    ) -> str:
        """Post a message to a read_write Discord channel (requires valid approval token)."""
        resolved_guild_id = await self._resolve_guild_id(guild_id)
        policy = self._check_access(resolved_guild_id, channel_id, require_write=True)
        await self._require_approval("discord_post_message", approval_token)

        async with self._create_client() as client:
            resp = await client.post(
                f"/channels/{channel_id}/messages",
                json={"content": content},
            )
            resp.raise_for_status()
            created = resp.json()

        msg_id = created.get("id", "")
        guild_label = policy.name or policy.guild_id
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            f"[Discord: {guild_label} / #{channel_id}] 投稿完了 (message_id={msg_id})",
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
                description="Post a message to a read_write Discord channel (requires approval_token).",
                handler=self.discord_post_message,
                requires_approval=True,
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
