from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from telethon import TelegramClient

from app.config import Settings


@dataclass(slots=True)
class PostMetric:
    message_id: int
    post_date: str | None
    views: int
    forwards: int
    reactions_total: int
    reactions_json: str


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
    emoji = getattr(reaction, "emoticon", None)
    if emoji:
        return str(emoji)
    document_id = getattr(reaction, "document_id", None)
    if document_id:
        return f"custom:{document_id}"
    return str(type(reaction).__name__)


class TelegramApiMetricsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.telegram_api_id and self.settings.telegram_api_hash)

    async def fetch_recent_post_metrics(self, limit: int) -> list[PostMetric]:
        if not self.enabled:
            return []

        session_path = Path(self.settings.telegram_api_session)
        session_path.parent.mkdir(parents=True, exist_ok=True)
        proxy = parse_telegram_proxy(self.settings.telegram_proxy_url)

        client = TelegramClient(
            str(session_path),
            self.settings.telegram_api_id,
            self.settings.telegram_api_hash,
            proxy=proxy,
        )

        metrics: list[PostMetric] = []
        async with client:
            entity = await client.get_input_entity(self.settings.channel_id)
            async for msg in client.iter_messages(entity, limit=max(1, int(limit))):
                if not getattr(msg, "id", None):
                    continue
                if getattr(msg, "post", None) is False:
                    continue

                reactions_total = 0
                reactions_map: dict[str, int] = {}
                reactions = getattr(msg, "reactions", None)
                for item in getattr(reactions, "results", []) or []:
                    key = _reaction_to_key(getattr(item, "reaction", None))
                    count = int(getattr(item, "count", 0) or 0)
                    reactions_map[key] = reactions_map.get(key, 0) + count
                    reactions_total += count

                metrics.append(
                    PostMetric(
                        message_id=int(msg.id),
                        post_date=msg.date.isoformat() if getattr(msg, "date", None) else None,
                        views=int(getattr(msg, "views", 0) or 0),
                        forwards=int(getattr(msg, "forwards", 0) or 0),
                        reactions_total=reactions_total,
                        reactions_json=json.dumps(reactions_map, ensure_ascii=False, sort_keys=True),
                    )
                )
        return metrics
