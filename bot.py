import os
import json
import requests

TELEGRAM_TOKEN = "8641883183:AAFTOVytAFoDhaEbF-cSwSw7BvIx7oIBKNk"
CHAT_ID = "6450217288"
FMP_API_KEY = "GBzfIZThj87JwZgdGYdPmuGsg39PFUmz"

STATE_FILE = "seen_splits.json"

ALLOWED_EXCHANGES = {
    "NASDAQ",
    "NYSE",
    "AMEX",
    "ARCA",
    "NYSE ARCA",
    "NYSE AMERICAN",
}

BLOCKED_KEYWORDS = {
    "OTC",
    "OTCQB",
    "OTCQX",
    "OTCMKTS",
    "PINK",
    "GREY",
    "ADR",
}


def load_seen():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(list(seen)), f, ensure_ascii=False, indent=2)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    r = requests.post(url, data=payload, timeout=30)
    r.raise_for_status()


def is_main_market(exchange_text="", symbol_text="", company_text=""):
    combined = f"{exchange_text} {symbol_text} {company_text}".upper()

    for bad in BLOCKED_KEYWORDS:
        if bad in combined:
            return False

    for ex in ALLOWED_EXCHANGES:
        if ex in combined:
            return True

    return False


def get_splits():
    url = f"https://financialmodelingprep.com/stable/splits-calendar?apikey={FMP_API_KEY}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()

    data = r.json()
    if not isinstance(data, list):
        raise ValueError(f"Неожиданный ответ API: {data}")

    results = []

    for item in data:
        symbol = str(item.get("symbol", "")).strip()
        company = str(item.get("companyName", "")).strip()
        exchange = str(item.get("exchange", "")).strip()
        split_date = str(item.get("date", "")).strip()
        numerator = str(item.get("numerator", "")).strip()
        denominator = str(item.get("denominator", "")).strip()
        split_type = str(item.get("type", "")).strip()

        ratio = f"{numerator}:{denominator}" if numerator and denominator else "N/A"

        if not symbol or not split_date:
            continue

        if not is_main_market(exchange, symbol, company):
            continue

        results.append({
            "symbol": symbol,
            "company": company or "N/A",
            "exchange": exchange or "N/A",
            "date": split_date,
            "ratio": ratio,
            "type": split_type or "split",
        })

    unique = []
    seen_local = set()

    for item in results:
        key = f"{item['symbol']}|{item['date']}|{item['ratio']}|{item['type']}"
        if key not in seen_local:
            seen_local.add(key)
            unique.append(item)

    return unique


def main():
    seen = load_seen()
    splits = get_splits()

    new_items = []
    for item in splits:
        key = f"{item['symbol']}|{item['date']}|{item['ratio']}|{item['type']}"
        if key not in seen:
            seen.add(key)
            new_items.append(item)

    if new_items:
        for item in new_items:
            msg = (
                f"🚨 Новый сплит\n"
                f"Тикер: {item['symbol']}\n"
                f"Компания: {item['company']}\n"
                f"Биржа: {item['exchange']}\n"
                f"Тип: {item['type']}\n"
                f"Сплит: {item['ratio']}\n"
                f"Дата: {item['date']}"
            )
            send_telegram(msg)

        save_seen(seen)
        print(f"Отправлено новых событий: {len(new_items)}")
    else:
        print("Новых сплитов нет.")


if __name__ == "__main__":
    main()