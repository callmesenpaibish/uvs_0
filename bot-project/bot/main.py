import asyncio
import json
import logging
import os
import urllib.request
from aiohttp import web

from pyrogram import Client
from bot.config import BOT_TOKEN, API_ID, API_HASH, STRING_SESSION
from bot.database import init_db
from bot.reminder import start_reminders

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _drain_pending_updates(bot_token: str) -> None:
    """Call Bot API to discard all pending updates before Pyrogram starts."""
    base = f"https://api.telegram.org/bot{bot_token}"
    try:
        with urllib.request.urlopen(
            f"{base}/getUpdates?timeout=0&limit=100", timeout=10
        ) as r:
            data = json.loads(r.read())
        updates = data.get("result", [])
        if updates:
            last_id = updates[-1]["update_id"]
            with urllib.request.urlopen(
                f"{base}/getUpdates?offset={last_id + 1}&timeout=0", timeout=10
            ) as r:
                r.read()
        print(f"[Bot] ⏭️  Skipped {len(updates)} pending update(s).")
    except Exception as e:
        print(f"[Bot] ⚠️  Could not drain pending updates: {e}")


async def _health_handler(request):
    return web.Response(text="OK")


async def _start_health_server():
    port = int(os.environ.get("HEALTH_PORT", os.environ.get("PORT", 8082)))
    app = web.Application()
    app.router.add_get("/", _health_handler)
    app.router.add_get("/health", _health_handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    print(f"[Health] ✅ Health check server running on port {port}")


async def main():
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("  📦 File Store & Auto-Post Bot Starting  ")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    await _start_health_server()

    print("[DB] Connecting to MongoDB...")
    await init_db()
    print("[DB] ✅ Connected")

    _drain_pending_updates(BOT_TOKEN)

    app = Client(
        name="filestore_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
        session_string=STRING_SESSION if STRING_SESSION else None,
        workers=8,
        sleep_threshold=60,
        plugins=dict(root="bot/plugins"),
    )

    print("[Bot] 🚀 Starting Pyrogram client...")

    async with app:
        me = await app.get_me()
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"[Bot] ✅ Logged in as @{me.username} ({me.id})")
        print("[Bot] 🟢 Bot is running. Press Ctrl+C to stop.")
        print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

        start_reminders(app)
        print("[Reminder] ✅ Reminder loop started")

        from bot.config import BIN_CHANNEL_ID, MAIN_CHANNEL_ID
        for ch_id in [BIN_CHANNEL_ID, MAIN_CHANNEL_ID]:
            if ch_id:
                try:
                    await app.get_chat(ch_id)
                    print(f"[Scraper] ✅ Resolved channel {ch_id}")
                except Exception as e:
                    print(f"[Scraper] ⚠️  Could not resolve channel {ch_id}: {e}")
                    print(f"[Scraper] ⚠️  Make sure the bot is admin in that channel!")

        from bot.plugins.scraper import start_scraper_worker
        start_scraper_worker(app)
        print("[Scraper] ✅ Sequential scraper worker started")

        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
