from __future__ import annotations

import fcntl
import logging
from pathlib import Path
from typing import TextIO
from datetime import time
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.error import Conflict
from telegram.error import NetworkError
from telegram.ext import Application, CommandHandler, MessageHandler, MessageReactionHandler, filters

from app.config import load_settings
from app.config import Settings
from app.db import Database
from app.handlers import (
    MENU_COMMANDS,
    cmd_contacts,
    cmd_exportcsv,
    cmd_help,
    cmd_postcommenters,
    cmd_poststats,
    cmd_reactions,
    cmd_reactiondebug,
    cmd_reactionstats,
    cmd_refreshcontacts,
    cmd_topcommenters,
    cmd_topposts,
    on_channel_post,
    on_message_reaction,
    on_message_reaction_count,
    cmd_start,
    cmd_stats,
    on_linked_chat_message,
)
from app.jobs import refresh_contacts_cache_job, snapshot_job
from app.logging_setup import configure_logging
from app.repositories import BotRepository

LOGGER = logging.getLogger(__name__)


def acquire_single_instance_lock(lock_path: Path) -> TextIO:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = lock_path.open("w", encoding="utf-8")
    try:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        lock_file.write(str(lock_path))
        lock_file.flush()
        return lock_file
    except BlockingIOError as exc:
        lock_file.close()
        raise RuntimeError(
            "Another bot instance is already running. Stop it before starting a new one."
        ) from exc


async def on_error(update: object, context) -> None:
    if isinstance(context.error, Conflict):
        LOGGER.error(
            "Telegram returned 409 Conflict: another getUpdates consumer is running with this token. "
            "Stopping this instance."
        )
        context.application.stop_running()
        return

    if isinstance(context.error, NetworkError):
        stats = context.application.bot_data.setdefault(
            "network_error_stats",
            {"count": 0, "last_error": None},
        )
        stats["count"] += 1
        stats["last_error"] = str(context.error)

        if stats["count"] == 1 or stats["count"] % 10 == 0:
            LOGGER.warning(
                "Transient Telegram network error #%s: %s. "
                "Usually a temporary internet/proxy issue; polling will continue.",
                stats["count"],
                context.error,
            )
        else:
            LOGGER.info("Transient Telegram network error #%s: %s", stats["count"], context.error)
        return

    LOGGER.exception("Unhandled bot error", exc_info=context.error)


async def on_post_init(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    linked_chat_ids: set[int] = app.bot_data.setdefault("linked_chat_ids", set())

    try:
        await app.bot.set_my_commands(MENU_COMMANDS)
    except Exception as exc:
        LOGGER.warning("Could not set bot command menu: %s", exc)

    try:
        channel_chat = await app.bot.get_chat(settings.channel_id)
    except Exception as exc:
        LOGGER.warning("Could not read channel metadata for linked chat detection: %s", exc)
        return

    app.bot_data["channel_username"] = getattr(channel_chat, "username", None)

    api_linked_chat_id = getattr(channel_chat, "linked_chat_id", None)
    if not api_linked_chat_id:
        LOGGER.warning(
            "Channel has no linked discussion chat according to Telegram API. "
            "Comments ingestion will stay disabled until discussion is configured."
        )
        return

    linked_chat_ids.add(api_linked_chat_id)
    if settings.linked_chat_id != api_linked_chat_id:
        LOGGER.warning(
            "LINKED_CHAT_ID mismatch: env=%s api=%s. Using API value for runtime ingestion.",
            settings.linked_chat_id,
            api_linked_chat_id,
        )
        settings.linked_chat_id = api_linked_chat_id


def build_app(settings: Settings) -> Application:

    db = Database(settings.db_path)
    db.init_schema()
    repo = BotRepository(db)

    app = Application.builder().token(settings.bot_token).post_init(on_post_init).build()
    app.bot_data["settings"] = settings
    app.bot_data["db"] = db
    app.bot_data["repo"] = repo
    app.bot_data["linked_chat_ids"] = {settings.linked_chat_id} if settings.linked_chat_id else set()
    app.bot_data["ingestion_counters"] = {"group_messages_seen": 0, "auto_forwards_seen": 0}
    app.bot_data["reaction_counters"] = {
        "message_reaction_seen": 0,
        "message_reaction_count_seen": 0,
        "last_message_reaction_at": None,
        "last_message_reaction_count_at": None,
    }
    app.bot_data["network_error_stats"] = {"count": 0, "last_error": None}

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("poststats", cmd_poststats))
    app.add_handler(CommandHandler("reactions", cmd_reactions))
    app.add_handler(CommandHandler("reactionstats", cmd_reactionstats))
    app.add_handler(CommandHandler("topposts", cmd_topposts))
    app.add_handler(CommandHandler("topcommenters", cmd_topcommenters))
    app.add_handler(CommandHandler("postcommenters", cmd_postcommenters))
    app.add_handler(CommandHandler("exportcsv", cmd_exportcsv))
    app.add_handler(CommandHandler("reactiondebug", cmd_reactiondebug))
    app.add_handler(CommandHandler("contacts", cmd_contacts))
    app.add_handler(CommandHandler("refreshcontacts", cmd_refreshcontacts))
    app.add_error_handler(on_error)

    app.add_handler(
        MessageHandler(
            filters.Chat(chat_id=[settings.channel_id]) & filters.UpdateType.CHANNEL_POSTS,
            on_channel_post,
        )
    )
    app.add_handler(
        MessageReactionHandler(
            on_message_reaction,
            message_reaction_types=MessageReactionHandler.MESSAGE_REACTION_UPDATED,
        )
    )
    app.add_handler(
        MessageReactionHandler(
            on_message_reaction_count,
            message_reaction_types=MessageReactionHandler.MESSAGE_REACTION_COUNT_UPDATED,
        )
    )
    app.add_handler(MessageHandler(filters.ALL, on_linked_chat_message))

    if app.job_queue:
        app.job_queue.run_repeating(snapshot_job, interval=6 * 60 * 60, first=45)
        app.job_queue.run_daily(
            refresh_contacts_cache_job,
            time=time(
                hour=settings.contacts_refresh_hour,
                minute=settings.contacts_refresh_minute,
                tzinfo=ZoneInfo(settings.tz),
            ),
        )
        app.job_queue.run_once(refresh_contacts_cache_job, when=20)

    return app


def main() -> None:
    settings = load_settings()
    configure_logging(settings.log_level)

    lock_path = settings.db_path.parent / "bot.lock"
    lock_file = acquire_single_instance_lock(lock_path)

    app = build_app(settings)
    LOGGER.info("Bot started")
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    main()
