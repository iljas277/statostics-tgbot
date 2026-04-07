from __future__ import annotations

from datetime import datetime, timezone
import logging
from io import BytesIO

from telegram import Message, Update
from telegram import InputFile
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from app.repositories import BotRepository
from app.services import analyze_comment
from app.charts import render_comments_trend_png
from app.telegram_api import TelegramApiMetricsService

LOGGER = logging.getLogger(__name__)


def _extract_forward_channel_info(message: Message) -> tuple[int | None, int | None]:
    """Return (channel_id, channel_message_id) for auto-forwarded channel posts.

    PTB versions expose this either via forward_from_* fields or via forward_origin.
    """
    fwd_chat = getattr(message, "forward_from_chat", None)
    fwd_msg_id = getattr(message, "forward_from_message_id", None)
    if fwd_chat and fwd_msg_id:
        return int(fwd_chat.id), int(fwd_msg_id)

    origin = getattr(message, "forward_origin", None)
    if not origin:
        return None, None

    origin_chat = getattr(origin, "chat", None)
    origin_message_id = getattr(origin, "message_id", None)
    if origin_chat and origin_message_id:
        return int(origin_chat.id), int(origin_message_id)

    return None, None


def _extract_comment_author(message: Message) -> tuple[int | None, str | None, str | None, str | None]:
    """Return a stable author identity for comment ingestion.

    In supergroups, messages may come from user accounts (from_user) or from chat identities
    (sender_chat), e.g. anonymous admins.
    """
    if message.from_user and not message.from_user.is_bot:
        return (
            int(message.from_user.id),
            message.from_user.username,
            message.from_user.first_name,
            message.from_user.last_name,
        )

    if message.sender_chat:
        chat = message.sender_chat
        return int(chat.id), getattr(chat, "username", None), getattr(chat, "title", None), None

    return None, None, None, None


def _is_admin(update: Update, admin_ids: set[int]) -> bool:
    user = update.effective_user
    return bool(user and user.id in admin_ids)


async def _require_admin(update: Update, admin_ids: set[int]) -> bool:
    if _is_admin(update, admin_ids):
        return True
    if update.effective_message:
        await update.effective_message.reply_text("Доступ запрещен")
    return False


def _resolve_channel_post_id(message: Message, linked_chat_id: int, repo: BotRepository, channel_id: int) -> int | None:
    if message.message_thread_id:
        mapped = repo.get_channel_post_by_discussion_root(linked_chat_id, message.message_thread_id)
        if mapped:
            return mapped

    current = message.reply_to_message
    hops = 0
    while current and hops < 5:
        mapped = repo.get_channel_post_by_discussion_root(linked_chat_id, current.message_id)
        if mapped:
            return mapped

        forward_channel_id, forward_message_id = _extract_forward_channel_info(current)
        if forward_channel_id == channel_id and forward_message_id:
            return forward_message_id

        current = current.reply_to_message
        hops += 1

    return None


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    help_text = (
        "Панель управления ботом\n\n"
        "Посты:\n"
        "- /post <текст>\n"
        "- /edit <message_id> <новый текст>\n"
        "- /delete <message_id>\n\n"
        "Аналитика:\n"
        "- /stats [hours]\n"
        "- /poststats <message_id>\n"
        "- /chart [days]\n"
        "- /tgstats [posts_limit]\n"
        "- /refreshmetrics [posts_limit]\n"
        "- /contacts\n"
        "- /refreshcontacts\n\n"
        "Диагностика:\n"
        "- /binddiscussion\n"
        "- /health"
    )
    await update.effective_message.reply_text(help_text)


