import asyncio
import uuid
from pyrogram import Client, filters
from pyrogram.types import (
    Message, InlineKeyboardMarkup, InlineKeyboardButton,
    CallbackQuery, ReplyKeyboardMarkup, KeyboardButton,
)

from bot.database import users_db, tokens_db, premium_db, settings_db, files_db, plans_db, scraper_db
from bot.config import SUPPORT_CHAT, ADMIN_IDS, BIN_CHANNEL_ID
from bot.utils.fsub_logic import check_fsub
from bot.utils.shortener import shorten_url
from bot.utils.helpers import readable_date, format_time_left


def _main_reply_keyboard():
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📋 My Plan"), KeyboardButton("📞 Contact us")],
        ],
        resize_keyboard=True,
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /start handler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    user = message.from_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    await users_db.add_user(user.id, user.username, full_name)

    args = message.command[1] if len(message.command) > 1 else None

    if args and args.startswith("file_"):
        file_uid = args[5:]
        await handle_file_request(client, message, file_uid)
        return

    if args and args.startswith("verify_"):
        token_uuid = args[7:]
        await handle_token_verify(client, message, token_uuid)
        return

    if args and args.startswith("video_"):
        unique_code = args[6:]
        await handle_video_request(client, message, unique_code)
        return

    await client.send_message(
        message.chat.id,
        "Use the buttons below to navigate 👇",
        reply_markup=_main_reply_keyboard(),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Reply keyboard text handlers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_message(filters.regex(r"^📋 My Plan$") & filters.private)
async def kb_my_plan(client: Client, message: Message):
    await _send_account_overview(client, message.chat.id, message.from_user)


@Client.on_message(filters.regex(r"^📞 Contact us$") & filters.private)
async def kb_contact_us(client: Client, message: Message):
    contact_link = await settings_db.get_contact_link()
    await message.reply_text(
        "📞 **Contact Support**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Need help? Reach out to our support team:\n"
        f"👉 {contact_link}\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📞 Open Support Chat", url=contact_link)],
        ])
    )



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Handle file request
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def handle_file_request(client: Client, message: Message, file_uid: str):
    user_id = message.from_user.id

    if user_id in ADMIN_IDS:
        await _deliver_file(client, message.chat.id, file_uid)
        return

    not_joined = await check_fsub(client, user_id)
    if not_joined:
        buttons = []
        for ch in not_joined:
            label = "📢 Request to Join" if ch["mode"] == "request" else "📢 Join Channel"
            buttons.append([InlineKeyboardButton(label + f": {ch['name']}", url=ch["invite_link"])])
        buttons.append([InlineKeyboardButton("✅ I've Joined — Check Again", callback_data=f"recheck_fsub_{file_uid}")])
        await message.reply_text(
            "🔒 **Access Restricted**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "You must join the following channels to access this file:\n",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return

    is_premium = await premium_db.is_premium(user_id)
    decrement_mode = None  # "token" | "free" | None

    if not is_premium:
        token_required = await settings_db.get_token_required()
        sl_configured = await settings_db.is_shortlink_configured()
        daily_limit_enabled = await settings_db.get_daily_limit_enabled()

        if token_required and sl_configured:
            # Token mode — verify token first
            has_token = await tokens_db.has_valid_token(user_id)
            if not has_token:
                await send_token_gate(client, message, file_uid)
                return
            # If limit is also ON: count downloads per token session
            if daily_limit_enabled:
                daily_limit_count = await settings_db.get_daily_limit_count()
                remaining = await tokens_db.get_downloads_remaining(user_id)
                if remaining is None:
                    remaining = daily_limit_count
                    await tokens_db.set_downloads_remaining(user_id, remaining)
                if remaining <= 0:
                    await send_token_gate(client, message, file_uid, limit_reached=True)
                    return
                decrement_mode = "token"
        else:
            # No-token mode — apply daily limit if enabled (separate free counter)
            if daily_limit_enabled:
                daily_limit_count = await settings_db.get_daily_limit_count()
                remaining = await tokens_db.get_free_remaining(user_id)
                if remaining is None:
                    remaining = daily_limit_count
                    await tokens_db.set_free_remaining(user_id, remaining)
                if remaining <= 0:
                    await _send_limit_reached(client, message.chat.id)
                    return
                decrement_mode = "free"

    success = await _deliver_file(client, message.chat.id, file_uid)
    if success:
        if decrement_mode == "token":
            await tokens_db.decrement_download(user_id)
        elif decrement_mode == "free":
            await tokens_db.decrement_free(user_id)


async def handle_video_request(client: Client, message: Message, unique_code: str):
    """Forward a scraped video from the BIN_CHANNEL to the user."""
    video_doc = await scraper_db.get_video_by_code(unique_code)
    if not video_doc:
        await message.reply_text("❌ Video not found or has been removed.")
        return

    bin_message_id = video_doc.get("bin_message_id")
    if not BIN_CHANNEL_ID or not bin_message_id:
        await message.reply_text("❌ Video unavailable. Please try again later.")
        return

    try:
        await client.forward_messages(
            chat_id=message.chat.id,
            from_chat_id=BIN_CHANNEL_ID,
            message_ids=bin_message_id,
        )
    except Exception as exc:
        await message.reply_text(f"❌ Could not deliver the video: {exc}")


async def _auto_delete_task(client: Client, chat_id: int, msg_id: int, delay: int):
    await asyncio.sleep(delay)
    try:
        await client.delete_messages(chat_id, msg_id)
    except Exception:
        pass
    if delay < 60:
        time_str = f"{delay} second{'s' if delay != 1 else ''}"
    elif delay < 3600:
        m = delay // 60
        time_str = f"{m} minute{'s' if m != 1 else ''}"
    else:
        h = delay // 3600
        time_str = f"{h} hour{'s' if h != 1 else ''}"
    try:
        await client.send_message(
            chat_id,
            f"🗑️ **File Auto-Deleted**\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"Your file was automatically removed after **{time_str}** for security.\n\n"
            f"To access it again, click the original link from the channel.\n"
            f"━━━━━━━━━━━━━━━━━━━━━━",
        )
    except Exception:
        pass


async def _deliver_file(client: Client, chat_id: int, file_uid: str) -> bool:
    file_doc = await files_db.get_file(file_uid)
    if not file_doc:
        await client.send_message(chat_id, "❌ File not found or has been removed.")
        return False
    try:
        file_id = file_doc.get("file_id")
        file_type = file_doc.get("file_type", "document")

        if file_type == "video":
            sent = await client.send_video(chat_id, file_id)
        elif file_type == "audio":
            sent = await client.send_audio(chat_id, file_id)
        elif file_type == "photo":
            sent = await client.send_photo(chat_id, file_id)
        else:
            sent = await client.send_document(chat_id, file_id)

        auto_delete = await settings_db.get_auto_delete_time()
        if auto_delete > 0:
            asyncio.create_task(_auto_delete_task(client, chat_id, sent.id, auto_delete))

        return True
    except Exception as e:
        await client.send_message(chat_id, f"❌ Failed to deliver file: {e}")
        return False


async def _send_limit_reached(client: Client, chat_id: int):
    await client.send_message(
        chat_id,
        "⚡ Daily Limit Reached!\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "You've used all your free downloads for today.\n"
        "Your limit resets automatically every 24 hours.\n\n"
        "💎 Upgrade to Premium for unlimited access!\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Get Premium", callback_data="show_plans")],
        ]),
    )


