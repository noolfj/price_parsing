import re
import time
import threading
from io import BytesIO
from urllib.parse import urlparse
from dataclasses import dataclass
from typing import Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup

try:
    import cloudscraper
    HAVE_CLOUDSCRAPER = True
except ImportError:
    HAVE_CLOUDSCRAPER = False

# ---------------------------------------------------------------------------
# НАСТРОЙКИ
# ---------------------------------------------------------------------------
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
}
TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BACKOFF = 1.5
DOMAIN_DELAY = 0.8   
MAX_WORKERS = 10     


class DomainRateLimiter:
    def __init__(self, min_delay: float):
        self.min_delay = min_delay
        self.lock = threading.Lock()
        self.last_call = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_call
            if elapsed < self.min_delay:
                time.sleep(self.min_delay - elapsed)
            self.last_call = time.time()


class DomainPool:
    def __init__(self, min_delay: float):
        self._sessions = {}
        self._limiters = {}
        self._lock = threading.Lock()
        self.min_delay = min_delay

    def _domain(self, url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return "unknown"

    def get(self, url: str):
        domain = self._domain(url)
        with self._lock:
            if domain not in self._sessions:
                if HAVE_CLOUDSCRAPER:
                    sess = cloudscraper.create_scraper(
                        browser={"browser": "chrome", "platform": "windows", "mobile": False}
                    )
                else:
                    sess = requests.Session()
                    sess.headers.update(BROWSER_HEADERS)
                self._sessions[domain] = sess
                self._limiters[domain] = DomainRateLimiter(self.min_delay)
        return self._sessions[domain], self._limiters[domain]


def clean_price(raw: str) -> Optional[float]:
    if not raw:
        return None
    num = raw.replace(" ", "").replace("\xa0", "").replace(",", ".").strip()
    num = re.sub(r'[^\d.]', '', num)
    try:
        val = float(num)
        return round(val) if val >= 100 else None
    except ValueError:
        return None


def extract_main_price(html: str, url: str = "") -> Optional[float]:
    soup = BeautifulSoup(html, 'lxml')

    if 'alifshop' in url.lower():
        h4 = soup.find('h4', class_=re.compile(r'text-heading1'))
        if h4:
            price = clean_price(h4.get_text(strip=True))
            if price:
                return price

    if 'tajmobile' in url.lower():
        span = soup.find('span', class_=re.compile(r'autocalc-product-special'))
        if span:
            price = clean_price(span.get_text(strip=True))
            if price:
                return price
        div_new = soup.find('div', class_='product-price-new')
        if div_new:
            price = clean_price(div_new.get_text(strip=True))
            if price:
                return price

    if 'obbo' in url.lower():
        div_price = soup.find('div', class_=re.compile(r'ty-product-block__price-actual'))
        if div_price:
            span = div_price.find('span')
            if span:
                price = clean_price(span.get_text(strip=True))
                if price:
                    return price

    meta_price = soup.find('meta', attrs={'property': 'og:price:amount'}) \
        or soup.find('meta', attrs={'itemprop': 'price'})
    if meta_price and meta_price.get('content'):
        price = clean_price(meta_price['content'])
        if price:
            return price

    itemprop_price = soup.find(attrs={'itemprop': 'price'})
    if itemprop_price:
        content = itemprop_price.get('content') or itemprop_price.get_text(strip=True)
        price = clean_price(content)
        if price:
            return price

    price_pattern = re.compile(r'(\d{1,3}(?:[ \u00a0]\d{3})+|\d+)\s*(?:TJS|tjs|с\.|c\.|сомони|сом)', re.IGNORECASE)
    monthly_pattern = re.compile(r'/\s*мес', re.IGNORECASE)

    prices_found = []
    for element in soup.find_all(text=True):
        text = str(element)
        if monthly_pattern.search(text):
            continue
        match = price_pattern.search(text)
        if match:
            price = clean_price(match.group(1))
            if price and 500 <= price <= 100000:
                prices_found.append(price)

    return prices_found[0] if prices_found else None


def extract_title(html: str) -> str:
    soup = BeautifulSoup(html, 'lxml')
    title_tag = soup.find('title')
    if title_tag:
        title = title_tag.get_text(strip=True)
        return title.split(' - ')[0].split(' | ')[0].split(' :: ')[0].strip()
    h1_tag = soup.find('h1')
    if h1_tag:
        return h1_tag.get_text(" ", strip=True)
    return "Название не найдено"


@dataclass
class ScrapedItem:
    row_idx: int
    name: str
    expected_name: str
    price: Optional[float]
    url: str
    shop: str
    status: str
    error_msg: str = ""


def scrape_url(row_idx: int, url: str, shop_name: str, expected_name: str, pool: DomainPool) -> ScrapedItem:
    if not url or not str(url).startswith("http"):
        return ScrapedItem(row_idx, "", expected_name, None, url, shop_name, "Error", "Нет ссылки")

    url = str(url).strip()
    session, limiter = pool.get(url)

    last_err = ""
    for attempt in range(MAX_RETRIES + 1):
        limiter.wait()  
        try:
            r = session.get(url, timeout=TIMEOUT)
            if r.status_code == 404:
                return ScrapedItem(row_idx, "", expected_name, None, url, shop_name, "Not Found", "404")
            r.raise_for_status()
            r.encoding = r.apparent_encoding

            price = extract_main_price(r.text, url)
            name = extract_title(r.text)

            if price is None:
                return ScrapedItem(row_idx, name, expected_name, None, url, shop_name, "Error", "Цена не найдена")

            return ScrapedItem(row_idx, name, expected_name, price, url, shop_name, "OK")

        except Exception as e:
            last_err = str(e)[:60]
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BACKOFF * (attempt + 1))
                continue

    return ScrapedItem(row_idx, "", expected_name, None, url, shop_name, "Error", last_err)


