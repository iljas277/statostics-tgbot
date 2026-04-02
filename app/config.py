from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(slots=True)
class Settings:
    bot_token: str
    channel_id: int
    linked_chat_id: int | None
    admin_ids: set[int]
    db_path: Path
    log_level: str
    tz: str
    contacts_posts_limit: int
    contacts_commenters_limit: int
    contacts_refresh_hour: int
    contacts_refresh_minute: int


def _load_dotenv(dotenv_path: Path) -> None:
    """Tiny .env parser to avoid extra dependency for a small project."""
    if not dotenv_path.exists():
        return

    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def _parse_admin_ids(raw_value: str) -> set[int]:
    admin_ids: set[int] = set()
    for part in raw_value.split(","):
        part = part.strip()
        if not part:
            continue
        admin_ids.add(int(part))
    return admin_ids


def load_settings() -> Settings:
    _load_dotenv(Path(".env"))

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    if not bot_token:
        raise ValueError("BOT_TOKEN is required")

    channel_id_raw = os.getenv("CHANNEL_ID", "").strip()
    if not channel_id_raw:
        raise ValueError("CHANNEL_ID is required")

    admin_ids_raw = os.getenv("ADMIN_IDS", "").strip()
    if not admin_ids_raw:
        raise ValueError("ADMIN_IDS is required")

    linked_chat_id_raw = os.getenv("LINKED_CHAT_ID", "").strip()

    db_path = Path(os.getenv("DB_PATH", "data/bot.sqlite3"))

    contacts_posts_limit = max(1, int(os.getenv("CONTACTS_POSTS_LIMIT", "30")))
    contacts_commenters_limit = max(1, int(os.getenv("CONTACTS_COMMENTERS_LIMIT", "20")))
    contacts_refresh_hour = max(0, min(23, int(os.getenv("CONTACTS_REFRESH_HOUR", "9"))))
    contacts_refresh_minute = max(0, min(59, int(os.getenv("CONTACTS_REFRESH_MINUTE", "0"))))

    return Settings(
        bot_token=bot_token,
        channel_id=int(channel_id_raw),
        linked_chat_id=int(linked_chat_id_raw) if linked_chat_id_raw else None,
        admin_ids=_parse_admin_ids(admin_ids_raw),
        db_path=db_path,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        tz=os.getenv("TZ", "UTC"),
        contacts_posts_limit=contacts_posts_limit,
        contacts_commenters_limit=contacts_commenters_limit,
        contacts_refresh_hour=contacts_refresh_hour,
        contacts_refresh_minute=contacts_refresh_minute,
    )
