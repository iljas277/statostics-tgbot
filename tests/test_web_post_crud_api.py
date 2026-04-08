from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

import app.web as web_module
from app.config import Settings
from app.db import Database
from app.web import create_web_app


class _FakeBot:
    def __init__(self, *args, **kwargs) -> None:
        self._sent_id = 777

    async def initialize(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    async def send_message(self, chat_id: int, text: str) -> SimpleNamespace:
        return SimpleNamespace(message_id=self._sent_id)

    async def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        return None

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        return None


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


def test_api_post_edit_delete_flow(monkeypatch, tmp_path: Path) -> None:
    db_path = tmp_path / "web.sqlite3"
    db = Database(db_path)
    db.init_schema()
    db.close()

    monkeypatch.setattr(web_module, "Bot", _FakeBot)

    app = create_web_app(_settings(db_path))
    client = TestClient(app)

    create_resp = client.post("/api/posts", json={"text": "hello"})
    assert create_resp.status_code == 200
    message_id = create_resp.json()["message_id"]
    assert message_id == 777

    patch_resp = client.patch(f"/api/posts/{message_id}", json={"text": "edited"})
    assert patch_resp.status_code == 200
    assert patch_resp.json()["ok"] is True

    delete_resp = client.delete(f"/api/posts/{message_id}")
    assert delete_resp.status_code == 200
    assert delete_resp.json()["ok"] is True

    db = Database(db_path)
    row = db.fetchone("SELECT message_id, deleted_at FROM posts WHERE message_id = ?", (message_id,))
    assert row is not None
    assert row["deleted_at"] is not None
    db.close()
