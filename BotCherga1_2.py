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

def get_kyiv_now():
    """Поточний київський час (UTC+2)"""
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
    # Усі види тире -> стандартний дефіс
    text = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\u2212]", "-", text)
    # "з 17:30 до 22:30" -> "17:30 - 22:30"
    text = re.sub(r"\bз\s*(\d{1,2}:\d{2})\s*до\s*(\d{1,2}:\d{2})", r"\1 - \2", text, flags=re.IGNORECASE)
    # Нормалізація пробілів
    text = re.sub(r"\s+", " ", text)
    return text.strip()

def get_telegram_page():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9"
    }
    try:
        response = requests.get(TARGET_TG_URL, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        print(f"⚠️ Помилка отримання Telegram-сторінки: {e}", flush=True)
    return None

def fetch_fresh_proxies():
    """Отримує список свіжих безкоштовних проксі (Україна / Європа)"""
    proxies = []
    try:
        url = "https://api.proxyscrape.com/v2/?request=displayproxies&protocol=http&timeout=3000&country=UA,PL,DE,RO&ssl=all&anonymity=all"
        resp = requests.get(url, timeout=8)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                line = line.strip()
                if line and ":" in line:
                    proxies.append({"http": f"http://{line}", "https": f"http://{line}"})
    except Exception:
        pass
    return proxies[:6]  # Беремо перші 6 найшвидших

def get_zoe_site_page():
    """Завантаження сайту ЗОЕ напряму або через свіжі проксі"""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    headers = {
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    # 1. Спочатку пробуємо прямий запит (часто працює напряму через cloudscraper)
    try:
        response = scraper.get(TARGET_SITE_URL, headers=headers, timeout=15)
        if response.status_code == 200 and "1.2" in response.text:
            return response.text
    except Exception:
        pass

    # 2. Якщо прямий IP заблоковано — перебираємо свіжі проксі
    for proxy in fetch_fresh_proxies():
        try:
            response = scraper.get(TARGET_SITE_URL, headers=headers, timeout=10, proxies=proxy)
            if response.status_code == 200 and "1.2" in response.text:
                return response.text
        except Exception:
            continue

    print("⚠️ Сайт ЗОЕ тимчасово недоступний або заблокував запит.", flush=True)
    return None

def get_ukrainian_months():
    return {
        1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
        5: "травня", 6: "червня", 7: "липня", 8: "серпня",
        9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня"
    }

def detect_date_from_text(text):
    """Повертає кортеж (рядок_дати_для_виводу, об'єкт_date_для_порівняння_свіжості)"""
    now = get_kyiv_now()
    months = get_ukrainian_months()
    months_rev = {v: k for k, v in months.items()}
    text_lower = text.lower()

    # 1. Словесний формат: "12 листопада", "04 листопада"
    match_word = re.search(
        r"(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)",
        text_lower
    )
    if match_word:
        day_num = int(match_word.group(1))
        month_word = match_word.group(2)
        month_num = months_rev[month_word]
        year = now.year + 1 if (now.month == 12 and month_num == 1) else now.year
        try:
            dt_obj = datetime(year, month_num, day_num).date()
            return f"{day_num} {month_word.upper()}", dt_obj
        except ValueError:
            pass

    # 2. Числовий формат: "11.11.2026", "12.11"
    match_num = re.search(r"\b(\d{1,2})[\.\/](\d{1,2})(?:[\.\/](\d{2,4}))?\b", text_lower)
    if match_num:
        day_num = int(match_num.group(1))
        month_num = int(match_num.group(2))
        if 1 <= day_num <= 31 and 1 <= month_num <= 12:
            year = now.year + 1 if (now.month == 12 and month_num == 1) else now.year
            try:
                dt_obj = datetime(year, month_num, day_num).date()
                return f"{day_num} {months[month_num].upper()}", dt_obj
            except ValueError:
                pass

    # 3. Слово "завтра"
    if "завтра" in text_lower:
        target_dt = (now + timedelta(days=1)).date()
        return f"{target_dt.day} {months[target_dt.month].upper()}", target_dt

    # 4. Дефолт — сьогодні
    today_dt = now.date()
    return f"{today_dt.day} {months[today_dt.month].upper()}", today_dt

def is_schedule_post(text):
    """Фільтр: відсіює новини, залишає тільки реальні графіки або повідомлення про скасування"""
    text_lower = normalize_text(text).lower()
    if "розпорядження щодо застосування графіків" in text_lower and "не надходило" in text_lower:
        return True
    if "графіки погодинних відключень" in text_lower and ("скасовано" in text_lower or "не застосовуватимуться" in text_lower):
        return True
    if re.search(r"(?:черга\s*)?[1-6]\.[1-2](?:\:|\s|$)", text_lower):
        return True
    return False

def extract_schedule_from_text(text):
    """Повертає (рядок_дати, об'єкт_date, текст_графіка_1_2)"""
    display_date, date_obj = detect_date_from_text(text)
    full_text_lower = normalize_text(text).lower()

    if "розпорядження щодо застосування графіків" in full_text_lower and "не надходило" in full_text_lower:
        return display_date, date_obj, "🟢 Відключень для населення наразі немає (розпорядження НЕК Укренерго не надходило)."

    lines = [normalize_text(line) for line in text.splitlines() if line.strip()]

    time_slots = []
    capture = False

    for line in lines:
        line_clean = line.replace("⚡", "").replace("•", "").strip()
        line_lower = line_clean.lower()

        if re.search(r"^(?:черга\s*)?1\.2(?:\:|\s|$)", line_lower):
            capture = True
            match_same_line = re.search(r"^(?:черга\s*)?1\.2\s*:\s*(.+)", line_clean, re.IGNORECASE)
            if match_same_line:
                slot = match_same_line.group(1).strip().rstrip(";").rstrip(",")
                if slot:
                    time_slots.append(slot)
            continue

        if capture:
            if re.search(r"^(?:черга\s*)?\d\.\d(?:\:|\s|$)", line_lower) or not re.search(r"\d{1,2}:\d{2}", line_clean):
                break
            if re.search(r"\d{1,2}:\d{2}", line_clean):
                slot_clean = line_clean.rstrip(";").rstrip(",")
                time_slots.append(slot_clean)

    if time_slots:
        formatted_slots = "\n".join([f"• {slot}" for slot in time_slots])
        return display_date, date_obj, formatted_slots

    return display_date, date_obj, "🟢 Для Черги 1.2 відключень у цьому графіку немає."

def parse_telegram_channel(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    posts = soup.find_
