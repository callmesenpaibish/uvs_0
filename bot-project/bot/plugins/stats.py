from pyrogram import Client, filters
from pyrogram.types import Message

from bot.database import users_db, premium_db, tokens_db, files_db
from bot.database.connection import get_db
from bot.config import ADMIN_IDS
from bot.utils.helpers import humanbytes


def is_admin(_, __, message):
    return message.from_user and message.from_user.id in ADMIN_IDS


admin_filter = filters.create(is_admin)


@Client.on_message(filters.command("stats") & admin_filter)
async def stats_command(client: Client, message: Message):
    msg = await message.reply_text("📊 Fetching stats...")

    total_users = await users_db.total_users()
    total_premium = await premium_db.total_premium()
    tokens_today = await tokens_db.tokens_today()
    total_files = await files_db.total_files()

    db = get_db()
    try:
        stats = await db.command("dbStats")
        db_size = humanbytes(stats.get("dataSize", 0))
    except Exception:
        db_size = "N/A"

    await msg.edit_text(
        "📊 **Bot Statistics**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Total Users: **{total_users:,}**\n"
        f"💎 Premium Users: **{total_premium:,}**\n"
        f"🔑 Tokens Today: **{tokens_today:,}**\n"
        f"📁 Total Files: **{total_files:,}**\n"
        f"🗄️ DB Size: **{db_size}**\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
