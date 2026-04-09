from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.parse import urlparse

from app.config import Settings


@dataclass(slots=True)
class PostMetric:
    message_id: int
    post_date: str | None
    views: int
    forwards: int
    reactions_total: int
    reactions_json: str


@dataclass(slots=True)
class PostReactor:
    user_id: int
    nickname: str
    username: str | None
    reactions_count: int
    positive_count: int
    negative_count: int


def parse_telegram_proxy(proxy_url: str | None) -> tuple[Any, str, int] | None:
    if not proxy_url:
        return None

    parsed = urlparse(proxy_url)
    if parsed.scheme.lower() != "socks5":
        raise ValueError("Only socks5 proxy scheme is supported for TELEGRAM_PROXY_URL")
    if not parsed.hostname or not parsed.port:
        raise ValueError("TELEGRAM_PROXY_URL must include host and port")

    try:
        import socks
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("PySocks is required for TELEGRAM_PROXY_URL support") from exc

    return (socks.SOCKS5, parsed.hostname, parsed.port)


def _reaction_to_key(reaction: Any) -> str:
    if reaction is None:
        return "unknown"

    emoji = getattr(reaction, "emoticon", None) or getattr(reaction, "emoji", None)
    if emoji:
        return str(emoji)

    custom_emoji_id = getattr(reaction, "custom_emoji_id", None) or getattr(reaction, "document_id", None)
    if custom_emoji_id:
        return f"custom:{custom_emoji_id}"

    value_type = getattr(reaction, "type", None)
    if value_type:
        return str(value_type)

    return str(type(reaction).__name__)


POSITIVE_REACTIONS = {"👍", "❤️", "🔥", "🥰", "👏", "😁", "🤩", "🎉", "🤝", "💯", "💪", "😄", "😍", "🙏", "👌", "✅"}
NEGATIVE_REACTIONS = {"👎", "💩", "🤬", "😢", "😡", "🤯", "🤮", "😈", "❌"}


def _reaction_sentiment(reaction_key: str) -> str:
    """Classify reaction key as positive/negative/neutral by predefined emoji sets."""
    if reaction_key in POSITIVE_REACTIONS:
        return "positive"
    if reaction_key in NEGATIVE_REACTIONS:
        return "negative"
    return "neutral"


def describe_reaction_key(reaction_key: str) -> str:
    """Return a human-readable reaction description for exports."""
    key = (reaction_key or "").strip()
    if not key:
        return "Unknown reaction"

    if key.startswith("custom:"):
        custom_id = key.split(":", 1)[1] or "unknown"
        return f"Custom emoji (id {custom_id})"

    sentiment = _reaction_sentiment(key)
    if sentiment == "positive":
        return f"Positive emoji ({key})"
    if sentiment == "negative":
        return f"Negative emoji ({key})"

    if len(key) <= 8:
        return f"Emoji ({key})"
    return f"Reaction type ({key})"


def _iter_reaction_items(raw_reactions: Any) -> list[Any]:
    if raw_reactions is None:
        return []
    if isinstance(raw_reactions, (list, tuple)):
        return list(raw_reactions)

    results = getattr(raw_reactions, "results", None)
    if isinstance(results, (list, tuple)):
        return list(results)

    reactions = getattr(raw_reactions, "reactions", None)
    if isinstance(reactions, (list, tuple)):
        return list(reactions)

    return []


def _reaction_count(item: Any) -> int:
    count = getattr(item, "total_count", None)
    if count is None:
        count = getattr(item, "count", None)
    try:
        return max(0, int(count or 0))
    except (TypeError, ValueError):
        return 0


def extract_post_metric_from_bot_message(message: Any) -> PostMetric | None:
    if not getattr(message, "message_id", None):
        return None

    message_id = int(message.message_id)
    post_date = message.date.isoformat() if getattr(message, "date", None) else None
    views = int(getattr(message, "views", 0) or 0)
    forwards = int(getattr(message, "forwards", 0) or 0)
    raw_reactions = getattr(message, "reactions", None)
    if raw_reactions is None:
        raw_reactions = getattr(message, "reaction_count", None)

    reactions_map: dict[str, int] = {}
    reactions_total = 0
    for item in _iter_reaction_items(raw_reactions):
        reaction = getattr(item, "reaction", None) or item
        key = _reaction_to_key(reaction)
        count = _reaction_count(item)
        if count <= 0:
            continue
        reactions_map[key] = reactions_map.get(key, 0) + count
        reactions_total += count

    return PostMetric(
        message_id=message_id,
        post_date=post_date,
        views=views,
        forwards=forwards,
        reactions_total=reactions_total,
        reactions_json=json.dumps(reactions_map, ensure_ascii=False, sort_keys=True),
    )


class TelegramApiMetricsService:
    """Compatibility shim for old MTProto integration; data now comes from Bot API updates."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return True

    async def fetch_recent_post_metrics(self, limit: int) -> list[PostMetric]:
        _ = limit
        return []

    async def fetch_post_reactors(self, message_id: int, limit: int = 2000) -> list[PostReactor]:
        _ = (message_id, limit)
        raise RuntimeError(
            "Telegram Bot API не предоставляет список пользователей, оставивших реакции для channel post."
        )
