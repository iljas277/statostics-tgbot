from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field
from telegram import Bot
from telegram.request import HTTPXRequest

from app.charts import render_comments_trend_png, render_metric_trend_png
from app.config import Settings
from app.db import Database
from app.repositories import BotRepository
from app.telegram_api import describe_reaction_key


class PostCreatePayload(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"text": "Новый пост: акция до 20:00, подробности в комментариях."}}
    )

    text: str = Field(
        ...,
        description="Текст поста для публикации в канал",
        examples=["Новый пост: акция до 20:00, подробности в комментариях."],
    )


class PostEditPayload(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"text": "Обновление: акция продлена до 22:00."}}
    )

    text: str = Field(
        ...,
        description="Обновленный текст поста",
        examples=["Обновление: акция продлена до 22:00."],
    )


def _format_timestamp(value: str | None) -> str:
    if not value:
        return ""
    raw = value.strip()
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return raw

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local_dt = dt.astimezone()
    return local_dt.strftime("%d.%m.%Y %H:%M")


def _csv_response(filename: str, rows: list[dict], headers: list[str]) -> Response:
    """Build downloadable CSV response from dict rows with stable header ordering."""
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        normalized: dict[str, object] = {}
        for key in headers:
            value = row.get(key, "")
            if key.endswith("_at") and isinstance(value, str):
                value = _format_timestamp(value)
            normalized[key] = value
        writer.writerow(normalized)
    content = buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _normalize_timestamps_in_row(row: dict) -> dict:
    normalized: dict[str, object] = {}
    for key, value in row.items():
        if key.endswith("_at") and isinstance(value, str):
            normalized[key] = _format_timestamp(value)
            continue
        normalized[key] = value
    return normalized


def _normalize_timestamps_in_payload(payload: object) -> object:
    if isinstance(payload, list):
        return [_normalize_timestamps_in_payload(item) for item in payload]
    if isinstance(payload, dict):
        normalized = _normalize_timestamps_in_row(payload)
        for key, value in list(normalized.items()):
            if isinstance(value, (list, dict)):
                normalized[key] = _normalize_timestamps_in_payload(value)
        return normalized
    return payload


