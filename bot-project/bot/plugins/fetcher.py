import asyncio
import os
import tempfile
from urllib.parse import quote
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.errors import PeerIdInvalid

from bot.database import settings_db, files_db
from bot.utils.thumbnail_gen import resize_thumbnail
from bot.utils.helpers import humanbytes
from bot.config import ADMIN_IDS, POST_DELAY_SECONDS

_fetch_lock = asyncio.Lock()
_fetching_active = False
_thumb_pending: dict[int, str] = {}


async def _resolve_channel(client: Client, identifier: str | int) -> int:
    """
    Resolve a channel identifier (username, invite link, or numeric ID) to
    a usable Pyrogram chat id.

    - @username / t.me/xxx: Pyrogram resolves via contacts.ResolveUsername — always works.
    - Numeric string / int (-100xxx): tries get_chat with the int; fails if the bot
      has never interacted with the channel (no access_hash in session).

    Returns the resolved integer chat id.
    """
    identifier = str(identifier).strip()

    # If it looks like a numeric ID, convert to int for Pyrogram
    if identifier.lstrip("-").isdigit():
        peer = int(identifier)
    else:
        peer = identifier  # @username or https://t.me/… — Pyrogram resolves these

    try:
        chat = await client.get_chat(peer)
        return chat.id
    except Exception as e:
        err = str(e)
        # Give a clear actionable message instead of raw Pyrogram error
        if "PEER_ID_INVALID" in err or "peer id" in err.lower():
            raise ValueError(
                "Cannot reach the public channel. "
                "Make sure the bot is an admin there, then update the channel setting "
                "using its @username (e.g. @mychannel) instead of the numeric ID."
            ) from e
        raise


def is_admin(_, __, message):
    return message.from_user and message.from_user.id in ADMIN_IDS


admin_filter = filters.create(is_admin)


def _approval_buttons(file_unique_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve & Post", callback_data=f"approve_post_{file_unique_id}")],
        [
            InlineKeyboardButton("🖼️ Change Thumb", callback_data=f"change_thumb_{file_unique_id}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"reject_post_{file_unique_id}"),
        ],
    ])


async def process_and_post(client: Client, message: Message, bin_channel_id: int):
    public_channel = await settings_db.get_public_channel()
    if not public_channel:
        return

    approval_mode = await settings_db.get_approval_queue()
    batch_mode = await settings_db.get_batch_mode()

    media = (
        message.video or message.document or
        message.audio or message.photo
    )
    if not media:
        return

    file_id = getattr(media, "file_id", None)
    file_unique_id = getattr(media, "file_unique_id", None)
    file_name = getattr(media, "file_name", None) or f"file_{message.id}"
    file_size = getattr(media, "file_size", 0) or 0
    file_type = type(media).__name__.lower()
    caption = message.caption or file_name

    await files_db.add_file(
        file_id=file_id,
        file_unique_id=file_unique_id,
        file_type=file_type,
        file_name=file_name,
        file_size=file_size,
        msg_id=message.id,
        channel_id=bin_channel_id,
        caption=caption,
    )

    if batch_mode:
        await settings_db.add_batch_file({
            "file_unique_id": file_unique_id,
            "file_name": file_name,
            "file_size": file_size,
            "file_type": file_type,
            "msg_id": message.id,
            "channel_id": bin_channel_id,
        })
        return

    await _create_post(client, message, file_unique_id, file_name, file_size,
                       file_type, public_channel, approval_mode)


def _post_buttons(start_link: str) -> InlineKeyboardMarkup:
    share_url = f"https://t.me/share/url?url={start_link}"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("WATCH NOW", url=start_link)],
        [InlineKeyboardButton("SHARE", url=share_url)],
    ])


