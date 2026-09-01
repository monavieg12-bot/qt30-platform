import os
import time
import urllib.parse
import hashlib
import json
import sqlite3
import requests
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

app = FastAPI(title="QT30 房屋修繕派工接單平台 (一鍵複製變現完整版)")

# CORS 設定
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin888")
DB_PATH = "qt30_database.db"

# LINE 金鑰設定
LINE_CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "Fqylo2CR5nbZX27rp8sg5F7l7Ik4UrvVTPEAxN9l+gpNd2C7V2LBY6NIEakUsBXvZGJ2yq/bzpv0lXLsMrv2C5c6rrG926TAkHnSZkIEZIS1uywU6XJ4waIONGyQxEVq8ff75muOQ4S9wF1mztzz8QdB04t89/1O/w1cDnyilFU=")
LINE_USER_ID = os.getenv("LINE_USER_ID", "Ub577d92184d6d37ec1b262a1bb72897b")

# 綠界測試/正式環境金鑰
ECPAY_MERCHANT_ID = os.getenv("ECPAY_MERCHANT_ID", "3002607") # 預設綠界測試商戶號
ECPAY_HASH_KEY = os.getenv("ECPAY_HASH_KEY", "pwFHCqoQZGmho4w6")
ECPAY_HASH_IV = os.getenv("ECPAY_HASH_IV", "EkRm7iFT261dpevs")
ECPAY_PAYMENT_URL = os.getenv("ECPAY_PAYMENT_URL", "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5")
BASE_URL = os.getenv("BASE_URL", "https://qt30home.com")

# --- 資料庫初始化 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    # 案件資料表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cases (
        id TEXT PRIMARY KEY,
        trade_no TEXT UNIQUE,
        client_name TEXT,
        client_phone TEXT,
        address TEXT,
        item TEXT,
        description TEXT,
        deposit_amount INTEGER DEFAULT 0,
        photo TEXT,
        status TEXT DEFAULT 'pending', -- 'pending', 'unlocked', 'completed', 'cancelled'
        technician TEXT,
        payment_status TEXT DEFAULT 'unpaid',
        unlocked_by TEXT DEFAULT '', -- 逗號分隔儲存已付費解鎖的師傅電話
        available_slots TEXT, -- JSON 儲存消費者可配合之現勘時段
        is_bidding INTEGER DEFAULT 0, -- 0: 專屬單, 1: 公開競價
        created_at TEXT
    )
    """)
    # 師傅卡位服務區表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS spots (
        id TEXT PRIMARY KEY,
        name TEXT,
        lat REAL,
        lng REAL,
        radius_km INTEGER DEFAULT 5,
        technician_phone TEXT,
        technician_name TEXT
    )
    """)
    # 師傅資料表 (密碼、專長、點數、實名認證狀態)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS technicians (
        phone TEXT PRIMARY KEY,
        password TEXT,
        name TEXT,
        id_card_no TEXT,
        id_card_photo TEXT,
        license_photo TEXT,
        skill TEXT,
        points INTEGER DEFAULT 100,
        verified_status TEXT DEFAULT '已通過', -- '待審核', '已通過', '已拒絕'
        created_at TEXT
    )
    """)
    # 點數儲值訂單紀錄表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS topup_orders (
        trade_no TEXT PRIMARY KEY,
        phone TEXT,
        amount INTEGER,
        points INTEGER,
        status TEXT DEFAULT 'unpaid',
        created_at TEXT
    )
    """)
    
    # 動態補足欄位 (防舊資料庫升級出錯)
    try:
        cursor.execute("ALTER TABLE cases ADD COLUMN available_slots TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE cases ADD COLUMN is_bidding INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    # 初始化預設示傅帳號 (0912345678 / 123456 / 已通過 / 2000點)
    cursor.execute("""
    INSERT OR IGNORE INTO technicians (phone, password, name, id_card_no, id_card_photo, license_photo, skill, points, verified_status, created_at)
    VALUES ('0912345678', '123456', '王師傅 (北部統包水電)', 'A123456789', '', '', '老屋翻修,水電維修', 2000, '已通過', '2026-09-01 00:00:00')
    """)
    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# --- LINE 推播通知 ---
def send_line_notification(message_text: str):
    if not LINE_CHANNEL_ACCESS_TOKEN or not LINE_USER_ID:
        return
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": LINE_USER_ID,
        "messages": [{"type": "text", "text": message_text}]
    }
    try:
        requests.post(url, json=payload, headers=headers, timeout=5)
    except Exception as e:
        print(f"LINE 推播發送失敗: {e}")

# --- 綠界 CheckMacValue 演算 ---
def ecpay_url_encode(s: str) -> str:
    encoded = urllib.parse.quote_plus(s)
    replacements = {
        '%2D': '-', '%5F': '_', '%2E': '.', '%21': '!', '%2A': '*', '%28': '(', '%29': ')', '%20': '+'
    }
    for old, new in replacements.items():
        encoded = encoded.replace(old, new)
    return encoded

def generate_check_mac_value(params: dict, hash_key: str, hash_iv: str) -> str:
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    raw_str = f"HashKey={hash_key}&" + "&".join([f"{k}={v}" for k, v in sorted_params]) + f"&HashIV={hash_iv}"
    encoded_str = ecpay_url_encode(raw_str).lower()
    return hashlib.sha256(encoded_str.encode('utf-8')).hexdigest().upper()

# --- Pydantic 模型 ---
class CaseCreate(BaseModel):
    clientName: str
    clientPhone: str
    address: str
    item: str # 選擇工種：水電維修、油漆粉刷、防水抓漏、浴室修繕、專業搬家、冷氣空調、拆除清運、老屋翻修
    description: str
    budget: int = 0
    availableSlots: str # 預估現勘時間 (JSON string 或文字串)
    isBidding: int = 0 # 0: 專屬媒合, 1: 公開競價
    photo: Optional[str] = None

class TechRegister(BaseModel):
    phone: str
    password: str
    name: str
    idCardNo: str
    skill: str = "水電維修"

class TechLogin(BaseModel):
    phone: str
    password: str

class TopupCreate(BaseModel):
    phone: str
    amount: int

class AdminVerifyTech(BaseModel):
    phone: str
    status: str # '已通過', '已拒絕'

class AdminUpdateCase(BaseModel):
    status: str # 'pending', 'unlocked', 'completed', 'cancelled' (cancelled 會觸發全額自動退款點數!)

