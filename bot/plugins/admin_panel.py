import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from bot.database import settings_db, premium_db, users_db, plans_db
from bot.config import ADMIN_IDS
from bot.plugins.fetcher import resume_fetch_task
from bot.plugins.batcher import post_batch_to_channel


def is_admin(_, __, update):
    user = getattr(update, "from_user", None)
    return user and user.id in ADMIN_IDS


admin_filter = filters.create(is_admin)
_pending: dict[int, str] = {}


@Client.on_message(filters.command("admin") & filters.private & admin_filter)
async def admin_panel(client: Client, message: Message):
    await _send_main_panel(client, message.chat.id)


async def _send_main_panel(client: Client, chat_id: int, msg=None):
    auto_fetch = await settings_db.get_auto_fetch()
    batch_mode = await settings_db.get_batch_mode()
    approval = await settings_db.get_approval_queue()
    token_required = await settings_db.get_token_required()
    daily_limit_enabled = await settings_db.get_daily_limit_enabled()
    daily_limit_count = await settings_db.get_daily_limit_count()
    auto_delete_time = await settings_db.get_auto_delete_time()

    fetch_icon = "🟢" if auto_fetch else "🔴"
    batch_icon = "📦 ON" if batch_mode else "📦 OFF"
    approval_icon = "✅ ON" if approval else "❌ OFF"
    token_icon = "🔑 ON" if token_required else "🔓 OFF"
    dl_icon = f"📊 {daily_limit_count} files" if daily_limit_enabled else "📊 OFF"
    if auto_delete_time <= 0:
        ad_icon = "🗑️ OFF"
    elif auto_delete_time < 60:
        ad_icon = f"🗑️ {auto_delete_time}s"
    else:
        ad_icon = f"🗑️ {auto_delete_time // 60}m"

    text = (
        "⚙️ **Admin Control Panel**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Manage all bot settings from here.\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("📡 Channels", callback_data="ap_channels"),
         InlineKeyboardButton("📢 FSub", callback_data="ap_fsub")],
        [InlineKeyboardButton(f"{fetch_icon} Auto-Fetch", callback_data="ap_toggle_fetch"),
         InlineKeyboardButton("🔄 Resume Fetch", callback_data="ap_resume_fetch")],
        [InlineKeyboardButton(f"{batch_icon}", callback_data="ap_toggle_batch"),
         InlineKeyboardButton("✅ Post Batch", callback_data="ap_post_batch")],
        [InlineKeyboardButton(f"🔔 Approval: {approval_icon}", callback_data="ap_toggle_approval"),
         InlineKeyboardButton(f"{token_icon} Token Gate", callback_data="ap_toggle_token")],
        [InlineKeyboardButton(f"{dl_icon} Daily Limit", callback_data="ap_daily_limit"),
         InlineKeyboardButton("🔗 Shortlink", callback_data="ap_shortlink")],
        [InlineKeyboardButton(f"{ad_icon} Auto-Delete", callback_data="ap_auto_delete"),
         InlineKeyboardButton("🔗 Contact Link", callback_data="ap_contact_link")],
        [InlineKeyboardButton("💎 Premium", callback_data="ap_premium"),
         InlineKeyboardButton("📦 Plans", callback_data="ap_plans")],
        [InlineKeyboardButton("👤 Users", callback_data="ap_users"),
         InlineKeyboardButton("📊 Stats", callback_data="ap_stats_btn")],
        [InlineKeyboardButton("📢 Broadcast", callback_data="ap_broadcast")],
    ])

    if msg:
        try:
            await msg.edit_text(text, reply_markup=buttons)
        except Exception:
            await client.send_message(chat_id, text, reply_markup=buttons)
    else:
        await client.send_message(chat_id, text, reply_markup=buttons)


