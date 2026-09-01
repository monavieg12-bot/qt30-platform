import os
import json
import sqlite3
import hashlib
import urllib.parse
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string, redirect

app = Flask(__name__)

# ==========================================
# 核心設定與金流參數
# ==========================================
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin888")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
ADMIN_LINE_USER_ID = os.environ.get("ADMIN_LINE_USER_ID", "")

ECPAY_MERCHANT_ID = "3002607"
ECPAY_HASH_KEY = "pwFHCqoQZGmho4w6"
ECPAY_HASH_IV = "EkRm7iFT261dpevs"
ECPAY_API_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"
BASE_URL = os.environ.get("BASE_URL", "https://qt30home.com")

DB_FILE = "qt30.db"

# ==========================================
# 資料庫初始化
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 工單與歷史實價登錄表
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            district TEXT DEFAULT '',
            address TEXT,
            lat REAL DEFAULT 25.175,
            lng REAL DEFAULT 121.443,
            category TEXT,
            area_ping REAL DEFAULT 0,
            scope_desc TEXT DEFAULT '',
            house_age TEXT DEFAULT '',
            description TEXT,
            photo_data TEXT DEFAULT '',
            budget INTEGER DEFAULT 0,
            required_points INTEGER DEFAULT 30,
            ref_tech_code TEXT DEFAULT '',
            status TEXT DEFAULT 'bidding',
            winner_phone TEXT DEFAULT '',
            final_price INTEGER DEFAULT 0,
            work_status TEXT DEFAULT '媒合現勘中',
            created_at TEXT
        )
    ''')
    try:
        c.execute("ALTER TABLE orders ADD COLUMN area_ping REAL DEFAULT 0")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE orders ADD COLUMN scope_desc TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE orders ADD COLUMN house_age TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE orders ADD COLUMN final_price INTEGER DEFAULT 0")
    except Exception:
        pass

    # 報價/競標表記錄 (暗標)
    c.execute('''
        CREATE TABLE IF NOT EXISTS bids (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_id INTEGER,
            tech_phone TEXT,
            tech_name TEXT,
            bid_amount INTEGER,
            notes TEXT,
            created_at TEXT
        )
    ''')

    # 師傅表 (含實名證照)
    c.execute('''
        CREATE TABLE IF NOT EXISTS technicians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE,
            password TEXT,
            name TEXT,
            skills TEXT,
            company_id TEXT DEFAULT '',
            license_name TEXT DEFAULT '',
            experience_years INTEGER DEFAULT 5,
            area TEXT DEFAULT '淡水區',
            exclusive_districts TEXT DEFAULT '',
            referral_code TEXT UNIQUE,
            referred_by TEXT DEFAULT '',
            ref_count INTEGER DEFAULT 0,
            points INTEGER DEFAULT 100,
            is_verified INTEGER DEFAULT 1,
            is_pro_member INTEGER DEFAULT 0,
            lat REAL DEFAULT 25.175,
            lng REAL DEFAULT 121.443,
            status TEXT DEFAULT 'approved',
            created_at TEXT
        )
    ''')
    try:
        c.execute("ALTER TABLE technicians ADD COLUMN company_id TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE technicians ADD COLUMN license_name TEXT DEFAULT ''")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE technicians ADD COLUMN experience_years INTEGER DEFAULT 5")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE technicians ADD COLUMN lat REAL DEFAULT 25.175")
    except Exception:
        pass
    try:
        c.execute("ALTER TABLE technicians ADD COLUMN lng REAL DEFAULT 121.443")
    except Exception:
        pass

    # 綠界訂單表
    c.execute('''
        CREATE TABLE IF NOT EXISTS ecpay_orders (
            merchant_trade_no TEXT PRIMARY KEY,
            tech_phone TEXT,
            amount INTEGER,
            points INTEGER,
            status TEXT DEFAULT 'unpaid',
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()

init_db()

def generate_check_mac_value(params, hash_key, hash_iv):
    filtered_params = {k: str(v) for k, v in params.items() if k != 'CheckMacValue'}
    sorted_params = sorted(filtered_params.items(), key=lambda x: x[0])
    param_str = "&".join([f"{k}={v}" for k, v in sorted_params])
    raw_str = f"HashKey={hash_key}&{param_str}&HashIV={hash_iv}"
    encoded_str = urllib.parse.quote_plus(raw_str).lower()
    encoded_str = (encoded_str
                   .replace('%2d', '-')
                   .replace('%5f', '_')
                   .replace('%2e', '.')
                   .replace('%21', '!')
                   .replace('%2a', '*')
                   .replace('%28', '(')
                   .replace('%29', ')')
                   .replace('%20', '+'))
    return hashlib.sha256(encoded_str.encode('utf-8')).hexdigest().upper()

def send_line_push_message(text):
    if not LINE_CHANNEL_ACCESS_TOKEN or not ADMIN_LINE_USER_ID:
        return
    import urllib.request
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {"to": ADMIN_LINE_USER_ID, "messages": [{"type": "text", "text": text}]}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"LINE Push Error: {e}")

def process_paid_order(trade_no, trade_amt=""):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT tech_phone, points, status FROM ecpay_orders WHERE merchant_trade_no=?", (trade_no,))
    order = c.fetchone()
    if order and order[2] != 'paid':
        phone = order[0]
        points_to_add = order[1]
        c.execute("UPDATE ecpay_orders SET status='paid' WHERE merchant_trade_no=?", (trade_no,))
        c.execute("UPDATE technicians SET points = points + ? WHERE phone=?", (points_to_add, phone))
        c.execute("SELECT name, points FROM technicians WHERE phone=?", (phone,))
        tech_info = c.fetchone()
        conn.commit()
        conn.close()

        if tech_info:
            amt_display = trade_amt if trade_amt else "線上儲值"
            msg = f"💎 【QT30 師傅儲值入帳】\n師傅：{tech_info[0]} ({phone})\n金額：NT$ {amt_display}\n增加：+{points_to_add} 點\n總餘額：{tech_info[1]} 點"
            send_line_push_message(msg)
        return True
    conn.close()
    return False