async def send_token_gate(client: Client, message: Message, file_uid: str, limit_reached: bool = False):
    user_id = message.from_user.id
    bot_info = await client.get_me()

    token_uuid = str(uuid.uuid4())
    await tokens_db.create_token(user_id, token_uuid)

    start_link = f"https://t.me/{bot_info.username}?start=verify_{token_uuid}"
    short_link = await shorten_url(start_link)

    daily_limit_enabled = await settings_db.get_daily_limit_enabled()
    daily_limit_count = await settings_db.get_daily_limit_count() if daily_limit_enabled else None

    if limit_reached:
        header = (
            "⚡ **Daily Limit Reached!**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            f"You've used all your downloads for this session.\n"
            f"{'🗂️ Verify again to get **' + str(daily_limit_count) + ' more files**.' if daily_limit_count else ''}\n\n"
            "💎 Get **Premium** to enjoy unlimited access!\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        if daily_limit_count:
            limit_hint = f"\n📦 After verifying you can download **{daily_limit_count} file(s)**.\n"
            expiry_note = f"🔄 Once you've used your {daily_limit_count} file(s), simply verify again to continue.\n"
        else:
            limit_hint = ""
            expiry_note = "⏳ Your token is valid for **24 hours**.\n"
        header = (
            "🔑 **One Quick Step to Get Your File**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "To keep our service free, we ask you to complete a short verification.\n\n"
            "**Here's how:**\n"
            "1️⃣ Tap **Get Access Token** below\n"
            "2️⃣ Complete the steps on the page that opens\n"
            "3️⃣ Come back and tap **I Have Verified** ✅\n"
            f"{limit_hint}\n"
            f"{expiry_note}"
            "💎 **Premium members** skip this step entirely!\n"
            "━━━━━━━━━━━━━━━━━━━━━━"
        )

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Get Access Token", url=short_link)],
        [InlineKeyboardButton("✅ I Have Verified", callback_data=f"check_token_{file_uid}_{token_uuid}")],
        [InlineKeyboardButton("💎 Premium Plans", callback_data="show_plans")],
    ])
    await message.reply_text(header, reply_markup=buttons)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Handle token verification (via shortlink)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async def handle_token_verify(client: Client, message: Message, token_uuid: str):
    doc = await tokens_db.verify_token(token_uuid)
    if not doc:
        await message.reply_text("❌ Invalid or expired token. Please request a new one.")
        return

    daily_limit_enabled = await settings_db.get_daily_limit_enabled()
    daily_limit_count = await settings_db.get_daily_limit_count() if daily_limit_enabled else None

    await tokens_db.mark_verified(token_uuid, downloads_limit=daily_limit_count)

    limit_text = f"\n📦 You can download **{daily_limit_count} file(s)** before verifying again." if daily_limit_count else ""
    await message.reply_text(
        "✅ **Token Verified Successfully!**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "🎉 You now have **24-hour access**.\n"
        f"{limit_text}\n"
        "Go back and tap **I Have Verified** to get your file."
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback: Check token after verification
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_callback_query(filters.regex(r"^check_token_(.+)_(.+)$"))
async def check_token_callback(client: Client, cb: CallbackQuery):
    parts = cb.data.split("_", 3)
    file_uid = parts[2]
    token_uuid = parts[3]

    user_id = cb.from_user.id
    doc = await tokens_db.verify_token(token_uuid)

    if not doc or doc.get("user_id") != user_id:
        await cb.answer("❌ Token not verified yet. Complete the link steps first.", show_alert=True)
        return

    if not doc.get("verified"):
        await cb.answer("❌ Token not yet verified. Open the link and complete steps.", show_alert=True)
        return

    # If limit is also ON: check per-token download count
    daily_limit_enabled = await settings_db.get_daily_limit_enabled()
    if daily_limit_enabled:
        daily_limit_count = await settings_db.get_daily_limit_count()
        remaining = await tokens_db.get_downloads_remaining(user_id)
        if remaining is None:
            remaining = daily_limit_count
            await tokens_db.set_downloads_remaining(user_id, remaining)
        if remaining <= 0:
            await cb.answer("⚡ Limit reached! Verify a new token for more files.", show_alert=True)
            return

    await cb.answer("✅ Delivering your file...", show_alert=False)
    success = await _deliver_file(client, cb.message.chat.id, file_uid)
    if success and daily_limit_enabled:
        await tokens_db.decrement_download(user_id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback: Re-check FSub
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_callback_query(filters.regex(r"^recheck_fsub_(.+)$"))
async def recheck_fsub_callback(client: Client, cb: CallbackQuery):
    file_uid = cb.data.split("_", 2)[2]
    user_id = cb.from_user.id

    not_joined = await check_fsub(client, user_id)
    if not_joined:
        await cb.answer("❌ You still haven't joined all channels!", show_alert=True)
        return

    await cb.answer("✅ Verified! Loading file...", show_alert=False)
    await cb.message.delete()

    is_premium = await premium_db.is_premium(user_id)
    decrement_mode = None  # "token" | "free" | None

    if not is_premium:
        token_required = await settings_db.get_token_required()
        sl_configured = await settings_db.is_shortlink_configured()
        daily_limit_enabled = await settings_db.get_daily_limit_enabled()

        if token_required and sl_configured:
            # Token mode — verify token first
            has_token = await tokens_db.has_valid_token(user_id)
            if not has_token:
                class _FakeMsg:
                    chat = cb.message.chat
                    from_user = cb.from_user
                    async def reply_text(self, *a, **kw):
                        await client.send_message(cb.message.chat.id, *a, **kw)
                await send_token_gate(client, _FakeMsg(), file_uid)
                return
            # If limit is also ON: count downloads per token session
            if daily_limit_enabled:
                daily_limit_count = await settings_db.get_daily_limit_count()
                remaining = await tokens_db.get_downloads_remaining(user_id)
                if remaining is None:
                    remaining = daily_limit_count
                    await tokens_db.set_downloads_remaining(user_id, remaining)
                if remaining <= 0:
                    class _FakeMsg2:
                        chat = cb.message.chat
                        from_user = cb.from_user
                        async def reply_text(self, *a, **kw):
                            await client.send_message(cb.message.chat.id, *a, **kw)
                    await send_token_gate(client, _FakeMsg2(), file_uid, limit_reached=True)
                    return
                decrement_mode = "token"
        else:
            # No-token mode — apply daily limit if enabled (separate free counter)
            if daily_limit_enabled:
                daily_limit_count = await settings_db.get_daily_limit_count()
                remaining = await tokens_db.get_free_remaining(user_id)
                if remaining is None:
                    remaining = daily_limit_count
                    await tokens_db.set_free_remaining(user_id, remaining)
                if remaining <= 0:
                    await _send_limit_reached(client, cb.message.chat.id)
                    return
                decrement_mode = "free"

    success = await _deliver_file(client, cb.message.chat.id, file_uid)
    if success:
        if decrement_mode == "token":
            await tokens_db.decrement_download(user_id)
        elif decrement_mode == "free":
            await tokens_db.decrement_free(user_id)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# /myplan command + Account Overview
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_message(filters.command("myplan") & filters.private)
async def my_plan(client: Client, message: Message):
    await _send_account_overview(client, message.chat.id, message.from_user)


async def _send_account_overview(client: Client, chat_id: int, user):
    user_id = user.id
    is_premium = await premium_db.is_premium(user_id)
    plan_doc = await premium_db.get_plan(user_id)

    daily_limit_enabled = await settings_db.get_daily_limit_enabled()
    daily_limit_count = await settings_db.get_daily_limit_count()

    if is_premium and plan_doc:
        time_left = format_time_left(plan_doc["expires_at"])
        membership = "Premium Member 💎"
        plan_type = "Premium"
        daily_limit_val = "Unlimited"
        used_val = "—"
        remaining_val = "Unlimited"
        footer = f"Your plan expires in: {time_left}"
    else:
        membership = "Free Access"
        plan_type = "Basic"

        token_required = await settings_db.get_token_required()
        sl_configured = await settings_db.is_shortlink_configured()
        token_mode = token_required and sl_configured

        if token_mode and daily_limit_enabled:
            # Token + Limit mode: show per-token download count
            remaining = await tokens_db.get_downloads_remaining(user_id)
            if remaining is None:
                remaining = daily_limit_count
            used = max(0, daily_limit_count - remaining)
            daily_limit_val = str(daily_limit_count)
            used_val = str(used)
            remaining_val = str(remaining)
        elif token_mode:
            # Token only: no limit tracking
            daily_limit_val = "No limit (Token mode)"
            used_val = "—"
            remaining_val = "∞"
        elif daily_limit_enabled:
            # Limit-only mode: read from free_limits collection
            remaining = await tokens_db.get_free_remaining(user_id)
            if remaining is None:
                remaining = daily_limit_count
            used = max(0, daily_limit_count - remaining)
            daily_limit_val = str(daily_limit_count)
            used_val = str(used)
            remaining_val = str(remaining)
        else:
            daily_limit_val = "No limit"
            used_val = "—"
            remaining_val = "∞"

        footer = "Your usage refreshes automatically every 24 hours."

    text = (
        "🗂️ Account Overview\n\n"
        f"👤 Membership: {membership}\n"
        f"📦 Plan Type: {plan_type}\n"
        f"📊 Daily Limit: {daily_limit_val}\n"
        f"📈 Used Today: {used_val}\n"
        f"🟢 Remaining Today: {remaining_val}\n\n"
        f"{footer}"
    )

    if is_premium:
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📞 Contact Us", callback_data="contact_cb")],
            [InlineKeyboardButton("❌ Close", callback_data="close_msg")],
        ])
    else:
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 View Plans", callback_data="show_plans")],
            [InlineKeyboardButton("❌ Close", callback_data="close_msg")],
        ])

    await client.send_message(chat_id, text, reply_markup=buttons)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback: My Plan button
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_callback_query(filters.regex("^my_plan$"))
async def my_plan_cb(client: Client, cb: CallbackQuery):
    await cb.answer()
    await _send_account_overview(client, cb.message.chat.id, cb.from_user)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback: Show premium plans
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_callback_query(filters.regex("^show_plans$"))
async def show_plans_cb(client: Client, cb: CallbackQuery):
    await cb.answer()
    plans = await plans_db.get_plans()
    contact_link = await settings_db.get_contact_link()

    if not plans:
        await cb.message.reply_text(
            "💎 **Premium Plans**\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━\n"
            "No plans are available yet.\n"
            "Contact an admin to get Premium access!\n"
            "━━━━━━━━━━━━━━━━━━━━━━",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📞 Contact Us", url=contact_link)],
            ])
        )
        return

    rows = []
    for plan in plans:
        per_day = round(plan['price'] / plan['duration_days'], 2)
        label = f"₹{plan['price']} = {plan['duration_days']} days  (₹{per_day}/day)"
        rows.append([InlineKeyboardButton(label, callback_data=f"buy_plan_{plan['_id']}")])
    rows.append([InlineKeyboardButton("❌ Cancel", callback_data="pay_cancel")])

    await cb.message.reply_text(
        "💎 **Premium Subscription Plans**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Upgrade for **unlimited downloads**, no daily limits,\n"
        "and instant file access — forever!\n\n"
        "Choose a plan to proceed 👇\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup(rows),
    )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Callback: Buy plan → send payment instructions + QR
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_callback_query(filters.regex(r"^buy_plan_(.+)$"))
async def buy_plan_cb(client: Client, cb: CallbackQuery):
    plan_id = cb.data.split("_", 2)[2]
    plan = await plans_db.get_plan_by_id(plan_id)
    if not plan:
        await cb.answer("⚠️ Plan not found!", show_alert=True)
        return

    await cb.answer()

    caption = (
        f"💳 **Payment Instructions**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📦 Plan: **{plan['name']}**\n"
        f"💰 Amount: **₹{plan['price']}**\n"
        f"⏳ Duration: **{plan['duration_days']} days**\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Please pay **₹{plan['price']}** and send a screenshot of the payment here. 👇\n\n"
        f"⚠️ Your plan will be activated after admin verification."
    )

    cancel_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data="pay_cancel")
    ]])

    from bot.plugins.payment_flow import pending_payments
    pending_payments[cb.from_user.id] = plan

    try:
        await cb.message.delete()
    except Exception:
        pass

    upi_qr = await settings_db.get_upi_qr_file_id()
    if upi_qr:
        await client.send_photo(
            cb.from_user.id,
            photo=upi_qr,
            caption=caption,
            reply_markup=cancel_kb,
        )
    else:
        await client.send_message(
            cb.from_user.id,
            caption,
            reply_markup=cancel_kb,
        )


