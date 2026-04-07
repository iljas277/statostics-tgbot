from __future__ import annotations

from datetime import datetime, timezone
import logging

from telegram.ext import ContextTypes

from app.repositories import BotRepository
from app.telegram_api import TelegramApiMetricsService

LOGGER = logging.getLogger(__name__)


async def snapshot_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    repo: BotRepository = context.application.bot_data["repo"]
    stats = repo.save_snapshot(period_hours=24)
    LOGGER.info(
        "Saved stats snapshot: posts=%s comments=%s unique=%s leads=%s",
        stats.posts_count,
        stats.comments_count,
        stats.unique_commenters,
        stats.leads_count,
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
    repo: BotRepository = context.application.bot_data["repo"]
    settings = context.application.bot_data["settings"]
    service = TelegramApiMetricsService(settings=settings)
    if not service.enabled:
        return
    metrics = await service.fetch_recent_post_metrics(limit=settings.mtproto_metrics_posts_limit)
    if not metrics:
        return
    snapshot_at = datetime.now(tz=timezone.utc).isoformat()
    count = repo.save_post_metrics_snapshots(
        rows=[
            {
                "message_id": m.message_id,
                "post_date": m.post_date,
                "views": m.views,
                "forwards": m.forwards,
                "reactions_total": m.reactions_total,
                "reactions_json": m.reactions_json,
            }
            for m in metrics
        ],
        snapshot_at=snapshot_at,
    )
    LOGGER.info(
        "MTProto metrics refreshed: rows=%s posts_limit=%s",
        count,
        settings.mtproto_metrics_posts_limit,
    )