async def _refresh_mtproto_metrics(context: ContextTypes.DEFAULT_TYPE, posts_limit: int) -> int:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]
    mtproto = TelegramApiMetricsService(settings=settings)

    metrics = await mtproto.fetch_recent_post_metrics(limit=posts_limit)
    if not metrics:
        return 0

    snapshot_at = datetime.now(tz=timezone.utc).isoformat()
    rows = [
        {
            "message_id": m.message_id,
            "post_date": m.post_date,
            "views": m.views,
            "forwards": m.forwards,
            "reactions_total": m.reactions_total,
            "reactions_json": m.reactions_json,
        }
        for m in metrics
    ]
    return repo.save_post_metrics_snapshots(rows=rows, snapshot_at=snapshot_at)


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    text = " ".join(context.args).strip()
    if not text:
        await update.effective_message.reply_text("Использование: /post <текст>")
        return

    sent = await context.bot.send_message(chat_id=settings.channel_id, text=text)
    repo.upsert_post(channel_id=settings.channel_id, message_id=sent.message_id, text=text)
    repo.log_action(update.effective_user.id, "post", {"message_id": sent.message_id})

    await update.effective_message.reply_text(
        "Пост опубликован\n"
        f"- message_id: {sent.message_id}\n"
        f"- канал: {settings.channel_id}"
    )


async def cmd_edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    if len(context.args) < 2:
        await update.effective_message.reply_text("Использование: /edit <message_id> <новый текст>")
        return

    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("message_id должен быть числом")
        return

    new_text = " ".join(context.args[1:]).strip()
    if not new_text:
        await update.effective_message.reply_text("Новый текст пуст")
        return

    await context.bot.edit_message_text(
        chat_id=settings.channel_id,
        message_id=message_id,
        text=new_text,
    )
    repo.upsert_post(channel_id=settings.channel_id, message_id=message_id, text=new_text)
    repo.log_action(update.effective_user.id, "edit", {"message_id": message_id})

    await update.effective_message.reply_text(
        "Пост обновлен\n"
        f"- message_id: {message_id}"
    )


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text("Использование: /delete <message_id>")
        return

    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("message_id должен быть числом")
        return

    await context.bot.delete_message(chat_id=settings.channel_id, message_id=message_id)
    repo.mark_post_deleted(message_id)
    repo.log_action(update.effective_user.id, "delete", {"message_id": message_id})

    await update.effective_message.reply_text(
        "Пост удален\n"
        f"- message_id: {message_id}"
    )


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    period_hours = 24
    if context.args:
        try:
            period_hours = max(1, min(24 * 30, int(context.args[0])))
        except ValueError:
            await update.effective_message.reply_text("Использование: /stats [hours]")
            return

    stats = repo.aggregate_stats(period_hours=period_hours)
    channel_metrics = repo.get_latest_channel_metric_stats(limit_posts=settings.mtproto_metrics_posts_limit)
    text = (
        f"Статистика за {period_hours} ч\n\n"
        f"- Постов: {stats.posts_count}\n"
        f"- Комментариев: {stats.comments_count}\n"
        f"- Уникальных комментаторов: {stats.unique_commenters}\n"
        f"- Лидов: {stats.leads_count}\n"
        "\n"
        f"MTProto (последних {channel_metrics.posts_sampled} постов):\n"
        f"- Просмотры (сумма): {channel_metrics.total_views}\n"
        f"- Просмотры/пост (среднее): {channel_metrics.avg_views_per_post:.1f}\n"
        f"- Реакции (сумма): {channel_metrics.total_reactions}\n"
        f"- Репосты (сумма): {channel_metrics.total_forwards}\n"
        "\n"
        "Если MTProto не настроен или нет данных: /refreshmetrics"
    )
    await update.effective_message.reply_text(text)


