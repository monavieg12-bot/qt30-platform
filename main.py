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

# 綠界正式環境參數
ECPAY_MERCHANT_ID = os.environ.get("ECPAY_MERCHANT_ID", "3513009")
ECPAY_HASH_KEY = os.environ.get("ECPAY_HASH_KEY", "bVfP4c3B8vLqW1zX")
ECPAY_HASH_IV = os.environ.get("ECPAY_HASH_IV", "9sD2kL5pM7nQ4rT6")
ECPAY_API_URL = "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"
BASE_URL = os.environ.get("BASE_URL", "https://qt30-platform.onrender.com")

DB_FILE = "qt30.db"

# ==========================================
# 資料庫初始化
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 案件資料表
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            address TEXT,
            category TEXT,
            description TEXT,
            budget TEXT,
            status TEXT DEFAULT 'pending',
            taken_by TEXT DEFAULT '',
            created_at TEXT
        )
    ''')
    # 師傅資料表
    c.execute('''
        CREATE TABLE IF NOT EXISTS technicians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE,
            password TEXT,
            name TEXT,
            id_card TEXT DEFAULT '',
            license TEXT DEFAULT '',
            skills TEXT,
            area TEXT,
            points INTEGER DEFAULT 100,
            status TEXT DEFAULT 'pending',
            created_at TEXT
        )
    ''')
    # 綠界交易紀錄表
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

# ==========================================
# 綠界 CheckMacValue 產生器
# ==========================================
def generate_check_mac_value(params, hash_key, hash_iv):
    # 1. 排除 CheckMacValue 欄位
    filtered_params = {k: v for k, v in params.items() if k != 'CheckMacValue'}
    # 2. 依照鍵名由小到大排序 (A-Z)
    sorted_params = sorted(filtered_params.items(), key=lambda x: x[0])
    # 3. 組成查詢字串
    param_str = "&".join([f"{k}={v}" for k, v in sorted_params])
    # 4. 前後加入 HashKey 與 HashIV
    raw_str = f"HashKey={hash_key}&{param_str}&HashIV={hash_iv}"
    # 5. URL Encode (符合 .NET 編碼規則)
    encoded_str = urllib.parse.quote_plus(raw_str).lower()
    # 修正特定字元
    encoded_str = (encoded_str
                   .replace('%21', '!')
                   .replace('%2a', '*')
                   .replace('%28', '(')
                   .replace('%29', ')')
                   .replace('%20', '+')
                   .replace('%2d', '-')
                   .replace('%5f', '_')
                   .replace('%2e', '.')
                   )
    # 6. SHA256 加密並轉大寫
    return hashlib.sha256(encoded_str.encode('utf-8')).hexdigest().upper()

# ==========================================
# LINE Push 訊息推送工具
# ==========================================
def send_line_push_message(text):
    if not LINE_CHANNEL_ACCESS_TOKEN or not ADMIN_LINE_USER_ID:
        return
    import urllib.request
    url = "https://api.line.me/v2/bot/message/push"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }
    payload = {
        "to": ADMIN_LINE_USER_ID,
        "messages": [{"type": "text", "text": text}]
    }
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode('utf-8'), headers=headers)
        urllib.request.urlopen(req)
    except Exception as e:
        print(f"LINE Push Error: {e}")

# ==========================================
# 首頁導向
# ==========================================
@app.route("/")
def index():
    return redirect("/app")

# ==========================================
# 1. 客戶端預約報修 (/app)
# ==========================================
@app.route("/app")
def client_app():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>QT30 房屋修繕預約</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-100 min-h-screen flex items-center justify-center p-4">
        <div class="bg-white rounded-2xl shadow-xl w-full max-w-md overflow-hidden">
            <div class="bg-blue-600 p-6 text-white text-center">
                <h1 class="text-2xl font-bold">QT30 房屋修繕預約</h1>
                <p class="text-blue-100 text-sm mt-1">填單立即為您安排通過實名認證的專業師傅</p>
            </div>
            <form id="orderForm" class="p-6 space-y-4">
                <div>
                    <label class="block text-sm font-bold text-gray-700">聯絡姓名</label>
                    <input type="text" id="name" required placeholder="例如：王先生" class="w-full mt-1 p-3 border rounded-xl focus:ring-2 focus:ring-blue-500">
                </div>
                <div>
                    <label class="block text-sm font-bold text-gray-700">聯絡電話</label>
                    <input type="tel" id="phone" required placeholder="例如：0912345678" class="w-full mt-1 p-3 border rounded-xl focus:ring-2 focus:ring-blue-500">
                </div>
                <div>
                    <label class="block text-sm font-bold text-gray-700">修繕地址</label>
                    <input type="text" id="address" required placeholder="例如：新北市淡水區中正路..." class="w-full mt-1 p-3 border rounded-xl focus:ring-2 focus:ring-blue-500">
                </div>
                <div>
                    <label class="block text-sm font-bold text-gray-700">修繕類別</label>
                    <select id="category" class="w-full mt-1 p-3 border rounded-xl focus:ring-2 focus:ring-blue-500">
                        <option value="水電維修">水電維修 / 衛浴更換</option>
                        <option value="冷氣空調">冷氣清洗 / 檢修安裝</option>
                        <option value="泥作防水">泥作泥工 / 屋頂抓漏</option>
                        <option value="油漆粉刷">油漆粉刷 / 壁癌處理</option>
                        <option value="門窗裝鎖">門窗紗網 / 鎖具更換</option>
                    </select>
                </div>
                <div>
                    <label class="block text-sm font-bold text-gray-700">問題描述</label>
                    <textarea id="description" rows="3" required placeholder="請簡單說明故障情況..." class="w-full mt-1 p-3 border rounded-xl focus:ring-2 focus:ring-blue-500"></textarea>
                </div>
                <div>
                    <label class="block text-sm font-bold text-gray-700">預估預算 (元)</label>
                    <input type="text" id="budget" placeholder="例如：2,000 ~ 5,000 或 依現場報價" class="w-full mt-1 p-3 border rounded-xl focus:ring-2 focus:ring-blue-500">
                </div>
                <button type="submit" class="w-full bg-blue-600 text-white font-bold py-3.5 rounded-xl shadow hover:bg-blue-700 transition">🚀 立即送出預約報修</button>
            </form>
        </div>
        <script>
            document.getElementById('orderForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('name').value,
                    phone: document.getElementById('phone').value,
                    address: document.getElementById('address').value,
                    category: document.getElementById('category').value,
                    description: document.getElementById('description').value,
                    budget: document.getElementById('budget').value
                };
                const res = await fetch('/api/orders', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.success) {
                    alert('預約成功！專業師傅已收到通報，稍後將致電與您確認時間。');
                    document.getElementById('orderForm').reset();
                } else {
                    alert('送出失敗，請稍後再試。');
                }
            });
        </script>
    </body>
    </html>
    ''')

