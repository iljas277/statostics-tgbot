from __future__ import annotations

from pathlib import Path

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
        telegram_api_id=None,
        telegram_api_hash=None,
        telegram_api_session="data/telethon.session",
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


def test_dashboard_and_openapi_routes(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    root = client.get("/")
    assert root.status_code == 200
    assert "Telegram Analytics Panel" in root.text
    assert "/api/export/commenters/channel.csv" in root.text

    openapi = client.get("/app/openapi.json")
    assert openapi.status_code == 200
    schema = openapi.json()
    assert "/api/posts" in schema["paths"]


def test_not_found_endpoints_return_404(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    poststats = client.get("/api/poststats/999999")
    assert poststats.status_code == 404
    assert poststats.json()["error"] == "not_found"

    reactions = client.get("/api/reactions/post/999999")
    assert reactions.status_code == 404
    assert reactions.json()["error"] == "not_found"


def test_mtproto_disabled_reactors_endpoints(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    json_resp = client.get("/api/reactors/post/10")
    assert json_resp.status_code == 400
    assert json_resp.json()["error"] == "mtproto_disabled"

    csv_resp = client.get("/api/export/reactors/post/10.csv")
    assert csv_resp.status_code == 400
    assert csv_resp.json()["error"] == "mtproto_disabled"


def test_export_endpoints_return_csv(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    channel_csv = client.get("/api/export/commenters/channel.csv")
    assert channel_csv.status_code == 200
    assert "attachment; filename=\"channel_unique_commenters.csv\"" in channel_csv.headers["content-disposition"]
    assert "nickname,comments_count" in channel_csv.text

    user_csv = client.get("/api/export/user-metrics.csv")
    assert user_csv.status_code == 200
    assert "attachment; filename=\"user_metrics.csv\"" in user_csv.headers["content-disposition"]
    assert "first_comment_at" in user_csv.text

    post_commenters_csv = client.get("/api/export/commenters/post/10.csv")
    assert post_commenters_csv.status_code == 200
    assert "attachment; filename=\"post_10_commenters.csv\"" in post_commenters_csv.headers["content-disposition"]

    reactions_csv = client.get("/api/export/reactions/post/10.csv")
    assert reactions_csv.status_code == 200
    assert "attachment; filename=\"post_10_reactions.csv\"" in reactions_csv.headers["content-disposition"]
    assert "reaction_description,count" in reactions_csv.text


def test_chart_endpoints_return_png(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    for path in ["/chart/comments.png", "/chart/views.png", "/chart/reactions.png", "/chart/forwards.png"]:
        resp = client.get(path, params={"days": 14})
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("image/png")
        assert resp.content.startswith(b"\x89PNG")


def test_endpoint_validation_bounds(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    _seed(db_path)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    bad_hours = client.get("/api/stats", params={"hours": 0})
    assert bad_hours.status_code == 422

    bad_limit = client.get("/api/commenters/channel", params={"limit": 0})
    assert bad_limit.status_code == 422

    bad_days = client.get("/chart/comments.png", params={"days": 0})
    assert bad_days.status_code == 422


def test_empty_contacts_endpoint_returns_list(tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    db = Database(db_path)
    db.init_schema()
    db.close()

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    contacts = client.get("/api/contacts")
    assert contacts.status_code == 200
    assert contacts.json() == []