# --- OpenAI AI極簡工種路由器 (輔助分類，若無金鑰則備用分類) ---
def ai_router_classify(description: str, user_selected: str) -> str:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    if not OPENAI_API_KEY:
        # 無金鑰則直接回傳使用者手動勾選的工種
        return user_selected
    
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json"
    }
    prompt = f"""
    你是一個台灣房屋修繕派工系統的智慧分類路由器。
    請根據消費者的口語化故障描述，從以下 8 大標準工種標籤中，選出最貼近的 1~2 個標籤（以半形逗號隔開，不要有空格，不要任何多餘敘述，僅輸出標籤文字）：
    標準標籤庫：['水電維修', '油漆粉刷', '防水抓漏', '浴室修繕', '專業搬家', '冷氣空調', '拆除清運', '老屋翻修']
    
    描述內容："{description}"
    """
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=5)
        if res.status_code == 200:
            result = res.json()["choices"][0]["message"]["content"].strip()
            valid_tags = ['水電維修', '油漆粉刷', '防水抓漏', '浴室修繕', '專業搬家', '冷氣空調', '拆除清運', '老屋翻修']
            matched_tags = [t.strip() for t in result.split(",") if t.strip() in valid_tags]
            if matched_tags:
                return ",".join(matched_tags)
    except Exception as e:
        print(f"AI 分類呼叫失敗，將採用使用者手選類別: {e}")
    return user_selected

# --- 解鎖點數扣點與退點核心邏輯 ---
def get_unlock_cost(category: str, budget: int) -> int:
    if "老屋翻修" in category or budget >= 100000:
        return 2000
    elif budget >= 15000:
        return 500
    else:
        return 50

