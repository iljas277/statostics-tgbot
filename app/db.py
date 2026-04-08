from __future__ import annotations

from pathlib import Path
import sqlite3
from threading import Lock


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS posts (
    message_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    text TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    comment_count INTEGER NOT NULL DEFAULT 0,
    last_activity TEXT
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_message_id INTEGER NOT NULL UNIQUE,
    channel_post_id INTEGER,
    linked_chat_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    text TEXT,
    created_at TEXT NOT NULL,
    has_contact INTEGER NOT NULL DEFAULT 0,
    has_intent INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS discussion_map (
    linked_chat_id INTEGER NOT NULL,
    root_group_message_id INTEGER NOT NULL,
    channel_post_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(linked_chat_id, root_group_message_id)
);

CREATE TABLE IF NOT EXISTS stats_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_at TEXT NOT NULL,
    period_hours INTEGER NOT NULL,
    posts_count INTEGER NOT NULL,
    comments_count INTEGER NOT NULL,
    unique_commenters INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operator_user_id INTEGER NOT NULL,
    action_type TEXT NOT NULL,
    payload TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts_cache (
    rank_pos INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    nickname TEXT NOT NULL,
    profile_url TEXT NOT NULL,
    comments_count INTEGER NOT NULL,
    refreshed_at TEXT NOT NULL,
    PRIMARY KEY(rank_pos)
);

CREATE TABLE IF NOT EXISTS post_metrics_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id INTEGER NOT NULL,
    post_date TEXT,
    snapshot_at TEXT NOT NULL,
    views INTEGER NOT NULL DEFAULT 0,
    forwards INTEGER NOT NULL DEFAULT 0,
    reactions_total INTEGER NOT NULL DEFAULT 0,
    reactions_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_comments_channel_post_id ON comments(channel_post_id);
CREATE INDEX IF NOT EXISTS idx_comments_user_id ON comments(user_id);
CREATE INDEX IF NOT EXISTS idx_comments_created_at ON comments(created_at);
CREATE INDEX IF NOT EXISTS idx_users_last_activity ON users(last_activity);
CREATE INDEX IF NOT EXISTS idx_bot_actions_created_at ON bot_actions(created_at);
CREATE INDEX IF NOT EXISTS idx_contacts_cache_user_id ON contacts_cache(user_id);
CREATE INDEX IF NOT EXISTS idx_post_metrics_message_snapshot ON post_metrics_snapshots(message_id, snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_post_metrics_snapshot_at ON post_metrics_snapshots(snapshot_at DESC);
"""


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

    def init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(SCHEMA_SQL)
            self._run_migrations()
            self._conn.commit()

    def _run_migrations(self) -> None:
        # Migration 1: drop obsolete leads table if present.
        self._conn.execute("DROP TABLE IF EXISTS leads")

        # Migration 2: rebuild stats_snapshots without deprecated leads_count column.
        cols = self._conn.execute("PRAGMA table_info(stats_snapshots)").fetchall()
        col_names = {str(row[1]) for row in cols}
        if "leads_count" not in col_names:
            return

        self._conn.execute("ALTER TABLE stats_snapshots RENAME TO stats_snapshots_old")
        self._conn.execute(
            """
            CREATE TABLE stats_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_at TEXT NOT NULL,
                period_hours INTEGER NOT NULL,
                posts_count INTEGER NOT NULL,
                comments_count INTEGER NOT NULL,
                unique_commenters INTEGER NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            INSERT INTO stats_snapshots(id, snapshot_at, period_hours, posts_count, comments_count, unique_commenters)
            SELECT id, snapshot_at, period_hours, posts_count, comments_count, unique_commenters
            FROM stats_snapshots_old
            """
        )
        self._conn.execute("DROP TABLE stats_snapshots_old")

    def execute(self, query: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cur = self._conn.execute(query, params)
            self._conn.commit()
            return cur

    def fetchone(self, query: str, params: tuple = ()) -> sqlite3.Row | None:
        with self._lock:
            cur = self._conn.execute(query, params)
            return cur.fetchone()

    def fetchall(self, query: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            cur = self._conn.execute(query, params)
            return list(cur.fetchall())

    def close(self) -> None:
        with self._lock:
            self._conn.close()
