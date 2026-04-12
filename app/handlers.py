from __future__ import annotations

import csv
import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from telegram import (
    BotCommand,
    Message,
    MessageReactionCountUpdated,
    MessageReactionUpdated,
    ReactionTypeCustomEmoji,
    ReactionTypeEmoji,
    ReactionTypePaid,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.repositories import BotRepository
from app.services import analyze_comment

LOGGER = logging.getLogger(__name__)

MENU_COMMANDS: list[BotCommand] = [
    BotCommand("start", "Коротко о возможностях бота"),
    BotCommand("help", "Подсказки и примеры команд"),
    BotCommand("stats", "Сводная статистика за период"),
    BotCommand("poststats", "Статистика по одному посту"),
    BotCommand("reactions", "Реакции по конкретному посту"),
    BotCommand("reactionstats", "Сводка реакций по каналу"),
    BotCommand("topposts", "Топ постов по реакциям"),
    BotCommand("topcommenters", "Топ юзеров (комм.+реакц.)"),
    BotCommand("postcommenters", "Топ юзеров по посту (комм.+реакц.)"),
    BotCommand("exportcsv", "Экспорт активности юзеров в CSV"),
    BotCommand("contacts", "Показать список активных контактов"),
    BotCommand("refreshcontacts", "Обновить список контактов"),
]


def _help_text() -> str:
    return (
        "🧭 <b>Команды аналитики</b>\n"
        "• <code>/stats [hours]</code> — сводка за период\n"
        "• <code>/poststats &lt;message_id&gt;</code> — детально по посту\n"
        "• <code>/reactions &lt;message_id&gt;</code> — реакции и последние изменения\n"
        "• <code>/reactionstats [hours]</code> — сводка реакций по каналу\n"
        "• <code>/topposts [limit]</code> — топ постов за 24ч\n"
        "• <code>/topposts &lt;hours&gt; [limit]</code> — топ постов за период\n"
        "• <code>/topcommenters [limit]</code> — топ юзеров (комментарии + реакции)\n"
        "• <code>/postcommenters &lt;message_id&gt; [limit]</code> — топ юзеров по посту (комм. + реакции)\n"
        "• <code>/exportcsv [hours] [limit] [message_id]</code> — CSV с активностью пользователей\n"
        "• <code>/contacts</code> — список активных контактов\n"
        "• <code>/refreshcontacts</code> — пересчитать контакты\n\n"
        "⚡ <b>Быстрые примеры</b>\n"
        "<code>/poststats 123</code>\n"
        "<code>/reactions 123</code>\n"
        "<code>/reactionstats 72</code>\n"
        "<code>/topposts 10</code>\n"
        "<code>/topposts 72 7</code>\n"
        "<code>/topcommenters 15</code>\n"
        "<code>/postcommenters 123 10</code>\n"
        "<code>/exportcsv 72 10 123</code>\n"
        "<code>/stats 72</code>"
    )


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _code(value: object) -> str:
    return f"<code>{_esc(value)}</code>"


def _bar(value: int, max_value: int, width: int = 8) -> str:
    if max_value <= 0:
        return "░" * width
    filled = max(1, round((value / max_value) * width)) if value > 0 else 0
    filled = min(width, filled)
    return "█" * filled + "░" * (width - filled)


async def _reply_html(update: Update, text: str, disable_preview: bool = True) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=disable_preview,
        )


def _reaction_to_key(reaction: object) -> str:
    if isinstance(reaction, ReactionTypeEmoji):
        return reaction.emoji
    if isinstance(reaction, ReactionTypeCustomEmoji):
        return f"custom:{reaction.custom_emoji_id}"
    if isinstance(reaction, ReactionTypePaid):
        return "paid"
    return "unknown"


def _reaction_list_to_keys(reactions: tuple[object, ...] | list[object] | None) -> list[str]:
    if not reactions:
        return []
    return [_reaction_to_key(reaction) for reaction in reactions]


