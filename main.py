import os
import time
import urllib.parse
import hashlib
import requests
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

app = FastAPI(title="QT30 派工與金流平台")

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

# 綠界測試金流
ECPAY_MERCHANT_ID = os.getenv("ECPAY_MERCHANT_ID", "2000132")
ECPAY_HASH_KEY = os.getenv("ECPAY_HASH_KEY", "5294y06JbISpM5x9")
ECPAY_HASH_IV = os.getenv("ECPAY_HASH_IV", "v77hoKGq4kWxNNIS")
ECPAY_PAYMENT_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"

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

class CaseUpdate(BaseModel):
    status: Optional[str] = None
    technician: Optional[str] = None
    paymentStatus: Optional[str] = None

cases_db = []

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
        "paymentStatus": "未付款",
        "depositAmount": data.depositAmount or 500,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **data.dict()
    }
    cases_db.insert(0, new_case)

    msg = (
        f"🔔 【QT30 新案件通報】\n"
        f"------------------------\n"
        f"📌 案件編號：{new_case['id']}\n"
        f"👤 報修客戶：{new_case['clientName']}\n"
        f"📞 聯絡電話：{new_case['clientPhone']}\n"
        f"📍 修繕地址：{new_case['address']}\n"
        f"🔧 修繕項目：{new_case['item']}\n"
        f"💰 預估定金：NT$ {new_case['depositAmount']}\n"
        f"📝 案件描述：{new_case['description']}\n"
        f"------------------------\n"
        f"⚡ 系統已建立訂單，可至後台進行派工！"
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
        "TradeDesc": ecpay_url_encode("QT30維修預付定金"),
        "ItemName": f"{target['item']} 預約修繕定金",
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
    <head><title>前往綠界金流支付...</title><meta charset="utf-8"></head>
    <body onload="document.getElementById('ecpay_form').submit();" style="display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;background:#f8fafc;">
        <div style="text-align:center;padding:30px;background:#fff;border-radius:12px;box-shadow:0 4px 6px rgba(0,0,0,0.1);">
            <h2 style="color:#0284c7;">正在安全跳轉至綠界金流收銀台...</h2>
            <p>案件編號：<b>{target['id']}</b> | 定金金額：<b>NT$ {target['depositAmount']}</b></p>
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
                    f"💳 【QT30 案件已付款通知】\n"
                    f"------------------------\n"
                    f"📌 案件編號：{c['id']}\n"
                    f"👤 客戶：{c['clientName']}\n"
                    f"💰 付款金額：NT$ {c['depositAmount']}\n"
                    f"🎉 付款狀態：已完成付款 (綠界測試金流)\n"
                    f"------------------------\n"
                    f"請至派工後台指派師傅！"
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
      <title>QT30 房屋修繕發案平台</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
      <div class="max-w-md mx-auto bg-white rounded-2xl shadow-xl overflow-hidden">
        <div class="bg-blue-600 p-6 text-white text-center">
          <h1 class="text-2xl font-bold">QT30 房屋修繕預約</h1>
          <p class="text-blue-100 text-sm mt-1">填單立即通知師傅，並支援線上支付定金</p>
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
            <label class="block text-sm font-semibold text-gray-700">預約定金 (NT$)</label>
            <input type="number" id="depositAmount" value="500" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-sm font-semibold text-gray-700">狀況描述</label>
            <textarea id="description" rows="3" placeholder="請簡述損壞情況..." class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"></textarea>
          </div>

          <button type="submit" id="submitBtn" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3.5 rounded-lg shadow-md transition duration-200">
            送出預約報修
          </button>
        </form>

        <div id="resultModal" class="hidden p-6 bg-green-50 border-t border-green-200 text-center">
          <h3 class="text-lg font-bold text-green-800">✅ 報修單已成功送出！</h3>
          <p class="text-sm text-gray-600 mt-1">案件編號：<span id="resCaseId" class="font-bold text-blue-600"></span></p>
          <p class="text-xs text-gray-500 mt-1">師傅已收到 LINE 即時推播通知</p>
          <div class="mt-4">
            <a id="payBtn" href="#" class="inline-block w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-3 rounded-lg shadow">
              💳 前往綠界刷卡支付定金 (NT$ <span id="resAmount">500</span>)
            </a>
          </div>
        </div>
      </div>

      <script>
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
            description: document.getElementById('description').value
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
              document.getElementById('resAmount').innerText = result.case.depositAmount;
              document.getElementById('payBtn').href = '/api/pay/' + result.case.id;
            }
          } catch(err) {
            alert('送出失敗，請稍後再試');
            btn.disabled = false;
            btn.innerText = '送出預約報修';
          }
        });
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
      <div class="max-w-6xl mx-auto">
        <header class="flex flex-col sm:flex-row justify-between items-center mb-6 bg-white p-6 rounded-2xl shadow-sm gap-4">
          <div>
            <h1 class="text-2xl font-black text-slate-800">QT30 派工管理後台</h1>
            <p class="text-sm text-slate-500 mt-1">即時掌握修繕案件、付款狀態與師傅派工</p>
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
                  <th class="p-4">修繕項目 / 內容</th>
                  <th class="p-4">定金 / 付款狀態</th>
                  <th class="p-4">派工師傅</th>
                  <th class="p-4">案件狀態</th>
                  <th class="p-4 text-center">操作</th>
                </tr>
              </thead>
              <tbody id="caseTableBody" class="divide-y divide-slate-100">
                <tr><td colspan="7" class="p-8 text-center text-slate-400">載入中...</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <script>
        async function loadCases() {
          const tbody = document.getElementById('caseTableBody');
          try {
            const res = await fetch('/api/cases');
            const data = await res.json();
            if (!data.cases || data.cases.length === 0) {
              tbody.innerHTML = '<tr><td colspan="7" class="p-8 text-center text-slate-400">目前尚無案件</td></tr>';
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
                  <span class="inline-block px-2 py-0.5 bg-slate-100 text-slate-700 rounded text-xs font-semibold">${c.item}</span>
                  <p class="text-xs text-slate-500 mt-1 max-w-xs truncate">${c.description}</p>
                </td>
                <td class="p-4">
                  <div class="font-semibold text-slate-700">NT$ ${c.depositAmount}</div>
                  <span class="inline-block mt-1 px-2 py-0.5 rounded text-xs font-bold ${c.paymentStatus === '已付款' ? 'bg-emerald-100 text-emerald-700' : 'bg-rose-100 text-rose-700'}">
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
                <td class="p-4 text-center">
                  <button onclick="saveCase('${c.id}')" class="bg-slate-800 hover:bg-slate-900 text-white text-xs font-semibold px-3 py-1.5 rounded transition">
                    儲存更新
                  </button>
                </td>
              </tr>
            `).join('');
          } catch(e) {
            tbody.innerHTML = '<tr><td colspan="7" class="p-8 text-center text-rose-500">載入失敗，請重新整理</td></tr>';
          }
        }

        async function saveCase(id) {
          const tech = document.getElementById('tech-' + id).value;
          const status = document.getElementById('status-' + id).value;
          try {
            const res = await fetch('/api/cases/' + id, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ technician: tech, status: status })
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
