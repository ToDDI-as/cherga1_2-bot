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
CACHE_FILE = "schedule_state_1_2.json"

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

def detect_date_from_text(text):
    now = get_kyiv_now()
    months = get_ukrainian_months()
    months_rev = {v: k for k, v in months.items()}
    text_lower = text.lower()

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

    if "завтра" in text_lower:
        target_dt = (now + timedelta(days=1)).date()
        return f"{target_dt.day} {months[target_dt.month].upper()}", target_dt

    today_dt = now.date()
    return f"{today_dt.day} {months[today_dt.month].upper()}", today_dt

def is_schedule_post(text):
    text_lower = normalize_text(text).lower()
    if "розпорядження щодо застосування графіків" in text_lower and "не надходило" in text_lower:
        return True
    if "графіки погодинних відключень" in text_lower and ("скасовано" in text_lower or "не застосовуватимуться" in text_lower):
        return True
    if re.search(r"(?:черга\s*)?[1-6]\.[1-2](?:\:|\s|$)", text_lower):
        return True
    return False

def extract_schedule_from_text(text):
    display_date, date_obj = detect_date_from_text(text)
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
    soup = BeautifulSoup(html_text, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message_wrap")
    if not messages:
        return None

    for msg_wrap in reversed(messages[-4:]):
        text_div = msg_wrap.find("div", class_="tgme_widget_message_text")
        if not text_div:
            continue
        text = text_div.get_text(separator="\n")
        if is_schedule_post(text):
            display_date, date_obj, schedule = extract_schedule_from_text(text)

            # Витягуємо точний час публікації поста з <time datetime="...">
            published_dt = get_kyiv_now()
            time_tag = msg_wrap.find("time", attrs={"datetime": True})
            if time_tag:
                try:
                    raw_iso = time_tag["datetime"].replace("Z", "+00:00")
                    published_dt = datetime.fromisoformat(raw_iso).astimezone(KYIV_TZ)
                except Exception:
                    pass

            return {
                "display_date": display_date,
                "date_obj": date_obj,
                "published_ts": published_dt.timestamp(),
                "published_str": published_dt.strftime("%Y-%m-%d %H:%M"),
                "schedule": schedule,
                "source": "📢 Telegram-канал ЗОЕ"
            }
    return None

def parse_zoe_site(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup(["script", "style"]):
        script.decompose()

    text_full = soup.get_text(separator="\n")
    if is_schedule_post(text_full):
        display_date, date_obj, schedule = extract_schedule_from_text(text_full)
        now_kyiv = get_kyiv_now()

        # Витягуємо час оновлення із заголовка сайту, наприклад "(оновлено 11:53)"
        published_dt = now_kyiv
        upd_match = re.search(r"оновлено\s*(\d{1,2}):(\d{2})", text_full.lower())
        if upd_match:
            h, m = int(upd_match.group(1)), int(upd_match.group(2))
            candidate_dt = now_kyiv.replace(hour=h, minute=m, second=0, microsecond=0)
            # Якщо час на сайті більший за поточний (наприклад 23:50, а зараз 00:05) — це вчорашній вечір
            if candidate_dt > now_kyiv + timedelta(minutes=10):
                candidate_dt -= timedelta(days=1)
            published_dt = candidate_dt

        return {
            "display_date": display_date,
            "date_obj": date_obj,
            "published_ts": published_dt.timestamp(),
            "published_str": published_dt.strftime("%Y-%m-%d %H:%M"),
            "schedule": schedule,
            "source": "🌐 Сайт ЗОЕ"
        }
    return None

def load_state():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"max_date": "", "latest_published_ts": 0, "last_payload": ""}

def save_state(state):
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def check_and_notify():
    timestamp = get_kyiv_now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{timestamp}] Перевірка джерел...", flush=True)

    candidates = []

    tg_html = get_telegram_page()
    if tg_html:
        tg_res = parse_telegram_channel(tg_html)
        if tg_res:
            candidates.append(tg_res)

    site_html = get_zoe_site_page()
    if site_html:
        site_res = parse_zoe_site(site_html)
        if site_res:
            candidates.append(site_res)

    if not candidates:
        print("Актуальних графіків у джерелах не знайдено.", flush=True)
        return

    # Обираємо джерело з найновішою датою графіка та найсвіжішим часом публікації
    candidates.sort(key=lambda x: (x["date_obj"], x["published_ts"]), reverse=True)
    best = candidates[0]

    state = load_state()
    max_date_str = state.get("max_date", "")
    max_date_obj = datetime.strptime(max_date_str, "%Y-%m-%d").date() if max_date_str else None
    latest_pub_ts = state.get("latest_published_ts", 0)
    last_payload = state.get("last_payload", "")

    cand_date_str = best["date_obj"].strftime("%Y-%m-%d")
    new_payload = f"{cand_date_str} | {best['schedule']}".strip()

    # 1. Захист від старішої дати
    if max_date_obj and best["date_obj"] < max_date_obj:
        print(f"Ігноруємо стару дату {best['display_date']}.", flush=True)
        return

    # 2. Захист від старішої публікації за ту саму дату (наприклад, пост о 07:05 після оновлення сайту об 11:53)
    if max_date_obj and best["date_obj"] == max_date_obj and best["published_ts"] < latest_pub_ts - 60:
        print(f"Ігноруємо старішу публікацію ({best['source']} від {best['published_str']}).", flush=True)
        return

    # 3. Перевірка, чи змінилися самі години для Черги 1.2
    if new_payload != last_payload:
        print(f"⚡ Новий графік ({best['source']} від {best['published_str']})! Відправляємо...", flush=True)
        msg = (
            f"⚡ <b>ОНОВЛЕННЯ ГРАФІКА ЗОЕ</b>\n"
            f"📅 <b>Графік на дату:</b> <u>{best['display_date']}</u>\n"
            f"ℹ️ <b>Джерело:</b> {best['source']}\n"
            f"🕒 <i>Отримано: {timestamp}</i>\n\n"
            f"📍 <b>Черга 1.2:</b>\n"
            f"{best['schedule']}"
        )
        if send_telegram(msg):
            save_state({
                "max_date": cand_date_str,
                "latest_published_ts": best["published_ts"],
                "last_payload": new_payload
            })
            print("✅ Стан оновлено.", flush=True)
    else:
        # Навіть якщо години для 1.2 не змінились, оновлюємо мітку часу публікації, щоб не відкотитися назад
        if best["published_ts"] > latest_pub_ts:
            save_state({
                "max_date": cand_date_str,
                "latest_published_ts": best["published_ts"],
                "last_payload": last_payload
            })
        print(f"Змін у графіку немає ({best['source']}).", flush=True)

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
