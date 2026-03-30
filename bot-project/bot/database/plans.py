import time
from bson import ObjectId
from .connection import get_db


class PlansDB:
    def col(self):
        return get_db()["plans"]

    async def get_plans(self) -> list[dict]:
        cursor = self.col().find({})
        docs = await cursor.to_list(length=None)
        for d in docs:
            d["_id"] = str(d["_id"])
        return docs

    async def get_plan_by_id(self, plan_id: str) -> dict | None:
        try:
            doc = await self.col().find_one({"_id": ObjectId(plan_id)})
            if doc:
                doc["_id"] = str(doc["_id"])
            return doc
        except Exception:
            return None

    async def add_plan(self, name: str, price: float, duration_days: int) -> str:
        doc = {
            "name": name,
            "price": price,
            "duration_days": duration_days,
            "created_at": time.time(),
        }
        result = await self.col().insert_one(doc)
        return str(result.inserted_id)

    async def edit_plan(self, plan_id: str, **kwargs) -> None:
        try:
            await self.col().update_one({"_id": ObjectId(plan_id)}, {"$set": kwargs})
        except Exception:
            pass

    async def delete_plan(self, plan_id: str) -> None:
        try:
            await self.col().delete_one({"_id": ObjectId(plan_id)})
        except Exception:
            pass


plans_db = PlansDB()