def create_web_app(settings: Settings) -> FastAPI:
    db = Database(settings.db_path)
    db.init_schema()
    repo = BotRepository(db)

    app = FastAPI(
        title="Telegram Analytics Panel",
        version="1.0.0",
        docs_url="/app/docs",
        redoc_url=None,
        openapi_url="/app/openapi.json",
    )
    templates = Jinja2Templates(directory=str(Path("web/templates")))

    request = HTTPXRequest(
        proxy=settings.telegram_proxy_url,
        httpx_kwargs={"trust_env": False},
    )

    def _build_bot() -> Bot:
        return Bot(token=settings.bot_token, request=request, get_updates_request=request)

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        summary = repo.get_dashboard_summary()
        top_posts = _normalize_timestamps_in_payload(repo.get_posts_with_comment_counts(limit=10))
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={
                "summary": summary,
                "top_posts": top_posts,
                "default_days": settings.chart_default_days,
            },
        )

    @app.get("/api/summary", response_class=JSONResponse)
    async def api_summary() -> JSONResponse:
        return JSONResponse(repo.get_dashboard_summary())

    @app.get("/api/stats", response_class=JSONResponse)
    async def api_stats(hours: int = Query(default=24, ge=1, le=24 * 30)) -> JSONResponse:
        stats = repo.aggregate_stats(period_hours=hours)
        return JSONResponse(
            {
                "period_hours": hours,
                "posts_count": stats.posts_count,
                "comments_count": stats.comments_count,
                "unique_commenters": stats.unique_commenters,
            }
        )

    @app.get("/api/tgstats", response_class=JSONResponse)
    async def api_tgstats(posts_limit: int = Query(default=settings.mtproto_metrics_posts_limit, ge=1, le=200)) -> JSONResponse:
        metrics = repo.get_latest_channel_metric_stats(limit_posts=posts_limit)
        return JSONResponse(
            {
                "posts_sampled": metrics.posts_sampled,
                "unique_views_total": metrics.total_views,
                "unique_views_avg_per_post": metrics.avg_views_per_post,
                "total_views": metrics.total_views,
                "total_reactions": metrics.total_reactions,
                "total_forwards": metrics.total_forwards,
                "avg_views_per_post": metrics.avg_views_per_post,
            }
        )

    @app.get("/api/top-posts", response_class=JSONResponse)
    async def api_top_posts(limit: int = Query(default=20, ge=1, le=100)) -> JSONResponse:
        rows = repo.get_posts_with_comment_counts(limit=limit)
        return JSONResponse(_normalize_timestamps_in_payload(rows))

    @app.post("/api/posts", response_class=JSONResponse)
    async def api_create_post(payload: PostCreatePayload) -> JSONResponse:
        text = payload.text.strip()
        if not text:
            return JSONResponse({"error": "bad_request", "message": "text is required"}, status_code=400)

        bot = _build_bot()
        await bot.initialize()
        try:
            sent = await bot.send_message(chat_id=settings.channel_id, text=text)
        finally:
            await bot.shutdown()

        repo.upsert_post(channel_id=settings.channel_id, message_id=sent.message_id, text=text)
        repo.log_action(0, "api_post", {"message_id": sent.message_id})
        return JSONResponse({"ok": True, "message_id": sent.message_id})

    @app.patch("/api/posts/{message_id}", response_class=JSONResponse)
    async def api_edit_post(message_id: int, payload: PostEditPayload) -> JSONResponse:
        text = payload.text.strip()
        if not text:
            return JSONResponse({"error": "bad_request", "message": "text is required"}, status_code=400)

        bot = _build_bot()
        await bot.initialize()
        try:
            await bot.edit_message_text(chat_id=settings.channel_id, message_id=message_id, text=text)
        finally:
            await bot.shutdown()

        repo.upsert_post(channel_id=settings.channel_id, message_id=message_id, text=text)
        repo.log_action(0, "api_edit", {"message_id": message_id})
        return JSONResponse({"ok": True, "message_id": message_id})

    @app.delete("/api/posts/{message_id}", response_class=JSONResponse)
    async def api_delete_post(message_id: int) -> JSONResponse:
        bot = _build_bot()
        await bot.initialize()
        try:
            await bot.delete_message(chat_id=settings.channel_id, message_id=message_id)
        finally:
            await bot.shutdown()

        repo.mark_post_deleted(message_id)
        repo.log_action(0, "api_delete", {"message_id": message_id})
        return JSONResponse({"ok": True, "message_id": message_id})

    @app.get("/api/poststats/{message_id}", response_class=JSONResponse)
    async def api_poststats(message_id: int) -> JSONResponse:
        data = repo.get_post_analytics(message_id=message_id)
        if not data:
            return JSONResponse({"error": "not_found", "message": "Post not found"}, status_code=404)
        return JSONResponse(_normalize_timestamps_in_payload(data))

    @app.get("/api/reactions/post/{message_id}", response_class=JSONResponse)
    async def api_post_reactions(message_id: int) -> JSONResponse:
        metric = repo.get_latest_post_metric(message_id=message_id)
        if not metric:
            return JSONResponse(
                {"error": "not_found", "message": "No Bot API metrics for this post"},
                status_code=404,
            )
        reactions = metric.get("reactions") or {}
        rows = [
            {"reaction_description": describe_reaction_key(str(reaction)), "count": int(count)}
            for reaction, count in sorted(reactions.items(), key=lambda item: item[1], reverse=True)
        ]
        return JSONResponse(rows)

    @app.get("/api/contacts", response_class=JSONResponse)
    async def api_contacts() -> JSONResponse:
        contacts = repo.get_contacts_cache()
        if not contacts:
            repo.rebuild_contacts_cache(
                posts_limit=settings.contacts_posts_limit,
                commenters_limit=settings.contacts_commenters_limit,
            )
            contacts = repo.get_contacts_cache()
        return JSONResponse(_normalize_timestamps_in_payload(contacts))

    @app.get("/api/commenters/channel", response_class=JSONResponse)
    async def api_channel_commenters(limit: int = Query(default=3000, ge=1, le=50000)) -> JSONResponse:
        return JSONResponse(repo.get_channel_unique_commenters(limit=limit))

    @app.get("/api/user-metrics", response_class=JSONResponse)
    async def api_user_metrics(limit: int = Query(default=5000, ge=1, le=50000)) -> JSONResponse:
        rows = repo.get_user_metrics_report(limit=limit)
        return JSONResponse(_normalize_timestamps_in_payload(rows))

    @app.get("/api/commenters/post/{message_id}", response_class=JSONResponse)
    async def api_post_commenters(message_id: int, limit: int = Query(default=3000, ge=1, le=50000)) -> JSONResponse:
        return JSONResponse(repo.get_post_commenters(message_id=message_id, limit=limit))

    @app.get("/api/reactors/post/{message_id}", response_class=JSONResponse)
    async def api_post_reactors(message_id: int, limit: int = Query(default=2000, ge=1, le=10000)) -> JSONResponse:
        _ = (message_id, limit)
        return JSONResponse(
            {
                "error": "bot_api_limitation",
                "message": "Telegram Bot API не предоставляет список пользователей, оставивших реакции у поста.",
            },
            status_code=501,
        )

    @app.get("/api/export/commenters/channel.csv")
    async def api_export_channel_commenters_csv(limit: int = Query(default=3000, ge=1, le=50000)) -> Response:
        rows = repo.get_channel_unique_commenters(limit=limit)
        csv_rows = [{"nickname": row["nickname"], "comments_count": row["comments_count"]} for row in rows]
        return _csv_response(
            filename="channel_unique_commenters.csv",
            rows=csv_rows,
            headers=["nickname", "comments_count"],
        )

    @app.get("/api/export/user-metrics.csv")
    async def api_export_user_metrics_csv(limit: int = Query(default=5000, ge=1, le=50000)) -> Response:
        rows = repo.get_user_metrics_report(limit=limit)
        return _csv_response(
            filename="user_metrics.csv",
            rows=rows,
            headers=[
                "user_id",
                "nickname",
                "username",
                "first_name",
                "last_name",
                "profile_url",
                "comments_count",
                "posts_commented_count",
                "linked_chats_count",
                "first_comment_at",
                "last_comment_at",
                "last_activity",
                "comments_with_contact",
                "comments_with_intent",
                "contacts_rank",
            ],
        )

    @app.get("/api/export/commenters/post/{message_id}.csv")
    async def api_export_post_commenters_csv(message_id: int, limit: int = Query(default=3000, ge=1, le=50000)) -> Response:
        rows = repo.get_post_commenters(message_id=message_id, limit=limit)
        csv_rows = [{"nickname": row["nickname"], "comments_count": row["comments_count"]} for row in rows]
        return _csv_response(
            filename=f"post_{message_id}_commenters.csv",
            rows=csv_rows,
            headers=["nickname", "comments_count"],
        )

    @app.get("/api/export/reactions/post/{message_id}.csv")
    async def api_export_post_reactions_csv(message_id: int) -> Response:
        metric = repo.get_latest_post_metric(message_id=message_id)
        if not metric:
            return JSONResponse(
                {"error": "not_found", "message": "No Bot API metrics for this post"},
                status_code=404,
            )
        reactions = metric.get("reactions") or {}
        csv_rows = [
            {"reaction_description": describe_reaction_key(str(reaction)), "count": int(count)}
            for reaction, count in sorted(reactions.items(), key=lambda item: item[1], reverse=True)
        ]
        return _csv_response(
            filename=f"post_{message_id}_reactions.csv",
            rows=csv_rows,
            headers=["reaction_description", "count"],
        )

    @app.get("/api/export/reactors/post/{message_id}.csv")
    async def api_export_post_reactors_csv(message_id: int, limit: int = Query(default=2000, ge=1, le=10000)) -> Response:
        _ = (message_id, limit)
        return JSONResponse(
            {
                "error": "bot_api_limitation",
                "message": "Telegram Bot API не предоставляет список пользователей, оставивших реакции у поста.",
            },
            status_code=501,
        )

    @app.get("/chart/comments.png")
    async def chart_comments(days: int = Query(default=settings.chart_default_days, ge=1, le=365)) -> Response:
        trend = repo.get_comments_trend(days=days)
        png = render_comments_trend_png(trend_rows=trend, days=days)
        return Response(content=png, media_type="image/png")

    @app.get("/chart/views.png")
    async def chart_views(days: int = Query(default=settings.chart_default_days, ge=1, le=365)) -> Response:
        trend = repo.get_channel_metric_trend(metric="views", days=days)
        png = render_metric_trend_png(
            trend_rows=trend,
            days=days,
            title="Views Trend",
            y_label="Views",
            line_color="#0d9488",
            fill_color="#cffafe",
        )
        return Response(content=png, media_type="image/png")

    @app.get("/chart/reactions.png")
    async def chart_reactions(days: int = Query(default=settings.chart_default_days, ge=1, le=365)) -> Response:
        trend = repo.get_channel_metric_trend(metric="reactions", days=days)
        png = render_metric_trend_png(
            trend_rows=trend,
            days=days,
            title="Reactions Trend",
            y_label="Reactions",
            line_color="#7c3aed",
            fill_color="#ede9fe",
        )
        return Response(content=png, media_type="image/png")

    @app.get("/chart/forwards.png")
    async def chart_forwards(days: int = Query(default=settings.chart_default_days, ge=1, le=365)) -> Response:
        trend = repo.get_channel_metric_trend(metric="forwards", days=days)
        png = render_metric_trend_png(
            trend_rows=trend,
            days=days,
            title="Forwards Trend",
            y_label="Forwards",
            line_color="#dc2626",
            fill_color="#fecaca",
        )
        return Response(content=png, media_type="image/png")

    return app