# ==========================================
# 2. 師傅接單工作台 (/tech)
# ==========================================
@app.route("/tech")
def tech_app():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>QT30 師傅接單工作台</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen">
        <div id="authBox" class="p-6 max-w-md mx-auto">
            <div class="text-center py-6">
                <h1 class="text-3xl font-black text-amber-400">QT30 師傅工作台</h1>
                <p class="text-slate-400 text-sm mt-1">實名認證・接單搶單・線上儲值</p>
            </div>
            <div class="bg-slate-800 p-6 rounded-2xl border border-slate-700 shadow-xl space-y-4">
                <div class="flex border-b border-slate-700 pb-2 mb-4">
                    <button id="tabLogin" class="flex-1 text-center font-bold text-amber-400 pb-2 border-b-2 border-amber-400">師傅登入</button>
                    <button id="tabRegister" class="flex-1 text-center font-bold text-slate-400 pb-2">新師傅註冊</button>
                </div>
                <!-- 登入 Form -->
                <form id="loginForm" class="space-y-4">
                    <div>
                        <label class="block text-sm text-slate-300">手機號碼</label>
                        <input type="tel" id="loginPhone" required placeholder="0912345678" class="w-full mt-1 p-3 bg-slate-900 border border-slate-600 rounded-xl text-white">
                    </div>
                    <div>
                        <label class="block text-sm text-slate-300">登入密碼</label>
                        <input type="password" id="loginPassword" required placeholder="請輸入密碼" class="w-full mt-1 p-3 bg-slate-900 border border-slate-600 rounded-xl text-white">
                    </div>
                    <button type="submit" class="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold py-3.5 rounded-xl transition">🔑 登入工作台</button>
                </form>
                <!-- 註冊 Form -->
                <form id="registerForm" class="space-y-3 hidden">
                    <div>
                        <label class="block text-xs text-slate-300">真實姓名</label>
                        <input type="text" id="regName" required class="w-full mt-1 p-2 bg-slate-900 border border-slate-600 rounded-lg text-white">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">手機號碼 (登入帳號)</label>
                        <input type="tel" id="regPhone" required class="w-full mt-1 p-2 bg-slate-900 border border-slate-600 rounded-lg text-white">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">設定密碼</label>
                        <input type="password" id="regPassword" required class="w-full mt-1 p-2 bg-slate-900 border border-slate-600 rounded-lg text-white">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">專業專長</label>
                        <input type="text" id="regSkills" placeholder="例：水電、冷氣清洗、泥作抓漏" class="w-full mt-1 p-2 bg-slate-900 border border-slate-600 rounded-lg text-white">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">服務地區</label>
                        <input type="text" id="regArea" placeholder="例：雙北地區、桃園市" class="w-full mt-1 p-2 bg-slate-900 border border-slate-600 rounded-lg text-white">
                    </div>
                    <button type="submit" class="w-full bg-green-600 hover:bg-green-700 text-white font-bold py-3 rounded-xl transition">📝 註冊送出審核 (送 100 點)</button>
                </form>
            </div>
        </div>

        <!-- 登入後的工作台 Dashboard -->
        <div id="dashBox" class="hidden max-w-4xl mx-auto p-4 space-y-6">
            <!-- 頂部資訊列 -->
            <div class="bg-slate-800 p-5 rounded-2xl border border-slate-700 flex flex-wrap items-center justify-between gap-4">
                <div>
                    <h2 class="text-xl font-bold flex items-center gap-2">
                        <span id="userName"></span> 師傅
                        <span id="userStatusBadge" class="text-xs px-2.5 py-1 rounded-full bg-amber-500/20 text-amber-400 font-normal">實名審核中</span>
                    </h2>
                    <p class="text-slate-400 text-sm mt-0.5">專業：<span id="userSkills"></span> | 服務區：<span id="userArea"></span></p>
                </div>
                <div class="flex items-center gap-4">
                    <div class="text-right">
                        <div class="text-xs text-slate-400">目前點數餘額</div>
                        <div class="text-2xl font-black text-amber-400"><span id="userPoints">0</span> <span class="text-sm font-normal text-slate-300">點</span></div>
                    </div>
                    <button onclick="showSection('topup')" class="bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold px-4 py-2 rounded-xl text-sm transition">💳 儲值點數</button>
                    <button onclick="logout()" class="text-slate-400 hover:text-white text-sm">登出</button>
                </div>
            </div>

            <!-- 分頁按鈕 -->
            <div class="flex border-b border-slate-700 space-x-6 text-sm font-bold">
                <button onclick="showSection('orders')" id="btnTabOrders" class="text-amber-400 border-b-2 border-amber-400 pb-2">📋 接單大廳 (扣點搶單)</button>
                <button onclick="showSection('topup')" id="btnTabTopup" class="text-slate-400 pb-2">💎 點數儲值專區</button>
            </div>

            <!-- 1. 接單大廳 -->
            <div id="sectionOrders" class="space-y-4">
                <div class="flex justify-between items-center">
                    <h3 class="font-bold text-lg text-slate-200">可接修繕案件</h3>
                    <button onclick="loadOrders()" class="text-xs bg-slate-800 hover:bg-slate-700 px-3 py-1.5 rounded-lg border border-slate-600">🔄 重新整理列表</button>
                </div>
                <div id="ordersList" class="space-y-3"></div>
            </div>

            <!-- 2. 線上儲值專區 (綠界刷卡) -->
            <div id="sectionTopup" class="hidden bg-slate-800 p-6 rounded-2xl border border-slate-700 space-y-6">
                <div>
                    <h3 class="text-xl font-bold text-amber-400">💎 師傅在線購點</h3>
                    <p class="text-slate-400 text-sm mt-1">每搶接一張訂單僅扣除 50 點！線上刷卡付款後系統即時自動入帳。</p>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div class="bg-slate-900 border border-slate-700 p-5 rounded-xl text-center space-y-3 hover:border-amber-500 transition">
                        <h4 class="font-bold text-slate-200">基礎體驗包</h4>
                        <div class="text-3xl font-black text-amber-400">500 <span class="text-sm font-normal text-slate-400">點</span></div>
                        <p class="text-xs text-slate-400">可搶接約 10 張修繕案件</p>
                        <button onclick="payECPay(500, 500)" class="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold py-2.5 rounded-lg">線上刷卡 NT$ 500</button>
                    </div>
                    <div class="bg-slate-900 border-2 border-amber-500 p-5 rounded-xl text-center space-y-3 relative shadow-lg">
                        <span class="absolute -top-3 left-1/2 -translate-x-1/2 bg-amber-500 text-slate-950 text-xs font-black px-2.5 py-0.5 rounded-full">超值推薦</span>
                        <h4 class="font-bold text-slate-200">專業進階包</h4>
                        <div class="text-3xl font-black text-amber-400">1,100 <span class="text-sm font-normal text-slate-400">點</span></div>
                        <p class="text-xs text-amber-300 font-bold">加贈 100 點！可搶 22 件</p>
                        <button onclick="payECPay(1000, 1100)" class="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold py-2.5 rounded-lg">線上刷卡 NT$ 1,000</button>
                    </div>
                    <div class="bg-slate-900 border border-slate-700 p-5 rounded-xl text-center space-y-3 hover:border-amber-500 transition">
                        <h4 class="font-bold text-slate-200">團隊尊榮包</h4>
                        <div class="text-3xl font-black text-amber-400">2,400 <span class="text-sm font-normal text-slate-400">點</span></div>
                        <p class="text-xs text-amber-300 font-bold">加贈 400 點！可搶 48 件</p>
                        <button onclick="payECPay(2000, 2400)" class="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold py-2.5 rounded-lg">線上刷卡 NT$ 2,000</button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            let currentTech = null;

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
                const phone = document.getElementById('loginPhone').value;
                const password = document.getElementById('loginPassword').value;
                const res = await fetch('/api/tech/login', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone, password})
                });
                const data = await res.json();
                if(data.success) {
                    currentTech = data.tech;
                    localStorage.setItem('qt30_tech', JSON.stringify(currentTech));
                    renderDashboard();
                } else {
                    alert(data.message || '登入失敗');
                }
            };

            document.getElementById('registerForm').onsubmit = async (e) => {
                e.preventDefault();
                const payload = {
                    name: document.getElementById('regName').value,
                    phone: document.getElementById('regPhone').value,
                    password: document.getElementById('regPassword').value,
                    skills: document.getElementById('regSkills').value,
                    area: document.getElementById('regArea').value
                };
                const res = await fetch('/api/tech/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.success) {
                    alert('註冊成功！系統已贈送 100 點體驗點數。管理員審核通過後即可搶單。');
                    location.reload();
                } else {
                    alert(data.message || '註冊失敗');
                }
            };

            function renderDashboard() {
                document.getElementById('authBox').classList.add('hidden');
                document.getElementById('dashBox').classList.remove('hidden');
                document.getElementById('userName').innerText = currentTech.name;
                document.getElementById('userSkills').innerText = currentTech.skills || '未填寫';
                document.getElementById('userArea').innerText = currentTech.area || '全區';
                document.getElementById('userPoints').innerText = currentTech.points;
                
                const badge = document.getElementById('userStatusBadge');
                if(currentTech.status === 'approved') {
                    badge.innerText = '✓ 實名認證通過';
                    badge.className = 'text-xs px-2.5 py-1 rounded-full bg-green-500/20 text-green-400 font-bold';
                } else {
                    badge.innerText = '⏳ 實名審核中 (暫無法搶單)';
                    badge.className = 'text-xs px-2.5 py-1 rounded-full bg-amber-500/20 text-amber-400';
                }
                loadOrders();
            }

            function showSection(sec) {
                if(sec === 'orders') {
                    document.getElementById('sectionOrders').classList.remove('hidden');
                    document.getElementById('sectionTopup').classList.add('hidden');
                    document.getElementById('btnTabOrders').className = 'text-amber-400 border-b-2 border-amber-400 pb-2';
                    document.getElementById('btnTabTopup').className = 'text-slate-400 pb-2';
                } else {
                    document.getElementById('sectionTopup').classList.remove('hidden');
                    document.getElementById('sectionOrders').classList.add('hidden');
                    document.getElementById('btnTabTopup').className = 'text-amber-400 border-b-2 border-amber-400 pb-2';
                    document.getElementById('btnTabOrders').className = 'text-slate-400 pb-2';
                }
            }

            async function loadOrders() {
                const res = await fetch('/api/orders');
                const orders = await res.json();
                const list = document.getElementById('ordersList');
                list.innerHTML = '';
                if(orders.length === 0) {
                    list.innerHTML = '<div class="text-center py-10 text-slate-500">目前尚無等待報修的案件</div>';
                    return;
                }
                orders.forEach(o => {
                    const isTaken = o.status === 'taken';
                    list.innerHTML += `
                        <div class="bg-slate-800 p-5 rounded-xl border border-slate-700 flex flex-col md:flex-row justify-between items-start md:items-center gap-4">
                            <div class="space-y-1">
                                <div class="flex items-center gap-2">
                                    <span class="bg-blue-600/30 text-blue-400 border border-blue-500/30 text-xs px-2 py-0.5 rounded font-bold">${o.category}</span>
                                    <span class="text-slate-400 text-xs">${o.created_at}</span>
                                </div>
                                <h4 class="text-lg font-bold text-white">${o.description}</h4>
                                <div class="text-sm text-slate-300">地點：${o.address} | 預算：<span class="text-amber-400 font-bold">${o.budget || '依現場報價'}</span></div>
                                ${isTaken ? `<div class="text-xs text-green-400 mt-1">✓ 由師傅 (${o.taken_by}) 承接 | 客戶電話：${o.phone}</div>` : ''}
                            </div>
                            <div>
                                ${isTaken ? '<span class="bg-slate-700 text-slate-400 text-xs px-3 py-1.5 rounded-lg">已被接單</span>' : 
                                `<button onclick="takeOrder(${o.id})" class="bg-green-600 hover:bg-green-700 text-white font-bold px-5 py-2.5 rounded-xl text-sm shadow">⚡ 扣 50 點搶單</button>`}
                            </div>
                        </div>
                    `;
                });
            }

            async function takeOrder(orderId) {
                if(currentTech.status !== 'approved') {
                    alert('您的實名認證仍在審核中，暫時無法搶單。');
                    return;
                }
                if(currentTech.points < 50) {
                    alert('點數餘額不足 50 點，請先前往儲值！');
                    showSection('topup');
                    return;
                }
                if(!confirm('確認要扣除 50 點搶接此案件嗎？搶單成功後將立即取得客戶電話。')) return;

                const res = await fetch('/api/tech/take_order', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone: currentTech.phone, order_id: orderId})
                });
                const data = await res.json();
                if(data.success) {
                    alert('🎉 搶單成功！已為您扣除 50 點，請立即致電客戶洽談施工細節！');
                    currentTech.points = data.points;
                    renderDashboard();
                } else {
                    alert(data.message || '搶單失敗');
                }
            }

            // 發起綠界線上付款
            async function payECPay(amount, points) {
                if(!currentTech) return;
                const res = await fetch('/api/ecpay/create_payment', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        phone: currentTech.phone,
                        amount: amount,
                        points: points
                    })
                });
                const data = await res.json();
                if(data.success) {
                    // 自動建立隱藏 Form 並 POST 到綠界收銀台
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
                } else {
                    alert(data.message || '建立訂單失敗');
                }
            }

            function logout() {
                localStorage.removeItem('qt30_tech');
                location.reload();
            }

            // 自動恢復登入狀態
            const cached = localStorage.getItem('qt30_tech');
            if(cached) {
                currentTech = JSON.parse(cached);
                // 重新同步最新點數與狀態
                fetch('/api/tech/info?phone=' + currentTech.phone).then(r=>r.json()).then(d=>{
                    if(d.success) {
                        currentTech = d.tech;
                        localStorage.setItem('qt30_tech', JSON.stringify(currentTech));
                    }
                    renderDashboard();
                });
            }
        </script>
    </body>
    </html>
    ''')

# ==========================================
# 3. 管理者後台 (/admin)
# ==========================================
@app.route("/admin")
def admin_page():
    return render_template_string('''
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>QT30 平台總後台</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-100 min-h-screen p-4 md:p-8">
        <div id="loginBox" class="max-w-sm mx-auto mt-20 bg-white p-6 rounded-2xl shadow-lg">
            <h1 class="text-xl font-bold text-center mb-4">🔐 QT30 總控制台登入</h1>
            <input type="password" id="adminPwd" placeholder="請輸入後台管理密碼" class="w-full p-3 border rounded-xl mb-4">
            <button onclick="loginAdmin()" class="w-full bg-blue-600 text-white font-bold py-3 rounded-xl">登入後台</button>
        </div>

        <div id="adminPanel" class="hidden max-w-6xl mx-auto space-y-6">
            <div class="bg-white p-6 rounded-2xl shadow flex justify-between items-center">
                <div>
                    <h1 class="text-2xl font-black text-gray-800">QT30 營運總控制台</h1>
                    <p class="text-gray-500 text-sm">全站修繕案件派工監控與師傅實名制 KYC 審查</p>
                </div>
                <div class="flex gap-4">
                    <button onclick="switchTab('orders')" id="tOrders" class="px-4 py-2 rounded-xl bg-blue-600 text-white font-bold text-sm">📋 案件派工管理</button>
                    <button onclick="switchTab('techs')" id="tTechs" class="px-4 py-2 rounded-xl bg-gray-200 text-gray-700 font-bold text-sm">🛡️ 師傅實名審核</button>
                    <button onclick="location.reload()" class="px-3 py-2 text-red-500 text-sm">登出後台</button>
                </div>
            </div>

            <!-- 案件列表 -->
            <div id="tabOrdersContent" class="bg-white p-6 rounded-2xl shadow">
                <h3 class="font-bold text-lg mb-4">客戶報修工單紀錄</h3>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm">
                        <thead class="bg-gray-50 border-b">
                            <tr>
                                <th class="p-3">單號</th>
                                <th class="p-3">客戶姓名</th>
                                <th class="p-3">聯絡電話</th>
                                <th class="p-3">類別</th>
                                <th class="p-3">修繕地址</th>
                                <th class="p-3">報價/預算</th>
                                <th class="p-3">狀態</th>
                                <th class="p-3">承接師傅</th>
                            </tr>
                        </thead>
                        <tbody id="adminOrdersTable"></tbody>
                    </table>
                </div>
            </div>

            <!-- 師傅審核列表 -->
            <div id="tabTechsContent" class="hidden bg-white p-6 rounded-2xl shadow">
                <div class="flex justify-between items-center mb-4">
                    <h3 class="font-bold text-lg">入駐師傅名單與實名審核</h3>
                    <button onclick="loadTechs()" class="text-xs bg-blue-50 text-blue-600 px-3 py-1.5 rounded-lg border border-blue-200">🔄 重新整理師傅名單</button>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm">
                        <thead class="bg-gray-50 border-b">
                            <tr>
                                <th class="p-3">師傅姓名</th>
                                <th class="p-3">手機帳號 / 專長</th>
                                <th class="p-3">服務地區</th>
                                <th class="p-3">點數餘額</th>
                                <th class="p-3">審核狀態</th>
                                <th class="p-3">審核操作</th>
                            </tr>
                        </thead>
                        <tbody id="adminTechsTable"></tbody>
                    </table>
                </div>
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

            function switchTab(t) {
                if(t === 'orders') {
                    document.getElementById('tabOrdersContent').classList.remove('hidden');
                    document.getElementById('tabTechsContent').classList.add('hidden');
                    document.getElementById('tOrders').className = 'px-4 py-2 rounded-xl bg-blue-600 text-white font-bold text-sm';
                    document.getElementById('tTechs').className = 'px-4 py-2 rounded-xl bg-gray-200 text-gray-700 font-bold text-sm';
                } else {
                    document.getElementById('tabTechsContent').classList.remove('hidden');
                    document.getElementById('tabOrdersContent').classList.add('hidden');
                    document.getElementById('tTechs').className = 'px-4 py-2 rounded-xl bg-blue-600 text-white font-bold text-sm';
                    document.getElementById('tOrders').className = 'px-4 py-2 rounded-xl bg-gray-200 text-gray-700 font-bold text-sm';
                    loadTechs();
                }
            }

            async function loadAdminData() {
                const res = await fetch('/api/orders');
                const orders = await res.json();
                const tbody = document.getElementById('adminOrdersTable');
                tbody.innerHTML = '';
                orders.forEach(o => {
                    tbody.innerHTML += `
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-3 font-mono text-xs">#${o.id}</td>
                            <td class="p-3 font-bold">${o.name}</td>
                            <td class="p-3 font-mono">${o.phone}</td>
                            <td class="p-3"><span class="bg-blue-100 text-blue-700 text-xs px-2 py-0.5 rounded font-bold">${o.category}</span></td>
                            <td class="p-3">${o.address}</td>
                            <td class="p-3 text-amber-600 font-bold">${o.budget || '面議'}</td>
                            <td class="p-3">${o.status === 'taken' ? '<span class="text-green-600 font-bold">已接單</span>' : '<span class="text-amber-500 font-bold">待接單</span>'}</td>
                            <td class="p-3 font-mono">${o.taken_by || '-'}</td>
                        </tr>
                    `;
                });
            }

            async function loadTechs() {
                const res = await fetch('/api/admin/techs?pwd=' + adminToken);
                const techs = await res.json();
                const tbody = document.getElementById('adminTechsTable');
                tbody.innerHTML = '';
                techs.forEach(t => {
                    const isApp = t.status === 'approved';
                    tbody.innerHTML += `
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-3 font-bold">${t.name}</td>
                            <td class="p-3">
                                <div class="font-mono text-xs text-gray-600">${t.phone}</div>
                                <div class="text-xs text-blue-600">${t.skills || '未填'}</div>
                            </td>
                            <td class="p-3 text-xs">${t.area || '全區'}</td>
                            <td class="p-3 font-black text-amber-600 font-mono">${t.points} 點</td>
                            <td class="p-3">
                                <span class="text-xs px-2 py-1 rounded font-bold ${isApp ? 'bg-green-100 text-green-700' : 'bg-amber-100 text-amber-700'}">
                                    ${isApp ? '已通過' : '待審核'}
                                </span>
                            </td>
                            <td class="p-3">
                                ${!isApp ? `<button onclick="approveTech('${t.phone}', 'approved')" class="bg-green-600 text-white text-xs font-bold px-3 py-1.5 rounded-lg mr-2">✓ 通過</button>` : ''}
                                <button onclick="approveTech('${t.phone}', 'rejected')" class="bg-red-500 text-white text-xs font-bold px-3 py-1.5 rounded-lg">✕ 拒絕</button>
                            </td>
                        </tr>
                    `;
                });
            }

            async function approveTech(phone, status) {
                const res = await fetch('/api/admin/approve_tech', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone, status, pwd: adminToken})
                });
                const data = await res.json();
                if(data.success) {
                    alert('操作成功！');
                    loadTechs();
                }
            }
        </script>
    </body>
    </html>
    ''', admin_pwd=ADMIN_PASSWORD)

# ==========================================
# 4. 後端 API 集合
# ==========================================

# 客戶送出報修單
@app.route("/api/orders", methods=["GET", "POST"])
def api_orders():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if request.method == "POST":
        d = request.json
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        c.execute('''
            INSERT INTO orders (name, phone, address, category, description, budget, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (d['name'], d['phone'], d['address'], d['category'], d['description'], d.get('budget', ''), now_str))
        order_id = c.lastrowid
        conn.commit()
        conn.close()

        # LINE 推播通知總管理員
        msg = f"🔔 【QT30 新修繕案件通報！】\n單號：#{order_id}\n客戶：{d['name']} ({d['phone']})\n類別：{d['category']}\n地點：{d['address']}\n說明：{d['description']}\n預算：{d.get('budget', '面議')}"
        send_line_push_message(msg)
        return jsonify({"success": True, "order_id": order_id})

    # GET
    c.execute("SELECT id, name, phone, address, category, description, budget, status, taken_by, created_at FROM orders ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "name": r[1], "phone": r[2], "address": r[3],
            "category": r[4], "description": r[5], "budget": r[6],
            "status": r[7], "taken_by": r[8], "created_at": r[9]
        })
    return jsonify(result)

