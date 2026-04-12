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

    def log_message_reaction_event(
        self,
        channel_id: int,
        message_id: int,
        actor_user_id: int | None,
        actor_chat_id: int | None,
        old_reaction: list[str],
        new_reaction: list[str],
        event_at: str,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO message_reaction_events(
                channel_id,
                message_id,
                actor_user_id,
                actor_chat_id,
                old_reaction,
                new_reaction,
                event_at,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                message_id,
                actor_user_id,
                actor_chat_id,
                json.dumps(old_reaction, ensure_ascii=False),
                json.dumps(new_reaction, ensure_ascii=False),
                event_at,
                self._now_iso(),
            ),
        )

    def replace_message_reaction_counts(
        self,
        channel_id: int,
        message_id: int,
        reactions: list[tuple[str, int]],
        updated_at: str,
    ) -> int:
        self.db.execute(
            "DELETE FROM message_reaction_counts WHERE channel_id=? AND message_id=?",
            (channel_id, message_id),
        )

        for reaction_key, total_count in reactions:
            self.db.execute(
                """
                INSERT INTO message_reaction_counts(channel_id, message_id, reaction_key, total_count, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (channel_id, message_id, reaction_key, total_count, updated_at),
            )

        return len(reactions)

    def log_message_reaction_count_event(
        self,
        channel_id: int,
        message_id: int,
        reactions: list[tuple[str, int]],
        event_at: str,
    ) -> None:
        snapshot = [{"reaction_key": key, "total_count": count} for key, count in reactions]
        total_count = sum(count for _, count in reactions)
        self.db.execute(
            """
            INSERT INTO message_reaction_count_events(
                channel_id,
                message_id,
                snapshot,
                total_count,
                event_at,
                created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                channel_id,
                message_id,
                json.dumps(snapshot, ensure_ascii=False),
                total_count,
                event_at,
                self._now_iso(),
            ),
        )

    def get_message_reaction_counts(self, channel_id: int, message_id: int) -> list[dict]:
        rows = self.db.fetchall(
            """
            SELECT reaction_key, total_count, updated_at
            FROM message_reaction_counts
            WHERE channel_id=? AND message_id=?
            ORDER BY total_count DESC, reaction_key ASC
            """,
            (channel_id, message_id),
        )
        return [dict(r) for r in rows]

    def get_recent_message_reaction_events(self, channel_id: int, message_id: int, limit: int = 8) -> list[dict]:
        rows = self.db.fetchall(
            """
            SELECT actor_user_id, actor_chat_id, old_reaction, new_reaction, event_at
            FROM message_reaction_events
            WHERE channel_id=? AND message_id=?
            ORDER BY event_at DESC
            LIMIT ?
            """,
            (channel_id, message_id, limit),
        )

        result: list[dict] = []
        for row in rows:
            entry = dict(row)
            entry["old_reaction"] = json.loads(entry["old_reaction"] or "[]")
            entry["new_reaction"] = json.loads(entry["new_reaction"] or "[]")
            result.append(entry)
        return result

    def get_recent_message_reaction_count_events(
        self,
        channel_id: int,
        message_id: int,
        limit: int = 8,
    ) -> list[dict]:
        rows = self.db.fetchall(
            """
            SELECT snapshot, total_count, event_at
            FROM message_reaction_count_events
            WHERE channel_id=? AND message_id=?
            ORDER BY event_at DESC
            LIMIT ?
            """,
            (channel_id, message_id, limit),
        )

        result: list[dict] = []
        for row in rows:
            entry = dict(row)
            entry["snapshot"] = json.loads(entry["snapshot"] or "[]")
            result.append(entry)
        return result

    def get_reaction_overview(self, channel_id: int, period_hours: int) -> dict:
        since = datetime.now(tz=timezone.utc) - timedelta(hours=period_hours)
        since_iso = since.isoformat()

        events_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS c
            FROM message_reaction_events
            WHERE channel_id=? AND event_at >= ?
            """,
            (channel_id, since_iso),
        )
        touched_posts_row = self.db.fetchone(
            """
            SELECT COUNT(DISTINCT message_id) AS c
            FROM message_reaction_events
            WHERE channel_id=? AND event_at >= ?
            """,
            (channel_id, since_iso),
        )
        count_events_row = self.db.fetchone(
            """
            SELECT COUNT(*) AS c
            FROM message_reaction_count_events
            WHERE channel_id=? AND event_at >= ?
            """,
            (channel_id, since_iso),
        )
        touched_posts_count_row = self.db.fetchone(
            """
            SELECT COUNT(DISTINCT message_id) AS c
            FROM message_reaction_count_events
            WHERE channel_id=? AND event_at >= ?
            """,
            (channel_id, since_iso),
        )
        total_current_row = self.db.fetchone(
            """
            SELECT COALESCE(SUM(total_count), 0) AS c
            FROM message_reaction_counts
            WHERE channel_id=?
            """,
            (channel_id,),
        )
        top_reactions_rows = self.db.fetchall(
            """
            SELECT reaction_key, SUM(total_count) AS total
            FROM message_reaction_counts
            WHERE channel_id=?
            GROUP BY reaction_key
            ORDER BY total DESC, reaction_key ASC
            LIMIT 10
            """,
            (channel_id,),
        )
        top_posts_rows = self.db.fetchall(
            """
            SELECT message_id, SUM(total_count) AS total
            FROM message_reaction_counts
            WHERE channel_id=?
            GROUP BY message_id
            ORDER BY total DESC, message_id DESC
            LIMIT 5
            """,
            (channel_id,),
        )

        return {
            "reaction_events_in_period": int(events_row["c"] if events_row else 0),
            "reaction_count_events_in_period": int(count_events_row["c"] if count_events_row else 0),
            "events_in_period": int(events_row["c"] if events_row else 0)
            + int(count_events_row["c"] if count_events_row else 0),
            "posts_touched_in_period": max(
                int(touched_posts_row["c"] if touched_posts_row else 0),
                int(touched_posts_count_row["c"] if touched_posts_count_row else 0),
            ),
            "current_total_reactions": int(total_current_row["c"] if total_current_row else 0),
            "top_reactions": [dict(r) for r in top_reactions_rows],
            "top_posts": [dict(r) for r in top_posts_rows],
        }

    def get_top_reacted_posts(self, channel_id: int, period_hours: int, limit: int = 7) -> list[dict]:
        since = datetime.now(tz=timezone.utc) - timedelta(hours=period_hours)
        since_iso = since.isoformat()

        period_rows = self.db.fetchall(
            """
            WITH touched_posts AS (
                SELECT DISTINCT message_id
                FROM message_reaction_count_events
                WHERE channel_id=? AND event_at >= ?
                UNION
                SELECT DISTINCT message_id
                FROM message_reaction_events
                WHERE channel_id=? AND event_at >= ?
                UNION
                SELECT DISTINCT message_id
                FROM message_reaction_counts
                WHERE channel_id=? AND updated_at >= ?
            ),
            current_totals AS (
                SELECT message_id,
                       SUM(total_count) AS reactions_total,
                       MAX(updated_at) AS last_reaction_update_at
                FROM message_reaction_counts
                WHERE channel_id=?
                GROUP BY message_id
            )
            SELECT tp.message_id,
                   COALESCE(ct.reactions_total, 0) AS reactions_total,
                   ct.last_reaction_update_at,
                   p.created_at,
                   p.text
            FROM touched_posts tp
            LEFT JOIN current_totals ct ON ct.message_id = tp.message_id
            LEFT JOIN posts p ON p.message_id = tp.message_id
            ORDER BY reactions_total DESC, tp.message_id DESC
            LIMIT ?
            """,
            (channel_id, since_iso, channel_id, since_iso, channel_id, since_iso, channel_id, limit),
        )
        if period_rows:
            return [dict(r) for r in period_rows]

        fallback_rows = self.db.fetchall(
            """
            SELECT mrc.message_id,
                   SUM(mrc.total_count) AS reactions_total,
                   MAX(mrc.updated_at) AS last_reaction_update_at,
                   p.created_at,
                   p.text
            FROM message_reaction_counts mrc
            LEFT JOIN posts p ON p.message_id = mrc.message_id
            WHERE mrc.channel_id=?
            GROUP BY mrc.message_id
            ORDER BY reactions_total DESC, mrc.message_id DESC
            LIMIT ?
            """,
            (channel_id, limit),
        )
        return [dict(r) for r in fallback_rows]

    def get_top_commenters(self, limit: int = 10) -> list[dict]:
        rows = self.db.fetchall(
            """
            WITH raw_users AS (
                SELECT u.user_id, u.username, u.first_name, u.last_name
                FROM users u
                UNION
                SELECT DISTINCT mre.actor_user_id AS user_id,
                       '' AS username,
                       '' AS first_name,
                       '' AS last_name
                FROM message_reaction_events mre
                WHERE mre.actor_user_id IS NOT NULL
            ),
            user_keys AS (
                SELECT ru.user_id,
                       CASE
                           WHEN NULLIF(LOWER(TRIM(ru.username)), '') IS NOT NULL
                               THEN 'uname:' || NULLIF(LOWER(TRIM(ru.username)), '')
                           ELSE 'id:' || CAST(ru.user_id AS TEXT)
                       END AS commenter_key,
                       COALESCE(NULLIF(TRIM(ru.username), ''), '') AS username,
                       COALESCE(NULLIF(TRIM(ru.first_name), ''), '') AS first_name,
                       COALESCE(NULLIF(TRIM(ru.last_name), ''), '') AS last_name
                FROM raw_users ru
            ),
            commenter_profiles AS (
                SELECT commenter_key,
                       MIN(user_id) AS sample_user_id,
                       GROUP_CONCAT(DISTINCT user_id) AS user_ids,
                       COALESCE(MAX(NULLIF(username, '')), '') AS username,
                       COALESCE(MAX(NULLIF(first_name, '')), '') AS first_name,
                       COALESCE(MAX(NULLIF(last_name, '')), '') AS last_name
                FROM user_keys
                GROUP BY commenter_key
            ),
            comment_stats AS (
                SELECT uk.commenter_key,
                       COUNT(*) AS comments_count,
                       MAX(c.created_at) AS last_comment_at
                FROM comments c
                JOIN user_keys uk ON uk.user_id = c.user_id
                GROUP BY uk.commenter_key
            ),
            reaction_stats AS (
                SELECT uk.commenter_key,
                       COUNT(*) AS reaction_events_count,
                       MAX(mre.event_at) AS last_reaction_at
                FROM message_reaction_events mre
                JOIN user_keys uk ON uk.user_id = mre.actor_user_id
                GROUP BY uk.commenter_key
            ),
            base_keys AS (
                SELECT commenter_key FROM comment_stats
                UNION
                SELECT commenter_key FROM reaction_stats
            )
            SELECT bk.commenter_key,
                   cp.sample_user_id,
                   cp.user_ids,
                   cp.username,
                   cp.first_name,
                   cp.last_name,
                   COALESCE(cs.comments_count, 0) AS comments_count,
                   COALESCE(cs.last_comment_at, '') AS last_comment_at,
                   COALESCE(rs.reaction_events_count, 0) AS reaction_events_count,
                   COALESCE(rs.last_reaction_at, '') AS last_reaction_at,
                   COALESCE(cs.comments_count, 0) + COALESCE(rs.reaction_events_count, 0) AS activity_score
            FROM base_keys bk
            LEFT JOIN commenter_profiles cp ON cp.commenter_key = bk.commenter_key
            LEFT JOIN comment_stats cs ON cs.commenter_key = bk.commenter_key
            LEFT JOIN reaction_stats rs ON rs.commenter_key = bk.commenter_key
            ORDER BY activity_score DESC, comments_count DESC, reaction_events_count DESC, bk.commenter_key ASC
            LIMIT ?
            """,
            (limit,),
        )

        result: list[dict] = []
        for row in rows:
            user_id = int(row["sample_user_id"])
            username = (row["username"] or "").strip() or None
            first_name = (row["first_name"] or "").strip()
            last_name = (row["last_name"] or "").strip()
            display_name = (
                f"@{username}"
                if username
                else (" ".join(part for part in [first_name, last_name] if part).strip() or str(user_id))
            )
            result.append(
                {
                    "commenter_key": row["commenter_key"],
                    "user_ids": row["user_ids"] or str(user_id),
                    "user_id": user_id,
                    "username": username,
                    "display_name": display_name,
                    "comments_count": int(row["comments_count"]),
                    "last_comment_at": row["last_comment_at"],
                    "reaction_events_count": int(row["reaction_events_count"]),
                    "last_reaction_at": row["last_reaction_at"],
                    "activity_score": int(row["activity_score"]),
                    "profile_url": self._profile_url_for_user(user_id=user_id, username=username),
                }
            )
        return result

    def get_top_commenters_for_post(self, message_id: int, limit: int = 10) -> list[dict]:
        rows = self.db.fetchall(
            """
            WITH raw_users AS (
                SELECT u.user_id, u.username, u.first_name, u.last_name
                FROM users u
                UNION
                SELECT DISTINCT mre.actor_user_id AS user_id,
                       '' AS username,
                       '' AS first_name,
                       '' AS last_name
                FROM message_reaction_events mre
                WHERE mre.actor_user_id IS NOT NULL
            ),
            user_keys AS (
                SELECT ru.user_id,
                       CASE
                           WHEN NULLIF(LOWER(TRIM(ru.username)), '') IS NOT NULL
                               THEN 'uname:' || NULLIF(LOWER(TRIM(ru.username)), '')
                           ELSE 'id:' || CAST(ru.user_id AS TEXT)
                       END AS commenter_key,
                       COALESCE(NULLIF(TRIM(ru.username), ''), '') AS username,
                       COALESCE(NULLIF(TRIM(ru.first_name), ''), '') AS first_name,
                       COALESCE(NULLIF(TRIM(ru.last_name), ''), '') AS last_name
                FROM raw_users ru
            ),
            commenter_profiles AS (
                SELECT commenter_key,
                       MIN(user_id) AS sample_user_id,
                       GROUP_CONCAT(DISTINCT user_id) AS user_ids,
                       COALESCE(MAX(NULLIF(username, '')), '') AS username,
                       COALESCE(MAX(NULLIF(first_name, '')), '') AS first_name,
                       COALESCE(MAX(NULLIF(last_name, '')), '') AS last_name
                FROM user_keys
                GROUP BY commenter_key
            ),
            comment_stats AS (
                SELECT uk.commenter_key,
                       COUNT(*) AS comments_count,
                       MAX(c.created_at) AS last_comment_at
                FROM comments c
                JOIN user_keys uk ON uk.user_id = c.user_id
                WHERE c.channel_post_id = ?
                GROUP BY uk.commenter_key
            ),
            reaction_stats AS (
                SELECT uk.commenter_key,
                       COUNT(*) AS reaction_events_count,
                       MAX(mre.event_at) AS last_reaction_at
                FROM message_reaction_events mre
                JOIN user_keys uk ON uk.user_id = mre.actor_user_id
                WHERE mre.message_id = ?
                GROUP BY uk.commenter_key
            ),
            base_keys AS (
                SELECT commenter_key FROM comment_stats
                UNION
                SELECT commenter_key FROM reaction_stats
            )
            SELECT bk.commenter_key,
                   cp.sample_user_id,
                   cp.user_ids,
                   cp.username,
                   cp.first_name,
                   cp.last_name,
                   COALESCE(cs.comments_count, 0) AS comments_count,
                   COALESCE(cs.last_comment_at, '') AS last_comment_at,
                   COALESCE(rs.reaction_events_count, 0) AS reaction_events_count,
                   COALESCE(rs.last_reaction_at, '') AS last_reaction_at,
                   COALESCE(cs.comments_count, 0) + COALESCE(rs.reaction_events_count, 0) AS activity_score
            FROM base_keys bk
            LEFT JOIN commenter_profiles cp ON cp.commenter_key = bk.commenter_key
            LEFT JOIN comment_stats cs ON cs.commenter_key = bk.commenter_key
            LEFT JOIN reaction_stats rs ON rs.commenter_key = bk.commenter_key
            ORDER BY activity_score DESC, comments_count DESC, reaction_events_count DESC, bk.commenter_key ASC
            LIMIT ?
            """,
            (message_id, message_id, limit),
        )

        result: list[dict] = []
        for row in rows:
            user_id = int(row["sample_user_id"])
            username = (row["username"] or "").strip() or None
            first_name = (row["first_name"] or "").strip()
            last_name = (row["last_name"] or "").strip()
            display_name = (
                f"@{username}"
                if username
                else (" ".join(part for part in [first_name, last_name] if part).strip() or str(user_id))
            )
            result.append(
                {
                    "message_id": message_id,
                    "commenter_key": row["commenter_key"],
                    "user_ids": row["user_ids"] or str(user_id),
                    "user_id": user_id,
                    "username": username,
                    "display_name": display_name,
                    "comments_count": int(row["comments_count"]),
                    "last_comment_at": row["last_comment_at"],
                    "reaction_events_count": int(row["reaction_events_count"]),
                    "last_reaction_at": row["last_reaction_at"],
                    "activity_score": int(row["activity_score"]),
                    "profile_url": self._profile_url_for_user(user_id=user_id, username=username),
                }
            )
        return result

    def get_users_full_stats(self, user_ids: list[int]) -> dict[int, dict]:
        if not user_ids:
            return {}

        normalized_user_ids = sorted({int(user_id) for user_id in user_ids})
        placeholders = ",".join("?" for _ in normalized_user_ids)

        rows = self.db.fetchall(
            f"""
            WITH comment_stats AS (
                SELECT user_id,
                       COUNT(*) AS comments_total,
                       COUNT(DISTINCT channel_post_id) AS unique_posts_commented,
                       SUM(CASE WHEN has_contact = 1 THEN 1 ELSE 0 END) AS comments_with_contact,
                       SUM(CASE WHEN has_intent = 1 THEN 1 ELSE 0 END) AS comments_with_intent,
                       MIN(created_at) AS first_comment_at,
                       MAX(created_at) AS last_comment_at
                FROM comments
                GROUP BY user_id
            ),
            reaction_actor_stats AS (
                SELECT actor_user_id AS user_id,
                       COUNT(*) AS reaction_events_count,
                       MAX(event_at) AS last_reaction_event_at
                FROM message_reaction_events
                WHERE actor_user_id IS NOT NULL
                GROUP BY actor_user_id
            )
            SELECT u.user_id,
                   COALESCE(cs.comments_total, 0) AS comments_total,
                   COALESCE(cs.unique_posts_commented, 0) AS unique_posts_commented,
                   COALESCE(cs.comments_with_contact, 0) AS comments_with_contact,
                   COALESCE(cs.comments_with_intent, 0) AS comments_with_intent,
                   COALESCE(cs.first_comment_at, '') AS first_comment_at,
                   COALESCE(cs.last_comment_at, '') AS last_comment_at,
                   COALESCE(ras.reaction_events_count, 0) AS reaction_events_count,
                   COALESCE(ras.last_reaction_event_at, '') AS last_reaction_event_at,
                   COALESCE(l.score, 0) AS lead_score,
                   COALESCE(l.status, '') AS lead_status,
                   COALESCE(l.tags, '') AS lead_tags,
                   COALESCE(l.updated_at, '') AS lead_updated_at
            FROM users u
            LEFT JOIN comment_stats cs ON cs.user_id = u.user_id
            LEFT JOIN reaction_actor_stats ras ON ras.user_id = u.user_id
            LEFT JOIN leads l ON l.user_id = u.user_id
            WHERE u.user_id IN ({placeholders})
            """,
            tuple(normalized_user_ids),
        )

        return {
            int(row["user_id"]): {
                "comments_total": int(row["comments_total"]),
                "unique_posts_commented": int(row["unique_posts_commented"]),
                "comments_with_contact": int(row["comments_with_contact"]),
                "comments_with_intent": int(row["comments_with_intent"]),
                "first_comment_at": row["first_comment_at"] or "",
                "last_comment_at": row["last_comment_at"] or "",
                "reaction_events_count": int(row["reaction_events_count"]),
                "last_reaction_event_at": row["last_reaction_event_at"] or "",
                "lead_score": int(row["lead_score"]),
                "lead_status": row["lead_status"] or "",
                "lead_tags": row["lead_tags"] or "",
                "lead_updated_at": row["lead_updated_at"] or "",
            }
            for row in rows
        }

    def get_commenters_full_stats(self, commenter_keys: list[str]) -> dict[str, dict]:
        if not commenter_keys:
            return {}

        normalized_keys = sorted({str(key).strip().lower() for key in commenter_keys if str(key).strip()})
        placeholders = ",".join("?" for _ in normalized_keys)

        rows = self.db.fetchall(
            f"""
            WITH raw_users AS (
                SELECT u.user_id, u.username, u.first_name, u.last_name
                FROM users u
                UNION
                SELECT DISTINCT mre.actor_user_id AS user_id,
                       '' AS username,
                       '' AS first_name,
                       '' AS last_name
                FROM message_reaction_events mre
                WHERE mre.actor_user_id IS NOT NULL
            ),
            user_keys AS (
                SELECT ru.user_id,
                       CASE
                           WHEN NULLIF(LOWER(TRIM(ru.username)), '') IS NOT NULL
                               THEN 'uname:' || NULLIF(LOWER(TRIM(ru.username)), '')
                           ELSE 'id:' || CAST(ru.user_id AS TEXT)
                       END AS commenter_key
                FROM raw_users ru
            ),
            selected_keys AS (
                SELECT DISTINCT commenter_key
                FROM user_keys
                WHERE commenter_key IN ({placeholders})
            ),
            comment_stats AS (
                SELECT uk.commenter_key,
                       COUNT(*) AS comments_total,
                       COUNT(DISTINCT c.channel_post_id) AS unique_posts_commented,
                       SUM(CASE WHEN c.has_contact = 1 THEN 1 ELSE 0 END) AS comments_with_contact,
                       SUM(CASE WHEN c.has_intent = 1 THEN 1 ELSE 0 END) AS comments_with_intent,
                       MIN(c.created_at) AS first_comment_at,
                       MAX(c.created_at) AS last_comment_at
                FROM comments c
                JOIN user_keys uk ON uk.user_id = c.user_id
                GROUP BY uk.commenter_key
            ),
            reaction_actor_stats AS (
                SELECT uk.commenter_key,
                       COUNT(*) AS reaction_events_count,
                       MAX(mre.event_at) AS last_reaction_event_at
                FROM message_reaction_events mre
                JOIN user_keys uk ON uk.user_id = mre.actor_user_id
                GROUP BY uk.commenter_key
            ),
            comment_like_totals AS (
                SELECT c.group_message_id,
                       c.linked_chat_id,
                       COALESCE(SUM(mrc.total_count), 0) AS likes_total
                FROM comments c
                LEFT JOIN message_reaction_counts mrc
                    ON mrc.channel_id = c.linked_chat_id
                   AND mrc.message_id = c.group_message_id
                GROUP BY c.group_message_id, c.linked_chat_id
            ),
            comment_reaction_event_totals AS (
                SELECT c.group_message_id,
                       c.linked_chat_id,
                       COUNT(mre.id) AS reaction_events_for_comment
                FROM comments c
                LEFT JOIN message_reaction_events mre
                    ON mre.channel_id = c.linked_chat_id
                   AND mre.message_id = c.group_message_id
                GROUP BY c.group_message_id, c.linked_chat_id
            ),
            comment_reaction_totals AS (
                SELECT uk.commenter_key,
                       c.group_message_id,
                       c.text,
                       COALESCE(clt.likes_total, 0) AS likes_total,
                       COALESCE(cert.reaction_events_for_comment, 0) AS reaction_events_for_comment,
                       CASE
                           WHEN COALESCE(clt.likes_total, 0) > 0 THEN COALESCE(clt.likes_total, 0)
                           ELSE COALESCE(cert.reaction_events_for_comment, 0)
                       END AS likes_score
                FROM comments c
                JOIN user_keys uk ON uk.user_id = c.user_id
                LEFT JOIN comment_like_totals clt
                    ON clt.group_message_id = c.group_message_id
                   AND clt.linked_chat_id = c.linked_chat_id
                LEFT JOIN comment_reaction_event_totals cert
                    ON cert.group_message_id = c.group_message_id
                   AND cert.linked_chat_id = c.linked_chat_id
            ),
            most_liked_comment AS (
                SELECT commenter_key,
                       group_message_id AS most_liked_comment_id,
                       text AS most_liked_comment_text,
                       likes_score AS most_liked_comment_likes
                FROM (
                    SELECT commenter_key,
                           group_message_id,
                           text,
                           likes_score,
                           ROW_NUMBER() OVER (
                               PARTITION BY commenter_key
                               ORDER BY likes_score DESC, group_message_id DESC
                           ) AS row_num
                    FROM comment_reaction_totals
                ) ranked
                WHERE row_num = 1
            ),
            lead_stats AS (
                SELECT uk.commenter_key,
                       SUM(COALESCE(l.score, 0)) AS lead_score,
                       MAX(COALESCE(l.updated_at, '')) AS lead_updated_at,
                       GROUP_CONCAT(DISTINCT COALESCE(l.status, '')) AS lead_statuses,
                       GROUP_CONCAT(DISTINCT COALESCE(l.tags, '')) AS lead_tags_raw
                FROM user_keys uk
                LEFT JOIN leads l ON l.user_id = uk.user_id
                GROUP BY uk.commenter_key
            )
            SELECT sk.commenter_key,
                   COALESCE(cs.comments_total, 0) AS comments_total,
                   COALESCE(cs.unique_posts_commented, 0) AS unique_posts_commented,
                   COALESCE(cs.comments_with_contact, 0) AS comments_with_contact,
                   COALESCE(cs.comments_with_intent, 0) AS comments_with_intent,
                   COALESCE(cs.first_comment_at, '') AS first_comment_at,
                   COALESCE(cs.last_comment_at, '') AS last_comment_at,
                   COALESCE(ras.reaction_events_count, 0) AS reaction_events_count,
                   COALESCE(ras.last_reaction_event_at, '') AS last_reaction_event_at,
                     COALESCE(mlc.most_liked_comment_id, 0) AS most_liked_comment_id,
                     COALESCE(mlc.most_liked_comment_text, '') AS most_liked_comment_text,
                     COALESCE(mlc.most_liked_comment_likes, 0) AS most_liked_comment_likes,
                   COALESCE(ls.lead_score, 0) AS lead_score,
                   COALESCE(ls.lead_statuses, '') AS lead_statuses,
                   COALESCE(ls.lead_tags_raw, '') AS lead_tags_raw,
                   COALESCE(ls.lead_updated_at, '') AS lead_updated_at
            FROM selected_keys sk
            LEFT JOIN comment_stats cs ON cs.commenter_key = sk.commenter_key
            LEFT JOIN reaction_actor_stats ras ON ras.commenter_key = sk.commenter_key
                 LEFT JOIN most_liked_comment mlc ON mlc.commenter_key = sk.commenter_key
            LEFT JOIN lead_stats ls ON ls.commenter_key = sk.commenter_key
            """,
            tuple(normalized_keys),
        )

        result: dict[str, dict] = {}
        for row in rows:
            key = (row["commenter_key"] or "").lower()
            status_parts = [part.strip() for part in (row["lead_statuses"] or "").split(",") if part.strip()]
            tag_parts = [part.strip() for part in (row["lead_tags_raw"] or "").split(",") if part.strip()]
            result[key] = {
                "comments_total": int(row["comments_total"]),
                "unique_posts_commented": int(row["unique_posts_commented"]),
                "comments_with_contact": int(row["comments_with_contact"]),
                "comments_with_intent": int(row["comments_with_intent"]),
                "first_comment_at": row["first_comment_at"] or "",
                "last_comment_at": row["last_comment_at"] or "",
                "reaction_events_count": int(row["reaction_events_count"]),
                "last_reaction_event_at": row["last_reaction_event_at"] or "",
                "most_liked_comment_id": int(row["most_liked_comment_id"]),
                "most_liked_comment_text": row["most_liked_comment_text"] or "",
                "most_liked_comment_likes": int(row["most_liked_comment_likes"]),
                "activity_score": int(row["comments_total"]) + int(row["reaction_events_count"]),
                "lead_score": int(row["lead_score"]),
                "lead_status": "|".join(dict.fromkeys(status_parts)),
                "lead_tags": ",".join(dict.fromkeys(tag_parts)),
                "lead_updated_at": row["lead_updated_at"] or "",
            }
        return result

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
        reaction_counts_rows = self.db.fetchall(
            """
            SELECT reaction_key, total_count, updated_at
            FROM message_reaction_counts
            WHERE channel_id = ? AND message_id = ?
            ORDER BY total_count DESC, reaction_key ASC
            """,
            (int(post_row["channel_id"]), message_id),
        )
        reaction_total_row = self.db.fetchone(
            """
            SELECT COALESCE(SUM(total_count), 0) AS c
            FROM message_reaction_counts
            WHERE channel_id = ? AND message_id = ?
            """,
            (int(post_row["channel_id"]), message_id),
        )
        reaction_last_event_row = self.db.fetchone(
            """
            SELECT MAX(event_at) AS ts
            FROM message_reaction_events
            WHERE channel_id = ? AND message_id = ?
            """,
            (int(post_row["channel_id"]), message_id),
        )

        return {
            "post": dict(post_row),
            "comments_count": int(comments_row["c"] if comments_row else 0),
            "unique_commenters": int(unique_row["c"] if unique_row else 0),
            "leads_in_comments": int(leads_row["c"] if leads_row else 0),
            "last_comment_at": last_comment_row["ts"] if last_comment_row else None,
            "reactions_total": int(reaction_total_row["c"] if reaction_total_row else 0),
            "reactions_last_event_at": reaction_last_event_row["ts"] if reaction_last_event_row else None,
            "reaction_counts": [dict(r) for r in reaction_counts_rows],
            "top_commenters": [dict(r) for r in top_rows],
        }
