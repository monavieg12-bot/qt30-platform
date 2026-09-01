import os
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify, render_template_string

app = Flask(__name__)
DB_FILE = "qt30.db"

# ==========================================
# 0. 資料庫初始化
# ==========================================
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 訂單資料表
    c.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            district TEXT,
            address TEXT,
            category TEXT,
            desc TEXT,
            tech_phone TEXT,
            status TEXT DEFAULT 'pending',
            photo_data TEXT,
            created_at TEXT
        )
    """)
    # 師傅資料表
    c.execute("""
        CREATE TABLE IF NOT EXISTS technicians (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT UNIQUE,
            district TEXT,
            referral_code TEXT,
            referrer_code TEXT,
            points INTEGER DEFAULT 100,
            created_at TEXT
        )
    """)
    # 綠界訂單紀錄
    c.execute("""
        CREATE TABLE IF NOT EXISTS ecpay_orders (
            trade_no TEXT PRIMARY KEY,
            phone TEXT,
            amount INTEGER,
            points INTEGER,
            status TEXT DEFAULT 'unpaid',
            trade_date TEXT
        )
    """)
    conn.commit()
    conn.close()

init_db()

# ==========================================
# 1. 前台客戶預約與案例展示 (/app 或 首頁 /)
# ==========================================
APP_HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>淡水房屋修繕推薦｜QT30 雙北居家裝修・水電抓漏工程・8%透明監工</title>
    <meta name="description" content="QT30 提供淡水及雙北地區專業房屋修繕、居家修繕、水電維修、防水抓漏、泥作油漆等統包工程。透明報價、專人監工、師傅即時派工，線上填單快速預約！">
    <meta name="keywords" content="淡水房屋修繕, 淡水水電維修, 淡水抓漏, 雙北居家修繕, 統包裝潢, 泥作油漆, 房屋翻修, QT30">
    <meta name="robots" content="index, follow">
    <link rel="canonical" href="https://qt30home.com/app">

    <!-- Open Graph 社群卡片 -->
    <meta property="og:type" content="website">
    <meta property="og:title" content="淡水房屋修繕推薦｜QT30 雙北居家裝修・水電抓漏工程">
    <meta property="og:description" content="淡水及雙北在地房屋修繕平台，水電、防水、泥作、裝修一鍵預約，專業監工品質保證。">
    <meta property="og:url" content="https://qt30home.com/app">

    <!-- 結構化資料 -->
    <script type="application/ld+json">
    {
      "@context": "https://schema.org",
      "@type": "HomeAndConstructionBusiness",
      "name": "QT30 房屋修繕平台",
      "url": "https://qt30home.com/app",
      "description": "雙北及淡水地區專業房屋修繕、水電、泥作、防水工程與監工服務",
      "areaServed": [{"@type": "AdministrativeArea", "name": "淡水區"}, {"@type": "AdministrativeArea", "name": "雙北地區"}],
      "priceRange": "$$"
    }
    </script>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 min-h-screen pb-12">
    <!-- 頂部導航 -->
    <header class="bg-white border-b border-gray-200 sticky top-0 z-30">
        <div class="max-w-xl mx-auto px-4 py-3 flex justify-between items-center">
            <h1 class="text-xl font-black text-blue-700 tracking-wider">QT30 房屋修繕</h1>
            <div class="space-x-3 text-sm">
                <button onclick="openAiModal()" class="text-indigo-600 font-semibold bg-indigo-50 px-3 py-1.5 rounded-lg border border-indigo-100">🤖 AI 行情諮詢</button>
                <a href="/tech" class="text-gray-600 font-medium hover:text-blue-600">師傅端</a>
            </div>
        </div>
    </header>

    <main class="max-w-xl mx-auto px-4 mt-6 space-y-6">
        <!-- 報修表單 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <h2 class="text-lg font-bold text-gray-800 mb-1">🏠 線上快速報修預約</h2>
            <p class="text-xs text-gray-500 mb-4">專人透明監工，填單後師傅即時接單報價</p>
            
            <form id="orderForm" onsubmit="submitOrder(event)" class="space-y-3 text-sm">
                <div>
                    <label class="block text-gray-700 font-medium mb-1">聯絡姓名</label>
                    <input type="text" id="cust_name" required class="w-full border border-gray-300 rounded-lg p-2.5 focus:ring-2 focus:ring-blue-500 outline-none" placeholder="例如：陳先生 / 林小姐">
                </div>
                <div>
                    <label class="block text-gray-700 font-medium mb-1">聯絡電話</label>
                    <input type="tel" id="cust_phone" required class="w-full border border-gray-300 rounded-lg p-2.5 focus:ring-2 focus:ring-blue-500 outline-none" placeholder="例如：0912345678">
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-gray-700 font-medium mb-1">行政區域</label>
                        <select id="cust_district" class="w-full border border-gray-300 rounded-lg p-2.5 outline-none bg-white">
                            <option value="淡水區">淡水區</option>
                            <option value="八里區">八里區</option>
                            <option value="三芝區">三芝區</option>
                            <option value="板橋區">板橋區</option>
                            <option value="三重區">三重區</option>
                            <option value="新莊區">新莊區</option>
                            <option value="台北市區">台北市區</option>
                            <option value="其他雙北區">其他雙北區</option>
                        </select>
                    </div>
                    <div>
                        <label class="block text-gray-700 font-medium mb-1">修繕類別</label>
                        <select id="cust_category" class="w-full border border-gray-300 rounded-lg p-2.5 outline-none bg-white">
                            <option value="水電維修">水電維修</option>
                            <option value="防水抓漏">防水抓漏</option>
                            <option value="衛浴整修">衛浴整修</option>
                            <option value="油漆粉刷">油漆粉刷</option>
                            <option value="泥作裝修">泥作裝修</option>
                            <option value="其他工程">其他統包</option>
                        </select>
                    </div>
                </div>
                <div>
                    <label class="block text-gray-700 font-medium mb-1">詳細地址</label>
                    <input type="text" id="cust_address" required class="w-full border border-gray-300 rounded-lg p-2.5 outline-none" placeholder="例如：新市一路三段 100 號">
                </div>
                <div>
                    <label class="block text-gray-700 font-medium mb-1">修繕需求說明</label>
                    <textarea id="cust_desc" rows="3" required class="w-full border border-gray-300 rounded-lg p-2.5 outline-none" placeholder="請描述修繕狀況（例如水管漏水、壁癌位置等）"></textarea>
                </div>
                <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-xl shadow transition">送出預約報修</button>
            </form>
        </div>

        <!-- 即時修繕案例展示牆 (SEO 核心) -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-100">
            <div class="flex items-center justify-between mb-4">
                <div>
                    <h2 class="text-base font-bold text-gray-800 flex items-center">
                        <span class="mr-1.5">🛠️</span> 雙北・淡水修繕施工實績
                    </h2>
                    <p class="text-xs text-gray-500">透明監工，以下為近期完工驗收實例</p>
                </div>
                <span class="text-xs bg-emerald-50 text-emerald-600 px-2 py-0.5 rounded-full font-bold border border-emerald-200">即時連線</span>
            </div>

            <div id="casesContainer" class="space-y-3">
                <div class="border border-slate-100 rounded-xl p-3.5 bg-slate-50">
                    <div class="flex justify-between items-center mb-1">
                        <span class="text-xs font-bold text-blue-600 bg-blue-50 px-2 py-0.5 rounded">水電抓漏</span>
                        <span class="text-xs text-gray-500">淡水區・近期</span>
                    </div>
                    <p class="text-xs font-medium text-gray-800">全室暗管加壓檢測與水管局部修復</p>
                    <p class="text-xs text-emerald-600 font-semibold mt-1">✓ 監工合格・已驗收結案</p>
                </div>
            </div>
        </div>
    </main>

    <!-- AI 智慧諮詢彈窗 -->
    <div id="aiModal" class="fixed inset-0 bg-black bg-opacity-50 z-50 hidden flex items-center justify-center p-4">
        <div class="bg-slate-900 text-white w-full max-w-md rounded-2xl p-5 shadow-2xl relative">
            <div class="flex justify-between items-center border-b border-gray-700 pb-3 mb-3">
                <h3 class="font-bold flex items-center"><span class="mr-2">🤖</span> QT30 AI 社區修繕智慧窗口</h3>
                <button onclick="closeAiModal()" class="text-gray-400 hover:text-white text-xl font-bold p-1">✕</button>
            </div>
            <div class="space-y-3 text-xs text-slate-300 max-h-64 overflow-y-auto mb-4 p-1">
                <div class="bg-slate-800 p-2.5 rounded-lg">
                    <span class="text-amber-400 font-semibold">【油漆行情】</span> 20 坪室內批土粉刷行情約 NT$ 30,000 ~ 35,000。
                </div>
                <div class="bg-slate-800 p-2.5 rounded-lg">
                    <span class="text-amber-400 font-semibold">【浴室整修】</span> 1.5 坪衛浴整修（浴缸拆除+防水+地壁磚）行情約 NT$ 75,000 ~ 85,000。
                </div>
            </div>
            <button onclick="closeAiModal()" class="w-full bg-blue-600 hover:bg-blue-700 py-2.5 rounded-lg font-bold text-sm">關閉諮詢視窗</button>
        </div>
    </div>

    <script>
    function openAiModal() { document.getElementById('aiModal').classList.remove('hidden'); }
    function closeAiModal() { document.getElementById('aiModal').classList.add('hidden'); }
    
    // 按 Esc 關閉彈窗
    document.addEventListener('keydown', (e) => { if(e.key === 'Escape') closeAiModal(); });

    async function submitOrder(e) {
        e.preventDefault();
        const payload = {
            name: document.getElementById('cust_name').value,
            phone: document.getElementById('cust_phone').value,
            district: document.getElementById('cust_district').value,
            category: document.getElementById('cust_category').value,
            address: document.getElementById('cust_address').value,
            desc: document.getElementById('cust_desc').value
        };
        const res = await fetch('/api/orders', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        if(data.success) {
            alert('報修單已成功送出！派工人員與師傅將盡快與您聯繫。');
            document.getElementById('orderForm').reset();
            loadPublicCases();
        }
    }

    async function loadPublicCases() {
        try {
            const res = await fetch('/api/public_cases');
            const data = await res.json();
            if(data && data.length > 0) {
                document.getElementById('casesContainer').innerHTML = data.map(item => `
                    <div class="border border-gray-100 rounded-xl p-3.5 bg-gray-50">
                        <div class="flex justify-between items-center mb-1">
                            <span class="text-xs font-bold bg-blue-600 text-white px-2 py-0.5 rounded">${item.category}</span>
                            <span class="text-xs text-gray-500">${item.district}・${item.date}</span>
                        </div>
                        <p class="text-xs font-semibold text-gray-800 mt-1">${item.desc}</p>
                        ${item.photo ? `<img src="${item.photo}" class="w-full h-32 object-cover rounded-lg mt-2 border border-gray-200" />` : ''}
                        <p class="text-xs text-emerald-600 font-semibold mt-1.5">✓ 監工合格・已驗收完工</p>
                    </div>
                `).join('');
            }
        } catch (e) {}
    }
    document.addEventListener('DOMContentLoaded', loadPublicCases);
    </script>
</body>
</html>
"""

