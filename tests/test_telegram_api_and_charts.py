from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from app.charts import render_metric_trend_png
from app.telegram_api import _reaction_sentiment
from app.telegram_api import describe_reaction_key
from app.telegram_api import extract_post_metric_from_bot_message
from app.telegram_api import parse_telegram_proxy


def test_parse_proxy_socks5_url() -> None:
    proxy = parse_telegram_proxy("socks5://127.0.0.1:1080")
    assert proxy is not None
    assert proxy[1] == "127.0.0.1"
    assert proxy[2] == 1080


def test_parse_proxy_rejects_non_socks5() -> None:
    import pytest

    with pytest.raises(ValueError):
        parse_telegram_proxy("http://127.0.0.1:8080")


def test_render_metric_trend_png_returns_png_bytes() -> None:
    png = render_metric_trend_png(
        trend_rows=[{"day": "2026-01-01", "value": 10}, {"day": "2026-01-02", "value": 12}],
        days=2,
        title="Views",
        y_label="Views",
    )
    assert png.startswith(b"\x89PNG")
    assert len(png) > 1000


def test_reaction_sentiment_positive() -> None:
    assert _reaction_sentiment("👍") == "positive"


def test_reaction_sentiment_negative() -> None:
    assert _reaction_sentiment("👎") == "negative"


def test_reaction_sentiment_neutral() -> None:
    assert _reaction_sentiment("😐") == "neutral"


def test_describe_reaction_key_positive_emoji() -> None:
    assert describe_reaction_key("👍") == "Positive emoji (👍)"


def test_describe_reaction_key_negative_emoji() -> None:
    assert describe_reaction_key("👎") == "Negative emoji (👎)"


def test_describe_reaction_key_custom_emoji() -> None:
    assert describe_reaction_key("custom:123456789") == "Custom emoji (id 123456789)"


def test_describe_reaction_key_unknown_type() -> None:
    assert describe_reaction_key("ReactionCustomEmoji") == "Reaction type (ReactionCustomEmoji)"


def test_extract_post_metric_from_bot_message_with_reactions() -> None:
    message = SimpleNamespace(
        message_id=10,
        date=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        views=120,
        forwards=7,
        reaction_count=[
            SimpleNamespace(reaction=SimpleNamespace(emoji="👍"), total_count=3),
            SimpleNamespace(reaction=SimpleNamespace(custom_emoji_id="123"), total_count=2),
        ],
    )

    metric = extract_post_metric_from_bot_message(message)
    assert metric is not None
    assert metric.message_id == 10
    assert metric.views == 120
    assert metric.forwards == 7
    assert metric.reactions_total == 5
    assert '"👍": 3' in metric.reactions_json
    assert '"custom:123": 2' in metric.reactions_json


def test_extract_post_metric_from_bot_message_without_reactions() -> None:
    message = SimpleNamespace(
        message_id=11,
        date=None,
        views=None,
        forwards=None,
        reaction_count=None,
    )

    metric = extract_post_metric_from_bot_message(message)
    assert metric is not None
    assert metric.message_id == 11
    assert metric.views == 0
    assert metric.forwards == 0
    assert metric.reactions_total == 0
    assert metric.reactions_json == "{}"
