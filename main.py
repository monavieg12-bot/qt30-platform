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
ECPAY_MERCHANT_ID = os.getenv("ECPAY_MERCHANT_ID", "3002607")
ECPAY_HASH_KEY = os.getenv("ECPAY_HASH_KEY", "pwFHCqoQZGmho4w6")
ECPAY_HASH_IV = os.getenv("ECPAY_HASH_IV", "EkRm7iFT261dpevs")
ECPAY_PAYMENT_URL = os.getenv("ECPAY_PAYMENT_URL", "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5")
BASE_URL = os.getenv("BASE_URL", "https://qt30home.com")

# --- 資料庫初始化 ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
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
        status TEXT DEFAULT 'pending',
        technician TEXT,
        payment_status TEXT DEFAULT 'unpaid',
        unlocked_by TEXT DEFAULT '',
        available_slots TEXT,
        is_bidding INTEGER DEFAULT 0,
        created_at TEXT
    )
    """)
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
        verified_status TEXT DEFAULT '已通過',
        created_at TEXT
    )
    """)
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
    
    try:
        cursor.execute("ALTER TABLE cases ADD COLUMN available_slots TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        cursor.execute("ALTER TABLE cases ADD COLUMN is_bidding INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass

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
    item: str
    description: str
    budget: int = 0
    availableSlots: str
    isBidding: int = 0
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
    status: str

class AdminUpdateCase(BaseModel):
    status: str

# --- 本地免費工種分類路由 ---
LOCAL_RULES = {
    "水電維修": ["水電", "漏水", "水管", "馬桶", "開關", "插座", "跳電", "熱水器", "水龍頭", "電箱", "燈具"],
    "油漆粉刷": ["油漆", "壁癌", "脫漆", "補土", "裂縫", "刷漆", "批土", "發霉"],
    "防水抓漏": ["防水", "抓漏", "滲水", "PU", "打針", "高壓灌注", "屋頂防水", "外牆防水"],
    "浴室修繕": ["浴室", "衛浴", "浴缸", "乾濕分離", "貼磚", "磁磚", "地磚"],
    "專業搬家": ["搬家", "貨車", "清運", "搬運"],
    "冷氣空調": ["冷氣", "空調", "漏冷媒", "清洗冷氣"],
    "拆除清運": ["拆除", "打牆", "打石", "廢棄物", "垃圾清運"],
    "老屋翻修": ["老屋", "翻修", "翻新", "統包", "裝潢", "格局重整", "拉皮"]
}

def ai_router_classify(description: str, user_selected: str) -> str:
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    if OPENAI_API_KEY:
        try:
            url = "https://api.openai.com/v1/chat/completions"
            headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}
            prompt = f"從以下工種選出1個最合適的: ['水電維修', '油漆粉刷', '防水抓漏', '浴室修繕', '專業搬家', '冷氣空調', '拆除清運', '老屋翻修']。描述: '{description}'"
            payload = {"model": "gpt-4o-mini", "messages": [{"role": "user", "content": prompt}], "temperature": 0.1}
            res = requests.post(url, json=payload, headers=headers, timeout=5)
            if res.status_code == 200:
                result = res.json()["choices"][0]["message"]["content"].strip()
                for k in LOCAL_RULES.keys():
                    if k in result:
                        return k
        except Exception:
            pass
            
    # 本地免費規則比對
    for cat, keywords in LOCAL_RULES.items():
        for kw in keywords:
            if kw in description:
                return cat
    return user_selected

def get_unlock_cost(category: str, budget: int) -> int:
    if "老屋翻修" in category or budget >= 100000:
        return 2000
    elif budget >= 15000:
        return 500
    else:
        return 50

