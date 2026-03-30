"""MongoDB collection for the sequential video scraper queue."""
from datetime import datetime
from .connection import get_db


class ScraperDB:
    def _queue(self):
        return get_db()["scraper_queue"]

    def _videos(self):
        return get_db()["scraper_videos"]

    async def is_url_known(self, url: str) -> bool:
        """Return True if this URL is already in the queue (any status) or already downloaded."""
        in_queue = await self._queue().find_one({"url": url})
        if in_queue:
            return True
        in_videos = await self._videos().find_one({"source_url": url})
        return in_videos is not None

    async def add_to_queue(
        self,
        url: str,
        source_url: str,
        title: str = "",
        admin_chat_id: int = 0,
        skip_duplicates: bool = True,
    ) -> str | None:
        if skip_duplicates and await self.is_url_known(url):
            return None
        doc = {
            "url": url,
            "source_url": source_url,
            "title": title,
            "admin_chat_id": admin_chat_id,
            "status": "pending",
            "added_at": datetime.utcnow(),
        }
        result = await self._queue().insert_one(doc)
        return str(result.inserted_id)

    async def get_next_pending(self) -> dict | None:
        return await self._queue().find_one(
            {"status": "pending"},
            sort=[("added_at", 1)],
        )

    async def mark_processing(self, doc_id) -> None:
        from bson import ObjectId
        await self._queue().update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {"status": "processing", "started_at": datetime.utcnow()}},
        )

    async def mark_done(self, doc_id) -> None:
        from bson import ObjectId
        await self._queue().update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {"status": "done", "finished_at": datetime.utcnow()}},
        )

    async def mark_failed(self, doc_id, reason: str = "") -> None:
        from bson import ObjectId
        await self._queue().update_one(
            {"_id": ObjectId(doc_id)},
            {"$set": {"status": "failed", "error": reason, "finished_at": datetime.utcnow()}},
        )

    async def count_pending(self) -> int:
        return await self._queue().count_documents({"status": "pending"})

    async def count_by_status(self) -> dict:
        pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        result = {}
        async for doc in self._queue().aggregate(pipeline):
            result[doc["_id"]] = doc["count"]
        return result

    async def save_video(self, unique_code: str, bin_message_id: int, caption: str, source_url: str = "") -> None:
        await self._videos().update_one(
            {"unique_code": unique_code},
            {"$set": {
                "unique_code": unique_code,
                "bin_message_id": bin_message_id,
                "caption": caption,
                "source_url": source_url,
                "saved_at": datetime.utcnow(),
            }},
            upsert=True,
        )

    async def get_video_by_code(self, unique_code: str) -> dict | None:
        return await self._videos().find_one({"unique_code": unique_code})

    async def total_videos(self) -> int:
        return await self._videos().count_documents({})

    async def clear_pending_failed(self) -> int:
        result = await self._queue().delete_many(
            {"status": {"$in": ["pending", "failed", "processing"]}}
        )
        return result.deleted_count


scraper_db = ScraperDB()
