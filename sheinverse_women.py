import time
import json
import requests
import os
from typing import Dict, Set, List

# ========= CONFIG =========

BASE_API = "https://www.sheinindia.in/api/category/sverse-5939-37961"
POLL_INTERVAL_SEC = 10  # tune carefully for speed vs rate-limit

STATE_FILE = "sheinverse_state_v2.json"
# ==========================


def send_telegram_message(text: str) -> None:
        bot_token = os.getenv("BOT_TOKEN")
        chat_id = os.getenv("CHAT_ID") 
        if not bot_token or not chat_id:
            print("Missing BOT_TOKEN or CHAT_ID env vars!")
            return

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }
        try:
            requests.post(url, json=payload, timeout=5)
        except Exception as e:
            print("Telegram error:", e)


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
        "query": ": relevance:genderfilter:Women",
        "currentPage": page,
    }
    r = requests.get(BASE_API, params=params, timeout=5)
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


def product_to_message(prod: Dict, event_type: str = "NEW") -> str:
    """
    Build a nice Telegram message for the product.
    """
    price = prod["price"]["displayformattedValue"]
    url_path = prod.get("url", "")
    link = f"https://www.sheinindia.in{url_path}" if url_path.startswith("/") else url_path

    text = (
        f"<b>{event_type}</b>\t"
        f"{link}\n"
        f"<b>••••••••••••••••••••••••••••••••••••{price}</b>"
    )
    return text


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
            summary_msg = summary_alert_message(current_total_results, new_count)
            send_telegram_message(summary_msg)
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
                msg = product_to_message(prod, event_type="NEW")
                send_telegram_message(msg)
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