# --- 1. 全新 SEO 優化精美首頁 ---
@app.get("/", response_class=HTMLResponse)
def serve_home_seo():
    return f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QT30 房屋修繕派工平台｜雙北居家裝修・水電抓漏工程・8%透明監工</title>
        <meta name="description" content="QT30為大台北及淡水區提供最透明專業的居家修繕服務。包含水電、油漆、防水、浴室修繕、搬家、冷氣、拆除及老屋翻修統包。一鍵AI工種診斷、秒級媒合實名認證優質工班，拒絕惡性低價搶單，用工藝打造溫馨住家！">
        <meta name="keywords" content="淡水修繕推薦, 雙北室內裝潢, 水電維修, 抓漏防水, 浴室修繕, 老屋翻新統包, LINE Pay儲值, 裝修估價, QT30">
        <link rel="canonical" href="https://qt30home.com/" />
        
        <!-- Open Graph Meta Tags -->
        <meta property="og:title" content="QT30 房屋修繕派工平台｜雙北居家裝修與老屋翻新統包" />
        <meta property="og:description" content="AI 智慧派工導診，極速匹配雙北及淡水最靠譜的實名制裝修與修繕師傅，完全免費發案，首創預約現勘無鬼單保障！" />
        <meta property="og:type" content="website" />
        <meta property="og:url" content="https://qt30home.com/" />
        <meta property="og:image" content="https://images.unsplash.com/photo-1581094288338-2314dddb7eed?auto=format&fit=crop&w=1200&h=630&q=80" />
        <meta property="og:locale" content="zh_TW" />
        
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-50 text-slate-800 font-sans leading-normal tracking-normal">
        <nav class="bg-white shadow-md sticky top-0 z-50">
            <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                <div class="flex justify-between h-16">
                    <div class="flex items-center">
                        <span class="text-2xl font-black text-blue-600 tracking-wider">QT30 <span class="text-slate-800 text-lg font-bold">Home</span></span>
                    </div>
                    <div class="flex items-center space-x-4">
                        <a href="/app" class="bg-blue-600 hover:bg-blue-700 text-white font-bold px-4 py-2 rounded-xl transition shadow text-sm">🙋 我要報修發案</a>
                        <a href="/tech" class="border border-blue-600 text-blue-600 hover:bg-blue-50 font-bold px-4 py-2 rounded-xl transition text-sm">🛠️ 師傅接單大廳</a>
                    </div>
                </div>
            </div>
        </nav>

        <section class="relative bg-slate-900 text-white py-24 px-4 overflow-hidden">
            <div class="absolute inset-0 opacity-20 bg-cover bg-center" style="background-image: url('https://images.unsplash.com/photo-1504307651254-35680f356dfd?auto=format&fit=crop&w=1920&q=80');"></div>
            <div class="relative max-w-5xl mx-auto text-center space-y-6">
                <span class="bg-blue-500/20 text-blue-300 font-bold text-xs uppercase tracking-widest px-4 py-1.5 rounded-full border border-blue-500/30">台灣首創・雙向確認現勘平台</span>
                <h1 class="text-4xl sm:text-5xl md:text-6xl font-black tracking-tight leading-tight">
                    告別裝修焦慮！<br><span class="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-emerald-400">一鍵AI精準診斷 媒合王牌工班</span>
                </h1>
                <p class="text-lg sm:text-xl text-slate-300 max-w-2xl mx-auto">
                    不管是老屋翻新統包，還是局部水電、抓漏、油漆、搬家、冷氣拆除，AI 幫您自動分析所需工種，不再找錯師傅！
                </p>
                <div class="flex flex-col sm:flex-row justify-center items-center gap-4 pt-4">
                    <a href="/app" class="w-full sm:w-auto bg-gradient-to-r from-blue-500 to-blue-600 hover:from-blue-600 hover:to-blue-700 text-white text-lg font-black px-8 py-4 rounded-2xl shadow-xl transition transform hover:-translate-y-0.5">
                        立即免費發案估價 ➔
                    </a>
                    <a href="/tech" class="w-full sm:w-auto bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-lg font-bold px-8 py-4 rounded-2xl transition">
                        我是專業師傅，我要入駐接單
                    </a>
                </div>
            </div>
        </section>

        <!-- 八大 MVP 服務項目 -->
        <section class="max-w-7xl mx-auto py-20 px-4">
            <div class="text-center space-y-3 mb-16">
                <h2 class="text-3xl font-black text-slate-900">全面覆蓋 8 大核心修繕服務</h2>
                <p class="text-slate-500 max-w-xl mx-auto">從急迫的小水電，到百萬預算的老屋整建，我們都幫您精準分流路由器。</p>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-6">
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 hover:shadow-md transition text-center space-y-3">
                    <div class="w-12 h-12 bg-blue-100 text-blue-600 rounded-xl flex items-center justify-center mx-auto text-xl font-bold">⚡</div>
                    <h3 class="font-bold text-slate-800">水電維修</h3>
                    <p class="text-xs text-slate-400">暗管漏水檢修、電箱重整、開關燈具面板更換</p>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 hover:shadow-md transition text-center space-y-3">
                    <div class="w-12 h-12 bg-emerald-100 text-emerald-600 rounded-xl flex items-center justify-center mx-auto text-xl font-bold">🎨</div>
                    <h3 class="font-bold text-slate-800">油漆粉刷</h3>
                    <p class="text-xs text-slate-400">壁癌處理、細緻補土打磨、兩底三度無痕噴漆</p>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 hover:shadow-md transition text-center space-y-3">
                    <div class="w-12 h-12 bg-indigo-100 text-indigo-600 rounded-xl flex items-center justify-center mx-auto text-xl font-bold">💧</div>
                    <h3 class="font-bold text-slate-800">防水抓漏</h3>
                    <p class="text-xs text-slate-400">屋頂外牆PU防水、高壓灌注打針、窗框隙縫阻水</p>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 hover:shadow-md transition text-center space-y-3">
                    <div class="w-12 h-12 bg-amber-100 text-amber-600 rounded-xl flex items-center justify-center mx-auto text-xl font-bold">🛀</div>
                    <h3 class="font-bold text-slate-800">浴室修繕</h3>
                    <p class="text-xs text-slate-400">免敲打防水底盤安裝、乾濕分離規劃、全套衛浴翻新</p>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 hover:shadow-md transition text-center space-y-3">
                    <div class="w-12 h-12 bg-orange-100 text-orange-600 rounded-xl flex items-center justify-center mx-auto text-xl font-bold">🚚</div>
                    <h3 class="font-bold text-slate-800">專業搬家</h3>
                    <p class="text-xs text-slate-400">精緻防撞打包、重物防護搬運、家具定位與還原</p>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 hover:shadow-md transition text-center space-y-3">
                    <div class="w-12 h-12 bg-sky-100 text-sky-600 rounded-xl flex items-center justify-center mx-auto text-xl font-bold">❄️</div>
                    <h3 class="font-bold text-slate-800">冷氣空調</h3>
                    <p class="text-xs text-slate-400">高壓深層清洗機、漏冷媒檢修、全冷排銅管抽真空安裝</p>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-100 hover:shadow-md transition text-center space-y-3">
                    <div class="w-12 h-12 bg-red-100 text-red-600 rounded-xl flex items-center justify-center mx-auto text-xl font-bold">🔨</div>
                    <h3 class="font-bold text-red-600">拆除清運</h3>
                    <p class="text-xs text-slate-400">裝潢隔間打石拆除、廢棄物合法堆疊與卡車清運</p>
                </div>
                <div class="bg-white p-6 rounded-2xl border-2 border-blue-500 shadow-md hover:shadow-lg transition text-center space-y-3">
                    <div class="w-12 h-12 bg-gradient-to-br from-blue-500 to-emerald-500 text-white rounded-xl flex items-center justify-center mx-auto text-xl font-bold">🏗️</div>
                    <h3 class="font-bold text-blue-600">老屋翻修統包</h3>
                    <p class="text-xs text-slate-500 font-medium">全屋大拉皮、水電管線全重拉、一條龍統包安心工程</p>
                </div>
            </div>
        </section>

        <!-- 平台優勢三大亮點 -->
        <section class="bg-blue-600 text-white py-20 px-4">
            <div class="max-w-6xl mx-auto grid grid-cols-1 md:grid-cols-3 gap-12 text-center">
                <div class="space-y-4">
                    <div class="w-16 h-16 bg-white/10 rounded-2xl flex items-center justify-center mx-auto text-2xl">⚡</div>
                    <h3 class="text-2xl font-black">AI 極簡工種翻譯</h3>
                    <p class="text-blue-100 text-sm">消費者只要說出「我家大門關不起來，鑰匙卡住」，AI自動分類指派「室內裝修/鎖具專家」，精準到頻不亂派單。</p>
                </div>
                <div class="space-y-4">
                    <div class="w-16 h-16 bg-white/10 rounded-2xl flex items-center justify-center mx-auto text-2xl">💰</div>
                    <h3 class="text-2xl font-black">真實現勘・退點保障</h3>
                    <p class="text-blue-100 text-sm">不同於傳統平台的幽靈死單，師傅在我們平台解鎖的名單，都是業主自選、親筆填好現勘預約時間，若業主取消，點數一秒自動全額退回！</p>
                </div>
                <div class="space-y-4">
                    <div class="w-16 h-16 bg-white/10 rounded-2xl flex items-center justify-center mx-auto text-2xl">🛡️</div>
                    <h3 class="text-2xl font-black">8% 透明監工(自選)</h3>
                    <p class="text-blue-100 text-sm">消費者完全免費發案，若需要專人打理繁瑣老屋翻新現場，可自選平台提供的 8% 安心代管監工，進度天天掌握。</p>
                </div>
            </div>
        </section>

        <footer class="bg-slate-900 text-slate-400 py-12 px-4 text-center border-t border-slate-800">
            <div class="max-w-7xl mx-auto space-y-4">
                <div class="text-white font-black text-xl tracking-wider">QT30 HOME</div>
                <p class="text-sm">新北市淡水區中正路一段100號4樓 ｜ 合作諮詢：service@qt30home.com</p>
                <p class="text-xs text-slate-500">© 2026 QT30 房屋修繕派工平台 版權所有。本平台符合中華民國消保及信託託管法規規定。</p>
            </div>
        </footer>
    </body>
    </html>
    """

# --- 2. 客戶預約發案頁面 (/app) ---
@app.get("/app", response_class=HTMLResponse)
def serve_app_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QT30 房屋修繕預約 - 免費發案</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
        <div class="max-w-md mx-auto bg-white rounded-2xl shadow-xl overflow-hidden">
            <div class="bg-blue-600 p-6 text-white text-center">
                <h1 class="text-2xl font-bold">🙋 QT30 房屋修繕發案</h1>
                <p class="text-blue-100 text-sm mt-1">填單立即自動診斷所需工種，安排師傅現勘</p>
            </div>
            <form id="orderForm" class="p-6 space-y-4" onsubmit="submitForm(event)">
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-bold text-gray-700">聯絡姓名</label>
                        <input type="text" id="clientName" required placeholder="例如：王先生" class="w-full mt-1 p-2.5 border rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-gray-700">聯絡電話</label>
                        <input type="tel" id="clientPhone" required placeholder="例如：0912345678" class="w-full mt-1 p-2.5 border rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none">
                    </div>
                </div>

                <div>
                    <label class="block text-xs font-bold text-gray-700">詳細修繕地址</label>
                    <input type="text" id="address" required placeholder="例如：台北市信義區中正路 100 號 3 樓" class="w-full mt-1 p-2.5 border rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none">
                </div>

                <div>
                    <label class="block text-xs font-bold text-gray-700">希望修繕項目</label>
                    <select id="item" class="w-full mt-1 p-2.5 border rounded-xl bg-white focus:ring-2 focus:ring-blue-500 focus:outline-none font-medium">
                        <option value="水電維修">水電維修 / 衛浴安裝更換</option>
                        <option value="油漆粉刷">油漆粉刷 / 壁癌處理</option>
                        <option value="防水抓漏">泥工泥作 / 屋頂外牆抓漏防水</option>
                        <option value="浴室修繕">整體浴室翻新 / 卜大整體衛浴</option>
                        <option value="專業搬家">精緻搬家 / 貨車與物資清運</option>
                        <option value="冷氣空調">冷氣高壓清洗 / 檢修安裝</option>
                        <option value="拆除清運">裝潢隔間牆拆除 / 打石廢棄物清運</option>
                        <option value="老屋翻修">🏗️ 老屋翻修 (居家裝潢統包)</option>
                    </select>
                </div>

                <div>
                    <label class="block text-xs font-bold text-gray-700">情況詳細說明（AI 智慧工種診斷路由器將進行比對）</label>
                    <textarea id="description" rows="3" required placeholder="請簡單說明故障狀況，例如：我家浴室牆壁在滲水，馬桶也有點不通..." class="w-full mt-1 p-2.5 border rounded-xl focus:ring-2 focus:ring-blue-500 focus:outline-none"></textarea>
                </div>

                <div>
                    <label class="block text-xs font-bold text-gray-700">預估工程預算 (新台幣，老屋翻修統包估 10萬~百萬起)</label>
                    <input type="number" id="budget" required placeholder="例如：15000" class="w-full mt-1 p-2.5 border rounded-xl font-bold text-amber-600 focus:ring-2 focus:ring-amber-500 focus:outline-none">
                </div>

                <div class="p-4 bg-slate-50 rounded-xl border border-slate-200 space-y-3">
                    <span class="text-xs font-bold text-blue-700 block">📅 現勘預約安排（必填）</span>
                    <div>
                        <label class="block text-[11px] font-semibold text-gray-500">方便在家等候現勘的時段（請提供2-3個，如：週六下午、平日晚上）</label>
                        <input type="text" id="availableSlots" required placeholder="例如：下週六下午、下週日下午14:00" class="w-full mt-1 p-2.5 border rounded-xl bg-white text-sm">
                    </div>
                    <div class="flex items-center space-x-2 pt-1">
                        <input type="checkbox" id="isBidding" value="1" class="w-4 h-4 text-blue-600 border-slate-300 rounded focus:ring-blue-500">
                        <label for="isBidding" class="text-xs font-bold text-gray-700 cursor-pointer">
                            開放多家公開比價（不限比價廠商，獲取更多報價）
                        </label>
                    </div>
                </div>

                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3.5 rounded-xl transition shadow shadow-blue-500/20">
                    🚀 立即提交發案 (免費)
                </button>
            </form>
        </div>

        <script>
            async function submitForm(e) {
                e.preventDefault();
                const payload = {
                    clientName: document.getElementById('clientName').value,
                    clientPhone: document.getElementById('clientPhone').value,
                    address: document.getElementById('address').value,
                    item: document.getElementById('item').value,
                    description: document.getElementById('description').value,
                    budget: parseInt(document.getElementById('budget').value) || 0,
                    availableSlots: document.getElementById('availableSlots').value,
                    isBidding: document.getElementById('isBidding').checked ? 1 : 0
                };

                try {
                    const res = await fetch('/api/cases', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(payload)
                    });
                    const data = await res.json();
                    if (data.success) {
                        alert(`發案成功！已為您指派 AI 分流路由器。案件編號: ${data.case_id}`);
                        document.getElementById('orderForm').reset();
                    } else {
                        alert(`發案失敗: ${data.message}`);
                    }
                } catch (err) {
                    alert('網路異常，請稍後再試！');
                }
            }
        </script>
    </body>
    </html>
    """