# ==========================================
# 2. 師傅端工作台 (/tech)
# ==========================================
TECH_HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QT30 師傅接單工作台</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen p-4 md:p-8">
    <div class="max-w-2xl mx-auto space-y-6">
        <header class="bg-white p-6 rounded-2xl shadow-sm border border-gray-200 flex justify-between items-center">
            <div>
                <h1 class="text-xl font-bold text-gray-800">🛠️ QT30 師傅卡位工作台</h1>
                <p class="text-xs text-gray-500">認領行政區・即時派工搶單・點數扣抵</p>
            </div>
            <a href="/app" class="text-sm text-blue-600 hover:underline">返回客戶端</a>
        </header>

        <!-- 師傅卡位註冊區 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-200">
            <h2 class="font-bold text-gray-800 mb-3 text-sm">👑 師傅卡位認領行政區</h2>
            <form onsubmit="registerTech(event)" class="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
                <input type="text" id="t_name" placeholder="師傅姓名" required class="border p-2.5 rounded-lg outline-none">
                <input type="tel" id="t_phone" placeholder="電話 (帳號)" required class="border p-2.5 rounded-lg outline-none">
                <select id="t_district" class="border p-2.5 rounded-lg outline-none bg-white">
                    <option value="淡水區">淡水區</option>
                    <option value="八里區">八里區</option>
                    <option value="三芝區">三芝區</option>
                    <option value="板橋區">板橋區</option>
                    <option value="三重區">三重區</option>
                    <option value="新莊區">新莊區</option>
                    <option value="台北市區">台北市區</option>
                </select>
                <button type="submit" class="md:col-span-3 bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-2.5 rounded-lg transition">確認卡位入駐</button>
            </form>
        </div>

        <!-- 案件列表 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-200">
            <h2 class="font-bold text-gray-800 mb-3 text-sm">📋 即時可接案件列表</h2>
            <div id="techOrdersList" class="space-y-3">
                <p class="text-xs text-gray-400">載入案件中...</p>
            </div>
        </div>
    </div>

    <script>
    async function registerTech(e) {
        e.preventDefault();
        const payload = {
            name: document.getElementById('t_name').value,
            phone: document.getElementById('t_phone').value,
            district: document.getElementById('t_district').value
        };
        const res = await fetch('/api/tech/register', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
        });
        const data = await res.json();
        alert(data.message);
    }

    async function loadOrders() {
        const res = await fetch('/api/orders');
        const orders = await res.json();
        const container = document.getElementById('techOrdersList');
        if(orders.length === 0) {
            container.innerHTML = '<p class="text-xs text-gray-400">目前尚無待接工單</p>';
            return;
        }
        container.innerHTML = orders.map(o => `
            <div class="border rounded-xl p-4 flex justify-between items-center ${o.status === 'pending' ? 'bg-amber-50/40 border-amber-200' : 'bg-gray-50 border-gray-200'}">
                <div>
                    <span class="text-xs font-bold px-2 py-0.5 rounded ${o.status === 'pending' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'}">#${o.id} ${o.status === 'pending' ? '等待派工' : '已承接'}</span>
                    <span class="text-xs font-bold text-gray-700 ml-2">${o.category}・${o.district}</span>
                    <p class="text-sm font-medium text-gray-800 mt-1">${o.desc}</p>
                </div>
                ${o.status === 'pending' ? `<button onclick="takeOrder(${o.id})" class="bg-blue-600 text-white text-xs font-bold px-4 py-2 rounded-lg hover:bg-blue-700">搶單接案</button>` : `<span class="text-xs text-gray-400 font-semibold">已由師傅承接</span>`}
            </div>
        `).join('');
    }

    async function takeOrder(orderId) {
        const phone = prompt('請輸入您的師傅登記電話進行接單：');
        if(!phone) return;
        const res = await fetch('/api/tech/take_order', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ order_id: orderId, phone: phone })
        });
        const data = await res.json();
        alert(data.message);
        loadOrders();
    }
    document.addEventListener('DOMContentLoaded', loadOrders);
    </script>
