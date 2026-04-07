from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates

from app.charts import render_comments_trend_png, render_metric_trend_png
from app.config import Settings
from app.db import Database
from app.repositories import BotRepository


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

    @app.get("/api/top-posts", response_class=JSONResponse)
    async def api_top_posts(limit: int = Query(default=20, ge=1, le=100)) -> JSONResponse:
        return JSONResponse(repo.get_posts_with_comment_counts(limit=limit))

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

    return app
