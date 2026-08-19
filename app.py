import streamlit as st
import requests
from bs4 import BeautifulSoup
import time
import pandas as pd
from io import BytesIO
import urllib3

# Отключаем предупреждения SSL для Obbo
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- НАСТРОЙКИ ИНТЕРФЕЙСА ---
st.set_page_config(page_title="Парсер цен TJ", page_icon="🛒", layout="wide")
st.title("🛒 Агрегатор цен: Alifshop, Tajmobile, Obbo")
st.write("Выберите источники, категорию и город (для Alifshop), затем нажмите кнопку для запуска.")

# --- ЭЛЕМЕНТЫ УПРАВЛЕНИЯ ---
col1, col2, col3 = st.columns(3)

with col1:
    st.subheader("1. Выберите магазины")
    use_alif = st.checkbox("Alifshop", value=True)
    use_tajmobile = st.checkbox("Tajmobile", value=False)
    use_obbo = st.checkbox("Obbo", value=True)

with col2:
    st.subheader("2. Выберите категорию")
    # Пока используем только смартфоны, так как для Tajmobile и Obbo нужны точные URL/ID для других категорий
    category_name = st.selectbox("Категория:", ["Смартфоны"]) 

with col3:
    st.subheader("3. Город (для Alifshop)")
    city_name = st.selectbox("Город:", ["Душанбе", "Худжанд"])
    city_id = 1 if city_name == "Душанбе" else 2

# Кнопка запуска
start_button = st.button("🚀 Начать парсинг", type="primary", use_container_width=True)

# --- ФУНКЦИИ ПАРСИНГА ---

def parse_alifshop(cat_slug, c_id):
    url = "https://api.alifshop.tj/service_product/products"
    headers = {"accept": "application/json", "user-agent": "Mozilla/5.0", "referer": "https://alifshop.tj/"}
    params = {"city_id": c_id, "limit": 100, "page": 1, "category_slug": cat_slug, "sort_type": "desc_by_popularity", "search": ""}
    
    products = []
    while True:
        resp = requests.get(url, headers=headers, params=params).json()
        items = resp.get("response", {}).get("products", {}).get("items", [])
        if not items: break
        for item in items:
            products.append({
                "Магазин": "Alifshop",
                "Название": item.get("name", ""),
                "Цена": str(item.get("final_price") or item.get("min_price") or "0"),
                "Ссылка": f"https://alifshop.tj/product/{item.get('slug', '')}"
            })
        params["page"] += 1
        time.sleep(0.5)
    return products

def parse_tajmobile():
    # ВНИМАНИЕ: Замените path=20 на ваш ID категории смартфонов на Tajmobile!
    base_url = "https://tajmobile.tj/index.php?route=product/category&path=20&page={}"
    headers = {"user-agent": "Mozilla/5.0", "x-requested-with": "XMLHttpRequest"}
    products = []
    
    for page in range(1, 15):
        resp = requests.get(base_url.format(page), headers=headers)
        if resp.status_code != 200: break
        
        soup = BeautifulSoup(resp.text, 'lxml')
        items = soup.select('.product-thumb') or soup.select('.product-layout')
        if not items: break
            
        for item in items:
            name_tag = item.select_one('.caption h4 a') or item.select_one('.name a')
            price_tag = item.select_one('.price .price-new') or item.select_one('.price')
            if name_tag and price_tag:
                products.append({
                    "Магазин": "Tajmobile",
                    "Название": name_tag.text.strip(),
                    "Цена": price_tag.text.strip().replace('с.', '').replace(' ', '').split('.')[0],
                    "Ссылка": name_tag.get('href', '')
                })
        time.sleep(1)
    return products

def parse_obbo():
    api_url = "https://obbo.tj/api/products"
    auth = ("firdavsjuraev8@gmail.com", "a9b5NgNa33h3jn2z04t1cR706zyb4B73")
    headers = {"User-Agent": "Mozilla/5.0"}
    products = []
    
    # Шаг 1: Ищем ID категории "Смартфоны" через API категорий
    cat_url = "https://obbo.tj/api/categories?status=A&items_per_page=100"
    cat_resp = requests.get(cat_url, auth=auth, headers=headers, verify=False)
    cat_id = None
    if cat_resp.status_code == 200:
        categories = cat_resp.json().get("categories", [])
        if isinstance(categories, dict):
            categories = list(categories.values())
        for cat in categories:
            if "смартфон" in cat.get("category", "").lower():
                cat_id = cat.get("category_id")
                break

    if not cat_id:
        print("Не удалось найти категорию Смартфоны на Obbo!")
        return products

    # Шаг 2: Парсим товары с фильтром по категории (cid)
    page = 1
    while True:
        # Добавлен параметр cid=cat_id
        params = {"status": "A", "items_per_page": 100, "page": page, "cid": cat_id}
        response = requests.get(api_url, auth=auth, headers=headers, params=params, verify=False)
        
        if response.status_code != 200: break
            
        data = response.json()
        items = data.get("products", [])
        if isinstance(items, dict): items = list(items.values())
        if not items: break
            
        for item in items:
            products.append({
                "Магазин": "Obbo",
                "Название": item.get("product", ""),
                "Цена": str(item.get("price", "0")),
                "Ссылка": f"https://obbo.tj/index.php?dispatch=products.view&product_id={item.get('product_id', '')}"
            })
        page += 1
        time.sleep(0.5)
    return products

# --- ЛОГИКА КНОПКИ ---
if start_button:
    if not (use_alif or use_tajmobile or use_obbo):
        st.error("Пожалуйста, выберите хотя бы один магазин!")
    else:
        all_data = []
        progress_text = st.empty()
        
        with st.spinner('Собираем данные... Это может занять около минуты.'):
            if use_alif:
                progress_text.text("📱 Парсинг Alifshop...")
                all_data.extend(parse_alifshop("smartfony", city_id))
            
            if use_tajmobile:
                progress_text.text("📱 Парсинг Tajmobile...")
                all_data.extend(parse_tajmobile())
                
            if use_obbo:
                progress_text.text("📱 Парсинг Obbo...")
                all_data.extend(parse_obbo())

        if all_data:
            progress_text.success(f"✅ Готово! Собрано товаров: {len(all_data)}")
            
            # Создаем DataFrame
            df = pd.DataFrame(all_data)
            
            # Очищаем цены для Excel (оставляем только цифры)
            df['Цена'] = df['Цена'].astype(str).str.replace(' ', '').str.extract(r'(\d+)')[0]
            df['Цена'] = pd.to_numeric(df['Цена'], errors='coerce').fillna(0).astype(int)
            
            # Сортируем по названию (чтобы одинаковые телефоны были рядом)
            df = df.sort_values(by=["Название", "Цена"])
            
            st.subheader("📊 Предпросмотр данных")
            st.dataframe(df, use_container_width=True, height=500)
            
            # Генерация Excel файла в памяти
            output = BytesIO()
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='Сравнение цен')
            output.seek(0)
            
            st.subheader("⬇️ Скачать результат")
            st.download_button(
                label="Скачать Excel файл",
                data=output,
                file_name=f"phones_comparison_{city_name}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True
            )
        else:
            st.warning("Товары не найдены. Попробуйте изменить параметры поиска.")