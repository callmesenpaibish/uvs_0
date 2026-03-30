from .connection import get_db


class SettingsDB:
    def col(self):
        return get_db()["settings"]

    async def get(self, key: str, default=None):
        doc = await self.col().find_one({"key": key})
        return doc["value"] if doc else default

    async def set(self, key: str, value):
        await self.col().update_one(
            {"key": key},
            {"$set": {"key": key, "value": value}},
            upsert=True,
        )

    async def delete(self, key: str):
        await self.col().delete_one({"key": key})

    async def get_shortlink_config(self):
        api = await self.get("shortlink_api", "")
        base_url = await self.get("shortlink_base_url", "")
        return api, base_url

    async def set_shortlink_config(self, api: str, base_url: str):
        await self.set("shortlink_api", api)
        await self.set("shortlink_base_url", base_url)

    async def is_shortlink_configured(self) -> bool:
        api, base_url = await self.get_shortlink_config()
        return bool(api and base_url)

    async def get_bin_channel(self) -> int | None:
        v = await self.get("bin_channel")
        return int(v) if v else None

    async def set_bin_channel(self, channel_id: int):
        await self.set("bin_channel", str(channel_id))

    async def get_public_channel(self) -> str | None:
        """Returns the raw stored value — may be @username, invite link, or numeric ID string."""
        v = await self.get("public_channel")
        return str(v) if v else None

    async def set_public_channel(self, value: str):
        """Store whatever the admin provides: @username, invite link, or -100xxx ID."""
        await self.set("public_channel", str(value).strip())

    async def get_fsub_channels(self) -> list:
        return await self.get("fsub_channels", [])

    async def add_fsub_channel(self, channel_id: int, mode: str = "join"):
        channels = await self.get_fsub_channels()
        ids = [c["id"] for c in channels]
        if channel_id not in ids:
            channels.append({"id": channel_id, "mode": mode})
            await self.set("fsub_channels", channels)

    async def remove_fsub_channel(self, channel_id: int):
        channels = await self.get_fsub_channels()
        channels = [c for c in channels if c["id"] != channel_id]
        await self.set("fsub_channels", channels)

    async def get_auto_fetch(self) -> bool:
        return await self.get("auto_fetch", False)

    async def set_auto_fetch(self, state: bool):
        await self.set("auto_fetch", state)

    async def get_fetch_resume_id(self) -> int | None:
        return await self.get("fetch_resume_id")

    async def set_fetch_resume_id(self, msg_id: int):
        await self.set("fetch_resume_id", msg_id)

    async def get_batch_mode(self) -> bool:
        return await self.get("batch_mode", False)

    async def set_batch_mode(self, state: bool):
        await self.set("batch_mode", state)

    async def get_batch_files(self) -> list:
        return await self.get("batch_files", [])

    async def add_batch_file(self, file_data: dict):
        files = await self.get_batch_files()
        files.append(file_data)
        await self.set("batch_files", files)

    async def clear_batch_files(self):
        await self.set("batch_files", [])

    async def get_approval_queue(self) -> bool:
        return await self.get("approval_queue", False)

    async def set_approval_queue(self, state: bool):
        await self.set("approval_queue", state)

    async def get_token_required(self) -> bool:
        return await self.get("token_required", True)

    async def set_token_required(self, state: bool):
        await self.set("token_required", state)

    async def get_daily_limit_enabled(self) -> bool:
        return await self.get("daily_limit_enabled", False)

    async def set_daily_limit_enabled(self, state: bool):
        await self.set("daily_limit_enabled", state)

    async def get_daily_limit_count(self) -> int:
        return int(await self.get("daily_limit_count", 5))

    async def set_daily_limit_count(self, count: int):
        await self.set("daily_limit_count", count)

    async def get_contact_link(self) -> str:
        from bot.config import SUPPORT_CHAT
        return await self.get("contact_link", SUPPORT_CHAT)

    async def set_contact_link(self, link: str):
        await self.set("contact_link", link)

    async def get_upi_qr_file_id(self) -> str | None:
        return await self.get("upi_qr_file_id")

    async def set_upi_qr_file_id(self, file_id: str):
        await self.set("upi_qr_file_id", file_id)

    async def delete_upi_qr(self):
        await self.delete("upi_qr_file_id")

    async def get_auto_delete_time(self) -> int:
        return int(await self.get("auto_delete_time", 0))

    async def set_auto_delete_time(self, seconds: int):
        await self.set("auto_delete_time", seconds)


settings_db = SettingsDB()