# 師傅註冊
@app.route("/api/tech/register", methods=["POST"])
def api_tech_register():
    d = request.json
    phone = d['phone'].strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        c.execute('''
            INSERT INTO technicians (phone, password, name, skills, area, points, status, created_at)
            VALUES (?, ?, ?, ?, ?, 100, 'pending', ?)
        ''', (phone, d['password'], d['name'], d.get('skills', ''), d.get('area', ''), now_str))
        conn.commit()
        conn.close()

        # LINE 推播通知總管理員審核
        msg = f"🛡️ 【QT30 新師傅實名註冊審核】\n姓名：{d['name']}\n手機：{phone}\n專長：{d.get('skills', '未填')}\n地區：{d.get('area', '全區')}\n\n請前往總控制台 /admin 進行核准。"
        send_line_push_message(msg)
        return jsonify({"success": True})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "message": "此手機號碼已經註冊過！"})

# 師傅登入
@app.route("/api/tech/login", methods=["POST"])
def api_tech_login():
    d = request.json
    phone = d['phone'].strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT phone, name, skills, area, points, status FROM technicians WHERE phone=? AND password=?", (phone, d['password']))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({
            "success": True,
            "tech": {
                "phone": row[0], "name": row[1], "skills": row[2],
                "area": row[3], "points": row[4], "status": row[5]
            }
        })
    return jsonify({"success": False, "message": "帳號或密碼錯誤！"})

