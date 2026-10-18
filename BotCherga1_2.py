import os
import re
import time
from datetime import datetime, timedelta
import cloudscraper
from bs4 import BeautifulSoup
import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.ssl_ import create_urllib3_context

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BOT_TOKEN = "8631538633:AAHg84NPAG9ozFxm9_Ikjtd9H2YpeKZ659c"
CHAT_ID = "1033814627"
URL = "https://www.zoe.com.ua/outage/"
CHECK_INTERVAL_SECONDS = 300
CACHE_FILE = "last_schedule_1_2.txt"


class CustomSSLAdapter(HTTPAdapter):
    def init_poolmanager(self, *args, **kwargs):
        ctx = create_urllib3_context()
        ctx.check_hostname = False
        ctx.verify_mode = False
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def send_telegram(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=10)
        return response.ok
    except Exception as e:
        print(f"❌ Помилка відправки в Telegram: {e}")
        return False


def get_page():
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "desktop": True}
    )
    scraper.mount("https://", CustomSSLAdapter())
    scraper.verify = False

    try:
        response = scraper.get(URL, timeout=12)
        if response.status_code == 200:
            return response.text
    except Exception as e:
        print(f"⚠️ Помилка мережі при запиті до ЗОЕ: {e}")
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

    # Розраховуємо актуальні дати (сьогодні та завтра)
    now = datetime.now()
    months = get_ukrainian_months()
    
    today_num = now.day
    today_month = months[now.month]
    today_str = f"{today_num} {today_month}"  # наприклад "18 жовтня"
    
    tomorrow = now + timedelta(days=1)
    tomorrow_str = f"{tomorrow.day} {months[tomorrow.month]}"  # наприклад "19 жовтня"

    # 1. Розбиваємо текст на блоки новин ГПВ
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

    # 2. Шукаємо ГПВ блок ТІЛЬКИ для актуальних дат (сьогодні або завтра)
    relevant_block = None
    for block in gpv_blocks:
        h_date = block["header_date"].lower()
        # Перевіряємо чи входить сьогоднішня або завтрашня дата у заголовок
        if str(today_num) in h_date or today_str in h_date or str(tomorrow.day) in h_date or tomorrow_str in h_date:
            relevant_block = block
            break

    # Якщо знайшли актуальний блок ГПВ
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
            return header_date, f"• {raw_time}", "Знайдено актуальний графік"
        else:
            return header_date, "🟢 Для Черги 1.2 відключень у цьому графіку немає.", "Черга 1.2 не вимикається"

    # Якщо на сайті немає оприлюднених ГПВ за сьогодні/завтра
    return today_str, "🟢 Відключень для населення наразі немає (актуальний ГПВ не оприлюднено).", "Немає актуальних ГПВ"


def main():
    print("🚀 Скрипт моніторингу ЗОЕ запущено!")

    if os.path.exists(CACHE_FILE):
        os.remove(CACHE_FILE)

    while True:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        print(f"[{timestamp}] Перевіряємо сторінку ЗОЕ...")

        html = get_page()

        if html:
            target_date, schedule_1_2, status_note = parse_zoe_posts(html)
            current_data = f"{target_date} | {schedule_1_2}".strip()

            print(f"\n--- РЕЗУЛЬТАТ ПАРСИНГУ ---")
            print(f"📅 Дата: {target_date}")
            print(f"📍 Черга 1.2: {schedule_1_2}")
            print(f"ℹ️ Статус: {status_note}\n---------------------------\n")

            msg = (
                f"⚡ <b>ОНОВЛЕННЯ ГРАФІКА ЗОЕ</b>\n"
                f"📅 <b>Графік на дату:</b> <u>{target_date}</u>\n"
                f"🕒 <i>Отримано: {timestamp}</i>\n\n"
                f"📍 <b>Черга 1.2:</b>\n"
                f"{schedule_1_2}"
            )

            last_data = ""
            if os.path.exists(CACHE_FILE):
                with open(CACHE_FILE, "r", encoding="utf-8") as f:
                    last_data = f.read().strip()

            if current_data != last_data:
                print("⚡ Зміни виявлено! Відправляємо в Telegram...")
                if send_telegram(msg):
                    with open(CACHE_FILE, "w", encoding="utf-8") as f:
                        f.write(current_data)
            else:
                print("Змін порівняно з попереднім сповіщенням немає.")
        else:
            print("❌ Не вдалося завантажити сторінку.")

        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()