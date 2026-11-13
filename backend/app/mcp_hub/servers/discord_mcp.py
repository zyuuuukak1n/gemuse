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
    ) -> None:
        self._bot_token = bot_token.strip()
        self._permissions_path = Path(permissions_path)
        self._approval_service = approval_service
        self._session_factory = session_factory
        self._api_base_url = api_base_url.rstrip("/")
        self._http_client_factory = http_client_factory
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

    async def discord_list_channels(self, guild_id: str | None = None) -> str:
        """List accessible channels across permitted Discord guilds."""
        target_guilds = (
            [self._check_access(guild_id)]
            if guild_id
            else [s for s in self._permissions.servers if s.permission != "disabled"]
        )
        if not target_guilds:
            return wrap_untrusted_data("discord:channels", "アクセス許可されたDiscordサーバーはありません。")

        async with self._create_client() as client:
            async def _fetch_guild(policy: GuildPermission) -> str:
                resp = await client.get(f"/guilds/{policy.guild_id}/channels")
                resp.raise_for_status()
                channels = resp.json()
                lines: list[str] = []
                for ch in channels:
                    cid = str(ch.get("id", ""))
                    if policy.allowed_channels and cid not in policy.allowed_channels:
                        continue
                    cname = ch.get("name", "unknown")
                    ctype = ch.get("type", 0)
                    # Text/announcement/forum/thread channel types
                    if ctype in (0, 5, 11, 12, 15):
                        lines.append(
                            f"- [Discord: {policy.name or policy.guild_id} / #{cname}] (channel_id={cid}, perm={policy.permission})"
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
        """Read recent messages from an allowed Discord channel."""
        policy = self._check_access(guild_id, channel_id, require_write=False)
        clamped_limit = max(1, min(100, int(limit)))

        async with self._create_client() as client:
            ch_resp = await client.get(f"/channels/{channel_id}")
            channel_name = channel_id
            if ch_resp.status_code == 200:
                channel_name = ch_resp.json().get("name", channel_id)

            msg_resp = await client.get(
                f"/channels/{channel_id}/messages",
                params={"limit": clamped_limit},
            )
            msg_resp.raise_for_status()
            messages = msg_resp.json()

        guild_label = policy.name or guild_id
        citation = f"[Discord: {guild_label} / #{channel_name}]"
        formatted = []
        for m in messages:
            author = (m.get("author") or {}).get("username", "unknown")
            ts = m.get("timestamp", "")
            text = m.get("content", "")
            formatted.append(f"{citation} ({ts}) @{author} [msg_id={m.get('id')}]: {text}")

        body = "\n".join(formatted) if formatted else f"{citation} メッセージはありません。"
        return wrap_untrusted_data(f"discord:{guild_label}/#{channel_name}", body)

    async def discord_search_messages(
        self,
        query: str,
        guild_ids: list[str] | None = None,
    ) -> str:
        """Search messages in parallel across permitted Discord servers and channels."""
        cleaned_query = query.strip().lower()
        if not cleaned_query:
            return wrap_untrusted_data("discord:search", "検索クエリが空です。")

        if guild_ids:
            policies = [self._check_access(gid, require_write=False) for gid in guild_ids]
        else:
            policies = [s for s in self._permissions.servers if s.permission != "disabled"]

        if not policies:
            return wrap_untrusted_data("discord:search", "検索対象の許可済みサーバーがありません。")

        async with self._create_client() as client:
            async def _scan_guild(policy: GuildPermission) -> list[str]:
                ch_resp = await client.get(f"/guilds/{policy.guild_id}/channels")
                ch_resp.raise_for_status()
                channels = [
                    ch
                    for ch in ch_resp.json()
                    if ch.get("type", 0) in (0, 5, 11, 12)
                    and (
                        not policy.allowed_channels
                        or str(ch.get("id", "")) in policy.allowed_channels
                    )
                ]

                async def _scan_channel(ch: dict[str, Any]) -> list[str]:
                    cid = str(ch.get("id", ""))
                    cname = ch.get("name", cid)
                    resp = await client.get(
                        f"/channels/{cid}/messages",
                        params={"limit": 50},
                    )
                    resp.raise_for_status()
                    hits: list[str] = []
                    guild_label = policy.name or policy.guild_id
                    citation = f"[Discord: {guild_label} / #{cname}]"
                    for msg in resp.json():
                        content = msg.get("content", "")
                        if cleaned_query in content.lower():
                            author = (msg.get("author") or {}).get("username", "unknown")
                            hits.append(
                                f"{citation} ({msg.get('timestamp', '')}) @{author}: {content}"
                            )
                    return hits

                ch_results = await asyncio.gather(
                    *(_scan_channel(ch) for ch in channels[:15]),
                    return_exceptions=True,
                )
                collected: list[str] = []
                for cr in ch_results:
                    if isinstance(cr, list):
                        collected.extend(cr)
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
        policy = self._check_access(guild_id, channel_id, require_write=True)
        await self._require_approval("discord_post_message", approval_token)

        async with self._create_client() as client:
            resp = await client.post(
                f"/channels/{channel_id}/messages",
                json={"content": content},
            )
            resp.raise_for_status()
            created = resp.json()

        msg_id = created.get("id", "")
        guild_label = policy.name or guild_id
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
        policy = self._check_access(guild_id, channel_id, require_write=True)
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

        guild_label = policy.name or guild_id
        return wrap_untrusted_data(
            f"discord:{guild_label}",
            f"[Discord: {guild_label} / #{channel_id}] {action_desc} (message_id={target_id})",
        )

    def get_tools(self) -> list[MCPTool]:
        return [
            MCPTool(
                name="discord_list_channels",
                description="List permitted Discord servers and channels.",
                handler=self.discord_list_channels,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_read_history",
                description="Read recent message history from a permitted Discord channel.",
                handler=self.discord_read_history,
                requires_approval=False,
                source_type="discord",
                already_wrapped=True,
            ),
            MCPTool(
                name="discord_search_messages",
                description="Search messages across permitted Discord servers in parallel.",
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

