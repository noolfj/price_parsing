import streamlit as st
import requests
import pandas as pd
import time
from io import BytesIO

# --- ДАННЫЕ ДЛЯ ВЫПАДАЮЩИХ СПИСКОВ ---
CITIES = {
    "Душанбе": 1,
    "Худжанд": 2
}

CATEGORIES = {
    "Смартфоны": "smartfony",
    "Бытовая техника": "bytovaya-tehnika"
}

# --- НАСТРОЙКА ИНТЕРФЕЙСА ---
st.title("🛒 Alif Shop Web Parser")
st.write("Выберите параметры и нажмите кнопку, чтобы скачать Excel-файл с товарами.")

col1, col2 = st.columns(2)
with col1:
    city_name = st.selectbox("Выберите город:", list(CITIES.keys()))
with col2:
    category_name = st.selectbox("Выберите категорию:", list(CATEGORIES.keys()))

# Кнопка старта
if st.button("Начать парсинг", type="primary"):
    city_id = CITIES[city_name]
    category_slug = CATEGORIES[category_name]
    
    progress_bar = st.progress(0, text="Подготовка к парсингу...")
    status_text = st.empty()

    all_products = []
    page = 1

    with st.spinner(f'Парсинг категории "{category_name}" в городе "{city_name}"...'):
        while True:
            api_url = "https://api.alifshop.tj/service_product/products"
            headers = {
                "accept": "application/json, text/plain, */*",
                "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
                "referer": "https://alifshop.tj/"
            }
            params = {
                "city_id": city_id,
                "limit": 100,
                "search": "",
                "page": page,
                "sort_type": "desc_by_popularity",
                "category_slug": category_slug
            }
            
            try:
                response = requests.get(api_url, headers=headers, params=params)
                if response.status_code != 200:
                    st.error(f"Ошибка сервера: {response.status_code}")
                    break
                    
                data = response.json()
                products = data.get("response", {}).get("products", {}).get("items", [])
                
                if not products:
                    break
                    
                for item in products:
                    all_products.append({
                        "Название": item.get("name", ""),
                        "Цена": item.get("final_price") or item.get("min_price") or "",
                        "Ссылка": f"https://alifshop.tj/product/{item.get('slug', '')}",
                        "Картинка": item.get("images", [""])[0] if item.get("images") else ""
                    })
                
                # Обновляем прогресс и текст
                progress_text = f'Спарсено товаров: {len(all_products)} (Страница {page})...'
                progress_bar.progress(50, text=progress_text)
                status_text.text(progress_text)
                
                page += 1
                time.sleep(1) # Пауза, чтобы не забанели
            except Exception as e:
                st.error(f"Произошла ошибка: {e}")
                break

    if all_products:
        progress_bar.progress(100, text="Готово!")
        st.success(f"Успешно спарсено {len(all_products)} товаров!")
        
        # Создаем DataFrame
        df = pd.DataFrame(all_products)
        
        # Показываем таблицу прямо на сайте (первые 50 строк)
        st.write("Предпросмотр данных:")
        st.dataframe(df.head(50))
        
        # Создаем Excel файл прямо в оперативной памяти (чтобы дать скачать)
        output = BytesIO()
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name=category_name[:31])
        output.seek(0)
        
        # Кнопка для скачивания файла
        st.download_button(
            label="⬇️ Скачать Excel файл",
            data=output,
            file_name=f"alifshop_{category_slug}_{city_id}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
    else:
        st.warning("Товары не найдены.")