# ==========================================
# 0. 品牌官方首頁 (深色高質感・精準文案升級)
# ==========================================
@app.route("/")
def home():
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QT30 社區修繕團隊｜裝潢報價實價登錄 ✕ 社區達人駐點深耕社區</title>
        <meta name="description" content="QT30 社區修繕團隊：所有報價公開透明網路永久留存、社區達人深耕社區、公開報價每一分修繕預算都花在刀口上！">
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 antialiased selection:bg-amber-500 selection:text-slate-950">
        <header class="bg-slate-900/80 backdrop-blur border-b border-slate-800 sticky top-0 z-50">
            <div class="max-w-6xl mx-auto px-4 h-16 flex items-center justify-between">
                <div class="flex items-center gap-3">
                    <span class="text-2xl font-black tracking-wider text-amber-400">QT30</span>
                    <span class="text-xs bg-amber-500/10 text-amber-400 border border-amber-500/30 font-bold px-2.5 py-0.5 rounded-full">社區修繕團隊</span>
                </div>
                <div class="flex items-center gap-3">
                    <a href="/tech" class="text-sm font-bold text-slate-300 hover:text-amber-400 px-3 py-2 transition">師傅接單工作台</a>
                    <a href="/app" class="bg-amber-500 hover:bg-amber-400 text-slate-950 text-sm font-black px-4 py-2 rounded-xl transition shadow-lg shadow-amber-500/20">免費發案報修</a>
                </div>
            </div>
        </header>

        <!-- Hero 主視覺區塊 -->
        <section class="py-20 px-4 text-center relative overflow-hidden bg-radial from-slate-900 to-slate-950">
            <div class="max-w-3xl mx-auto space-y-6">
                <div class="inline-flex items-center gap-2 bg-slate-800/80 border border-slate-700 px-3.5 py-1.5 rounded-full text-xs font-bold text-amber-300">
                    <span>⚡ 拒絕裝修暴利與黑箱喊價</span>
                </div>
                <h1 class="text-3xl md:text-5xl font-black tracking-tight text-white leading-tight">
                    裝潢報價實價登錄<br>
                    <span class="text-transparent bg-clip-text bg-gradient-to-r from-amber-400 to-amber-200">社區達人駐點深耕社區</span>
                </h1>
                <p class="text-slate-300 text-base md:text-lg leading-relaxed font-medium">
                    所有報價公開透明網路永久留存 ✕ 社區達人深耕社區 ✕ 公開報價每一分修繕預算都花在刀口上
                </p>
                <div class="flex flex-col sm:flex-row justify-center gap-4 pt-4">
                    <a href="/app" class="bg-amber-500 hover:bg-amber-400 text-slate-950 font-black text-lg px-8 py-4 rounded-2xl shadow-xl shadow-amber-500/20 hover:scale-105 transition">🚀 立即免費發案 (AI 互動填單)</a>
                    <a href="/tech" class="bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold text-lg px-8 py-4 rounded-2xl border border-slate-700 transition">🛠️ 專業師傅實名入駐</a>
                </div>
            </div>
        </section>

        <!-- 核心三大承諾卡片 -->
        <section class="py-16 max-w-5xl mx-auto px-4">
            <div class="bg-gradient-to-b from-slate-900 to-slate-900/60 p-8 rounded-3xl border border-slate-800 space-y-6">
                <div class="text-center space-y-2">
                    <h2 class="text-2xl font-black text-white">💡 為什麼修繕一定要在平台公開報價，切勿私下成交？</h2>
                    <p class="text-slate-400 text-xs md:text-sm">私下交易失去第三方紀錄，爛尾、加價投訴無門！在 QT30 留存報價享有三大終生保障：</p>
                </div>
                <div class="grid md:grid-cols-3 gap-4 text-xs">
                    <div class="bg-slate-950/80 p-5 rounded-2xl border border-slate-800 space-y-2">
                        <div class="text-amber-400 font-black text-sm">📊 1. 所有報價公開透明網路永久留存。</div>
                        <p class="text-slate-400 leading-relaxed">工項明細、材料規格與報價單據永久存證於雲端數據庫。遭遇施工瑕疵、偷工減料或履約爭議，完整紀錄保障權益不吃虧。</p>
                    </div>
                    <div class="bg-slate-950/80 p-5 rounded-2xl border border-slate-800 space-y-2">
                        <div class="text-amber-400 font-black text-sm">🌟 2. 社區達人深耕社區。</div>
                        <p class="text-slate-400 leading-relaxed">在地實名認證達人長年駐點深耕社區，重視在地名譽與鄰里口碑，完工接受住戶公開五星評價檢驗，絕不敢隨意怠工或加價。</p>
                    </div>
                    <div class="bg-slate-950/80 p-5 rounded-2xl border border-slate-800 space-y-2">
                        <div class="text-amber-400 font-black text-sm">🔍 3. 公開報價 每一分修繕預算都花在刀口上。</div>
                        <p class="text-slate-400 leading-relaxed">多位在地師傅暗標競爭，系統比對同社區真實歷史實價登錄，工料行情公開透明，杜絕漫天喊價，讓預算發揮最大價值！</p>
                    </div>
                </div>
            </div>
        </section>

        <!-- 20大工種網格 -->
        <section class="py-12 max-w-6xl mx-auto px-4">
            <h2 class="text-xl font-black text-center mb-8 text-slate-300">涵蓋 20+ 完整居家修繕與裝潢工種</h2>
            <div class="grid grid-cols-2 sm:grid-cols-4 md:grid-cols-5 gap-3 text-center text-xs font-bold">
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🔨 拆除工程</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">⚡ 水電工程</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🧱 泥作貼磚</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🪵 木作裝潢</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🗄️ 系統櫃工程</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🔍 抓漏檢測</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🏠 屋頂防水</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🏢 外牆防水</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🏡 老屋翻新統包</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🚿 衛浴整修</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">❄️ 冷氣空調</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">📦 搬家清運</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🪟 鋁門窗/氣密窗</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🎨 油漆粉刷</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🚪 鐵工/鐵捲門</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🪞 玻璃工程</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🛋️ 窗簾壁紙地毯</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🧹 清潔細清</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🌿 園藝造景</div>
                <div class="bg-slate-900 border border-slate-800 p-3.5 rounded-xl hover:border-amber-500/50 transition">🛠️ 其他綜合修繕</div>
            </div>
        </section>

        <!-- 平台免責聲明 -->
        <footer class="bg-slate-950 text-slate-500 py-10 text-center text-xs border-t border-slate-900 space-y-2">
            <p class="max-w-3xl mx-auto text-[11px] leading-relaxed text-slate-600">
                【平台聲明】QT30 為資訊撮合與裝潢報價實價登錄平台，非工程承攬契約當事人。實際施工品質、款項給付、保固承諾與工安責任，由獨立承攬施作之認證師傅直接向業主負責。
            </p>
            <p>© 2026 QT30 社區修繕團隊 (qt30home.com). All Rights Reserved.</p>
        </footer>
    </body>
    </html>
    ''')

# ==========================================
# 1. 客戶端預約報修 (/app) - AI 互動窗口 + 照片上傳 + 坪數範圍量化 + 地圖
# ==========================================
@app.route("/app")
def client_app():
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>淡水房屋修繕推薦｜QT30 免費發案・AI 互動窗口・實價登錄地圖</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            #map { height: 260px; width: 100%; border-radius: 1rem; z-index: 1; }
        </style>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen flex items-center justify-center p-3 antialiased relative">
        <div class="bg-slate-900 rounded-3xl border border-slate-800 shadow-2xl w-full max-w-2xl overflow-hidden my-4">
            <div class="bg-gradient-to-r from-slate-900 via-slate-800 to-slate-900 p-5 border-b border-slate-800 text-center">
                <span class="text-xs font-black text-amber-400 tracking-wider">QT30 社區修繕團隊</span>
                <h1 class="text-2xl font-black text-white mt-0.5">房屋修繕免費發案</h1>
                <p class="text-slate-400 text-xs mt-1">送出發案完全免費！認證師傅主動預約現場勘查。完工後報價將登錄至社區實價數據庫。</p>
            </div>
            
            <form id="orderForm" class="p-6 space-y-4">
                <!-- 基本聯絡 -->
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-bold text-slate-300">聯絡姓名</label>
                        <input type="text" id="name" required placeholder="例如：王先生" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-white">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-300">聯絡電話</label>
                        <input type="tel" id="phone" required placeholder="例如：0912345678" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-white">
                    </div>
                </div>

                <div class="grid grid-cols-3 gap-3">
                    <div>
                        <label class="block text-xs font-bold text-slate-300">行政地區</label>
                        <select id="district" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-amber-400 font-bold" onchange="onDistrictChange()">
                            <option value="淡水區">淡水區</option>
                            <option value="板橋區">板橋區</option>
                            <option value="三重區">三重區</option>
                            <option value="中和區">中和區</option>
                            <option value="新莊區">新莊區</option>
                            <option value="新店區">新店區</option>
                            <option value="台北市全區">台北市全區</option>
                            <option value="桃園市全區">桃園市全區</option>
                        </select>
                    </div>
                    <div class="col-span-2">
                        <label class="block text-xs font-bold text-slate-300">詳細修繕地址</label>
                        <input type="text" id="address" required placeholder="例如：中正東路二段 88 號" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-white">
                    </div>
                </div>

                <!-- 綜合實價地圖 (含駐點師傅、案件、歷史報價) -->
                <div>
                    <div class="flex justify-between items-center mb-1 text-xs">
                        <span class="font-bold text-slate-300">🗺️ 社區地圖 (顯示駐點師傅 🟢・案件進行中 🟡・實價登錄 🔵)</span>
                        <button type="button" onclick="locateUser()" class="text-amber-400 font-bold hover:underline">📍 自動定位</button>
                    </div>
                    <div id="map"></div>
                    <input type="hidden" id="lat" value="25.175">
                    <input type="hidden" id="lng" value="121.443">
                </div>

                <!-- 工種與量化數據欄位 -->
                <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-bold text-slate-300">修繕/裝修工種 (20大工種)</label>
                        <select id="category" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl font-bold text-white" onchange="triggerAiCalc()">
                            <option value="水電工程">⚡ 水電工程</option>
                            <option value="油漆粉刷">🎨 油漆粉刷</option>
                            <option value="泥作貼磚">🧱 泥作貼磚</option>
                            <option value="衛浴整修">🚿 衛浴整修</option>
                            <option value="老屋翻新/統包">🏡 老屋翻新統包</option>
                            <option value="屋頂防水">🏠 屋頂防水</option>
                            <option value="外牆防水">🏢 外牆防水</option>
                            <option value="抓漏檢測">🔍 抓漏檢測</option>
                            <option value="拆除工程">🔨 拆除工程</option>
                            <option value="木作裝潢">🪵 木作裝潢</option>
                            <option value="系統櫃工程">🗄️ 系統櫃工程</option>
                            <option value="鋁門窗/氣密窗">🪟 鋁門窗 / 氣密窗</option>
                            <option value="冷氣空調">❄️ 冷氣空調</option>
                            <option value="搬家清運">📦 搬家清運</option>
                            <option value="其他綜合修繕">🛠️ 其他綜合修繕</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-300">施作坪數 (坪) *量化計算依據</label>
                        <input type="number" step="0.5" id="areaPing" placeholder="例：20" oninput="triggerAiCalc()" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-amber-400 font-bold">
                    </div>
                </div>

                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-bold text-slate-300">施作範圍 / 格局</label>
                        <input type="text" id="scopeDesc" placeholder="例：客廳+2房 或 1間衛浴" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-white text-xs">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-300">屋況 / 屋齡</label>
                        <select id="houseAge" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-slate-200 text-xs">
                            <option value="10年以下新成屋">10年以下新成屋</option>
                            <option value="10-20年中古屋">10-20年中古屋</option>
                            <option value="20-30年老屋">20-30年老屋</option>
                            <option value="30年以上高齡老屋">30年以上高齡老屋</option>
                        </select>
                    </div>
                </div>

                <!-- 照片上傳 -->
                <div>
                    <label class="block text-xs font-bold text-slate-300">📸 現場損壞/施工範圍照片上傳 (有圖師傅報價更精準)</label>
                    <input type="file" id="orderPhotoFile" accept="image/*" class="w-full mt-1 text-xs text-slate-400 file:mr-3 file:py-2 file:px-3 file:rounded-xl file:border-0 file:text-xs file:font-bold file:bg-slate-800 file:text-amber-400">
                    <input type="hidden" id="photoData">
                </div>

                <!-- 需求說明 -->
                <div>
                    <label class="block text-xs font-bold text-slate-300">具體需求說明</label>
                    <textarea id="description" rows="2" required placeholder="請簡單說明現場狀況、損壞情況或施作要求..." class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-white text-xs"></textarea>
                </div>

                <!-- AI 估算行情預算 -->
                <div class="bg-slate-950 p-3.5 rounded-2xl border border-slate-800 flex justify-between items-center">
                    <div>
                        <div class="text-xs text-slate-400">🤖 AI 依坪數與工種即時估算行情：</div>
                        <div class="text-xl font-black text-amber-400" id="aiEstimatedDisplay">NT$ 0</div>
                    </div>
                    <input type="hidden" id="budget" value="0">
                    <button type="button" onclick="openAiChatModal()" class="bg-amber-500/20 text-amber-300 border border-amber-500/40 text-xs font-bold px-3 py-1.5 rounded-xl hover:bg-amber-500/30">💬 打開 AI 估價窗口</button>
                </div>

                <div class="bg-slate-950 p-3.5 rounded-2xl border border-slate-800 text-[11px] text-slate-400 space-y-1">
                    <p class="font-bold text-amber-400">🛡️ QT30 發案承諾：</p>
                    <p>所有報價公開透明網路永久留存。公開報價每一分修繕預算都花在刀口上。認證師傅將主動預約現場勘查。</p>
                </div>

                <button type="submit" class="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-black py-4 rounded-2xl shadow-xl transition text-base">🚀 免費送出發案（開放認證師傅競價）</button>
            </form>
        </div>

        <!-- 🤖 全功能 AI 智慧估價窗口 (彈窗) -->
        <div id="aiChatModal" class="hidden fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4">
            <div class="bg-slate-900 rounded-3xl max-w-lg w-full p-6 space-y-4 max-h-[85vh] flex flex-col border border-slate-800 shadow-2xl">
                <div class="flex justify-between items-center border-b border-slate-800 pb-3">
                    <div class="flex items-center gap-2">
                        <span class="text-amber-400 font-black">🤖 QT30 AI 社區修繕智慧窗口</span>
                    </div>
                    <button onclick="closeAiChatModal()" class="text-slate-400 hover:text-white">✕</button>
                </div>
                <div id="aiChatMessages" class="flex-1 overflow-y-auto space-y-3 p-2 bg-slate-950 rounded-2xl text-xs">
                    <div class="bg-slate-800 p-3 rounded-xl text-slate-200">
                        您好！我是 QT30 社區修繕 AI 窗口。請告訴我您的「工種、施作坪數或屋況」，我會為您即時計算雙北與淡水實價行情，並幫您一鍵填入表單！
                    </div>
                </div>
                <div class="flex gap-1.5 overflow-x-auto pb-1 text-[11px]">
                    <button onclick="sendAiQuickPrompt('油漆 20 坪壁癌行情')" class="bg-slate-800 text-amber-300 px-2.5 py-1 rounded-lg border border-slate-700 whitespace-nowrap">🎨 油漆 20 坪行情</button>
                    <button onclick="sendAiQuickPrompt('浴室 1.5 坪整修行情')" class="bg-slate-800 text-amber-300 px-2.5 py-1 rounded-lg border border-slate-700 whitespace-nowrap">🚿 浴室整修行情</button>
                    <button onclick="sendAiQuickPrompt('屋頂防水 20 坪抓漏')" class="bg-slate-800 text-amber-300 px-2.5 py-1 rounded-lg border border-slate-700 whitespace-nowrap">🏠 屋頂防水行情</button>
                </div>
                <div class="flex gap-2">
                    <input type="text" id="aiChatInput" placeholder="請輸入您的修繕問題或坪數..." class="flex-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-white text-xs">
                    <button onclick="sendAiMessage()" class="bg-amber-500 text-slate-950 font-black px-4 rounded-xl text-xs">諮詢</button>
                </div>
            </div>
        </div>

        <script>
            let map, marker;
            const districtCoords = {
                "淡水區": [25.175, 121.443],
                "板橋區": [25.012, 121.465],
                "三重區": [25.061, 121.498],
                "中和區": [24.998, 121.500],
                "新莊區": [25.035, 121.450],
                "新店區": [24.968, 121.541],
                "台北市全區": [25.033, 121.565],
                "桃園市全區": [24.993, 121.300]
            };

            function initMap() {
                map = L.map('map').setView([25.175, 121.443], 13);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '© OpenStreetMap'
                }).addTo(map);

                marker = L.marker([25.175, 121.443], {draggable: true}).addTo(map);
                marker.on('dragend', function() {
                    const pos = marker.getLatLng();
                    document.getElementById('lat').value = pos.lat;
                    document.getElementById('lng').value = pos.lng;
                });
                map.on('click', function(e) {
                    marker.setLatLng(e.latlng);
                    document.getElementById('lat').value = e.latlng.lat;
                    document.getElementById('lng').value = e.latlng.lng;
                });

                loadMapPins();
            }

            async function loadMapPins() {
                try {
                    const res = await fetch('/api/public/map_pins');
                    const data = await res.json();
                    
                    // 1. 駐點師傅 (綠色圖釘)
                    data.techs.forEach(t => {
                        L.circleMarker([t.lat, t.lng], {color: '#22c55e', radius: 8, fillOpacity: 0.8}).addTo(map)
                            .bindPopup(`<b>🟢 駐點達人：${t.name}</b><br>專長：${t.skills}<br>證照：${t.license_name || '實名核驗'}<br>年資：${t.experience_years} 年`);
                    });
                    // 2. 進行中工單 (黃色圖釘)
                    data.active_orders.forEach(o => {
                        L.circleMarker([o.lat, o.lng], {color: '#eab308', radius: 7, fillOpacity: 0.8}).addTo(map)
                            .bindPopup(`<b>🟡 進行中：${o.category}</b><br>規模：${o.area_ping}坪 ｜ ${o.district}<br>狀態：${o.work_status}`);
                    });
                    // 3. 歷史實價登錄 (藍色圖釘)
                    data.closed_orders.forEach(c => {
                        L.circleMarker([c.lat, c.lng], {color: '#3b82f6', radius: 7, fillOpacity: 0.8}).addTo(map)
                            .bindPopup(`<b>🔵 實價登錄：${c.category}</b><br>完工登錄金額：<span style="color:#d97706;font-weight:bold;">NT$ ${c.final_price.toLocaleString()}</span><br>規模：${c.area_ping} 坪 ｜ ${c.district}`);
                    });
                } catch(e){}
            }

            function onDistrictChange() {
                const dist = document.getElementById('district').value;
                if(districtCoords[dist]) {
                    const coord = districtCoords[dist];
                    map.setView(coord, 13);
                    marker.setLatLng(coord);
                    document.getElementById('lat').value = coord[0];
                    document.getElementById('lng').value = coord[1];
                }
            }

            function locateUser() {
                if (navigator.geolocation) {
                    navigator.geolocation.getCurrentPosition(function(pos) {
                        const lat = pos.coords.latitude;
                        const lng = pos.coords.longitude;
                        map.setView([lat, lng], 15);
                        marker.setLatLng([lat, lng]);
                        document.getElementById('lat').value = lat;
                        document.getElementById('lng').value = lng;
                    }, function() {
                        alert('無法獲取定位權限，請在地圖上手動點選。');
                    });
                }
            }

            window.onload = initMap;

            function triggerAiCalc() {
                const cat = document.getElementById('category').value;
                const ping = parseFloat(document.getElementById('areaPing').value) || 0;
                let estimated = 0;

                if (cat === '油漆粉刷') estimated = ping > 0 ? ping * 1500 : 25000;
                else if (cat === '老屋翻新/統包') estimated = ping > 0 ? ping * 35000 : 800000;
                else if (cat === '衛浴整修') estimated = 75000;
                else if (cat === '屋頂防水') estimated = ping > 0 ? ping * 3000 : 65000;
                else if (cat === '水電工程') estimated = ping > 0 ? ping * 1800 : 45000;
                else estimated = ping > 0 ? ping * 2000 : 30000;

                document.getElementById('budget').value = Math.round(estimated);
                document.getElementById('aiEstimatedDisplay').innerText = 'NT$ ' + Math.round(estimated).toLocaleString();
            }

            function openAiChatModal() { document.getElementById('aiChatModal').classList.remove('hidden'); }
            function closeAiChatModal() { document.getElementById('aiChatModal').classList.add('hidden'); }

            function sendAiQuickPrompt(prompt) {
                document.getElementById('aiChatInput').value = prompt;
                sendAiMessage();
            }

            async function sendAiMessage() {
                const text = document.getElementById('aiChatInput').value.trim();
                if(!text) return;
                const box = document.getElementById('aiChatMessages');
                box.innerHTML += `<div class="bg-slate-800 p-2.5 rounded-xl text-amber-300 font-bold ml-6 text-right">${text}</div>`;
                document.getElementById('aiChatInput').value = '';

                let reply = '', estPing = 20, estCat = '油漆粉刷', estBudget = 30000;
                if(text.includes('油漆')) {
                    reply = '📊 【AI 分析】：依據雙北與淡水實價登錄，20 坪室內油漆批土粉刷行情約 NT$ 30,000 ~ 35,000。已為您帶入估算！';
                    estCat = '油漆粉刷'; estPing = 20; estBudget = 32000;
                } else if(text.includes('浴室') || text.includes('衛浴')) {
                    reply = '📊 【AI 分析】：1.5 坪標準衛浴整修（浴缸拆除+彈泥防水重做+地壁磚+乾濕分離）市場行情約 NT$ 75,000 ~ 85,000。已為您帶入估算！';
                    estCat = '衛浴整修'; estPing = 1.5; estBudget = 78000;
                } else if(text.includes('防水') || text.includes('屋頂')) {
                    reply = '📊 【AI 分析】：20 坪頂樓防水抓漏（PU刨除+水刀清洗+聚酯抗裂網+2道耐候面漆）行情約 NT$ 65,000 ~ 72,000。已為您帶入估算！';
                    estCat = '屋頂防水'; estPing = 20; estBudget = 68000;
                } else {
                    reply = `📊 【AI 分析】：已為您分析「${text}」市場行情區間，並依據標準工料計算帶入表單。`;
                }

                setTimeout(() => {
                    box.innerHTML += `<div class="bg-slate-800 p-2.5 rounded-xl text-slate-200">${reply}</div>`;
                    box.scrollTop = box.scrollHeight;
                    document.getElementById('category').value = estCat;
                    document.getElementById('areaPing').value = estPing;
                    document.getElementById('budget').value = estBudget;
                    document.getElementById('aiEstimatedDisplay').innerText = 'NT$ ' + estBudget.toLocaleString();
                }, 400);
            }

            document.getElementById('orderPhotoFile').addEventListener('change', function(e) {
                const file = e.target.files[0];
                if(file) {
                    const reader = new FileReader();
                    reader.onload = function(evt) { document.getElementById('photoData').value = evt.target.result; };
                    reader.readAsDataURL(file);
                }
            });

            document.getElementById('orderForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const b = parseFloat(document.getElementById('budget').value) || 0;
                const payload = {
                    name: document.getElementById('name').value,
                    phone: document.getElementById('phone').value,
                    district: document.getElementById('district').value,
                    address: document.getElementById('address').value,
                    lat: parseFloat(document.getElementById('lat').value),
                    lng: parseFloat(document.getElementById('lng').value),
                    category: document.getElementById('category').value,
                    area_ping: parseFloat(document.getElementById('areaPing').value) || 0,
                    scope_desc: document.getElementById('scopeDesc').value,
                    house_age: document.getElementById('houseAge').value,
                    description: document.getElementById('description').value,
                    photo_data: document.getElementById('photoData').value,
                    budget: b
                };
                const res = await fetch('/api/orders', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.success) {
                    alert('🎉 報修發案成功！已為您開放該社區認證師傅進行暗標競價與現場勘查。');
                    document.getElementById('orderForm').reset();
                    document.getElementById('aiEstimatedDisplay').innerText = 'NT$ 0';
                } else {
                    alert('送出失敗，請稍後再試。');
                }
            });
        </script>
    </body>
    </html>
    ''')

