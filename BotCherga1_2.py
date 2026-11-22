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
TELEGRAM_CHANNELS = [
    {"url": "https://t.me/s/Zaporizhzhyaoblenergo_news", "label": "📢 Telegram-канал ЗОЕ"},
    {"url": "https://t.me/s/info_zp", "label": "📢 ЗАПОРІЖЖЯ.ІНФО"},
]

CHECK_INTERVAL_SECONDS = 300  # Перевірка кожні 5 хвилин
MAX_POST_AGE_MINUTES = 45     # Ігноруємо пости в Telegram, старші за 45 хвилин
CACHE_FILE = "schedules_by_date_1_2.json"

KYIV_TZ = timezone(timedelta(hours=2))

def get_kyiv_now():
    return datetime.now(KYIV_TZ)

def update_pinned_state_board(schedule_text):
    """Тихо оновлює текст уже закріпленого повідомлення (без сервісних повідомлень про закріплення)"""
    try:
        chat_resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getChat",
            params={"chat_id": CHAT_ID},
            timeout=10
        )
        pinned = chat_resp.json().get("result", {}).get("pinned_message") if chat_resp.ok else None

        if pinned and "message_id" in pinned:
            requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
                json={
                    "chat_id": CHAT_ID,
                    "message_id": pinned["message_id"],
                    "text": schedule_text,
                    "parse_mode": "HTML"
                },
                timeout=10
            )
        else:
            send_resp = requests.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": CHAT_ID, "text": schedule_text, "parse_mode": "HTML"},
                timeout=10
            )
            if send_resp.ok:
                msg_id = send_resp.json().get("result", {}).get("message_id")
                if msg_id:
                    requests.post(
                        f"https://api.telegram.org/bot{BOT_TOKEN}/pinChatMessage",
                        json={"chat_id": CHAT_ID, "message_id": msg_id, "disable_notification": True},
                        timeout=10
                    )
    except Exception as e:
        print(f"⚠️ Помилка оновлення закріпленого стану: {e}", flush=True)

