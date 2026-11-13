"""Proactive briefing & event-driven alert engine backed by APScheduler, Redis, Discord & Web Push."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import yaml
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from pydantic import BaseModel, Field
from pywebpush import WebPushException, webpush
from redis.asyncio import Redis
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent.memory import MemoryService
from app.core.logging import get_logger
from app.db.models import ProactiveNotification, PushSubscription

logger = get_logger(__name__)

SENT_KEY_PREFIX = "gemuse:proactive:sent:"
DEFAULT_IDEMPOTENCY_TTL_SECONDS = 48 * 3600  # 48 hours


class ProactiveCandidate(BaseModel):
    """Normalized candidate notification ready for idempotency check and dispatch."""

    rule_id: str
    event_key: str
    priority: str = "normal"
    title: str
    body: str
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def event_hash(self) -> str:
        raw = f"{self.rule_id}:{self.event_key}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ProactiveDispatchResult(BaseModel):
    """Outcome for a single proactive notification dispatch attempt."""

    rule_id: str
    event_hash: str
    dispatched: bool
    skipped_duplicate: bool = False
    channels_sent: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


CalendarFetcher = Callable[[datetime, datetime], Awaitable[list[dict[str, Any]]]]
MailFetcher = Callable[[list[str]], Awaitable[list[dict[str, Any]]]]


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        normalized = value.strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                parsed = datetime.strptime(normalized[:10], "%Y-%m-%d")
                return parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
    return None


class ProactiveEngine:
    """Runs scheduled morning briefings and periodic calendar/mail proactive alerts."""

    def __init__(
        self,
        *,
        rules_path: str | Path = "/app/config/proactive_rules.yaml",
        timezone_name: str = "Asia/Tokyo",
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        redis: Redis | None = None,
        discord_webhook_url: str = "",
        vapid_public_key: str = "",
        vapid_private_key: str = "",
        vapid_claims_email: str = "mailto:admin@example.com",
        memory_service: MemoryService | None = None,
        calendar_fetcher: CalendarFetcher | None = None,
        mail_fetcher: MailFetcher | None = None,
        http_client: httpx.AsyncClient | None = None,
        idempotency_ttl_seconds: int = DEFAULT_IDEMPOTENCY_TTL_SECONDS,
    ) -> None:
        self.rules_path = Path(rules_path)
        self.timezone_name = timezone_name
        self.session_factory = session_factory
        self.redis = redis
        self.discord_webhook_url = discord_webhook_url.strip()
        self.vapid_public_key = vapid_public_key.strip()
        self.vapid_private_key = vapid_private_key.strip()
        self.vapid_claims_email = vapid_claims_email.strip() or "mailto:admin@example.com"
        self.memory_service = memory_service
        self.calendar_fetcher = calendar_fetcher
        self.mail_fetcher = mail_fetcher
        self._http_client = http_client
        self.idempotency_ttl_seconds = idempotency_ttl_seconds

        self._scheduler: AsyncIOScheduler | None = None
        self._local_sent_hashes: set[str] = set()

    def load_rules(self) -> dict[str, Any]:
        """Load proactive rules from YAML cleanly, returning defaults if absent."""
        if not self.rules_path.exists():
            return {"timezone": self.timezone_name, "schedules": [], "alert_rules": []}
        raw = yaml.safe_load(self.rules_path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            return {"timezone": self.timezone_name, "schedules": [], "alert_rules": []}
        return raw

    def start_scheduler(self) -> AsyncIOScheduler:
        """Register APScheduler cron and interval jobs from proactive_rules.yaml."""
        if self._scheduler is not None and self._scheduler.running:
            return self._scheduler

        rules = self.load_rules()
        tz_name = str(rules.get("timezone") or self.timezone_name)
        tz = ZoneInfo(tz_name)
        scheduler = AsyncIOScheduler(timezone=tz)

        for schedule in rules.get("schedules") or []:
            if not schedule.get("enabled", True):
                continue
            sched_id = str(schedule.get("id") or "")
            if sched_id == "morning_briefing":
                cron_expr = str(schedule.get("cron") or "30 7 * * *")
                scheduler.add_job(
                    self.run_morning_briefing,
                    trigger=CronTrigger.from_crontab(cron_expr, timezone=tz),
                    id="gemuse_morning_briefing",
                    replace_existing=True,
                )
            elif sched_id == "event_watcher":
                interval_minutes = int(schedule.get("interval_minutes") or 15)
                scheduler.add_job(
                    self.run_event_alerts,
                    trigger=IntervalTrigger(minutes=max(1, interval_minutes)),
                    id="gemuse_event_watcher",
                    replace_existing=True,
                )

        scheduler.start()
        self._scheduler = scheduler
        logger.info("APScheduler started for ProactiveEngine timezone=%s", tz_name)
        return scheduler

    def stop_scheduler(self) -> None:
        if self._scheduler is not None:
            if self._scheduler.running:
                self._scheduler.shutdown(wait=False)
            self._scheduler = None

    async def _acquire_idempotency_lock(self, event_hash: str) -> bool:
        """Return True if this event_hash has NOT been sent within TTL, marking it atomically."""
        if self.redis is not None:
            key = f"{SENT_KEY_PREFIX}{event_hash}"
            created = await self.redis.set(
                key, "1", ex=self.idempotency_ttl_seconds, nx=True
            )
            return bool(created)
        if event_hash in self._local_sent_hashes:
            return False
        self._local_sent_hashes.add(event_hash)
        return True

    async def _send_discord(self, candidate: ProactiveCandidate) -> bool:
        if not self.discord_webhook_url:
            return False
        payload = {
            "content": f"**[{candidate.priority.upper()}] {candidate.title}**\n{candidate.body}",
        }
        if self._http_client is not None:
            resp = await self._http_client.post(self.discord_webhook_url, json=payload)
            resp.raise_for_status()
            return True
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(self.discord_webhook_url, json=payload)
            resp.raise_for_status()
            return True

    async def _send_web_push(
        self, db: AsyncSession, candidate: ProactiveCandidate
    ) -> int:
        if not self.vapid_private_key or not self.vapid_public_key:
            return 0
        res = await db.execute(select(PushSubscription).where(PushSubscription.is_active.is_(True)))
        subs = res.scalars().all()
        if not subs:
            return 0

        data = json.dumps(
            {
                "title": candidate.title,
                "body": candidate.body,
                "priority": candidate.priority,
                "rule_id": candidate.rule_id,
                "payload": candidate.payload,
            },
            ensure_ascii=False,
        )
        sent_count = 0
        for sub in subs:
            sub_info = {
                "endpoint": sub.endpoint,
                "keys": {"p256dh": sub.p256dh, "auth": sub.auth},
            }
            try:
                await asyncio.to_thread(
                    webpush,
                    subscription_info=sub_info,
                    data=data,
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims={"sub": self.vapid_claims_email},
                )
                sent_count += 1
            except WebPushException as exc:
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                if status_code in (404, 410):
                    sub.is_active = False
                logger.warning("Web Push delivery error endpoint=%s: %s", sub.endpoint, exc)
            except Exception as exc:
                logger.warning("Unexpected Web Push error: %s", exc)
        return sent_count

    async def dispatch_notification(
        self,
        candidate: ProactiveCandidate,
        *,
        db: AsyncSession | None = None,
    ) -> ProactiveDispatchResult:
        """Deduplicate via Redis (48h TTL), dispatch to Discord/Web Push, and persist history."""
        event_hash = candidate.event_hash
        acquired = await self._acquire_idempotency_lock(event_hash)
        if not acquired:
            return ProactiveDispatchResult(
                rule_id=candidate.rule_id,
                event_hash=event_hash,
                dispatched=False,
                skipped_duplicate=True,
            )

        channels_sent: list[str] = []
        errors: list[str] = []

        if self.discord_webhook_url:
            try:
                if await self._send_discord(candidate):
                    channels_sent.append("discord_webhook")
            except Exception as exc:
                errors.append(f"discord_webhook: {exc}")
                logger.warning("Discord webhook dispatch failed: %s", exc)

        async def _persist_and_push(session: AsyncSession) -> None:
            if self.vapid_private_key and self.vapid_public_key:
                try:
                    push_count = await self._send_web_push(session, candidate)
                    if push_count > 0:
                        channels_sent.append(f"web_push:{push_count}")
                except Exception as exc:
                    errors.append(f"web_push: {exc}")

            record = ProactiveNotification(
                rule_id=candidate.rule_id,
                event_hash=event_hash,
                priority=candidate.priority,
                title=candidate.title,
                body=candidate.body,
                payload=candidate.payload,
                channels_sent=channels_sent,
                is_read=False,
            )
            session.add(record)
            await session.commit()

        if db is not None:
            await _persist_and_push(db)
        elif self.session_factory is not None:
            async with self.session_factory() as session:
                await _persist_and_push(session)

        return ProactiveDispatchResult(
            rule_id=candidate.rule_id,
            event_hash=event_hash,
            dispatched=True,
            skipped_duplicate=False,
            channels_sent=channels_sent,
            errors=errors,
        )

    async def _fetch_calendar_safe(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        if self.calendar_fetcher is None:
            return []
        try:
            return await self.calendar_fetcher(start, end)
        except Exception as exc:
            logger.warning("Calendar fetch failed during proactive evaluation: %s", exc)
            return []

    async def _fetch_mail_safe(self, keywords: list[str]) -> list[dict[str, Any]]:
        if self.mail_fetcher is None:
            return []
        try:
            return await self.mail_fetcher(keywords)
        except Exception as exc:
            logger.warning("Mail fetch failed during proactive evaluation: %s", exc)
            return []

    async def run_morning_briefing(
        self,
        *,
        now: datetime | None = None,
        db: AsyncSession | None = None,
    ) -> ProactiveDispatchResult:
        """Generate and send the daily morning briefing once per date."""
        tz = ZoneInfo(self.timezone_name)
        current = now.astimezone(tz) if now else datetime.now(tz)
        day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)

        events = await self._fetch_calendar_safe(day_start, day_end)
        mails = await self._fetch_mail_safe(["重要", "締切", "面接", "urgent"])

        lines = [f"日付: {current.strftime('%Y-%m-%d')}"]
        if events:
            lines.append("【本日の予定】")
            for ev in events[:8]:
                lines.append(f"- {ev.get('start', '')}: {ev.get('summary', '(無題)')}")
        else:
            lines.append("【本日の予定】登録された予定はありません。")

        if mails:
            lines.append("【注目メール】")
            for mail in mails[:5]:
                lines.append(
                    f"- {mail.get('subject', '(件名なし)')} (From: {mail.get('sender', 'unknown')})"
                )

        if self.memory_service is not None and (db is not None or self.session_factory is not None):
            try:
                if db is not None:
                    episodes = await self.memory_service.search_episodes(
                        db, query="重要 予定 進行中 タスク", top_k=3
                    )
                elif self.session_factory is not None:
                    async with self.session_factory() as session:
                        episodes = await self.memory_service.search_episodes(
                            session, query="重要 予定 進行中 タスク", top_k=3
                        )
                else:
                    episodes = []
                if episodes:
                    lines.append("【記憶からの補足】")
                    for ep in episodes:
                        lines.append(f"- {ep.content[:140]}")
            except Exception as exc:
                logger.warning("Memory enrichment skipped for briefing: %s", exc)

        candidate = ProactiveCandidate(
            rule_id="morning_briefing",
            event_key=current.strftime("%Y-%m-%d"),
            priority="normal",
            title=f"Gemuse 朝のブリーフィング ({current.strftime('%m/%d')})",
            body="\n".join(lines),
            payload={"event_count": len(events), "mail_count": len(mails)},
        )
        return await self.dispatch_notification(candidate, db=db)

    async def run_event_alerts(
        self,
        *,
        now: datetime | None = None,
        db: AsyncSession | None = None,
    ) -> list[ProactiveDispatchResult]:
        """Evaluate calendar deadlines (24h) and urgent mail rules with calendar cross-checking."""
        rules = self.load_rules()
        tz = ZoneInfo(str(rules.get("timezone") or self.timezone_name))
        current = now.astimezone(tz) if now else datetime.now(tz)
        alert_rules = rules.get("alert_rules") or []

        results: list[ProactiveDispatchResult] = []
        upcoming_24h = await self._fetch_calendar_safe(current, current + timedelta(hours=24))
        upcoming_7d = await self._fetch_calendar_safe(current, current + timedelta(days=7))

        for rule in alert_rules:
            if not rule.get("enabled", True):
                continue
            rule_id = str(rule.get("id") or "alert")
            rule_type = str(rule.get("type") or "")

            if rule_type == "calendar_reminder":
                within_hours = int(rule.get("within_hours") or 24)
                keywords = [str(k).lower() for k in (rule.get("title_keywords") or [])]
                window_end = current + timedelta(hours=within_hours)
                for ev in upcoming_24h:
                    summary = str(ev.get("summary") or "")
                    start_dt = _parse_dt(ev.get("start"))
                    if start_dt is not None and not (current <= start_dt <= window_end):
                        continue
                    if keywords and not any(k in summary.lower() for k in keywords):
                        continue
                    ev_id = str(ev.get("id") or f"{summary}:{ev.get('start', '')}")
                    template = str(
                        rule.get("message_template")
                        or "24時間以内の重要予定があります: {summary} ({start})"
                    )
                    candidate = ProactiveCandidate(
                        rule_id=rule_id,
                        event_key=ev_id,
                        priority=str(rule.get("priority") or "high"),
                        title=f"予定リマインダー: {summary}",
                        body=template.format(
                            summary=summary,
                            start=ev.get("start", ""),
                        ),
                        payload={"calendar_event": ev},
                    )
                    results.append(await self.dispatch_notification(candidate, db=db))

            elif rule_type == "mail_keyword":
                keywords = [str(k) for k in (rule.get("keywords") or [])]
                mails = await self._fetch_mail_safe(keywords)
                for mail in mails:
                    subject = str(mail.get("subject") or "(No Subject)")
                    snippet = str(mail.get("snippet") or "")
                    haystack = f"{subject}\n{snippet}".lower()
                    if keywords and not any(k.lower() in haystack for k in keywords):
                        continue
                    msg_id = str(mail.get("message_id") or f"{mail.get('sender', '')}:{subject}")
                    cross_check = ""
                    if rule.get("cross_check_calendar", False):
                        cross_check = (
                            f"\n参考（直近7日間のカレンダー予定数: {len(upcoming_7d)}件）"
                        )
                    template = str(
                        rule.get("message_template")
                        or "重要メールを検知しました: {subject} (From: {sender})"
                    )
                    body = (
                        template.format(
                            subject=subject,
                            sender=mail.get("sender", "unknown"),
                        )
                        + cross_check
                    )
                    candidate = ProactiveCandidate(
                        rule_id=rule_id,
                        event_key=f"{mail.get('provider', 'mail')}:{msg_id}",
                        priority=str(rule.get("priority") or "urgent"),
                        title=f"緊急メール通知: {subject}",
                        body=body,
                        payload={
                            "mail": mail,
                            "upcoming_calendar_count": len(upcoming_7d),
                        },
                    )
                    results.append(await self.dispatch_notification(candidate, db=db))

        return results

    async def list_notifications(
        self, db: AsyncSession, *, limit: int = 50
    ) -> list[ProactiveNotification]:
        stmt = (
            select(ProactiveNotification)
            .order_by(desc(ProactiveNotification.created_at))
            .limit(max(1, min(limit, 200)))
        )
        res = await db.execute(stmt)
        return list(res.scalars().all())

