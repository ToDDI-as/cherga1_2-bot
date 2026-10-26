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

@app.route('/', methods=['GET', 'HEAD'])
def home():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

# --- Основні конфігурації бота ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "8631538633:AAHg84NPAG9ozFxm9_Ikjtd9H2YpeKZ659c")
CHAT_ID = os.environ.get("CHAT_ID", "1033814627")
SCRAPER_API_KEY = os.environ.get("SCRAPER_API_KEY", "7ba5fdfc4a308ae20ca2793434044b0e")

TARGET_SITE_URL = "https://www.zoe.com.ua/outage/"
TARGET_TG_URL = "https://t.me/s/Zaporizhzhyaoblenergo_news"

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

def normalize_text(text):
    """Нормалізація тире та пробілів для уніфікації тексту з різних джерел"""
    if not text:
        return ""
    # Заміна усіх типів тире (em-dash, en-dash тощо) на стандартний дефіс
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", text)
    # Нормалізація пробілів
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def get_page(url):
    if not SCRAPER_API_KEY:
        print("⚠️ Помилка: SCRAPER_API_KEY не знайдено!", flush=True)
        return None

    cache_buster = int(time.time())
    scraper_url = (
        f"http://api.scraperapi.com?api_key={SCRAPER_API_KEY}"
        f"&url={url}&country_code=ua&_t={cache_buster}"
    )
    
    headers = {
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    try:
        response = requests.get(scraper_url, headers=headers, timeout=30)
        if response.status_code == 200:
            return response.text
        else:
            print(f"⚠️ ScraperAPI повернув статус {response.status_code} для {url}", flush=True)
    except Exception as e:
        print(f"⚠️ Помилка мережі через ScraperAPI для {url}: {e}", flush=True)

    return None

def get_ukrainian_months():
    return {
        1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
        5: "травня", 6: "червня", 7: "липня", 8: "серпня",
        9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня"
    }

def extract_schedule_from_text(text):
    """Універсальний пошук розкладу для Черги 1.2 у довільному тексті"""
    now = datetime.now()
    months = get_ukrainian_months()
    today_num = now.day
    today_month = months[now.month]

    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in lines:
        line_norm = normalize_text(line)
        line_lower = line_norm.lower()
        if re.search(r"(?:черга\s*)?1\.2\s*:\s*[\d\s\:\-\,]+", line_lower):
            match = re.search(r"(?:черга\s*)?1\.2\s*:\s*([\d\s\:\-\,]+)", line_norm, re.IGNORECASE)
            if match:
                raw_time = normalize_text(match.group(1)).rstrip(",")
                display_date = f"{today_num} {months[now.month].upper()}"
                return display_date, f"• {raw_time}"

    display_date = f"{today_num} {months[now.month].upper()}"
    return display_date, "🟢 Для Черги 1.2 відключень у цьому графіку немає."

def parse_telegram_channel(html_text):
    """Парсинг веб-версії Telegram-каналу"""
    soup = BeautifulSoup(html_text, "html.parser")
    posts = soup.find_all("div", class_="tgme_widget_message_text")
    
    if not posts:
        return None, None

    for post in reversed(posts[-5:]):
        text = post.get_text(separator="\n")
        text_lower = text.lower()
        
        if ("гпв" in text_lower or "графік" in text_lower or "оновлено" in text_lower or "відключен" in text_lower):
            date, schedule = extract_schedule_from_text(text)
            if "немає" not in schedule:
                return date, schedule

    return extract_schedule_from_text(posts[-1].get_text(separator="\n"))

def parse_zoe_site(html_text):
    """Парсинг офіційного сайту ЗОЕ"""
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup(["script", "style"]):
        script.decompose()

    text_full = soup.get_text(separator="\n")
    return extract_schedule_from_text(text_full)

def check_and_notify():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Перевірка джерел...", flush=True)

    # 1. Перевірка Telegram-каналу
    print(" 📡 Перевіряємо Telegram-канал @Zaporizhzhyaoblenergo_news...", flush=True)
    tg_html = get_page(TARGET_TG_URL)
    if tg_html:
        tg_date, tg_schedule = parse_telegram_channel(tg_html)
        if tg_schedule and "немає" not in tg_schedule:
            process_schedule_data(tg_date, tg_schedule, "📢 Telegram-канал ЗОЕ", timestamp)

    # 2. Перевірка веб-сайту ЗОЕ
    print(" 🌐 Перевіряємо сайт ЗОЕ...", flush=True)
    site_html = get_page(TARGET_SITE_URL)
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
    print("🚀 Скрипт моніторингу ЗОЕ (Сайт + Telegram) запущено!", flush=True)

    while True:
        try:
            check_and_notify()
        except Exception as e:
            print(f"❌ Загальна помилка під час перевірки: {e}", flush=True)

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_flask()