def send_telegram(text, pin=True):
    """Надсилає чисте повідомлення користувачу та тихо оновлює закріплений стан"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        if response.ok:
            if pin:
                update_pinned_state_board(text)
            return True
        return False
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

def get_telegram_page(tg_url):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9"
    }
    try:
        response = requests.get(tg_url, headers=headers, timeout=15)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        print(f"⚠️ Помилка отримання {tg_url}: {e}", flush=True)
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

def get_ukrainian_months():
    return {
        1: "січня", 2: "лютого", 3: "березня", 4: "квітня",
        5: "травня", 6: "червня", 7: "липня", 8: "серпня",
        9: "вересня", 10: "жовтня", 11: "листопада", 12: "грудня"
    }

def get_zoe_site_page():
    """Завантаження свіжої сторінки сайту ЗОЕ з обходом кешу шлюзів та блокувань"""
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8",
        "Cache-Control": "no-cache, no-store, must-revalidate",
        "Pragma": "no-cache"
    }

    now_kyiv = get_kyiv_now()
    months = get_ukrainian_months()
    today_marker = f"{now_kyiv.day} {months[now_kyiv.month]}"
    tomorrow_dt = now_kyiv + timedelta(days=1)
    tomorrow_marker = f"{tomorrow_dt.day} {months[tomorrow_dt.month]}"

    def is_fresh_zoe_html(html):
        if not html:
            return False
        html_lower = html.lower()
        has_schedule = ("гпв" in html_lower or "черг" in html_lower) and ("1.1" in html_lower or "2.1" in html_lower)
        if not has_schedule:
            return False
        return (today_marker in html_lower) or (tomorrow_marker in html_lower)

    ts_buster = int(time.time())
    target_with_buster = f"{TARGET_SITE_URL}?_={ts_buster}"

    # 1. Прямий запит
    try:
        response = scraper.get(target_with_buster, headers=headers, timeout=15)
        if response.status_code == 200 and is_fresh_zoe_html(response.text):
            return response.text
    except Exception:
        pass

    # 2. HTTP-шлюзи з унікальним параметром проти кешування
    encoded_url = requests.utils.quote(target_with_buster, safe="")
    gateways = [
        f"https://api.codetabs.com/v1/proxy?quest={encoded_url}",
        f"https://api.allorigins.win/raw?url={encoded_url}",
        f"https://corsproxy.io/?{encoded_url}",
    ]
    for gw_url in gateways:
        try:
            resp = requests.get(gw_url, headers=headers, timeout=15)
            if resp.status_code == 200 and is_fresh_zoe_html(resp.text):
                return resp.text
        except Exception:
            continue

    # 3. Перебір свіжих проксі
    for proxy in fetch_fresh_proxies():
        try:
            response = scraper.get(target_with_buster, headers=headers, timeout=10, proxies=proxy)
            if response.status_code == 200 and is_fresh_zoe_html(response.text):
                return response.text
        except Exception:
            continue

    print("⚠️ Не вдалося отримати свіжу сторінку сайту ЗОЕ за сьогодні/завтра.", flush=True)
    return None

def detect_date_from_text(text, post_dt):
    months = get_ukrainian_months()
    months_rev = {v: k for k, v in months.items()}
    text_lower = text.lower()
    base_date = post_dt.date()

    # 1. Словесний формат: "22 листопада"
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

    # 2. Числовий формат: "22.11.2026", "22.11"
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

    # 3. Відносне слово "завтра"
    if re.search(r"\bзавтра\b", text_lower):
        target_dt = base_date + timedelta(days=1)
        return f"{target_dt.day} {months[target_dt.month].upper()}", target_dt

    # 4. Слово "сьогодні" або дефолт — дата виходу допису
    return f"{base_date.day} {months[base_date.month].upper()}", base_date

def restore_state_from_pinned():
    """Відновлює стан із закріпленого повідомлення в Telegram після рестарту Render"""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getChat"
    try:
        resp = requests.get(url, params={"chat_id": CHAT_ID}, timeout=10)
        if not resp.ok:
            return {}
        pinned = resp.json().get("result", {}).get("pinned_message")
        if not pinned or "text" not in pinned:
            return {}

        pinned_text = pinned["text"]
        now_kyiv = get_kyiv_now()

        date_match = re.search(r"Графік на дату:\s*(.+)", pinned_text)
        sched_match = re.search(r"Черга 1\.2:\s*\n(.+)", pinned_text, re.DOTALL)

        if date_match and sched_match:
            _, date_obj = detect_date_from_text(date_match.group(1), now_kyiv)
            schedule_str = sched_match.group(1).strip()
            d_str = date_obj.strftime("%Y-%m-%d")
            print(f"♻️ Відновлено пам'ять із закріпленого повідомлення за {d_str}!", flush=True)
            return {
                d_str: {
                    "latest_ts": 0,
                    "schedule": schedule_str
                }
            }
    except Exception as e:
        print(f"⚠️ Не вдалося відновити стан із закріпленого повідомлення: {e}", flush=True)
    return {}

def is_schedule_post(text):
    text_lower = normalize_text(text).lower()
    if "розпорядження щодо застосування графіків" in text_lower and "не надходило" in text_lower:
        return True
    if "графіки погодинних відключень" in text_lower and ("скасовано" in text_lower or "не застосовуватимуться" in text_lower):
        return True
    # Має бути хоча б 2 черги (наприклад 1.1 та 2.1), щоб випадкові новини з цифрами не проходили як графік
    queues_found = re.findall(r"(?:^|\n|\s)(?:черга\s*)?[1-6]\.[1-2](?:\:|\s|$)", text_lower)
    if len(set(queues_found)) >= 2:
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

    raw_intervals = []
    capture = False

    for line in lines:
        line_clean = line.replace("⚡", "").replace("•", "").strip()
        line_lower = line_clean.lower()

        if re.search(r"^(?:черга\s*)?1\.2(?:\:|\s|$)", line_lower):
            capture = True
            match_same_line = re.search(r"^(?:черга\s*)?1\.2\s*:\s*(.+)", line_clean, re.IGNORECASE)
            if match_same_line:
                found = re.findall(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", match_same_line.group(1))
                raw_intervals.extend(found)
            continue

        if capture:
            if re.search(r"^(?:черга\s*)?\d\.\d(?:\:|\s|$)", line_lower) or not re.search(r"\d{1,2}:\d{2}", line_clean):
                break
            found = re.findall(r"\d{1,2}:\d{2}\s*-\s*\d{1,2}:\d{2}", line_clean)
            raw_intervals.extend(found)

    if raw_intervals:
        unique_intervals = []
        for item in raw_intervals:
            clean_item = re.sub(r"\s*-\s*", " - ", item.strip())
            if clean_item not in unique_intervals:
                unique_intervals.append(clean_item)

        formatted_slots = "\n".join([f"• {slot}" for slot in unique_intervals])
        return display_date, date_obj, formatted_slots

    return display_date, date_obj, "🟢 Для Черги 1.2 відключень у цьому графіку немає."

def parse_telegram_channel(html_text, source_label):
    soup = BeautifulSoup(html_text, "html.parser")
    messages = soup.find_all("div", class_="tgme_widget_message_wrap")
    results = []
    now_kyiv = get_kyiv_now()

    # Переглядаємо останні 10 постів (актуально для новинних каналів на кшталт ЗАПОРІЖЖЯ.ІНФО)
    for msg_wrap in messages[-10:]:
        text_div = msg_wrap.find("div", class_="tgme_widget_message_text")
        if not text_div:
            continue
        text = text_div.get_text(separator="\n")
        if not is_schedule_post(text):
            continue

        published_dt = now_kyiv
        time_tag = msg_wrap.find("time", attrs={"datetime": True})
        if time_tag:
            try:
                raw_iso = time_tag["datetime"].replace("Z", "+00:00")
                published_dt = datetime.fromisoformat(raw_iso).astimezone(KYIV_TZ)
            except Exception:
                pass

        age_minutes = (now_kyiv - published_dt).total_seconds() / 60.0
        if age_minutes > MAX_POST_AGE_MINUTES:
            continue

        display_date, date_obj, schedule = extract_schedule_from_text(text, published_dt)
        results.append({
            "display_date": display_date,
            "date_obj": date_obj,
            "published_ts": published_dt.timestamp(),
            "published_str": published_dt.strftime("%Y-%m-%d %H:%M"),
            "schedule": schedule,
            "source": source_label
        })

    return results

def parse_zoe_site(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup(["script", "style", "header", "footer", "nav"]):
        script.decompose()

    text_full = soup.get_text(separator="\n")
    lines = [line.strip() for line in text_full.splitlines() if line.strip()]

    start_idx = 0
    for i, line in enumerate(lines):
        if re.search(r"(?:оновлено\s*гпв|графік\s*погодинних|за\s*вказівкою\s*нек)", line.lower()):
            start_idx = i
            break

    block_lines = []
    for line in lines[start_idx:]:
        if len(block_lines) > 3 and re.search(r"^(?:оновлено\s*гпв|графік\s*погодинних|за\s*вказівкою\s*нек|архів)", line.lower()):
            break
        block_lines.append(line)

    first_block_text = "\n".join(block_lines)

    if not is_schedule_post(first_block_text):
        return []

    now_kyiv = get_kyiv_now()
    published_dt = now_kyiv

    upd_match = re.search(r"оновлено\s*(\d{1,2}):(\d{2})", first_block_text.lower())
    if upd_match:
        h, m = int(upd_match.group(1)), int(upd_match.group(2))
        candidate_dt = now_kyiv.replace(hour=h, minute=m, second=0, microsecond=0)
        if candidate_dt > now_kyiv + timedelta(minutes=10):
            candidate_dt -= timedelta(days=1)
        published_dt = candidate_dt

    display_date, date_obj, schedule = extract_schedule_from_text(first_block_text, published_dt)
    return [{
        "display_date": display_date,
        "date_obj": date_obj,
        "published_ts": published_dt.timestamp(),
        "published_str": published_dt.strftime("%Y-%m-%d %H:%M"),
        "schedule": schedule,
        "source": "🌐 Сайт ЗОЕ"
    }]

def load_state_by_date():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    restored = restore_state_from_pinned()
    if restored:
        save_state_by_date(restored, get_kyiv_now().date())
    return restored

def save_state_by_date(state, today_obj):
    today_str = today_obj.strftime("%Y-%m-%d")
    cleaned = {k: v for k, v in state.items() if k >= today_str}
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=2)

def check_and_notify():
    now_kyiv = get_kyiv_now()
    timestamp = now_kyiv.strftime("%Y-%m-%d %H:%M:%S")
    today_obj = now_kyiv.date()
    tomorrow_obj = today_obj + timedelta(days=1)

    print(f"[{timestamp}] Перевірка всіх джерел...", flush=True)

    all_candidates = []

    # 1. Перевіряємо всі Telegram-канали (ЗОЕ та ЗАПОРІЖЖЯ.ІНФО)
    for ch in TELEGRAM_CHANNELS:
        tg_html = get_telegram_page(ch["url"])
        if tg_html:
            all_candidates.extend(parse_telegram_channel(tg_html, ch["label"]))

    # 2. Перевіряємо офіційний сайт ЗОЕ
    site_html = get_zoe_site_page()
    if site_html:
        all_candidates.extend(parse_zoe_site(site_html))

    if not all_candidates:
        print("Актуальних оновлень у джерелах не знайдено.", flush=True)
        return

    best_per_date = {}
    for cand in all_candidates:
        d_obj = cand["date_obj"]
        if d_obj < today_obj or d_obj > tomorrow_obj:
            continue

        d_str = d_obj.strftime("%Y-%m-%d")
        if d_str not in best_per_date or cand["published_ts"] > best_per_date[d_str]["published_ts"]:
            best_per_date[d_str] = cand

    state = load_state_by_date()
    state_changed = False

    for d_str in sorted(best_per_date.keys()):
        cand = best_per_date[d_str]
        prev_record = state.get(d_str, {})
        prev_ts = prev_record.get("latest_ts", 0)
        prev_schedule = prev_record.get("schedule", "")

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
            if send_telegram(msg, pin=True):
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
        print("Нових змін у графіках немає.", flush=True)

def bot_loop():
    print("🚀 Безкоштовний автономний моніторинг ЗОЕ + ЗАПОРІЖЖЯ.ІНФО запущено!", flush=True)

    while True:
        try:
            check_and_notify()
        except Exception as e:
            print(f"❌ Загальна помилка під час перевірки: {e}", flush=True)

        time.sleep(CHECK_INTERVAL_SECONDS)

if __name__ == "__main__":
    threading.Thread(target=bot_loop, daemon=True).start()
    run_flask()