# ---------------------------------------------------------------------------
# STREAMLIT UI
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Парсинг цен", page_icon="⚡", layout="wide")
st.title("⚡ Парсинг и сравнение цен")

st.subheader("1. Загрузка списка товаров")
input_data = None

uploaded_file = st.file_uploader("📁 Загрузите Excel или CSV файл", type=["csv", "xlsx"])

if uploaded_file is not None:
    if uploaded_file.name.endswith(".csv"):
        input_data = pd.read_csv(uploaded_file)
    else:
        input_data = pd.read_excel(uploaded_file)

if input_data is not None:
    st.success(f"Загружено {len(input_data)} товаров.")

    df = input_data.copy()
    df.columns = [str(c).strip().lower() for c in df.columns]

    col_name = next((c for c in df.columns if "назв" in c or "name" in c or "модель" in c), df.columns[0])
    col_obbo = next((c for c in df.columns if "obbo" in c), None)
    col_alif = next((c for c in df.columns if "alif" in c), None)
    col_taj = next((c for c in df.columns if "taj" in c), None)



    if st.button("🚀 Начать сбор цен", type="primary"):
        pool = DomainPool(min_delay=DOMAIN_DELAY)

        tasks = []
        for idx, row in df.iterrows():
            name = str(row.get(col_name, "")).strip()
            if col_obbo:
                tasks.append((idx, str(row.get(col_obbo, "")).strip(), "OBBO", name))
            if col_alif:
                tasks.append((idx, str(row.get(col_alif, "")).strip(), "Alifshop", name))
            if col_taj:
                tasks.append((idx, str(row.get(col_taj, "")).strip(), "Tajmobile", name))

        total_tasks = len(tasks)
        progress_bar = st.progress(0)
        status_text = st.empty()

        results_by_row = {idx: {"name": str(row.get(col_name, "")).strip()} for idx, row in df.iterrows()}
        done = 0

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(scrape_url, idx, url, shop, name, pool): (idx, shop)
                for idx, url, shop, name in tasks
            }

            for future in as_completed(futures):
                item: ScrapedItem = future.result()
                done += 1
                status_text.text(f"Обработано {done}/{total_tasks} — {item.shop}: {item.expected_name}")
                progress_bar.progress(done / total_tasks)

                row_bucket = results_by_row[item.row_idx]
                row_bucket[f"{item.shop} Цена"] = item.price
                row_bucket[f"{item.shop} URL"] = item.url
                row_bucket[f"{item.shop} Статус"] = item.status

        status_text.text("✅ Готово!")

        rows = []
        for idx in df.index:
            b = results_by_row[idx]
            rows.append({
                "Модель (Ожидаемая)": b.get("name", ""),
                "OBBO Цена": b.get("OBBO Цена"),
                "OBBO URL": b.get("OBBO URL"),
                "OBBO Статус": b.get("OBBO Статус", "—"),
                "Alifshop Цена": b.get("Alifshop Цена"),
                "Alifshop URL": b.get("Alifshop URL"),
                "Alifshop Статус": b.get("Alifshop Статус", "—"),
                "Tajmobile Цена": b.get("Tajmobile Цена"),
                "Tajmobile URL": b.get("Tajmobile URL"),
                "Tajmobile Статус": b.get("Tajmobile Статус", "—"),
            })
        res_df = pd.DataFrame(rows)

        def get_min_and_diff(row):
            prices = {}
            for shop in ["OBBO", "Alifshop", "Tajmobile"]:
                val = row.get(f"{shop} Цена")
                if pd.notna(val):
                    prices[shop] = val

            if len(prices) >= 2:
                min_shop = min(prices, key=prices.get)
                min_price = prices[min_shop]
                if pd.notna(row["OBBO Цена"]):
                    diff = row["OBBO Цена"] - min_price
                    return pd.Series([min_price, min_shop, diff])
                return pd.Series([min_price, min_shop, None])
            return pd.Series([None, "Нет данных", None])

        res_df[["Мин. цена", "Где дешевле", "Разница с OBBO"]] = res_df.apply(get_min_and_diff, axis=1)

        st.subheader("2. Результаты сравнения (нажмите на цену для открытия товара)")

        display_df = pd.DataFrame()
        display_df["Модель"] = res_df["Модель (Ожидаемая)"]

        def make_price_link(price, url):
            if pd.notna(price) and pd.notna(url) and str(url).startswith("http"):
                return f'<a href="{url}" target="_blank" style="color: #4CAF50; text-decoration: none; font-weight: bold;">{price:,.0f} TJS 🔗</a>'
            elif pd.notna(price):
                return f'{price:,.0f} TJS'
            return '❌'

        display_df["OBBO"] = [make_price_link(p, u) for p, u in zip(res_df["OBBO Цена"], res_df["OBBO URL"])]
        display_df["Alifshop"] = [make_price_link(p, u) for p, u in zip(res_df["Alifshop Цена"], res_df["Alifshop URL"])]
        display_df["Tajmobile"] = [make_price_link(p, u) for p, u in zip(res_df["Tajmobile Цена"], res_df["Tajmobile URL"])]
        display_df["Мин. цена"] = res_df["Мин. цена"].apply(lambda x: f"{x:,.0f} TJS" if pd.notna(x) else "❌")
        display_df["Где дешевле"] = res_df["Где дешевле"]
        display_df["Разница"] = res_df["Разница с OBBO"].apply(lambda x: f"{x:,.0f} TJS" if pd.notna(x) else "—")

        st.markdown(display_df.to_html(escape=False, index=False), unsafe_allow_html=True)

        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            res_df.to_excel(writer, index=False, sheet_name="Сравнение цен")
        buffer.seek(0)

        st.download_button(
            label="⬇️ Скачать результат в Excel",
            data=buffer,
            file_name="price_comparison.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

else:
    st.info("👆 Загрузите файл, чтобы начать.")
