import json
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
CACHE_FILE = "schedules_by_date_1_2.json"

KYIV_TZ = timezone(timedelta(hours=2))

def get_kyiv_now():
    return datetime.now(KYIV_TZ)

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
    text = re.sub(r"\bз\s*(\d{1,2}:\d{2})\s*до\s*(\d{1,2}:\d{2})", r"\1 - \2", text, flags=re.IGNORECASE)
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
    return proxies[:6]

def get_zoe_site_page():
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    headers = {
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache"
    }

    try:
        response = scraper.get(TARGET_SITE_URL, headers=headers, timeout=15)
        if response.status_code == 200 and "1.2" in response.text:
            return response.text
    except Exception:
        pass

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

def detect_date_from_text(text, post_dt):
    """
    Визначає цільову дату графіка відносно часу публікації поста (post_dt):
    1. Явна дата (14 листопада / 14.11 / 14.11.2026)
    2. Слово 'завтра' -> post_dt.date() + 1 день
    3. Слово 'сьогодні' або без вказівки -> post_dt.date()
    """
    months = get_ukrainian_months()
    months_rev = {v: k for k, v in months.items()}
    text_lower = text.lower()
    base_date = post_dt.date()

    # 1. Словесний формат: "14 листопада", "04 листопада"
    match_word = re.search(
        r"(\d{1,2})\s+(січня|лютого|березня|квітня|травня|червня|липня|серпня|вересня|жовтня|листопада|грудня)",
        text_lower
    )
    if match_word:
        day_num = int(match_word.group(1))
        month_word = match_word.group(2)
        month_num = months_rev[month_word]
        year = base_date.year + 1 if (base_date.month == 12 and month_num == 1) else base_date.year
        try:
            dt_obj = datetime(year, month_num, day_num).date()
            return f"{day_num} {month_word.upper()}", dt_obj
        except ValueError:
            pass

    # 2. Числовий формат: "14.11.2026", "14.11", "14/11"
    match_num = re.search(r"\b(\d{1,2})[\.\/](\d{1,2})(?:[\.\/](\d{2,4}))?\b", text_lower)
    if match_num:
        day_num = int(match_num.group(1))
        month_num = int(match_num.group(2))
        if 1 <= day_num <= 31 and 1 <= month_num <= 12:
            year = base_date.year + 1 if (base_date.month == 12 and month_num == 1) else base_date.year
            try:
                dt_obj = datetime(year, month_num, day_num).date()
                return f"{day_num} {months[month_num].upper()}", dt_obj
            except ValueError:
                pass

    # 3. Відносне слово "завтра" (рахуємо від дати виходу допису!)
    if re.search(r"\bзавтра\b", text_lower):
        target_dt = base_date + timedelta(days=1)
        return f"{target_dt.day} {months[target_dt.month].upper()}", target_dt

    # 4. Слово "сьогодні" або відсутність дати -> дата виходу допису
    return f"{base_date.day} {months[base_date.month].upper()}", base_date

def is_schedule_post(text):
    text_lower = normalize_text(text).lower()
    if "розпорядження щодо застосування графіків" in text_lower and "не надходило" in text_lower:
        return True
    if "графіки погодинних відключень" in text_lower and ("скасовано" in text_lower or "не застосовуватимуться" in text_lower):
        return True
    if re.search(r"(?:черга\s*)?[1-6]\.[1-2](?:\:|\s|$)", text_lower):
        return True
    return False