# --- 1. SEO 精美形象首頁 ---
@app.get("/", response_class=HTMLResponse)
def serve_home_seo():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QT30 房屋修繕派工平台｜雙北居家裝修・水電抓漏工程・8%透明監工</title>
        <meta name="description" content="QT30為大台北及淡水區提供最透明專業的居家修繕服務。包含水電、油漆、防水、浴室修繕、搬家、冷氣、拆除及老屋翻修統包。一鍵AI工種診斷、秒級媒合實名認證優質工班！">
        <meta name="keywords" content="淡水修繕推薦, 雙北室內裝潢, 水電維修, 抓漏防水, 浴室修繕, 老屋翻新統包, LINE Pay儲值, 裝修估價, QT30">
        <link rel="canonical" href="https://qt30home.com/" />
        <meta property="og:title" content="QT30 房屋修繕派工平台｜雙北居家裝修與老屋翻新統包" />
        <meta property="og:description" content="AI 智慧派工導診，極速匹配雙北及淡水最靠譜的實名制裝修與修繕師傅，完全免費發案！" />
        <meta property="og:type" content="website" />
        <meta property="og:url" content="https://qt30home.com/" />
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-50 text-slate-800 font-sans leading-normal">
        <nav class="bg-white shadow-md sticky top-0 z-50">
            <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 flex justify-between h-16 items-center">
                <span class="text-2xl font-black text-blue-600 tracking-wider">QT30 <span class="text-slate-800 text-lg font-bold">Home</span></span>
                <div class="flex items-center space-x-3">
                    <a href="/app" class="bg-blue-600 hover:bg-blue-700 text-white font-bold px-4 py-2 rounded-xl transition shadow text-sm">🙋 我要報修發案</a>
                    <a href="/tech" class="border border-blue-600 text-blue-600 hover:bg-blue-50 font-bold px-4 py-2 rounded-xl transition text-sm">🛠️ 師傅接單大廳</a>
                </div>
            </div>
        </nav>

        <section class="relative bg-slate-900 text-white py-20 px-4 text-center">
            <div class="max-w-4xl mx-auto space-y-6">
                <span class="bg-blue-500/20 text-blue-300 font-bold text-xs uppercase px-4 py-1.5 rounded-full border border-blue-500/30">台灣首創・雙向確認現勘平台</span>
                <h1 class="text-3xl sm:text-5xl font-black tracking-tight leading-tight">
                    告別裝修焦慮！<br><span class="text-transparent bg-clip-text bg-gradient-to-r from-blue-400 to-emerald-400">一鍵AI精準診斷 媒合王牌工班</span>
                </h1>
                <p class="text-base sm:text-lg text-slate-300 max-w-2xl mx-auto">
                    不管是老屋翻新統包，還是局部水電、抓漏、油漆、搬家、冷氣拆除，AI 幫您自動分析所需工種，不再找錯師傅！
                </p>
                <div class="flex flex-col sm:flex-row justify-center items-center gap-4 pt-4">
                    <a href="/app" class="w-full sm:w-auto bg-blue-600 hover:bg-blue-700 text-white text-lg font-black px-8 py-3.5 rounded-2xl shadow-xl transition">
                        立即免費發案估價 ➔
                    </a>
                    <a href="/tech" class="w-full sm:w-auto bg-slate-800 hover:bg-slate-700 border border-slate-700 text-slate-200 text-lg font-bold px-8 py-3.5 rounded-2xl transition">
                        我是專業師傅，我要入駐接單
                    </a>
                </div>
            </div>
        </section>

        <!-- 八大核心修繕服務 -->
        <section class="max-w-6xl mx-auto py-16 px-4">
            <div class="text-center space-y-2 mb-12">
                <h2 class="text-2xl font-black text-slate-900">全面覆蓋 8 大核心修繕服務</h2>
                <p class="text-slate-500 text-sm">從急迫的水電維修，到百萬預算的老屋整建，精準分流派工。</p>
            </div>
            <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 text-center space-y-2">
                    <div class="text-2xl">⚡</div>
                    <h3 class="font-bold text-slate-800 text-sm">水電維修</h3>
                    <p class="text-xs text-slate-400">暗管漏水、電箱重整、燈具面板</p>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 text-center space-y-2">
                    <div class="text-2xl">🎨</div>
                    <h3 class="font-bold text-slate-800 text-sm">油漆粉刷</h3>
                    <p class="text-xs text-slate-400">壁癌處理、細緻補土、全室噴漆</p>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 text-center space-y-2">
                    <div class="text-2xl">💧</div>
                    <h3 class="font-bold text-slate-800 text-sm">防水抓漏</h3>
                    <p class="text-xs text-slate-400">屋頂PU防水、高壓打針、窗框阻水</p>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 text-center space-y-2">
                    <div class="text-2xl">🛀</div>
                    <h3 class="font-bold text-slate-800 text-sm">浴室修繕</h3>
                    <p class="text-xs text-slate-400">乾濕分離、磁磚泥作、衛浴翻新</p>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 text-center space-y-2">
                    <div class="text-2xl">🚚</div>
                    <h3 class="font-bold text-slate-800 text-sm">專業搬家</h3>
                    <p class="text-xs text-slate-400">精緻防撞包裝、家具定位還原</p>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 text-center space-y-2">
                    <div class="text-2xl">❄️</div>
                    <h3 class="font-bold text-slate-800 text-sm">冷氣空調</h3>
                    <p class="text-xs text-slate-400">高壓清洗、漏冷媒檢修、移機安裝</p>
                </div>
                <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-100 text-center space-y-2">
                    <div class="text-2xl">🔨</div>
                    <h3 class="font-bold text-slate-800 text-sm">拆除清運</h3>
                    <p class="text-xs text-slate-400">隔間打石拆除、廢棄物清運</p>
                </div>
                <div class="bg-white p-5 rounded-2xl border-2 border-blue-500 shadow-md text-center space-y-2">
                    <div class="text-2xl">🏗️</div>
                    <h3 class="font-bold text-blue-600 text-sm">老屋翻修統包</h3>
                    <p class="text-xs text-slate-500">水電重拉、格局重劃、安心統包</p>
                </div>
            </div>
        </section>

        <footer class="bg-slate-900 text-slate-400 py-8 px-4 text-center text-xs space-y-2 border-t border-slate-800">
            <div class="text-white font-bold text-base">QT30 HOME</div>
            <p>新北市淡水區中正路一段100號 ｜ 合作諮詢：service@qt30home.com</p>
            <p class="text-slate-500">© 2026 QT30 房屋修繕派工平台 版權所有。</p>
        </footer>
    </body>
    </html>
    """

# --- 2. 客戶發案頁面 (/app) ---
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
    <body class="bg-slate-100 min-h-screen p-4 sm:p-6">
        <div class="max-w-md mx-auto bg-white rounded-2xl shadow-xl overflow-hidden">
            <div class="bg-blue-600 p-5 text-white text-center">
                <h1 class="text-xl font-bold">🙋 QT30 房屋修繕發案</h1>
                <p class="text-blue-100 text-xs mt-1">填單立即自動分析工種，安排師傅現勘</p>
            </div>
            <form id="orderForm" class="p-5 space-y-3.5" onsubmit="submitForm(event)">
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-xs font-bold text-gray-700">聯絡姓名</label>
                        <input type="text" id="clientName" required placeholder="例如：王先生" class="w-full mt-1 p-2.5 text-sm border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-gray-700">聯絡電話</label>
                        <input type="tel" id="clientPhone" required placeholder="例如：0912345678" class="w-full mt-1 p-2.5 text-sm border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none">
                    </div>
                </div>

                <div>
                    <label class="block text-xs font-bold text-gray-700">詳細地址</label>
                    <input type="text" id="address" required placeholder="例如：新北市淡水區中正路100號" class="w-full mt-1 p-2.5 text-sm border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none">
                </div>

                <div>
                    <label class="block text-xs font-bold text-gray-700">希望修繕項目</label>
                    <select id="item" class="w-full mt-1 p-2.5 text-sm border rounded-xl bg-white focus:ring-2 focus:ring-blue-500 outline-none">
                        <option value="水電維修">水電維修 / 衛浴更換</option>
                        <option value="油漆粉刷">油漆粉刷 / 壁癌處理</option>
                        <option value="防水抓漏">防水抓漏 / 泥作工程</option>
                        <option value="浴室修繕">整體衛浴翻新</option>
                        <option value="專業搬家">專業搬家 / 清運</option>
                        <option value="冷氣空調">冷氣清洗 / 檢修安裝</option>
                        <option value="拆除清運">裝潢隔間打石拆除</option>
                        <option value="老屋翻修">🏗️ 老屋翻修 (統包工程)</option>
                    </select>
                </div>

                <div>
                    <label class="block text-xs font-bold text-gray-700">情況說明（AI 會自動分析工種）</label>
                    <textarea id="description" rows="2" required placeholder="例如：浴室牆壁滲水、馬桶有點不通..." class="w-full mt-1 p-2.5 text-sm border rounded-xl focus:ring-2 focus:ring-blue-500 outline-none"></textarea>
                </div>

                <div>
                    <label class="block text-xs font-bold text-gray-700">預估工程預算 (新台幣 NT$)</label>
                    <input type="number" id="budget" required placeholder="例如：15000" class="w-full mt-1 p-2.5 text-sm border rounded-xl font-bold text-amber-600 focus:ring-2 focus:ring-amber-500 outline-none">
                </div>

                <div class="p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-2">
                    <label class="block text-xs font-bold text-blue-700">📅 方便現勘的時段（必填）</label>
                    <input type="text" id="availableSlots" required placeholder="例如：週六下午、平日晚上" class="w-full p-2 text-sm border rounded-lg bg-white outline-none">
                    <div class="flex items-center space-x-2 pt-1">
                        <input type="checkbox" id="isBidding" value="1" class="w-4 h-4 text-blue-600 rounded">
                        <label for="isBidding" class="text-xs font-bold text-gray-700 cursor-pointer">開放多家師傅公開比價</label>
                    </div>
                </div>

                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-xl transition shadow">
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

                const res = await fetch('/api/cases', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (data.success) {
                    alert(`發案成功！工種判定為：${data.item_diagnosed}。案件單號: ${data.case_id}`);
                    document.getElementById('orderForm').reset();
                } else {
                    alert(`發案失敗: ${data.message}`);
                }
            }
        </script>
    </body>
    </html>
    """

