from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from telethon import TelegramClient
from telethon.errors.rpcerrorlist import BotMethodInvalidError, BroadcastForbiddenError
from telethon.tl.functions.messages import GetMessageReactionsListRequest
from telethon.tl.types import PeerChannel, PeerChat, PeerUser

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
    emoji = getattr(reaction, "emoticon", None)
    if emoji:
        return str(emoji)
    document_id = getattr(reaction, "document_id", None)
    if document_id:
        return f"custom:{document_id}"
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

    if len(key) <= 4:
        return f"Emoji ({key})"
    return f"Reaction type ({key})"


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

    async def fetch_post_reactors(self, message_id: int, limit: int = 2000) -> list[PostReactor]:
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

        aggregated: dict[int, dict[str, Any]] = {}
        remaining = max(1, min(10000, int(limit)))
        offset: str | None = None

        try:
            async with client:
                entity = await client.get_input_entity(self.settings.channel_id)

                while remaining > 0:
                    page_limit = min(100, remaining)
                    response = await client(
                        GetMessageReactionsListRequest(
                            peer=entity,
                            id=int(message_id),
                            limit=page_limit,
                            reaction=None,
                            offset=offset,
                        )
                    )

                    users_map = {int(user.id): user for user in getattr(response, "users", []) or []}
                    chats_map = {int(chat.id): chat for chat in getattr(response, "chats", []) or []}

                    reactions = getattr(response, "reactions", []) or []
                    if not reactions:
                        break

                    for row in reactions:
                        peer = getattr(row, "peer_id", None)
                        reaction_key = _reaction_to_key(getattr(row, "reaction", None))
                        sentiment = _reaction_sentiment(reaction_key)

                        user_id: int | None = None
                        username: str | None = None
                        nickname: str | None = None

                        if isinstance(peer, PeerUser):
                            user_id = int(peer.user_id)
                            user = users_map.get(user_id)
                            username = (getattr(user, "username", None) or "").strip() or None
                            if username:
                                nickname = f"@{username}"
                            else:
                                first_name = (getattr(user, "first_name", None) or "").strip()
                                last_name = (getattr(user, "last_name", None) or "").strip()
                                nickname = " ".join(part for part in [first_name, last_name] if part).strip() or str(user_id)
                        elif isinstance(peer, PeerChannel):
                            user_id = -int(peer.channel_id)
                            chat = chats_map.get(int(peer.channel_id))
                            username = (getattr(chat, "username", None) or "").strip() or None
                            title = (getattr(chat, "title", None) or "").strip()
                            nickname = f"@{username}" if username else (title or str(user_id))
                        elif isinstance(peer, PeerChat):
                            user_id = -int(peer.chat_id)
                            chat = chats_map.get(int(peer.chat_id))
                            username = (getattr(chat, "username", None) or "").strip() or None
                            title = (getattr(chat, "title", None) or "").strip()
                            nickname = f"@{username}" if username else (title or str(user_id))

                        if user_id is None:
                            continue
                        item = aggregated.setdefault(
                            user_id,
                            {
                                "user_id": user_id,
                                "nickname": nickname or str(user_id),
                                "username": username,
                                "reactions_count": 0,
                                "positive_count": 0,
                                "negative_count": 0,
                            },
                        )
                        item["reactions_count"] += 1
                        if sentiment == "positive":
                            item["positive_count"] += 1
                        elif sentiment == "negative":
                            item["negative_count"] += 1

                    remaining -= len(reactions)
                    offset = getattr(response, "next_offset", None)
                    if not offset:
                        break
        except BotMethodInvalidError as exc:
            raise RuntimeError(
                "MTProto-сессия авторизована как бот. Для экспорта реакторов нужна user-сессия Telethon."
            ) from exc
        except BroadcastForbiddenError as exc:
            raise RuntimeError(
                "Telegram API не позволяет выгружать список пользователей, оставивших реакции, для постов broadcast-канала."
            ) from exc

        sorted_rows = sorted(
            aggregated.values(),
            key=lambda item: (
                -int(item["reactions_count"]),
                -int(item["positive_count"]),
                int(item["negative_count"]),
            ),
        )
        return [
            PostReactor(
                user_id=int(item["user_id"]),
                nickname=str(item["nickname"]),
                username=item["username"],
                reactions_count=int(item["reactions_count"]),
                positive_count=int(item["positive_count"]),
                negative_count=int(item["negative_count"]),
            )
            for item in sorted_rows
        ]