@Client.on_callback_query(filters.regex("^ap_") & admin_filter)
async def admin_panel_callback(client: Client, cb: CallbackQuery):
    data = cb.data
    chat_id = cb.message.chat.id

    if data == "ap_back":
        await cb.answer()
        await _send_main_panel(client, chat_id, cb.message)
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CHANNELS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_channels":
        await cb.answer()
        bin_ch = await settings_db.get_bin_channel()
        pub_ch = await settings_db.get_public_channel()
        text = (
            "📡 **Channel Configuration**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📥 Bin Channel: `{bin_ch or 'Not set'}`\n"
            f"📢 Public Channel: `{pub_ch or 'Not set'}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ **Public Channel:** Use `@username` (recommended) or numeric ID.\n"
            "The bot must be an **admin** in both channels."
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Set Bin Channel", callback_data="ap_set_bin"),
             InlineKeyboardButton("📢 Set Public Channel", callback_data="ap_set_pub")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_set_bin":
        await cb.answer()
        _pending[chat_id] = "set_bin"
        await cb.message.edit_text(
            "📥 **Set Bin Channel**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send the Bin Channel ID now.\n"
            "Example: `-100123456789`\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_channels")]]),
        )
        return

    if data == "ap_set_pub":
        await cb.answer()
        _pending[chat_id] = "set_pub"
        await cb.message.edit_text(
            "📢 **Set Public Channel**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send the Public Channel **username** or ID.\n\n"
            "✅ **Recommended:** `@yourchannelname`\n"
            "   (always works as long as bot is admin)\n\n"
            "⚠️ **Numeric ID** (`-100123456789`) only works\n"
            "   if the bot already interacted with the channel.\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_channels")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # FSUB
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_fsub":
        await cb.answer()
        channels = await settings_db.get_fsub_channels()
        ch_list = "\n".join(
            [f"• `{c['id']}` — **{c['mode']}** mode" for c in channels]
        ) or "None configured"
        text = (
            "📢 **Force Subscribe (FSub)**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"**Current Channels:**\n{ch_list}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "• `join` — Normal join button\n"
            "• `request` — Request to Join button"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add (Join)", callback_data="ap_fsub_add_join"),
             InlineKeyboardButton("➕ Add (Request)", callback_data="ap_fsub_add_request")],
            [InlineKeyboardButton("➖ Remove Channel", callback_data="ap_fsub_remove")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_fsub_add_join":
        await cb.answer()
        _pending[chat_id] = "fsub_add_join"
        await cb.message.edit_text(
            "➕ **Add FSub Channel (Join Mode)**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send the Channel ID to add as forced-join.\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_fsub")]]),
        )
        return

    if data == "ap_fsub_add_request":
        await cb.answer()
        _pending[chat_id] = "fsub_add_request"
        await cb.message.edit_text(
            "➕ **Add FSub Channel (Request Mode)**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send the Channel ID to add as request-to-join.\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_fsub")]]),
        )
        return

    if data == "ap_fsub_remove":
        await cb.answer()
        _pending[chat_id] = "fsub_remove"
        await cb.message.edit_text(
            "➖ **Remove FSub Channel**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send the Channel ID to remove from FSub.\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_fsub")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # AUTO-FETCH
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_toggle_fetch":
        current = await settings_db.get_auto_fetch()
        new_state = not current
        await settings_db.set_auto_fetch(new_state)
        await cb.answer(f"Auto-Fetch {'ON' if new_state else 'OFF'}", show_alert=False)
        await _send_main_panel(client, chat_id, cb.message)
        return

    if data == "ap_resume_fetch":
        await cb.answer()
        _pending[chat_id] = "resume_fetch"
        await cb.message.edit_text(
            "🔄 **Resume Fetching**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send a message ID to start fetching from (e.g. `12345`).\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_back")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BATCH
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_toggle_batch":
        current = await settings_db.get_batch_mode()
        new_state = not current
        await settings_db.set_batch_mode(new_state)
        await cb.answer(f"Batch Mode {'ON' if new_state else 'OFF'}", show_alert=False)
        await _send_main_panel(client, chat_id, cb.message)
        return

    if data == "ap_post_batch":
        await cb.answer("⏳ Posting batch...", show_alert=False)
        success, result_msg = await post_batch_to_channel(client)
        icon = "✅" if success else "❌"
        await cb.message.reply_text(f"{icon} **Batch Result:** {result_msg}")
        if success:
            await settings_db.set_batch_mode(False)
            await _send_main_panel(client, chat_id, cb.message)
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # APPROVAL QUEUE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_toggle_approval":
        current = await settings_db.get_approval_queue()
        new_state = not current
        await settings_db.set_approval_queue(new_state)
        await cb.answer(f"Approval Queue {'ON' if new_state else 'OFF'}", show_alert=False)
        await _send_main_panel(client, chat_id, cb.message)
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # TOKEN GATE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_toggle_token":
        current = await settings_db.get_token_required()
        new_state = not current
        await settings_db.set_token_required(new_state)
        label = "ON — users must verify a shortlink token" if new_state else "OFF — files delivered directly"
        await cb.answer(f"Token Gate {label}", show_alert=True)
        await _send_main_panel(client, chat_id, cb.message)
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # DAILY LIMIT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_daily_limit":
        await cb.answer()
        enabled = await settings_db.get_daily_limit_enabled()
        count = await settings_db.get_daily_limit_count()
        status = f"✅ ON — **{count} files** per token verification" if enabled else "❌ OFF — unlimited after token"
        text = (
            "📊 **Daily Download Limit**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: {status}\n\n"
            "When enabled, each token verification allows the user to download\n"
            "a set number of files. After the limit is hit, they must verify again.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        toggle_label = "✅ Turn OFF Limit" if enabled else "❌ Turn ON Limit"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle_label, callback_data="ap_toggle_dlimit")],
            [InlineKeyboardButton("🔢 Set File Count", callback_data="ap_set_dlimit_count")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_toggle_dlimit":
        current = await settings_db.get_daily_limit_enabled()
        new_state = not current
        await settings_db.set_daily_limit_enabled(new_state)
        await cb.answer(f"Daily Limit {'ON' if new_state else 'OFF'}", show_alert=False)
        enabled = new_state
        count = await settings_db.get_daily_limit_count()
        status = f"✅ ON — **{count} files** per token verification" if enabled else "❌ OFF — unlimited after token"
        text = (
            "📊 **Daily Download Limit**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: {status}\n\n"
            "When enabled, each token verification allows the user to download\n"
            "a set number of files. After the limit is hit, they must verify again.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        toggle_label = "✅ Turn OFF Limit" if enabled else "❌ Turn ON Limit"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle_label, callback_data="ap_toggle_dlimit")],
            [InlineKeyboardButton("🔢 Set File Count", callback_data="ap_set_dlimit_count")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_set_dlimit_count":
        await cb.answer()
        _pending[chat_id] = "set_dlimit_count"
        current_count = await settings_db.get_daily_limit_count()
        await cb.message.edit_text(
            "🔢 **Set Daily File Limit**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Current limit: **{current_count} files** per verification\n\n"
            "Send the number of files a user can download after each token verification.\n"
            "Example: `5`, `10`, `20`\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_daily_limit")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # AUTO DELETE
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_auto_delete":
        await cb.answer()
        secs = await settings_db.get_auto_delete_time()
        _t = f"{secs}s" if secs < 60 else f"{secs // 60}m"
        status = f"✅ ON — files deleted after **{_t}**" if secs > 0 else "❌ OFF — files are never deleted"
        text = (
            "🗑️ **Auto-Delete Files**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: {status}\n\n"
            "When enabled, every file sent to a user is automatically\n"
            "deleted after the set time. The user receives a notice\n"
            "to re-download from the channel link.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        toggle_label = "✅ Turn OFF" if secs > 0 else "❌ Turn ON"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle_label, callback_data="ap_toggle_autodel")],
            [InlineKeyboardButton("⏱️ Set Delete Time", callback_data="ap_set_autodel_time")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_toggle_autodel":
        secs = await settings_db.get_auto_delete_time()
        if secs > 0:
            await settings_db.set_auto_delete_time(0)
            new_secs = 0
        else:
            new_secs = 10 * 60
            await settings_db.set_auto_delete_time(new_secs)
        await cb.answer(f"Auto-Delete {'ON (10 min default)' if new_secs else 'OFF'}", show_alert=False)
        _t = f"{new_secs}s" if new_secs < 60 else f"{new_secs // 60}m"
        status = f"✅ ON — files deleted after **{_t}**" if new_secs > 0 else "❌ OFF — files are never deleted"
        text = (
            "🗑️ **Auto-Delete Files**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: {status}\n\n"
            "When enabled, every file sent to a user is automatically\n"
            "deleted after the set time. The user receives a notice\n"
            "to re-download from the channel link.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        toggle_label = "✅ Turn OFF" if new_secs > 0 else "❌ Turn ON"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton(toggle_label, callback_data="ap_toggle_autodel")],
            [InlineKeyboardButton("⏱️ Set Delete Time", callback_data="ap_set_autodel_time")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_set_autodel_time":
        await cb.answer()
        _pending[chat_id] = "set_auto_delete_time"
        secs = await settings_db.get_auto_delete_time()
        current_str = f"{secs} seconds" if secs > 0 else "OFF"
        await cb.message.edit_text(
            "⏱️ **Set Auto-Delete Time**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Current: **{current_str}**\n\n"
            "Send the number of **seconds** after which files should be deleted.\n\n"
            "💡 Examples:\n"
            "`10` — 10 seconds (for testing)\n"
            "`300` — 5 minutes\n"
            "`600` — 10 minutes\n"
            "`3600` — 1 hour\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_auto_delete")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # SHORTLINK
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_shortlink":
        await cb.answer()
        api, base_url = await settings_db.get_shortlink_config()
        text = (
            "🔗 **Shortlink Configuration**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 Base URL: `{base_url or 'Not set'}`\n"
            f"🔑 API Key: `{'****' + api[-4:] if len(api) > 4 else ('Set' if api else 'Not set')}`\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "Used to generate token verification links.\n"
            "Token Gate must also be **ON** to use this."
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Set Base URL", callback_data="ap_sl_url"),
             InlineKeyboardButton("🔑 Set API Key", callback_data="ap_sl_api")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_sl_url":
        await cb.answer()
        _pending[chat_id] = "sl_url"
        await cb.message.edit_text(
            "🌐 **Set Shortlink Base URL**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send the base URL of your shortlink service.\n"
            "Example: `https://arolinks.com`\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_shortlink")]]),
        )
        return

    if data == "ap_sl_api":
        await cb.answer()
        _pending[chat_id] = "sl_api"
        await cb.message.edit_text(
            "🔑 **Set Shortlink API Key**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send your AroLinks (or compatible) API key.\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_shortlink")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PREMIUM
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_premium":
        await cb.answer()
        text = (
            "💎 **Premium Management**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Premium users skip all token & limit gates.\n\n"
            "Use the buttons below to manage premium users.\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Premium", callback_data="ap_add_prem"),
             InlineKeyboardButton("➖ Remove Premium", callback_data="ap_rem_prem")],
            [InlineKeyboardButton("📋 List Premium", callback_data="ap_list_prem")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_add_prem":
        await cb.answer()
        _pending[chat_id] = "add_prem"
        await cb.message.edit_text(
            "➕ **Add Premium**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send: `user_id days`\n"
            "Example: `123456789 30`\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_premium")]]),
        )
        return

    if data == "ap_rem_prem":
        await cb.answer()
        _pending[chat_id] = "rem_prem"
        await cb.message.edit_text(
            "➖ **Remove Premium**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send the user ID to remove premium from.\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_premium")]]),
        )
        return

    if data == "ap_list_prem":
        await cb.answer()
        premium_users = await premium_db.get_all_premium()
        if not premium_users:
            await cb.message.edit_text(
                "💎 **Premium Users**\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━\n"
                "No active premium users.\n"
                "━━━━━━━━━━━━━━━━━━━━━━",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ap_premium")]]),
            )
            return

        from bot.utils.helpers import readable_date
        lines = []
        for p in premium_users[:20]:
            lines.append(f"• `{p['user_id']}` — Expires: **{readable_date(p['expires_at'])}**")

        await cb.message.edit_text(
            "💎 **Active Premium Users**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            + "\n".join(lines) +
            "\n━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ap_premium")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # PLANS MANAGEMENT
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_plans":
        await cb.answer()
        plans = await plans_db.get_plans()
        if not plans:
            plan_text = "No plans configured yet."
        else:
            plan_text = "\n".join(
                [f"• **{p['name']}** — ₹{p['price']} / {p['duration_days']} days" for p in plans]
            )
        text = (
            "📦 **Premium Plans**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{plan_text}\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        upi_qr = await settings_db.get_upi_qr_file_id()
        qr_status = "✅ Set" if upi_qr else "❌ Not set"
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Plan", callback_data="ap_add_plan"),
             InlineKeyboardButton("➖ Delete Plan", callback_data="ap_del_plan")],
            [InlineKeyboardButton(f"📷 UPI QR: {qr_status}", callback_data="ap_upi_qr")],
            [InlineKeyboardButton("🔙 Back", callback_data="ap_back")],
        ])
        await cb.message.edit_text(text, reply_markup=buttons)
        return

    if data == "ap_upi_qr":
        await cb.answer()
        upi_qr = await settings_db.get_upi_qr_file_id()
        qr_status = "✅ A QR image is set." if upi_qr else "❌ No QR image set."
        _pending[chat_id] = "set_upi_qr"
        await cb.message.edit_text(
            "📷 **UPI QR Code**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Status: {qr_status}\n\n"
            "Send a **photo** of your UPI QR code now.\n"
            "It will be shown to users when they choose a plan.\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Remove QR", callback_data="ap_del_upi_qr")],
                [InlineKeyboardButton("🔙 Cancel", callback_data="ap_plans")],
            ]),
        )
        return

    if data == "ap_del_upi_qr":
        await settings_db.delete_upi_qr()
        _pending.pop(chat_id, None)
        await cb.answer("✅ UPI QR removed.", show_alert=True)
        await _send_main_panel(client, chat_id, cb.message)
        return

    if data == "ap_add_plan":
        await cb.answer()
        _pending[chat_id] = "add_plan"
        await cb.message.edit_text(
            "➕ **Add Premium Plans**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Send one plan per line in this format:\n\n"
            "`Name, Price, Days`\n\n"
            "**Example (you can paste all at once):**\n"
            "`Starter, 20, 12`\n"
            "`Basic, 30, 21`\n"
            "`Premium, 45, 35`\n"
            "`Elite, 99, 99`\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_plans")]]),
        )
        return

    if data == "ap_del_plan":
        await cb.answer()
        plans = await plans_db.get_plans()
        if not plans:
            await cb.answer("No plans to delete.", show_alert=True)
            return
        rows = []
        for p in plans:
            rows.append([InlineKeyboardButton(
                f"❌ {p['name']} (₹{p['price']})",
                callback_data=f"ap_delplan_{p['_id']}"
            )])
        rows.append([InlineKeyboardButton("🔙 Cancel", callback_data="ap_plans")])
        await cb.message.edit_text(
            "➖ **Delete Plan**\n\nSelect a plan to delete:",
            reply_markup=InlineKeyboardMarkup(rows)
        )
        return

    if data.startswith("ap_delplan_"):
        plan_id = data.split("_", 2)[2]
        await plans_db.delete_plan(plan_id)
        await cb.answer("✅ Plan deleted.", show_alert=True)
        await _send_main_panel(client, chat_id, cb.message)
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # USERS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_users":
        await cb.answer()
        total = await users_db.total_users()
        prem = await premium_db.total_premium()
        text = (
            "👤 **User Overview**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Total Users: **{total:,}**\n"
            f"💎 Premium Users: **{prem:,}**\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        await cb.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ap_back")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # STATS
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_stats_btn":
        await cb.answer()
        from bot.database import tokens_db, files_db
        total_users = await users_db.total_users()
        total_premium = await premium_db.total_premium()
        tokens_today = await tokens_db.tokens_today()
        total_files = await files_db.total_files()
        text = (
            "📊 **Bot Statistics**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👥 Total Users: **{total_users:,}**\n"
            f"💎 Premium Users: **{total_premium:,}**\n"
            f"🔑 Tokens Today: **{tokens_today:,}**\n"
            f"📁 Total Files: **{total_files:,}**\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
        await cb.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ap_back")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # BROADCAST
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_broadcast":
        await cb.answer()
        await cb.message.edit_text(
            "📢 **Broadcast**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "Reply to any message with one of these commands:\n\n"
            "• `/broadcast` — Normal: send to all users\n"
            "• `/broadcast pin` — Send + **pin** in every chat\n"
            "• `/broadcast test` — Preview only (sends to you, deletes in 5 s)\n"
            "• `/broadcast ads` — Adds **#ads #promo** header on top\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="ap_back")]]),
        )
        return

    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # CONTACT LINK
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    if data == "ap_contact_link":
        await cb.answer()
        current_link = await settings_db.get_contact_link()
        _pending[chat_id] = "contact_link"
        await cb.message.edit_text(
            "🔗 **Set Contact/Support Link**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Current: `{current_link}`\n\n"
            "Send the new contact link (e.g. `https://t.me/yoursupportchat`).\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Cancel", callback_data="ap_back")]]),
        )
        return


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Text input handler for pending admin actions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_not_command = filters.create(lambda _, __, m: bool(m.text and not m.text.startswith("/")))

