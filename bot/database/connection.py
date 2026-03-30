from urllib.parse import quote_plus
import motor.motor_asyncio
from bot.config import MONGO_URI, DB_NAME

_client = None
_db = None


def _encode_uri_credentials(uri: str) -> str:
    try:
        scheme_end = uri.find("://")
        if scheme_end == -1:
            return uri
        scheme = uri[:scheme_end + 3]
        after_scheme = uri[scheme_end + 3:]
        at_pos = after_scheme.rfind("@")
        if at_pos == -1:
            return uri
        credentials = after_scheme[:at_pos]
        rest = after_scheme[at_pos + 1:]
        colon_pos = credentials.find(":")
        if colon_pos == -1:
            return uri
        user = credentials[:colon_pos]
        password = credentials[colon_pos + 1:]
        return f"{scheme}{quote_plus(user)}:{quote_plus(password)}@{rest}"
    except Exception:
        pass
    return uri


async def init_db():
    global _client, _db
    uri = MONGO_URI.strip()
    if not uri:
        raise ValueError("MONGO_URI is not set.")
    uri = _encode_uri_credentials(uri)
    _client = motor.motor_asyncio.AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
    _db = _client[DB_NAME]
    await _db.command("ping")
    print(f"[DB] Connected to MongoDB: {DB_NAME}")
    return _db


def get_db():
    return _db