async def _create_post(client: Client, source_msg: Message, file_unique_id: str,
                       file_name: str, file_size: int, file_type: str,
                       public_channel, approval_mode: bool):
    bot_info = await client.get_me()
    start_link = f"https://t.me/{bot_info.username}?start=file_{file_unique_id}"
    size_str = humanbytes(file_size)
    icon = "🎥" if file_type == "video" else "📄" if file_type == "document" else "🎵" if file_type == "audio" else "🖼️"

    post_caption = (
        f"{icon} **{file_name}**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Size: `{size_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )

    pub_buttons = _post_buttons(start_link)

    thumb_path = None
    if file_type == "video" and source_msg.video and source_msg.video.thumbs:
        try:
            thumb = source_msg.video.thumbs[0]
            tmp = tempfile.mktemp(suffix=".jpg")
            await client.download_media(thumb.file_id, file_name=tmp)
            thumb_path = resize_thumbnail(tmp)
        except Exception:
            pass

    try:
        if approval_mode:
            draft_caption = (
                f"📋 **Draft for Approval**\n\n"
                f"{icon} **{file_name}**\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📦 Size: `{size_str}`\n"
                f"🆔 ID: `{file_unique_id[:12]}...`\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Approve to post, or change the thumbnail first."
            )
            draft_buttons = _approval_buttons(file_unique_id)

            for admin_id in ADMIN_IDS:
                try:
                    if thumb_path and os.path.exists(thumb_path):
                        await client.send_photo(admin_id, thumb_path, caption=draft_caption, reply_markup=draft_buttons)
                    else:
                        await client.send_message(admin_id, draft_caption, reply_markup=draft_buttons)
                except Exception:
                    pass
        else:
            # Resolve peer so Pyrogram can route to the channel correctly
            resolved_ch = await _resolve_channel(client, public_channel)
            if thumb_path and os.path.exists(thumb_path):
                await client.send_photo(resolved_ch, thumb_path, caption=None, reply_markup=pub_buttons)
            else:
                await client.send_message(resolved_ch, "‌", reply_markup=pub_buttons)
    finally:
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Approval callbacks
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_callback_query(filters.regex(r"^approve_post_(.+)$"))
async def approve_post_cb(client: Client, cb):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Not authorized.", show_alert=True)
        return

    file_uid = cb.data.split("_", 2)[2]
    public_channel = await settings_db.get_public_channel()
    if not public_channel:
        await cb.answer("❌ Public channel not configured.", show_alert=True)
        return

    file_doc = await files_db.get_file(file_uid)
    if not file_doc:
        await cb.answer("❌ File not found.", show_alert=True)
        return

    bot_info = await client.get_me()
    start_link = f"https://t.me/{bot_info.username}?start=file_{file_uid}"
    size_str = humanbytes(file_doc.get("file_size", 0))
    ft = file_doc["file_type"]
    icon = "🎥" if ft == "video" else "📄" if ft == "document" else "🎵" if ft == "audio" else "🖼️"

    post_caption = (
        f"{icon} **{file_doc['file_name']}**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Size: `{size_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    pub_buttons = _post_buttons(start_link)

    try:
        resolved_ch = await _resolve_channel(client, public_channel)
        if cb.message.photo:
            await client.send_photo(resolved_ch, cb.message.photo.file_id, caption=None, reply_markup=pub_buttons)
        else:
            await client.send_message(resolved_ch, "‌", reply_markup=pub_buttons)
        await cb.message.edit_reply_markup(None)
        await cb.answer("✅ Posted to channel!", show_alert=True)
    except Exception as e:
        await cb.answer(f"❌ Error: {e}", show_alert=True)


@Client.on_callback_query(filters.regex(r"^reject_post_(.+)$"))
async def reject_post_cb(client: Client, cb):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Not authorized.", show_alert=True)
        return
    _thumb_pending.pop(cb.from_user.id, None)
    await cb.message.edit_reply_markup(None)
    await cb.answer("❌ Rejected.", show_alert=False)


@Client.on_callback_query(filters.regex(r"^change_thumb_(.+)$"))
async def change_thumb_cb(client: Client, cb):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Not authorized.", show_alert=True)
        return

    file_uid = cb.data.split("_", 2)[2]
    _thumb_pending[cb.from_user.id] = file_uid

    cancel_btn = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Approve & Post", callback_data=f"approve_post_{file_uid}")],
        [InlineKeyboardButton("❌ Cancel Thumb Change", callback_data=f"cancel_thumb_{file_uid}")],
    ])
    try:
        if cb.message.photo:
            await cb.message.edit_caption(
                cb.message.caption.split("━━")[0].strip() +
                "\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
                "📸 **Send me a photo now** to use as the new thumbnail.",
                reply_markup=cancel_btn,
            )
        else:
            await cb.message.edit_text(
                cb.message.text.split("━━")[0].strip() +
                "\n\n━━━━━━━━━━━━━━━━━━━━━━\n"
                "📸 **Send me a photo now** to use as the new thumbnail.",
                reply_markup=cancel_btn,
            )
    except Exception:
        await cb.message.edit_reply_markup(cancel_btn)
    await cb.answer("Send me a photo!", show_alert=False)


