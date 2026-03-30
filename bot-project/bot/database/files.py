from datetime import datetime
from .connection import get_db


class FilesDB:
    def col(self):
        return get_db()["files"]

    async def add_file(self, file_id: str, file_unique_id: str, file_type: str,
                       file_name: str, file_size: int, msg_id: int,
                       channel_id: int, caption: str = ""):
        await self.col().update_one(
            {"file_unique_id": file_unique_id},
            {"$set": {
                "file_id": file_id,
                "file_unique_id": file_unique_id,
                "file_type": file_type,
                "file_name": file_name,
                "file_size": file_size,
                "msg_id": msg_id,
                "channel_id": channel_id,
                "caption": caption,
                "added_at": datetime.utcnow(),
            }},
            upsert=True,
        )
        return file_unique_id

    async def get_file(self, file_unique_id: str) -> dict | None:
        return await self.col().find_one({"file_unique_id": file_unique_id})

    async def get_file_by_msg(self, msg_id: int, channel_id: int) -> dict | None:
        return await self.col().find_one({"msg_id": msg_id, "channel_id": channel_id})

    async def total_files(self) -> int:
        return await self.col().count_documents({})

    async def delete_file(self, file_unique_id: str):
        await self.col().delete_one({"file_unique_id": file_unique_id})


files_db = FilesDB()
