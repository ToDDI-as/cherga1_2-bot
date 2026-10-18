import os
import re
import threading
import time
from datetime import datetime, timedelta
from flask import Flask
import requests
from bs4 import BeautifulSoup

# --- Налаштування Flask для Render Web Service ---
app = Flask(__name__)

@app.route('/')
def home():
    return "ZOE Monitor Bot via ScraperAPI is running 24/7!", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- Основні конфігурації бота ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8631538633:AAHg84NPAG9ozFxm9_Ikjtd9H2YpeKZ659c")
CHAT_ID = os.environ.get("CHAT_ID", "1033814627")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "7ba5fdfc4a308ae20ca2793434044b0e")
TARGET_URL = "https://www.zoe.com.ua/outage/"
CHECK_INTERVAL_SECONDS = 180  # Перевірка кожні 3 хвилини
CACHE_FILE = "last_schedule_1_2.txt"

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        print(f"❌ Помилка відправки в Telegram: {e}", flush=True)
        return False

def get_page():
    if not SCRAPER_API_KEY:
        print("⚠️ Помилка: SCRAPER_API_KEY не знайдено!", flush=True)
        return None

    # Запит через ScraperAPI з українським IP (country_code=ua)
    scraper_url = f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}&url={TARGET_URL}&country_code=ua"
    
    try:
        response = requests.get(scraper_url, timeout=30)
        if response.status_code == 200:
            return response.text
        else:
            print(f"⚠️ ScraperAPI повернув статус: {response.status_code}", flush=True)
    except Exception as e:
        print(f"⚠️ Помилка мережі через ScraperAPI: {e}", flush=True)

    return None

def get_ukrainian_months():
    return {
        1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
        5: "травня", 6: "червня", 7: "липня", 8: "серпня",
        9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня"
    }

def parse_zoe_posts(html_text):
    soup = BeautifulSoup(html_text, "html.parser")

    for script in soup(["script", "style"]):
        script.decompose()

    text_full = soup.get_text(separator="\n")
    lines = [line.strip() for line in text_full.splitlines() if line.strip()]

    now = datetime.now()
    months = get_ukrainian_months()

    today_num = now.day
    today_month = months[now.month]
    today_str = f"{today_num} {today_month}"

    tomorrow = now + timedelta(days=1)
    tomorrow_str = f"{tomorrow.day} {months[tomorrow.month]}"

    date_pattern = re.compile(
        r"(\d{1,2}\s+(?:січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)|\d{1,2}\.\d{2}(?:\.\d{2,4})?)",
        re.IGNORECASE,
    )

    gpv_blocks = []
    current_block = None

    for line in lines:
        if ("ГПВ" in line.upper() or "ГРАФІК" in line.upper()) and "ПРОМИСЛОВОСТІ" not in line.upper():
            match_date = date_pattern.search(line)
            if match_date:
                if current_block:
                    gpv_blocks.append(current_block)
                current_block = {
                    "header_date": match_date.group(0),
                    "text": line
                }
                continue

        if current_block:
            if "ПРОМИСЛОВОСТІ" in line.upper() or "ДО УВАГИ" in line.upper():
                gpv_blocks.append(current_block)
                current_block = None
            else:
                current_block["text"] += " " + line

    if current_block:
        gpv_blocks.append(current_block)

    relevant_block = None
    for block in gpv_blocks:
        h_date = block["header_date"].lower()
        if str(today_num) in h_date or today_str in h_date or str(tomorrow.day) in h_date or tomorrow_str in h_date:
            relevant_block = block
            break

    if relevant_block:
        header_date = relevant_block["header_date"]
        block_text = relevant_block["text"]

        queue_pattern = re.compile(
            r"(?:Черга\s*)?1\.2\s*:\s*([\d\s\:\–\—\-\,]+?)(?=(?:Черга\s*)?\d\.\d\s*:|$)",
            re.IGNORECASE,
        )
        match_queue = queue_pattern.search(block_text)

        if match_queue:
            raw_time = match_queue.group(1).strip().rstrip(",")
            return header_date, f"• {raw_time}"
        else:
            return header_date, "🟢 Для Черги 1.2 відключень у цьому графіку немає."

    return today_str, "🟢 Відключень для населення наразі немає (актуальний ГПВ не оприлюднено)."

def bot_loop():
    print("🚀 Скрипт моніторингу ЗОЕ через ScraperAPI запущено!", flush=True)

    while True:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Перевіряємо сторінку ЗОЕ через ScraperAPI...", flush=True)

        html = get_page()

        if html:
            target_date, schedule_1_2 = parse_zoe_posts(html)
            current_data = f"{target_date} | {schedule_1_2}".strip()

            print(f"\n--- РЕЗУЛЬТАТ ПАРСИНГУ ---", flush=True)
            print(f"📅 Дата: {target_date}", flush=True)
            print(f"📍 Черга 1.2: {schedule_1_2}", flush=True)

            last_data = ""
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    last_data = f.read().strip()

            if current_data != last_data:
                print("⚡ Зміни виявлено! Відправляємо в Telegram...", flush=True)
                msg = (
                    f"⚡ <b>ОНОВЛЕННЯ ГРАФІКА ЗОЕ</b>\n"
                    f"📅 <b>Графік на дату:</b> <u>{target_date}</u>\n"
                    f"🕒 <i>Отримано: {timestamp}</i>\n\n"
                    f"📍 <b>Черга 1.2:</b>\n"
                    f"{schedule_1_2}"
                )
                if send_telegram(msg):
                    with open(CACHE_FILE, "w", encoding="utf-8") as f:
                        f.write(current_data)
                    print("✅ Збережено в кеш.", flush=True)
            else:
                print("Змін у графіку немає.", flush=True)

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_flask()