# 獲取師傅資訊
@app.route("/api/tech/info")
def api_tech_info():
    phone = request.args.get("phone", "").strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT phone, name, skills, area, points, status FROM technicians WHERE phone=?", (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({
            "success": True,
            "tech": {
                "phone": row[0], "name": row[1], "skills": row[2],
                "area": row[3], "points": row[4], "status": row[5]
            }
        })
    return jsonify({"success": False})

# 師傅扣點搶單
@app.route("/api/tech/take_order", methods=["POST"])
def api_tech_take_order():
    d = request.json
    phone = d['phone']
    order_id = d['order_id']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # 驗證師傅狀態與點數
    c.execute("SELECT name, points, status FROM technicians WHERE phone=?", (phone,))
    t = c.fetchone()
    if not t or t[2] != 'approved':
        conn.close()
        return jsonify({"success": False, "message": "尚未通過實名審核！"})
    if t[1] < 50:
        conn.close()
        return jsonify({"success": False, "message": "點數不足 50 點！"})

    # 檢查案件是否已被搶
    c.execute("SELECT status, name, phone, address FROM orders WHERE id=?", (order_id,))
    o = c.fetchone()
    if not o or o[0] == 'taken':
        conn.close()
        return jsonify({"success": False, "message": "該案件已被其他師傅搶接！"})

    # 扣 50 點並標記案件
    new_points = t[1] - 50
    c.execute("UPDATE technicians SET points=? WHERE phone=?", (new_points, phone))
    c.execute("UPDATE orders SET status='taken', taken_by=? WHERE id=?", (f"{t[0]} ({phone})", order_id))
    conn.commit()
    conn.close()

    # LINE 推播通知
    send_line_push_message(f"⚡ 【QT30 案件已被搶接！】\n單號：#{order_id}\n接單師傅：{t[0]} ({phone})\n師傅剩餘點數：{new_points} 點")
    return jsonify({"success": True, "points": new_points})