@Client.on_callback_query(filters.regex("^pay_cancel$"))
async def pay_cancel_cb(client: Client, cb: CallbackQuery):
    from bot.plugins.payment_flow import pending_payments
    pending_payments.pop(cb.from_user.id, None)
    await cb.answer("Cancelled.", show_alert=False)
    try:
        await cb.message.delete()
    except Exception:
        pass


@Client.on_callback_query(filters.regex("^close_msg$"))
async def close_msg_cb(client: Client, cb: CallbackQuery):
    await cb.answer()
    try:
        await cb.message.delete()
    except Exception:
        pass


@Client.on_callback_query(filters.regex("^contact_cb$"))
async def contact_cb(client: Client, cb: CallbackQuery):
    await cb.answer()
    contact_link = await settings_db.get_contact_link()
    await cb.message.reply_text(
        f"📞 **Support:** {contact_link}",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📞 Open Chat", url=contact_link)],
        ])
    )


@Client.on_callback_query(filters.regex("^help$"))
async def help_cb(client: Client, cb: CallbackQuery):
    await cb.answer()
    contact_link = await settings_db.get_contact_link()
    await cb.message.edit_text(
        "ℹ️ **Help**\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "📁 Click a file link → Bot delivers the file\n"
        "🔑 Free users need a token (via shortlink)\n"
        "💎 Premium users get instant unlimited access\n"
        "/myplan — Check your plan & remaining downloads\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("💎 Premium Plans", callback_data="show_plans")],
            [InlineKeyboardButton("📞 Contact Us", callback_data="contact_cb")],
        ])
    )
