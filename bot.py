import os
import requests

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
payload = {
    "chat_id": CHAT_ID,
    "text": "🧪 GITHUB BOT TEST OK"
}
requests.post(url, json=payload, timeout=30)