from pyrogram import Client
from pyrogram.errors import UserNotParticipant, ChatAdminRequired
from bot.database import settings_db


async def check_fsub(client: Client, user_id: int) -> list:
    channels = await settings_db.get_fsub_channels()
    not_joined = []

    for ch in channels:
        channel_id = ch["id"]
        mode = ch.get("mode", "join")
        try:
            member = await client.get_chat_member(channel_id, user_id)
            if member.status.value in ("left", "banned", "restricted"):
                raise UserNotParticipant
        except UserNotParticipant:
            try:
                chat = await client.get_chat(channel_id)
                invite_link = chat.invite_link or f"https://t.me/c/{str(channel_id).replace('-100', '')}"
                not_joined.append({
                    "id": channel_id,
                    "mode": mode,
                    "name": chat.title or "Channel",
                    "invite_link": invite_link,
                })
            except Exception:
                not_joined.append({
                    "id": channel_id,
                    "mode": mode,
                    "name": "Channel",
                    "invite_link": "#",
                })
        except (ChatAdminRequired, Exception):
            pass

    return not_joined
