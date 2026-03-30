import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", 0))
API_HASH = os.environ.get("API_HASH", "")
STRING_SESSION = os.environ.get("STRING_SESSION", "")

MONGO_URI = os.environ.get("MONGO_URI", "")
DB_NAME = os.environ.get("DB_NAME", "filestore_bot")

_admins_raw = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = [int(x.strip()) for x in _admins_raw.split(",") if x.strip().isdigit()]

TOKEN_VALIDITY_HOURS = int(os.environ.get("TOKEN_VALIDITY_HOURS", 24))
POST_DELAY_SECONDS = int(os.environ.get("POST_DELAY_SECONDS", 5))
BROADCAST_CHUNK = int(os.environ.get("BROADCAST_CHUNK", 200))

SUPPORT_CHAT = os.environ.get("SUPPORT_CHAT", "https://t.me/secretsocietysupportbot")

BIN_CHANNEL_ID = int(os.environ.get("BIN_CHANNEL_ID", 0))
MAIN_CHANNEL_ID = int(os.environ.get("MAIN_CHANNEL_ID", 0))
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")
