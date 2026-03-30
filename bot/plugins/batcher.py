from pyrogram import Client
from bot.database import settings_db, files_db
from bot.utils.helpers import humanbytes
from bot.config import POST_DELAY_SECONDS
from bot.plugins.fetcher import _resolve_channel


async def post_batch_to_channel(client: Client):
    batch_files = await settings_db.get_batch_files()
    if not batch_files:
        return False, "Batch is empty."

    public_channel = await settings_db.get_public_channel()
    if not public_channel:
        return False, "Public channel not configured."

    # Resolve peer so Pyrogram can route to the channel correctly
    try:
        resolved_ch = await _resolve_channel(client, public_channel)
    except Exception as e:
        return False, str(e)

    bot_info = await client.get_me()
    lines = []
    for i, f in enumerate(batch_files, 1):
        uid = f["file_unique_id"]
        name = f["file_name"]
        size = humanbytes(f.get("file_size", 0))
        link = f"https://t.me/{bot_info.username}?start=file_{uid}"
        icon = "🎥" if f["file_type"] == "video" else "📄" if f["file_type"] == "document" else "🎵"
        lines.append(f"{i}. {icon} [{name}]({link}) • `{size}`")

    post_text = (
        f"📦 **Batch Upload — {len(batch_files)} Files**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        + "\n".join(lines) +
        f"\n━━━━━━━━━━━━━━━━━━━━━━"
    )

    try:
        await client.send_message(resolved_ch, post_text, disable_web_page_preview=True)
        await settings_db.clear_batch_files()
        return True, f"Posted {len(batch_files)} files."
    except Exception as e:
        return False, str(e)