# ==========================================
# 2. 師傅端工作台 (/tech)
# ==========================================
@app.route("/tech", methods=["GET", "POST"])
def tech_app():
    if request.method == "POST":
        trade_no = request.form.get("MerchantTradeNo", "")
        rtn_code = request.form.get("RtnCode", "")
        if rtn_code == "1" and trade_no:
            process_paid_order(trade_no, request.form.get("TradeAmt", ""))
    else:
        trade_no = request.args.get("MerchantTradeNo", "")
        if trade_no:
            process_paid_order(trade_no)

    return render_template_string('''
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>QT30 師傅工作台｜社區達人接單與實名證照管理</title>
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            #techMap { height: 260px; width: 100%; border-radius: 1rem; z-index: 1; }
        </style>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen antialiased">
        <div id="authBox" class="p-6 max-w-md mx-auto">
            <div class="text-center py-6">
                <span class="text-xs font-black text-amber-400 tracking-wider">QT30 社區修繕團隊</span>
                <h1 class="text-3xl font-black text-white mt-1">師傅工作台</h1>
                <p class="text-slate-400 text-sm mt-1">實名認證・國家證照・社區達人深耕・暗標競價</p>
            </div>
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-2xl space-y-4">
                <div class="flex border-b border-slate-800 pb-2 mb-4">
                    <button id="tabLogin" class="flex-1 text-center font-bold text-amber-400 pb-2 border-b-2 border-amber-400">師傅登入</button>
                    <button id="tabRegister" class="flex-1 text-center font-bold text-slate-400 pb-2">新師傅註冊 (實名制)</button>
                </div>
                <form id="loginForm" class="space-y-4">
                    <div>
                        <label class="block text-sm text-slate-300">手機號碼</label>
                        <input type="tel" id="loginPhone" required placeholder="0912345678" class="w-full mt-1 p-3 bg-slate-950 border border-slate-700 rounded-xl text-white">
                    </div>
                    <div>
                        <label class="block text-sm text-slate-300">登入密碼</label>
                        <input type="password" id="loginPassword" required placeholder="請輸入密碼" class="w-full mt-1 p-3 bg-slate-950 border border-slate-700 rounded-xl text-white">
                    </div>
                    <button type="submit" class="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-black py-3.5 rounded-xl transition">🔑 登入工作台</button>
                </form>

                <form id="registerForm" class="space-y-3 hidden">
                    <div>
                        <label class="block text-xs text-slate-300">真實姓名 (實名核驗)</label>
                        <input type="text" id="regName" required placeholder="例如：林大明" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-white text-xs">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">手機號碼 (登入帳號)</label>
                        <input type="tel" id="regPhone" required placeholder="0912345678" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-white text-xs">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">設定密碼</label>
                        <input type="password" id="regPassword" required class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-white text-xs">
                    </div>
                    <div class="grid grid-cols-2 gap-2">
                        <div>
                            <label class="block text-xs text-slate-300">專業證照名稱 (選填)</label>
                            <input type="text" id="regLicense" placeholder="例：乙級室內配線" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-amber-300 text-xs">
                        </div>
                        <div>
                            <label class="block text-xs text-slate-300">施工經歷年資</label>
                            <input type="number" id="regExp" value="8" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-white text-xs">
                        </div>
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">主修專業工種</label>
                        <select id="regSkills" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-amber-400 text-xs font-bold">
                            <option value="水電工程">⚡ 水電工程</option>
                            <option value="油漆粉刷">🎨 油漆粉刷</option>
                            <option value="泥作貼磚">🧱 泥作貼磚</option>
                            <option value="木作裝潢">🪵 木作裝潢</option>
                            <option value="系統櫃工程">🗄️ 系統櫃工程</option>
                            <option value="抓漏檢測">🔍 抓漏檢測</option>
                            <option value="屋頂防水">🏠 屋頂防水</option>
                            <option value="外牆防水">🏢 外牆防水</option>
                            <option value="老屋翻新/統包">🏡 老屋翻新統包</option>
                            <option value="衛浴整修">🚿 衛浴整修</option>
                            <option value="冷氣空調">❄️ 冷氣空調</option>
                            <option value="鋁門窗/氣密窗">🪟 鋁門窗/氣密窗</option>
                            <option value="搬家清運">📦 搬家清運</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">同行推薦人邀請碼 (選填)</label>
                        <input type="text" id="regRefCode" placeholder="填同行推薦碼，推薦人贈 300 點" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-amber-300 font-mono text-xs">
                    </div>
                    <button type="submit" class="w-full bg-green-600 hover:bg-green-500 text-white font-bold py-3 rounded-xl transition">📝 註冊認證 (送 100 點體驗金)</button>
                </form>
            </div>
        </div>

        <!-- Dashboard -->
        <div id="dashBox" class="hidden max-w-5xl mx-auto p-4 space-y-6">
            <div class="bg-slate-900 p-5 rounded-3xl border border-slate-800 flex flex-wrap items-center justify-between gap-4 shadow-xl">
                <div>
                    <h2 class="text-xl font-black flex items-center gap-2">
                        <span id="userName"></span> 師傅
                        <span class="text-xs px-2.5 py-1 rounded-full bg-green-500/20 text-green-400 font-bold border border-green-500/30">✓ 實名核驗達人</span>
                    </h2>
                    <p class="text-slate-400 text-xs mt-1">主修：<span id="userSkills" class="text-amber-300 font-bold"></span> ｜ 證照：<span id="userLicense" class="text-slate-200"></span> (經歷 <span id="userExp"></span> 年)</p>
                    <p class="text-slate-400 text-xs mt-0.5">駐點行政區：<span id="userDistricts" class="text-green-400 font-bold">無</span> ｜ 我的推薦碼：<span id="myRefCode" class="text-amber-400 font-bold font-mono"></span></p>
                </div>
                <div class="flex items-center gap-4">
                    <div class="text-right">
                        <div class="text-xs text-slate-400">點數餘額</div>
                        <div class="text-2xl font-black text-amber-400"><span id="userPoints">0</span> <span class="text-xs font-normal text-slate-300">點</span></div>
                    </div>
                    <button onclick="showSection('topup')" class="bg-amber-500 hover:bg-amber-400 text-slate-950 font-black px-4 py-2 rounded-xl text-sm transition">💳 儲值</button>
                    <button onclick="logout()" class="text-slate-400 hover:text-white text-sm">登出</button>
                </div>
            </div>

            <div class="flex border-b border-slate-800 space-x-6 text-sm font-bold">
                <button onclick="showSection('orders')" id="btnTabOrders" class="text-amber-400 border-b-2 border-amber-400 pb-2">📋 競標大廳</button>
                <button onclick="showSection('myProjects')" id="btnTabProjects" class="text-slate-400 pb-2">🛠️ 得標工單存證</button>
                <button onclick="showSection('lockArea')" id="btnTabLock" class="text-slate-400 pb-2">👑 社區達人卡位</button>
                <button onclick="showSection('topup')" id="btnTabTopup" class="text-slate-400 pb-2">💎 購點與月費方案</button>
            </div>

            <!-- 1. 競標大廳 -->
            <div id="sectionOrders" class="space-y-4">
                <div class="bg-slate-900 p-4 rounded-2xl border border-slate-800 space-y-2">
                    <div class="flex justify-between items-center">
                        <span class="text-xs font-bold text-amber-400">🗺️ 雙北修繕案件即時分佈</span>
                        <span class="text-[11px] text-slate-400">暗標競價：互隱價格，顯示投標人數</span>
                    </div>
                    <div id="techMap"></div>
                </div>

                <div class="flex justify-between items-center bg-slate-900 p-3.5 rounded-xl border border-slate-800">
                    <div class="flex items-center gap-2">
                        <span class="text-xs font-bold text-slate-300">工種篩選：</span>
                        <select id="filterCategory" onchange="loadOrders()" class="bg-slate-950 border border-slate-700 text-amber-300 font-bold text-xs p-2 rounded-lg">
                            <option value="ALL">全部 20 大工種</option>
                            <option value="水電工程">⚡ 水電工程</option>
                            <option value="油漆粉刷">🎨 油漆粉刷</option>
                            <option value="泥作貼磚">🧱 泥作貼磚</option>
                            <option value="衛浴整修">🚿 衛浴整修</option>
                            <option value="老屋翻新">🏡 老屋翻新統包</option>
                            <option value="防水">🏠 防水抓漏</option>
                        </select>
                    </div>
                    <button onclick="loadOrders()" class="text-xs bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded-lg text-white font-bold">🔄 刷新列表</button>
                </div>
                <div id="ordersList" class="space-y-3"></div>
            </div>

            <!-- 2. 得標工單進度回報 -->
            <div id="sectionMyProjects" class="hidden space-y-4">
                <h3 class="font-bold text-slate-200">得標案件・施工存證回報</h3>
                <div id="myProjectsList" class="space-y-3"></div>
            </div>

            <!-- 3. 卡位專區 -->
            <div id="sectionLockArea" class="hidden bg-slate-900 p-6 rounded-2xl border border-slate-800 space-y-6">
                <div>
                    <h3 class="text-xl font-bold text-amber-400">👑 社區/行政區達人卡位</h3>
                    <p class="text-slate-400 text-xs mt-1">持有 3,000 點以上免費加入卡位，享有該區新案優先 15 分鐘通知與搶標！</p>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div class="bg-slate-950 border border-slate-800 p-4 rounded-xl space-y-3">
                        <div class="font-bold text-white">📍 新北・淡水區</div>
                        <p class="text-xs text-slate-400">門檻：持有 3,000 點 (無需扣點)</p>
                        <button onclick="joinDistrict('淡水區')" class="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold py-2 rounded-lg text-xs transition">加入淡水卡位</button>
                    </div>
                    <div class="bg-slate-950 border border-slate-800 p-4 rounded-xl space-y-3">
                        <div class="font-bold text-white">📍 新北・板橋區</div>
                        <p class="text-xs text-slate-400">門檻：持有 3,000 點 (無需扣點)</p>
                        <button onclick="joinDistrict('板橋區')" class="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold py-2 rounded-lg text-xs transition">加入板橋卡位</button>
                    </div>
                    <div class="bg-slate-950 border border-slate-800 p-4 rounded-xl space-y-3">
                        <div class="font-bold text-white">📍 新北・三重區</div>
                        <p class="text-xs text-slate-400">門檻：持有 3,000 點 (無需扣點)</p>
                        <button onclick="joinDistrict('三重區')" class="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-bold py-2 rounded-lg text-xs transition">加入三重卡位</button>
                    </div>
                </div>
            </div>

            <!-- 4. 購點方案 -->
            <div id="sectionTopup" class="hidden bg-slate-900 p-6 rounded-2xl border border-slate-800 space-y-6">
                <div>
                    <h3 class="text-xl font-bold text-amber-400">💎 師傅購點與達人月費方案</h3>
                    <p class="text-slate-400 text-xs mt-1">扣點依 AI 估價 7 折計算 3% 扣除。綠界線上刷卡秒級入帳！</p>
                </div>
                <div class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-4">
                    <div class="bg-slate-950 border border-slate-800 p-4 rounded-xl text-center space-y-2">
                        <div class="text-xs text-slate-400">新手體驗包</div>
                        <div class="text-2xl font-black text-amber-400">500 點</div>
                        <button onclick="payECPay(500, 500)" class="w-full bg-slate-800 hover:bg-slate-700 text-white font-bold py-2 rounded-lg text-xs">線上刷卡 NT$ 500</button>
                    </div>
                    <div class="bg-slate-950 border border-slate-800 p-4 rounded-xl text-center space-y-2">
                        <div class="text-xs text-slate-400">社區實力包 (+20%)</div>
                        <div class="text-2xl font-black text-amber-400">1,200 點</div>
                        <button onclick="payECPay(1000, 1200)" class="w-full bg-slate-800 hover:bg-slate-700 text-white font-bold py-2 rounded-lg text-xs">線上刷卡 NT$ 1,000</button>
                    </div>
                    <div class="bg-slate-950 border border-amber-500/50 p-4 rounded-xl text-center space-y-2">
                        <div class="text-xs text-amber-400 font-bold">👑 區域卡位包 (+30%)</div>
                        <div class="text-2xl font-black text-amber-400">3,900 點</div>
                        <button onclick="payECPay(3000, 3900)" class="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-black py-2 rounded-lg text-xs">線上刷卡 NT$ 3,000</button>
                    </div>
                    <div class="bg-slate-950 border border-slate-800 p-4 rounded-xl text-center space-y-2">
                        <div class="text-xs text-slate-400">進階達人包 (+40%)</div>
                        <div class="text-2xl font-black text-amber-400">7,000 點</div>
                        <button onclick="payECPay(5000, 7000)" class="w-full bg-slate-800 hover:bg-slate-700 text-white font-bold py-2 rounded-lg text-xs">線上刷卡 NT$ 5,000</button>
                    </div>
                    <div class="bg-slate-950 border border-slate-800 p-4 rounded-xl text-center space-y-2">
                        <div class="text-xs text-slate-400">工班隊長包 (+50%)</div>
                        <div class="text-2xl font-black text-amber-400">12,000 點</div>
                        <button onclick="payECPay(8000, 12000)" class="w-full bg-slate-800 hover:bg-slate-700 text-white font-bold py-2 rounded-lg text-xs">線上刷卡 NT$ 8,000</button>
                    </div>
                    <div class="bg-slate-950 border-2 border-amber-400 p-4 rounded-xl text-center space-y-2">
                        <div class="text-xs text-amber-300 font-black">🔥 統包尊榮包 (+66.7%)</div>
                        <div class="text-2xl font-black text-amber-400">20,000 點</div>
                        <button onclick="payECPay(12000, 20000)" class="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-black py-2 rounded-lg text-xs">線上刷卡 NT$ 12,000</button>
                    </div>
                </div>
            </div>
        </div>

        <!-- 報價彈窗 -->
        <div id="bidModal" class="hidden fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4">
            <div class="bg-slate-900 rounded-3xl max-w-lg w-full p-6 space-y-4 max-h-[90vh] overflow-y-auto border border-slate-800">
                <div class="flex justify-between items-center border-b border-slate-800 pb-3">
                    <h3 class="text-lg font-black text-amber-400" id="bidModalTitle">⚡ 暗標報價與工項填寫</h3>
                    <button onclick="closeBidModal()" class="text-slate-400 hover:text-white">✕</button>
                </div>
                <div class="bg-slate-950 p-3.5 rounded-2xl space-y-1 text-xs">
                    <div>工種：<span id="bidModalCategory" class="text-amber-400 font-bold"></span> ｜ 坪數：<span id="bidModalPing" class="text-slate-300 font-bold"></span> 坪</div>
                    <div>AI 行情預估：<span id="bidModalBudget" class="text-white font-bold"></span> ｜ 投標扣點：<span id="bidModalReqPts" class="text-amber-400 font-black"></span></div>
                    <div class="text-slate-400 pt-1">需求描述：<span id="bidModalDesc" class="text-slate-200"></span></div>
                    <div id="bidModalPhotoBox" class="mt-2 hidden">
                        <span class="text-slate-400">業主上傳照片：</span>
                        <img id="bidModalPhoto" src="" class="mt-1 h-32 rounded-xl object-cover border border-slate-700">
                    </div>
                </div>
                <form id="bidSubmitForm" class="space-y-4">
                    <input type="hidden" id="bidOrderId">
                    <input type="hidden" id="bidReqPoints">
                    <div>
                        <label class="block text-xs font-bold text-slate-300">您的最終報價金額 (新台幣 NT$)</label>
                        <input type="number" id="bidAmount" required placeholder="例：35000" class="w-full mt-1 p-3 bg-slate-950 border border-slate-700 rounded-xl text-amber-400 font-black text-lg">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-300">報價工項與材料承諾</label>
                        <textarea id="bidNotes" rows="3" required placeholder="請條列包含工資、材料品牌規格、施工天數與保固承諾..." class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-xl text-white text-xs"></textarea>
                    </div>
                    <button type="submit" class="w-full bg-green-600 hover:bg-green-500 text-white font-black py-3.5 rounded-xl shadow-lg transition" id="btnSubmitBid">🚀 確認扣點並送出暗標</button>
                </form>
            </div>
        </div>

        <script>
            let currentTech = null;
            let techMap = null;
            let mapMarkers = [];

            document.getElementById('tabLogin').onclick = () => {
                document.getElementById('loginForm').classList.remove('hidden');
                document.getElementById('registerForm').classList.add('hidden');
                document.getElementById('tabLogin').className = 'flex-1 text-center font-bold text-amber-400 pb-2 border-b-2 border-amber-400';
                document.getElementById('tabRegister').className = 'flex-1 text-center font-bold text-slate-400 pb-2';
            };
            document.getElementById('tabRegister').onclick = () => {
                document.getElementById('registerForm').classList.remove('hidden');
                document.getElementById('loginForm').classList.add('hidden');
                document.getElementById('tabRegister').className = 'flex-1 text-center font-bold text-amber-400 pb-2 border-b-2 border-amber-400';
                document.getElementById('tabLogin').className = 'flex-1 text-center font-bold text-slate-400 pb-2';
            };

            document.getElementById('loginForm').onsubmit = async (e) => {
                e.preventDefault();
                const res = await fetch('/api/tech/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        phone: document.getElementById('loginPhone').value.trim(),
                        password: document.getElementById('loginPassword').value.trim()
                    })
                });
                const data = await res.json();
                if(data.success) {
                    currentTech = data.tech;
                    localStorage.setItem('qt30_tech', JSON.stringify(currentTech));
                    syncTechInfo();
                } else {
                    alert(data.message || '登入失敗');
                }
            };

            document.getElementById('registerForm').onsubmit = async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('regName').value.trim(),
                    phone: document.getElementById('regPhone').value.trim(),
                    password: document.getElementById('regPassword').value.trim(),
                    skills: document.getElementById('regSkills').value.trim(),
                    license_name: document.getElementById('regLicense').value.trim(),
                    experience_years: parseInt(document.getElementById('regExp').value) || 5,
                    referral_by: document.getElementById('regRefCode').value.trim()
                };
                const res = await fetch('/api/tech/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.success) {
                    alert('🎉 註冊成功！系統已發放 100 點體驗點數與實名認證徽章。');
                    currentTech = data.tech;
                    localStorage.setItem('qt30_tech', JSON.stringify(currentTech));
                    syncTechInfo();
                } else {
                    alert(data.message || '註冊失敗');
                }
            };

            function initTechMap() {
                if (!techMap) {
                    techMap = L.map('techMap').setView([25.10, 121.48], 11);
                    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                        attribution: '© OpenStreetMap'
                    }).addTo(techMap);
                }
            }

            function renderDashboard() {
                document.getElementById('authBox').classList.add('hidden');
                document.getElementById('dashBox').classList.remove('hidden');
                document.getElementById('userName').innerText = currentTech.name;
                document.getElementById('userSkills').innerText = currentTech.skills || '全項修繕';
                document.getElementById('userLicense').innerText = currentTech.license_name || '國家合格核驗';
                document.getElementById('userExp').innerText = currentTech.experience_years || 5;
                document.getElementById('userPoints').innerText = currentTech.points;
                document.getElementById('myRefCode').innerText = currentTech.referral_code;
                document.getElementById('userDistricts').innerText = currentTech.exclusive_districts || '尚未加入任何卡位名單';
                
                setTimeout(() => {
                    initTechMap();
                    loadOrders();
                }, 100);
            }

            async function syncTechInfo() {
                if(!currentTech || !currentTech.phone) return;
                try {
                    const res = await fetch('/api/tech/info?phone=' + encodeURIComponent(currentTech.phone));
                    const d = await res.json();
                    if(d.success) {
                        currentTech = d.tech;
                        localStorage.setItem('qt30_tech', JSON.stringify(currentTech));
                    }
                } catch(e){}
                renderDashboard();
            }

            function showSection(sec) {
                ['Orders', 'MyProjects', 'LockArea', 'Topup'].forEach(s => {
                    document.getElementById('section' + s).classList.add('hidden');
                    document.getElementById('btnTab' + s).className = 'text-slate-400 pb-2';
                });
                const targetKey = sec.charAt(0).toUpperCase() + sec.slice(1);
                document.getElementById('section' + targetKey).classList.remove('hidden');
                document.getElementById('btnTab' + targetKey).className = 'text-amber-400 border-b-2 border-amber-400 pb-2';
                if(sec === 'orders' && techMap) {
                    setTimeout(() => { techMap.invalidateSize(); }, 200);
                }
                if(sec === 'myProjects') loadMyProjects();
            }

            async function joinDistrict(dist) {
                if(currentTech.points < 3000) {
                    alert('卡位資格門檻需持有 3,000 點以上，請先前往儲值！');
                    showSection('topup');
                    return;
                }
                const res = await fetch('/api/tech/join_district', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone: currentTech.phone, district: dist})
                });
                const d = await res.json();
                if(d.success) {
                    alert(`🎉 恭喜！您已成功加入【${dist}】社區達人卡位名單！`);
                    syncTechInfo();
                } else {
                    alert(d.message || '加入失敗');
                }
            }

            async function loadOrders() {
                const res = await fetch('/api/orders');
                const orders = await res.json();
                const list = document.getElementById('ordersList');
                list.innerHTML = '';
                
                mapMarkers.forEach(m => techMap.removeLayer(m));
                mapMarkers = [];

                const filterVal = document.getElementById('filterCategory').value;
                const myDists = (currentTech.exclusive_districts || '').split(',');
                
                const filteredOrders = orders.filter(o => {
                    if(filterVal === 'ALL') return true;
                    return o.category && o.category.includes(filterVal);
                });

                if(filteredOrders.length === 0) {
                    list.innerHTML = '<div class="text-center py-8 text-slate-500">目前尚無符合該條件的待競標工單</div>';
                    return;
                }

                filteredOrders.forEach(o => {
                    const isClosed = o.status === 'closed';
                    const isMyArea = myDists.includes(o.district);

                    if (!isClosed && o.lat && o.lng) {
                        const m = L.marker([o.lat, o.lng]).addTo(techMap)
                            .bindPopup(`<b>#${o.id} ${o.category}</b><br>${o.area_ping} 坪 ｜ ${o.district}<br><span style="color:#d97706;font-weight:bold;">已有 ${o.bid_count || 0} 位師傅報價</span>`);
                        mapMarkers.push(m);
                    }

                    list.innerHTML += `
                        <div class="bg-slate-900 p-4 rounded-2xl border ${isMyArea ? 'border-amber-500' : 'border-slate-800'} flex flex-col md:flex-row justify-between items-start md:items-center gap-3">
                            <div>
                                <div class="flex items-center gap-2">
                                    <span class="bg-blue-600/30 text-blue-400 text-xs px-2 py-0.5 rounded font-bold">${o.category}</span>
                                    <span class="bg-amber-500/20 text-amber-300 text-xs px-2 py-0.5 rounded font-bold">📍 ${o.district}</span>
                                    <span class="bg-purple-600/30 text-purple-300 text-xs px-2 py-0.5 rounded font-bold">已有 ${o.bid_count || 0} 位競標</span>
                                    ${isMyArea ? '<span class="bg-amber-500 text-slate-950 text-[10px] px-2 py-0.5 rounded-full font-bold">👑 您已卡位此區</span>' : ''}
                                </div>
                                <h4 class="text-base font-bold text-white mt-1">${o.description}</h4>
                                <div class="text-xs text-slate-400 mt-0.5">
                                    坪數：<span class="text-amber-400 font-bold">${o.area_ping} 坪</span> ｜ 
                                    範圍：<span class="text-slate-200">${o.scope_desc || '全區'}</span> ｜ 
                                    AI估價：<span class="text-amber-400 font-bold">NT$ ${o.budget.toLocaleString()}</span> ｜ 
                                    投標扣點：<span class="text-amber-300 font-black">${o.required_points} 點</span>
                                </div>
                            </div>
                            <div>
                                ${isClosed ? '<span class="bg-slate-800 text-slate-500 text-xs px-3 py-1.5 rounded-lg">已結標媒合</span>' : 
                                `<button onclick="openBidModal(${o.id}, '${o.category}', ${o.area_ping}, '${o.description}', ${o.budget}, ${o.required_points}, '${o.photo_data || ''}')" class="bg-amber-500 hover:bg-amber-400 text-slate-950 font-black px-4 py-2 rounded-xl text-xs transition">⚡ 扣 ${o.required_points} 點暗標報價</button>`}
                            </div>
                        </div>
                    `;
                });
            }

            function openBidModal(id, cat, ping, desc, budget, reqPts, photo) {
                if(currentTech.points < reqPts) {
                    alert(`您的點數不足 (需 ${reqPts} 點)，請先前往儲值！`);
                    showSection('topup');
                    return;
                }
                document.getElementById('bidOrderId').value = id;
                document.getElementById('bidReqPoints').value = reqPts;
                document.getElementById('bidModalTitle').innerText = `⚡ 案件 #${id} 暗標競價`;
                document.getElementById('bidModalCategory').innerText = cat;
                document.getElementById('bidModalPing').innerText = ping;
                document.getElementById('bidModalBudget').innerText = `NT$ ${budget.toLocaleString()}`;
                document.getElementById('bidModalReqPts').innerText = `${reqPts} 點`;
                document.getElementById('bidModalDesc').innerText = desc;
                document.getElementById('bidAmount').value = budget;
                
                if(photo) {
                    document.getElementById('bidModalPhotoBox').classList.remove('hidden');
                    document.getElementById('bidModalPhoto').src = photo;
                } else {
                    document.getElementById('bidModalPhotoBox').classList.add('hidden');
                }

                document.getElementById('btnSubmitBid').innerText = `🚀 確認扣除 ${reqPts} 點並送出暗標`;
                document.getElementById('bidModal').classList.remove('hidden');
            }

            function closeBidModal() { document.getElementById('bidModal').classList.add('hidden'); }

            document.getElementById('bidSubmitForm').onsubmit = async (e) => {
                e.preventDefault();
                const payload = {
                    order_id: document.getElementById('bidOrderId').value,
                    tech_phone: currentTech.phone,
                    tech_name: currentTech.name,
                    bid_amount: parseInt(document.getElementById('bidAmount').value),
                    req_points: parseInt(document.getElementById('bidReqPoints').value),
                    notes: document.getElementById('bidNotes').value.trim()
                };
                const res = await fetch('/api/tech/submit_bid', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.success) {
                    alert('🎉 報價成功送出！請靜候業主或平台現勘媒合。');
                    closeBidModal();
                    syncTechInfo();
                } else {
                    alert(data.message || '報價失敗');
                }
            };

            async function loadMyProjects() {
                const res = await fetch('/api/orders');
                const orders = await res.json();
                const list = document.getElementById('myProjectsList');
                list.innerHTML = '';
                const myOrders = orders.filter(o => o.winner_phone.includes(currentTech.phone));
                if(myOrders.length === 0) {
                    list.innerHTML = '<div class="text-center py-8 text-slate-500">您目前尚無得標承接的工單</div>';
                    return;
                }
                myOrders.forEach(o => {
                    list.innerHTML += `
                        <div class="bg-slate-900 p-5 rounded-2xl border border-slate-800 space-y-3">
                            <div class="flex justify-between items-start">
                                <div>
                                    <h4 class="text-base font-bold text-white">#${o.id} [${o.category}] - ${o.description}</h4>
                                    <div class="text-xs text-green-400 font-bold mt-1">📞 客戶電話：${o.phone} (${o.name})</div>
                                    <div class="text-xs text-slate-300">地址：${o.district} ${o.address}</div>
                                </div>
                                <span class="bg-blue-600/30 text-blue-300 text-xs px-2.5 py-1 rounded font-bold">進度：${o.work_status}</span>
                            </div>
                            <div class="bg-slate-950 p-3 rounded-xl flex flex-wrap gap-2 items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="text-xs text-slate-400">更新進度：</span>
                                    <select id="st_${o.id}" class="bg-slate-900 text-white text-xs p-1.5 rounded border border-slate-700 font-bold">
                                        <option value="現勘確認中" ${o.work_status==='現勘確認中'?'selected':''}>現勘確認中</option>
                                        <option value="備料施工中" ${o.work_status==='備料施工中'?'selected':''}>備料施工中</option>
                                        <option value="完工待驗收" ${o.work_status==='完工待驗收'?'selected':''}>完工待驗收</option>
                                    </select>
                                    <button onclick="updateWorkStatus(${o.id})" class="bg-blue-600 text-white text-xs px-2.5 py-1.5 rounded font-bold">儲存進度</button>
                                </div>
                                <div class="flex items-center gap-2">
                                    <input type="file" id="file_${o.id}" accept="image/*" class="text-xs text-slate-400 file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-xs file:bg-slate-800 file:text-white">
                                    <button onclick="uploadPhoto(${o.id})" class="bg-amber-500 text-slate-950 font-bold text-xs px-2.5 py-1.5 rounded">📸 施工存證照</button>
                                </div>
                            </div>
                            ${o.photo_data ? `<div class="mt-2"><span class="text-xs text-slate-400">施工存證相片：</span><img src="${o.photo_data}" class="mt-1 h-28 rounded-lg border border-slate-700 object-cover"></div>` : ''}
                        </div>
                    `;
                });
            }

            async function updateWorkStatus(id) {
                const st = document.getElementById('st_' + id).value;
                const res = await fetch('/api/tech/update_order_work', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({order_id: id, work_status: st})
                });
                if((await res.json()).success) { alert('進度更新成功！'); loadMyProjects(); }
            }

            async function uploadPhoto(id) {
                const file = document.getElementById('file_' + id).files[0];
                if(!file) { alert('請先選擇照片！'); return; }
                const reader = new FileReader();
                reader.onload = async (e) => {
                    const res = await fetch('/api/tech/upload_order_photo', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({order_id: id, photo_data: e.target.result})
                    });
                    if((await res.json()).success) { alert('照片存證成功上傳！'); loadMyProjects(); }
                };
                reader.readAsDataURL(file);
            }

            async function payECPay(amount, points) {
                if(!currentTech) return;
                const res = await fetch('/api/ecpay/create_payment', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone: currentTech.phone, amount: amount, points: points})
                });
                const data = await res.json();
                if(data.success) {
                    const form = document.createElement('form');
                    form.method = 'POST';
                    form.action = data.ecpay_url;
                    for (const [k, v] of Object.entries(data.params)) {
                        const input = document.createElement('input');
                        input.type = 'hidden';
                        input.name = k;
                        input.value = v;
                        form.appendChild(input);
                    }
                    document.body.appendChild(form);
                    form.submit();
                }
            }

            function logout() {
                localStorage.removeItem('qt30_tech');
                location.reload();
            }

            const cached = localStorage.getItem('qt30_tech');
            if(cached) {
                currentTech = JSON.parse(cached);
                syncTechInfo();
            }
        </script>
    </body>
    </html>
    ''')