# --- 3. 師傅端工作台 (/tech) ---
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
        <header class="bg-slate-900 border-b border-slate-800 p-4 sticky top-0 z-40">
            <div class="max-w-4xl mx-auto flex justify-between items-center">
                <div>
                    <div class="text-[11px] text-blue-400 font-bold">QT30 專業師傅平台</div>
                    <div class="flex items-center space-x-2 mt-0.5">
                        <span class="font-bold text-sm text-white">王師傅 (北部統包水電)</span>
                        <span class="text-[10px] font-bold px-1.5 py-0.5 rounded bg-emerald-950 text-emerald-400 border border-emerald-800">✓ 實名認證</span>
                    </div>
                </div>
                <div class="flex items-center space-x-3">
                    <div class="text-right">
                        <span class="text-[11px] text-slate-400">點數餘額</span>
                        <div class="font-black text-amber-400 text-base leading-none">
                            <span id="pointsBalance">--</span> 點
                        </div>
                    </div>
                    <button onclick="switchTab('topup')" class="bg-emerald-600 hover:bg-emerald-700 text-slate-950 font-bold text-xs px-3 py-1.5 rounded-lg transition">
                        💳 線上儲值
                    </button>
                </div>
            </div>
        </header>

        <main class="max-w-4xl mx-auto p-4 space-y-4">
            <div class="flex space-x-2 border-b border-slate-800 pb-2">
                <button onclick="switchTab('cases')" id="btn-cases" class="tab-btn px-3 py-1.5 rounded-lg font-bold text-xs bg-blue-600 text-white">
                    📋 待搶修繕工單大廳
                </button>
                <button onclick="switchTab('topup')" id="btn-topup" class="tab-btn px-3 py-1.5 rounded-lg font-bold text-xs bg-slate-900 text-slate-400">
                    💳 購買點數儲值包
                </button>
            </div>

            <!-- 工單大廳 -->
            <section id="section-cases" class="space-y-3">
                <div class="flex justify-between items-center">
                    <h2 class="text-sm font-bold">即時待接工單 (AI 自動分類)</h2>
                    <button onclick="loadCases()" class="text-xs bg-slate-800 px-2.5 py-1 rounded border border-slate-700">🔄 刷新</button>
                </div>
                <div id="casesList" class="space-y-3">
                    <p class="text-slate-500 text-xs">載入工單中...</p>
                </div>
            </section>

            <!-- 儲值頁面 -->
            <section id="section-topup" class="hidden max-w-md mx-auto bg-slate-900 p-5 rounded-2xl border border-slate-800 space-y-4">
                <h3 class="text-base font-bold text-amber-400">💎 師傅在線儲值</h3>
                <div class="grid grid-cols-3 gap-2 text-center text-xs">
                    <button onclick="setAmount(500)" class="border border-slate-700 p-3 rounded-xl hover:border-amber-500">
                        <div class="font-bold text-slate-300">基礎包</div>
                        <div class="text-amber-400 font-bold mt-1">500 點</div>
                        <div class="text-slate-500">NT$ 500</div>
                    </button>
                    <button onclick="setAmount(1000)" class="border border-slate-700 p-3 rounded-xl hover:border-amber-500">
                        <div class="font-bold text-slate-300">優惠包</div>
                        <div class="text-amber-400 font-bold mt-1">1100 點</div>
                        <div class="text-slate-500">NT$ 1000</div>
                    </button>
                    <button onclick="setAmount(3000)" class="border border-emerald-500 p-3 rounded-xl bg-emerald-950/20">
                        <div class="font-bold text-emerald-400">卡位包</div>
                        <div class="text-amber-400 font-bold mt-1">3500 點</div>
                        <div class="text-slate-500">NT$ 3000</div>
                    </button>
                </div>
                <div>
                    <label class="block text-xs font-bold text-slate-400 mb-1">儲值金額 (NT$)</label>
                    <input type="number" id="topupAmount" value="1000" class="w-full bg-slate-950 p-2.5 rounded-lg border border-slate-800 text-amber-400 font-bold outline-none">
                </div>
                <button onclick="requestTopup()" class="w-full bg-emerald-600 hover:bg-emerald-700 text-slate-950 font-bold py-2.5 rounded-lg text-sm transition">
                    💳 前往綠界收銀台 (LINE Pay / 信用卡)
                </button>
            </section>
        </main>

        <script>
            const PHONE = '0912345678';
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

            function setAmount(amt) { document.getElementById('topupAmount').value = amt; }

            async function loadProfile() {
                try {
                    const res = await fetch(`/api/tech/profile?phone=${PHONE}`);
                    const data = await res.json();
                    if (data.success) document.getElementById('pointsBalance').innerText = data.tech.points;
                } catch(e) {}
            }

            async function loadCases() {
                try {
                    const res = await fetch(`/api/cases?phone=${PHONE}`);
                    const data = await res.json();
                    const container = document.getElementById('casesList');
                    container.innerHTML = '';
                    if (data.cases.length === 0) {
                        container.innerHTML = '<p class="text-slate-500 text-xs">目前尚無待接案件。</p>';
                        return;
                    }
                    data.cases.forEach(c => {
                        const isUnlocked = c.unlocked;
                        const cost = getUnlockCost(c.item, c.budget);
                        let html = `
                            <div class="bg-slate-900 p-4 rounded-xl border ${isUnlocked ? 'border-blue-500' : 'border-slate-800'} space-y-2">
                                <div class="flex justify-between text-xs">
                                    <span class="font-mono text-slate-400">${c.id}</span>
                                    <span class="text-blue-400 font-bold">${c.item}</span>
                                </div>
                                <p class="text-sm text-slate-200">${c.description}</p>
                                <div class="text-xs text-slate-400">預算：<span class="text-amber-400 font-bold">NT$ ${c.budget}</span> ｜ 現勘時段：${c.available_slots || '未約定'}</div>
                        `;
                        if (isUnlocked) {
                            html += `
                                <div class="p-2.5 bg-blue-950/40 rounded-lg border border-blue-900 text-xs space-y-1">
                                    <div class="font-bold text-blue-300">✓ 已解鎖聯絡人資訊：</div>
                                    <div>客戶：${c.client_name} (電話: <a href="tel:${c.client_phone}" class="text-blue-400 underline">${c.client_phone}</a>)</div>
                                    <div>地址：${c.address}</div>
                                </div>
                            `;
                        } else {
                            html += `
                                <div class="flex justify-between items-center pt-2">
                                    <span class="text-xs text-slate-400">解鎖需 <span class="text-amber-400 font-bold">${cost}</span> 點</span>
                                    <button onclick="unlockCase('${c.id}')" class="bg-blue-600 hover:bg-blue-700 text-white font-bold text-xs px-3 py-1.5 rounded-lg">⚡ 扣點解鎖</button>
                                </div>
                            `;
                        }
                        html += `</div>`;
                        container.innerHTML += html;
                    });
                } catch(e) {}
            }

            function getUnlockCost(item, budget) {
                if (item.includes("老屋翻修") || budget >= 100000) return 2000;
                if (budget >= 15000) return 500;
                return 50;
            }

            async function unlockCase(id) {
                if (!confirm('確認扣點解鎖現勘資訊？（若客戶取消將全額退點）')) return;
                const res = await fetch(`/api/cases/${id}/unlock?phone=${PHONE}`, { method: 'POST' });
                const data = await res.json();
                alert(data.message);
                loadProfile();
                loadCases();
            }

            async function requestTopup() {
                const amt = parseInt(document.getElementById('topupAmount').value);
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

            loadProfile();
            loadCases();
        </script>
    </body>
    </html>
    """

# --- 4. 管理者後台 (/admin) ---
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
    <body class="bg-slate-100 min-h-screen p-4 sm:p-6">
        <div class="max-w-5xl mx-auto space-y-4">
            <header class="flex justify-between items-center bg-white p-4 rounded-2xl shadow-sm">
                <div>
                    <h1 class="text-xl font-bold text-slate-800">QT30 營運總控制後台</h1>
                    <p class="text-xs text-slate-500">工單監控・師傅管理・取消退點</p>
                </div>
                <button onclick="loadAll()" class="bg-blue-600 text-white text-xs font-bold px-3 py-2 rounded-xl shadow">🔄 刷新</button>
            </header>

            <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                <section class="bg-white p-4 rounded-2xl shadow-sm space-y-3">
                    <h2 class="text-sm font-bold border-b pb-2">👨 師傅名冊</h2>
                    <div id="techsList" class="space-y-2 text-xs"></div>
                </section>
                <section class="md:col-span-2 bg-white p-4 rounded-2xl shadow-sm space-y-3">
                    <h2 class="text-sm font-bold border-b pb-2">📋 修繕工單與退點管理</h2>
                    <div id="casesList" class="space-y-3 text-xs"></div>
                </section>
            </div>
        </div>

        <script>
            async function loadAll() { loadTechs(); loadCases(); }

            async function loadTechs() {
                const res = await fetch('/api/admin/technicians');
                const data = await res.json();
                const container = document.getElementById('techsList');
                container.innerHTML = data.technicians.map(t => `
                    <div class="p-3 bg-slate-50 rounded-xl border border-slate-200">
                        <div class="flex justify-between font-bold text-slate-800">
                            <span>${t.name}</span>
                            <span class="text-amber-600">${t.points} 點</span>
                        </div>
                        <div class="text-slate-500 mt-1">${t.phone} ｜ ${t.skill}</div>
                    </div>
                `).join('');
            }

            async function loadCases() {
                const res = await fetch('/api/cases');
                const data = await res.json();
                const container = document.getElementById('casesList');
                container.innerHTML = data.cases.map(c => `
                    <div class="p-3 bg-slate-50 rounded-xl border border-slate-200 space-y-1.5">
                        <div class="flex justify-between font-bold">
                            <span class="text-blue-600">${c.id} (${c.item})</span>
                            <span class="${c.status === 'cancelled' ? 'text-red-500' : 'text-emerald-600'}">${c.status}</span>
                        </div>
                        <div class="text-slate-700">客戶：${c.client_name} (${c.client_phone}) ｜ 地址：${c.address}</div>
                        <div class="text-slate-500">預算：NT$ ${c.budget} ｜ 現勘時段：${c.available_slots || '未指定'}</div>
                        <div class="text-slate-400">已解鎖師傅：${c.unlocked_by || '無'}</div>
                        ${c.status !== 'cancelled' ? `
                            <button onclick="cancelCase('${c.id}')" class="bg-red-500 text-white font-bold px-3 py-1 rounded hover:bg-red-600 text-[11px]">
                                客戶取消現勘（一鍵全額退點）
                            </button>
                        ` : '<span class="text-slate-400 italic text-[11px]">點數已退還師傅</span>'}
                    </div>
                `).join('');
            }

            async function cancelCase(id) {
                if (!confirm('確認取消此單並退點給師傅？')) return;
                const res = await fetch(`/api/cases/${id}/cancel`, { method: 'POST' });
                const data = await res.json();
                alert(data.message);
                loadAll();
            }

            loadAll();
        </script>
    </body>
    </html>
    """

# --- 5. 路由 API 接口 ---
@app.post("/api/cases")
def create_case(data: CaseCreate):
    trade_no = f"QT{datetime.now().strftime('%Y%m%d%H%M%S')[-8:]}{int(time.time()*1000)%1000:03d}"
    case_id = f"CASE-{trade_no[-6:]}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    final_item = ai_router_classify(data.description, data.item)

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO cases (id, trade_no, client_name, client_phone, address, item, description, budget, status, unlocked_by, available_slots, is_bidding, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, ?, ?)
    """, (case_id, trade_no, data.clientName, data.clientPhone, data.address, final_item, data.description, data.budget, data.availableSlots, data.isBidding, created_at))
    conn.commit()
    conn.close()

    send_line_notification(f"【QT30 新增派工單】\\n單號: {case_id}\\n客戶: {data.clientName}\\n類別: {final_item}\\n預算: {data.budget}元\\n現勘時間: {data.availableSlots}")
    return {"success": True, "case_id": case_id, "item_diagnosed": final_item}

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
        cases.append({
            "id": r["id"], "item": r["item"], "description": r["description"],
            "budget": r["budget"], "status": r["status"], "created_at": r["created_at"],
            "available_slots": r["available_slots"], "is_bidding": r["is_bidding"],
            "unlocked": unlocked, "unlocked_by": r["unlocked_by"],
            "client_name": r["client_name"] if unlocked else r["client_name"][0] + "先生/小姐",
            "client_phone": r["client_phone"] if unlocked else "解鎖後顯示",
            "address": r["address"] if unlocked else r["address"][:6] + " (解鎖後顯示詳細門牌)"
        })
    conn.close()
    return {"success": True, "cases": cases}

@app.post("/api/cases/{case_id}/unlock")
def unlock_case(case_id: str, phone: str = "0912345678"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cases WHERE id = ?", (case_id,))
    case = cursor.fetchone()
    cursor.execute("SELECT points, name FROM technicians WHERE phone = ?", (phone,))
    tech = cursor.fetchone()
    if not case or not tech:
        conn.close()
        raise HTTPException(status_code=404, detail="找不到案件或師傅")

    unlocked_list = case["unlocked_by"].split(",") if case["unlocked_by"] else []
    if phone in unlocked_list:
        conn.close()
        return {"success": True, "message": "已解鎖過此案"}

    cost = get_unlock_cost(case["item"], case["budget"])
    if tech["points"] < cost:
        conn.close()
        return {"success": False, "message": f"點數不足！需 {cost} 點，目前僅有 {tech['points']} 點。"}

    new_points = tech["points"] - cost
    unlocked_list.append(phone)
    new_unlocked_by = ",".join([p for p in unlocked_list if p])

    cursor.execute("UPDATE technicians SET points = ? WHERE phone = ?", (new_points, phone))
    cursor.execute("UPDATE cases SET unlocked_by = ?, status = 'unlocked' WHERE id = ?", (new_unlocked_by, case_id))
    conn.commit()
    conn.close()

    send_line_notification(f"【QT30 師傅扣點解鎖】\\n師傅: {tech['name']}\\n單號: {case_id}\\n扣除: {cost}點\\n餘額: {new_points}點")
    return {"success": True, "message": f"成功扣除 {cost} 點！已解鎖聯絡人資訊。"}

@app.post("/api/cases/{case_id}/cancel")
def cancel_and_refund_case(case_id: str):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cases WHERE id = ?", (case_id,))
    case = cursor.fetchone()
    if not case or case["status"] == "cancelled":
        conn.close()
        return {"success": False, "message": "案件不存在或已是取消狀態"}

    cursor.execute("UPDATE cases SET status = 'cancelled' WHERE id = ?", (case_id,))
    unlocked_list = case["unlocked_by"].split(",") if case["unlocked_by"] else []
    cost = get_unlock_cost(case["item"], case["budget"])
    
    for tech_phone in unlocked_list:
        if tech_phone:
            cursor.execute("UPDATE technicians SET points = points + ? WHERE phone = ?", (cost, tech_phone))
            
    conn.commit()
    conn.close()
    return {"success": True, "message": f"案件已取消，已全額退還 {cost} 點至解鎖師傅帳號。"}

@app.get("/api/tech/profile")
def get_tech_profile(phone: str = "0912345678"):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM technicians WHERE phone = ?", (phone,))
    tech = cursor.fetchone()
    conn.close()
    if not tech:
        return {"success": False}
    return {"success": True, "tech": {"phone": tech["phone"], "name": tech["name"], "points": tech["points"], "skill": tech["skill"]}}

@app.post("/api/tech/topup")
def create_topup_order(data: TopupCreate):
    amount = data.amount
    points = amount
    if amount == 1000: points = 1100
    elif amount == 3000: points = 3500

    trade_no = f"TP{datetime.now().strftime('%Y%m%d%H%M%S')[-8:]}{int(time.time()*1000)%1000:03d}"
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO topup_orders VALUES (?, ?, ?, ?, 'unpaid', ?)", (trade_no, data.phone, amount, points, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

    order_params = {
        'MerchantID': ECPAY_MERCHANT_ID,
        'MerchantTradeNo': trade_no,
        'MerchantTradeDate': datetime.now().strftime('%Y/%m/%d %H:%M:%S'),
        'PaymentType': 'aio',
        'TotalAmount': str(amount),
        'TradeDesc': ecpay_url_encode('QT30 點數儲值'),
        'ItemName': f'QT30-儲值-{points}點',
        'ReturnURL': f"{BASE_URL}/api/ecpay/topup-callback",
        'ClientBackURL': f"{BASE_URL}/tech",
        'ChoosePayment': 'ALL',
        'EncryptType': '1'
    }
    order_params['CheckMacValue'] = generate_check_mac_value(order_params, ECPAY_HASH_KEY, ECPAY_HASH_IV)
    return {"success": True, "payment_url": ECPAY_PAYMENT_URL, "params": order_params}

@app.get("/api/admin/technicians")
def list_technicians():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT phone, name, skill, points, verified_status, created_at FROM technicians")
    rows = cursor.fetchall()
    techs = [dict(r) for r in rows]
    conn.close()
    return {"success": True, "technicians": techs}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
