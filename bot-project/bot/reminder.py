"""
Background reminder loop — ported and adapted from Bot Admin Fix.

Reminders sent:
- Free users: when their daily limit resets (if they used the bot)
- Premium users: when their plan expires today or has already expired
"""
import asyncio
import logging
import time
from datetime import datetime

from pyrogram import Client

from bot.database import users_db, premium_db, settings_db

logger = logging.getLogger(__name__)

_last_premium_expiry_reminder: dict[int, str] = {}
_reminder_task: asyncio.Task | None = None


def _time_left_str(dt) -> str:
    if isinstance(dt, datetime):
        remaining = int((dt - datetime.utcnow()).total_seconds())
    else:
        remaining = int(float(dt) - time.time())
    if remaining <= 0:
        return "Expired"
    days = remaining // 86400
    hours = (remaining % 86400) // 3600
    minutes = (remaining % 3600) // 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    return " ".join(parts) if parts else "< 1m"


def _readable(dt) -> str:
    if isinstance(dt, datetime):
        return dt.strftime("%d %b %Y at %I:%M %p UTC")
    return datetime.utcfromtimestamp(float(dt)).strftime("%d %b %Y at %I:%M %p UTC")


async def _safe_send(app: Client, user_id: int, text: str) -> None:
    try:
        await app.send_message(user_id, text)
    except Exception as e:
        logger.debug(f"Could not send reminder to {user_id}: {e}")


async def _handle_premium_expiry(app: Client, user_ids: list[int]) -> None:
    now = datetime.utcnow()
    today_key = now.date().isoformat()

    for user_id in user_ids:
        plan = await premium_db.get_plan(user_id)
        if not plan:
            continue

        expires_at = plan.get("expires_at")
        if not expires_at:
            continue

        # expires_at is a datetime object from our DB
        if isinstance(expires_at, datetime):
            expiry_dt = expires_at
        else:
            expiry_dt = datetime.utcfromtimestamp(float(expires_at))

        expiry_date_key = expiry_dt.date().isoformat()

        # Only remind if expiring today or already expired
        is_today = expiry_dt.date() == now.date()
        is_expired = expiry_dt <= now

        if not (is_today or is_expired):
            continue

        if _last_premium_expiry_reminder.get(user_id) == expiry_date_key:
            continue

        if not is_expired:
            text = (
                "⏳ **Your premium plan ends today**\n\n"
                f"📦 **Plan:** Premium\n"
                f"🕒 **Time Left:** {_time_left_str(expires_at)}\n"
                f"📅 **Expires On:** {_readable(expires_at)}\n\n"
                "Renew it to keep unlimited downloads without interruption.\n\n"
                "Use /myplan to see your current status."
            )
        else:
            text = (
                "⚠️ **Your premium plan has ended**\n\n"
                f"📦 **Plan:** Premium\n"
                f"📅 **Expired On:** {_readable(expires_at)}\n\n"
                "Renew it to continue unlimited downloads.\n\n"
                "Use /myplan to see available plans."
            )

        await _safe_send(app, user_id, text)
        _last_premium_expiry_reminder[user_id] = expiry_date_key


async def reminder_loop(app: Client, interval_seconds: int = 1800) -> None:
    logger.info("🕒 Reminder loop started (interval: %ds)", interval_seconds)
    await asyncio.sleep(interval_seconds)
    while True:
        try:
            user_ids = await users_db.get_all_user_ids()
            if user_ids:
                await _handle_premium_expiry(app, user_ids)
        except Exception as e:
            logger.exception(f"Reminder loop error: {e}")

        await asyncio.sleep(interval_seconds)


def start_reminders(app: Client, interval_seconds: int = 1800) -> asyncio.Task:
    global _reminder_task

    if _reminder_task and not _reminder_task.done():
        return _reminder_task

    _reminder_task = asyncio.create_task(reminder_loop(app, interval_seconds))
    return _reminder_task