@Client.on_callback_query(filters.regex(r"^cancel_thumb_(.+)$"))
async def cancel_thumb_cb(client: Client, cb):
    if cb.from_user.id not in ADMIN_IDS:
        await cb.answer("Not authorized.", show_alert=True)
        return
    file_uid = cb.data.split("_", 2)[2]
    _thumb_pending.pop(cb.from_user.id, None)
    await cb.message.edit_reply_markup(_approval_buttons(file_uid))
    await cb.answer("Cancelled.", show_alert=False)


@Client.on_message(filters.photo & filters.private & admin_filter)
async def admin_photo_input(client: Client, message: Message):
    admin_id = message.from_user.id
    if admin_id not in _thumb_pending:
        return

    file_uid = _thumb_pending.pop(admin_id)
    public_channel = await settings_db.get_public_channel()
    if not public_channel:
        await message.reply_text("❌ Public channel not configured.")
        return

    file_doc = await files_db.get_file(file_uid)
    if not file_doc:
        await message.reply_text("❌ File not found in database.")
        return

    bot_info = await client.get_me()
    start_link = f"https://t.me/{bot_info.username}?start=file_{file_uid}"
    size_str = humanbytes(file_doc.get("file_size", 0))
    ft = file_doc["file_type"]
    icon = "🎥" if ft == "video" else "📄" if ft == "document" else "🎵" if ft == "audio" else "🖼️"

    post_caption = (
        f"{icon} **{file_doc['file_name']}**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Size: `{size_str}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    pub_buttons = _post_buttons(start_link)

    thumb_path = None
    try:
        tmp = tempfile.mktemp(suffix=".jpg")
        await client.download_media(message.photo.file_id, file_name=tmp)
        thumb_path = resize_thumbnail(tmp)
        resolved_ch = await _resolve_channel(client, public_channel)
        await client.send_photo(resolved_ch, thumb_path, caption=None, reply_markup=pub_buttons)
        await message.reply_text("✅ **Posted to channel with your custom thumbnail!**")
    except Exception as e:
        await message.reply_text(f"❌ Failed to post: {e}")
    finally:
        if thumb_path and os.path.exists(thumb_path):
            try:
                os.remove(thumb_path)
            except Exception:
                pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Monitor Bin Channel
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_message(filters.channel)
async def bin_monitor(client: Client, message: Message):
    bin_channel = await settings_db.get_bin_channel()
    if not bin_channel:
        return

    auto_fetch = await settings_db.get_auto_fetch()
    if not auto_fetch:
        return

    if message.chat.id != bin_channel:
        return

    await process_and_post(client, message, bin_channel)
    await asyncio.sleep(POST_DELAY_SECONDS)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Resume Fetch (with improved error handling)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def resume_fetch_task(client: Client, from_msg_id: int):
    global _fetching_active
    bin_channel = await settings_db.get_bin_channel()
    if not bin_channel:
        return

    _fetching_active = True
    msg_id = from_msg_id
    empty_streak = 0

    try:
        while empty_streak < 50:
            auto_fetch = await settings_db.get_auto_fetch()
            if not auto_fetch or not _fetching_active:
                break
            try:
                result = await client.get_messages(bin_channel, msg_id)
                # Handle both single message and list responses
                if isinstance(result, list):
                    msg = result[0] if result else None
                else:
                    msg = result

                if msg is None:
                    empty_streak += 1
                elif hasattr(msg, 'empty') and msg.empty:
                    empty_streak += 1
                elif msg.media:
                    empty_streak = 0
                    await process_and_post(client, msg, bin_channel)
                    await asyncio.sleep(POST_DELAY_SECONDS)
                else:
                    empty_streak = 0
            except Exception as e:
                print(f"[Fetcher] Error at msg {msg_id}: {e}")
                empty_streak += 1
            msg_id += 1
            await asyncio.sleep(0.5)
    finally:
        _fetching_active = False
