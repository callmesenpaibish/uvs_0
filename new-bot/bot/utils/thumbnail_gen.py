import os
import asyncio
import tempfile
from PIL import Image


async def extract_thumbnail(video_path: str, output_path: str = None) -> str | None:
    if output_path is None:
        output_path = tempfile.mktemp(suffix=".jpg")

    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-ss", "00:00:01",
        "-vframes", "1",
        "-vf", "scale=320:-1",
        output_path
    ]

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
    except Exception as e:
        print(f"[Thumbnail] Error: {e}")
    return None


def resize_thumbnail(path: str, size: tuple = (320, 320)) -> str:
    try:
        img = Image.open(path)
        img.thumbnail(size, Image.LANCZOS)
        img.save(path, "JPEG", quality=85)
        return path
    except Exception as e:
        print(f"[Thumbnail] Resize error: {e}")
        return path
