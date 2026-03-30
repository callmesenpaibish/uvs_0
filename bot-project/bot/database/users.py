from datetime import datetime
from .connection import get_db


class UsersDB:
    def col(self):
        return get_db()["users"]

    async def add_user(self, user_id: int, username: str = None, full_name: str = None):
        col = self.col()
        existing = await col.find_one({"user_id": user_id})
        if not existing:
            await col.insert_one({
                "user_id": user_id,
                "username": username,
                "full_name": full_name,
                "joined": datetime.utcnow(),
                "banned": False,
            })

    async def get_user(self, user_id: int):
        return await self.col().find_one({"user_id": user_id})

    async def total_users(self) -> int:
        return await self.col().count_documents({})

    async def get_all_user_ids(self) -> list:
        cursor = self.col().find({}, {"user_id": 1})
        return [doc["user_id"] async for doc in cursor]

    async def ban_user(self, user_id: int):
        await self.col().update_one({"user_id": user_id}, {"$set": {"banned": True}})

    async def unban_user(self, user_id: int):
        await self.col().update_one({"user_id": user_id}, {"$set": {"banned": False}})

    async def is_banned(self, user_id: int) -> bool:
        doc = await self.col().find_one({"user_id": user_id})
        return doc.get("banned", False) if doc else False


users_db = UsersDB()