# --- 3. 師傅端工作台頁面 (/tech) ---
@app.get("/tech", response_class=HTMLResponse)
def serve_tech_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QT30 師傅接單與點數儲值工作台</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen">
        
        <!-- 頂部導航 -->
        <header class="bg-slate-900 border-b border-slate-800 p-4 px-6 sticky top-0 z-40">
            <div class="max-w-6xl mx-auto flex flex-wrap justify-between items-center gap-4">
                <div>
                    <div class="text-[11px] text-blue-400 font-bold uppercase tracking-wider">QT30 專業師傅平台</div>
                    <div class="flex items-center space-x-2 mt-0.5">
                        <span id="headerPhone" class="font-mono text-xs bg-slate-800 text-slate-300 px-2 py-0.5 rounded border border-slate-700">0912345678</span>
                        <span id="headerName" class="font-bold text-base text-white">王師傅 (北部統包水電)</span>
                        <span class="text-[11px] font-bold px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">✓ 實名認證</span>
                    </div>
                </div>
                <div class="flex items-center space-x-3">
                    <div class="text-right">
                        <span class="text-[11px] text-slate-400">點數餘額</span>
                        <div class="font-black text-amber-400 text-lg leading-none mt-0.5">
                            <span id="pointsBalance">--</span> <span class="text-xs font-normal text-amber-200/80">點</span>
                        </div>
                    </div>
                    <button onclick="switchTab('topup')" class="bg-gradient-to-r from-emerald-500 to-emerald-600 hover:from-emerald-600 text-slate-950 font-black text-xs px-3.5 py-2 rounded-xl shadow transition">
                        💳 LINE Pay / 信用卡儲值
                    </button>
                </div>
            </div>
        </header>

        <!-- 主體內容 -->
        <main class="max-w-6xl mx-auto p-4 sm:p-6 space-y-6">
            
            <!-- 頁籤切換 -->
            <div class="flex space-x-2 border-b border-slate-800 pb-3">
                <button onclick="switchTab('cases')" id="btn-cases" class="tab-btn px-4 py-2 rounded-lg font-bold text-sm bg-blue-600 text-white">
                    📋 待搶修繕工單大廳
                </button>
                <button onclick="switchTab('topup')" id="btn-topup" class="tab-btn px-4 py-2 rounded-lg font-bold text-sm bg-slate-900 text-slate-400 hover:bg-slate-800">
                    💳 購買點數儲值包
                </button>
            </div>

            <!-- 1. 工單大廳 -->
            <section id="section-cases" class="space-y-4">
                <div class="flex justify-between items-center">
                    <h2 class="text-lg font-bold">即時工單清單 (AI已完成標籤分流)</h2>
                    <button onclick="loadCases()" class="text-xs bg-slate-800 px-3 py-1.5 rounded-lg border border-slate-700 hover:bg-slate-700">🔄 整理清單</button>
                </div>
                <div id="casesList" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <!-- 載入中 -->
                    <p class="text-slate-500 text-sm">正在載入案源大廳...</p>
                </div>
            </section>

            <!-- 2. 儲值頁面 -->
            <section id="section-topup" class="hidden max-w-lg mx-auto bg-slate-900 p-6 rounded-2xl border border-slate-800 space-y-6">
                <div>
                    <h3 class="text-xl font-bold text-amber-400">💎 師傅在線即時儲值</h3>
                    <p class="text-xs text-slate-400 mt-1">對接綠界科技金流，支援信用卡及 LINE Pay（秒級自動入帳，隨充隨用）</p>
                </div>
                <div class="grid grid-cols-3 gap-3">
                    <button onclick="setAmount(500)" class="topup-pack-btn border border-slate-700 p-4 rounded-xl text-center hover:border-amber-500 transition">
                        <div class="text-sm font-bold text-slate-300">基礎包</div>
                        <div class="text-lg font-black text-amber-400 mt-1">500 點</div>
                        <div class="text-xs text-slate-500 mt-0.5">NT$ 500</div>
                    </button>
                    <button onclick="setAmount(1000)" class="topup-pack-btn border border-slate-700 p-4 rounded-xl text-center hover:border-amber-500 transition">
                        <div class="text-sm font-bold text-slate-300">優惠包</div>
                        <div class="text-lg font-black text-amber-400 mt-1">1100 點</div>
                        <div class="text-xs text-slate-500 mt-0.5">NT$ 1000</div>
                    </button>
                    <button onclick="setAmount(3000)" class="topup-pack-btn border-2 border-emerald-500 p-4 rounded-xl text-center bg-emerald-950/20 hover:border-amber-500 transition">
                        <div class="text-sm font-bold text-emerald-400">區域卡位包</div>
                        <div class="text-lg font-black text-amber-400 mt-1">3500 點</div>
                        <div class="text-xs text-slate-500 mt-0.5">NT$ 3000</div>
                    </button>
                </div>
                <div class="space-y-2">
                    <label class="block text-xs font-bold text-slate-400">自訂儲值金額 (新台幣 NT$)</label>
                    <input type="number" id="topupAmount" value="1000" class="w-full bg-slate-950 p-3 rounded-xl border border-slate-800 text-amber-400 font-bold text-lg focus:outline-none focus:border-amber-500">
                </div>
                <button onclick="requestTopup()" class="w-full bg-gradient-to-r from-emerald-500 to-teal-600 hover:from-emerald-600 text-slate-950 font-black py-3.5 rounded-xl transition shadow shadow-emerald-500/10">
                    💳 前往綠界收銀台 (可用 LINE Pay 及信用卡)
                </button>
            </section>

        </main>

        <script>
            const PHONE = '0912345678'; // 預設師傅號碼
            let selectedAmount = 1000;

            function switchTab(tab) {
                document.querySelectorAll('.tab-btn').forEach(b => {
                    b.classList.remove('bg-blue-600', 'text-white');
                    b.classList.add('bg-slate-900', 'text-slate-400');
                });
                document.getElementById(`btn-${tab}`).classList.add('bg-blue-600', 'text-white');
                document.getElementById(`btn-${tab}`).classList.remove('bg-slate-900', 'text-slate-400');
                
                if(tab === 'cases') {
                    document.getElementById('section-cases').classList.remove('hidden');
                    document.getElementById('section-topup').classList.add('hidden');
                } else {
                    document.getElementById('section-cases').classList.add('hidden');
                    document.getElementById('section-topup').classList.remove('hidden');
                }
            }

            function setAmount(amt) {
                document.getElementById('topupAmount').value = amt;
            }

            async function loadProfile() {
                try {
                    const res = await fetch(`/api/tech/profile?phone=${PHONE}`);
                    const data = await res.json();
                    if (data.success) {
                        document.getElementById('pointsBalance').innerText = data.tech.points;
                    }
                } catch(e) {}
            }

            async function loadCases() {
                try {
                    const res = await fetch(`/api/cases?phone=${PHONE}`);
                    const data = await res.json();
                    const container = document.getElementById('casesList');
                    container.innerHTML = '';
                    
                    if (data.cases.length === 0) {
                        container.innerHTML = '<p class="text-slate-500 text-sm">目前尚無等待現勘的工單案源。</p>';
                        return;
                    }

                    data.cases.forEach(c => {
                        const isUnlocked = c.unlocked;
                        const cost = getUnlockCost(c.item, c.budget);
                        
                        let cardHtml = `
                            <div class="bg-slate-900 p-5 rounded-2xl border ${isUnlocked ? 'border-blue-500' : 'border-slate-800'} space-y-3">
                                <div class="flex justify-between items-start">
                                    <span class="bg-slate-800 text-slate-300 text-xs px-2.5 py-1 rounded font-mono">${c.id}</span>
                                    <span class="text-xs px-2 py-0.5 rounded font-bold ${c.is_bidding === 1 ? 'bg-amber-950 text-amber-400 border border-amber-800' : 'bg-blue-950 text-blue-400 border border-blue-800'}">
                                        ${c.is_bidding === 1 ? '公開競價(不限家數)' : '1對1 專屬指派'}
                                    </span>
                                </div>
                                <h3 class="text-base font-black text-white flex items-center gap-1.5">
                                    🛠️ 需求工種：<span class="text-blue-400">${c.item}</span>
                                </h3>
                                <p class="text-slate-300 text-sm">客戶主訴：${c.description}</p>
                                <div class="text-xs text-slate-400 space-y-1 bg-slate-950 p-3 rounded-lg">
                                    <div>預算：<span class="text-amber-500 font-bold">NT$ ${c.budget}</span></div>
                                    <div>現勘時段：<span class="text-emerald-400 font-medium">${c.available_slots || "未約定"}</span></div>
                                    <div>發案時間：${c.created_at}</div>
                                </div>
                        `;

                        if (isUnlocked) {
                            cardHtml += `
                                <div class="p-3 bg-blue-950/30 rounded-xl border border-blue-900/50 space-y-1.5">
                                    <span class="text-xs font-bold text-blue-400 block">📞 已解鎖聯絡人資訊：</span>
                                    <div class="text-sm font-semibold">客戶姓名：${c.client_name}</div>
                                    <div class="text-sm font-semibold">聯絡電話：<a href="tel:${c.client_phone}" class="text-blue-400 underline">${c.client_phone}</a></div>
                                    <div class="text-sm font-semibold">現勘地址：${c.address}</div>
                                </div>
                            `;
                        } else {
                            cardHtml += `
                                <div class="flex items-center justify-between pt-2">
                                    <div class="text-xs text-slate-400">解鎖需扣除 <span class="text-amber-400 font-black">${cost}</span> 點</div>
                                    <button onclick="unlockCase('${c.id}')" class="bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs px-4  py-2 rounded-lg transition">
                                        ⚡ 扣點直接解鎖現勘資訊
                                    </button>
                                </div>
                            `;
                        }

                        cardHtml += `</div>`;
                        container.innerHTML += cardHtml;
                    });
                } catch(e) {}
            }

            function getUnlockCost(item, budget) {
                if (item.includes("老屋翻修") || budget >= 100000) return 2000;
                if (budget >= 15000) return 500;
                return 50;
            }

            async function unlockCase(id) {
                if (!confirm(`確定扣點解鎖現勘？若消費者之後「取消現勘」，平台承諾100%全額自動退款點數！`)) return;
                try {
                    const res = await fetch(`/api/cases/${id}/unlock?phone=${PHONE}`, { method: 'POST' });
                    const data = await res.json();
                    if (data.success) {
                        alert(data.message);
                        loadProfile();
                        loadCases();
                    } else {
                        alert(`解鎖失敗：${data.message}`);
                    }
                } catch(e) {
                    alert('解鎖過程發生網路錯誤');
                }
            }

            async function requestTopup() {
                const amt = parseInt(document.getElementById('topupAmount').value);
                if (!amt || amt < 100) {
                    alert('儲值金額至少需滿 NT$ 100');
                    return;
                }
                try {
                    const res = await fetch('/api/tech/topup', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ phone: PHONE, amount: amt })
                    });
                    const data = await res.json();
                    if (data.success) {
                        const form = document.createElement('form');
                        form.method = 'POST';
                        form.action = data.payment_url;
                        for (const [key, value] of Object.entries(data.params)) {
                            const input = document.createElement('input');
                            input.type = 'hidden';
                            input.name = key;
                            input.value = value;
                            form.appendChild(input);
                        }
                        document.body.appendChild(form);
                        form.submit();
                    } else {
                        alert(data.message);
                    }
                } catch(e) {
                    alert('建立金流儲值訂單失敗');
                }
            }

            // 初始化載入
            loadProfile();
            loadCases();
        </script>
    </body>
    </html>
    """

# --- 4. 派工與退點管理後台 (/admin) ---
@app.get("/admin", response_class=HTMLResponse)
def serve_admin_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QT30 營運總控制後台</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
        <div class="max-w-7xl mx-auto space-y-6">
            
            <!-- 頂部標題 -->
            <header class="flex flex-col sm:flex-row justify-between items-center bg-white p-6 rounded-2xl shadow-sm gap-4">
                <div>
                    <h1 class="text-2xl font-black text-slate-800">QT30 營運總控制後台</h1>
                    <p class="text-sm text-slate-500 mt-1">即時掌握全站案件、師傅搶單狀態與退點金流（可一鍵取消案件全額退款師傅）</p>
                </div>
                <button onclick="loadAll()" class="bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-5 py-2.5 rounded-xl shadow transition">
                    🔄 重新整理所有數據
                </button>
            </header>

            <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <!-- 左側：師傅名單與點數管理 -->
                <section class="lg:col-span-1 bg-white p-6 rounded-2xl shadow-sm space-y-4">
                    <h2 class="text-lg font-bold border-b border-slate-100 pb-3">👨 合作工班列表</h2>
                    <div id="techsList" class="space-y-3 max-h-[600px] overflow-y-auto">
                        <!-- 動態載入 -->
                    </div>
                </section>

                <!-- 右側：全站工單列表與取消退款管理 -->
                <section class="lg:col-span-2 bg-white p-6 rounded-2xl shadow-sm space-y-4">
                    <h2 class="text-lg font-bold border-b border-slate-100 pb-3">📋 房屋修繕工單控制</h2>
                    <div id="casesList" class="space-y-4 max-h-[600px] overflow-y-auto">
                        <!-- 動態載入 -->
                    </div>
                </section>
            </div>
        </div>

        <script>
            async function loadAll() {
                loadTechs();
                loadCases();
            }

            async function loadTechs() {
                try {
                    const res = await fetch('/api/admin/technicians');
                    const data = await res.json();
                    const container = document.getElementById('techsList');
                    container.innerHTML = '';
                    
                    data.technicians.forEach(t => {
                        container.innerHTML += `
                            <div class="p-4 bg-slate-50 rounded-xl border border-slate-200 text-sm space-y-1">
                                <div class="flex justify-between">
                                    <span class="font-bold text-slate-800">${t.name}</span>
                                    <span class="text-amber-600 font-extrabold font-mono">${t.points} 點</span>
                                </div>
                                <div class="text-xs text-slate-500">電話：${t.phone}</div>
                                <div class="text-xs text-slate-500">專長：${t.skill}</div>
                                <div class="text-xs text-slate-500">狀態：<span class="font-bold text-emerald-600">${t.verified_status}</span></div>
                            </div>
                        `;
                    });
                } catch(e) {}
            }

            async function loadCases() {
                try {
                    const res = await fetch('/api/cases');
                    const data = await res.json();
                    const container = document.getElementById('casesList');
                    container.innerHTML = '';

                    data.cases.forEach(c => {
                        const unlockedList = c.unlocked_by ? c.unlocked_by.split(',') : [];
                        const unlockedCount = unlockedList.filter(x => x).length;

                        container.innerHTML += `
                            <div class="p-5 bg-slate-50 rounded-2xl border border-slate-200 space-y-3 text-sm">
                                <div class="flex justify-between">
                                    <span class="bg-slate-200 px-2 py-0.5 rounded font-mono font-semibold">${c.id}</span>
                                    <span class="font-bold ${c.status === 'cancelled' ? 'text-red-500' : 'text-blue-600'}">
                                        ${c.status === 'cancelled' ? '❌ 已取消 (點數已退還)' : '🟢 進行中/待現勘'}
                                    </span>
                                </div>
                                <div class="font-bold text-slate-800">工種：${c.item} ｜ 預算：NT$ ${c.budget}</div>
                                <div class="text-slate-600">客戶：${c.client_name} (${c.client_phone})</div>
                                <div class="text-slate-600">地址：${c.address}</div>
                                <div class="text-slate-600">現勘方便時段：${c.available_slots || "未指定"}</div>
                                <div class="p-3 bg-white rounded-xl border border-slate-200">
                                    <div class="text-xs font-semibold text-slate-500">已解鎖師傅 (${unlockedCount} 位)：</div>
                                    <div class="text-xs mt-1 text-slate-700">${c.unlocked_by || '暫無師傅解鎖'}</div>
                                </div>
                                <div class="flex justify-end space-x-2">
                                    ${c.status !== 'cancelled' ? `
                                        <button onclick="cancelAndRefund('${c.id}')" class="bg-red-500 hover:bg-red-600 text-white font-bold text-xs px-4 py-2 rounded-lg shadow transition">
                                            ⚠️ 客戶取消現勘（一鍵全額自動退款點數）
                                        </button>
                                    ` : `
                                        <span class="text-xs text-slate-400 font-semibold italic">點數已退還所有解鎖師傅</span>
                                    `}
                                </div>
                            </div>
                        `;
                    });
                } catch(e) {}
            }

            async function cancelAndRefund(caseId) {
                if (!confirm(`確定要取消此案件？系統將會自動計算解鎖成本，並「全額一秒退回」所有已解鎖師傅的點數！`)) return;
                try {
                    const res = await fetch(`/api/cases/${caseId}/cancel`, { method: 'POST' });
                    const data = await res.json();
                    if (data.success) {
                        alert(data.message);
                        loadAll();
                    } else {
                        alert(`操作失敗：${data.message}`);
                    }
                } catch(e) {
                    alert('退款過程發生網路異常');
                }
            }

            // 初始化載入
            loadAll();
        </script>
    </body>
    </html>
    """

