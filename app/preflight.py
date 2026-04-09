from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import subprocess
import sys

from telegram import Bot
from telegram.request import HTTPXRequest

from app.config import Settings


LOGGER = logging.getLogger(__name__)


def run_local_tests() -> None:
    tests_dir = Path("tests")
    if not tests_dir.exists():
        return

    LOGGER.info("Startup tests: running pytest...")

    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        check=False,
        capture_output=True,
        text=True,
    )

    # Pytest exit code 5 means no tests were collected.
    if result.returncode == 5:
        LOGGER.warning("Startup tests skipped: no tests were collected")
        return

    if result.returncode != 0:
        output = (result.stdout or "") + ("\n" if result.stdout and result.stderr else "") + (result.stderr or "")
        raise RuntimeError(f"Startup tests failed\n{output}".strip())

    summary = (result.stdout or "").strip()
    if summary:
        LOGGER.info("Startup tests passed:\n%s", summary)
    else:
        LOGGER.info("Startup tests passed")


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

def run_startup_tests(settings: Settings) -> None:
    run_local_tests()
    asyncio.run(run_telegram_checks(settings=settings))
