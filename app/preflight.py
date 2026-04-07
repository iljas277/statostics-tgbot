from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import subprocess
import sys

from telegram import Bot
from telegram.request import HTTPXRequest

from app.config import Settings
from app.telegram_api import TelegramApiMetricsService


LOGGER = logging.getLogger(__name__)


def run_local_tests() -> None:
    tests_dir = Path("tests")
    if not tests_dir.exists():
        return
    result = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        output = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
        raise RuntimeError(f"Startup tests failed\n{output}".strip())


async def run_telegram_checks(settings: Settings) -> None:
    request = HTTPXRequest(
        proxy=settings.telegram_proxy_url,
        httpx_kwargs={"trust_env": False},
    )
    get_updates_request = HTTPXRequest(
        proxy=settings.telegram_proxy_url,
        httpx_kwargs={"trust_env": False},
    )
    bot = Bot(
        token=settings.bot_token,
        request=request,
        get_updates_request=get_updates_request,
    )
    await bot.initialize()
    try:
        await bot.get_me()
        await bot.get_chat(settings.channel_id)
    finally:
        await bot.shutdown()

    mtproto = TelegramApiMetricsService(settings=settings)
    if mtproto.enabled:
        try:
            await mtproto.fetch_recent_post_metrics(limit=1)
        except Exception as exc:
            LOGGER.warning(
                "MTProto startup check failed (%s). Bot API polling will continue.",
                exc,
            )


def run_startup_tests(settings: Settings) -> None:
    run_local_tests()
    asyncio.run(run_telegram_checks(settings=settings))