# --- 5. 房屋修繕發案與診斷 API ---
@app.post("/api/cases")
def create_case(data: CaseCreate):
    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"QT{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    case_id = f"CASE-{trade_no[-6:]}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 呼叫 AI 自動進行工種分析路由
    final_item = ai_router_classify(data.description, data.item)

    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        INSERT INTO cases (id, trade_no, client_name, client_phone, address, item, description, budget, status, unlocked_by, available_slots, is_bidding, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?, ?)
        """, (case_id, trade_no, data.clientName, data.clientPhone, data.address, final_item, data.description, data.budget, data.availableSlots, data.isBidding, created_at))
        conn.commit()
    except Exception as e:
        conn.close()
        return {"success": False, "message": f"寫入資料庫失敗: {e}"}
    conn.close()

    # LINE 推播通知管理員
    bidding_str = "公開比價(不限家數)" if data.isBidding == 1 else "1對1 專屬指派"
    send_line_notification(f"【QT30 新增派工單】\\n單號: {case_id}\\n客戶: {data.clientName}\\n類別: {final_item}\\n預算: {data.budget}元\\n媒合模式: {bidding_str}\\n現勘方便時間: {data.availableSlots}")
    
    return {"success": True, "case_id": case_id, "item_diagnosed": final_item}

# --- 6. 案件大廳查詢 API (過濾已解鎖資訊) ---
@app.get("/api/cases")
def get_cases(phone: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cases ORDER BY created_at DESC")
    rows = cursor.fetchall()
    
    cases = []
    for r in rows:
        unlocked = False
        unlocked_list = r["unlocked_by"].split(",") if r["unlocked_by"] else []
        if phone and phone in unlocked_list:
            unlocked = True
        
        # 僅當師傅已付費解鎖時，才在 JSON 中揭露隱私電話及地址；否則進行去識別化處理
        cases.append({
            "id": r["id"],
            "item": r["item"],
            "description": r["description"],
            "budget": r["budget"],
            "status": r["status"],
            "created_at": r["created_at"],
            "available_slots": r["available_slots"],
            "is_bidding": r["is_bidding"],
            "unlocked": unlocked,
            "unlocked_by": r["unlocked_by"],
            "client_name": r["client_name"] if unlocked else r["client_name"][0] + "先生/女士",
            "client_phone": r["client_phone"] if unlocked else "解鎖後顯示",
            "address": r["address"] if unlocked else r["address"][:6] + " (解鎖後顯示詳細門牌)"
        })
    conn.close()
    return {"success": True, "cases": cases}

# --- 7. 師傅扣點解鎖案件 (直接解鎖，免雙向確認) ---
@app.post("/api/cases/{case_id}/unlock")
def unlock_case(case_id: str, phone: str = "0912345678"):
    conn = get_db()
    cursor = conn.cursor()
    
    # 讀取案件
    cursor.execute("SELECT * FROM cases WHERE id = ?", (case_id,))
    case = cursor.fetchone()
    if not case:
        conn.close()
        raise HTTPException(status_code=404, detail="找不到此房屋修繕工單")
    
    # 讀取師傅點數
    cursor.execute("SELECT points, name FROM technicians WHERE phone = ?", (phone,))
    tech = cursor.fetchone()
    if not tech:
        conn.close()
        raise HTTPException(status_code=404, detail="找不到師傅帳號")

    # 檢查是否解鎖過
    unlocked_list = case["unlocked_by"].split(",") if case["unlocked_by"] else []
    if phone in unlocked_list:
        conn.close()
        return {"success": True, "message": "您之前已扣點解鎖過此案，可直接瀏覽！"}

    cost = get_unlock_cost(case["item"], case["budget"])
    if tech["points"] < cost:
        conn.close()
        return {"success": False, "message": f"您的點數餘額不足！本次解鎖需 {cost} 點，您目前僅有 {tech['points']} 點，請先使用 LINE Pay 或信用卡儲值！"}

    # 扣點並更新解鎖名單
    new_points = tech["points"] - cost
    unlocked_list.append(phone)
    new_unlocked_by = ",".join([p for p in unlocked_list if p])

    cursor.execute("UPDATE technicians SET points = ? WHERE phone = ?", (new_points, phone))
    cursor.execute("UPDATE cases SET unlocked_by = ?, status = 'unlocked' WHERE id = ?", (new_unlocked_by, case_id))
    conn.commit()
    conn.close()

    # LINE 提示管理員
    send_line_notification(f"【QT30 點數變現扣點】\\n師傅: {tech['name']} ({phone})\\n解鎖單號: {case_id}\\n扣除點數: {cost} 點\\n剩餘點數: {new_points} 點")

    return {"success": True, "message": f"成功扣除 {cost} 點！您已解鎖現勘通聯個資。"}

# --- 8. 消費者/管理員取消現勘 ➔ 「點數1秒自動退回師傅」核心 API ---
@app.post("/api/cases/{case_id}/cancel")
def cancel_and_refund_case(case_id: str):
    conn = get_db()
    cursor = conn.cursor()
    
    # 讀取案件資訊
    cursor.execute("SELECT * FROM cases WHERE id = ?", (case_id,))
    case = cursor.fetchone()
    if not case:
        conn.close()
        raise HTTPException(status_code=404, detail="找不到此房屋修繕工單")

    if case["status"] == "cancelled":
        conn.close()
        return {"success": False, "message": "此案件已經是取消狀態，不需重複退點！"}

    # 更新案件狀態為已取消
    cursor.execute("UPDATE cases SET status = 'cancelled' WHERE id = ?", (case_id,))
    
    # 檢查有哪些師傅解鎖過此單，並自動進行全額退點！
    unlocked_list = case["unlocked_by"].split(",") if case["unlocked_by"] else []
    refunded_techs = []
    
    cost = get_unlock_cost(case["item"], case["budget"])
    
    for tech_phone in unlocked_list:
        if not tech_phone:
            continue
        # 加點回師傅帳號
        cursor.execute("SELECT points, name FROM technicians WHERE phone = ?", (tech_phone,))
        tech = cursor.fetchone()
        if tech:
            refunded_points = tech["points"] + cost
            cursor.execute("UPDATE technicians SET points = ? WHERE phone = ?", (refunded_points, tech_phone))
            refunded_techs.append(f"{tech['name']}(已退還 {cost} 點)")
            
    conn.commit()
    conn.close()

    refund_summary = ", ".join(refunded_techs) if refunded_techs else "無"
    send_line_notification(f"【QT30 工單取消退款】\\n單號: {case_id}\\n狀態: 已取消\\n原因: 消費者取消現勘\\n已自動退款師傅名單: {refund_summary}")

    return {"success": True, "message": f"工單已成功取消！已將名單解鎖點數 ({cost} 點) 全額退還至 {len(refunded_techs)} 位合作師傅的儲值帳戶。"}

# --- 9. 師傅認證與登入查詢 API ---
@app.post("/api/tech/register")
def register_tech(data: TechRegister):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT phone FROM technicians WHERE phone = ?", (data.phone,))
    if cursor.fetchone():
        conn.close()
        return {"success": False, "message": "此手機號碼已經註冊過，請直接登入！"}
    
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cursor.execute("""
    INSERT INTO technicians (phone, password, name, id_card_no, id_card_photo, license_photo, skill, points, verified_status, created_at)
    VALUES (?, ?, ?, ?, '', '', ?, 100, '已通過', ?)
    """, (data.phone, data.password, data.name, data.idCardNo, data.skill, created_at))
    conn.commit()
    conn.close()
    return {"success": True, "message": "實名註冊成功！系統贈送 100 點體驗點數。"}

@app.get("/api/tech/profile")
def get_tech_profile(phone: str = "0912345678"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM technicians WHERE phone = ?", (phone,))
    tech = cursor.fetchone()
    conn.close()
    if not tech:
        return {"success": False, "message": "無此師傅"}
    return {
        "success": True,
        "tech": {
            "phone": tech["phone"],
            "name": tech["name"],
            "points": tech["points"],
            "verifiedStatus": tech["verified_status"],
            "skill": tech["skill"]
        }
    }

# --- 10. 綠界 B2B 儲值與 LINE Pay 串接金流 API ---
@app.post("/api/tech/topup")
def create_topup_order(data: TopupCreate, request: Request):
    amount = data.amount
    points = amount
    # 首充或大額儲值包優惠
    if amount == 1000:
        points = 1100
    elif amount == 3000:
        points = 3500

    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"TP{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO topup_orders (trade_no, phone, amount, points, status, created_at)
    VALUES (?, ?, ?, ?, 'unpaid', ?)
    """, (trade_no, data.phone, amount, points, created_at))
    conn.commit()
    conn.close()

    # 準備綠界 SDK 表單參數
    order_params = {
        'MerchantID': ECPAY_MERCHANT_ID,
        'MerchantTradeNo': trade_no,
        'MerchantTradeDate': datetime.now().strftime('%Y/%m/%d %H:%M:%S'),
        'PaymentType': 'aio',
        'TotalAmount': str(amount),
        'TradeDesc': ecpay_url_encode('QT30 點數儲值'),
        'ItemName': f'QT30-儲值點數-{points}點',
        'ReturnURL': f"{BASE_URL}/api/ecpay/topup-callback",
        'ClientBackURL': f"{BASE_URL}/tech",
        'ChoosePayment': 'ALL', # 綠界收銀台會自動顯示 LINE Pay ＋ 信用卡等多功能支付！
        'EncryptType': '1'
    }

    order_params['CheckMacValue'] = generate_check_mac_value(order_params, ECPAY_HASH_KEY, ECPAY_HASH_IV)

    return {
        "success": True,
        "payment_url": ECPAY_PAYMENT_URL,
        "params": order_params
    }