</body>
</html>
"""

# ==========================================
# 3. 管理者後台 (/admin)
# ==========================================
ADMIN_HTML = """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QT30 營運總控制台</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 min-h-screen p-4 md:p-8">
    <div class="max-w-5xl mx-auto space-y-6">
        <header class="bg-white p-6 rounded-2xl shadow-sm border border-gray-200 flex justify-between items-center">
            <div>
                <h1 class="text-xl font-bold text-gray-900">QT30 營運總控制台</h1>
                <p class="text-xs text-gray-500">全站派工・8% 監工收益・師傅卡位點數結算</p>
            </div>
            <a href="/app" class="text-sm text-blue-600 hover:underline font-semibold">前台預約頁</a>
        </header>

        <!-- 工單全流程監管 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-200">
            <h2 class="font-bold text-gray-800 mb-4 text-sm flex items-center">📝 工單派發與 8% 收益結算</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-left text-xs">
                    <thead>
                        <tr class="bg-slate-50 text-gray-600 border-b">
                            <th class="p-2.5">單號</th>
                            <th class="p-2.5">客戶姓名/電話</th>
                            <th class="p-2.5">地區/類別</th>
                            <th class="p-2.5">完整地址</th>
                            <th class="p-2.5">承接師傅</th>
                            <th class="p-2.5">狀態</th>
                        </tr>
                    </thead>
                    <tbody id="adminOrdersTable" class="divide-y text-gray-700">
                        <tr><td colspan="6" class="p-4 text-center text-gray-400">載入中...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- 師傅卡位與推薦紀錄 -->
        <div class="bg-white p-6 rounded-2xl shadow-sm border border-gray-200">
            <h2 class="font-bold text-gray-800 mb-4 text-sm">👑 師傅卡位名冊與點數餘額</h2>
            <div class="overflow-x-auto">
                <table class="w-full text-left text-xs">
                    <thead>
                        <tr class="bg-slate-50 text-gray-600 border-b">
                            <th class="p-2.5">師傅姓名/電話</th>
                            <th class="p-2.5">卡位行政區</th>
                            <th class="p-2.5">點數餘額</th>
                            <th class="p-2.5">入駐時間</th>
                        </tr>
                    </thead>
                    <tbody id="adminTechTable" class="divide-y text-gray-700">
                        <tr><td colspan="4" class="p-4 text-center text-gray-400">載入中...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <script>
    async function loadAdminData() {
        // 載入工單
        const resOrders = await fetch('/api/orders');
        const orders = await resOrders.json();
        document.getElementById('adminOrdersTable').innerHTML = orders.map(o => `
            <tr>
                <td class="p-2.5 font-bold">#${o.id}</td>
                <td class="p-2.5">${o.name}<br><span class="text-gray-400">${o.phone}</span></td>
                <td class="p-2.5"><span class="bg-blue-50 text-blue-700 px-1.5 py-0.5 rounded font-bold">${o.category}</span><br>${o.district}</td>
                <td class="p-2.5 text-gray-600">${o.address}</td>
                <td class="p-2.5">${o.tech_phone || '<span class="text-amber-500 font-medium">尚未接單</span>'}</td>
                <td class="p-2.5"><span class="px-2 py-0.5 rounded font-bold ${o.status === 'pending' ? 'bg-amber-100 text-amber-800' : 'bg-emerald-100 text-emerald-800'}">${o.status}</span></td>
            </tr>
        `).join('');

        // 載入師傅名冊
        const resTechs = await fetch('/api/admin/technicians');
        const techs = await resTechs.json();
        document.getElementById('adminTechTable').innerHTML = techs.map(t => `
            <tr>
                <td class="p-2.5 font-bold">${t.name}<br><span class="text-gray-400">${t.phone}</span></td>
                <td class="p-2.5"><span class="bg-purple-50 text-purple-700 px-2 py-0.5 rounded font-bold">${t.district}</span></td>
                <td class="p-2.5 font-bold text-emerald-600">${t.points} 點</td>
                <td class="p-2.5 text-gray-400">${t.created_at}</td>
            </tr>
        `).join('');
    }
    document.addEventListener('DOMContentLoaded', loadAdminData);
    </script>
