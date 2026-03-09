import os
import requests

token = os.getenv("TELEGRAM_TOKEN")
chat_id = os.getenv("CHAT_ID")

requests.post(
    f"https://api.telegram.org/bot{token}/sendMessage",
    json={"chat_id": chat_id, "text": "🧪 GITHUB BOT TEST OK"},
    timeout=30,
)