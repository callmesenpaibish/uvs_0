from datetime import datetime, timedelta
from .connection import get_db
from bot.config import TOKEN_VALIDITY_HOURS


class TokensDB:
    def col(self):
        return get_db()["tokens"]

    async def create_token(self, user_id: int, uuid: str):
        col = self.col()
        expires_at = datetime.utcnow() + timedelta(hours=TOKEN_VALIDITY_HOURS)
        await col.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "uuid": uuid,
                "created_at": datetime.utcnow(),
                "expires_at": expires_at,
                "verified": False,
                "downloads_remaining": None,
            }},
            upsert=True,
        )

    async def verify_token(self, uuid: str) -> dict | None:
        col = self.col()
        doc = await col.find_one({"uuid": uuid})
        if not doc:
            return None
        if datetime.utcnow() > doc["expires_at"]:
            return None
        return doc

    async def mark_verified(self, uuid: str, downloads_limit: int | None = None):
        update = {"verified": True}
        if downloads_limit is not None:
            update["downloads_remaining"] = downloads_limit
        await self.col().update_one({"uuid": uuid}, {"$set": update})

    async def has_valid_token(self, user_id: int) -> bool:
        doc = await self.col().find_one({"user_id": user_id, "verified": True})
        if not doc:
            return False
        return datetime.utcnow() <= doc["expires_at"]

    async def get_token_doc(self, user_id: int) -> dict | None:
        doc = await self.col().find_one({"user_id": user_id, "verified": True})
        if not doc:
            return None
        if datetime.utcnow() > doc["expires_at"]:
            return None
        return doc

    async def get_downloads_remaining(self, user_id: int) -> int | None:
        doc = await self.get_token_doc(user_id)
        if not doc:
            return None
        return doc.get("downloads_remaining")

    async def set_downloads_remaining(self, user_id: int, count: int):
        await self.col().update_one(
            {"user_id": user_id},
            {"$set": {"downloads_remaining": count}},
        )

    async def decrement_download(self, user_id: int):
        doc = await self.get_token_doc(user_id)
        if not doc:
            return
        remaining = doc.get("downloads_remaining")
        if remaining is None:
            return
        new_val = max(0, remaining - 1)
        await self.col().update_one(
            {"user_id": user_id},
            {"$set": {"downloads_remaining": new_val}},
        )

    # ── Free-limit tracking (no token required) ──────────────────────────────
    def _free_col(self):
        return get_db()["free_limits"]

    async def get_free_remaining(self, user_id: int) -> int | None:
        doc = await self._free_col().find_one({"user_id": user_id})
        if not doc:
            return None
        return doc.get("remaining")

    async def set_free_remaining(self, user_id: int, count: int):
        await self._free_col().update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "remaining": count}},
            upsert=True,
        )

    async def decrement_free(self, user_id: int):
        doc = await self._free_col().find_one({"user_id": user_id})
        if not doc:
            return
        remaining = doc.get("remaining")
        if remaining is None:
            return
        new_val = max(0, remaining - 1)
        await self._free_col().update_one(
            {"user_id": user_id},
            {"$set": {"remaining": new_val}},
        )

    async def tokens_today(self) -> int:
        today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        return await self.col().count_documents({"created_at": {"$gte": today_start}})

    async def delete_token(self, user_id: int):
        await self.col().delete_one({"user_id": user_id})


tokens_db = TokensDB()