</body>
</html>
"""

# ==========================================
# 4. 路由與 API 接口
# ==========================================
@app.route("/")
@app.route("/app")
def page_app():
    return render_template_string(APP_HTML)

@app.route("/tech")
def page_tech():
    return render_template_string(TECH_HTML)

@app.route("/admin")
def page_admin():
    return render_template_string(ADMIN_HTML)

# ----------------- 工單與案例 API -----------------
@app.route("/api/orders", methods=["GET", "POST"])
def api_orders():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    if request.method == "POST":
        d = request.json
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
        c.execute("""
            INSERT INTO orders (name, phone, district, address, category, desc, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (d.get("name"), d.get("phone"), d.get("district", "淡水區"), d.get("address"), d.get("category"), d.get("desc"), now_str))
        conn.commit()
        conn.close()
        return jsonify({"success": True})
    else:
        c.execute("SELECT id, name, phone, district, address, category, desc, tech_phone, status, created_at FROM orders ORDER BY id DESC")
        rows = c.fetchall()
        conn.close()
        orders = []
        for r in rows:
            orders.append({
                "id": r[0], "name": r[1], "phone": r[2], "district": r[3],
                "address": r[4], "category": r[5], "desc": r[6], "tech_phone": r[7],
                "status": r[8], "created_at": r[9]
            })
        return jsonify(orders)

