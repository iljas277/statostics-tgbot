from __future__ import annotations

import logging

from telegram.ext import ContextTypes

from app.repositories import BotRepository

LOGGER = logging.getLogger(__name__)


async def snapshot_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    repo: BotRepository = context.application.bot_data["repo"]
    stats = repo.save_snapshot(period_hours=24)
    LOGGER.info(
        "Saved stats snapshot: posts=%s comments=%s unique=%s",
        stats.posts_count,
        stats.comments_count,
        stats.unique_commenters,
    )


async def refresh_contacts_cache_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    repo: BotRepository = context.application.bot_data["repo"]
    settings = context.application.bot_data["settings"]
    count = repo.rebuild_contacts_cache(
        posts_limit=settings.contacts_posts_limit,
        commenters_limit=settings.contacts_commenters_limit,
    )
    LOGGER.info(
        "Contacts cache refreshed: rows=%s posts_limit=%s commenters_limit=%s",
        count,
        settings.contacts_posts_limit,
        settings.contacts_commenters_limit,
    )


async def refresh_mtproto_metrics_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    LOGGER.debug("Bot API metrics are captured on channel_post updates; scheduled refresh skipped.")
