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
BASE_URL = os.environ.get("BASE_URL", "https://qt30-platform.onrender.com")

DB_FILE = "qt30.db"

# ==========================================
# 資料庫初始化
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            district TEXT DEFAULT '',
            address TEXT,
            category TEXT,
            description TEXT,
            budget INTEGER DEFAULT 0,
            fee_8pct INTEGER DEFAULT 0,
            ref_tech_code TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            taken_by TEXT DEFAULT '',
            work_status TEXT DEFAULT '待開工',
            photo_data TEXT DEFAULT '',
            reward_paid INTEGER DEFAULT 0,
            reward_amount INTEGER DEFAULT 0,
            created_at TEXT
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS technicians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            phone TEXT UNIQUE,
            password TEXT,
            name TEXT,
            skills TEXT,
            area TEXT,
            exclusive_districts TEXT DEFAULT '',
            referral_code TEXT UNIQUE,
            referred_by TEXT DEFAULT '',
            ref_count INTEGER DEFAULT 0,
            points INTEGER DEFAULT 100,
            status TEXT DEFAULT 'approved',
            created_at TEXT
        )
    ''')
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
    <body class="bg-gray-100 min-h-screen flex items-center justify-center p-3">
        <div class="bg-white rounded-2xl shadow-xl w-full max-w-lg overflow-hidden my-4">
            <div class="bg-blue-600 p-5 text-white text-center">
                <h1 class="text-2xl font-black">QT30 房屋修繕預約</h1>
                <p class="text-blue-100 text-xs mt-1">實名認證師傅派工・官方代管監工驗收保障</p>
            </div>
            <form id="orderForm" class="p-6 space-y-4">
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-bold text-gray-700">聯絡姓名</label>
                        <input type="text" id="name" required placeholder="例如：王先生" class="w-full mt-1 p-2.5 border rounded-xl">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-gray-700">聯絡電話</label>
                        <input type="tel" id="phone" required placeholder="例如：0912345678" class="w-full mt-1 p-2.5 border rounded-xl">
                    </div>
                </div>
                <div class="grid grid-cols-3 gap-3">
                    <div>
                        <label class="block text-xs font-bold text-gray-700">行政地區</label>
                        <select id="district" class="w-full mt-1 p-2.5 border rounded-xl bg-white font-bold text-blue-700">
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
                        <label class="block text-xs font-bold text-gray-700">詳細修繕地址</label>
                        <input type="text" id="address" required placeholder="例如：中正路一段 100 號 3 樓" class="w-full mt-1 p-2.5 border rounded-xl">
                    </div>
                </div>
                <div>
                    <label class="block text-xs font-bold text-gray-700">修繕項目類別</label>
                    <select id="category" class="w-full mt-1 p-2.5 border rounded-xl">
                        <option value="水電維修">水電維修 / 衛浴安裝更換</option>
                        <option value="冷氣空調">冷氣清洗 / 檢修安裝</option>
                        <option value="泥作防水">泥作泥工 / 屋頂抓漏防水</option>
                        <option value="油漆粉刷">油漆粉刷 / 壁癌處理</option>
                        <option value="室內裝修">室內裝修 / 門窗鎖具</option>
                    </select>
                </div>
                <div>
                    <label class="block text-xs font-bold text-gray-700">修繕需求說明</label>
                    <textarea id="description" rows="2" required placeholder="請簡單說明故障狀況或施工需求..." class="w-full mt-1 p-2.5 border rounded-xl"></textarea>
                </div>
                <div class="grid grid-cols-2 gap-3">
                    <div>
                        <label class="block text-xs font-bold text-gray-700">預估工程預算 (新台幣)</label>
                        <input type="number" id="budget" required placeholder="例如：10000" oninput="calcFee()" class="w-full mt-1 p-2.5 border rounded-xl font-bold text-amber-600">
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-gray-700">推薦師傅邀請碼 (選填)</label>
                        <input type="text" id="refTechCode" placeholder="若有指定師傅請填碼" class="w-full mt-1 p-2.5 border rounded-xl font-mono text-blue-700 font-bold">
                    </div>
                </div>

                <!-- 8% 監工條款與強化免責 -->
                <div class="bg-blue-50 border border-blue-200 rounded-xl p-3.5 space-y-2">
                    <div class="flex justify-between items-center text-xs">
                        <span class="font-bold text-blue-900">🛡️ 平台專案代管暨 8% 監工服務費：</span>
                        <span class="font-black text-blue-700 text-sm" id="feeDisplay">NT$ 0</span>
                    </div>
                    <p class="text-[11px] text-gray-600 leading-tight">含：施工進度回傳、施工照片存證、行政驗收協調。</p>
                    <div class="flex items-start gap-2 pt-2 border-t border-blue-200">
                        <input type="checkbox" id="agreeTerm" required class="mt-0.5 rounded text-blue-600">
                        <label for="agreeTerm" class="text-[11px] text-gray-700 leading-tight">
                            我已閱讀並同意 <a href="javascript:void(0)" onclick="openModal()" class="text-blue-600 font-bold underline">《QT30 工程代管服務協議與免責聲明》</a>（含 8% 監工計收與監督過失免責條款）。
                        </label>
                    </div>
                </div>

                <button type="submit" class="w-full bg-blue-600 text-white font-bold py-3.5 rounded-xl shadow hover:bg-blue-700 transition">🚀 立即送出預約報修</button>
            </form>
        </div>

        <!-- 協議彈窗 -->
        <div id="termModal" class="hidden fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
            <div class="bg-white rounded-2xl max-w-lg w-full p-6 space-y-4 max-h-[85vh] overflow-y-auto">
                <h3 class="text-lg font-black text-gray-900 border-b pb-2">QT30 工程代管服務協議與免責聲明</h3>
                <div class="text-xs text-gray-600 space-y-2 leading-relaxed">
                    <p><strong>一、 服務性質界定：</strong>本平台所提供之「8% 監工/專案代管服務」，性質僅限於施工進度協調、工程款項託管、施工照片存證及完工行政驗收媒合，非屬法定建築法、民法委任承攬或工程技術法規之實質現場技術監督。</p>
                    <p><strong>二、 完全排除監督與過失責任：</strong>本平台及營運團隊不具備實體指揮監督權限，亦不承擔任何現場安全管理、施工品質保證或過失監督責任。凡因施工瑕疵、工安意外、第三人損害、隱蔽工程隱患、材料劣質或未按圖施工等所衍生之損害賠償或法律糾紛，概由獨立承攬施作之入駐師傅承擔全部民事、刑事與行政賠償責任，委託人與施工方均不得以平台收取專案管理費為由主張本平台負擔連帶或任何賠償責任。</p>
                    <p><strong>三、 驗收與爭議處理：</strong>平台依師傅上傳之施工進度照進行行政結案，不代為承擔實質保固與修繕義務；若衍生履約爭議，平台僅協助提供通訊紀錄與存證照片供雙方調解。</p>
                </div>
                <button onclick="closeModal()" class="w-full bg-blue-600 text-white font-bold py-2.5 rounded-xl">我已了解並同意</button>
            </div>
        </div>

        <script>
            function calcFee() {
                const b = parseFloat(document.getElementById('budget').value) || 0;
                document.getElementById('feeDisplay').innerText = 'NT$ ' + Math.round(b * 0.08).toLocaleString();
            }
            function openModal(){ document.getElementById('termModal').classList.remove('hidden'); }
            function closeModal(){ document.getElementById('termModal').classList.add('hidden'); document.getElementById('agreeTerm').checked = true; }

            document.getElementById('orderForm').addEventListener('submit', async (e) => {
                e.preventDefault();
                if(!document.getElementById('agreeTerm').checked) {
                    alert('請先勾選同意服務協議與免責聲明！');
                    return;
                }
                const b = parseFloat(document.getElementById('budget').value) || 0;
                const payload = {
                    name: document.getElementById('name').value,
                    phone: document.getElementById('phone').value,
                    district: document.getElementById('district').value,
                    address: document.getElementById('address').value,
                    category: document.getElementById('category').value,
                    description: document.getElementById('description').value,
                    ref_tech_code: document.getElementById('refTechCode').value.trim(),
                    budget: b,
                    fee_8pct: Math.round(b * 0.08)
                };
                const res = await fetch('/api/orders', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.success) {
                    alert('🎉 報修預約成功！認證師傅將儘速與您致電確認施工時程。');
                    document.getElementById('orderForm').reset();
                    calcFee();
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
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>QT30 師傅接單與監工工作台</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen">
        <div id="authBox" class="p-6 max-w-md mx-auto">
            <div class="text-center py-6">
                <h1 class="text-3xl font-black text-amber-400">QT30 師傅工作台</h1>
                <p class="text-slate-400 text-sm mt-1">接單派工・施工監工存證・轉介賺點</p>
            </div>
            <div class="bg-slate-800 p-6 rounded-2xl border border-slate-700 shadow-xl space-y-4">
                <div class="flex border-b border-slate-700 pb-2 mb-4">
                    <button id="tabLogin" class="flex-1 text-center font-bold text-amber-400 pb-2 border-b-2 border-amber-400">師傅登入</button>
                    <button id="tabRegister" class="flex-1 text-center font-bold text-slate-400 pb-2">新師傅註冊</button>
                </div>
                <!-- 登入 -->
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
                <!-- 註冊 -->
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
                        <input type="text" id="regSkills" placeholder="例：水電安裝、冷氣清洗、抓漏泥作" class="w-full mt-1 p-2 bg-slate-900 border border-slate-600 rounded-lg text-white">
                    </div>
                    <div>
                        <label class="block text-xs text-slate-300">同行推薦人邀請碼 (選填)</label>
                        <input type="text" id="regRefCode" placeholder="填寫同行推薦碼，推薦人立得 300 點" class="w-full mt-1 p-2 bg-slate-900 border border-slate-600 rounded-lg text-amber-300 font-mono">
                    </div>
                    <button type="submit" class="w-full bg-green-600 hover:bg-green-700 text-white font-bold py-3 rounded-xl transition">📝 註冊開通 (送 100 點體驗金)</button>
                </form>
            </div>
        </div>

        <!-- Dashboard -->
        <div id="dashBox" class="hidden max-w-4xl mx-auto p-4 space-y-6">
            <div class="bg-slate-800 p-5 rounded-2xl border border-slate-700 flex flex-wrap items-center justify-between gap-4">
                <div>
                    <h2 class="text-xl font-bold flex items-center gap-2">
                        <span id="userName"></span> 師傅
                        <span class="text-xs px-2.5 py-1 rounded-full bg-green-500/20 text-green-400 font-bold">✓ 實名認證</span>
                    </h2>
                    <p class="text-slate-400 text-xs mt-1">專長：<span id="userSkills"></span> | 我的推薦碼：<span id="myRefCode" class="text-amber-400 font-bold font-mono"></span> (已推薦：<span id="myRefCount" class="text-white font-bold">0</span> 人)</p>
                    <p class="text-slate-400 text-xs mt-0.5">已卡位區域：<span id="userDistricts" class="text-green-400 font-bold">無</span></p>
                </div>
                <div class="flex items-center gap-4">
                    <div class="text-right">
                        <div class="text-xs text-slate-400">點數餘額</div>
                        <div class="text-2xl font-black text-amber-400"><span id="userPoints">0</span> <span class="text-xs font-normal text-slate-300">點</span></div>
                    </div>
                    <button onclick="showSection('topup')" class="bg-amber-500 text-slate-950 font-bold px-3.5 py-2 rounded-xl text-sm">💳 儲值</button>
                    <button onclick="logout()" class="text-slate-400 hover:text-white text-sm">登出</button>
                </div>
            </div>

            <!-- 分頁列 -->
            <div class="flex border-b border-slate-700 space-x-6 text-sm font-bold">
                <button onclick="showSection('orders')" id="btnTabOrders" class="text-amber-400 border-b-2 border-amber-400 pb-2">📋 派工大廳</button>
                <button onclick="showSection('myProjects')" id="btnTabProjects" class="text-slate-400 pb-2">🛠️ 監工存證回報</button>
                <button onclick="showSection('referral')" id="btnTabReferral" class="text-slate-400 pb-2">🎁 轉介賺點專區</button>
                <button onclick="showSection('lockArea')" id="btnTabLock" class="text-slate-400 pb-2">👑 社區/行政區卡位</button>
                <button onclick="showSection('topup')" id="btnTabTopup" class="text-slate-400 pb-2">💎 線上購點</button>
            </div>

            <!-- 1. 派工大廳 -->
            <div id="sectionOrders" class="space-y-4">
                <div class="flex justify-between items-center">
                    <h3 class="font-bold text-slate-200">待搶修繕工單</h3>
                    <button onclick="loadOrders()" class="text-xs bg-slate-800 px-3 py-1.5 rounded-lg border border-slate-600">🔄 重新整理</button>
                </div>
                <div id="ordersList" class="space-y-3"></div>
            </div>

            <!-- 2. 我的監工進度回報 -->
            <div id="sectionMyProjects" class="hidden space-y-4">
                <h3 class="font-bold text-slate-200">已承接案件・施工存證回報</h3>
                <div id="myProjectsList" class="space-y-3"></div>
            </div>

            <!-- 3. 轉介賺點專區 -->
            <div id="sectionReferral" class="hidden bg-slate-800 p-6 rounded-2xl border border-slate-700 space-y-6">
                <div>
                    <h3 class="text-xl font-bold text-amber-400">🎁 師傅與業主轉介回饋機制</h3>
                    <p class="text-slate-400 text-xs mt-1">多元獲取點數，打造全自動被動回饋網絡！</p>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div class="bg-slate-900 border border-slate-700 p-5 rounded-xl space-y-3">
                        <div class="font-bold text-white text-base">🤝 推薦同行師傅入駐</div>
                        <ul class="text-xs text-slate-300 space-y-1.5 list-disc pl-4">
                            <li>每成功推薦 1 位新師傅註冊：立即獲得 <span class="text-amber-400 font-bold">300 點</span>。</li>
                            <li>累積每滿 5 位師傅：系統加碼額外再送 <span class="text-amber-400 font-bold">1,000 點</span>！</li>
                        </ul>
                        <div class="bg-slate-800 p-3 rounded-lg flex items-center justify-between">
                            <span class="text-xs text-slate-400">您的專屬推薦碼：</span>
                            <span class="text-amber-400 font-bold font-mono text-base" id="cardRefCode"></span>
                        </div>
                    </div>
                    <div class="bg-slate-900 border border-slate-700 p-5 rounded-xl space-y-3">
                        <div class="font-bold text-white text-base">🏠 轉介業主報修需求</div>
                        <p class="text-xs text-slate-300 leading-relaxed">
                            將您的推薦碼提供給業主發案填寫，由平台接手派工並完工後，管理後台可一鍵結算，如有成交將自動反饋點數至該推薦師傅帳戶。
                        </p>
                        <div class="text-[11px] text-amber-400/90 font-medium">※ 註：回饋點數依實際成交金額結算，平台保留最終審核與活動解釋權力。</div>
                    </div>
                </div>
            </div>

            <!-- 4. 社區卡位 -->
            <div id="sectionLockArea" class="hidden bg-slate-800 p-6 rounded-2xl border border-slate-700 space-y-6">
                <div>
                    <h3 class="text-xl font-bold text-amber-400">👑 社區行政區卡位名單（不限人數）</h3>
                    <p class="text-slate-400 text-xs mt-1">門檻說明：凡點數持有或儲值達 <strong>3,000 點以上</strong> 之師傅，即可免費加入卡位名單，享有該區案件優先搶單權限與專屬徽章！</p>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div class="bg-slate-900 border border-slate-700 p-4 rounded-xl space-y-3">
                        <div class="font-bold text-white">📍 新北・淡水區</div>
                        <p class="text-xs text-slate-400">門檻：持有 3,000 點 (無需扣點)</p>
                        <button onclick="joinDistrict('淡水區')" class="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold py-2 rounded-lg text-xs">加入淡水區卡位名單</button>
                    </div>
                    <div class="bg-slate-900 border border-slate-700 p-4 rounded-xl space-y-3">
                        <div class="font-bold text-white">📍 新北・板橋區</div>
                        <p class="text-xs text-slate-400">門檻：持有 3,000 點 (無需扣點)</p>
                        <button onclick="joinDistrict('板橋區')" class="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold py-2 rounded-lg text-xs">加入板橋區卡位名單</button>
                    </div>
                    <div class="bg-slate-900 border border-slate-700 p-4 rounded-xl space-y-3">
                        <div class="font-bold text-white">📍 新北・三重區</div>
                        <p class="text-xs text-slate-400">門檻：持有 3,000 點 (無需扣點)</p>
                        <button onclick="joinDistrict('三重區')" class="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold py-2 rounded-lg text-xs">加入三重區卡位名單</button>
                    </div>
                </div>
            </div>

            <!-- 5. 線上儲值 -->
            <div id="sectionTopup" class="hidden bg-slate-800 p-6 rounded-2xl border border-slate-700 space-y-6">
                <div>
                    <h3 class="text-xl font-bold text-amber-400">💎 師傅在線儲值購點</h3>
                    <p class="text-slate-400 text-xs mt-1">單次派工搶單扣除 50 點。刷卡成功後點數秒級自動入帳。</p>
                </div>
                <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                    <div class="bg-slate-900 border border-slate-700 p-5 rounded-xl text-center space-y-3">
                        <h4 class="font-bold text-slate-200">基礎體驗包</h4>
                        <div class="text-3xl font-black text-amber-400">500 <span class="text-xs font-normal text-slate-400">點</span></div>
                        <button onclick="payECPay(500, 500)" class="w-full bg-amber-500 text-slate-950 font-bold py-2 rounded-lg text-sm">線上刷卡 NT$ 500</button>
                    </div>
                    <div class="bg-slate-900 border border-slate-700 p-5 rounded-xl text-center space-y-3">
                        <h4 class="font-bold text-slate-200">進階實力包</h4>
                        <div class="text-3xl font-black text-amber-400">1,100 <span class="text-xs font-normal text-slate-400">點</span></div>
                        <button onclick="payECPay(1000, 1100)" class="w-full bg-amber-500 text-slate-950 font-bold py-2 rounded-lg text-sm">線上刷卡 NT$ 1,000</button>
                    </div>
                    <div class="bg-slate-900 border-2 border-amber-500 p-5 rounded-xl text-center space-y-3 relative">
                        <span class="absolute -top-3 left-1/2 -translate-x-1/2 bg-amber-500 text-slate-950 text-[10px] font-black px-2 py-0.5 rounded-full">直接達標 3000 點卡位門檻</span>
                        <h4 class="font-bold text-slate-200">區域卡位旗艦包</h4>
                        <div class="text-3xl font-black text-amber-400">3,600 <span class="text-xs font-normal text-slate-400">點</span></div>
                        <button onclick="payECPay(3000, 3600)" class="w-full bg-amber-500 text-slate-950 font-bold py-2 rounded-lg text-sm">線上刷卡 NT$ 3,000</button>
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
                    referral_by: document.getElementById('regRefCode').value.trim()
                };
                const res = await fetch('/api/tech/register', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if(data.success) {
                    alert('🎉 註冊成功！系統已發放 100 點體驗點數。');
                    currentTech = data.tech;
                    localStorage.setItem('qt30_tech', JSON.stringify(currentTech));
                    syncTechInfo();
                } else {
                    alert(data.message || '註冊失敗');
                }
            };

            function renderDashboard() {
                document.getElementById('authBox').classList.add('hidden');
                document.getElementById('dashBox').classList.remove('hidden');
                document.getElementById('userName').innerText = currentTech.name;
                document.getElementById('userSkills').innerText = currentTech.skills || '全項修繕';
                document.getElementById('userPoints').innerText = currentTech.points;
                document.getElementById('myRefCode').innerText = currentTech.referral_code;
                document.getElementById('cardRefCode').innerText = currentTech.referral_code;
                document.getElementById('myRefCount').innerText = currentTech.ref_count || 0;
                document.getElementById('userDistricts').innerText = currentTech.exclusive_districts || '尚未加入任何卡位名單';
                loadOrders();
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
                ['Orders', 'MyProjects', 'Referral', 'LockArea', 'Topup'].forEach(s => {
                    document.getElementById('section' + s).classList.add('hidden');
                    document.getElementById('btnTab' + s).className = 'text-slate-400 pb-2';
                });
                const targetKey = sec.charAt(0).toUpperCase() + sec.slice(1);
                document.getElementById('section' + targetKey).classList.remove('hidden');
                document.getElementById('btnTab' + targetKey).className = 'text-amber-400 border-b-2 border-amber-400 pb-2';
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
                    alert(`🎉 恭喜！您已成功加入【${dist}】卡位專屬名單！`);
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
                const myDists = (currentTech.exclusive_districts || '').split(',');
                if(orders.length === 0) {
                    list.innerHTML = '<div class="text-center py-8 text-slate-500">目前尚無等待承接的修繕案件</div>';
                    return;
                }
                orders.forEach(o => {
                    const isTaken = o.status === 'taken';
                    const isMyArea = myDists.includes(o.district);
                    list.innerHTML += `
                        <div class="bg-slate-800 p-4 rounded-xl border ${isMyArea ? 'border-amber-500' : 'border-slate-700'} flex flex-col md:flex-row justify-between items-start md:items-center gap-3">
                            <div>
                                <div class="flex items-center gap-2">
                                    <span class="bg-blue-600/30 text-blue-400 text-xs px-2 py-0.5 rounded font-bold">${o.category}</span>
                                    <span class="bg-amber-500/20 text-amber-300 text-xs px-2 py-0.5 rounded font-bold">📍 ${o.district}</span>
                                    ${isMyArea ? '<span class="bg-amber-500 text-slate-950 text-[10px] px-2 py-0.5 rounded-full font-bold">👑 您已卡位此區</span>' : ''}
                                </div>
                                <h4 class="text-base font-bold text-white mt-1">${o.description}</h4>
                                <div class="text-xs text-slate-300">地址：${o.address} | 工程預算：<span class="text-amber-400 font-bold">NT$ ${o.budget.toLocaleString()}</span></div>
                            </div>
                            <div>
                                ${isTaken ? '<span class="bg-slate-700 text-slate-400 text-xs px-3 py-1.5 rounded-lg">已被承接</span>' : 
                                `<button onclick="takeOrder(${o.id})" class="bg-green-600 hover:bg-green-700 text-white font-bold px-4 py-2 rounded-xl text-xs">⚡ 扣 50 點搶單</button>`}
                            </div>
                        </div>
                    `;
                });
            }

            async function takeOrder(orderId) {
                if(currentTech.points < 50) {
                    alert('點數餘額不足 50 點，請先前往儲值！');
                    showSection('topup');
                    return;
                }
                if(!confirm('確認要扣除 50 點搶單並解鎖客戶聯絡電話嗎？')) return;

                const res = await fetch('/api/tech/take_order', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({phone: currentTech.phone, order_id: orderId})
                });
                const data = await res.json();
                if(data.success) {
                    alert('🎉 搶單成功！請立即前往「監工存證回報」進行聯絡與進度存證。');
                    syncTechInfo();
                    showSection('myProjects');
                } else {
                    alert(data.message || '搶單失敗');
                }
            }

            async function loadMyProjects() {
                const res = await fetch('/api/orders');
                const orders = await res.json();
                const list = document.getElementById('myProjectsList');
                list.innerHTML = '';
                const myOrders = orders.filter(o => o.taken_by.includes(currentTech.phone));
                if(myOrders.length === 0) {
                    list.innerHTML = '<div class="text-center py-8 text-slate-500">您尚未承接任何修繕工單</div>';
                    return;
                }
                myOrders.forEach(o => {
                    list.innerHTML += `
                        <div class="bg-slate-800 p-5 rounded-xl border border-slate-700 space-y-3">
                            <div class="flex justify-between items-start">
                                <div>
                                    <h4 class="text-base font-bold text-white">#${o.id} - ${o.description}</h4>
                                    <div class="text-xs text-green-400 font-bold mt-1">📞 客戶電話：${o.phone} (${o.name})</div>
                                    <div class="text-xs text-slate-300">地址：${o.district} ${o.address}</div>
                                </div>
                                <span class="bg-blue-600/30 text-blue-300 text-xs px-2.5 py-1 rounded font-bold">進度：${o.work_status}</span>
                            </div>
                            <div class="bg-slate-900 p-3 rounded-lg flex flex-wrap gap-2 items-center justify-between">
                                <div class="flex items-center gap-2">
                                    <span class="text-xs text-slate-400">更新工程進度：</span>
                                    <select id="st_${o.id}" class="bg-slate-800 text-white text-xs p-1.5 rounded border border-slate-600 font-bold">
                                        <option value="備料準備中" ${o.work_status==='備料準備中'?'selected':''}>備料準備中</option>
                                        <option value="施工進行中" ${o.work_status==='施工進行中'?'selected':''}>施工進行中</option>
                                        <option value="已完工待驗收" ${o.work_status==='已完工待驗收'?'selected':''}>已完工待驗收</option>
                                    </select>
                                    <button onclick="updateWorkStatus(${o.id})" class="bg-blue-600 text-white text-xs px-2.5 py-1.5 rounded font-bold">儲存進度</button>
                                </div>
                                <div class="flex items-center gap-2">
                                    <input type="file" id="file_${o.id}" accept="image/*" class="text-xs text-slate-400 file:mr-2 file:py-1 file:px-2 file:rounded file:border-0 file:text-xs file:bg-slate-700 file:text-white">
                                    <button onclick="uploadPhoto(${o.id})" class="bg-amber-500 text-slate-950 font-bold text-xs px-2.5 py-1.5 rounded">📸 上傳施工照</button>
                                </div>
                            </div>
                            ${o.photo_data ? `<div class="mt-2"><span class="text-xs text-slate-400">施工存證相片：</span><img src="${o.photo_data}" class="mt-1 h-28 rounded-lg border border-slate-600 object-cover"></div>` : ''}
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
                if(!file) { alert('請先選擇要上傳的照片！'); return; }
                const reader = new FileReader();
                reader.onload = async (e) => {
                    const res = await fetch('/api/tech/upload_order_photo', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({order_id: id, photo_data: e.target.result})
                    });
                    if((await res.json()).success) { alert('施工照片存證成功上傳！'); loadMyProjects(); }
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
        <title>QT30 營運總控制台</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-gray-100 min-h-screen p-4 md:p-8">
        <div id="loginBox" class="max-w-sm mx-auto mt-20 bg-white p-6 rounded-2xl shadow-lg">
            <h1 class="text-xl font-bold text-center mb-4">🔐 QT30 總控制台</h1>
            <input type="password" id="adminPwd" placeholder="請輸入管理密碼" class="w-full p-3 border rounded-xl mb-4">
            <button onclick="loginAdmin()" class="w-full bg-blue-600 text-white font-bold py-3 rounded-xl">登入後台</button>
        </div>

        <div id="adminPanel" class="hidden max-w-6xl mx-auto space-y-6">
            <div class="bg-white p-6 rounded-2xl shadow flex justify-between items-center">
                <div>
                    <h1 class="text-2xl font-black text-gray-800">QT30 營運總控制台</h1>
                    <p class="text-gray-500 text-sm">全站派工・8% 監工收益・成交轉介點數結算（平台保留最終解釋權）</p>
                </div>
                <div class="flex gap-3">
                    <button onclick="switchTab('orders')" id="tOrders" class="px-4 py-2 rounded-xl bg-blue-600 text-white font-bold text-sm">📋 工單與轉介結算</button>
                    <button onclick="switchTab('techs')" id="tTechs" class="px-4 py-2 rounded-xl bg-gray-200 text-gray-700 font-bold text-sm">🛡️ 師傅卡位與推薦紀錄</button>
                    <button onclick="location.reload()" class="px-3 py-2 text-red-500 text-sm">登出</button>
                </div>
            </div>

            <!-- 工單記錄 -->
            <div id="tabOrdersContent" class="bg-white p-6 rounded-2xl shadow space-y-4">
                <div class="flex justify-between items-center">
                    <div>
                        <h3 class="font-bold text-lg">修繕工單明細與轉介回饋管理</h3>
                        <p class="text-xs text-gray-500 mt-0.5">※ 平台接手派工並完工後，管理員可自訂成交回饋點數一鍵結算撥款至推薦師傅帳戶。</p>
                    </div>
                    <div class="text-sm font-bold bg-blue-50 text-blue-800 px-3 py-1.5 rounded-lg border border-blue-200">
                        預計 8% 監工總收益：<span id="totalFeeDisplay" class="font-black text-blue-600">NT$ 0</span>
                    </div>
                </div>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm">
                        <thead class="bg-gray-50 border-b">
                            <tr>
                                <th class="p-3">單號</th>
                                <th class="p-3">客戶姓名 / 電話</th>
                                <th class="p-3">地區 / 地址</th>
                                <th class="p-3">預算 / 8%監工費</th>
                                <th class="p-3 text-purple-700">推薦師傅碼</th>
                                <th class="p-3">承接進度</th>
                                <th class="p-3">照片</th>
                                <th class="p-3">成交轉介結算</th>
                            </tr>
                        </thead>
                        <tbody id="adminOrdersTable"></tbody>
                    </table>
                </div>
            </div>

            <!-- 師傅管理 -->
            <div id="tabTechsContent" class="hidden bg-white p-6 rounded-2xl shadow">
                <h3 class="font-bold text-lg mb-4">入駐師傅・卡位與推薦紀錄 (滿5人贈1000點)</h3>
                <div class="overflow-x-auto">
                    <table class="w-full text-left text-sm">
                        <thead class="bg-gray-50 border-b">
                            <tr>
                                <th class="p-3">師傅姓名 / 電話</th>
                                <th class="p-3">專屬推薦碼</th>
                                <th class="p-3">推薦人</th>
                                <th class="p-3 text-amber-600 font-bold">成功推薦師傅人數</th>
                                <th class="p-3">卡位行政區</th>
                                <th class="p-3">點數餘額</th>
                                <th class="p-3">操作</th>
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
                    loadAdminData();
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
                let totalFee = 0;
                orders.forEach(o => {
                    totalFee += (o.fee_8pct || 0);
                    const suggestedPts = Math.round((o.fee_8pct || 0) * 0.5);
                    tbody.innerHTML += `
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-3 font-mono text-xs">#${o.id}</td>
                            <td class="p-3 font-bold">${o.name} <div class="text-xs font-mono text-gray-500">${o.phone}</div></td>
                            <td class="p-3"><span class="font-bold text-amber-700">${o.district}</span> ${o.address}</td>
                            <td class="p-3">
                                <div>NT$ ${o.budget.toLocaleString()}</div>
                                <div class="text-xs font-bold text-blue-600">8%監工：NT$ ${(o.fee_8pct||0).toLocaleString()}</div>
                            </td>
                            <td class="p-3 font-mono font-bold text-purple-700">${o.ref_tech_code || '-'}</td>
                            <td class="p-3">
                                <div class="text-xs">${o.taken_by || '<span class="text-amber-500 font-bold">待接單</span>'}</div>
                                <div class="text-xs text-blue-500 font-bold">${o.work_status}</div>
                            </td>
                            <td class="p-3">
                                ${o.photo_data ? `<img src="${o.photo_data}" class="h-10 w-10 rounded object-cover border">` : '<span class="text-gray-400 text-xs">無</span>'}
                            </td>
                            <td class="p-3">
                                ${o.ref_tech_code && !o.reward_paid ? 
                                `<button onclick="settleReferralReward(${o.id}, '${o.ref_tech_code}', ${suggestedPts})" class="bg-purple-600 hover:bg-purple-700 text-white font-bold text-xs px-2.5 py-1.5 rounded-lg shadow">結算成交回饋點數</button>` : 
                                (o.reward_paid ? `<span class="text-green-600 font-bold text-xs">✓ 已結算撥款 (+${o.reward_amount||0}點)</span>` : '-')}
                            </td>
                        </tr>
                    `;
                });
                document.getElementById('totalFeeDisplay').innerText = 'NT$ ' + totalFee.toLocaleString();
            }

            async function settleReferralReward(orderId, refCode, defaultPts) {
                const inputPts = prompt(`【工單 #${orderId} 成交轉介點數結算】\n推薦師傅邀請碼：${refCode}\n請輸入本次欲反饋撥款至該師傅帳戶之點數：`, defaultPts);
                if(inputPts === null || inputPts.trim() === '') return;
                const pts = parseInt(inputPts.trim());
                if(isNaN(pts) || pts <= 0) {
                    alert('請輸入有效的正整數點數！');
                    return;
                }

                if(!confirm(`確認要撥付 【${pts} 點】 至推薦師傅【${refCode}】帳戶嗎？（平台保留最終審核權力）`)) return;

                const res = await fetch('/api/admin/settle_order_referral', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({order_id: orderId, ref_code: refCode, reward_points: pts, pwd: adminToken})
                });
                const d = await res.json();
                if(d.success) {
                    alert(`🎉 成功撥付 ${pts} 點至師傅帳戶！`);
                    loadAdminData();
                } else {
                    alert(d.message || '結算失敗');
                }
            }

            async function loadTechs() {
                const res = await fetch('/api/admin/techs?pwd=' + adminToken);
                const techs = await res.json();
                const tbody = document.getElementById('adminTechsTable');
                tbody.innerHTML = '';
                techs.forEach(t => {
                    tbody.innerHTML += `
                        <tr class="border-b hover:bg-gray-50">
                            <td class="p-3 font-bold">${t.name} <div class="font-mono text-xs text-gray-500">${t.phone}</div></td>
                            <td class="p-3 font-mono font-bold text-amber-600">${t.referral_code}</td>
                            <td class="p-3 font-mono text-xs text-gray-600">${t.referred_by || '-'}</td>
                            <td class="p-3 font-bold text-purple-700 font-mono">${t.ref_count || 0} 位</td>
                            <td class="p-3 text-xs font-bold text-green-600">${t.exclusive_districts || '尚未卡位'}</td>
                            <td class="p-3 font-black text-amber-600 font-mono">${t.points} 點</td>
                            <td class="p-3"><button onclick="approveTech('${t.phone}', 'rejected')" class="bg-red-500 text-white text-xs font-bold px-3 py-1.5 rounded-lg">停權</button></td>
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
                if((await res.json()).success) { alert('操作成功！'); loadTechs(); }
            }
        </script>
    </body>
    </html>
    ''', admin_pwd=ADMIN_PASSWORD)