@app.route("/api/public_cases", methods=["GET"])
def api_public_cases():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT id, category, district, desc, photo_data, created_at FROM orders ORDER BY id DESC LIMIT 10")
    rows = c.fetchall()
    conn.close()
    cases = []
    for r in rows:
        cases.append({
            "id": r[0], "category": r[1] or "居家修繕", "district": r[2] or "雙北地區",
            "desc": r[3] or "依標準工法完成修繕驗收。", "photo": r[4] or "",
            "date": r[5][:10] if r[5] else "近期"
        })
    return jsonify(cases)

# ----------------- 師傅端 API -----------------
@app.route("/api/tech/register", methods=["POST"])
def api_tech_register():
    d = request.json
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO technicians (name, phone, district, points, created_at)
            VALUES (?, ?, ?, 100, ?)
        """, (d.get("name"), d.get("phone"), d.get("district", "淡水區"), now_str))
        conn.commit()
        msg = f"師傅 {d.get('name')} 註冊卡位成功！贈送 100 點接單點數。"
    except Exception:
        msg = "此電話已註冊過，卡位資料已更新。"
    conn.close()
    return jsonify({"success": True, "message": msg})

@app.route("/api/tech/take_order", methods=["POST"])
def api_tech_take_order():
    d = request.json
    order_id = d.get("order_id")
    phone = d.get("phone")
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT points FROM technicians WHERE phone=?", (phone,))
    tech = c.fetchone()
    if not tech:
        conn.close()
        return jsonify({"success": False, "message": "找不到此師傅帳號，請先在上方完成卡位註冊！"})
    if tech[0] < 50:
        conn.close()
        return jsonify({"success": False, "message": "點數不足（接單需 50 點），請先儲值！"})
    
    # 扣點並綁定接單
    c.execute("UPDATE technicians SET points = points - 50 WHERE phone=?", (phone,))
    c.execute("UPDATE orders SET status='claimed', tech_phone=? WHERE id=? AND status='pending'", (phone, order_id))
    conn.commit()
    conn.close()
    return jsonify({"success": True, "message": "搶單成功！扣除 50 點，請盡快與業主聯繫。"})

@app.route("/api/admin/technicians", methods=["GET"])
def api_admin_technicians():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("SELECT name, phone, district, points, created_at FROM technicians ORDER BY id DESC")
    rows = c.fetchall()
    conn.close()
    return jsonify([{"name": r[0], "phone": r[1], "district": r[2], "points": r[3], "created_at": r[4]} for r in rows])

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