# ==========================================
# 3. 管理者後台 (/admin) - 暗標審查、實價登錄歸檔
# ==========================================
@app.route("/admin")
def admin_page():
    return render_template_string('''
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>QT30 營運總控制台</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen p-4 md:p-8 antialiased">
        <div id="loginBox" class="max-w-sm mx-auto mt-20 bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-2xl">
            <h1 class="text-xl font-black text-center mb-4 text-amber-400">🔐 QT30 總控制台</h1>
            <input type="password" id="adminPwd" placeholder="請輸入管理密碼" class="w-full p-3 bg-slate-950 border border-slate-700 rounded-xl mb-4 text-white">
            <button onclick="loginAdmin()" class="w-full bg-amber-500 hover:bg-amber-400 text-slate-950 font-black py-3 rounded-xl transition">登入後台</button>
        </div>

        <div id="adminPanel" class="hidden max-w-6xl mx-auto space-y-6">
            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-xl flex justify-between items-center">
                <div>
                    <h1 class="text-2xl font-black text-amber-400">QT30 營運總控制台</h1>
                    <p class="text-slate-400 text-sm">暗標審查・實名核驗・實價登錄公開管理</p>
                </div>
                <div class="flex gap-3">
                    <button onclick="loadAdminData()" class="px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-white font-bold text-sm border border-slate-700">🔄 重新整理</button>
                    <button onclick="location.reload()" class="px-3 py-2 text-red-400 text-sm">登出</button>
                </div>
            </div>

            <div class="bg-slate-900 p-6 rounded-3xl border border-slate-800 shadow-xl space-y-4">
                <h3 class="font-bold text-lg text-white">發案工單與暗標競價管理</h3>
                <div id="adminOrdersContainer" class="space-y-4"></div>
            </div>
        </div>

        <script>
            let adminToken = '';

            function loginAdmin() {
                const pwd = document.getElementById('adminPwd').value;
                if(pwd === '{{ admin_pwd }}' || pwd === 'admin888') {
                    adminToken = pwd;
                    document.getElementById('loginBox').classList.add('hidden');
                    document.getElementById('adminPanel').classList.remove('hidden');
                    loadAdminData();
                } else {
                    alert('密碼錯誤！');
                }
            }

            async function loadAdminData() {
                const res = await fetch('/api/admin/orders_with_bids?pwd=' + adminToken);
                const data = await res.json();
                const container = document.getElementById('adminOrdersContainer');
                container.innerHTML = '';

                data.forEach(o => {
                    let bidsHtml = '';
                    if (o.bids && o.bids.length > 0) {
                        bidsHtml = `
                            <div class="mt-3 bg-slate-950 p-3.5 rounded-xl border border-slate-800 space-y-2">
                                <div class="text-xs font-bold text-amber-400">📋 各師傅暗標報價 (共 ${o.bids.length} 家)：</div>
                                <div class="grid grid-cols-1 md:grid-cols-2 gap-2">
                                    ${o.bids.map(b => `
                                        <div class="bg-slate-900 p-3 rounded-lg border border-slate-800 text-xs flex justify-between items-center">
                                            <div>
                                                <span class="font-bold text-white">${b.tech_name} (${b.tech_phone})</span>
                                                <div class="text-amber-400 font-black mt-0.5">報價：NT$ ${b.bid_amount.toLocaleString()}</div>
                                                <div class="text-slate-400 text-[11px] mt-0.5">${b.notes}</div>
                                            </div>
                                            <div>
                                                ${o.winner_phone === b.tech_phone ? 
                                                '<span class="bg-green-500/20 text-green-400 font-bold px-2 py-1 rounded">✓ 已選定得標</span>' : 
                                                `<button onclick="selectWinner(${o.id}, '${b.tech_phone}', ${b.bid_amount})" class="bg-amber-500 hover:bg-amber-400 text-slate-950 font-black px-2.5 py-1.5 rounded-lg">選定得標並登錄實價</button>`}
                                            </div>
                                        </div>
                                    `).join('')}
                                </div>
                            </div>
                        `;
                    } else {
                        bidsHtml = '<div class="mt-2 text-xs text-slate-500">目前尚無師傅參與報價</div>';
                    }

                    container.innerHTML += `
                        <div class="border border-slate-800 rounded-2xl p-5 bg-slate-950/60 space-y-2 shadow-sm">
                            <div class="flex justify-between items-start">
                                <div>
                                    <span class="font-bold text-white text-base">#${o.id} - ${o.category}</span>
                                    <span class="text-xs bg-amber-500/20 text-amber-300 font-bold px-2 py-0.5 rounded ml-2">📍 ${o.district}</span>
                                    <div class="text-xs text-slate-400 mt-1">發案人：${o.name} (${o.phone}) ｜ 地址：${o.address}</div>
                                    <div class="text-xs text-slate-300 mt-1">坪數：${o.area_ping} 坪 ｜ 範圍：${o.scope_desc || '-'} ｜ 屋況：${o.house_age || '-'}</div>
                                    <div class="text-xs text-slate-400 mt-0.5">需求描述：${o.description}</div>
                                    ${o.photo_data ? `<img src="${o.photo_data}" class="h-16 mt-2 rounded-lg border border-slate-700 object-cover">` : ''}
                                </div>
                                <div class="text-right">
                                    <div class="text-xs text-slate-400">預估行情：NT$ ${o.budget.toLocaleString()}</div>
                                    <div class="text-xs font-black text-amber-400">投標扣點：${o.required_points} 點</div>
                                    <div class="text-xs font-bold text-blue-400 mt-1">狀態：${o.work_status}</div>
                                </div>
                            </div>
                            ${bidsHtml}
                        </div>
                    `;
                });
            }

            async function selectWinner(orderId, techPhone, finalAmt) {
                if(!confirm(`確認要選定師傅 (${techPhone}) 作為得標方，並將結案金額 NT$ ${finalAmt.toLocaleString()} 登錄至社區實價數據庫嗎？`)) return;
                const res = await fetch('/api/admin/select_winner', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({order_id: orderId, winner_phone: techPhone, final_price: finalAmt, pwd: adminToken})
                });
                if((await res.json()).success) {
                    alert('🎉 得標選定成功！實價登錄資料已同步至社區地圖。');
                    loadAdminData();
                }
            }
        </script>
    </body>
    </html>
    ''', admin_pwd=ADMIN_PASSWORD)

