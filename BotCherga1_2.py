import os
import re
import threading
import time
from datetime import datetime
from flask import Flask
import requests
import cloudscraper
from bs4 import BeautifulSoup

# --- Налаштування Flask для Render Web Service ---
app = Flask(__name__)

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- Основні конфігурації бота ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8631538633:AAHg84NPAG9ozFxm9_Ikjtd9H2YpeKZ659c")
CHAT_ID = os.environ.get("CHAT_ID", "1033814627")

TARGET_SITE_URL = "https://www.zoe.com.ua/outage/"
TARGET_TG_URL = "https://t.me/s/Zaporizhzhyaoblenergo_news"

CHECK_INTERVAL_SECONDS = 300  # Перевірка кожні 5 хвилин
CACHE_FILE = "last_schedule_1_2.txt"

PROXIES_LIST = [
    None,
    {"http": "http://194.44.208.194:8080", "https": "http://194.44.208.194:8080"},
    {"http": "http://185.233.112.222:8080", "https": "http://185.233.112.222:8080"},
    {"http": "http://195.162.81.189:8080", "https": "http://195.162.81.189:8080"},
]

def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        print(f"❌ Помилка відправки в Telegram: {e}", flush=True)
        return False

def normalize_text(text):
    if not text:
        return ""
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def get_telegram_page():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        response = requests.get(TARGET_TG_URL, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        print(f"⚠️ Помилка отримання Telegram-сторінки: {e}", flush=True)
    return None

def get_zoe_site_page():
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    for proxy in PROXIES_LIST:
        try:
            if proxy:
                response = scraper.get(TARGET_SITE_URL, timeout=15, proxies=proxy)
            else:
                response = scraper.get(TARGET_SITE_URL, timeout=15)

            if response.status_code == 200:
                return response.text
        except Exception:
            continue
    print("⚠️ Не вдалося з'єднатися із сайтом ЗОЕ.", flush=True)
    return None

def get_ukrainian_months():
    return {
        1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
        5: "травня", 6: "червня", 7: "липня", 8: "серпня",
        9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня"
    }

def extract_schedule_from_text(text):
    """Універсальний парсинг Черги 1.2 з підтримкою багаторядкових графіків та 'з .. до ..'"""
    now = datetime.now()
    months = get_ukrainian_months()
    today_num = now.day
    display_date = f"{today_num} {months[now.month].upper()}"

    full_text_lower = normalize_text(text).lower()
    if "розпорядження щодо застосування графіків" in full_text_lower and "не надходило" in full_text_lower:
        return display_date, "🟢 Відключень для населення наразі немає (розпорядження НЕК Укренерго не надходило)."

    lines = [normalize_text(line) for line in text.splitlines() if line.strip()]

    time_slots = []
    capture = False

    for line in lines:
        line_clean = line.replace("⚡", "").strip()
        line_lower = line_clean.lower()

        # Початок блоку Черги 1.2
        if re.search(r"(?:черга\s*)?1\.2", line_lower):
            capture = True
            # Якщо години на тому ж рядку (наприклад "1.2: 09:00 - 13:00")
            match_same_line = re.search(r"(?:черга\s*)?1\.2\s*:\s*(.+)", line_clean, re.IGNORECASE)
            if match_same_line:
                slot = match_same_line.group(1).strip()
                if slot:
                    time_slots.append(slot)
            continue

        # Зупиняємо збір, якщо дійшли до іншої черги (наприклад "Черга 2.1", "Черга 1.1")
        if capture and re.search(r"(?:черга\s*)\d\.\d", line_lower):
            break

        # Якщо ми всередині блоку 1.2 і рядок містить години
        if capture:
            if re.search(r"\d{1,2}:\d{2}", line_clean):
                time_slots.append(line_clean)

    if time_slots:
        formatted_slots = "\n".join([f"• {slot}" for slot in time_slots])
        return display_date, formatted_slots

    return display_date, "🟢 Для Черги 1.2 відключень у цьому графіку немає."

def parse_telegram_channel(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    posts = soup.find_all("div", class_="tgme_widget_message_text")
    if not posts:
        return None, None

    # Перевіряємо останні пости
    for post in reversed(posts[-3:]):
        text = post.get_text(separator="\n")
        date, schedule = extract_schedule_from_text(text)
        if "немає" not in schedule:
            return date, schedule

    return extract_schedule_from_text(posts[-1].get_text(separator="\n"))

def parse_zoe_site(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup(["script", "style"]):
        script.decompose()

    text_full = soup.get_text(separator="\n")
    return extract_schedule_from_text(text_full)

def check_and_notify():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Перевірка джерел...", flush=True)

    print(" 📡 Перевіряємо Telegram-канал @Zaporizhzhyaoblenergo_news...", flush=True)
    tg_html = get_telegram_page()
    if tg_html:
        tg_date, tg_schedule = parse_telegram_channel(tg_html)
        if tg_schedule:
            if process_schedule_data(tg_date, tg_schedule, "📢 Telegram-канал ЗОЕ", timestamp):
                return

    print(" 🌐 Перевіряємо сайт ЗОЕ...", flush=True)
    site_html = get_zoe_site_page()
    if site_html:
        site_date, site_schedule = parse_zoe_site(site_html)
        process_schedule_data(site_date, site_schedule, "🌐 Сайт ЗОЕ", timestamp)

def process_schedule_data(target_date, schedule_1_2, source_name, timestamp):
    current_data = f"{target_date} | {schedule_1_2}".strip()

    last_data = ""
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            last_data = f.read().strip()

    if current_data != last_data:
        print(f"⚡ Зміни виявлено з джерела: {source_name}! Відправляємо в Telegram...", flush=True)
        msg = (
            f"⚡ <b>ОНОВЛЕННЯ ГРАФІКА ЗОЕ</b>\n"
            f"📅 <b>Графік на дату:</b> <u>{target_date}</u>\n"
            f"ℹ️ <b>Джерело:</b> {source_name}\n"
            f"🕒 <i>Отримано: {timestamp}</i>\n\n"
            f"📍 <b>Черга 1.2:</b>\n"
            f"{schedule_1_2}"
        )
        if send_telegram(msg):
            with open(CACHE_FILE, "w", encoding="utf-8") as f:
                f.write(current_data)
            print("✅ Збережено в кеш.", flush=True)
            return True
    else:
        print(f"Змін у графіку немає ({source_name}).", flush=True)
    return False

def bot_loop():
    print("🚀 Безкоштовний автономний моніторинг ЗОЕ запущено!", flush=True)

    while True:
        try:
            check_and_notify()
        except Exception as e:
            print(f"❌ Загальна помилка під час перевірки: {e}", flush=True)

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_flask()