async def cmd_refreshmetrics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    if not await _require_admin(update, settings.admin_ids):
        return

    posts_limit = settings.mtproto_metrics_posts_limit
    if context.args:
        try:
            posts_limit = max(1, min(200, int(context.args[0])))
        except ValueError:
            await update.effective_message.reply_text("Использование: /refreshmetrics [posts_limit]")
            return

    mtproto = TelegramApiMetricsService(settings=settings)
    if not mtproto.enabled:
        await update.effective_message.reply_text(
            "MTProto не настроен. Укажите TELEGRAM_API_ID и TELEGRAM_API_HASH в .env"
        )
        return

    try:
        count = await _refresh_mtproto_metrics(context=context, posts_limit=posts_limit)
    except Exception as exc:
        LOGGER.warning("MTProto refresh failed: %s", exc)
        await update.effective_message.reply_text(
            "MTProto обновление не выполнено. Проверьте, что Telethon-сессия авторизована как пользователь, не как бот."
        )
        return

    await update.effective_message.reply_text(
        "Метрики Telegram API обновлены\n"
        f"- постов: {count}\n"
        f"- окно: {posts_limit}"
    )


async def cmd_tgstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]
    if not await _require_admin(update, settings.admin_ids):
        return

    posts_limit = settings.mtproto_metrics_posts_limit
    if context.args:
        try:
            posts_limit = max(1, min(200, int(context.args[0])))
        except ValueError:
            await update.effective_message.reply_text("Использование: /tgstats [posts_limit]")
            return

    channel_metrics = repo.get_latest_channel_metric_stats(limit_posts=posts_limit)
    if channel_metrics.posts_sampled == 0:
        await update.effective_message.reply_text(
            "Нет данных MTProto. Выполните /refreshmetrics для загрузки просмотров и реакций."
        )
        return

    text = (
        f"Telegram API метрики (последних {channel_metrics.posts_sampled} постов)\n\n"
        f"- Просмотры (сумма): {channel_metrics.total_views}\n"
        f"- Просмотры/пост (среднее): {channel_metrics.avg_views_per_post:.1f}\n"
        f"- Реакции (сумма): {channel_metrics.total_reactions}\n"
        f"- Репосты (сумма): {channel_metrics.total_forwards}"
    )
    await update.effective_message.reply_text(text)


async def cmd_poststats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    if len(context.args) != 1:
        await update.effective_message.reply_text("Использование: /poststats <message_id>")
        return

    try:
        message_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("message_id должен быть числом")
        return

    data = repo.get_post_analytics(message_id)
    if not data:
        await update.effective_message.reply_text("Пост с таким message_id не найден в БД")
        return

    post = data["post"]
    preview = (post.get("text") or "").strip().replace("\n", " ")
    if len(preview) > 100:
        preview = f"{preview[:100]}..."
    if not preview:
        preview = "<без текста>"

    lines = [
        f"Аналитика поста {message_id}",
        "",
        f"- Создан: {post.get('created_at')}",
        f"- Обновлен: {post.get('updated_at') or '-'}",
        f"- Удален: {post.get('deleted_at') or '-'}",
        f"- Комментариев: {data['comments_count']}",
        f"- Уникальных комментаторов: {data['unique_commenters']}",
        f"- Лидов в комментариях: {data['leads_in_comments']}",
        f"- Последний комментарий: {data['last_comment_at'] or '-'}",
        f"- Текст: {preview}",
    ]

    top = data.get("top_commenters", [])
    if top:
        lines.append("")
        lines.append("Топ комментаторов:")
        for idx, row in enumerate(top, start=1):
            lines.append(f"  {idx}. {row['author']} ({row['comments_count']})")

    metric = data.get("latest_metric")
    if metric:
        lines.append("")
        lines.append("MTProto метрики:")
        lines.append(f"- Views: {metric.get('views', 0)}")
        lines.append(f"- Reactions: {metric.get('reactions_total', 0)}")
        lines.append(f"- Forwards: {metric.get('forwards', 0)}")
        reactions = metric.get("reactions", {}) or {}
        if reactions:
            lines.append("- Реакции по типам:")
            for reaction, count in sorted(reactions.items(), key=lambda item: item[1], reverse=True):
                lines.append(f"  {reaction}: {count}")

    await update.effective_message.reply_text("\n".join(lines))


