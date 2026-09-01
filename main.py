import os
import time
import urllib.parse
import hashlib
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

app = FastAPI(title="QT30 房屋修繕派工與金流平台 (坪數試算+監工+郵件備份版)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- LINE 設定 ---
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

# --- Email 備份發送 (monavie.g12@gmail.com) ---
BACKUP_EMAIL = "monavie.g12@gmail.com"
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")

def send_email_backup(subject: str, content_html: str):
    print(f"📧 案件備份存檔至: {BACKUP_EMAIL} | 標題: {subject}")
    if not SMTP_USER or not SMTP_PASS:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = SMTP_USER
        msg["To"] = BACKUP_EMAIL
        msg.attach(MIMEText(content_html, "html", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(SMTP_USER, SMTP_PASS)
            server.sendmail(SMTP_USER, [BACKUP_EMAIL], msg.as_string())
    except Exception as e:
        print(f"Email 發送失敗: {e}")

# --- 綠界正式環境金鑰 ---
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
    item: Optional[str] = "水電維修"
    areaPing: Optional[float] = 0.0
    needSupervision: Optional[bool] = False
    description: Optional[str] = "無詳細描述"
    depositAmount: Optional[int] = 500
    pointsRequired: Optional[int] = 50
    photo: Optional[str] = None

class CaseUpdate(BaseModel):
    status: Optional[str] = None
    technician: Optional[str] = None
    depositAmount: Optional[int] = None
    paymentStatus: Optional[str] = None

cases_db = []

@app.post("/api/cases")
def create_case(data: CaseCreate):
    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"QT{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    case_id = f"CASE-{trade_no[-6:]}"

    pts = max(50, int((data.depositAmount or 500) * 0.03))  # 3% 扣點，低消 50 點

    new_case = {
        "id": case_id,
        "tradeNo": trade_no,
        "status": "待派工",
        "technician": "未指派",
        "paymentStatus": "未付款",
        "depositAmount": data.depositAmount or 500,
        "pointsRequired": pts,
        "areaPing": data.areaPing or 0,
        "needSupervision": data.needSupervision or False,
        "photo": data.photo,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **data.dict()
    }
    cases_db.insert(0, new_case)

    photo_tag = "📷 【已附現場照片】" if data.photo else "📷 【未附現場照片】"
    sup_tag = "👷‍♂️ 【需要平台專業監工 (+8%)]" if data.needSupervision else "👷‍♂️ 【一般派工 (無監工)】"

    msg = (
        f"🔔 【QT30 新進預約報修單】\n"
        f"------------------------\n"
        f"📌 案件編號：{new_case['id']}\n"
        f"👤 客戶姓名：{new_case['clientName']}\n"
        f"📞 聯絡電話：{new_case['clientPhone']}\n"
        f"📍 修繕地址：{new_case['address']}\n"
        f"🔧 報修項目：{new_case['item']} ({new_case['areaPing']} 坪)\n"
        f"{sup_tag}\n"
        f"💰 預估總額：NT$ {new_case['depositAmount']}\n"
        f"🎯 師傅接單扣點：{pts} 點\n"
        f"📝 狀況描述：{new_case['description']}\n"
        f"{photo_tag}\n"
        f"------------------------\n"
        f"📁 案件資料已同步備份至 monavie.g12@gmail.com"
    )
    send_line_notification(msg)

    email_html = f"""
    <h2>【QT30 社區修繕發案備份】</h2>
    <p><b>案件編號：</b> {new_case['id']}</p>
    <p><b>發案時間：</b> {new_case['createdAt']}</p>
    <p><b>客戶姓名：</b> {new_case['clientName']}</p>
    <p><b>聯絡電話：</b> {new_case['clientPhone']}</p>
    <p><b>修繕地址：</b> {new_case['address']}</p>
    <p><b>工項與坪數：</b> {new_case['item']}（{new_case['areaPing']} 坪）</p>
    <p><b>平台監工服務：</b> {'是 (+8%)' if new_case['needSupervision'] else '否'}</p>
    <p><b>預估金額：</b> NT$ {new_case['depositAmount']}</p>
    <p><b>接單扣除點數：</b> {pts} 點</p>
    <p><b>詳細描述：</b> {new_case['description']}</p>
    """
    send_email_backup(f"【QT30 新案件備份】{new_case['id']} - {new_case['clientName']}", email_html)

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
        "TradeDesc": ecpay_url_encode("QT30修繕款項支付"),
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
                    f"🎉 【QT30 款項已入帳！】\n"
                    f"------------------------\n"
                    f"📌 案件編號：{c['id']}\n"
                    f"👤 客戶：{c['clientName']}\n"
                    f"💰 入帳金額：NT$ {c['depositAmount']}\n"
                    f"💳 付款狀態：綠界扣款成功\n"
                    f"------------------------\n"
                    f"款項已入帳，請安排師傅前往施工！"
                )
                send_line_notification(msg)
                send_email_backup(f"【QT30 付款成功通知】{c['id']} - NT$ {c['depositAmount']}", f"<p>案件 {c['id']} 客戶 {c['clientName']} 已完成付款 NT$ {c['depositAmount']}！</p>")
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
                c["pointsRequired"] = max(50, int(data.depositAmount * 0.03))
            if data.paymentStatus is not None:
                c["paymentStatus"] = data.paymentStatus
            return {"success": True, "case": c}
    raise HTTPException(status_code=404, detail="找不到案件")

# --- 客戶發案頁面 (/app) ---
@app.get("/app", response_class=HTMLResponse)
def serve_app_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 房屋修繕智慧預約</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
      <div class="max-w-md mx-auto bg-white rounded-2xl shadow-xl overflow-hidden">
        <div class="bg-blue-600 p-6 text-white text-center">
          <h1 class="text-2xl font-bold">QT30 房屋修繕預約</h1>
          <p class="text-blue-100 text-sm mt-1">精準坪數試算行情，專業師傅快速派工</p>
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
          
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block text-sm font-semibold text-gray-700">修繕項目</label>
              <select id="item" onchange="calculatePrice()" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm">
                <option value="油漆粉刷" data-price="1500">油漆粉刷 ($1,500/坪)</option>
                <option value="泥作防水" data-price="8000">泥作防水 ($8,000/坪)</option>
                <option value="水電維修" data-price="1500">水電檢修 (基礎 $1,500)</option>
                <option value="冷氣清洗" data-price="2000">冷氣清洗 ($2,000/台)</option>
                <option value="裝潢木作" data-price="25000">裝潢木作 ($25,000/坪)</option>
                <option value="其他綜合修繕" data-price="1000">其他綜合修繕</option>
              </select>
            </div>
            <div>
              <label class="block text-sm font-semibold text-gray-700">預估坪數/數量</label>
              <input type="number" id="areaPing" value="10" min="1" oninput="calculatePrice()" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm">
            </div>
          </div>

          <!-- 8% 監工選項 -->
          <div class="bg-blue-50 p-3.5 rounded-xl border border-blue-200">
            <label class="flex items-start space-x-2.5 cursor-pointer">
              <input type="checkbox" id="needSupervision" onchange="calculatePrice()" class="mt-1 w-4 h-4 text-blue-600 rounded focus:ring-blue-500">
              <div class="text-xs text-gray-700">
                <span class="font-bold text-blue-900">加選「平台專業監工與驗收服務」(+8%)</span>
                <p class="text-gray-500 mt-0.5">由平台指派特約工務現場監工、責任驗收與施工把關。</p>
              </div>
            </label>
          </div>

          <!-- 自動預算試算框 -->
          <div class="bg-slate-50 p-4 rounded-xl border border-slate-200">
            <div class="flex justify-between items-center text-sm">
              <span class="text-gray-600 font-medium">系統預估總金額：</span>
              <span class="text-xl font-black text-blue-600">NT$ <span id="displayAmount">15,000</span></span>
            </div>
            <div class="flex justify-between items-center text-xs text-gray-500 mt-1">
              <span>師傅接單媒合點數 (3%)：</span>
              <span class="font-semibold text-slate-700"><span id="displayPoints">450</span> 點</span>
            </div>
            <input type="hidden" id="depositAmount" value="15000">
          </div>

          <div>
            <label class="block text-sm font-semibold text-gray-700">狀況描述</label>
            <textarea id="description" rows="2" placeholder="請簡述損壞情況..." class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"></textarea>
          </div>

          <div>
            <label class="block text-sm font-semibold text-gray-700">上傳現場照片（拍照或選取照片）</label>
            <input type="file" id="photoInput" accept="image/*" class="w-full mt-1 p-2 border border-dashed border-gray-400 rounded-lg text-sm bg-gray-50 cursor-pointer">
            <div id="previewContainer" class="mt-2 hidden">
              <img id="imagePreview" src="" alt="預覽照片" class="w-full h-36 object-cover rounded-lg border border-gray-200">
            </div>
          </div>

          <button type="submit" id="submitBtn" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3.5 rounded-lg shadow-md transition duration-200">
            立即送出修繕預約
          </button>
        </form>

        <div id="resultModal" class="hidden p-8 bg-green-50 text-center space-y-3">
          <div class="w-16 h-16 bg-green-100 text-green-600 rounded-full flex items-center justify-center mx-auto text-2xl font-bold">✓</div>
          <h3 class="text-xl font-bold text-green-800">預約單已成功送出！</h3>
          <p class="text-sm text-gray-600">案件編號：<span id="resCaseId" class="font-mono font-bold text-blue-600"></span></p>
          <div class="bg-white p-4 rounded-xl border border-green-200 text-left text-xs text-gray-600 space-y-1">
            <p>• 系統已即時推播給專業師傅與備份存檔。</p>
            <p>• 師傅將會儘速透過電話或 LINE 與您聯絡確認細節與到府時間。</p>
          </div>
          <button onclick="location.reload()" class="mt-4 w-full bg-gray-100 hover:bg-gray-200 text-gray-700 font-semibold py-2.5 rounded-lg text-sm transition">
            再填寫一筆
          </button>
        </div>
      </div>

      <script>
        let base64Photo = null;

        function calculatePrice() {
          const itemSelect = document.getElementById('item');
          const unitPrice = parseInt(itemSelect.options[itemSelect.selectedIndex].getAttribute('data-price')) || 1500;
          const ping = parseFloat(document.getElementById('areaPing').value) || 1;
          const needSup = document.getElementById('needSupervision').checked;

          let base = Math.round(unitPrice * ping);
          if (base < 1000) base = 1000;
          if (needSup) {
            base = Math.round(base * 1.08); // +8% 監工費
          }

          const points = Math.max(50, Math.round(base * 0.03)); // 3% 扣點
          document.getElementById('displayAmount').innerText = base.toLocaleString();
          document.getElementById('displayPoints').innerText = points.toLocaleString();
          document.getElementById('depositAmount').value = base;
        }

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
            areaPing: parseFloat(document.getElementById('areaPing').value) || 0,
            needSupervision: document.getElementById('needSupervision').checked,
            depositAmount: parseInt(document.getElementById('depositAmount').value) || 1000,
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
            btn.innerText = '立即送出修繕預約';
          }
        });

        calculatePrice();
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
            <p class="text-sm text-slate-500 mt-1">坪數行情、8% 監工、師傅扣點、現場照片與專屬付款連結</p>
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
                  <th class="p-4">工項 / 坪數 / 監工</th>
                  <th class="p-4">預估金額 / 扣點</th>
                  <th class="p-4">付款狀態</th>
                  <th class="p-4">派工師傅</th>
                  <th class="p-4">案件狀態</th>
                  <th class="p-4 text-center">操作</th>
                </tr>
              </thead>
              <tbody id="caseTableBody" class="divide-y divide-slate-100">
                <tr><td colspan="9" class="p-8 text-center text-slate-400">載入中...</td></tr>
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
            alert('💳 專屬付款網址已複製！\\n可直接透過 LINE 發給客戶刷卡：\\n' + url);
          });
        }

        async function loadCases() {
          const tbody = document.getElementById('caseTableBody');
          try {
            const res = await fetch('/api/cases');
            const data = await res.json();
            if (!data.cases || data.cases.length === 0) {
              tbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-slate-400">目前尚無案件</td></tr>';
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
                  <div class="text-xs text-slate-500 mt-1">${c.areaPing || 0} 坪</div>
                  ${c.needSupervision ? `<span class="inline-block mt-1 px-1.5 py-0.5 bg-blue-100 text-blue-700 text-[10px] font-bold rounded">8% 平台監工</span>` : ''}
                </td>
                <td class="p-4">
                  <div class="flex items-center space-x-1">
                    <span class="text-xs text-slate-400">NT$</span>
                    <input type="number" id="amt-${c.id}" value="${c.depositAmount}" class="w-20 border border-slate-300 rounded px-1.5 py-0.5 text-xs font-bold text-slate-800 focus:outline-none">
                  </div>
                  <div class="text-xs text-emerald-600 font-semibold mt-1">扣點：${c.pointsRequired || 50} 點</div>
                </td>
                <td class="p-4">
                  <span class="inline-block px-2 py-0.5 rounded text-xs font-bold ${c.paymentStatus === '已付款' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}">
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
            tbody.innerHTML = '<tr><td colspan="9" class="p-8 text-center text-rose-500">載入失敗，請重新整理</td></tr>';
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
              alert('案件 ' + id + ' 已成功更新！');
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
