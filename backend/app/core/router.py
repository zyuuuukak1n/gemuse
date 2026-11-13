"""Dynamic Gemini thinking-level router based on task complexity."""

from dataclasses import dataclass
from typing import Literal

ThinkingLevel = Literal["low", "medium", "high", "xhigh"]
RequestedThinkingLevel = Literal["auto", "low", "medium", "high", "xhigh"]

_XHIGH_KEYWORDS: tuple[str, ...] = (
    "アーキテクチャ",
    "設計",
    "トレードオフ",
    "根本原因",
    "徹底分析",
    "セキュリティ監査",
    "全体構成",
    "段階的",
    "architecture",
    "trade-off",
    "tradeoff",
    "root cause",
    "security audit",
    "comprehensive",
    "system design",
    "refactor",
)

_HIGH_KEYWORDS: tuple[str, ...] = (
    "実装",
    "調査",
    "比較",
    "分析",
    "原因",
    "デバッグ",
    "最適化",
    "計画",
    "手順",
    "implement",
    "analyze",
    "analyse",
    "compare",
    "debug",
    "optimize",
    "plan",
    "strategy",
    "why",
    "なぜ",
)

_MEDIUM_KEYWORDS: tuple[str, ...] = (
    "まとめ",
    "要約",
    "下書き",
    "翻訳",
    "説明",
    "確認",
    "教えて",
    "summarize",
    "draft",
    "translate",
    "explain",
    "check",
    "how to",
)


@dataclass(frozen=True)
class RoutingDecision:
    """Result of thinking-level routing with rationale for transparency."""

    level: ThinkingLevel
    reason: str
    score: int


def route_thinking_level(
    prompt: str,
    requested_level: RequestedThinkingLevel = "auto",
    *,
    has_tools: bool = False,
    history_length: int = 0,
) -> RoutingDecision:
    """
    Select Gemini 4 Hydrogen thinking level (`low` / `medium` / `high` / `xhigh`).

    Manual selection is always respected; when `auto`, complexity heuristics score
    prompt length, multi-step structure, domain keywords, tools, and conversation depth.
    """
    if requested_level != "auto":
        return RoutingDecision(
            level=requested_level,
            reason=f"Manual override requested: {requested_level}",
            score=-1,
        )

    cleaned = prompt.strip()
    lowered = cleaned.lower()
    score = 0

    # Length contribution
    char_len = len(cleaned)
    if char_len >= 600:
        score += 4
    elif char_len >= 220:
        score += 2
    elif char_len >= 65:
        score += 1

    # Multi-step / multi-question signals
    question_marks = cleaned.count("?") + cleaned.count("？")
    if question_marks >= 3:
        score += 2
    elif question_marks == 2:
        score += 1

    line_breaks = cleaned.count("\n")
    if line_breaks >= 4:
        score += 2
    elif line_breaks >= 2:
        score += 1

    # Keyword complexity matching
    xhigh_hits = sum(1 for kw in _XHIGH_KEYWORDS if kw in lowered)
    high_hits = sum(1 for kw in _HIGH_KEYWORDS if kw in lowered)
    medium_hits = sum(1 for kw in _MEDIUM_KEYWORDS if kw in lowered)

    if xhigh_hits >= 2:
        score += 6
    elif xhigh_hits == 1:
        score += 4

    if high_hits >= 2:
        score += 4
    elif high_hits == 1:
        score += 2

    if medium_hits >= 1:
        score += 2

    # Context and external tools
    if has_tools:
        score += 1
    if history_length >= 6:
        score += 1

    if score >= 7:
        return RoutingDecision(
            level="xhigh",
            reason="High-complexity architectural, analytical, or multi-step reasoning detected",
            score=score,
        )
    if score >= 4:
        return RoutingDecision(
            level="high",
            reason="Multi-step implementation, debugging, or analytical task detected",
            score=score,
        )
    if score >= 2:
        return RoutingDecision(
            level="medium",
            reason="Standard synthesis, explanation, or tool-assisted task detected",
            score=score,
        )
    return RoutingDecision(
        level="low",
        reason="Simple query or greeting detected",
        score=score,
    )