# ==========================================
# 4. 後端 API 集合
# ==========================================

@app.route("/api/public/map_pins")
def api_public_map_pins():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, skills, license_name, experience_years, lat, lng FROM technicians WHERE is_verified=1")
    techs = [{"name": r[0], "skills": r[1], "license_name": r[2], "experience_years": r[3], "lat": r[4] or 25.175, "lng": r[5] or 121.443} for r in c.fetchall()]
    
    c.execute("SELECT category, area_ping, district, work_status, lat, lng FROM orders WHERE status='bidding' OR work_status='施工中'")
    active_orders = [{"category": r[0], "area_ping": r[1], "district": r[2], "work_status": r[3], "lat": r[4] or 25.175, "lng": r[5] or 121.443} for r in c.fetchall()]

    c.execute("SELECT category, area_ping, district, final_price, lat, lng FROM orders WHERE status='closed' AND final_price > 0")
    closed_orders = [{"category": r[0], "area_ping": r[1], "district": r[2], "final_price": r[3], "lat": r[4] or 25.175, "lng": r[5] or 121.443} for r in c.fetchall()]
    conn.close()

    return jsonify({"techs": techs, "active_orders": active_orders, "closed_orders": closed_orders})

@app.route("/api/orders", methods=["GET", "POST"])
def api_orders():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if request.method == "POST":
        d = request.json
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        b = int(d.get('budget', 0))
        cat = d.get('category', '')
        
        calculated_pts = int(b * 0.70 * 0.03)
        if '老屋翻新' in cat or '室內設計' in cat or b >= 500000:
            req_pts = max(3000, calculated_pts)
        else:
            req_pts = max(30, calculated_pts)

        c.execute('''
            INSERT INTO orders (name, phone, district, address, lat, lng, category, area_ping, scope_desc, house_age, description, photo_data, budget, required_points, ref_tech_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (d['name'], d['phone'], d.get('district', '淡水區'), d['address'], float(d.get('lat', 25.175)), float(d.get('lng', 121.443)), d['category'], float(d.get('area_ping', 0)), d.get('scope_desc', ''), d.get('house_age', ''), d['description'], d.get('photo_data', ''), b, req_pts, d.get('ref_tech_code', ''), now_str))
        order_id = c.lastrowid
        conn.commit()
        conn.close()

        ref_info = f"\n推薦代碼：{d.get('ref_tech_code')}" if d.get('ref_tech_code') else ""
        msg = f"🔔 【QT30 新修繕發案】\n單號：#{order_id}\n工種：{d['category']} (約 {d.get('area_ping', 0)} 坪)\n客戶：{d['name']} ({d['phone']})\n地區：{d.get('district', '')}\n預估行情：NT$ {b:,}\n投標扣點：{req_pts} 點{ref_info}\n※ 已開放認證師傅暗標競價！"
        send_line_push_message(msg)
        return jsonify({"success": True, "order_id": order_id})

    c.execute('''
        SELECT o.id, o.name, o.phone, o.district, o.address, o.category, o.area_ping, o.scope_desc, o.house_age, o.description, o.photo_data, o.budget, o.required_points, o.ref_tech_code, o.status, o.winner_phone, o.work_status, o.created_at, o.lat, o.lng,
               COUNT(b.id) as bid_count
        FROM orders o
        LEFT JOIN bids b ON o.id = b.order_id
        GROUP BY o.id
        ORDER BY o.id DESC
    ''')
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "name": r[1], "phone": r[2], "district": r[3],
            "address": r[4], "category": r[5], "area_ping": r[6], "scope_desc": r[7], "house_age": r[8], "description": r[9], "photo_data": r[10],
            "budget": r[11], "required_points": r[12] or 30, "ref_tech_code": r[13], "status": r[14],
            "winner_phone": r[15], "work_status": r[16], "created_at": r[17], "lat": r[18] or 25.175, "lng": r[19] or 121.443,
            "bid_count": r[20]
        })
    return jsonify(result)

@app.route("/api/tech/submit_bid", methods=["POST"])
def api_tech_submit_bid():
    d = request.json
    phone = d['tech_phone']
    order_id = int(d['order_id'])
    amount = int(d['bid_amount'])
    req_pts = int(d.get('req_points', 30))
    notes = d.get('notes', '')

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, points FROM technicians WHERE phone=?", (phone,))
    t = c.fetchone()
    if not t or t[1] < req_pts:
        conn.close()
        return jsonify({"success": False, "message": f"點數不足 (需 {req_pts} 點)！"})

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    new_points = t[1] - req_pts
    c.execute("UPDATE technicians SET points=? WHERE phone=?", (new_points, phone))
    c.execute("INSERT INTO bids (order_id, tech_phone, tech_name, bid_amount, notes, created_at) VALUES (?, ?, ?, ?, ?, ?)",
              (order_id, phone, t[0], amount, notes, now_str))
    conn.commit()
    conn.close()

    send_line_push_message(f"⚡ 【師傅暗標報價】\n單號：#{order_id}\n師傅：{t[0]} ({phone})\n報價：NT$ {amount:,}\n扣除：{req_pts} 點 (餘額：{new_points} 點)")
    return jsonify({"success": True, "points": new_points})

@app.route("/api/admin/orders_with_bids")
def api_admin_orders_bids():
    if request.args.get("pwd") != ADMIN_PASSWORD:
        return jsonify([])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, name, phone, district, address, category, area_ping, scope_desc, house_age, description, photo_data, budget, required_points, status, winner_phone, work_status, created_at FROM orders ORDER BY id DESC")
    orders = c.fetchall()
    result = []
    for o in orders:
        c.execute("SELECT id, tech_phone, tech_name, bid_amount, notes, created_at FROM bids WHERE order_id=? ORDER BY id ASC", (o[0],))
        bids = [{"id": b[0], "tech_phone": b[1], "tech_name": b[2], "bid_amount": b[3], "notes": b[4], "created_at": b[5]} for b in c.fetchall()]
        result.append({
            "id": o[0], "name": o[1], "phone": o[2], "district": o[3],
            "address": o[4], "category": o[5], "area_ping": o[6], "scope_desc": o[7], "house_age": o[8], "description": o[9], "photo_data": o[10],
            "budget": o[11], "required_points": o[12], "status": o[13],
            "winner_phone": o[14], "work_status": o[15],
            "created_at": o[16], "bids": bids
        })
    conn.close()
    return jsonify(result)

@app.route("/api/admin/select_winner", methods=["POST"])
def api_admin_select_winner():
    d = request.json
    if d.get("pwd") != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "無權限"})
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    final_price = int(d.get('final_price', 0))
    c.execute("UPDATE orders SET winner_phone=?, final_price=?, status='closed', work_status='現勘確認中' WHERE id=?", (d['winner_phone'], final_price, d['order_id']))
    conn.commit()
    conn.close()
    send_line_push_message(f"🏆 【工單已選定得標】\n單號：#{d['order_id']}\n得標師傅：{d['winner_phone']}\n結案實價金額：NT$ {final_price:,}")
    return jsonify({"success": True})

@app.route("/api/tech/register", methods=["POST"])
def api_tech_register():
    d = request.json
    phone = d['phone'].strip()
    ref_by = d.get('referral_by', '').strip()
    ref_code = "QT" + phone[-4:] + str(int(datetime.now().timestamp()))[-3:]

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        c.execute('''
            INSERT INTO technicians (phone, password, name, skills, license_name, experience_years, referral_code, referred_by, ref_count, points, is_verified, is_pro_member, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 100, 1, 0, 'approved', ?)
        ''', (phone, d['password'], d['name'], d.get('skills', ''), d.get('license_name', ''), int(d.get('experience_years', 5)), ref_code, ref_by, now_str))
        
        if ref_by:
            c.execute("SELECT name, phone, ref_count, points FROM technicians WHERE referral_code=?", (ref_by,))
            inviter = c.fetchone()
            if inviter:
                new_ref_count = inviter[2] + 1
                bonus = 300
                if new_ref_count % 5 == 0:
                    bonus += 1000
                c.execute("UPDATE technicians SET points = points + ?, ref_count = ? WHERE referral_code=?", (bonus, new_ref_count, ref_by))
                send_line_push_message(f"🎁 【推薦師傅獎勵】\n推薦人：{inviter[0]} ({inviter[1]})\n新入駐：{d['name']}\n獲得獎勵：+{bonus} 點")

        conn.commit()
        conn.close()
        send_line_push_message(f"🛡️ 【新師傅入駐認證】\n姓名：{d['name']}\n手機：{phone}\n證照：{d.get('license_name', '實名核驗')}")
        return jsonify({"success": True, "tech": {"phone": phone, "name": d['name'], "skills": d.get('skills', ''), "license_name": d.get('license_name', ''), "experience_years": d.get('experience_years', 5), "referral_code": ref_code, "exclusive_districts": "", "points": 100, "status": "approved"}})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "message": "此手機號碼已經註冊過！"})

@app.route("/api/tech/login", methods=["POST"])
def api_tech_login():
    d = request.json
    phone = d['phone'].strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT phone, name, skills, referral_code, exclusive_districts, ref_count, points, license_name, experience_years, status FROM technicians WHERE phone=? AND password=?", (phone, d['password']))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({"success": True, "tech": {"phone": row[0], "name": row[1], "skills": row[2], "referral_code": row[3], "exclusive_districts": row[4], "ref_count": row[5], "points": row[6], "license_name": row[7], "experience_years": row[8], "status": row[9]}})
    return jsonify({"success": False, "message": "帳號或密碼錯誤！"})

@app.route("/api/tech/info")
def api_tech_info():
    phone = request.args.get("phone", "").strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT phone, name, skills, referral_code, exclusive_districts, ref_count, points, license_name, experience_years, status FROM technicians WHERE phone=?", (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({"success": True, "tech": {"phone": row[0], "name": row[1], "skills": row[2], "referral_code": row[3], "exclusive_districts": row[4], "ref_count": row[5], "points": row[6], "license_name": row[7], "experience_years": row[8], "status": row[9]}})
    return jsonify({"success": False})

@app.route("/api/tech/join_district", methods=["POST"])
def api_tech_join_district():
    d = request.json
    phone = d['phone']
    district = d['district']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT points, exclusive_districts, name FROM technicians WHERE phone=?", (phone,))
    t = c.fetchone()
    if not t or t[0] < 3000:
        conn.close()
        return jsonify({"success": False, "message": "持有點數需達 3,000 點以上方可卡位！"})
    curr = t[1] if t[1] else ""
    dists = [x.strip() for x in curr.split(',') if x.strip()]
    if district not in dists:
        dists.append(district)
    new_dists = ",".join(dists)
    c.execute("UPDATE technicians SET exclusive_districts=? WHERE phone=?", (new_dists, phone))
    conn.commit()
    conn.close()
    send_line_push_message(f"👑 【師傅社區卡位成功】\n師傅：{t[2]} ({phone})\n卡位區域：{district}")
    return jsonify({"success": True})

@app.route("/api/tech/update_order_work", methods=["POST"])
def api_tech_update_work():
    d = request.json
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE orders SET work_status=? WHERE id=?", (d['work_status'], d['order_id']))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

@app.route("/api/tech/upload_order_photo", methods=["POST"])
def api_tech_upload_photo():
    d = request.json
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE orders SET photo_data=? WHERE id=?", (d['photo_data'], d['order_id']))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ==========================================
# 5. 綠界金流
# ==========================================
@app.route("/api/ecpay/create_payment", methods=["POST"])
def api_ecpay_create():
    d = request.json
    phone = d['phone']
    amount = int(d['amount'])
    points = int(d['points'])

    trade_no = f"QT{datetime.now().strftime('%Y%m%d%H%M%S')}{int(datetime.now().microsecond/1000):03d}"
    trade_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO ecpay_orders VALUES (?, ?, ?, ?, 'unpaid', ?)", (trade_no, phone, amount, points, trade_date))
    conn.commit()
    conn.close()

    params = {
        "MerchantID": ECPAY_MERCHANT_ID,
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(amount),
        "TradeDesc": "QT30_Points_Topup",
        "ItemName": f"QT30_Points_{points}pts",
        "ReturnURL": f"{BASE_URL}/api/ecpay/callback",
        "ClientBackURL": f"{BASE_URL}/tech?MerchantTradeNo={trade_no}",
        "OrderResultURL": f"{BASE_URL}/tech?MerchantTradeNo={trade_no}",
        "ChoosePayment": "Credit",
        "EncryptType": "1",
        "NeedExtraPaidInfo": "Y",
        "DeviceSource": "M"
    }
    params["CheckMacValue"] = generate_check_mac_value(params, ECPAY_HASH_KEY, ECPAY_HASH_IV)
    return jsonify({"success": True, "ecpay_url": ECPAY_API_URL, "params": params})

@app.route("/api/ecpay/callback", methods=["POST"])
def api_ecpay_callback():
    data = request.form.to_dict()
    trade_no = data.get("MerchantTradeNo", "")
    rtn_code = data.get("RtnCode", "")
    if rtn_code == "1" and trade_no:
        process_paid_order(trade_no, data.get("TradeAmt", ""))
    return "1|OK"

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
