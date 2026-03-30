from datetime import datetime, timedelta
from .connection import get_db


class PremiumDB:
    def col(self):
        return get_db()["premium"]

    async def add_premium(self, user_id: int, days: int, added_by: int = None):
        now = datetime.utcnow()
        doc = await self.col().find_one({"user_id": user_id})
        if doc and doc.get("expires_at") and doc["expires_at"] > now:
            base = doc["expires_at"]
        else:
            base = now
        expires_at = base + timedelta(days=days)
        await self.col().update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "expires_at": expires_at,
                "added_by": added_by,
                "added_at": now,
            }},
            upsert=True,
        )

    async def remove_premium(self, user_id: int):
        await self.col().delete_one({"user_id": user_id})

    async def is_premium(self, user_id: int) -> bool:
        doc = await self.col().find_one({"user_id": user_id})
        if not doc:
            return False
        return datetime.utcnow() <= doc["expires_at"]

    async def get_plan(self, user_id: int) -> dict | None:
        return await self.col().find_one({"user_id": user_id})

    async def total_premium(self) -> int:
        now = datetime.utcnow()
        return await self.col().count_documents({"expires_at": {"$gt": now}})

    async def get_all_premium(self) -> list:
        cursor = self.col().find({"expires_at": {"$gt": datetime.utcnow()}})
        return [doc async for doc in cursor]


premium_db = PremiumDB()
