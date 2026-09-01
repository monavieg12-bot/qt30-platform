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

app = FastAPI(title="QT30 房屋修繕全功能派工平台 (客戶端+管理後台+師傅接單儲值端)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- LINE 推播設定 ---
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

# --- Email 備份設定 ---
BACKUP_EMAIL = "monavie.g12@gmail.com"
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")

def send_email_backup(subject: str, content_html: str):
    print(f"📧 案件存檔備份: {BACKUP_EMAIL} | 標題: {subject}")
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

# --- 資料結構 ---
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

class TechClaimRequest(BaseModel):
    caseId: str
    techName: str
    techPhone: str

class TopupOrderRequest(BaseModel):
    techPhone: str
    amount: int
    points: int

class ReferralCreate(BaseModel):
    techName: str
    techPhone: str
    clientName: str
    clientPhone: str
    address: str
    estAmount: int
    notes: Optional[str] = "大額統包案轉介"

cases_db = []
referrals_db = []
techs_db = {
    "0912345678": {"name": "王師傅 (水電/防水)", "phone": "0912345678", "points": 800},
    "0988776655": {"name": "阿國師傅 (泥作/木作)", "phone": "0988776655", "points": 1500}
}
topup_orders = {}

# --- 案件相關 API ---
@app.post("/api/cases")
def create_case(data: CaseCreate):
    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"QT{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    case_id = f"CASE-{trade_no[-6:]}"

    pts = max(50, int((data.depositAmount or 500) * 0.03))

    new_case = {
        "id": case_id,
        "tradeNo": trade_no,
        "status": "待派工",
        "technician": "未指派",
        "technicianPhone": "",
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
    sup_tag = "👷‍♂️ 【加選平台專業監工 (+8%)]" if data.needSupervision else "👷‍♂️ 【一般派工 (無監工)】"

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
        f"⚡ 師傅已可在「接單大廳」扣點領單！"
    )
    send_line_notification(msg)

    email_html = f"""
    <h2>【QT30 社區修繕發案備份】</h2>
    <p><b>案件編號：</b> {new_case['id']}</p>
    <p><b>客戶姓名：</b> {new_case['clientName']}（{new_case['clientPhone']}）</p>
    <p><b>修繕地址：</b> {new_case['address']}</p>
    <p><b>工項與坪數：</b> {new_case['item']}（{new_case['areaPing']} 坪）</p>
    <p><b>平台監工：</b> {'是 (+8%)' if new_case['needSupervision'] else '否'}</p>
    <p><b>預估金額：</b> NT$ {new_case['depositAmount']}</p>
    <p><b>接單扣點：</b> {pts} 點</p>
    """
    send_email_backup(f"【QT30 新案件備份】{new_case['id']} - {new_case['clientName']}", email_html)
    return {"success": True, "case": new_case}

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

# --- 師傅端 API (扣點接單、查詢點數、儲值、轉介大案) ---
@app.get("/api/tech/profile")
def get_tech_profile(phone: str):
    if phone not in techs_db:
        techs_db[phone] = {"name": f"師傅 ({phone[-4:]})", "phone": phone, "points": 300}  # 新註冊贈送 300 點體驗
    return {"success": True, "profile": techs_db[phone]}

@app.post("/api/tech/claim")
def claim_case(req: TechClaimRequest):
    case = next((c for c in cases_db if c["id"] == req.caseId), None)
    if not case:
        raise HTTPException(status_code=404, detail="案件不存在")
    if case["status"] != "待派工":
        raise HTTPException(status_code=400, detail="該案件已被其他師傅接單或已結案")

    tech = techs_db.get(req.techPhone)
    if not tech:
        techs_db[req.techPhone] = {"name": req.techName, "phone": req.techPhone, "points": 300}
        tech = techs_db[req.techPhone]

    cost_pts = case.get("pointsRequired", 50)
    if tech["points"] < cost_pts:
        return {"success": False, "msg": f"點數不足！本案需扣 {cost_pts} 點，目前餘額僅 {tech['points']} 點，請先前往儲值。"}

    tech["points"] -= cost_pts
    case["status"] = "施工中"
    case["technician"] = req.techName
    case["technicianPhone"] = req.techPhone

    msg = (
        f"⚡ 【QT30 師傅成功接單通報】\n"
        f"------------------------\n"
        f"📌 案件編號：{case['id']}\n"
        f"🔧 修繕項目：{case['item']}\n"
        f"👷‍♂️ 接單師傅：{req.techName} ({req.techPhone})\n"
        f"💰 已扣除點數：{cost_pts} 點 (師傅剩餘：{tech['points']} 點)\n"
        f"👤 客戶資料已解鎖，請盡速聯繫施工！"
    )
    send_line_notification(msg)
    return {"success": True, "msg": "接單成功！已解鎖客戶完整資訊", "case": case, "remainingPoints": tech["points"]}

# 師傅儲值綠界跳轉
@app.post("/api/tech/topup")
def create_topup_order(req: TopupOrderRequest, request: Request):
    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"TOP{timestamp_str[-9:]}{int(time.time()*1000)%1000:03d}"
    topup_orders[trade_no] = {
        "tradeNo": trade_no,
        "techPhone": req.techPhone,
        "amount": req.amount,
        "points": req.points,
        "status": "未付款"
    }

    base_url = str(request.base_url).rstrip('/')
    trade_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    params = {
        "MerchantID": ECPAY_MERCHANT_ID,
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(req.amount),
        "TradeDesc": ecpay_url_encode("QT30師傅點數儲值"),
        "ItemName": f"QT30 接單點數 {req.points} 點",
        "ReturnURL": f"{base_url}/api/ecpay/topup-callback",
        "ClientBackURL": f"{base_url}/tech",
        "ChoosePayment": "ALL",
        "EncryptType": "1"
    }
    check_mac = generate_check_mac_value(params, ECPAY_HASH_KEY, ECPAY_HASH_IV)
    params["CheckMacValue"] = check_mac

    inputs_html = "".join([f'<input type="hidden" name="{k}" value="{v}" />' for k, v in params.items()])
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>儲值跳轉中...</title><meta charset="utf-8"></head>
    <body onload="document.getElementById('ecpay_topup').submit();" style="display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;background:#f8fafc;">
        <div style="text-align:center;padding:30px;background:#fff;border-radius:12px;box-shadow:0 4px 6px rgba(0,0,0,0.1);">
            <h2 style="color:#0284c7;">正在前往綠界官方安全收銀台 (儲值)...</h2>
            <p>儲值金額：<b>NT$ {req.amount}</b> | 取得點數：<b>{req.points} 點</b></p>
            <form id="ecpay_topup" method="POST" action="{ECPAY_PAYMENT_URL}">
                {inputs_html}
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

@app.post("/api/ecpay/topup-callback")
async def ecpay_topup_callback(request: Request):
    form_data = await request.form()
    data = dict(form_data)
    trade_no = data.get("MerchantTradeNo")
    rtn_code = data.get("RtnCode")

    if rtn_code == "1" and trade_no in topup_orders:
        order = topup_orders[trade_no]
        order["status"] = "已付款"
        phone = order["techPhone"]
        pts = order["points"]
        if phone in techs_db:
            techs_db[phone]["points"] += pts
        else:
            techs_db[phone] = {"name": f"師傅 ({phone[-4:]})", "phone": phone, "points": pts}

        msg = (
            f"💰 【QT30 師傅儲值入帳通知】\n"
            f"------------------------\n"
            f"👷‍♂️ 師傅電話：{phone}\n"
            f"💵 儲值金額：NT$ {order['amount']}\n"
            f"🎁 入帳點數：+{pts} 點\n"
            f"📈 目前總餘額：{techs_db[phone]['points']} 點\n"
            f"------------------------\n"
            f"款項已直接進入綠界帳戶！"
        )
        send_line_notification(msg)
    return "1|OK"

# 師傅轉介大案 API
@app.post("/api/tech/referral")
def create_referral(data: ReferralCreate):
    pts_reward = int(data.estAmount * 0.02)  # 2% 點數回饋
    ref_id = f"REF-{int(time.time())%100000:05d}"
    item = {
        "id": ref_id,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "pointsReward": pts_reward,
        "status": "洽談中",
        **data.dict()
    }
    referrals_db.insert(0, item)

    msg = (
        f"🏆 【QT30 師傅大案轉介回報】\n"
        f"------------------------\n"
        f"👷‍♂️ 轉介師傅：{data.techName} ({data.techPhone})\n"
        f"👤 業主姓名：{data.clientName} ({data.clientPhone})\n"
        f"📍 施工地址：{data.address}\n"
        f"💰 預估大額預算：NT$ {data.estAmount:,}\n"
        f"🎁 預計回饋師傅：{pts_reward:,} 點 (簽約後發放)\n"
        f"📝 案件說明：{data.notes}\n"
        f"------------------------\n"
        f"請官方統包團隊盡速聯繫業主！"
    )
    send_line_notification(msg)
    return {"success": True, "msg": f"轉介回報成功！若簽約成交將發放 {pts_reward:,} 點至您的帳戶", "referral": item}

# 綠界客戶付款跳轉
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
        "TradeDesc": ecpay_url_encode("QT30修繕工程款"),
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
                    f"款項已入帳，請通知師傅施工！"
                )
                send_line_notification(msg)
                break
    return "1|OK"