async def cmd_chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    days = settings.chart_default_days
    if context.args:
        try:
            days = max(1, min(365, int(context.args[0])))
        except ValueError:
            await update.effective_message.reply_text("Использование: /chart [days]")
            return

    trend = repo.get_comments_trend(days=days)
    image = render_comments_trend_png(trend_rows=trend, days=days)
    await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=InputFile(BytesIO(image), filename=f"comments_trend_{days}d.png"),
        caption=f"График комментариев за {days} дней",
    )


async def cmd_contacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    contacts = repo.get_contacts_cache()
    if not contacts:
        repo.rebuild_contacts_cache(
            posts_limit=settings.contacts_posts_limit,
            commenters_limit=settings.contacts_commenters_limit,
        )
        contacts = repo.get_contacts_cache()

    if not contacts:
        await update.effective_message.reply_text("Пока нет данных по контактам")
        return

    refreshed_at = contacts[0]["refreshed_at"]
    lines = [
        "Контакты",
        f"Окно: последние {settings.contacts_posts_limit} постов",
        f"Лимит: {settings.contacts_commenters_limit}",
        f"Обновлено: {refreshed_at}",
        "",
    ]
    for c in contacts:
        lines.append(f"{c['rank_pos']}. Ник: {c['nickname']}")
        lines.append(f"   Профиль: {c['profile_url']}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_refreshcontacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    count = repo.rebuild_contacts_cache(
        posts_limit=settings.contacts_posts_limit,
        commenters_limit=settings.contacts_commenters_limit,
    )
    await update.effective_message.reply_text(
        "Список контактов обновлен\n"
        f"- строк: {count}\n"
        f"- постов в окне: {settings.contacts_posts_limit}\n"
        f"- комментаторов в выдаче: {settings.contacts_commenters_limit}"
    )