# ==========================================
# 4. 後端 API 集合
# ==========================================

@app.route("/api/orders", methods=["GET", "POST"])
def api_orders():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if request.method == "POST":
        d = request.json
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        c.execute('''
            INSERT INTO orders (name, phone, district, address, category, description, budget, fee_8pct, ref_tech_code, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (d['name'], d['phone'], d.get('district', '雙北區'), d['address'], d['category'], d['description'], int(d.get('budget', 0)), int(d.get('fee_8pct', 0)), d.get('ref_tech_code', ''), now_str))
        order_id = c.lastrowid
        conn.commit()
        conn.close()

        ref_info = f"\n推薦師傅代碼：{d.get('ref_tech_code')}" if d.get('ref_tech_code') else ""
        msg = f"🔔 【QT30 新修繕案件】\n單號：#{order_id}\n客戶：{d['name']} ({d['phone']})\n地區：{d.get('district', '')}\n預算：NT$ {int(d.get('budget', 0)):,}\n預計8%監工費：NT$ {int(d.get('fee_8pct', 0)):,}{ref_info}"
        send_line_push_message(msg)
        return jsonify({"success": True, "order_id": order_id})

    c.execute("SELECT id, name, phone, district, address, category, description, budget, fee_8pct, ref_tech_code, status, taken_by, work_status, photo_data, reward_paid, reward_amount, created_at FROM orders ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    result = []
    for r in rows:
        result.append({
            "id": r[0], "name": r[1], "phone": r[2], "district": r[3],
            "address": r[4], "category": r[5], "description": r[6],
            "budget": r[7], "fee_8pct": r[8], "ref_tech_code": r[9], "status": r[10],
            "taken_by": r[11], "work_status": r[12], "photo_data": r[13],
            "reward_paid": r[14], "reward_amount": r[15], "created_at": r[16]
        })
    return jsonify(result)

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
            INSERT INTO technicians (phone, password, name, skills, referral_code, referred_by, ref_count, points, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, 100, 'approved', ?)
        ''', (phone, d['password'], d['name'], d.get('skills', ''), ref_code, ref_by, now_str))
        
        # 推薦獎勵：成功推薦 1 人加 300 點；累積滿 5 人再加贈 1000 點
        if ref_by:
            c.execute("SELECT name, phone, ref_count, points FROM technicians WHERE referral_code=?", (ref_by,))
            inviter = c.fetchone()
            if inviter:
                new_ref_count = inviter[2] + 1
                bonus = 300
                extra_msg = ""
                if new_ref_count % 5 == 0:
                    bonus += 1000
                    extra_msg = "（含滿 5 人里程碑加贈 1,000 點！）"
                
                c.execute("UPDATE technicians SET points = points + ?, ref_count = ? WHERE referral_code=?", (bonus, new_ref_count, ref_by))
                send_line_push_message(f"🎁 【推薦師傅獎勵入帳】\n推薦人：{inviter[0]} ({inviter[1]})\n新入駐師傅：{d['name']}\n獲得獎勵：+{bonus} 點 {extra_msg}\n累計推薦人數：{new_ref_count} 人")

        conn.commit()
        conn.close()

        send_line_push_message(f"🛡️ 【新師傅入駐】\n姓名：{d['name']}\n手機：{phone}\n邀請碼：{ref_code}")
        tech_obj = {
            "phone": phone, "name": d['name'], "skills": d.get('skills', ''),
            "referral_code": ref_code, "exclusive_districts": "", "ref_count": 0, "points": 100, "status": "approved"
        }
        return jsonify({"success": True, "tech": tech_obj})
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"success": False, "message": "此手機號碼已經註冊過！"})

