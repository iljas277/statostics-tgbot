from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from app.charts import render_comments_trend_png, render_metric_trend_png
from app.config import Settings
from app.db import Database
from app.repositories import BotRepository
from app.telegram_api import TelegramApiMetricsService


def _csv_response(filename: str, rows: list[dict], headers: list[str]) -> Response:
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers)
    writer.writeheader()
    for row in rows:
        writer.writerow({key: row.get(key, "") for key in headers})
    content = buf.getvalue()
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def create_web_app(settings: Settings) -> FastAPI:
    db = Database(settings.db_path)
    db.init_schema()
    repo = BotRepository(db)

    app = FastAPI(title="Telegram Analytics Panel", version="1.0.0")
    templates = Jinja2Templates(directory=str(Path("web/templates")))

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        summary = repo.get_dashboard_summary()
        top_posts = repo.get_posts_with_comment_counts(limit=10)
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
                "leads_count": stats.leads_count,
            }
        )

    @app.get("/api/tgstats", response_class=JSONResponse)
    async def api_tgstats(posts_limit: int = Query(default=settings.mtproto_metrics_posts_limit, ge=1, le=200)) -> JSONResponse:
        metrics = repo.get_latest_channel_metric_stats(limit_posts=posts_limit)
        return JSONResponse(
            {
                "posts_sampled": metrics.posts_sampled,
                "total_views": metrics.total_views,
                "total_reactions": metrics.total_reactions,
                "total_forwards": metrics.total_forwards,
                "avg_views_per_post": metrics.avg_views_per_post,
            }
        )

    @app.get("/api/top-posts", response_class=JSONResponse)
    async def api_top_posts(limit: int = Query(default=20, ge=1, le=100)) -> JSONResponse:
        return JSONResponse(repo.get_posts_with_comment_counts(limit=limit))

    @app.get("/api/poststats/{message_id}", response_class=JSONResponse)
    async def api_poststats(message_id: int) -> JSONResponse:
        data = repo.get_post_analytics(message_id=message_id)
        if not data:
            return JSONResponse({"error": "not_found", "message": "Post not found"}, status_code=404)
        return JSONResponse(data)

    @app.get("/api/contacts", response_class=JSONResponse)
    async def api_contacts() -> JSONResponse:
        contacts = repo.get_contacts_cache()
        if not contacts:
            repo.rebuild_contacts_cache(
                posts_limit=settings.contacts_posts_limit,
                commenters_limit=settings.contacts_commenters_limit,
            )
            contacts = repo.get_contacts_cache()
        return JSONResponse(contacts)

    @app.get("/api/commenters/channel", response_class=JSONResponse)
    async def api_channel_commenters(limit: int = Query(default=5000, ge=1, le=50000)) -> JSONResponse:
        return JSONResponse(repo.get_channel_unique_commenters(limit=limit))

    @app.get("/api/commenters/post/{message_id}", response_class=JSONResponse)
    async def api_post_commenters(message_id: int, limit: int = Query(default=5000, ge=1, le=50000)) -> JSONResponse:
        return JSONResponse(repo.get_post_commenters(message_id=message_id, limit=limit))

    @app.get("/api/reactors/post/{message_id}", response_class=JSONResponse)
    async def api_post_reactors(message_id: int, limit: int = Query(default=2000, ge=1, le=10000)) -> JSONResponse:
        mtproto = TelegramApiMetricsService(settings=settings)
        if not mtproto.enabled:
            return JSONResponse(
                {"error": "mtproto_disabled", "message": "Set TELEGRAM_API_ID and TELEGRAM_API_HASH"},
                status_code=400,
            )
        reactors = await mtproto.fetch_post_reactors(message_id=message_id, limit=limit)
        return JSONResponse(
            [
                {
                    "user_id": item.user_id,
                    "nickname": item.nickname,
                    "username": item.username,
                    "reactions_count": item.reactions_count,
                    "positive_count": item.positive_count,
                    "negative_count": item.negative_count,
                }
                for item in reactors
            ]
        )

    @app.get("/api/export/commenters/channel.csv")
    async def api_export_channel_commenters_csv(limit: int = Query(default=5000, ge=1, le=50000)) -> Response:
        rows = repo.get_channel_unique_commenters(limit=limit)
        csv_rows = [{"nickname": row["nickname"], "comments_count": row["comments_count"]} for row in rows]
        return _csv_response(
            filename="channel_unique_commenters.csv",
            rows=csv_rows,
            headers=["nickname", "comments_count"],
        )

    @app.get("/api/export/commenters/post/{message_id}.csv")
    async def api_export_post_commenters_csv(message_id: int, limit: int = Query(default=5000, ge=1, le=50000)) -> Response:
        rows = repo.get_post_commenters(message_id=message_id, limit=limit)
        csv_rows = [{"nickname": row["nickname"], "comments_count": row["comments_count"]} for row in rows]
        return _csv_response(
            filename=f"post_{message_id}_commenters.csv",
            rows=csv_rows,
            headers=["nickname", "comments_count"],
        )

    @app.get("/api/export/reactors/post/{message_id}.csv")
    async def api_export_post_reactors_csv(message_id: int, limit: int = Query(default=2000, ge=1, le=10000)) -> Response:
        mtproto = TelegramApiMetricsService(settings=settings)
        if not mtproto.enabled:
            return JSONResponse(
                {"error": "mtproto_disabled", "message": "Set TELEGRAM_API_ID and TELEGRAM_API_HASH"},
                status_code=400,
            )
        reactors = await mtproto.fetch_post_reactors(message_id=message_id, limit=limit)
        csv_rows = [
            {
                "nickname": item.nickname,
                "reactions_count": item.reactions_count,
                "positive_count": item.positive_count,
                "negative_count": item.negative_count,
            }
            for item in reactors
        ]
        return _csv_response(
            filename=f"post_{message_id}_reactors.csv",
            rows=csv_rows,
            headers=["nickname", "reactions_count", "positive_count", "negative_count"],
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
