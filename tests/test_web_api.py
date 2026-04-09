from __future__ import annotations

from pathlib import Path
import re

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import Database
from app.repositories import BotRepository
from app.web import create_web_app


def _settings(db_path: Path) -> Settings:
    return Settings(
        bot_token="test-token",
        channel_id=-100123,
        linked_chat_id=None,
        admin_ids={1},
        db_path=db_path,
        log_level="INFO",
        tz="UTC",
        contacts_posts_limit=14,
        contacts_commenters_limit=10,
        contacts_refresh_hour=9,
        contacts_refresh_minute=0,
        web_host="127.0.0.1",
        web_port=8080,
        web_reload=False,
        chart_default_days=14,
        telegram_proxy_url=None,
        mtproto_metrics_posts_limit=50,
        run_startup_tests=False,
    )


def _seed(db_path: Path) -> None:
    db = Database(db_path)
    db.init_schema()
    repo = BotRepository(db)

    repo.upsert_post(channel_id=-100123, message_id=10, text="hello")
    repo.upsert_user(user_id=1, username="alice", first_name="Alice", last_name=None)
    repo.save_comment(1001, 10, -2001, 1, "first", False, True)
    repo.save_post_metrics_snapshots(
        rows=[
            {
                "message_id": 10,
                "post_date": "2026-01-02T00:00:00+00:00",
                "views": 100,
                "forwards": 2,
                "reactions_total": 5,
                "reactions_json": '{"👍": 3, "🔥": 2}',
            }
        ],
        snapshot_at="2026-01-02T10:00:00+00:00",
    )
    db.close()


def test_summary_and_stats_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    summary = client.get("/api/summary")
    assert summary.status_code == 200
    data = summary.json()
    assert data["posts"] == 1
    assert data["comments"] == 1

    stats = client.get("/api/stats", params={"hours": 24})
    assert stats.status_code == 200
    stats_data = stats.json()
    assert stats_data["posts_count"] >= 1
    assert stats_data["comments_count"] >= 1

    tgstats = client.get("/api/tgstats")
    assert tgstats.status_code == 200
    tgstats_data = tgstats.json()
    assert "unique_views_total" in tgstats_data
    assert "unique_views_avg_per_post" in tgstats_data


def test_docs_available_at_app_docs(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    docs = client.get("/app/docs")
    assert docs.status_code == 200
    assert "Swagger UI" in docs.text


def test_timestamp_fields_are_human_readable_in_json_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    top_posts = client.get("/api/top-posts")
    assert top_posts.status_code == 200
    top_post = top_posts.json()[0]
    assert re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", top_post["created_at"])

    metrics = client.get("/api/user-metrics")
    assert metrics.status_code == 200
    row = metrics.json()[0]
    assert re.fullmatch(r"\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}", row["first_comment_at"])


def test_user_metrics_and_csv_exports(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    resp = client.get("/api/user-metrics")
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["nickname"] == "@alice"

    csv_resp = client.get("/api/export/user-metrics.csv")
    assert csv_resp.status_code == 200
    assert "text/csv" in csv_resp.headers["content-type"]
    assert "nickname" in csv_resp.text


def test_post_reactions_json_and_csv(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    reactions = client.get("/api/reactions/post/10")
    assert reactions.status_code == 200
    payload = reactions.json()
    assert payload[0]["reaction_description"].startswith("Positive emoji") or payload[0]["reaction_description"].startswith("Emoji")

    csv_resp = client.get("/api/export/reactions/post/10.csv")
    assert csv_resp.status_code == 200
    assert "reaction_description" in csv_resp.text
