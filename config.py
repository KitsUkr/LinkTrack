import os
import sys

from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    sys.exit("Ошибка: BOT_TOKEN не задан в .env файле!")

_raw_ids = os.getenv("ADMIN_IDS", "")
if not _raw_ids.strip():
    sys.exit("Ошибка: ADMIN_IDS не задан в .env файле!")

ADMIN_IDS: list[int] = [int(uid.strip()) for uid in _raw_ids.split(",") if uid.strip()]
