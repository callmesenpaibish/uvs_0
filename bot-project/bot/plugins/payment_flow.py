"""
Payment flow — handles screenshot submission and admin approval/rejection.
"""
import logging
from pyrogram import Client, filters
from pyrogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
)

from bot.database import premium_db, settings_db
from bot.config import ADMIN_IDS

logger = logging.getLogger(__name__)

# In-memory state: user_id -> plan dict
pending_payments: dict[int, dict] = {}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Photo handler — user sends payment screenshot
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_message(filters.photo & filters.private)
async def payment_photo_handler(client: Client, message: Message):
    user_id = message.from_user.id
    plan = pending_payments.get(user_id)
    if not plan:
        return

    await message.reply_text(
        "✅ **Screenshot received!**\n\n"
        "Status: **Pending Approval ⏳**\n\n"
        "You'll be notified as soon as an admin verifies your payment. 🙏"
    )

    del pending_payments[user_id]

    user = message.from_user
    admin_caption = (
        f"💳 **New Payment Request**\n\n"
        f"👤 User: [{user.first_name}](tg://user?id={user.id})\n"
        f"🆔 ID: `{user.id}`\n"
        f"📦 Plan: **{plan['name']}** ({plan['duration_days']} days)\n"
        f"💰 Amount: ₹{plan['price']}"
    )
    approval_kb = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            "✅ Approve",
            callback_data=f"payapprove:{user.id}:{plan['duration_days']}"
        ),
        InlineKeyboardButton(
            "❌ Reject",
            callback_data=f"payreject:{user.id}"
        ),
    ]])

    for admin_id in ADMIN_IDS:
        try:
            await client.send_photo(
                admin_id,
                photo=message.photo.file_id,
                caption=admin_caption,
                reply_markup=approval_kb,
            )
        except Exception as e:
            logger.warning(f"Could not notify admin {admin_id}: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Admin: Approve payment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_callback_query(filters.regex(r"^payapprove:\d+:\d+$"))
async def payment_approve_cb(client: Client, callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Admins only!", show_alert=True)
        return

    _, user_id_str, days_str = callback.data.split(":")
    user_id, days = int(user_id_str), int(days_str)

    await premium_db.add_premium(user_id, days, added_by=callback.from_user.id)
    await callback.answer("✅ Premium activated!", show_alert=True)

    try:
        await callback.message.edit_caption(
            callback.message.caption + "\n\n✅ **APPROVED**",
            reply_markup=None,
        )
    except Exception:
        pass

    try:
        await client.send_message(
            user_id,
            f"🎉 **Premium Activated!**\n\n"
            f"Your payment has been verified. ✅\n"
            f"You now have **{days} days** of Premium access!\n\n"
            f"Enjoy unlimited downloads! 🚀"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Admin: Reject payment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@Client.on_callback_query(filters.regex(r"^payreject:\d+$"))
async def payment_reject_cb(client: Client, callback: CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Admins only!", show_alert=True)
        return

    user_id = int(callback.data.split(":")[1])
    await callback.answer("❌ Rejected.", show_alert=True)

    try:
        await callback.message.edit_caption(
            callback.message.caption + "\n\n❌ **REJECTED**",
            reply_markup=None,
        )
    except Exception:
        pass

    try:
        await client.send_message(
            user_id,
            "❌ **Payment Rejected**\n\n"
            "Your screenshot could not be verified.\n"
            "Please contact support if you believe this is a mistake. 📞"
        )
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")
