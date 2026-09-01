import os
import time
import urllib.parse
import hashlib
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

app = FastAPI(title="QT30 房屋修繕派工接單平台 (正式營運版)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# LINE 金鑰
LINE_CHANNEL_ACCESS_TOKEN = os.getenv(
    "LINE_CHANNEL_ACCESS_TOKEN",
    "Fqylo2CR5nbZX27rp8sg5F7l7Ik4UrvVTPEAxN9l+gpNd2C7V2LBY6NIEakUsBXvZGJ2yq/bzpv0lXLsMrv2C5c6rrG926TAkHnSZkIEZIS1uywU6XJ4waIONGyQxEVq8ff75muOQ4S9wF1mztzz8QdB04t89/1O/w1cDnyilFU="
)
LINE_USER_ID = os.getenv("LINE_USER_ID", "Ub577d92184d6d37ec1b262a1bb72897b")

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

# 綠界正式環境金鑰
ECPAY_MERCHANT_ID = os.getenv("ECPAY_MERCHANT_ID", "3513009")
ECPAY_HASH_KEY = os.getenv("ECPAY_HASH_KEY", "LefLmiHiXMuHMPhA")
ECPAY_HASH_IV = os.getenv("ECPAY_HASH_IV", "Vcz5eQfMDiRe3ZSy")
ECPAY_PAYMENT_URL = "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"

def ecpay_url_encode(s: str) -> str:
    encoded = urllib.parse.quote_plus(s)
    replacements = {
        '%2D': '-', '%5F': '_', '%2E': '.', '%21': '!', '%2A': '*',
        '%28': '(', '%29': ')', '%20': '+'
    }
    for old, new in replacements.items():
        encoded = encoded.replace(old, new)
    return encoded

def generate_check_mac_value(params: dict, hash_key: str, hash_iv: str) -> str:
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    raw_str = f"HashKey={hash_key}&" + "&".join([f"{k}={v}" for k, v in sorted_params]) + f"&HashIV={hash_iv}"
    encoded_str = ecpay_url_encode(raw_str).lower()
    return hashlib.sha256(encoded_str.encode('utf-8')).hexdigest().upper()

class CaseCreate(BaseModel):
    clientName: Optional[str] = "未填寫"
    clientPhone: Optional[str] = "未填寫"
    address: Optional[str] = "未填寫"
    item: Optional[str] = "一般修繕"
    description: Optional[str] = "無詳細描述"
    depositAmount: Optional[int] = 500
    photo: Optional[str] = None

class CaseUpdate(BaseModel):
    status: Optional[str] = None
    technician: Optional[str] = None
    depositAmount: Optional[int] = None
    paymentStatus: Optional[str] = None

class CustomSpotCreate(BaseModel):
    name: str
    lat: float
    lng: float
    radiusKm: Optional[int] = 5
    technicianName: Optional[str] = "王師傅"

cases_db = []
spots_db = [
    {"id": "spot-1", "name": "淡海新市鎮特區", "lat": 25.1956, "lng": 121.4398, "radiusKm": 5, "technicianName": "王師傅 (北部水電)"},
    {"id": "spot-2", "name": "林口三井遠雄生活圈", "lat": 25.0712, "lng": 121.3658, "radiusKm": 6, "technicianName": "張師傅 (泥作防水)"},
    {"id": "spot-3", "name": "竹北高鐵特區", "lat": 24.8085, "lng": 121.0402, "radiusKm": 8, "technicianName": "李師傅 (冷氣空調)"}
]

@app.post("/api/cases")
def create_case(data: CaseCreate):
    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"QT{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    case_id = f"CASE-{trade_no[-6:]}"

    new_case = {
        "id": case_id,
        "tradeNo": trade_no,
        "status": "待派工",
        "technician": "未指派",
        "paymentStatus": "未收款",
        "depositAmount": data.depositAmount or 500,
        "photo": data.photo,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **data.dict()
    }
    cases_db.insert(0, new_case)

    photo_tag = "📷 【已附現場照片】" if data.photo else "📷 【未附現場照片】"

    msg = (
        f"🔔 【QT30 新進預約報修單】\n"
        f"------------------------\n"
        f"📌 案件編號：{new_case['id']}\n"
        f"👤 客戶姓名：{new_case['clientName']}\n"
        f"📞 聯絡電話：{new_case['clientPhone']}\n"
        f"📍 修繕地址：{new_case['address']}\n"
        f"🔧 報修項目：{new_case['item']}\n"
        f"💰 客戶預算：NT$ {new_case['depositAmount']}\n"
        f"📝 狀況描述：{new_case['description']}\n"
        f"{photo_tag}\n"
        f"------------------------\n"
        f"⚡ 請儘速聯繫客戶，確認後可至後台發送付款連結！"
    )
    send_line_notification(msg)
    return {"success": True, "case": new_case}

@app.get("/api/pay/{case_id}", response_class=HTMLResponse)
def get_payment_page(case_id: str, request: Request):
    target = next((c for c in cases_db if c["id"] == case_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="找不到案件")

    base_url = str(request.base_url).rstrip('/')
    trade_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    params = {
        "MerchantID": ECPAY_MERCHANT_ID,
        "MerchantTradeNo": target["tradeNo"],
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(target["depositAmount"]),
        "TradeDesc": ecpay_url_encode("QT30維修工程款"),
        "ItemName": f"{target['item']} 修繕款項",
        "ReturnURL": f"{base_url}/api/ecpay/callback",
        "ClientBackURL": f"{base_url}/app",
        "ChoosePayment": "ALL",
        "EncryptType": "1"
    }

    check_mac = generate_check_mac_value(params, ECPAY_HASH_KEY, ECPAY_HASH_IV)
    params["CheckMacValue"] = check_mac

    inputs_html = "".join([f'<input type="hidden" name="{k}" value="{v}" />' for k, v in params.items()])
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>前往綠界支付...</title><meta charset="utf-8"></head>
    <body onload="document.getElementById('ecpay_form').submit();" style="display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;background:#f8fafc;">
        <div style="text-align:center;padding:30px;background:#fff;border-radius:12px;box-shadow:0 4px 6px rgba(0,0,0,0.1);">
            <h2 style="color:#0284c7;">正在前往綠界官方安全收銀台...</h2>
            <p>案件編號：<b>{target['id']}</b> | 應付金額：<b>NT$ {target['depositAmount']}</b></p>
            <form id="ecpay_form" method="POST" action="{ECPAY_PAYMENT_URL}">
                {inputs_html}
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/api/ecpay/callback")
async def ecpay_callback(request: Request):
    form_data = await request.form()
    data = dict(form_data)
    trade_no = data.get("MerchantTradeNo")
    rtn_code = data.get("RtnCode")

    if rtn_code == "1":
        for c in cases_db:
            if c["tradeNo"] == trade_no:
                c["paymentStatus"] = "已付款"
                msg = (
                    f"🎉 【QT30 款項已成功入帳！】\n"
                    f"------------------------\n"
                    f"📌 案件編號：{c['id']}\n"
                    f"👤 客戶：{c['clientName']}\n"
                    f"💰 入帳金額：NT$ {c['depositAmount']}\n"
                    f"💳 付款狀態：綠界扣款成功\n"
                    f"------------------------\n"
                    f"款項已入帳，請安排師傅前往施工！"
                )
                send_line_notification(msg)
                break
    return "1|OK"

@app.get("/api/cases")
def get_cases():
    return {"success": True, "cases": cases_db}

@app.patch("/api/cases/{case_id}")
def update_case(case_id: str, data: CaseUpdate):
    for c in cases_db:
        if c["id"] == case_id:
            if data.status is not None:
                c["status"] = data.status
            if data.technician is not None:
                c["technician"] = data.technician
            if data.depositAmount is not None:
                c["depositAmount"] = data.depositAmount
            if data.paymentStatus is not None:
                c["paymentStatus"] = data.paymentStatus
            return {"success": True, "case": c}
    raise HTTPException(status_code=404, detail="找不到案件")

# --- 師傅卡位 API ---
@app.get("/api/spots")
def get_spots():
    return {"success": True, "spots": spots_db}

@app.post("/api/spots")
def create_spot(spot: CustomSpotCreate):
    new_spot = {
        "id": f"spot-{int(time.time()*1000)%10000}",
        "name": spot.name,
        "lat": spot.lat,
        "lng": spot.lng,
        "radiusKm": spot.radiusKm,
        "technicianName": spot.technicianName
    }
    spots_db.append(new_spot)
    return {"success": True, "spot": new_spot}

# --- 客戶發案頁面 (/app) ---
@app.get("/app", response_class=HTMLResponse)
def serve_app_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 房屋修繕預約接單</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
      <div class="max-w-md mx-auto bg-white rounded-2xl shadow-xl overflow-hidden">
        <div class="bg-blue-600 p-6 text-white text-center">
          <h1 class="text-2xl font-bold">QT30 房屋修繕預約</h1>
          <p class="text-blue-100 text-sm mt-1">填單立即為您安排專業師傅聯繫報價</p>
        </div>
        
        <form id="caseForm" class="p-6 space-y-4">
          <div>
            <label class="block text-sm font-semibold text-gray-700">聯絡姓名</label>
            <input type="text" id="clientName" required placeholder="例如：王先生" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-sm font-semibold text-gray-700">聯絡電話</label>
            <input type="tel" id="clientPhone" required placeholder="例如：0912345678" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-sm font-semibold text-gray-700">修繕地址</label>
            <input type="text" id="address" required placeholder="例如：新北市淡水區..." class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-sm font-semibold text-gray-700">修繕項目</label>
            <select id="item" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
              <option value="水電維修">水電維修 (水龍頭、漏水、開關)</option>
              <option value="泥作防水">泥作防水 (抓漏、磁磚)</option>
              <option value="冷氣空調">冷氣空調 (清洗、保養、移機)</option>
              <option value="油漆粉刷">油漆粉刷 (室內油漆)</option>
              <option value="裝潢木作">裝潢木作</option>
              <option value="其他綜合修繕">其他綜合修繕</option>
            </select>
          </div>
          <div>
            <label class="block text-sm font-semibold text-gray-700">預算金額 (NT$)</label>
            <input type="number" id="depositAmount" value="500" placeholder="預計修繕預算" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-sm font-semibold text-gray-700">狀況描述</label>
            <textarea id="description" rows="3" placeholder="請簡述損壞情況..." class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"></textarea>
          </div>

          <div>
            <label class="block text-sm font-semibold text-gray-700">上傳現場照片（可拍照或選取照片）</label>
            <input type="file" id="photoInput" accept="image/*" class="w-full mt-1 p-2 border border-dashed border-gray-400 rounded-lg text-sm bg-gray-50 cursor-pointer">
            <div id="previewContainer" class="mt-2 hidden">
              <img id="imagePreview" src="" alt="預覽照片" class="w-full h-40 object-cover rounded-lg border border-gray-200">
            </div>
          </div>

          <button type="submit" id="submitBtn" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3.5 rounded-lg shadow-md transition duration-200">
            送出預約需求
          </button>
        </form>

        <div id="resultModal" class="hidden p-8 bg-green-50 text-center space-y-3">
          <div class="w-16 h-16 bg-green-100 text-green-600 rounded-full flex items-center justify-center mx-auto text-2xl font-bold">✓</div>
          <h3 class="text-xl font-bold text-green-800">預約單已成功送出！</h3>
          <p class="text-sm text-gray-600">您的案件編號：<span id="resCaseId" class="font-mono font-bold text-blue-600"></span></p>
          <div class="bg-white p-4 rounded-xl border border-green-200 text-left text-xs text-gray-600 space-y-1">
            <p>• 系統已即時通知專業師傅。</p>
            <p>• 師傅將會儘速透過電話或 LINE 與您聯絡確認細節與到府時間。</p>
          </div>
          <button onclick="location.reload()" class="mt-4 w-full bg-gray-100 hover:bg-gray-200 text-gray-700 font-semibold py-2.5 rounded-lg text-sm transition">
            再填寫一筆
          </button>
        </div>
      </div>

      <script>
        let base64Photo = null;
        document.getElementById('photoInput').addEventListener('change', function(e) {
          const file = e.target.files[0];
          if (file) {
            const reader = new FileReader();
            reader.onload = function(evt) {
              base64Photo = evt.target.result;
              document.getElementById('imagePreview').src = base64Photo;
              document.getElementById('previewContainer').classList.remove('hidden');
            };
            reader.readAsDataURL(file);
          }
        });

        document.getElementById('caseForm').addEventListener('submit', async (e) => {
          e.preventDefault();
          const btn = document.getElementById('submitBtn');
          btn.disabled = true;
          btn.innerText = '正在送出...';

          const data = {
            clientName: document.getElementById('clientName').value,
            clientPhone: document.getElementById('clientPhone').value,
            address: document.getElementById('address').value,
            item: document.getElementById('item').value,
            depositAmount: parseInt(document.getElementById('depositAmount').value) || 500,
            description: document.getElementById('description').value,
            photo: base64Photo
          };

          try {
            const res = await fetch('/api/cases', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(data)
            });
            const result = await res.json();
            if (result.success) {
              document.getElementById('caseForm').classList.add('hidden');
              document.getElementById('resultModal').classList.remove('hidden');
              document.getElementById('resCaseId').innerText = result.case.id;
            }
          } catch(err) {
            alert('送出失敗，請稍後再試');
            btn.disabled = false;
            btn.innerText = '送出預約需求';
          }
        });
      </script>
    </body>
    </html>
    """

# --- 師傅端工作台 (/tech) 支援地圖點選 + 自訂服務區卡位 ---
@app.get("/tech", response_class=HTMLResponse)
def serve_tech_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 師傅工作台 - 跨區卡位與自訂據點</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
      <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen">
      <!-- 頂部導航 -->
      <header class="bg-slate-800 border-b border-slate-700 p-4 px-6 flex flex-wrap justify-between items-center gap-4">
        <div>
          <div class="text-xs text-slate-400 font-semibold tracking-wide">QT30 全國師傅工作台</div>
          <div class="flex items-center space-x-2 mt-0.5">
            <span class="font-mono text-xs bg-slate-700 text-blue-300 px-2 py-0.5 rounded">0912345678</span>
            <span class="font-bold text-base text-white">王師傅 (北部水電)</span>
          </div>
        </div>
        <div class="flex items-center space-x-4">
          <div class="text-right">
            <span class="text-xs text-slate-400">點數餘額</span>
            <div class="font-black text-amber-400 text-lg">800 <span class="text-xs text-amber-200">點</span></div>
          </div>
          <button class="bg-amber-500 hover:bg-amber-600 text-slate-950 font-black text-xs px-3 py-2 rounded-lg shadow transition">
            儲值 $1,000 卡位
          </button>
        </div>
      </header>

      <main class="max-w-6xl mx-auto p-4 sm:p-6 space-y-6">
        <!-- 說明橫幅 -->
        <div class="bg-gradient-to-r from-blue-900 to-indigo-900 border border-blue-700/50 p-5 rounded-2xl flex flex-col md:flex-row justify-between items-start md:items-center gap-4 shadow-lg">
          <div>
            <h2 class="text-base font-bold text-white flex items-center gap-2">
              📍 全台指標社區「專屬師傅」卡位專區
            </h2>
            <p class="text-xs text-blue-200 mt-1">若地圖上沒有您的目標區域，可直接點擊地圖任意位置「自訂新增服務據點」享 15 分鐘優先接單權！</p>
          </div>
          <button onclick="document.getElementById('addSpotModal').classList.remove('hidden')" class="bg-blue-500 hover:bg-blue-600 text-white font-bold text-xs px-4 py-2.5 rounded-xl shadow transition whitespace-nowrap">
            ➕ 自訂新增服務地區
          </button>
        </div>

        <!-- 地圖與自訂區塊 -->
        <div class="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden shadow-md">
          <div class="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-800/80">
            <span class="text-sm font-bold text-slate-200">🗺️ 服務範圍地圖（點擊地圖即可直接設定新據點）</span>
            <span class="text-xs text-slate-400">已卡位據點以藍色標記顯示</span>
          </div>
          <div id="map" class="h-[450px] w-full bg-slate-950"></div>
        </div>

        <!-- 已卡位地區列表 -->
        <div class="bg-slate-800 border border-slate-700 rounded-2xl p-5 shadow-md">
          <h3 class="text-sm font-bold text-slate-200 mb-3">📋 目前專屬卡位與服務據點一覽</h3>
          <div id="spotList" class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3">
            <div class="p-4 bg-slate-900/50 rounded-xl text-xs text-slate-400 text-center">載入中...</div>
          </div>
        </div>
      </main>

      <!-- 新增據點彈窗 -->
      <div id="addSpotModal" class="hidden fixed inset-0 bg-black/70 flex justify-center items-center z-50 p-4">
        <div class="bg-slate-800 border border-slate-700 rounded-2xl max-w-md w-full p-6 space-y-4">
          <h3 class="text-lg font-bold text-white">📍 新增自訂服務地區 / 卡位</h3>
          <div>
            <label class="block text-xs font-semibold text-slate-300">服務地區 / 社區名稱</label>
            <input type="text" id="newSpotName" placeholder="例如：新北市淡水新市鎮、台中逢甲特區" class="w-full mt-1 p-2.5 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500">
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-semibold text-slate-300">緯度 (Latitude)</label>
              <input type="number" step="0.0001" id="newSpotLat" class="w-full mt-1 p-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white">
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-300">經度 (Longitude)</label>
              <input type="number" step="0.0001" id="newSpotLng" class="w-full mt-1 p-2 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white">
            </div>
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-300">服務範圍半徑 (公里)</label>
            <input type="number" id="newSpotRadius" value="5" class="w-full mt-1 p-2.5 bg-slate-900 border border-slate-700 rounded-lg text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500">
          </div>
          <div class="flex space-x-2 pt-2">
            <button onclick="document.getElementById('addSpotModal').classList.add('hidden')" class="w-1/2 bg-slate-700 hover:bg-slate-600 text-slate-200 font-semibold py-2.5 rounded-lg text-xs transition">
              取消
            </button>
            <button onclick="saveNewSpot()" class="w-1/2 bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 rounded-lg text-xs shadow transition">
              確認卡位並儲存
            </button>
          </div>
        </div>
      </div>

      <script>
        let map;
        let markers = [];

        function initMap() {
          map = L.map('map').setView([24.5, 121.0], 8);
          L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
            attribution: '© OpenStreetMap'
          }).addTo(map);

          // 點擊地圖快速選取座標
          map.on('click', function(e) {
            document.getElementById('newSpotLat').value = e.latlng.lat.toFixed(4);
            document.getElementById('newSpotLng').value = e.latlng.lng.toFixed(4);
            document.getElementById('addSpotModal').classList.remove('hidden');
          });

          loadSpots();
        }

        async function loadSpots() {
          try {
            const res = await fetch('/api/spots');
            const data = await res.json();
            markers.forEach(m => map.removeLayer(m));
            markers = [];

            const listDiv = document.getElementById('spotList');
            if (!data.spots || data.spots.length === 0) {
              listDiv.innerHTML = '<div class="col-span-3 text-center text-slate-500 py-4 text-xs">目前尚無卡位據點，請點擊地圖新增</div>';
              return;
            }

            listDiv.innerHTML = data.spots.map(s => `
              <div class="p-3 bg-slate-900 border border-slate-700/60 rounded-xl flex justify-between items-center">
                <div>
                  <div class="font-bold text-sm text-white">${s.name}</div>
                  <div class="text-xs text-blue-400 mt-0.5">${s.technicianName}</div>
                  <div class="text-[11px] text-slate-500">服務半徑：${s.radiusKm} 公里</div>
                </div>
                <span class="text-xs font-bold text-emerald-400 bg-emerald-950 border border-emerald-800 px-2 py-1 rounded">卡位中</span>
              </div>
            `).join('');

            data.spots.forEach(s => {
              const marker = L.marker([s.lat, s.lng]).addTo(map)
                .bindPopup(`<b>${s.name}</b><br>專屬師傅：${s.technicianName}<br>服務半徑：${s.radiusKm} km`);
              
              const circle = L.circle([s.lat, s.lng], {
                color: '#3b82f6',
                fillColor: '#60a5fa',
                fillOpacity: 0.2,
                radius: s.radiusKm * 1000
              }).addTo(map);

              markers.push(marker, circle);
            });
          } catch(e) {
            console.error(e);
          }
        }

        async function saveNewSpot() {
          const name = document.getElementById('newSpotName').value.trim();
          const lat = parseFloat(document.getElementById('newSpotLat').value);
          const lng = parseFloat(document.getElementById('newSpotLng').value);
          const radius = parseInt(document.getElementById('newSpotRadius').value) || 5;

          if (!name || isNaN(lat) || isNaN(lng)) {
            alert('請輸入地區名稱與有效經緯度！');
            return;
          }

          try {
            const res = await fetch('/api/spots', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                name: name,
                lat: lat,
                lng: lng,
                radiusKm: radius,
                technicianName: "王師傅 (北部水電)"
              })
            });
            const data = await res.json();
            if (data.success) {
              alert('🎉 成功卡位並新增服務地區：' + name);
              document.getElementById('addSpotModal').classList.add('hidden');
              document.getElementById('newSpotName').value = '';
              loadSpots();
            }
          } catch(e) {
            alert('儲存失敗，請稍後再試');
          }
        }

        window.onload = initMap;
      </script>
    </body>
    </html>
    """

# --- 派工管理後台 (/admin) ---
@app.get("/admin", response_class=HTMLResponse)
def serve_admin_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 派工管理後台</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
      <div class="max-w-7xl mx-auto">
        <header class="flex flex-col sm:flex-row justify-between items-center mb-6 bg-white p-6 rounded-2xl shadow-sm gap-4">
          <div>
            <h1 class="text-2xl font-black text-slate-800">QT30 派工管理後台</h1>
            <p class="text-sm text-slate-500 mt-1">掌握案件、照片、修改金額與發送專屬收款連結</p>
          </div>
          <button onclick="loadCases()" class="bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg shadow transition">
            🔄 重新整理清單
          </button>
        </header>

        <div class="bg-white rounded-2xl shadow-sm overflow-hidden border border-slate-200">
          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm text-slate-600">
              <thead class="bg-slate-50 text-slate-700 font-bold border-b border-slate-200">
                <tr>
                  <th class="p-4">案件編號 / 時間</th>
                  <th class="p-4">客戶資訊</th>
                  <th class="p-4">現場照片</th>
                  <th class="p-4">修繕項目 / 內容</th>
                  <th class="p-4">應收金額 / 付款狀態</th>
                  <th class="p-4">派工師傅</th>
                  <th class="p-4">案件狀態</th>
                  <th class="p-4 text-center">操作 / 收款</th>
                </tr>
              </thead>
              <tbody id="caseTableBody" class="divide-y divide-slate-100">
                <tr><td colspan="8" class="p-8 text-center text-slate-400">載入中...</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div id="imgModal" class="hidden fixed inset-0 bg-black bg-opacity-75 flex justify-center items-center z-50 p-4" onclick="this.classList.add('hidden')">
        <img id="modalImg" src="" class="max-w-full max-h-[85vh] rounded-lg shadow-2xl">
      </div>

      <script>
        function viewPhoto(src) {
          document.getElementById('modalImg').src = src;
          document.getElementById('imgModal').classList.remove('hidden');
        }

        function copyPayLink(caseId) {
          const url = window.location.origin + '/api/pay/' + caseId;
          navigator.clipboard.writeText(url).then(() => {
            alert('💳 專屬付款網址已複製！\\n可以透過 LINE 發給客戶刷卡：\\n' + url);
          });
        }

        async function loadCases() {
          const tbody = document.getElementById('caseTableBody');
          try {
            const res = await fetch('/api/cases');
            const data = await res.json();
            if (!data.cases || data.cases.length === 0) {
              tbody.innerHTML = '<tr><td colspan="8" class="p-8 text-center text-slate-400">目前尚無案件</td></tr>';
              return;
            }
            tbody.innerHTML = data.cases.map(c => `
              <tr class="hover:bg-slate-50 transition">
                <td class="p-4">
                  <span class="font-mono font-bold text-blue-600">${c.id}</span>
                  <div class="text-xs text-slate-400 mt-0.5">${c.createdAt}</div>
                </td>
                <td class="p-4">
                  <div class="font-bold text-slate-800">${c.clientName}</div>
                  <div class="text-xs text-slate-500">${c.clientPhone}</div>
                  <div class="text-xs text-slate-400">${c.address}</div>
                </td>
                <td class="p-4">
                  ${c.photo ? `
                    <img src="${c.photo}" onclick="viewPhoto('${c.photo}')" class="w-14 h-14 object-cover rounded-lg border border-slate-200 cursor-pointer hover:opacity-80 transition" title="點擊放大查看">
                  ` : `<span class="text-xs text-slate-300">無照片</span>`}
                </td>
                <td class="p-4">
                  <span class="inline-block px-2 py-0.5 bg-slate-100 text-slate-700 rounded text-xs font-semibold">${c.item}</span>
                  <p class="text-xs text-slate-500 mt-1 max-w-xs truncate">${c.description}</p>
                </td>
                <td class="p-4">
                  <div class="flex items-center space-x-1">
                    <span class="text-xs text-slate-400">NT$</span>
                    <input type="number" id="amt-${c.id}" value="${c.depositAmount}" class="w-20 border border-slate-300 rounded px-1.5 py-0.5 text-xs font-bold text-slate-800 focus:outline-none">
                  </div>
                  <span class="inline-block mt-1 px-2 py-0.5 rounded text-xs font-bold ${c.paymentStatus === '已付款' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}">
                    ${c.paymentStatus}
                  </span>
                </td>
                <td class="p-4">
                  <input type="text" id="tech-${c.id}" value="${c.technician || ''}" placeholder="未指派" class="border border-slate-300 rounded px-2 py-1 text-xs w-24 focus:ring-1 focus:ring-blue-500 focus:outline-none">
                </td>
                <td class="p-4">
                  <select id="status-${c.id}" class="border border-slate-300 rounded px-2 py-1 text-xs focus:ring-1 focus:ring-blue-500 focus:outline-none">
                    <option value="待派工" ${c.status === '待派工' ? 'selected' : ''}>待派工</option>
                    <option value="施工中" ${c.status === '施工中' ? 'selected' : ''}>施工中</option>
                    <option value="已完工" ${c.status === '已完工' ? 'selected' : ''}>已完工</option>
                    <option value="已結案" ${c.status === '已結案' ? 'selected' : ''}>已結案</option>
                  </select>
                </td>
                <td class="p-4 text-center space-y-1">
                  <button onclick="saveCase('${c.id}')" class="block w-full bg-slate-800 hover:bg-slate-900 text-white text-xs font-semibold px-2.5 py-1 rounded transition">
                    💾 儲存修改
                  </button>
                  <button onclick="copyPayLink('${c.id}')" class="block w-full bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold px-2.5 py-1 rounded transition">
                    🔗 複製付款連結
                  </button>
                </td>
              </tr>
            `).join('');
          } catch(e) {
            tbody.innerHTML = '<tr><td colspan="8" class="p-8 text-center text-rose-500">載入失敗，請重新整理</td></tr>';
          }
        }

        async function saveCase(id) {
          const tech = document.getElementById('tech-' + id).value;
          const status = document.getElementById('status-' + id).value;
          const amt = parseInt(document.getElementById('amt-' + id).value) || 500;
          try {
            const res = await fetch('/api/cases/' + id, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ technician: tech, status: status, depositAmount: amt })
            });
            const data = await res.json();
            if (data.success) {
              alert('案件 ' + id + ' 已成功更新金額與指派！');
              loadCases();
            }
          } catch(e) {
            alert('更新失敗');
          }
        }

        loadCases();
      </script>
    </body>
    </html>
    """

@app.get("/", response_class=HTMLResponse)
def serve_home():
    return """
    <script>window.location.href = '/app';</script>
    """
