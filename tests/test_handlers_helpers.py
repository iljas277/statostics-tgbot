from __future__ import annotations

from types import SimpleNamespace

from app.handlers import _csv_bytes
from app.handlers import _extract_comment_author
from app.handlers import _extract_forward_channel_info
from app.handlers import _format_timestamp
from app.handlers import _resolve_channel_post_id


class _FakeRepo:
    def __init__(self, mapping: dict[tuple[int, int], int]) -> None:
        self.mapping = mapping

    def get_channel_post_by_discussion_root(self, linked_chat_id: int, root_group_message_id: int) -> int | None:
        return self.mapping.get((linked_chat_id, root_group_message_id))


def test_format_timestamp_handles_empty_invalid_and_iso() -> None:
    assert _format_timestamp(None) == ""
    assert _format_timestamp(" ") == ""
    assert _format_timestamp("not-a-date") == "not-a-date"

    rendered = _format_timestamp("2026-01-02T10:00:00+00:00")
    assert len(rendered) == 16
    assert rendered[2] == "."
    assert rendered[5] == "."
    assert rendered[10] == " "


def test_csv_bytes_formats_timestamp_columns() -> None:
    content = _csv_bytes(
        rows=[{"name": "alice", "created_at": "2026-01-02T10:00:00+00:00"}],
        headers=["name", "created_at"],
    ).decode("utf-8")
    assert "name,created_at" in content
    assert "02.01.2026" in content


def test_extract_forward_channel_info_from_forward_fields() -> None:
    message = SimpleNamespace(
        forward_from_chat=SimpleNamespace(id=-100123),
        forward_from_message_id=777,
        forward_origin=None,
    )
    assert _extract_forward_channel_info(message) == (-100123, 777)


def test_extract_forward_channel_info_from_forward_origin() -> None:
    message = SimpleNamespace(
        forward_from_chat=None,
        forward_from_message_id=None,
        forward_origin=SimpleNamespace(chat=SimpleNamespace(id=-100321), message_id=42),
    )
    assert _extract_forward_channel_info(message) == (-100321, 42)


def test_extract_forward_channel_info_no_data() -> None:
    message = SimpleNamespace(forward_from_chat=None, forward_from_message_id=None, forward_origin=None)
    assert _extract_forward_channel_info(message) == (None, None)


def test_extract_comment_author_prefers_human_user() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=55, username="alice", first_name="Alice", last_name="Stone", is_bot=False),
        sender_chat=SimpleNamespace(id=-200, username="chatname", title="ChatTitle"),
    )
    assert _extract_comment_author(message) == (55, "alice", "Alice", "Stone")


def test_extract_comment_author_falls_back_to_sender_chat() -> None:
    message = SimpleNamespace(
        from_user=SimpleNamespace(id=77, username="bot", first_name="Bot", last_name=None, is_bot=True),
        sender_chat=SimpleNamespace(id=-333, username="group_alias", title="Group Alias"),
    )
    assert _extract_comment_author(message) == (-333, "group_alias", "Group Alias", None)


def test_extract_comment_author_none_when_unknown() -> None:
    message = SimpleNamespace(from_user=None, sender_chat=None)
    assert _extract_comment_author(message) == (None, None, None, None)


def test_resolve_channel_post_id_by_thread_mapping() -> None:
    repo = _FakeRepo({(-2001, 100): 5001})
    message = SimpleNamespace(message_thread_id=100, reply_to_message=None)
    assert _resolve_channel_post_id(message=message, linked_chat_id=-2001, repo=repo, channel_id=-100123) == 5001


def test_resolve_channel_post_id_by_reply_forward_chain() -> None:
    repo = _FakeRepo({})
    reply = SimpleNamespace(
        message_id=999,
        forward_from_chat=SimpleNamespace(id=-100123),
        forward_from_message_id=555,
        forward_origin=None,
        reply_to_message=None,
    )
    message = SimpleNamespace(message_thread_id=None, reply_to_message=reply)
    assert _resolve_channel_post_id(message=message, linked_chat_id=-2001, repo=repo, channel_id=-100123) == 555


def test_resolve_channel_post_id_returns_none_when_not_found() -> None:
    repo = _FakeRepo({})
    message = SimpleNamespace(message_thread_id=None, reply_to_message=None)
    assert _resolve_channel_post_id(message=message, linked_chat_id=-2001, repo=repo, channel_id=-100123) is None