# --- 11. 綠界 ECPay 自動補點 Callback API ---
@app.post("/api/ecpay/topup-callback")
async def ecpay_topup_callback(request: Request):
    form_data = await request.form()
    data = dict(form_data)
    trade_no = data.get("MerchantTradeNo")
    rtn_code = data.get("RtnCode")

    if rtn_code == "1": # 1 代表扣款交易成功
        conn = get_db()
        cursor = conn.cursor()
        
        # 查詢點數儲值單
        cursor.execute("SELECT * FROM topup_orders WHERE trade_no = ?", (trade_no,))
        order = cursor.fetchone()
        if order and order["status"] == "unpaid":
            phone = order["phone"]
            points_to_add = order["points"]
            
            # 更新訂單狀態
            cursor.execute("UPDATE topup_orders SET status = 'paid' WHERE trade_no = ?", (trade_no,))
            # 幫師傅加點
            cursor.execute("UPDATE technicians SET points = points + ? WHERE phone = ?", (points_to_add, phone))
            
            # 取得師傅姓名與最新點數
            cursor.execute("SELECT name, points FROM technicians WHERE phone = ?", (phone,))
            tech = cursor.fetchone()
            conn.commit()
            conn.close()

            # LINE 通報管理員
            send_line_notification(f"【QT30 綠界儲值成功】\\n單號: {trade_no}\\n師傅: {tech['name']} ({phone})\\n儲值金額: {order['amount']}元\\n新增點數: {points_to_add}點\\n最新餘額: {tech['points']}點")
        else:
            conn.close()
            
    return "1|OK"