async def cmd_health(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    detected_ids = sorted(context.application.bot_data.get("linked_chat_ids", set()))
    counters = context.application.bot_data.setdefault(
        "ingestion_counters",
        {"group_messages_seen": 0, "auto_forwards_seen": 0},
    )
    try:
        me = await context.bot.get_me()
    except TelegramError as exc:
        await update.effective_message.reply_text(f"Сетевая ошибка при health-check: {exc}")
        return

    linked_chat_access = "n/a"
    linked_chat_title = "n/a"
    linked_chat_type = "n/a"
    linked_chat_member_status = "n/a"
    channel_api_linked_chat_id = "n/a"

    try:
        channel_chat = await context.bot.get_chat(settings.channel_id)
        channel_api_linked_chat_id = getattr(channel_chat, "linked_chat_id", None) or "none"
    except TelegramError as exc:
        channel_api_linked_chat_id = f"error: {exc}"

    if settings.linked_chat_id:
        try:
            linked_chat = await context.bot.get_chat(settings.linked_chat_id)
            linked_chat_access = "ok"
            linked_chat_title = linked_chat.title or "(no title)"
            linked_chat_type = linked_chat.type
        except TelegramError as exc:
            linked_chat_access = f"error: {exc}"

        try:
            member = await context.bot.get_chat_member(settings.linked_chat_id, me.id)
            linked_chat_member_status = member.status
        except TelegramError as exc:
            linked_chat_member_status = f"error: {exc}"

    debug = repo.get_ingestion_debug()

    lines = [
        "Диагностика ingestion",
        "",
        f"- Config LINKED_CHAT_ID: {settings.linked_chat_id}",
        f"- API channel linked_chat_id: {channel_api_linked_chat_id}",
        f"- Bot privacy disabled: {bool(getattr(me, 'can_read_all_group_messages', False))}",
        f"- Linked chat access: {linked_chat_access}",
        f"- Linked chat title: {linked_chat_title}",
        f"- Linked chat type: {linked_chat_type}",
        f"- Bot member status in linked chat: {linked_chat_member_status}",
        f"- Runtime linked chats: {detected_ids if detected_ids else '[]'}",
        f"- Seen group messages: {counters['group_messages_seen']}",
        f"- Seen auto forwards: {counters['auto_forwards_seen']}",
        f"- discussion_map rows: {debug['discussion_map_count']}",
        f"- comments rows: {debug['comments_count']}",
        f"- users rows: {debug['users_count']}",
    ]

    if debug["top_linked_chats"]:
        lines.append("")
        lines.append("- Top chats in comments table:")
        for row in debug["top_linked_chats"]:
            lines.append(f"  chat_id={row['linked_chat_id']} comments={row['c']}")

    await update.effective_message.reply_text("\n".join(lines))


async def cmd_binddiscussion(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]

    if not await _require_admin(update, settings.admin_ids):
        return

    msg = update.effective_message
    if not msg or not msg.chat or msg.chat.type not in {"group", "supergroup"}:
        await update.effective_message.reply_text("Эту команду нужно вызвать в discussion-группе канала")
        return

    linked_chat_ids: set[int] = context.application.bot_data.setdefault("linked_chat_ids", set())
    linked_chat_ids.add(msg.chat.id)
    await update.effective_message.reply_text(
        "Discussion-чат привязан\n"
        f"- chat_id: {msg.chat.id}\n"
        "Теперь новые комментарии из этого чата будут учитываться."
    )


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    msg = update.channel_post
    if not msg or not msg.chat:
        return
    if msg.chat.id != settings.channel_id:
        return

    text = msg.text or msg.caption
    repo.upsert_post(channel_id=settings.channel_id, message_id=msg.message_id, text=text)
    LOGGER.debug("Channel post captured: message_id=%s", msg.message_id)


async def on_linked_chat_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    msg = update.effective_message
    if not msg or not msg.chat:
        return

    if msg.chat.type not in {"group", "supergroup"}:
        return

    linked_chat_ids: set[int] = context.application.bot_data.setdefault("linked_chat_ids", set())
    counters = context.application.bot_data.setdefault(
        "ingestion_counters",
        {"group_messages_seen": 0, "auto_forwards_seen": 0},
    )
    counters["group_messages_seen"] += 1

    if msg.is_automatic_forward:
        forward_channel_id, forward_message_id = _extract_forward_channel_info(msg)
        if forward_channel_id == settings.channel_id and forward_message_id:
            linked_chat_ids.add(msg.chat.id)
            counters["auto_forwards_seen"] += 1
            repo.save_discussion_root_map(
                linked_chat_id=msg.chat.id,
                root_group_message_id=msg.message_id,
                channel_post_id=forward_message_id,
            )
        return

    # Process messages only from configured/discovered discussion chats.
    if msg.chat.id not in linked_chat_ids:
        return

    author_id, author_username, author_first_name, author_last_name = _extract_comment_author(msg)
    if author_id is None:
        return

    text = (msg.text or msg.caption or "").strip()
    if text.startswith("/"):
        return

    channel_post_id = _resolve_channel_post_id(
        message=msg,
        linked_chat_id=msg.chat.id,
        repo=repo,
        channel_id=settings.channel_id,
    )

    repo.upsert_user(
        user_id=author_id,
        username=author_username,
        first_name=author_first_name,
        last_name=author_last_name,
    )

    if text:
        has_contact, has_intent, lead_score, tags = analyze_comment(text)
    else:
        has_contact, has_intent, lead_score, tags = False, False, 0, set()
    saved = repo.save_comment(
        group_message_id=msg.message_id,
        channel_post_id=channel_post_id,
        linked_chat_id=msg.chat.id,
        user_id=author_id,
        text=text or "<non-text-comment>",
        has_contact=has_contact if text else False,
        has_intent=has_intent if text else False,
    )
    if not saved:
        return

    if lead_score > 0:
        repo.upsert_lead(user_id=author_id, score_delta=lead_score, tags=tags)

    LOGGER.debug(
        "Comment saved: group_message_id=%s channel_post_id=%s user_id=%s",
        msg.message_id,
        channel_post_id,
        author_id,
    )