@app.route("/api/tech/login", methods=["POST"])
def api_tech_login():
    d = request.json
    phone = d['phone'].strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT phone, name, skills, referral_code, exclusive_districts, ref_count, points, status FROM technicians WHERE phone=? AND password=?", (phone, d['password']))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({
            "success": True,
            "tech": {
                "phone": row[0], "name": row[1], "skills": row[2],
                "referral_code": row[3], "exclusive_districts": row[4],
                "ref_count": row[5], "points": row[6], "status": row[7]
            }
        })
    return jsonify({"success": False, "message": "帳號或密碼錯誤！"})

@app.route("/api/tech/info")
def api_tech_info():
    phone = request.args.get("phone", "").strip()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT phone, name, skills, referral_code, exclusive_districts, ref_count, points, status FROM technicians WHERE phone=?", (phone,))
    row = c.fetchone()
    conn.close()
    if row:
        return jsonify({
            "success": True,
            "tech": {
                "phone": row[0], "name": row[1], "skills": row[2],
                "referral_code": row[3], "exclusive_districts": row[4],
                "ref_count": row[5], "points": row[6], "status": row[7]
            }
        })
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

    send_line_push_message(f"👑 【師傅卡位加入】\n師傅：{t[2]} ({phone})\n卡位區域：{district}\n目前卡位清單：{new_dists}")
    return jsonify({"success": True})

