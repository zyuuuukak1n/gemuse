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
        http_client_factory: Callable[[], httpx.AsyncClient] | None = None,
        transcript_fetcher: Callable[[str], list[dict[str, Any]]] | None = None,
    ) -> None:
        self._x_bearer_token = x_bearer_token.strip()
        self._x_user_id = x_user_id.strip()
        self._youtube_api_key = youtube_api_key.strip()
        self._http_client_factory = http_client_factory
        self._transcript_fetcher = transcript_fetcher

    def is_configured(self) -> bool:
        return bool(self._x_bearer_token or self._youtube_api_key)

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

    async def youtube_get_subscriptions_and_videos(
        self,
        query: str | None = None,
        limit: int = 10,
    ) -> str:
        """Search YouTube videos or fetch popular/recent videos via YouTube Data API v3."""
        if not self._youtube_api_key:
            raise RuntimeError("YOUTUBE_API_KEY is not configured")

        clamped = max(1, min(50, int(limit)))
        params: dict[str, Any] = {
            "part": "snippet",
            "type": "video",
            "maxResults": clamped,
            "order": "date",
            "key": self._youtube_api_key,
        }
        if query and query.strip():
            params["q"] = query.strip()

        async with self._create_client() as client:
            resp = await client.get(
                "https://www.googleapis.com/youtube/v3/search",
                params=params,
            )
            resp.raise_for_status()
            items = resp.json().get("items") or []

        if not items:
            return wrap_untrusted_data("youtube:videos", "動画が見つかりませんでした。")

        lines: list[str] = []
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
        if self._youtube_api_key:
            tools.append(
                MCPTool(
                    name="youtube_get_subscriptions_and_videos",
                    description="Search or list recent YouTube videos.",
                    handler=self.youtube_get_subscriptions_and_videos,
                    requires_approval=False,
                    source_type="youtube",
                    already_wrapped=True,
                )
            )
        # Transcript extraction works via video_id even with youtube_api_key alone (or standalone)
        if self._youtube_api_key or self._transcript_fetcher is not None:
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