# --- 12. 據點管理與管理員 API ---
@app.get("/api/admin/technicians")
def list_technicians():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT phone, name, skill, points, verified_status, created_at FROM technicians ORDER BY created_at DESC")
    rows = cursor.fetchall()
    techs = [dict(r) for r in rows]
    conn.close()
    return {"success": True, "technicians": techs}

@app.post("/api/admin/verify-technician")
def verify_technician(data: AdminVerifyTech):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE technicians SET verified_status = ? WHERE phone = ?", (data.status, data.phone))
    conn.commit()
    conn.close()
    return {"success": True}

# --- 據點卡位（非核心，保留地圖擴充性） ---
class CustomSpotCreate(BaseModel):
    name: str
    lat: float
    lng: float
    radiusKm: Optional[int] = 5
    technicianPhone: str
    technicianName: str

@app.get("/api/spots")
def get_spots():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM spots")
    rows = cursor.fetchall()
    spots = [{"id": r["id"], "name": r["name"], "lat": r["lat"], "lng": r["lng"], "radiusKm": r["radius_km"], "technicianPhone": r["technician_phone"], "technicianName": r["technician_name"]} for r in rows]
    conn.close()
    return {"success": True, "spots": spots}

@app.post("/api/spots")
def create_spot(spot: CustomSpotCreate):
    conn = get_db()
    cursor = conn.cursor()
    new_id = f"spot-{int(time.time()*1000)%10000}"
    cursor.execute("""
    INSERT INTO spots (id, name, lat, lng, radius_km, technician_phone, technician_name)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (new_id, spot.name, spot.lat, spot.lng, spot.radiusKm, spot.technicianPhone, spot.technicianName))
    conn.commit()
    conn.close()
    return {"success": True, "spot": {"id": new_id, "name": spot.name, "lat": spot.lat, "lng": spot.lng, "radiusKm": spot.radiusKm, "technicianName": spot.technicianName}}

if __name__ == "__main__":
    import uvicorn
    # 本地或雲端運行 (FastAPI 支援一鍵複製部署至 Render / Heroku 或 VPS)
    uvicorn.run("main_completed:app", host="0.0.0.0", port=8000, reload=True)