@app.route("/api/tech/take_order", methods=["POST"])
def api_tech_take_order():
    d = request.json
    phone = d['phone']
    order_id = d['order_id']
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    c.execute("SELECT name, points FROM technicians WHERE phone=?", (phone,))
    t = c.fetchone()
    if not t or t[1] < 50:
        conn.close()
        return jsonify({"success": False, "message": "點數不足 50 點！"})

    c.execute("SELECT status, district FROM orders WHERE id=?", (order_id,))
    o = c.fetchone()
    if not o or o[0] == 'taken':
        conn.close()
        return jsonify({"success": False, "message": "該案件已被其他師傅搶接！"})

    new_points = t[1] - 50
    c.execute("UPDATE technicians SET points=? WHERE phone=?", (new_points, phone))
    c.execute("UPDATE orders SET status='taken', taken_by=? WHERE id=?", (f"{t[0]} ({phone})", order_id))
    conn.commit()
    conn.close()

    send_line_push_message(f"⚡ 【工單已被搶接】\n單號：#{order_id}\n師傅：{t[0]} ({phone})\n師傅餘額：{new_points} 點")
    return jsonify({"success": True, "points": new_points})

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

# 業主轉介自訂結算反饋 API
@app.route("/api/admin/settle_order_referral", methods=["POST"])
def api_admin_settle_order_referral():
    d = request.json
    if d.get("pwd") != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "無權限"})
    
    order_id = d['order_id']
    ref_code = d['ref_code']
    reward_pts = int(d['reward_points'])

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, phone FROM technicians WHERE referral_code=?", (ref_code,))
    tech = c.fetchone()
    if not tech:
        conn.close()
        return jsonify({"success": False, "message": "查無該推薦碼師傅！"})

    c.execute("UPDATE technicians SET points = points + ? WHERE referral_code=?", (reward_pts, ref_code))
    c.execute("UPDATE orders SET reward_paid = 1, reward_amount = ? WHERE id=?", (reward_pts, order_id))
    conn.commit()
    conn.close()

    send_line_push_message(f"🎉 【業主發案轉介回饋結算】\n單號：#{order_id}\n獲贈師傅：{tech[0]} ({tech[1]})\n結算點數：+{reward_pts} 點\n已成功反饋至師傅帳戶！（平台保留最終審核權力）")
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
    c.execute("INSERT INTO ecpay_orders VALUES (?, ?, ?, ?, 'unpaid', ?)",
              (trade_no, phone, amount, points, trade_date))
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

# ==========================================
# 6. 管理者 API
# ==========================================
@app.route("/api/admin/techs")
def api_admin_techs():
    pwd = request.args.get("pwd", "")
    if pwd != ADMIN_PASSWORD:
        return jsonify([])
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, phone, referral_code, referred_by, ref_count, exclusive_districts, points, status FROM technicians ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return jsonify([{"name": r[0], "phone": r[1], "referral_code": r[2], "referred_by": r[3], "ref_count": r[4], "exclusive_districts": r[5], "points": r[6], "status": r[7]} for r in rows])

@app.route("/api/admin/approve_tech", methods=["POST"])
def api_admin_approve():
    d = request.json
    if d.get("pwd") != ADMIN_PASSWORD:
        return jsonify({"success": False, "message": "無權限"})
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE technicians SET status=? WHERE phone=?", (d['status'], d['phone']))
    conn.commit()
    conn.close()
    return jsonify({"success": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
