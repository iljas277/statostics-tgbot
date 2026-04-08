from __future__ import annotations

from pathlib import Path
from collections.abc import Iterator

import pytest

from app.db import Database
from app.repositories import BotRepository


@pytest.fixture()
def repo(tmp_path: Path) -> Iterator[BotRepository]:
    db = Database(tmp_path / "test.sqlite3")
    db.init_schema()
    repository = BotRepository(db)
    try:
        yield repository
    finally:
        db.close()


def test_latest_channel_metric_stats_uses_latest_snapshot_per_post(repo: BotRepository) -> None:
    repo.save_post_metrics_snapshots(
        rows=[
            {"message_id": 10, "views": 100, "forwards": 4, "reactions_total": 8, "reactions_json": "{}"},
            {"message_id": 11, "views": 50, "forwards": 1, "reactions_total": 2, "reactions_json": "{}"},
        ],
        snapshot_at="2026-01-01T00:00:00+00:00",
    )
    repo.save_post_metrics_snapshots(
        rows=[
            {"message_id": 10, "views": 150, "forwards": 7, "reactions_total": 10, "reactions_json": "{}"},
        ],
        snapshot_at="2026-01-02T00:00:00+00:00",
    )

    stats = repo.get_latest_channel_metric_stats(limit_posts=10)
    assert stats.posts_sampled == 2
    assert stats.total_views == 200
    assert stats.total_forwards == 8
    assert stats.total_reactions == 12


def test_post_analytics_includes_latest_metric(repo: BotRepository) -> None:
    repo.upsert_post(channel_id=-1001, message_id=42, text="hello")
    repo.save_post_metrics_snapshots(
        rows=[
            {
                "message_id": 42,
                "post_date": "2026-01-02T00:00:00+00:00",
                "views": 99,
                "forwards": 3,
                "reactions_total": 5,
                "reactions_json": '{"🔥": 3, "👍": 2}',
            }
        ],
        snapshot_at="2026-01-03T00:00:00+00:00",
    )
    analytics = repo.get_post_analytics(42)
    assert analytics is not None
    assert analytics["latest_metric"]["views"] == 99
    assert analytics["latest_metric"]["reactions"]["🔥"] == 3


def test_get_channel_unique_commenters_sorted_by_comments_count(repo: BotRepository) -> None:
    repo.upsert_user(user_id=1, username="alice", first_name="Alice", last_name=None)
    repo.upsert_user(user_id=2, username="bob", first_name="Bob", last_name=None)
    repo.upsert_user(user_id=3, username=None, first_name="Carol", last_name=None)

    repo.save_comment(1001, 10, -2001, 1, "one", False, False)
    repo.save_comment(1002, 10, -2001, 1, "two", False, False)
    repo.save_comment(1003, 11, -2001, 2, "hello", False, False)
    repo.save_comment(1004, 11, -2001, 3, "comment", False, False)
    repo.save_comment(1005, 11, -2001, 3, "comment2", False, False)
    repo.save_comment(1006, 12, -2001, 3, "comment3", False, False)

    rows = repo.get_channel_unique_commenters(limit=10)
    assert len(rows) == 3
    assert rows[0]["nickname"] == "Carol"
    assert rows[0]["comments_count"] == 3
    assert rows[1]["nickname"] == "@alice"
    assert rows[1]["comments_count"] == 2
    assert rows[2]["nickname"] == "@bob"
    assert rows[2]["comments_count"] == 1


def test_get_post_commenters_returns_only_given_post(repo: BotRepository) -> None:
    repo.upsert_user(user_id=1, username="alice", first_name="Alice", last_name=None)
    repo.upsert_user(user_id=2, username=None, first_name="Bob", last_name=None)

    repo.save_comment(2001, 42, -2001, 1, "one", False, False)
    repo.save_comment(2002, 42, -2001, 1, "two", False, False)
    repo.save_comment(2003, 42, -2001, 2, "x", False, False)
    repo.save_comment(2004, 43, -2001, 2, "ignored", False, False)

    rows = repo.get_post_commenters(message_id=42, limit=10)
    assert len(rows) == 2
    assert rows[0]["nickname"] == "@alice"
    assert rows[0]["comments_count"] == 2
    assert rows[1]["nickname"] == "Bob"
    assert rows[1]["comments_count"] == 1


def test_get_user_metrics_report_contains_combined_user_metrics(repo: BotRepository) -> None:
    repo.upsert_user(user_id=1, username="alice", first_name="Alice", last_name=None)
    repo.upsert_user(user_id=2, username=None, first_name="Bob", last_name="Stone")

    repo.upsert_post(channel_id=-1001, message_id=101, text="p1")
    repo.upsert_post(channel_id=-1001, message_id=102, text="p2")

    repo.save_comment(3001, 101, -2001, 1, "contact me", True, True)
    repo.save_comment(3002, 101, -2001, 1, "second", False, True)
    repo.save_comment(3003, 102, -2002, 1, "third", False, False)
    repo.save_comment(3004, 102, -2002, 2, "hello", False, False)

    repo.rebuild_contacts_cache(posts_limit=10, commenters_limit=10)

    rows = repo.get_user_metrics_report(limit=10)
    assert len(rows) == 2

    top = rows[0]
    assert top["user_id"] == 1
    assert top["nickname"] == "@alice"
    assert top["comments_count"] == 3
    assert top["posts_commented_count"] == 2
    assert top["linked_chats_count"] == 2
    assert top["comments_with_contact"] == 1
    assert top["comments_with_intent"] == 2
    assert top["first_comment_at"] is not None
    assert top["last_comment_at"] is not None
    assert top["contacts_rank"] is not None


def test_get_channel_metric_trend_uses_latest_snapshot_per_post_per_day(repo: BotRepository) -> None:
    repo.save_post_metrics_snapshots(
        rows=[
            {"message_id": 10, "views": 10, "forwards": 1, "reactions_total": 2, "reactions_json": "{}"},
            {"message_id": 11, "views": 5, "forwards": 1, "reactions_total": 1, "reactions_json": "{}"},
        ],
        snapshot_at="2026-01-01T10:00:00+00:00",
    )
    repo.save_post_metrics_snapshots(
        rows=[
            {"message_id": 10, "views": 20, "forwards": 2, "reactions_total": 3, "reactions_json": "{}"},
            {"message_id": 11, "views": 8, "forwards": 1, "reactions_total": 1, "reactions_json": "{}"},
        ],
        snapshot_at="2026-01-01T18:00:00+00:00",
    )

    trend = repo.get_channel_metric_trend(metric="views", days=365)
    assert len(trend) >= 1
    # On day 2026-01-01 latest values per message are 20 and 8 => 28 total.
    day_row = next((r for r in trend if r["day"] == "2026-01-01"), None)
    assert day_row is not None
    assert int(day_row["value"]) == 28


def test_dashboard_summary_uses_average_unique_views(repo: BotRepository) -> None:
    repo.save_post_metrics_snapshots(
        rows=[
            {"message_id": 10, "views": 120, "forwards": 1, "reactions_total": 3, "reactions_json": "{}"},
            {"message_id": 11, "views": 80, "forwards": 2, "reactions_total": 5, "reactions_json": "{}"},
        ],
        snapshot_at="2026-01-01T10:00:00+00:00",
    )
    summary = repo.get_dashboard_summary()
    assert summary["posts_sampled"] == 2
    assert summary["unique_views_avg_per_post"] == 100.0
    # Backward-compatible key should carry the same meaning/value.
    assert summary["views_total"] == 100.0
