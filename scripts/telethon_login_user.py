from __future__ import annotations

import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from telethon import TelegramClient

from app.config import load_settings
from app.telegram_api import parse_telegram_proxy


async def main() -> None:
    settings = load_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env before login")

    session_path = Path(settings.telegram_api_session)
    session_path.parent.mkdir(parents=True, exist_ok=True)

    proxy = parse_telegram_proxy(settings.telegram_proxy_url)

    client = TelegramClient(
        str(session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
        proxy=proxy,
    )

    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            if getattr(me, "bot", False):
                raise RuntimeError(
                    "Current Telethon session is authorized as BOT. "
                    "Delete the session file and run this script again to sign in as USER."
                )
            print(f"Already authorized as user: id={getattr(me, 'id', None)} username={getattr(me, 'username', None)}")
            return
    finally:
        await client.disconnect()

    print("Starting user login flow for Telethon session...")
    print("Enter phone number in international format, e.g. +15551234567")

    async with client:
        await client.start()
        me = await client.get_me()
        if getattr(me, "bot", False):
            raise RuntimeError("Authorized as bot, expected user account.")
        print(f"User session created: id={getattr(me, 'id', None)} username={getattr(me, 'username', None)}")


if __name__ == "__main__":
    asyncio.run(main())
