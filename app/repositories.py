from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import sqlite3

from app.db import Database


@dataclass(slots=True)
class AggregateStats:
    posts_count: int
    comments_count: int
    unique_commenters: int
    leads_count: int


class BotRepository:
    def __init__(self, db: Database) -> None:
        self.db = db

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(tz=timezone.utc).isoformat()

    def log_action(self, operator_user_id: int, action_type: str, payload: dict) -> None:
        self.db.execute(
            """
            INSERT INTO bot_actions(operator_user_id, action_type, payload, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (operator_user_id, action_type, json.dumps(payload, ensure_ascii=False), self._now_iso()),
        )

    def upsert_post(self, channel_id: int, message_id: int, text: str | None) -> None:
        self.db.execute(
            """
            INSERT INTO posts(message_id, channel_id, text, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(message_id) DO UPDATE SET
                text=excluded.text,
                updated_at=excluded.created_at
            """,
            (message_id, channel_id, text, self._now_iso()),
        )

    def mark_post_deleted(self, message_id: int) -> None:
        self.db.execute(
            "UPDATE posts SET deleted_at=?, updated_at=? WHERE message_id=?",
            (self._now_iso(), self._now_iso(), message_id),
        )

    def save_discussion_root_map(self, linked_chat_id: int, root_group_message_id: int, channel_post_id: int) -> None:
        self.db.execute(
            """
            INSERT INTO discussion_map(linked_chat_id, root_group_message_id, channel_post_id, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(linked_chat_id, root_group_message_id) DO NOTHING
            """,
            (linked_chat_id, root_group_message_id, channel_post_id, self._now_iso()),
        )

    def get_channel_post_by_discussion_root(self, linked_chat_id: int, root_group_message_id: int) -> int | None:
        row = self.db.fetchone(
            """
            SELECT channel_post_id
            FROM discussion_map
            WHERE linked_chat_id=? AND root_group_message_id=?
            """,
            (linked_chat_id, root_group_message_id),
        )
        return int(row["channel_post_id"]) if row else None

    def upsert_user(self, user_id: int, username: str | None, first_name: str | None, last_name: str | None) -> None:
        self.db.execute(
            """
            INSERT INTO users(user_id, username, first_name, last_name, comment_count, last_activity)
            VALUES (?, ?, ?, ?, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name
            """,
            (user_id, username, first_name, last_name, self._now_iso()),
        )

    def save_comment(
        self,
        group_message_id: int,
        channel_post_id: int | None,
        linked_chat_id: int,
        user_id: int,
        text: str,
        has_contact: bool,
        has_intent: bool,
    ) -> bool:
        """Returns False if comment already exists."""
        try:
            self.db.execute(
                """
                INSERT INTO comments(
                    group_message_id, channel_post_id, linked_chat_id, user_id, text, created_at, has_contact, has_intent
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    group_message_id,
                    channel_post_id,
                    linked_chat_id,
                    user_id,
                    text,
                    self._now_iso(),
                    1 if has_contact else 0,
                    1 if has_intent else 0,
                ),
            )
        except sqlite3.IntegrityError:
            return False

        self.db.execute(
            """
            UPDATE users
            SET comment_count = comment_count + 1,
                last_activity = ?
            WHERE user_id = ?
            """,
            (self._now_iso(), user_id),
        )
        return True

    def upsert_lead(self, user_id: int, score_delta: int, tags: set[str]) -> None:
        row = self.db.fetchone("SELECT score, tags FROM leads WHERE user_id=?", (user_id,))
        now = self._now_iso()

        if not row:
            self.db.execute(
                """
                INSERT INTO leads(user_id, score, tags, status, updated_at)
                VALUES (?, ?, ?, 'new', ?)
                """,
                (user_id, score_delta, ",".join(sorted(tags)), now),
            )
            return

        existing_tags = set(filter(None, (row["tags"] or "").split(",")))
        merged_tags = existing_tags | tags
        self.db.execute(
            """
            UPDATE leads
            SET score=?, tags=?, updated_at=?
            WHERE user_id=?
            """,
            (int(row["score"]) + score_delta, ",".join(sorted(merged_tags)), now, user_id),
        )

    def get_top_contacts(self, limit: int = 10) -> list[dict]:
        rows = self.db.fetchall(
            """
            SELECT u.user_id, u.username, u.first_name, u.last_name, u.comment_count,
                   COALESCE(l.score, 0) AS score,
                   COALESCE(l.tags, '') AS tags,
                   COALESCE(l.status, 'new') AS status
            FROM users u
            LEFT JOIN leads l ON l.user_id = u.user_id
            WHERE u.comment_count > 0
            ORDER BY score DESC, u.comment_count DESC, u.last_activity DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]

    @staticmethod
    def _profile_url_for_user(user_id: int, username: str | None) -> str:
        if username:
            return f"https://t.me/{username}"
        if user_id > 0:
            return f"tg://user?id={user_id}"
        return "недоступно"

    def rebuild_contacts_cache(self, posts_limit: int, commenters_limit: int) -> int:
        rows = self.db.fetchall(
            """
            WITH recent_posts AS (
                SELECT message_id
                FROM posts
                WHERE deleted_at IS NULL
                ORDER BY created_at DESC
                LIMIT ?
            )
            SELECT c.user_id,
                   COUNT(*) AS comments_count,
                   COALESCE(u.username, '') AS username,
                   COALESCE(u.first_name, '') AS first_name,
                   COALESCE(u.last_name, '') AS last_name
            FROM comments c
            LEFT JOIN users u ON u.user_id = c.user_id
            WHERE c.channel_post_id IN (SELECT message_id FROM recent_posts)
            GROUP BY c.user_id
            ORDER BY comments_count DESC, COALESCE(u.last_activity, c.created_at) DESC
            LIMIT ?
            """,
            (posts_limit, commenters_limit),
        )

        refreshed_at = self._now_iso()
        self.db.execute("DELETE FROM contacts_cache")

        rank = 1
        for row in rows:
            user_id = int(row["user_id"])
            username = (row["username"] or "").strip() or None
            first_name = (row["first_name"] or "").strip()
            nickname = f"@{username}" if username else (first_name or str(user_id))
            profile_url = self._profile_url_for_user(user_id=user_id, username=username)
            self.db.execute(
                """
                INSERT INTO contacts_cache(rank_pos, user_id, nickname, profile_url, comments_count, refreshed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (rank, user_id, nickname, profile_url, int(row["comments_count"]), refreshed_at),
            )
            rank += 1

        return len(rows)

    def get_contacts_cache(self) -> list[dict]:
        rows = self.db.fetchall(
            """
            SELECT rank_pos, user_id, nickname, profile_url, comments_count, refreshed_at
            FROM contacts_cache
            ORDER BY rank_pos ASC
            """,
            (),
        )
        return [dict(r) for r in rows]

    def aggregate_stats(self, period_hours: int = 24) -> AggregateStats:
        since = datetime.now(tz=timezone.utc) - timedelta(hours=period_hours)
        since_iso = since.isoformat()

        posts_row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM posts WHERE created_at >= ? AND deleted_at IS NULL",
            (since_iso,),
        )
        comments_row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM comments WHERE created_at >= ?",
            (since_iso,),
        )
        unique_row = self.db.fetchone(
            "SELECT COUNT(DISTINCT user_id) AS c FROM comments WHERE created_at >= ?",
            (since_iso,),
        )
        leads_row = self.db.fetchone("SELECT COUNT(*) AS c FROM leads", ())

        return AggregateStats(
            posts_count=int(posts_row["c"] if posts_row else 0),
            comments_count=int(comments_row["c"] if comments_row else 0),
            unique_commenters=int(unique_row["c"] if unique_row else 0),
            leads_count=int(leads_row["c"] if leads_row else 0),
        )

    def save_snapshot(self, period_hours: int = 24) -> AggregateStats:
        stats = self.aggregate_stats(period_hours=period_hours)
        self.db.execute(
            """
            INSERT INTO stats_snapshots(
                snapshot_at, period_hours, posts_count, comments_count, unique_commenters, leads_count
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                self._now_iso(),
                period_hours,
                stats.posts_count,
                stats.comments_count,
                stats.unique_commenters,
                stats.leads_count,
            ),
        )
        return stats

    def get_ingestion_debug(self) -> dict:
        comments_row = self.db.fetchone("SELECT COUNT(*) AS c FROM comments", ())
        users_row = self.db.fetchone("SELECT COUNT(*) AS c FROM users", ())
        map_row = self.db.fetchone("SELECT COUNT(*) AS c FROM discussion_map", ())
        chats_rows = self.db.fetchall(
            """
            SELECT linked_chat_id, COUNT(*) AS c
            FROM comments
            GROUP BY linked_chat_id
            ORDER BY c DESC
            LIMIT 5
            """,
            (),
        )

        return {
            "comments_count": int(comments_row["c"] if comments_row else 0),
            "users_count": int(users_row["c"] if users_row else 0),
            "discussion_map_count": int(map_row["c"] if map_row else 0),
            "top_linked_chats": [dict(r) for r in chats_rows],
        }

    def get_post_analytics(self, message_id: int) -> dict | None:
        post_row = self.db.fetchone(
            """
            SELECT message_id, channel_id, text, created_at, updated_at, deleted_at
            FROM posts
            WHERE message_id = ?
            """,
            (message_id,),
        )
        if not post_row:
            return None

        comments_row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM comments WHERE channel_post_id = ?",
            (message_id,),
        )
        unique_row = self.db.fetchone(
            "SELECT COUNT(DISTINCT user_id) AS c FROM comments WHERE channel_post_id = ?",
            (message_id,),
        )
        leads_row = self.db.fetchone(
            """
            SELECT COUNT(DISTINCT c.user_id) AS c
            FROM comments c
            JOIN leads l ON l.user_id = c.user_id
            WHERE c.channel_post_id = ?
            """,
            (message_id,),
        )
        last_comment_row = self.db.fetchone(
            "SELECT MAX(created_at) AS ts FROM comments WHERE channel_post_id = ?",
            (message_id,),
        )
        top_rows = self.db.fetchall(
            """
            SELECT c.user_id,
                   COUNT(*) AS comments_count,
                   COALESCE(u.username, u.first_name, CAST(c.user_id AS TEXT)) AS author
            FROM comments c
            LEFT JOIN users u ON u.user_id = c.user_id
            WHERE c.channel_post_id = ?
            GROUP BY c.user_id
            ORDER BY comments_count DESC
            LIMIT 5
            """,
            (message_id,),
        )

        return {
            "post": dict(post_row),
            "comments_count": int(comments_row["c"] if comments_row else 0),
            "unique_commenters": int(unique_row["c"] if unique_row else 0),
            "leads_in_comments": int(leads_row["c"] if leads_row else 0),
            "last_comment_at": last_comment_row["ts"] if last_comment_row else None,
            "top_commenters": [dict(r) for r in top_rows],
        }

    def get_dashboard_summary(self) -> dict:
        posts_row = self.db.fetchone("SELECT COUNT(*) AS c FROM posts WHERE deleted_at IS NULL", ())
        comments_row = self.db.fetchone("SELECT COUNT(*) AS c FROM comments", ())
        unique_row = self.db.fetchone("SELECT COUNT(DISTINCT user_id) AS c FROM comments", ())
        leads_row = self.db.fetchone("SELECT COUNT(*) AS c FROM leads", ())
        return {
            "posts": int(posts_row["c"] if posts_row else 0),
            "comments": int(comments_row["c"] if comments_row else 0),
            "unique_commenters": int(unique_row["c"] if unique_row else 0),
            "leads": int(leads_row["c"] if leads_row else 0),
        }

    def get_comments_trend(self, days: int = 14) -> list[dict]:
        days = max(1, min(365, int(days)))
        rows = self.db.fetchall(
            """
            SELECT strftime('%Y-%m-%d', created_at) AS day,
                   COUNT(*) AS comments
            FROM comments
            WHERE created_at >= datetime('now', ?)
            GROUP BY day
            ORDER BY day ASC
            """,
            (f"-{days} day",),
        )
        return [dict(r) for r in rows]

    def get_posts_with_comment_counts(self, limit: int = 20) -> list[dict]:
        limit = max(1, min(100, int(limit)))
        rows = self.db.fetchall(
            """
            SELECT p.message_id,
                   COALESCE(substr(p.text, 1, 120), '<без текста>') AS preview,
                   p.created_at,
                   COUNT(c.id) AS comments_count,
                   COUNT(DISTINCT c.user_id) AS unique_commenters
            FROM posts p
            LEFT JOIN comments c ON c.channel_post_id = p.message_id
            WHERE p.deleted_at IS NULL
            GROUP BY p.message_id
            ORDER BY p.created_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(r) for r in rows]