# ==================== 頁面路由 ====================

# 1. 師傅端專區 (/tech)
@app.get("/tech", response_class=HTMLResponse)
def serve_tech_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 師傅接單與點數中心</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen pb-16">
      
      <!-- 頂部師傅狀態列 -->
      <header class="bg-slate-900 text-white p-4 sticky top-0 z-40 shadow-md">
        <div class="max-w-4xl mx-auto flex justify-between items-center">
          <div>
            <span class="text-xs text-slate-400">QT30 師傅專屬工作台</span>
            <div class="flex items-center space-x-2 mt-0.5">
              <input type="text" id="techPhone" value="0912345678" onchange="loadProfile()" class="bg-slate-800 text-white text-xs px-2 py-1 rounded border border-slate-700 w-28 focus:outline-none focus:border-blue-400" title="輸入手機號碼登入">
              <span id="techNameDisplay" class="font-bold text-sm text-blue-300">王師傅</span>
            </div>
          </div>
          <div class="text-right">
            <div class="text-xs text-slate-400">目前點數餘額</div>
            <div class="text-lg font-black text-amber-400"><span id="ptsBalance">0</span> <span class="text-xs text-slate-300">點</span></div>
          </div>
        </div>
      </header>

      <!-- 頁籤導覽列 -->
      <div class="max-w-4xl mx-auto px-4 mt-4">
        <div class="flex bg-white rounded-xl shadow-sm p-1 border border-slate-200 text-sm font-semibold">
          <button onclick="switchTab('jobs')" id="tabBtn-jobs" class="flex-1 py-2.5 text-center rounded-lg bg-blue-600 text-white shadow-sm transition">⚡ 接單大廳</button>
          <button onclick="switchTab('topup')" id="tabBtn-topup" class="flex-1 py-2.5 text-center rounded-lg text-slate-600 hover:text-blue-600 transition">💰 儲值享折扣</button>
          <button onclick="switchTab('referral')" id="tabBtn-referral" class="flex-1 py-2.5 text-center rounded-lg text-slate-600 hover:text-blue-600 transition">🏆 轉介大案賺點</button>
        </div>
      </div>

      <!-- 主要內容區 -->
      <main class="max-w-4xl mx-auto px-4 mt-4">
        
        <!-- 頁籤 1：接單大廳 -->
        <section id="tab-jobs" class="space-y-4">
          <div class="flex justify-between items-center bg-white p-4 rounded-xl shadow-sm border border-slate-200">
            <div>
              <h2 class="font-bold text-slate-800">最新派工需求</h2>
              <p class="text-xs text-slate-500 mt-0.5">依預估施工金額 3% 扣點，搶單後即刻取得客戶電話</p>
            </div>
            <button onclick="loadJobs()" class="text-xs bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-1.5 rounded-lg font-semibold transition">🔄 刷新</button>
          </div>

          <div id="jobList" class="space-y-3">
            <div class="bg-white p-8 rounded-xl text-center text-slate-400">載入案件中...</div>
          </div>
        </section>

        <!-- 頁籤 2：儲值方案 (綠界金流) -->
        <section id="tab-topup" class="hidden space-y-4">
          <div class="bg-gradient-to-br from-blue-700 to-indigo-800 p-6 rounded-2xl text-white shadow-lg">
            <h2 class="text-xl font-black">師傅專屬儲值優惠</h2>
            <p class="text-blue-200 text-xs mt-1">儲值立即享額外點數贈送，1 點 = 1 元，接單成本最高下殺 8 折！</p>
          </div>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
            <!-- 方案 1 -->
            <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col justify-between hover:border-blue-500 transition">
              <div>
                <div class="flex justify-between items-center">
                  <span class="font-bold text-slate-800">新手體驗方案</span>
                  <span class="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded font-bold">95 折</span>
                </div>
                <div class="text-2xl font-black text-slate-900 mt-2">NT$ 1,000</div>
                <div class="text-xs text-emerald-600 font-semibold mt-1">實得 1,050 點 (+50 點)</div>
              </div>
              <button onclick="buyPoints(1000, 1050)" class="mt-4 w-full bg-slate-800 hover:bg-slate-900 text-white text-xs font-bold py-2.5 rounded-lg transition">線上儲值 $1,000</button>
            </div>

            <!-- 方案 2 -->
            <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col justify-between hover:border-blue-500 transition">
              <div>
                <div class="flex justify-between items-center">
                  <span class="font-bold text-slate-800">兼職首選方案</span>
                  <span class="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded font-bold">91 折</span>
                </div>
                <div class="text-2xl font-black text-slate-900 mt-2">NT$ 3,000</div>
                <div class="text-xs text-emerald-600 font-semibold mt-1">實得 3,300 點 (+300 點)</div>
              </div>
              <button onclick="buyPoints(3000, 3300)" class="mt-4 w-full bg-slate-800 hover:bg-slate-900 text-white text-xs font-bold py-2.5 rounded-lg transition">線上儲值 $3,000</button>
            </div>

            <!-- 方案 3 (熱門) -->
            <div class="bg-white p-5 rounded-xl border-2 border-amber-500 shadow-md flex flex-col justify-between relative overflow-hidden">
              <div class="absolute top-0 right-0 bg-amber-500 text-white text-[10px] font-bold px-3 py-0.5 rounded-bl-lg">🔥 最多師傅選</div>
              <div>
                <div class="flex justify-between items-center">
                  <span class="font-bold text-slate-800">全職推薦方案</span>
                  <span class="text-xs bg-amber-100 text-amber-800 px-2 py-0.5 rounded font-bold">86 折</span>
                </div>
                <div class="text-2xl font-black text-slate-900 mt-2">NT$ 5,000</div>
                <div class="text-xs text-emerald-600 font-semibold mt-1">實得 5,800 點 (+800 點)</div>
              </div>
              <button onclick="buyPoints(500, 5800)" class="mt-4 w-full bg-amber-500 hover:bg-amber-600 text-slate-900 text-xs font-bold py-2.5 rounded-lg transition">線上儲值 $5,000</button>
            </div>

            <!-- 方案 4 -->
            <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm flex flex-col justify-between hover:border-blue-500 transition">
              <div>
                <div class="flex justify-between items-center">
                  <span class="font-bold text-slate-800">工程行進階方案</span>
                  <span class="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded font-bold">83 折</span>
                </div>
                <div class="text-2xl font-black text-slate-900 mt-2">NT$ 8,000</div>
                <div class="text-xs text-emerald-600 font-semibold mt-1">實得 9,600 點 (+1,600 點)</div>
              </div>
              <button onclick="buyPoints(8000, 9600)" class="mt-4 w-full bg-slate-800 hover:bg-slate-900 text-white text-xs font-bold py-2.5 rounded-lg transition">線上儲值 $8,000</button>
            </div>

            <!-- 方案 5 (VIP) -->
            <div class="sm:col-span-2 bg-slate-900 text-white p-6 rounded-2xl shadow-xl flex flex-col sm:flex-row justify-between items-center gap-4">
              <div>
                <div class="inline-block bg-amber-400 text-slate-900 text-[10px] font-black px-2 py-0.5 rounded">👑 VIP 旗艦大戶</div>
                <h3 class="text-xl font-bold mt-1">超值儲值 NT$ 12,000</h3>
                <p class="text-xs text-slate-400 mt-1">實得 <span class="text-amber-300 font-bold text-base">15,000 點</span>（現省 3,000 元，享整單 8 折接單成本）</p>
              </div>
              <button onclick="buyPoints(12000, 15000)" class="w-full sm:w-auto bg-amber-400 hover:bg-amber-500 text-slate-900 font-bold text-sm px-6 py-3 rounded-xl shadow-lg transition">立即升級 VIP 儲值</button>
            </div>
          </div>
        </section>

        <!-- 頁籤 3：轉介大額統包案 -->
        <section id="tab-referral" class="hidden space-y-4">
          <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
            <h2 class="text-lg font-bold text-slate-800">回報大額統包案（簽約享 2% 點數回饋）</h2>
            <p class="text-xs text-slate-500 mt-1">遇到全室翻修、老屋拉皮等吃不下的 5 萬以上大案，交給平台官方監管統包，成交後自動回饋高額接單點數！</p>
            
            <form id="referralForm" class="mt-4 space-y-3">
              <div>
                <label class="block text-xs font-semibold text-slate-700">業主姓名</label>
                <input type="text" id="refClientName" required placeholder="例如：林小姐" class="w-full mt-1 p-2.5 border border-slate-300 rounded-lg text-xs focus:ring-1 focus:ring-blue-500 focus:outline-none">
              </div>
              <div>
                <label class="block text-xs font-semibold text-slate-700">業主電話</label>
                <input type="tel" id="refClientPhone" required placeholder="例如：0933xxxxxx" class="w-full mt-1 p-2.5 border border-slate-300 rounded-lg text-xs focus:ring-1 focus:ring-blue-500 focus:outline-none">
              </div>
              <div>
                <label class="block text-xs font-semibold text-slate-700">施工地址</label>
                <input type="text" id="refAddress" required placeholder="例如：淡水區中正東路二段..." class="w-full mt-1 p-2.5 border border-slate-300 rounded-lg text-xs focus:ring-1 focus:ring-blue-500 focus:outline-none">
              </div>
              <div>
                <label class="block text-xs font-semibold text-slate-700">預估工程總預算 (NT$)</label>
                <input type="number" id="refEstAmount" value="300000" min="50000" class="w-full mt-1 p-2.5 border border-slate-300 rounded-lg text-xs focus:ring-1 focus:ring-blue-500 focus:outline-none">
                <p class="text-[11px] text-emerald-600 font-semibold mt-1">預估可獲得回饋：<span id="refCalcPoints">6,000</span> 點 (2%)</p>
              </div>
              <div>
                <label class="block text-xs font-semibold text-slate-700">狀況與需求說明</label>
                <textarea id="refNotes" rows="2" placeholder="例如：三房兩廳全室電線水管換新+泥作重做" class="w-full mt-1 p-2.5 border border-slate-300 rounded-lg text-xs focus:ring-1 focus:ring-blue-500 focus:outline-none"></textarea>
              </div>
              <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-xl text-xs shadow transition">送出大案轉介</button>
            </form>
          </div>
        </section>

      </main>

      <!-- 圖片放大彈窗 -->
      <div id="imgModal" class="hidden fixed inset-0 bg-black bg-opacity-75 flex justify-center items-center z-50 p-4" onclick="this.classList.add('hidden')">
        <img id="modalImg" src="" class="max-w-full max-h-[85vh] rounded-lg shadow-2xl">
      </div>

      <script>
        let currentProfile = { name: "王師傅", phone: "0912345678", points: 800 };

        function switchTab(tabId) {
          ['jobs', 'topup', 'referral'].forEach(t => {
            document.getElementById('tab-' + t).classList.add('hidden');
            const btn = document.getElementById('tabBtn-' + t);
            btn.className = "flex-1 py-2.5 text-center rounded-lg text-slate-600 hover:text-blue-600 transition";
          });
          document.getElementById('tab-' + tabId).classList.remove('hidden');
          const activeBtn = document.getElementById('tabBtn-' + tabId);
          activeBtn.className = "flex-1 py-2.5 text-center rounded-lg bg-blue-600 text-white shadow-sm transition";
          if (tabId === 'jobs') loadJobs();
        }

        async function loadProfile() {
          const phone = document.getElementById('techPhone').value.trim();
          if (!phone) return;
          try {
            const res = await fetch('/api/tech/profile?phone=' + phone);
            const data = await res.json();
            if (data.success) {
              currentProfile = data.profile;
              document.getElementById('techNameDisplay').innerText = currentProfile.name;
              document.getElementById('ptsBalance').innerText = currentProfile.points.toLocaleString();
            }
          } catch(e) {}
        }

        async function loadJobs() {
          const container = document.getElementById('jobList');
          try {
            const res = await fetch('/api/cases');
            const data = await res.json();
            if (!data.cases || data.cases.length === 0) {
              container.innerHTML = '<div class="bg-white p-8 rounded-xl text-center text-slate-400">目前尚無派工案件</div>';
              return;
            }
            container.innerHTML = data.cases.map(c => {
              const isClaimedByMe = c.technicianPhone === currentProfile.phone;
              const isAvailable = c.status === '待派工';

              return `
                <div class="bg-white p-4 sm:p-5 rounded-xl border ${isClaimedByMe ? 'border-emerald-500 bg-emerald-50/20' : 'border-slate-200'} shadow-sm space-y-3">
                  <div class="flex justify-between items-start">
                    <div>
                      <span class="inline-block px-2 py-0.5 bg-blue-100 text-blue-700 text-xs font-bold rounded">${c.item}</span>
                      <span class="text-xs text-slate-400 ml-1.5 font-mono">${c.id}</span>
                      <div class="text-xs text-slate-500 mt-1">📍 ${isClaimedByMe ? c.address : c.address.substring(0, 6) + '*** (接單後解鎖)'}</div>
                    </div>
                    <div class="text-right">
                      <div class="text-sm font-black text-slate-800">預估 NT$ ${c.depositAmount.toLocaleString()}</div>
                      <div class="text-xs text-rose-600 font-bold mt-0.5">扣點：${c.pointsRequired || 50} 點</div>
                    </div>
                  </div>

                  <div class="text-xs text-slate-600 bg-slate-50 p-2.5 rounded-lg border border-slate-100">
                    <p><b>施作坪數：</b> ${c.areaPing || 0} 坪 ${c.needSupervision ? ' | <span class="text-blue-600 font-bold">含 8% 監工</span>' : ''}</p>
                    <p class="mt-1"><b>損壞描述：</b> ${c.description || '無'}</p>
                  </div>

                  ${c.photo ? `
                    <div class="flex items-center space-x-2">
                      <img src="${c.photo}" onclick="viewPhoto('${c.photo}')" class="w-12 h-12 object-cover rounded-lg border border-slate-200 cursor-pointer hover:opacity-80" title="點擊放大現場照片">
                      <span class="text-[11px] text-slate-400">點擊查看現場照片</span>
                    </div>
                  ` : ''}

                  <!-- 狀態與接單按鈕 -->
                  <div class="pt-2 border-t border-slate-100 flex justify-between items-center">
                    <div>
                      ${isClaimedByMe ? `
                        <div class="text-xs font-bold text-emerald-700">
                          ✓ 已接單 | 客戶電話：<a href="tel:${c.clientPhone}" class="text-blue-600 underline font-mono text-sm">${c.clientPhone}</a> (${c.clientName})
                        </div>
                      ` : `
                        <span class="text-xs font-semibold ${isAvailable ? 'text-blue-600' : 'text-slate-400'}">
                          狀態：${c.status} ${!isAvailable && c.technician ? '(' + c.technician + ')' : ''}
                        </span>
                      `}
                    </div>

                    <div>
                      ${isAvailable ? `
                        <button onclick="claimJob('${c.id}', ${c.pointsRequired || 50})" class="bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold px-4 py-2 rounded-lg shadow transition">
                          ⚡ 扣 ${c.pointsRequired || 50} 點接單
                        </button>
                      ` : ''}
                    </div>
                  </div>
                </div>
              `;
            }).join('');
          } catch(e) {
            container.innerHTML = '<div class="p-8 text-center text-rose-500">載入失敗，請重試</div>';
          }
        }

        async function claimJob(caseId, pts) {
          if (!confirm(`確定要扣除 ${pts} 點接下此案件並取得客戶資料嗎？`)) return;
          try {
            const res = await fetch('/api/tech/claim', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                caseId: caseId,
                techName: currentProfile.name,
                techPhone: currentProfile.phone
              })
            });
            const data = await res.json();
            if (data.success) {
              alert('🎉 接單成功！\\n' + data.msg);
              loadProfile();
              loadJobs();
            } else {
              alert('❌ ' + data.msg);
            }
          } catch(e) {
            alert('系統忙碌中，請稍後再試');
          }
        }

        function buyPoints(amount, points) {
          const form = document.createElement('form');
          form.method = 'POST';
          form.action = '/api/tech/topup';

          const fields = { techPhone: currentProfile.phone, amount: amount, points: points };
          for (const key in fields) {
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = key;
            input.value = fields[key];
            form.appendChild(input);
          }
          document.body.appendChild(form);
          form.submit();
        }

        document.getElementById('refEstAmount').addEventListener('input', function(e) {
          const amt = parseInt(e.target.value) || 0;
          document.getElementById('refCalcPoints').innerText = Math.round(amt * 0.02).toLocaleString();
        });

        document.getElementById('referralForm').addEventListener('submit', async (e) => {
          e.preventDefault();
          const data = {
            techName: currentProfile.name,
            techPhone: currentProfile.phone,
            clientName: document.getElementById('refClientName').value,
            clientPhone: document.getElementById('refClientPhone').value,
            address: document.getElementById('refAddress').value,
            estAmount: parseInt(document.getElementById('refEstAmount').value) || 50000,
            notes: document.getElementById('refNotes').value
          };
          try {
            const res = await fetch('/api/tech/referral', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(data)
            });
            const result = await res.json();
            if (result.success) {
              alert('🎉 ' + result.msg);
              document.getElementById('referralForm').reset();
              switchTab('jobs');
            }
          } catch(err) {
            alert('送出失敗');
          }
        });

        function viewPhoto(src) {
          document.getElementById('modalImg').src = src;
          document.getElementById('imgModal').classList.remove('hidden');
        }

        // 初始化載入
        loadProfile();
        loadJobs();
      </script>
    </body>
    </html>
    """

# 2. 消費端 (/app)
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
          <p class="text-blue-100 text-sm mt-1">精準坪數試算行情，在地專業師傅快速到府</p>
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
            <p>• 系統已即時推播給專業師傅。</p>
            <p>• 師傅扣點接單後，將會立即透過電話與您聯絡確認細節與到府時間。</p>
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
            base = Math.round(base * 1.08);
          }

          const points = Math.max(50, Math.round(base * 0.03));
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

# 3. 管理後台 (/admin)
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
            <p class="text-sm text-slate-500 mt-1">掌握案件、師傅派工扣點、照片、修改金額與綠界專屬收款</p>
          </div>
          <div class="flex gap-2">
            <a href="/tech" target="_blank" class="bg-slate-800 hover:bg-slate-900 text-white text-sm font-semibold px-4 py-2.5 rounded-lg shadow transition">
              👷‍♂️ 開啟師傅端工作台
            </a>
            <button onclick="loadCases()" class="bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold px-4 py-2.5 rounded-lg shadow transition">
              🔄 重新整理
            </button>
          </div>
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
                  <th class="p-4">應收金額 / 扣點</th>
                  <th class="p-4">付款狀態</th>
                  <th class="p-4">接單師傅</th>
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
                  ${c.technicianPhone ? `<div class="text-[10px] text-slate-400 mt-0.5">${c.technicianPhone}</div>` : ''}
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
    <script>window.location.href = '/tech';</script>
    """