def _to_utc_iso(dt) -> str:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc).isoformat()
    return dt.astimezone(timezone.utc).isoformat()


def _resolve_timezone(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def _to_datetime(value: object) -> datetime | None:
    if not value:
        return None

    if isinstance(value, datetime):
        dt = value
    else:
        raw = str(value).strip()
        if not raw or raw == "-":
            return None

        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _human_time(value: object, tz_name: str | None) -> str:
    dt = _to_datetime(value)
    if dt is None:
        return "-"

    local_dt = dt.astimezone(_resolve_timezone(tz_name))
    return local_dt.strftime("%d.%m.%Y %H:%M")


def _channel_post_url(channel_username: str | None, message_id: int) -> str | None:
    if not channel_username:
        return None
    return f"https://t.me/{channel_username}/{message_id}"


def _parse_topposts_args(args: list[str]) -> tuple[int, int]:
    period_hours = 24
    limit = 7

    if not args:
        return period_hours, limit

    if len(args) == 1:
        value = int(args[0])
        if value <= 24:
            return period_hours, max(1, min(50, value))
        return max(1, min(24 * 30, value)), limit

    if len(args) > 2:
        raise ValueError("Too many arguments")

    period_hours = max(1, min(24 * 30, int(args[0])))
    limit = max(1, min(50, int(args[1])))
    return period_hours, limit


def _write_csv(file_path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("w", encoding="utf-8", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({name: row.get(name, "") for name in fieldnames})


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
    await _reply_html(
        update,
        "⛔ <b>Доступ запрещен</b>\n"
        "Команда доступна только администраторам, указанным в <code>ADMIN_IDS</code>.",
    )
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
    text = (
        "🚀 <b>Бот активен</b>\n"
        "Отслеживаю комментарии и реакции канала в реальном времени.\n\n"
        "• Открой <code>/help</code> для полного списка команд\n"
        "• Быстрый старт: <code>/reactionstats 24</code>"
    )
    await _reply_html(update, text)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _reply_html(update, _help_text())


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
            await _reply_html(update, "ℹ️ Пример: <code>/stats 72</code>")
            return

    stats = repo.aggregate_stats(period_hours=period_hours)
    text = (
        f"📊 <b>Сводная статистика за {_code(period_hours)} ч</b>\n\n"
        f"• Постов: <b>{stats.posts_count}</b>\n"
        f"• Комментариев: <b>{stats.comments_count}</b>\n"
        f"• Уникальных комментаторов: <b>{stats.unique_commenters}</b>\n"
        f"• Лидов: <b>{stats.leads_count}</b>\n\n"
        "Подсказка: <code>/poststats &lt;message_id&gt;</code> для детального разбора поста."
    )
    await _reply_html(update, text)


async def cmd_reactions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    if len(context.args) != 1:
        await _reply_html(update, "ℹ️ Пример: <code>/reactions 123</code>")
        return

    try:
        message_id = int(context.args[0])
    except ValueError:
        await _reply_html(update, "⚠️ ID поста должен быть числом.")
        return

    counts = repo.get_message_reaction_counts(channel_id=settings.channel_id, message_id=message_id)
    events = repo.get_recent_message_reaction_events(
        channel_id=settings.channel_id,
        message_id=message_id,
        limit=6,
    )
    count_events = repo.get_recent_message_reaction_count_events(
        channel_id=settings.channel_id,
        message_id=message_id,
        limit=6,
    )

    if not counts and not events and not count_events:
        await _reply_html(
            update,
            "🫥 <b>По этому посту пока нет данных о реакциях</b>\n"
            "Проверь: бот админ в канале, реакции включены, и была новая реакция после запуска бота.",
        )
        return

    total = sum(int(row["total_count"]) for row in counts)
    updated_at = _human_time(counts[0]["updated_at"] if counts else None, settings.tz)

    lines = [
        f"🔥 <b>Реакции поста {_code(message_id)}</b>",
        "",
        f"• Всего реакций: <b>{total}</b>",
        f"• Последнее обновление: {_code(updated_at)}",
        "",
        "<b>Текущее распределение</b>",
    ]

    if counts:
        max_count = max(int(row["total_count"]) for row in counts)
        for row in counts:
            count_value = int(row["total_count"])
            lines.append(
                f"• {_esc(row['reaction_key'])} <b>{count_value}</b>  {_bar(count_value, max_count)}"
            )
    else:
        lines.append("• Нет агрегированных данных")

    if events:
        lines.append("")
        lines.append("<b>Последние изменения</b>")
        for event in events:
            actor = event["actor_user_id"] or event["actor_chat_id"] or "unknown"
            old_reaction = ", ".join(event["old_reaction"]) if event["old_reaction"] else "none"
            new_reaction = ", ".join(event["new_reaction"]) if event["new_reaction"] else "none"
            lines.append(
                f"• {_code(_human_time(event['event_at'], settings.tz))}: {_esc(old_reaction)} → {_esc(new_reaction)} "
                f"(<i>actor={_esc(actor)}</i>)"
            )

    if count_events:
        lines.append("")
        lines.append("<b>Последние снимки счетчиков</b>")
        for event in count_events:
            snapshot = ", ".join(
                f"{item['reaction_key']}={item['total_count']}" for item in event["snapshot"]
            )
            lines.append(
                f"• {_code(_human_time(event['event_at'], settings.tz))}: total=<b>{event['total_count']}</b> "
                f"(<i>{_esc(snapshot or 'empty')}</i>)"
            )

    await _reply_html(update, "\n".join(lines))


async def cmd_reactionstats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    period_hours = 24
    if context.args:
        try:
            period_hours = max(1, min(24 * 30, int(context.args[0])))
        except ValueError:
            await _reply_html(update, "ℹ️ Пример: <code>/reactionstats 72</code>")
            return

    overview = repo.get_reaction_overview(channel_id=settings.channel_id, period_hours=period_hours)

    lines = [
        f"⚡ <b>Сводка реакций за {_code(period_hours)} ч</b>",
        "",
        f"• Событий изменения реакций: <b>{overview['events_in_period']}</b>",
        f"• Из них персональные: <b>{overview['reaction_events_in_period']}</b>",
        f"• Из них агрегированные: <b>{overview['reaction_count_events_in_period']}</b>",
        f"• Постов с изменениями: <b>{overview['posts_touched_in_period']}</b>",
        f"• Текущая сумма реакций: <b>{overview['current_total_reactions']}</b>",
    ]

    lines.append("")
    lines.append("<b>Топ реакций</b>")
    if overview["top_reactions"]:
        max_reaction_total = max(int(row["total"]) for row in overview["top_reactions"])
        for row in overview["top_reactions"]:
            total_value = int(row["total"])
            lines.append(
                f"• {_esc(row['reaction_key'])} <b>{total_value}</b>  {_bar(total_value, max_reaction_total)}"
            )
    else:
        lines.append("• Нет данных")

    lines.append("")
    lines.append("<b>Топ постов по реакциям</b>")
    if overview["top_posts"]:
        max_post_total = max(int(row["total"]) for row in overview["top_posts"])
        for row in overview["top_posts"]:
            total_value = int(row["total"])
            lines.append(
                f"• post {_code(row['message_id'])}: <b>{total_value}</b>  {_bar(total_value, max_post_total)}"
            )
    else:
        lines.append("• Нет данных")

    await _reply_html(update, "\n".join(lines))


async def cmd_topposts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]
    channel_username = context.application.bot_data.get("channel_username")

    if not await _require_admin(update, settings.admin_ids):
        return

    try:
        period_hours, limit = _parse_topposts_args(context.args)
    except ValueError:
        await _reply_html(
            update,
            "ℹ️ Примеры: <code>/topposts 10</code>, <code>/topposts 72</code> или <code>/topposts 72 7</code>",
        )
        return

    rows = repo.get_top_reacted_posts(channel_id=settings.channel_id, period_hours=period_hours, limit=limit)
    if not rows:
        await _reply_html(
            update,
            "🫥 <b>Пока нет данных для топа постов</b>\n"
            "Поставь несколько реакций на посты и попробуй снова.",
        )
        return

    max_total = max(int(row["reactions_total"] or 0) for row in rows) or 1
    lines = [
        f"🏆 <b>Топ постов по реакциям за {_code(period_hours)} ч</b>",
        f"• Лимит: <b>{limit}</b> (найдено: <b>{len(rows)}</b>)",
        "",
    ]

    for idx, row in enumerate(rows, start=1):
        message_id = int(row["message_id"])
        total = int(row["reactions_total"] or 0)
        updated_at = _human_time(row.get("last_reaction_update_at"), settings.tz)
        preview = (row.get("text") or "<без текста>").strip().replace("\n", " ")
        if len(preview) > 90:
            preview = f"{preview[:90]}..."

        post_url = _channel_post_url(channel_username, message_id)
        post_ref = (
            f"<a href=\"{_esc(post_url)}\">post {_esc(message_id)}</a>"
            if post_url
            else f"post {_code(message_id)}"
        )
        lines.append(
            f"• {idx}. {post_ref} — <b>{total}</b>  {_bar(total, max_total)}"
        )
        lines.append(f"  обновлено: {_code(updated_at)}")
        lines.append(f"  <i>{_esc(preview)}</i>")

    await _reply_html(update, "\n".join(lines), disable_preview=False)


async def cmd_topcommenters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    limit = 10
    if context.args:
        try:
            limit = max(1, min(30, int(context.args[0])))
        except ValueError:
            await _reply_html(update, "ℹ️ Пример: <code>/topcommenters 15</code>")
            return

    rows = repo.get_top_commenters(limit=limit)
    if not rows:
        await _reply_html(
            update,
            "🫥 <b>Пока нет данных по активности юзеров</b>\n"
            "Когда появятся комментарии или реакции, топ станет доступен.",
        )
        return

    max_score = max(int(row.get("activity_score") or 0) for row in rows) or 1
    lines = [
        f"👤 <b>Топ юзеров по активности (комм. + реакции)</b>",
        f"• Лимит: <b>{limit}</b> (найдено: <b>{len(rows)}</b>)",
        "",
    ]

    for idx, row in enumerate(rows, start=1):
        profile_url = _esc(row["profile_url"])
        comments_count = int(row["comments_count"])
        reaction_events_count = int(row.get("reaction_events_count") or 0)
        activity_score = int(row.get("activity_score") or (comments_count + reaction_events_count))
        last_comment_at = _human_time(row["last_comment_at"], settings.tz)
        last_reaction_at = _human_time(row.get("last_reaction_at"), settings.tz)
        lines.append(
            f"• {idx}. <a href=\"{profile_url}\">{_esc(row['display_name'])}</a> — "
            f"score=<b>{activity_score}</b>  {_bar(activity_score, max_score)}"
        )
        lines.append(
            f"  comments={_code(comments_count)} reactions={_code(reaction_events_count)}"
        )
        lines.append(
            f"  last_comment={_code(last_comment_at)} "
            f"last_reaction={_code(last_reaction_at)}"
        )

    await _reply_html(update, "\n".join(lines), disable_preview=False)


async def cmd_postcommenters(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]
    channel_username = context.application.bot_data.get("channel_username")

    if not await _require_admin(update, settings.admin_ids):
        return

    if not context.args:
        await _reply_html(update, "ℹ️ Пример: <code>/postcommenters 123 10</code>")
        return

    try:
        message_id = int(context.args[0])
    except ValueError:
        await _reply_html(update, "⚠️ ID поста должен быть числом.")
        return

    limit = 10
    if len(context.args) > 1:
        try:
            limit = max(1, min(30, int(context.args[1])))
        except ValueError:
            await _reply_html(update, "ℹ️ Пример: <code>/postcommenters 123 10</code>")
            return

    rows = repo.get_top_commenters_for_post(message_id=message_id, limit=limit)
    if not rows:
        await _reply_html(
            update,
            "🫥 <b>По этому посту пока нет активности</b>\n"
            "Проверь ID поста и попробуй снова позже.",
        )
        return

    post_url = _channel_post_url(channel_username, message_id)
    post_ref = (
        f"<a href=\"{_esc(post_url)}\">post {_esc(message_id)}</a>"
        if post_url
        else f"post {_code(message_id)}"
    )

    max_score = max(int(row.get("activity_score") or 0) for row in rows) or 1
    lines = [
        f"🧵 <b>Топ юзеров по {post_ref} (комм. + реакции)</b>",
        f"• Лимит: <b>{limit}</b> (найдено: <b>{len(rows)}</b>)",
        "",
    ]

    for idx, row in enumerate(rows, start=1):
        profile_url = _esc(row["profile_url"])
        comments_count = int(row["comments_count"])
        reaction_events_count = int(row.get("reaction_events_count") or 0)
        activity_score = int(row.get("activity_score") or (comments_count + reaction_events_count))
        last_comment_at = _human_time(row["last_comment_at"], settings.tz)
        last_reaction_at = _human_time(row.get("last_reaction_at"), settings.tz)
        lines.append(
            f"• {idx}. <a href=\"{profile_url}\">{_esc(row['display_name'])}</a> — "
            f"score=<b>{activity_score}</b>  {_bar(activity_score, max_score)}"
        )
        lines.append(
            f"  comments={_code(comments_count)} reactions={_code(reaction_events_count)}"
        )
        lines.append(
            f"  last_comment={_code(last_comment_at)} "
            f"last_reaction={_code(last_reaction_at)}"
        )

    await _reply_html(update, "\n".join(lines), disable_preview=False)


async def cmd_exportcsv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    period_hours = 24
    limit = 10
    message_id: int | None = None

    if context.args:
        try:
            period_hours = max(1, min(24 * 30, int(context.args[0])))
            if len(context.args) > 1:
                limit = max(1, min(30, int(context.args[1])))
            if len(context.args) > 2:
                message_id = int(context.args[2])
        except ValueError:
            await _reply_html(update, "ℹ️ Пример: <code>/exportcsv 72 10 123</code>")
            return

    selected_rows = (
        repo.get_top_commenters_for_post(message_id=message_id, limit=limit)
        if message_id is not None
        else repo.get_top_commenters(limit=limit)
    )

    if not selected_rows:
        await _reply_html(
            update,
            "🫥 <b>Нет данных для экспорта</b>\n"
            "Сначала собери немного статистики (реакции/комментарии), затем попробуй снова.",
        )
        return

    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    export_dir = Path("data/exports")
    export_file = export_dir / f"analytics_export_{ts}.csv"

    commenter_keys = [str(row["commenter_key"]).lower() for row in selected_rows]
    user_stats_map = repo.get_commenters_full_stats(commenter_keys) if commenter_keys else {}

    export_rows: list[dict] = []
    for row in selected_rows:
        commenter_key = str(row["commenter_key"]).lower()
        user_stats = user_stats_map.get(
            commenter_key,
            {
                "unique_posts_commented": 0,
                "last_comment_at": "",
                "most_liked_comment_text": "",
                "most_liked_comment_likes": 0,
            },
        )
        username = (row.get("username") or "").strip() or row.get("display_name") or str(row["user_id"])
        most_liked_comment_text = str(user_stats.get("most_liked_comment_text") or "").replace("\n", " ").strip()
        most_liked_comment_likes = int(user_stats.get("most_liked_comment_likes") or 0)
        if most_liked_comment_text:
            most_liked_comment = f"{most_liked_comment_likes} | {most_liked_comment_text}"
            if len(most_liked_comment) > 220:
                most_liked_comment = f"{most_liked_comment[:217]}..."
        else:
            most_liked_comment = "-"

        export_rows.append(
            {
                "username": username,
                "commenters count": int(row.get("comments_count") or 0),
                "reactions by user": int(row.get("reaction_events_count") or 0),
                "last comment time": _human_time(
                    row.get("last_comment_at") or user_stats.get("last_comment_at"),
                    settings.tz,
                ),
                "user unique post commented": int(user_stats.get("unique_posts_commented") or 0),
                "most liked comment": most_liked_comment,
            }
        )

    _write_csv(
        export_file,
        [
            "username",
            "commenters count",
            "reactions by user",
            "last comment time",
            "user unique post commented",
            "most liked comment",
        ],
        export_rows,
    )

    if not update.effective_message:
        return

    with export_file.open("rb") as fh:
        await update.effective_message.reply_document(
            document=fh,
            filename=export_file.name,
            caption="analytics CSV",
        )

    await _reply_html(
        update,
        "✅ <b>Экспорт завершен: CSV по пользователям</b>\n"
        f"• Период: {_code(period_hours)} ч\n"
        f"• Лимит: <b>{limit}</b>\n"
        f"• Строк в файле: <b>{len(export_rows)}</b>\n"
        f"• Файл: {_code(export_file.name)}",
    )


async def cmd_poststats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    if len(context.args) != 1:
        await _reply_html(update, "ℹ️ Пример: <code>/poststats 123</code>")
        return

    try:
        message_id = int(context.args[0])
    except ValueError:
        await _reply_html(update, "⚠️ ID поста должен быть числом.")
        return

    data = repo.get_post_analytics(message_id)
    if not data:
        await _reply_html(
            update,
            "🫥 <b>Пост с таким ID не найден в базе</b>\n"
            "Проверь ID или дождись, пока бот зафиксирует пост в канале.",
        )
        return

    post = data["post"]
    preview = (post.get("text") or "").strip().replace("\n", " ")
    if len(preview) > 100:
        preview = f"{preview[:100]}..."
    if not preview:
        preview = "<без текста>"

    lines = [
        f"🧾 <b>Аналитика поста {_code(message_id)}</b>",
        "",
        f"• Создан: {_code(_human_time(post.get('created_at'), settings.tz))}",
        f"• Обновлен: {_code(_human_time(post.get('updated_at'), settings.tz))}",
        f"• Удален: {_code(_human_time(post.get('deleted_at'), settings.tz))}",
        f"• Комментариев: <b>{data['comments_count']}</b>",
        f"• Уникальных комментаторов: <b>{data['unique_commenters']}</b>",
        f"• Лидов в комментариях: <b>{data['leads_in_comments']}</b>",
        f"• Последний комментарий: {_code(_human_time(data['last_comment_at'], settings.tz))}",
        f"• Реакций: <b>{data['reactions_total']}</b>",
        f"• Последнее событие по реакциям: {_code(_human_time(data['reactions_last_event_at'], settings.tz))}",
        f"• Текст: <i>{_esc(preview)}</i>",
    ]

    reaction_counts = data.get("reaction_counts", [])
    if reaction_counts:
        lines.append("")
        lines.append("<b>Распределение реакций</b>")
        max_count = max(int(row["total_count"]) for row in reaction_counts)
        for row in reaction_counts:
            count_value = int(row["total_count"])
            lines.append(f"• {_esc(row['reaction_key'])} <b>{count_value}</b>  {_bar(count_value, max_count)}")

    top = data.get("top_commenters", [])
    if top:
        lines.append("")
        lines.append("<b>Топ комментаторов</b>")
        for idx, row in enumerate(top, start=1):
            lines.append(f"• {idx}. {_esc(row['author'])} — <b>{row['comments_count']}</b>")

    await _reply_html(update, "\n".join(lines))


async def cmd_reactiondebug(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    counters = context.application.bot_data.setdefault(
        "reaction_counters",
        {
            "message_reaction_seen": 0,
            "message_reaction_count_seen": 0,
            "last_message_reaction_at": None,
            "last_message_reaction_count_at": None,
        },
    )

    overview = repo.get_reaction_overview(channel_id=settings.channel_id, period_hours=24)

    member_status = "unknown"
    try:
        me = await context.bot.get_me()
        member = await context.bot.get_chat_member(settings.channel_id, me.id)
        member_status = member.status
    except Exception as exc:
        member_status = f"error: {exc}"

    lines = [
        "🛠️ <b>Диагностика reaction updates</b>",
        "",
        f"• Канал: {_code(settings.channel_id)}",
        f"• Статус бота в канале: <b>{_esc(member_status)}</b>",
        f"• Получено message_reaction: <b>{int(counters['message_reaction_seen'])}</b>",
        f"• Последний message_reaction: {_code(_human_time(counters['last_message_reaction_at'], settings.tz))}",
        f"• Получено message_reaction_count: <b>{int(counters['message_reaction_count_seen'])}</b>",
        f"• Последний message_reaction_count: {_code(_human_time(counters['last_message_reaction_count_at'], settings.tz))}",
        f"• Записей в БД (реакции суммарно): <b>{overview['current_total_reactions']}</b>",
        f"• Изменений реакций за 24ч: <b>{overview['events_in_period']}</b>",
        f"• Персональные события за 24ч: <b>{overview['reaction_events_in_period']}</b>",
        f"• Агрегированные события за 24ч: <b>{overview['reaction_count_events_in_period']}</b>",
        "",
        "<b>Если нули, проверь</b>",
        "• Реакции действительно ставили после запуска бота",
        "• Бот администратор канала",
        "• Реакции включены в настройках канала",
        "• Реакцию ставят пользователи, не боты",
    ]

    await _reply_html(update, "\n".join(lines))


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
        await _reply_html(
            update,
            "📭 <b>Пока нет данных по контактам</b>\n"
            "Когда появятся комментарии к постам, список заполнится автоматически.",
        )
        return

    refreshed_at = _human_time(contacts[0]["refreshed_at"], settings.tz)
    lines = [
        "👥 <b>Контакты</b>",
        f"• Окно: последние <b>{settings.contacts_posts_limit}</b> постов",
        f"• Лимит: <b>{settings.contacts_commenters_limit}</b>",
        f"• Обновлено: {_code(refreshed_at)}",
        "",
    ]
    for c in contacts:
        profile_url = _esc(c["profile_url"])
        lines.append(
            f"• {c['rank_pos']}. <b>{_esc(c['nickname'])}</b> — "
            f"<a href=\"{profile_url}\">профиль</a> (<i>{c['comments_count']} комм.</i>)"
        )

    await _reply_html(update, "\n".join(lines), disable_preview=False)


async def cmd_refreshcontacts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    if not await _require_admin(update, settings.admin_ids):
        return

    count = repo.rebuild_contacts_cache(
        posts_limit=settings.contacts_posts_limit,
        commenters_limit=settings.contacts_commenters_limit,
    )
    await _reply_html(
        update,
        "✅ <b>Список контактов обновлен</b>\n"
        f"• Записей: <b>{count}</b>\n"
        f"• Окно постов: <b>{settings.contacts_posts_limit}</b>\n"
        f"• Лимит комментаторов: <b>{settings.contacts_commenters_limit}</b>",
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


async def on_message_reaction(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    reaction: MessageReactionUpdated | None = update.message_reaction
    if not reaction or not reaction.chat:
        return

    chat_id = int(reaction.chat.id)
    linked_chat_ids: set[int] = context.application.bot_data.setdefault("linked_chat_ids", set())
    is_channel_post = chat_id == settings.channel_id
    is_linked_chat_message = chat_id in linked_chat_ids or (
        settings.linked_chat_id is not None and chat_id == settings.linked_chat_id
    )

    if not is_channel_post and not is_linked_chat_message:
        return

    old_reaction = _reaction_list_to_keys(reaction.old_reaction)
    new_reaction = _reaction_list_to_keys(reaction.new_reaction)
    actor_user_id = int(reaction.user.id) if reaction.user else None
    actor_chat_id = int(reaction.actor_chat.id) if reaction.actor_chat else None
    event_at = _to_utc_iso(reaction.date)

    if reaction.user:
        repo.upsert_user(
            user_id=int(reaction.user.id),
            username=reaction.user.username,
            first_name=reaction.user.first_name,
            last_name=reaction.user.last_name,
        )

    if is_channel_post:
        counters = context.application.bot_data.setdefault(
            "reaction_counters",
            {
                "message_reaction_seen": 0,
                "message_reaction_count_seen": 0,
                "last_message_reaction_at": None,
                "last_message_reaction_count_at": None,
            },
        )
        counters["message_reaction_seen"] += 1
        counters["last_message_reaction_at"] = event_at

    repo.log_message_reaction_event(
        channel_id=chat_id,
        message_id=reaction.message_id,
        actor_user_id=actor_user_id,
        actor_chat_id=actor_chat_id,
        old_reaction=old_reaction,
        new_reaction=new_reaction,
        event_at=event_at,
    )

    if is_channel_post:
        LOGGER.info(
            "Reaction changed: message_id=%s old=%s new=%s actor_user_id=%s actor_chat_id=%s",
            reaction.message_id,
            old_reaction,
            new_reaction,
            actor_user_id,
            actor_chat_id,
        )
    else:
        LOGGER.debug(
            "Linked-chat reaction changed: chat_id=%s message_id=%s old=%s new=%s",
            chat_id,
            reaction.message_id,
            old_reaction,
            new_reaction,
        )


async def on_message_reaction_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    settings = context.application.bot_data["settings"]
    repo: BotRepository = context.application.bot_data["repo"]

    reaction_count: MessageReactionCountUpdated | None = update.message_reaction_count
    if not reaction_count or not reaction_count.chat:
        return
    chat_id = int(reaction_count.chat.id)
    linked_chat_ids: set[int] = context.application.bot_data.setdefault("linked_chat_ids", set())
    is_channel_post = chat_id == settings.channel_id
    is_linked_chat_message = chat_id in linked_chat_ids or (
        settings.linked_chat_id is not None and chat_id == settings.linked_chat_id
    )

    if not is_channel_post and not is_linked_chat_message:
        return

    rows = [(_reaction_to_key(item.type), int(item.total_count)) for item in reaction_count.reactions]
    event_at = _to_utc_iso(reaction_count.date)

    if is_channel_post:
        counters = context.application.bot_data.setdefault(
            "reaction_counters",
            {
                "message_reaction_seen": 0,
                "message_reaction_count_seen": 0,
                "last_message_reaction_at": None,
                "last_message_reaction_count_at": None,
            },
        )
        counters["message_reaction_count_seen"] += 1
        counters["last_message_reaction_count_at"] = event_at

    variants = repo.replace_message_reaction_counts(
        channel_id=chat_id,
        message_id=reaction_count.message_id,
        reactions=rows,
        updated_at=event_at,
    )
    repo.log_message_reaction_count_event(
        channel_id=chat_id,
        message_id=reaction_count.message_id,
        reactions=rows,
        event_at=event_at,
    )
    total = sum(count for _, count in rows)

    if is_channel_post:
        LOGGER.info(
            "Reaction counts updated: message_id=%s total=%s variants=%s",
            reaction_count.message_id,
            total,
            variants,
        )
    else:
        LOGGER.debug(
            "Linked-chat reaction counts updated: chat_id=%s message_id=%s total=%s variants=%s",
            chat_id,
            reaction_count.message_id,
            total,
            variants,
        )


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
