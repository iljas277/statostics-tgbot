from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from app.db import Database
from app.repositories import BotRepository


class MetricsRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "test.sqlite3"
        self.db = Database(db_path)
        self.db.init_schema()
        self.repo = BotRepository(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_latest_channel_metric_stats_uses_latest_snapshot_per_post(self) -> None:
        self.repo.save_post_metrics_snapshots(
            rows=[
                {"message_id": 10, "views": 100, "forwards": 4, "reactions_total": 8, "reactions_json": "{}"},
                {"message_id": 11, "views": 50, "forwards": 1, "reactions_total": 2, "reactions_json": "{}"},
            ],
            snapshot_at="2026-01-01T00:00:00+00:00",
        )
        self.repo.save_post_metrics_snapshots(
            rows=[
                {"message_id": 10, "views": 150, "forwards": 7, "reactions_total": 10, "reactions_json": "{}"},
            ],
            snapshot_at="2026-01-02T00:00:00+00:00",
        )

        stats = self.repo.get_latest_channel_metric_stats(limit_posts=10)
        self.assertEqual(stats.posts_sampled, 2)
        self.assertEqual(stats.total_views, 200)
        self.assertEqual(stats.total_forwards, 8)
        self.assertEqual(stats.total_reactions, 12)

    def test_post_analytics_includes_latest_metric(self) -> None:
        self.repo.upsert_post(channel_id=-1001, message_id=42, text="hello")
        self.repo.save_post_metrics_snapshots(
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
        analytics = self.repo.get_post_analytics(42)
        self.assertIsNotNone(analytics)
        self.assertEqual(analytics["latest_metric"]["views"], 99)
        self.assertEqual(analytics["latest_metric"]["reactions"]["🔥"], 3)

    def test_get_channel_unique_commenters_sorted_by_comments_count(self) -> None:
        self.repo.upsert_user(user_id=1, username="alice", first_name="Alice", last_name=None)
        self.repo.upsert_user(user_id=2, username="bob", first_name="Bob", last_name=None)
        self.repo.upsert_user(user_id=3, username=None, first_name="Carol", last_name=None)

        self.repo.save_comment(1001, 10, -2001, 1, "one", False, False)
        self.repo.save_comment(1002, 10, -2001, 1, "two", False, False)
        self.repo.save_comment(1003, 11, -2001, 2, "hello", False, False)
        self.repo.save_comment(1004, 11, -2001, 3, "comment", False, False)
        self.repo.save_comment(1005, 11, -2001, 3, "comment2", False, False)
        self.repo.save_comment(1006, 12, -2001, 3, "comment3", False, False)

        rows = self.repo.get_channel_unique_commenters(limit=10)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["nickname"], "Carol")
        self.assertEqual(rows[0]["comments_count"], 3)
        self.assertEqual(rows[1]["nickname"], "@alice")
        self.assertEqual(rows[1]["comments_count"], 2)
        self.assertEqual(rows[2]["nickname"], "@bob")
        self.assertEqual(rows[2]["comments_count"], 1)

    def test_get_post_commenters_returns_only_given_post(self) -> None:
        self.repo.upsert_user(user_id=1, username="alice", first_name="Alice", last_name=None)
        self.repo.upsert_user(user_id=2, username=None, first_name="Bob", last_name=None)

        self.repo.save_comment(2001, 42, -2001, 1, "one", False, False)
        self.repo.save_comment(2002, 42, -2001, 1, "two", False, False)
        self.repo.save_comment(2003, 42, -2001, 2, "x", False, False)
        self.repo.save_comment(2004, 43, -2001, 2, "ignored", False, False)

        rows = self.repo.get_post_commenters(message_id=42, limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["nickname"], "@alice")
        self.assertEqual(rows[0]["comments_count"], 2)
        self.assertEqual(rows[1]["nickname"], "Bob")
        self.assertEqual(rows[1]["comments_count"], 1)


if __name__ == "__main__":
    unittest.main()