def extract_schedule_from_text(text, post_dt):
    display_date, date_obj = detect_date_from_text(text, post_dt)
    full_text_lower = normalize_text(text).lower()

    if "розпорядження щодо застосування графіків" in full_text_lower and "не надходило" in full_text_lower:
        return display_date, date_obj, "🟢 Відключень для населення наразі немає (розпорядження НЕК Укренерго не надходило)."

    if "графіки погодинних відключень" in full_text_lower and ("скасовано" in full_text_lower or "не застосовуватимуться" in full_text_lower):
        return display_date, date_obj, "🟢 Графіки погодинних відключень скасовано!"

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
    """Збирає всі релевантні графіки з останніх 5 постів (і на сьогодні, і на завтра)"""
    soup = BeautifulSoup(html_text, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message_wrap")
    results = []

    for msg_wrap in messages[-5:]:
        text_div = msg_wrap.find("div", class_="tgme_widget_message_text")
        if not text_div:
            continue
        text = text_div.get_text(separator="\n")
        if not is_schedule_post(text):
            continue

        published_dt = get_kyiv_now()
        time_tag = msg_wrap.find("time", attrs={"datetime": True})
        if time_tag:
            try:
                raw_iso = time_tag["datetime"].replace("Z", "+00:00")
                published_dt = datetime.fromisoformat(raw_iso).astimezone(KYIV_TZ)
            except Exception:
                pass

        display_date, date_obj, schedule = extract_schedule_from_text(text, published_dt)
        results.append({
            "display_date": display_date,
            "date_obj": date_obj,
            "published_ts": published_dt.timestamp(),
            "published_str": published_dt.strftime("%Y-%m-%d %H:%M"),
            "schedule": schedule,
            "source": "📢 Telegram-канал ЗОЕ"
        })

    return results

def parse_zoe_site(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup(["script", "style"]):
        script.decompose()

    text_full = soup.get_text(separator="\n")
    if not is_schedule_post(text_full):
        return []

    now_kyiv = get_kyiv_now()
    published_dt = now_kyiv
    upd_match = re.search(r"оновлено\s*(\d{1,2}):(\d{2})", text_full.lower())
    if upd_match:
        h, m = int(upd_match.group(1)), int(upd_match.group(2))
        candidate_dt = now_kyiv.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate_dt > now_kyiv + timedelta(minutes=10):
            candidate_dt -= timedelta(days=1)
        published_dt = candidate_dt

    display_date, date_obj, schedule = extract_schedule_from_text(text_full, published_dt)
    return [{
        "display_date": display_date,
        "date_obj": date_obj,
        "published_ts": published_dt.timestamp(),
        "published_str": published_dt.strftime("%Y-%m-%d %H:%M"),
        "schedule": schedule,
        "source": "🌐 Сайт ЗОЕ"
    }]

def load_state_by_date():
    """Формат: {'2026-11-13': {'latest_ts': 123456, 'schedule': '...'}, '2026-11-14': {...}}"""
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_state_by_date(state, today_obj):
    # Автоматично видаляємо з кешу всі вчорашні та старіші дати (< today)
    today_str = today_obj.strftime("%Y-%m-%d")
    cleaned = {k: v for k, v in state.items() if k >= today_str}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

def check_and_notify():
    now_kyiv = get_kyiv_now()
    timestamp = now_kyiv.strftime("%Y-%m-%d %H:%M:%S")
    today_obj = now_kyiv.date()
    tomorrow_obj = today_obj + timedelta(days=1)

    print(f"[{timestamp}] Перевірка джерел (актуальне вікно: {today_obj} .. {tomorrow_obj})...", flush=True)

    all_candidates = []

    tg_html = get_telegram_page()
    if tg_html:
        all_candidates.extend(parse_telegram_channel(tg_html))

    site_html = get_zoe_site_page()
    if site_html:
        all_candidates.extend(parse_zoe_site(site_html))

    if not all_candidates:
        print("Актуальних графіків у джерелах не знайдено.", flush=True)
        return

    # Групуємо кандидатів за датою графіка, залишаючи для кожної дати лише найсвіжіший за часом публікації
    best_per_date = {}
    for cand in all_candidates:
        d_obj = cand["date_obj"]
        # Жорсткий фільтр: приймаємо ТІЛЬКИ сьогодні або завтра! Учорашні дати (після 00:00) відсікаються.
        if d_obj < today_obj or d_obj > tomorrow_obj:
            continue

        d_str = d_obj.strftime("%Y-%m-%d")
        if d_str not in best_per_date or cand["published_ts"] > best_per_date[d_str]["published_ts"]:
            best_per_date[d_str] = cand

    state = load_state_by_date()
    state_changed = False

    # Перевіряємо окремо "Сьогодні" та "Завтра" по порядку
    for d_str in sorted(best_per_date.keys()):
        cand = best_per_date[d_str]
        prev_record = state.get(d_str, {})
        prev_ts = prev_record.get("latest_ts", 0)
        prev_schedule = prev_record.get("schedule", "")

        # Ігноруємо, якщо джерело старіше за вже зафіксовану публікацію для цієї ж дати
        if cand["published_ts"] < prev_ts - 60:
            continue

        if cand["schedule"] != prev_schedule:
            day_label = "СЬОГОДНІ" if cand["date_obj"] == today_obj else "ЗАВТРА"
            print(f"⚡ Оновлення на {day_label} ({cand['display_date']}) з {cand['source']}! Відправляємо...", flush=True)
            msg = (
                f"⚡ <b>ОНОВЛЕННЯ ГРАФІКА ЗОЕ ({day_label})</b>\n"
                f"📅 <b>Графік на дату:</b> <u>{cand['display_date']}</u>\n"
                f"ℹ️ <b>Джерело:</b> {cand['source']}\n"
                f"🕒 <i>Отримано: {timestamp}</i>\n\n"
                f"📍 <b>Черга 1.2:</b>\n"
                f"{cand['schedule']}"
            )
            if send_telegram(msg):
                state[d_str] = {
                    "latest_ts": cand["published_ts"],
                    "schedule": cand["schedule"]
                }
                state_changed = True
        else:
            if cand["published_ts"] > prev_ts:
                state[d_str] = {
                    "latest_ts": cand["published_ts"],
                    "schedule": prev_schedule
                }
                state_changed = True

    if state_changed:
        save_state_by_date(state, today_obj)
    else:
        print("Змін у графіках на сьогодні/завтра немає.", flush=True)

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
