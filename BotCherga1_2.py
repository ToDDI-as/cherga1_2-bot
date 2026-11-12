import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
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

def get_kyiv_now():
    """Отримання поточного київського часу (UTC+2)"""
    kyiv_tz = timezone(timedelta(hours=2))
    return datetime.now(kyiv_tz)

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
    # Усі види тире замінюємо на стандартний дефіс
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", text)
    # Зводимо "з 17:30 до 22:30" до "17:30 - 22:30"
    text = re.sub(r"\bз\s*(\d{1,2}:\d{2})\s*до\s*(\d{1,2}:\d{2})", r"\1 - \2", text, flags=re.IGNORECASE)
    # Нормалізуємо пробіли
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

def detect_date_from_text(text, post_date=None):
    """Строге визначення дати з підтримкою словесного та числового (ДД.ММ) форматів"""
    now = post_date if post_date else get_kyiv_now()
    months = get_ukrainian_months()
    text_lower = text.lower()

    # 1. Словесний формат: "11 листопада", "04 листопада"
    match_word_date = re.search(
        r"(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)",
        text_lower
    )
    if match_word_date:
        day_num = int(match_word_date.group(1))
        month_str = match_word_date.group(2).upper()
        return f"{day_num} {month_str}"

    # 2. Числовий формат: "11.11.2026", "11.11", "11/11" (ігноруємо час виду 11:11)
    match_num_date = re.search(r"\b(\d{1,2})[\.\/](\d{1,2})(?:[\.\/]\d{2,4})?\b", text_lower)
    if match_num_date:
        day_num = int(match_num_date.group(1))
        month_num = int(match_num_date.group(2))
        if 1 <= day_num <= 31 and 1 <= month_num <= 12:
            return f"{day_num} {months[month_num].upper()}"

    # 3. Слово "завтра"
    if "завтра" in text_lower:
        target_dt = now + timedelta(days=1)
        return f"{target_dt.day} {months[target_dt.month].upper()}"

    # 4. Дефолт — поточна дата
    return f"{now.day} {months[now.month].upper()}"

def extract_schedule_from_text(text):
    """Універсальний строгий парсинг Черги 1.2 для комбінованих та багаторядкових графіків"""
    display_date = detect_date_from_text(text)
    full_text_lower = normalize_text(text).lower()

    if "розпорядження щодо застосування графіків" in full_text_lower and "не надходило" in full_text_lower:
        return display_date, "🟢 Відключень для населення наразі немає (розпорядження НЕК Укренерго не надходило)."

    lines = [normalize_text(line) for line in text.splitlines() if line.strip()]

    time_slots = []
    capture = False

    for line in lines:
        line_clean = line.replace("⚡", "").replace("•", "").strip()
        line_lower = line_clean.lower()

        # 1. Початок блоку Черги 1.2
        if re.search(r"^(?:черга\s*)?1\.2(?:\:|\s|$)", line_lower):
            capture = True
            # Якщо є години на цьому ж рядку (наприклад "Черга 1.2: 00:00 - 02:00;")
            match_same_line = re.search(r"^(?:черга\s*)?1\.2\s*:\s*(.+)", line_clean, re.IGNORECASE)
            if match_same_line:
                slot = match_same_line.group(1).strip().rstrip(";").rstrip(",")
                if slot:
                    time_slots.append(slot)
            continue

        # 2. Умова зупинки: якщо дійшли до іншої черги (наприклад "Черга 2.1:", "2.1:") або стороннього тексту
        if capture:
            if re.search(r"^(?:черга\s*)?\d\.\d(?:\:|\s|$)", line_lower) or not re.search(r"\d{1,2}:\d{2}", line_clean):
                break

            # Якщо це продовження годин для 1.2 на наступних рядках
            if re.search(r"\d{1,2}:\d{2}", line_clean):
                slot_clean = line_clean.rstrip(";").rstrip(",")
                time_slots.append(slot_clean)

    if time_slots:
        formatted_slots = "\n".join([f"• {slot}" for slot in time_slots])
        return display_date, formatted_slots

    return display_date, "🟢 Для Черги 1.2 відключень у цьому графіку немає."

def is_schedule_post(text):
    """Перевіряє, чи є текст реальним графіком або повідомленням про його відсутність"""
    text_lower = normalize_text(text).lower()
    
    # Ознака 1: Явна фраза про відсутність/скасування ГПВ
    if "розпорядження щодо застосування графіків" in text_lower and "не надходило" in text_lower:
        return True
    if "графіки погодинних відключень" in text_lower and ("скасовано" in text_lower or "не застосовуватимуться" in text_lower):
        return True
        
    # Ознака 2: Наявність у тексті хоча б однієї черги (наприклад 1.1, 2.2, 6.1)
    if re.search(r"(?:черга\s*)?[1-6]\.[1-2](?:\:|\s|$)", text_lower):
        return True
        
    return False

def parse_telegram_channel(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    posts = soup.find_all("div", class_="tgme_widget_message_text")
    if not posts:
        return None, None

    # Перевіряємо останні 3 пости від найновішого до старішого
    for post in reversed(posts[-3:]):
        text = post.get_text(separator="\n")
        if is_schedule_post(text):
            return extract_schedule_from_text(text)

    # Якщо серед останніх 3 постів немає жодного графіка — ігноруємо
    return None, None

def parse_zoe_site(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup(["script", "style"]):
        script.decompose()

    text_full = soup.get_text(separator="\n")
    return extract_schedule_from_text(text_full)

def check_and_notify():
    timestamp = get_kyiv_now().strftime("%Y-%m-%d %H:%M:%S")
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