@Client.on_message(filters.text & _not_command & filters.private & admin_filter)
async def admin_text_input(client: Client, message: Message):
    chat_id = message.chat.id
    pending = _pending.get(chat_id)
    if not pending:
        return

    text = message.text.strip()

    if pending == "set_bin":
        try:
            ch_id = int(text)
            await settings_db.set_bin_channel(ch_id)
            del _pending[chat_id]
            await message.reply_text(f"✅ Bin Channel set to `{ch_id}`.")
        except ValueError:
            await message.reply_text("❌ Invalid channel ID. Must be a number.")
        return

    if pending == "set_pub":
        # Accept @username, https://t.me/xxx, or numeric ID
        value = text.strip()
        if not value:
            await message.reply_text("❌ Please send a valid @username or channel ID.")
            return
        await settings_db.set_public_channel(value)
        del _pending[chat_id]
        await message.reply_text(
            f"✅ Public Channel set to `{value}`.\n\n"
            f"Make sure the bot is an **admin** in that channel with 'Post Messages' permission."
        )
        return

    if pending == "fsub_add_join":
        try:
            ch_id = int(text)
            await settings_db.add_fsub_channel(ch_id, "join")
            del _pending[chat_id]
            await message.reply_text(f"✅ Added `{ch_id}` as FSub (Join mode).")
        except ValueError:
            await message.reply_text("❌ Invalid channel ID.")
        return

    if pending == "fsub_add_request":
        try:
            ch_id = int(text)
            await settings_db.add_fsub_channel(ch_id, "request")
            del _pending[chat_id]
            await message.reply_text(f"✅ Added `{ch_id}` as FSub (Request mode).")
        except ValueError:
            await message.reply_text("❌ Invalid channel ID.")
        return

    if pending == "fsub_remove":
        try:
            ch_id = int(text)
            await settings_db.remove_fsub_channel(ch_id)
            del _pending[chat_id]
            await message.reply_text(f"✅ Removed `{ch_id}` from FSub.")
        except ValueError:
            await message.reply_text("❌ Invalid channel ID.")
        return

    if pending == "resume_fetch":
        try:
            msg_id = int(text)
            del _pending[chat_id]
            await message.reply_text(f"🔄 Starting fetch from message `{msg_id}`...")
            asyncio.create_task(resume_fetch_task(client, msg_id))
        except ValueError:
            await message.reply_text("❌ Invalid message ID.")
        return

    if pending == "sl_url":
        api, _ = await settings_db.get_shortlink_config()
        await settings_db.set_shortlink_config(api, text)
        del _pending[chat_id]
        await message.reply_text(f"✅ Shortlink base URL set to `{text}`.")
        return

    if pending == "sl_api":
        _, base_url = await settings_db.get_shortlink_config()
        await settings_db.set_shortlink_config(text, base_url)
        del _pending[chat_id]
        await message.reply_text("✅ Shortlink API key updated.")
        return

    if pending == "set_dlimit_count":
        try:
            count = int(text)
            if count < 1:
                raise ValueError
            await settings_db.set_daily_limit_count(count)
            del _pending[chat_id]
            await message.reply_text(f"✅ Daily limit set to **{count} files** per verification.")
        except ValueError:
            await message.reply_text("❌ Invalid number. Send a positive integer.")
        return

    if pending == "set_auto_delete_time":
        try:
            secs = int(text)
            if secs < 1:
                raise ValueError
            await settings_db.set_auto_delete_time(secs)
            del _pending[chat_id]
            if secs < 60:
                time_str = f"{secs} second{'s' if secs != 1 else ''}"
            elif secs < 3600:
                m = secs // 60
                time_str = f"{m} minute{'s' if m != 1 else ''}"
            else:
                h = secs // 3600
                time_str = f"{h} hour{'s' if h != 1 else ''}"
            await message.reply_text(
                f"✅ Auto-delete set to **{time_str}**.\n"
                f"Files will be deleted {time_str} after delivery."
            )
        except ValueError:
            await message.reply_text("❌ Invalid number. Send seconds as a positive integer (e.g. `10`).")
        return

    if pending == "add_prem":
        try:
            parts = text.split()
            user_id = int(parts[0])
            days = int(parts[1])
            await premium_db.add_premium(user_id, days, added_by=chat_id)
            del _pending[chat_id]
            await message.reply_text(f"✅ Added **{days} days** premium to user `{user_id}`.")
            try:
                await client.send_message(
                    user_id,
                    f"🎉 **Premium Activated!**\n\n"
                    f"You now have **{days} days** of Premium access!\n\n"
                    f"Enjoy unlimited downloads! 🚀"
                )
            except Exception:
                pass
        except (ValueError, IndexError):
            await message.reply_text("❌ Format: `user_id days` (e.g. `123456789 30`)")
        return

    if pending == "rem_prem":
        try:
            user_id = int(text)
            await premium_db.remove_premium(user_id)
            del _pending[chat_id]
            await message.reply_text(f"✅ Removed premium from user `{user_id}`.")
        except ValueError:
            await message.reply_text("❌ Invalid user ID.")
        return

    if pending == "add_plan":
        lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
        added = []
        failed = []
        for line in lines:
            sep = "," if "," in line else "|"
            parts = [p.strip() for p in line.split(sep)]
            try:
                name = parts[0]
                price = float(parts[1])
                duration = int(parts[2])
                await plans_db.add_plan(name, price, duration)
                per_day = round(price / duration, 2)
                added.append(f"✅ **{name}** — ₹{price} / {duration} days  (₹{per_day}/day)")
            except (ValueError, IndexError):
                failed.append(f"❌ `{line}`  ← couldn't read this line")
        del _pending[chat_id]
        result_lines = added + ([""] + failed if failed else [])
        hint = "\n\n⚠️ Fix the red lines and add them again." if failed else ""
        await message.reply_text(
            f"**Plans processed:**\n\n" + "\n".join(result_lines) + hint
        )
        return

    if pending == "contact_link":
        await settings_db.set_contact_link(text)
        del _pending[chat_id]
        await message.reply_text(f"✅ Contact link updated to:\n`{text}`")
        return

    if pending == "set_upi_qr":
        await message.reply_text("⚠️ Please send a **photo** (not text) of your UPI QR code.")
        return


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Photo input handler for pending admin actions (UPI QR)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_message(filters.photo & filters.private & admin_filter)
async def admin_qr_photo_input(client: Client, message: Message):
    chat_id = message.chat.id
    pending = _pending.get(chat_id)
    if pending != "set_upi_qr":
        await message.continue_propagation()
        return

    file_id = message.photo.file_id
    await settings_db.set_upi_qr_file_id(file_id)
    del _pending[chat_id]
    await message.reply_text(
        "✅ **UPI QR image saved!**\n\n"
        "It will now be shown to users when they select a payment plan."
    )
