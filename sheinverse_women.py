import time
import json
import requests
import os
from typing import Dict, Set, List

# ========= CONFIG =========

BASE_API = "https://www.sheinindia.in/api/category/sverse-5939-37961"
POLL_INTERVAL_SEC = 8  # tune carefully for speed vs rate-limit

STATE_FILE = "sheinverse_state_v2.json"
# ==========================


def send_telegram_photo(caption: str, photo_url: str) -> None:
    bot_token = os.getenv("BOT_TOKEN")
    chat_id = os.getenv("CHAT_ID")
    if not bot_token or not chat_id:
        print("Missing BOT_TOKEN or CHAT_ID env vars!")
        return

    url = f"https://api.telegram.org/bot{bot_token}/sendPhoto"
    payload = {
        "chat_id": chat_id,
        "photo": photo_url,
        "caption": caption,
        "parse_mode": "HTML",
    }
    try:
        requests.post(url, json=payload, timeout=3)
    except Exception as e:
        print("Telegram photo error:", e)


def load_state() -> Dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {
            "seen_products": {}, 
            "restock_alerted": {},
            "last_total_results": 0,
            "summary_alerted": {}
        }
    except Exception as e:
        print("Error loading state:", e)
        return {"seen_products": {}, "restock_alerted": {}, "last_total_results": 0, "summary_alerted": {}}


def save_state(state: Dict) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except Exception as e:
        print("Error saving state:", e)


def fetch_page(page: int = 0) -> Dict:
    """
    Fetch one page of SHEINVERSE Women listing.
    Uses currentPage query param used by the site's pagination.
    """
    params = {
        "query": ":newn:genderfilter:Women",
        "currentPage": page,
    }

    headers = {
    "Host": "www.sheinindia.in",
    "User-Agent": "Mozilla/5.0 (Linux; Android 12; GM1911 Build/SKQ1.211113.001) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.7632.80 Mobile Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "DNT": "1",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "sec-ch-ua": '"Not:A-Brand";v="99", "Android WebView";v="145", "Chromium";v="145"',
    "sec-ch-ua-mobile": "?1",
    "sec-ch-ua-platform": '"Android"',
    "X-Requested-With": "mark.via.gp"
    }
    r = requests.get(BASE_API, params=params, headers=headers ,timeout=5)
    r.raise_for_status()
    return r.json()


def fetch_all_products() -> tuple[List[Dict], int]:
    """
    Walk all pages and return (products, total_results)
    """
    first = fetch_page(0)
    products = first.get("products", [])
    pagination = first.get("pagination", {})
    total_results = int(pagination.get("totalResults", 0))  # 🔥 Get totalResults[file:2]
    total_pages = int(pagination.get("totalPages", 1))

    # If there are more pages, fetch them
    for page in range(1, total_pages):
        try:
            data = fetch_page(page)
            products.extend(data.get("products", []))
        except Exception as e:
            print(f"Error fetching page {page}:", e)
            break

    return products, total_results


def extract_product_key(prod: Dict) -> str:
    """
    Unique key for product; here just use product code.
    You could also combine with colorGroup if you want per-color alerts.[file:2]
    """
    return str(prod.get("code", ""))


def product_to_message(prod: Dict, event_type: str = "NEW") -> tuple[str, str]:
    price = prod["price"]["displayformattedValue"]
    url_path = prod.get("url", "")
    link = f"https://www.sheinindia.in{url_path}" if url_path.startswith("/") else url_path

    caption = (
    f"<b>{price}</b> "
    f'<a href="{link}">Open</a>')

    images = prod.get("images") or []
    photo_url = images[0]["url"] if images else None

    return caption, photo_url


def summary_alert_message(current_total: int, new_count: int) -> str:
    """
    Summary alert when totalResults increases - matches your style
    """
    text = (
        f"<b>📊 STOCK UPDATE</b>\t"
        f"<b>Total: {current_total} \t (+{new_count})</b>"
    )
    return text


def main_loop():
    state = load_state()
    seen_products: Dict[str, Dict] = state.get("seen_products", {})
    restock_alerted: Dict[str, bool] = state.get("restock_alerted", {})
    last_total_results = state.get("last_total_results", 0)

    print(f"Starting monitor... Last total: {last_total_results}")

    while True:
        try:
            products, current_total_results = fetch_all_products()  # 🔥 Now returns total too
            print(f"Fetched {len(products)} products, totalResults: {current_total_results}")
        except Exception as e:
            print("Fetch error:", e)
            time.sleep(POLL_INTERVAL_SEC)
            continue

        # 🔥 SUMMARY ALERT - when totalResults increases
        if current_total_results > last_total_results:
            new_count = current_total_results - last_total_results
            #summary_msg = summary_alert_message(current_total_results, new_count)
            #send_telegram_message(summary_msg)
            print(f"📊 SUMMARY: {last_total_results} → {current_total_results} (+{new_count})")
            last_total_results = current_total_results

        # Individual product alerts (your existing logic)
        current_codes: Set[str] = set()
        for prod in products:
            key = extract_product_key(prod)
            if not key:
                continue
            current_codes.add(key)

            # NEW product (never seen before)
            if key not in seen_products:
                seen_products[key] = {"first_seen": time.time(), "last_seen": time.time()}
                caption, photo_url = product_to_message(prod, event_type="NEW")
                if photo_url:
                    send_telegram_photo(caption, photo_url)
                else:
                    send_telegram_message(caption)
                print(f"NEW product: {key}")

        # Update seen_products timestamps (your existing logic)
        for key in list(seen_products.keys()):
            if key not in current_codes:
                # product not on current listing page(s)
                info = seen_products[key]
                if "missing_since" not in info:
                    info["missing_since"] = time.time()
            else:
                info = seen_products[key]
                if "missing_since" in info:
                    # Just came back after being missing -> treat as RESTOCK
                    if not restock_alerted.get(key):
                        restock_alerted[key] = True
                info["last_seen"] = time.time()

        # Save state
        state["seen_products"] = seen_products
        state["restock_alerted"] = restock_alerted
        state["last_total_results"] = last_total_results
        save_state(state)

        time.sleep(POLL_INTERVAL_SEC)


if __name__ == "__main__":
    main_loop()
