import aiohttp
from bot.database import settings_db


async def shorten_url(long_url: str) -> str:
    api_key, base_url = await settings_db.get_shortlink_config()

    if not api_key or not base_url:
        return long_url

    base_url = base_url.rstrip("/")
    api_url = f"{base_url}/api"

    try:
        async with aiohttp.ClientSession() as session:
            params = {"api": api_key, "url": long_url}
            async with session.get(api_url, params=params, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()
                if data.get("status") == "success" and data.get("shortenedUrl"):
                    return data["shortenedUrl"]
                if data.get("short_url"):
                    return data["short_url"]
    except Exception as e:
        print(f"[Shortener] Error: {e}")

    return long_url