# ==========================================
# 5. 綠界金流串接核心 (/api/ecpay/...)
# ==========================================

# 建立綠界信用卡支付訂單
@app.route("/api/ecpay/create_payment", methods=["POST"])
def api_ecpay_create():
    d = request.json
    phone = d['phone']
    amount = int(d['amount'])
    points = int(d['points'])

    # 綠界交易單號 (20字元以內唯一碼)
    trade_no = f"QT{datetime.now().strftime('%Y%m%d%H%M%S')}{int(datetime.now().microsecond/1000):03d}"
    trade_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    # 紀錄至暫存交易表
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("INSERT INTO ecpay_orders VALUES (?, ?, ?, ?, 'unpaid', ?)",
              (trade_no, phone, amount, points, trade_date))
    conn.commit()
    conn.close()

    # 組裝送給綠界的標準參數表
    params = {
        "MerchantID": ECPAY_MERCHANT_ID,
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(amount),
        "TradeDesc": urllib.parse.quote("QT30師傅點數儲值"),
        "ItemName": f"QT30修繕派工點數{points}點",
        "ReturnURL": f"{BASE_URL}/api/ecpay/callback",
        "ClientBackURL": f"{BASE_URL}/tech",
        "OrderResultURL": f"{BASE_URL}/tech",
        "ChoosePayment": "Credit",
        "EncryptType": "1",
        "NeedExtraPaidInfo": "Y",
        "DeviceSource": "M"
    }

    # 產生驗證碼 CheckMacValue
    params["CheckMacValue"] = generate_check_mac_value(params, ECPAY_HASH_KEY, ECPAY_HASH_IV)

    return jsonify({
        "success": True,
        "ecpay_url": ECPAY_API_URL,
        "params": params
    })

