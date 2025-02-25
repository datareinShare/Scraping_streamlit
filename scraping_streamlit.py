import streamlit as st
import nest_asyncio
import asyncio
import aiohttp
import math
import re
import os
from urllib.parse import urlencode
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# 必要なライブラリのインストール： nest_asyncio
nest_asyncio.apply()

# .env ファイルの内容を読み込む
load_dotenv()

# ※各種APIキー・URLはご自身のものに置き換えてください
API_KEY = os.environ.get("API_KEY")
GEOCODE_BASE_URL = os.environ.get("GEOCODE_BASE_URL")
PLACES_BASE_URL = os.environ.get("PLACES_BASE_URL")
CX_ID = os.environ.get("CX_ID")
CUSTOM_SEARCH_BASE_URL = os.environ.get("CUSTOM_SEARCH_BASE_URL")

def calculate_distance(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)
    a = (math.sin(delta_phi/2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda/2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

async def get_station_location(session, station_name):
    params = {
        'address': station_name,
        'key': API_KEY,
        'region': 'jp',
        'components': 'country:JP'
    }
    url = f"{GEOCODE_BASE_URL}?{urlencode(params)}"
    async with session.get(url) as response:
        data = await response.json()
        if data['results']:
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng']
        else:
            raise Exception('指定した駅が見つかりませんでした。')

async def search_places(session, lat, lng, keywords, radius=833):
    params = {
        'location': f'{lat},{lng}',
        'radius': radius,
        'keyword': keywords,
        'language': 'ja',
        'key': API_KEY
    }
    url = f"{PLACES_BASE_URL}?{urlencode(params)}"
    places = []
    while True:
        async with session.get(url) as response:
            data = await response.json()
            places.extend(data.get('results', []))
            next_page_token = data.get('next_page_token')
            if next_page_token:
                await asyncio.sleep(2)
                params['pagetoken'] = next_page_token
                url = f"{PLACES_BASE_URL}?{urlencode(params)}"
            else:
                break
    return places

async def google_search(session, query):
    params = {
        'q': query,
        'cx': CX_ID,
        'key': API_KEY,
        'lr': 'lang_ja'
    }
    url = f"{CUSTOM_SEARCH_BASE_URL}?{urlencode(params)}"
    async with session.get(url) as response:
        data = await response.json()
        return data.get('items', [])

async def geocode_address(session, address):
    params = {
        'address': address,
        'key': API_KEY,
        'region': 'jp',
        'components': 'country:JP'
    }
    url = f"{GEOCODE_BASE_URL}?{urlencode(params)}"
    async with session.get(url) as response:
        data = await response.json()
        if data['results']:
            location = data['results'][0]['geometry']['location']
            return location['lat'], location['lng']
        else:
            return None, None

async def scrape_and_validate(session, url, station_lat, station_lng, radius=833):
    try:
        async with session.get(url, timeout=10) as response:
            if response.status != 200:
                return None
            html = await response.text()
            soup = BeautifulSoup(html, 'html.parser')
            # 仮のロジック（住所抽出）
            address_candidates = []
            for p in soup.find_all('p'):
                text = p.get_text(strip=True)
                if re.search(r'(東京都|北海道|(?:京都|大阪)府|.{1,3}県.{1,3}市)', text):
                    address_candidates.append(text)
            if not address_candidates:
                return None
            address = address_candidates[0]
            lat, lng = await geocode_address(session, address)
            if lat is None or lng is None:
                return None
            dist = calculate_distance(station_lat, station_lng, lat, lng)
            if dist <= radius:
                title_tag = soup.find('title')
                if title_tag:
                    facility_name = title_tag.get_text(strip=True)
                else:
                    facility_name = address
                return facility_name
            else:
                return None
    except Exception:
        return None

# main_logic を駅名を引数に取るように変更
async def main_logic(station_name):
    output = ""
    try:
        async with aiohttp.ClientSession() as session:
            # 駅の位置情報取得
            station_lat, station_lng = await get_station_location(session, station_name)

            # カテゴリ定義
            categories = [
                {
                    'name': 'ボイトレ',
                    'keywords': 'ボイストレーニング OR ボイトレ',
                },
                {
                    'name': 'ダンススクール',
                    'keywords': 'ダンススクール',
                },
                {
                    # "レンタルスタジオ" ではなく、"ダンスレンタルスタジオ" だけを検索
                    'name': 'ダンスレンタルスタジオ',
                    'keywords': 'ダンスレンタルスタジオ',
                },
                {
                    'name': 'ライブハウス・音楽ハウス',
                    'keywords': 'ライブハウス OR 音楽ホール OR ミュージックバー',
                },
                {
                    'name': '塾',
                    'keywords': '塾 OR 学習塾'
                }
            ]

            # 集計用の辞書
            all_results = {
                'ボイトレ': [],
                'ダンススクール': [],
                'ダンスレンタルスタジオ': [],
                'ライブハウス・音楽ハウス': [],
                '塾': []
            }

            # Places API 検索
            for category in categories:
                places = await search_places(session, station_lat, station_lng, category['keywords'])
                for place in places:
                    place_lat = place['geometry']['location']['lat']
                    place_lng = place['geometry']['location']['lng']
                    distance = calculate_distance(station_lat, station_lng, place_lat, place_lng)
                    if distance <= 833:
                        all_results[category['name']].append(place['name'])

            # ボイトレ関連のGoogleカスタム検索
            query = f"{station_name} ボイトレ"
            search_results = await google_search(session, query)

            # スクレイピング検証
            scrape_tasks = []
            for item in search_results:
                link = item.get('link', '')
                if link:
                    scrape_tasks.append(scrape_and_validate(session, link, station_lat, station_lng, 833))
            scrape_results = await asyncio.gather(*scrape_tasks)
            for r in scrape_results:
                if r is not None:
                    all_results['ボイトレ'].append(r)

            # 重複削除
            for cat in all_results:
                all_results[cat] = list(set(all_results[cat]))

            # 結果文字列の作成
            output += "===== 最終結果まとめ =====\n"
            for category_name, result_list in all_results.items():
                output += f"\n{category_name}：{len(result_list)} 件\n"
                for r in result_list:
                    output += f"・{r}\n"
            output += f"\n駅から10分以内の塾の数: {len(all_results['塾'])} 件\n"
    except Exception as e:
        output = f"エラーが発生しました：{e}"
    return output

# === Streamlit 用レイアウト ===

# 固定下部に入力欄を表示するためのCSSを挿入
st.markdown(
    """
    <style>
    .fixed-bottom {
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        background-color: #FFFFFF;
        padding: 10px;
        border-top: 1px solid #ddd;
        z-index: 1000;
    }
    .content {
        padding-bottom: 120px;  /* 下部の固定領域分の余白 */
    }
    </style>
    """,
    unsafe_allow_html=True
)

# 結果表示用のコンテンツ領域（固定下部の入力欄に隠れないよう余白を確保）
st.markdown("<div class='content'>", unsafe_allow_html=True)
st.title("駅周辺検索 Web アプリ")
st.write("上記の入力欄から駅名を入力して検索ボタンを押すと、指定駅周辺の施設情報を取得します。")

# 結果表示用のプレースホルダー
result_placeholder = st.empty()

# コンテンツ領域終了
st.markdown("</div>", unsafe_allow_html=True)

# 固定下部の入力欄（常に画面下に表示）
st.markdown("<div class='fixed-bottom'>", unsafe_allow_html=True)
with st.form(key="station_form"):
    station_name = st.text_input("駅名を入力してください。例：○○駅")
    submitted = st.form_submit_button("検索")
    if submitted and station_name:
        # 非同期処理の実行
        result_text = asyncio.run(main_logic(station_name))
        result_placeholder.text(result_text)
st.markdown("</div>", unsafe_allow_html=True)
