"""X (Twitter API v2) & YouTube Data/Transcript MCP connector."""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx

from app.core.logging import get_logger
from app.core.security import wrap_untrusted_data
from app.mcp_hub.base import BaseMCPConnector, MCPTool

logger = get_logger(__name__)


class SocialMediaMCPConnector(BaseMCPConnector):
    """Connector for X search/bookmarks/lists and YouTube videos/transcripts."""

    connector_name = "social_media"

    def __init__(
        self,
        *,
        x_bearer_token: str = "",
        x_user_id: str = "",
        youtube_api_key: str = "",
        youtube_oauth_refresh_token: str = "",
        google_oauth_client_id: str = "",
        google_oauth_client_secret: str = "",
        google_oauth_refresh_token: str = "",
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        transcript_fetcher: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._x_bearer_token = x_bearer_token.strip()
        self._x_user_id = x_user_id.strip()
        self._youtube_api_key = youtube_api_key.strip()
        self._google_oauth_client_id = google_oauth_client_id.strip()
        self._google_oauth_client_secret = google_oauth_client_secret.strip()
        self._youtube_oauth_refresh_token = (
            youtube_oauth_refresh_token.strip() or google_oauth_refresh_token.strip()
        )
        self._http_client_factory = http_client_factory
        self._transcript_fetcher = transcript_fetcher

    @property
    def effective_youtube_refresh_token(self) -> str:
        """Return configured YouTube refresh token with GOOGLE_OAUTH_REFRESH_TOKEN fallback."""
        return self._youtube_oauth_refresh_token

    def _has_youtube_oauth(self) -> bool:
        return bool(
            self._google_oauth_client_id
            and self._google_oauth_client_secret
            and self._youtube_oauth_refresh_token
        )

    def is_configured(self) -> bool:
        return bool(self._x_bearer_token or self._youtube_api_key or self._has_youtube_oauth())

    def _create_client(self) -> httpx.AsyncClient:
        if self._http_client_factory is not None:
            return self._http_client_factory()
        return httpx.AsyncClient(timeout=15.0)

    async def x_search_or_bookmarks(
        self,
        mode: str = "search",
        query: str | None = None,
        limit: int = 15,
    ) -> str:
        """
        Query X API v2 (`mode`: 'search' | 'bookmarks' | 'list') with 429 rate-limit handling.
        """
        if not self._x_bearer_token:
            raise RuntimeError("X_BEARER_TOKEN is not configured")

        clamped = max(1, min(100, int(limit)))
        headers = {"Authorization": f"Bearer {self._x_bearer_token}"}
        params: dict[str, Any] = {
            "max_results": max(10, clamped) if mode == "search" else clamped,
            "tweet.fields": "created_at,author_id,public_metrics",
        }

        if mode == "bookmarks":
            if not self._x_user_id:
                raise ValueError("X_USER_ID is required for bookmarks mode")
            url = f"https://api.x.com/2/users/{self._x_user_id}/bookmarks"
        elif mode == "list":
            if not query:
                raise ValueError("List ID must be provided in `query` for list mode")
            url = f"https://api.x.com/2/lists/{query}/tweets"
        else:
            if not query or not query.strip():
                raise ValueError("`query` is required for search mode")
            url = "https://api.x.com/2/tweets/search/recent"
            params["query"] = query.strip()

        async with self._create_client() as client:
            resp = await client.get(url, headers=headers, params=params)

        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After") or resp.headers.get(
                "x-rate-limit-reset", "60"
            )
            logger.warning("X API rate limit hit", extra={"retry_after": retry_after})
            return wrap_untrusted_data(
                "x:rate_limit",
                json.dumps(
                    {
                        "rate_limited": True,
                        "status_code": 429,
                        "retry_after": retry_after,
                        "message": "X API のレート制限 (429) に達しました。しばらく待ってから再試行してください。",
                    },
                    ensure_ascii=False,
                ),
            )

        resp.raise_for_status()
        tweets = (resp.json().get("data") or [])[:clamped]
        if not tweets:
            return wrap_untrusted_data("x:tweets", "該当するポストは見つかりませんでした。")

        lines = [
            f"- [MCP: x] ({t.get('created_at', '')}) id={t.get('id')} author={t.get('author_id', '')}: {t.get('text', '')}"
            for t in tweets
        ]
        return wrap_untrusted_data(f"x:{mode}", "\n".join(lines))

    async def _get_youtube_access_token(self, client: httpx.AsyncClient) -> str:
        resp = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self._google_oauth_client_id,
                "client_secret": self._google_oauth_client_secret,
                "refresh_token": self._youtube_oauth_refresh_token,
                "grant_type": "refresh_token",
            },
        )
        resp.raise_for_status()
        return resp.json()["access_token"]

    async def youtube_get_subscriptions_and_videos(
        self,
        query: str | None = None,
        limit: int = 10,
    ) -> str:
        """Search YouTube videos or fetch user subscriptions via API key or OAuth2 fallback."""
        has_oauth = self._has_youtube_oauth()
        if not self._youtube_api_key and not has_oauth:
            raise RuntimeError(
                "YOUTUBE_API_KEY or YouTube/Google OAuth credentials are not configured"
            )

        clamped = max(1, min(50, int(limit)))
        clean_query = query.strip() if query and query.strip() else ""

        async with self._create_client() as client:
            if has_oauth and (not clean_query or not self._youtube_api_key):
                token = await self._get_youtube_access_token(client)
                headers = {"Authorization": f"Bearer {token}"}
                if not clean_query:
                    resp = await client.get(
                        "https://www.googleapis.com/youtube/v3/subscriptions",
                        headers=headers,
                        params={"part": "snippet", "mine": "true", "maxResults": clamped},
                    )
                    resp.raise_for_status()
                    items = resp.json().get("items") or []
                    if not items:
                        return wrap_untrusted_data(
                            "youtube:subscriptions", "登録チャンネルが見つかりませんでした。"
                        )
                    lines = []
                    for item in items:
                        snippet = item.get("snippet") or {}
                        ch_id = (snippet.get("resourceId") or {}).get("channelId", "")
                        lines.append(
                            f"- [MCP: youtube] {snippet.get('title', '')} (channel_id={ch_id}, published={snippet.get('publishedAt', '')})"
                        )
                    return wrap_untrusted_data("youtube:subscriptions", "\n".join(lines))

                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    headers=headers,
                    params={
                        "part": "snippet",
                        "type": "video",
                        "maxResults": clamped,
                        "order": "date",
                        "q": clean_query,
                    },
                )
            else:
                params: dict[str, Any] = {
                    "part": "snippet",
                    "type": "video",
                    "maxResults": clamped,
                    "order": "date",
                    "key": self._youtube_api_key,
                }
                if clean_query:
                    params["q"] = clean_query
                resp = await client.get(
                    "https://www.googleapis.com/youtube/v3/search",
                    params=params,
                )

            resp.raise_for_status()
            items = resp.json().get("items") or []

        if not items:
            return wrap_untrusted_data("youtube:videos", "動画が見つかりませんでした。")

        lines = []
        for item in items:
            vid = (item.get("id") or {}).get("videoId", "")
            snippet = item.get("snippet") or {}
            lines.append(
                f"- [MCP: youtube] {snippet.get('title', '')} (video_id={vid}, channel={snippet.get('channelTitle', '')}, published={snippet.get('publishedAt', '')})"
            )
        return wrap_untrusted_data("youtube:videos", "\n".join(lines))

    def _fetch_transcript_sync(self, video_id: str) -> list[dict[str, Any]]:
        if self._transcript_fetcher is not None:
            return self._transcript_fetcher(video_id)

        from youtube_transcript_api import YouTubeTranscriptApi

        # Support both youtube-transcript-api >= 1.0 instance API and legacy 0.6 classmethod API
        if hasattr(YouTubeTranscriptApi, "get_transcript"):
            return list(
                YouTubeTranscriptApi.get_transcript(video_id, languages=["ja", "en"])
            )
        api = YouTubeTranscriptApi()
        fetched = api.fetch(video_id, languages=["ja", "en"])
        return [
            {
                "text": getattr(snippet, "text", str(snippet)),
                "start": getattr(snippet, "start", 0.0),
                "duration": getattr(snippet, "duration", 0.0),
            }
            for snippet in fetched
        ]

    async def youtube_get_transcript(self, video_id: str) -> str:
        """Retrieve and structure Japanese/English transcript text for a YouTube video."""
        segments = await asyncio.to_thread(self._fetch_transcript_sync, video_id.strip())
        if not segments:
            return wrap_untrusted_data(f"youtube:{video_id}", "字幕データが見つかりませんでした。")

        lines = [
            f"[{seg.get('start', 0.0):.1f}s] {seg.get('text', '').strip()}"
            for seg in segments
            if seg.get("text")
        ]
        full_text = " ".join(
            seg.get("text", "").strip() for seg in segments if seg.get("text")
        )
        body = (
            f"[MCP: youtube_transcript] video_id={video_id}\n"
            f"Full Text: {full_text}\n\n"
            f"Timestamped:\n" + "\n".join(lines)
        )
        return wrap_untrusted_data(f"youtube:{video_id}", body)

    def get_tools(self) -> list[MCPTool]:
        tools: list[MCPTool] = []
        if self._x_bearer_token:
            tools.append(
                MCPTool(
                    name="x_search_or_bookmarks",
                    description="Search recent X posts or read bookmarks/lists (handles 429 rate limits).",
                    handler=self.x_search_or_bookmarks,
                    requires_approval=False,
                    source_type="x",
                    already_wrapped=True,
                )
            )
        if self._youtube_api_key or self._has_youtube_oauth():
            tools.append(
                MCPTool(
                    name="youtube_get_subscriptions_and_videos",
                    description="Search or list recent YouTube videos and subscriptions.",
                    handler=self.youtube_get_subscriptions_and_videos,
                    requires_approval=False,
                    source_type="youtube",
                    already_wrapped=True,
                )
            )
        # Transcript extraction works via video_id with YouTube credentials or standalone fetcher
        if (
            self._youtube_api_key
            or self._has_youtube_oauth()
            or self._transcript_fetcher is not None
        ):
            tools.append(
                MCPTool(
                    name="youtube_get_transcript",
                    description="Extract Japanese/English transcript text from a YouTube video ID.",
                    handler=self.youtube_get_transcript,
                    requires_approval=False,
                    source_type="youtube",
                    already_wrapped=True,
                )
            )
        return tools

