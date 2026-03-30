import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message

from bot.database import users_db
from bot.config import ADMIN_IDS, BROADCAST_CHUNK


def is_admin(_, __, message):
    return message.from_user and message.from_user.id in ADMIN_IDS


admin_filter = filters.create(is_admin)
_broadcast_active = False

_ADS_HEADER = "📢 **#ads #promo**\n━━━━━━━━━━━━━━━━━━━━━━\n\n"

_HELP = (
    "📢 **Broadcast Help**\n\n"
    "━━━━━━━━━━━━━━━━━━━━━━\n"
    "Reply to any message with one of:\n\n"
    "`/broadcast` — Normal: send to all users\n"
    "`/broadcast pin` — Send + pin in every chat\n"
    "`/broadcast test` — Preview only (sends to you, deletes in 5s)\n"
    "`/broadcast ads` — Adds **#ads #promo** header on top\n"
    "━━━━━━━━━━━━━━━━━━━━━━"
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Core sender
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def _send_one(client: Client, source: Message, chat_id: int,
                    mode: str, ads_header: str = "") -> None:
    sent = None

    if ads_header and source.text:
        sent = await client.send_message(chat_id, ads_header + source.text)
    elif ads_header and source.caption:
        new_caption = ads_header + (source.caption or "")
        sent = await source.copy(chat_id, caption=new_caption)
    elif ads_header:
        await client.send_message(chat_id, ads_header.rstrip("\n"))
        sent = await source.copy(chat_id)
    else:
        sent = await source.copy(chat_id)

    if sent and mode == "pin":
        try:
            await client.pin_chat_message(
                chat_id, sent.id, disable_notification=True, both_sides=False
            )
        except Exception:
            pass

    if sent and mode == "test":
        await asyncio.sleep(5)
        try:
            await client.delete_messages(chat_id, sent.id)
        except Exception:
            pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /broadcast command
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_message(filters.command("broadcast") & admin_filter)
async def broadcast_command(client: Client, message: Message):
    global _broadcast_active

    args = message.command[1].lower() if len(message.command) > 1 else ""
    replied = message.reply_to_message

    if not replied:
        await message.reply_text(_HELP)
        return

    if args not in ("", "pin", "test", "ads"):
        await message.reply_text(
            "❌ Unknown mode.\n\n" + _HELP
        )
        return

    mode = args or "normal"
    ads_header = _ADS_HEADER if mode == "ads" else ""

    # ── Test mode: send only to admin, then delete ──
    if mode == "test":
        notice = await message.reply_text(
            "🧪 **Test mode** — sending to you only, deletes in 5 seconds…"
        )
        await _send_one(client, replied, message.chat.id, mode="test", ads_header=ads_header)
        try:
            await notice.delete()
        except Exception:
            pass
        await message.reply_text("✅ Test done — message was auto-deleted.")
        return

    # ── Full broadcast ──
    if _broadcast_active:
        await message.reply_text("⚠️ A broadcast is already running. Wait for it to finish.")
        return

    user_ids = await users_db.get_all_user_ids()
    if not user_ids:
        await message.reply_text("No users to broadcast to.")
        return

    mode_label = {"normal": "📢 Normal", "pin": "📌 Pin", "ads": "📣 Ads"}[mode]
    status_msg = await message.reply_text(
        f"{mode_label} **Broadcast Starting…**\n"
        f"👥 Total users: **{len(user_ids)}**"
    )

    _broadcast_active = True
    sent = failed = 0

    try:
        for i in range(0, len(user_ids), BROADCAST_CHUNK):
            chunk = user_ids[i:i + BROADCAST_CHUNK]
            tasks = [
                _send_one(client, replied, uid, mode=mode, ads_header=ads_header)
                for uid in chunk
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for r in results:
                if isinstance(r, Exception):
                    failed += 1
                else:
                    sent += 1

            if i % (BROADCAST_CHUNK * 5) == 0 and i > 0:
                try:
                    await status_msg.edit_text(
                        f"{mode_label} **Broadcasting…**\n"
                        f"✅ Sent: **{sent}** | ❌ Failed: **{failed}**"
                    )
                except Exception:
                    pass

            await asyncio.sleep(1)
    finally:
        _broadcast_active = False

    await status_msg.edit_text(
        f"✅ **Broadcast Complete!**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Mode: {mode_label}\n"
        f"✅ Sent: **{sent}**\n"
        f"❌ Failed: **{failed}**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
