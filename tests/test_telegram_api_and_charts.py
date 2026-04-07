from __future__ import annotations

import unittest

from app.charts import render_metric_trend_png
from app.telegram_api import parse_telegram_proxy
from app.telegram_api import _reaction_sentiment


class TelegramApiAndChartsTests(unittest.TestCase):
    def test_parse_proxy_socks5_url(self) -> None:
        proxy = parse_telegram_proxy("socks5://127.0.0.1:1080")
        self.assertIsNotNone(proxy)
        self.assertEqual(proxy[1], "127.0.0.1")
        self.assertEqual(proxy[2], 1080)

    def test_parse_proxy_rejects_non_socks5(self) -> None:
        with self.assertRaises(ValueError):
            parse_telegram_proxy("http://127.0.0.1:8080")

    def test_render_metric_trend_png_returns_png_bytes(self) -> None:
        png = render_metric_trend_png(
            trend_rows=[{"day": "2026-01-01", "value": 10}, {"day": "2026-01-02", "value": 12}],
            days=2,
            title="Views",
            y_label="Views",
        )
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertGreater(len(png), 1000)

    def test_reaction_sentiment_positive(self) -> None:
        self.assertEqual(_reaction_sentiment("👍"), "positive")

    def test_reaction_sentiment_negative(self) -> None:
        self.assertEqual(_reaction_sentiment("👎"), "negative")

    def test_reaction_sentiment_neutral(self) -> None:
        self.assertEqual(_reaction_sentiment("😐"), "neutral")


if __name__ == "__main__":
    unittest.main()
