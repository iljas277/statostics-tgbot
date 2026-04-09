from __future__ import annotations

import asyncio
import fcntl
import logging
from pathlib import Path
from typing import TextIO
from datetime import time
from zoneinfo import ZoneInfo

from telegram.error import Conflict
from telegram.error import NetworkError
from telegram import BotCommand
from telegram.request import HTTPXRequest
from telegram.ext import Application, CommandHandler, MessageHandler, filters

from app.config import load_settings
from app.config import Settings
from app.db import Database
from app.handlers import (
    cmd_binddiscussion,
    cmd_chart,
    cmd_help,
    cmd_contacts,
    cmd_viewschart,
    cmd_export_commenters_csv,
    cmd_export_user_metrics_csv,
    cmd_export_post_commenters_csv,
    cmd_export_post_reactions_csv,
    cmd_poststats,
    cmd_refreshcontacts,
    on_channel_post,
    cmd_start,
    cmd_stats,
    cmd_refreshmetrics,
    on_linked_chat_message,
)
from app.jobs import refresh_contacts_cache_job, refresh_mtproto_metrics_job, snapshot_job
from app.logging_setup import configure_logging
from app.preflight import run_startup_tests
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
        LOGGER.warning("Transient Telegram network error: %s", context.error)
        return

    LOGGER.exception("Unhandled bot error", exc_info=context.error)


async def on_post_init(app: Application) -> None:
    settings: Settings = app.bot_data["settings"]
    linked_chat_ids: set[int] = app.bot_data.setdefault("linked_chat_ids", set())

    try:
        channel_chat = await app.bot.get_chat(settings.channel_id)
    except Exception as exc:
        LOGGER.warning("Could not read channel metadata for linked chat detection: %s", exc)
        return

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

    await app.bot.set_my_commands(
        [
            BotCommand("start", "Показать стартовую справку"),
            BotCommand("help", "Показать список команд"),
            BotCommand("stats", "Сводная статистика"),
            BotCommand("poststats", "Аналитика по post message_id"),
            BotCommand("chart", "График комментариев"),
            BotCommand("viewschart", "График просмотров"),
            BotCommand("refreshmetrics", "Справка по обновлению метрик Bot API"),
            BotCommand("contacts", "Топ комментаторов"),
            BotCommand("refreshcontacts", "Обновить кэш контактов"),
            BotCommand("export_user_metrics_csv", "CSV метрик пользователей"),
            BotCommand("export_commenters_csv", "CSV комментаторов канала"),
            BotCommand("export_post_commenters_csv", "CSV комментаторов поста"),
            BotCommand("export_post_reactions_csv", "CSV агрегированных реакций"),
            BotCommand("binddiscussion", "Привязать discussion-чат"),
        ]
    )


def build_app(settings: Settings) -> Application:

    db = Database(settings.db_path)
    db.init_schema()
    repo = BotRepository(db)

    request = HTTPXRequest(
        proxy=settings.telegram_proxy_url,
        httpx_kwargs={"trust_env": False},
    )
    get_updates_request = HTTPXRequest(
        proxy=settings.telegram_proxy_url,
        httpx_kwargs={"trust_env": False},
    )

    app_builder = (
        Application.builder()
        .token(settings.bot_token)
        .request(request)
        .get_updates_request(get_updates_request)
        .post_init(on_post_init)
    )
    app = app_builder.build()
    app.bot_data["settings"] = settings
    app.bot_data["db"] = db
    app.bot_data["repo"] = repo
    app.bot_data["linked_chat_ids"] = {settings.linked_chat_id} if settings.linked_chat_id else set()
    app.bot_data["ingestion_counters"] = {"group_messages_seen": 0, "auto_forwards_seen": 0}

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("chart", cmd_chart))
    app.add_handler(CommandHandler("viewschart", cmd_viewschart))
    app.add_handler(CommandHandler("refreshmetrics", cmd_refreshmetrics))
    app.add_handler(CommandHandler("poststats", cmd_poststats))
    app.add_handler(CommandHandler("contacts", cmd_contacts))
    app.add_handler(CommandHandler("refreshcontacts", cmd_refreshcontacts))
    app.add_handler(CommandHandler("export_user_metrics_csv", cmd_export_user_metrics_csv))
    app.add_handler(CommandHandler("export_commenters_csv", cmd_export_commenters_csv))
    app.add_handler(CommandHandler("export_post_commenters_csv", cmd_export_post_commenters_csv))
    app.add_handler(CommandHandler("export_post_reactions_csv", cmd_export_post_reactions_csv))
    app.add_handler(CommandHandler("binddiscussion", cmd_binddiscussion))
    app.add_error_handler(on_error)

    app.add_handler(
        MessageHandler(
            filters.Chat(chat_id=[settings.channel_id]) & filters.UpdateType.CHANNEL_POSTS,
            on_channel_post,
        )
    )
    app.add_handler(MessageHandler(filters.ALL, on_linked_chat_message))

    if app.job_queue:
        app.job_queue.run_repeating(snapshot_job, interval=6 * 60 * 60, first=45)
        app.job_queue.run_repeating(refresh_mtproto_metrics_job, interval=30 * 60, first=70)
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
    if settings.run_startup_tests:
        run_startup_tests(settings=settings)
        # run_startup_tests uses asyncio.run(), which closes the current loop.
        # PTB expects an available loop when starting polling.
        asyncio.set_event_loop(asyncio.new_event_loop())

    lock_path = settings.db_path.parent / "bot.lock"
    lock_file = acquire_single_instance_lock(lock_path)

    app = build_app(settings)
    LOGGER.info("Bot started")
    try:
        app.run_polling(allowed_updates=["message", "channel_post", "edited_channel_post"])
    finally:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    main()