# 綠界付款結果背景回傳 Callback (綠界 Server to Server)
@app.route("/api/ecpay/callback", methods=["POST"])
def api_ecpay_callback():
    data = request.form.to_dict()
    received_mac = data.get("CheckMacValue", "")

    # 驗證綠界回傳的 CheckMacValue
    computed_mac = generate_check_mac_value(data, ECPAY_HASH_KEY, ECPAY_HASH_IV)
    if received_mac != computed_mac:
        return "0|CheckMacValue Error"

    rtn_code = data.get("RtnCode", "")
    trade_no = data.get("MerchantTradeNo", "")

    if rtn_code == "1":  # 交易成功
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute("SELECT tech_phone, points, status FROM ecpay_orders WHERE merchant_trade_no=?", (trade_no,))
        order = c.fetchone()
        if order and order[2] != 'paid':
            phone = order[0]
            points_to_add = order[1]
            # 更新訂單狀態
            c.execute("UPDATE ecpay_orders SET status='paid' WHERE merchant_trade_no=?", (trade_no,))
            # 為師傅增加點數
            c.execute("UPDATE technicians SET points = points + ? WHERE phone=?", (points_to_add, phone))
            # 查詢師傅姓名與最新點數
            c.execute("SELECT name, points FROM technicians WHERE phone=?", (phone,))
            tech_info = c.fetchone()
            conn.commit()
            conn.close()

            # LINE 即時推播通報管理員收錢成功
            if tech_info:
                msg = f"💎 【QT30 師傅線上儲值成功！】\n師傅：{tech_info[0]} ({phone})\n儲值金額：NT$ {data.get('TradeAmt')}\n獲得點數：+{points_to_add} 點\n目前總餘額：{tech_info[1]} 點\n交易單號：{trade_no}"
                send_line_push_message(msg)

            return "1|OK"
        conn.close()
        return "1|OK"

    return "1|OK"

# ==========================================
# 6. 管理者審核 API
# ==========================================
@app.route("/api/admin/techs")
def api_admin_techs():
    pwd = request.args.get("pwd", "")
    if pwd != ADMIN_PASSWORD:
        return jsonify([])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, phone, skills, area, points, status FROM technicians ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return jsonify([{"name": r[0], "phone": r[1], "skills": r[2], "area": r[3], "points": r[4], "status": r[5]} for r in rows])

@app.route("/api/admin/approve_tech", methods=["POST"])
def api_admin_approve():
    d = request.json
    if d.get("pwd") != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "無權限"})
    phone = d['phone']
    status = d['status']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE technicians SET status=? WHERE phone=?", (status, phone))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

# ==========================================
# 啟動應用
# ==========================================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
