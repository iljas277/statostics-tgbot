from __future__ import annotations

import sqlite3
from pathlib import Path

from app.db import Database


def test_init_schema_migrates_legacy_leads_and_stats_snapshot_schema(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE leads (
            user_id INTEGER PRIMARY KEY,
            score INTEGER NOT NULL DEFAULT 0,
            tags TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE stats_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_at TEXT NOT NULL,
            period_hours INTEGER NOT NULL,
            posts_count INTEGER NOT NULL,
            comments_count INTEGER NOT NULL,
            unique_commenters INTEGER NOT NULL,
            leads_count INTEGER NOT NULL
        );

        INSERT INTO stats_snapshots(snapshot_at, period_hours, posts_count, comments_count, unique_commenters, leads_count)
        VALUES ('2026-01-01T00:00:00+00:00', 24, 10, 20, 5, 3);
        """
    )
    conn.commit()
    conn.close()

    db = Database(db_path)
    db.init_schema()

    cols = db.fetchall("PRAGMA table_info(stats_snapshots)")
    names = [row["name"] for row in cols]
    assert "leads_count" not in names
    assert names == [
        "id",
        "snapshot_at",
        "period_hours",
        "posts_count",
        "comments_count",
        "unique_commenters",
    ]

    leads_exists = db.fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='leads'")
    assert leads_exists is None

    row = db.fetchone("SELECT posts_count, comments_count, unique_commenters FROM stats_snapshots LIMIT 1")
    assert row is not None
    assert int(row["posts_count"]) == 10
    assert int(row["comments_count"]) == 20
    assert int(row["unique_commenters"]) == 5

    db.close()
