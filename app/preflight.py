from __future__ import annotations

import asyncio
from pathlib import Path
import subprocess
import sys

from telegram import Bot

from app.config import Settings
from app.telegram_api import TelegramApiMetricsService


def run_local_tests() -> None:
    tests_dir = Path("tests")
    if not tests_dir.exists():
        return
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError("Startup tests failed")


async def run_telegram_checks(settings: Settings) -> None:
    bot = Bot(token=settings.bot_token)
    await bot.initialize()
    try:
        await bot.get_me()
        await bot.get_chat(settings.channel_id)
    finally:
        await bot.shutdown()

    mtproto = TelegramApiMetricsService(settings=settings)
    if mtproto.enabled:
        await mtproto.fetch_recent_post_metrics(limit=1)


def run_startup_tests(settings: Settings) -> None:
    run_local_tests()
    asyncio.run(run_telegram_checks(settings=settings))
