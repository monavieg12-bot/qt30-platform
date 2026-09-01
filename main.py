import os
import time
import urllib.parse
import hashlib
import requests
import sqlite3
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

app = FastAPI(title="QT30 房屋修繕派工平台 (實名制與安全登入版)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin888")
DB_PATH = "qt30_database.db"

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
        deposit_amount INTEGER,
        photo TEXT,
        status TEXT,
        technician TEXT,
        payment_status TEXT,
        unlocked_by TEXT,
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
        radius_km INTEGER,
        technician_phone TEXT,
        technician_name TEXT
    )
    """)
    # 師傅實名資料表 (密碼、證件、認證狀態)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS technicians (
        phone TEXT PRIMARY KEY,
        password TEXT,
        name TEXT,
        id_card_no TEXT,
        id_card_photo TEXT,
        license_photo TEXT,
        skill TEXT,
        points INTEGER,
        verified_status TEXT,
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
        status TEXT,
        created_at TEXT
    )
    """)
    # 初始化預設示範師傅帳號 (0912345678 / 123456 / 已通過 / 800點)
    cursor.execute("""
    INSERT OR IGNORE INTO technicians (phone, password, name, id_card_no, id_card_photo, license_photo, skill, points, verified_status, created_at)
    VALUES ('0912345678', '123456', '王師傅', 'A123456789', '', '', '水電維修', 800, '已通過', '2026-09-01 00:00:00')
    """)

    cursor.execute("SELECT COUNT(*) FROM spots")
    if cursor.fetchone()[0] == 0:
        default_spots = [
            ("spot-1", "淡海新市鎮特區", 25.1956, 121.4398, 5, "0912345678", "王師傅 (北部水電)"),
            ("spot-2", "林口三井生活圈", 25.0712, 121.3658, 6, "0912345678", "王師傅 (北部水電)"),
            ("spot-3", "竹北高鐵特區", 24.8085, 121.0402, 8, "0912345678", "王師傅 (北部水電)")
        ]
        cursor.executemany("INSERT INTO spots (id, name, lat, lng, radius_km, technician_phone, technician_name) VALUES (?, ?, ?, ?, ?, ?, ?)", default_spots)

    conn.commit()
    conn.close()

init_db()

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

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

# --- Pydantic 模型 ---
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
    technicianPhone: str
    technicianName: str

class TopupCreate(BaseModel):
    phone: str
    amount: int

class TechRegister(BaseModel):
    phone: str
    password: str
    name: str
    idCardNo: str
    idCardPhoto: Optional[str] = None
    licensePhoto: Optional[str] = None
    skill: Optional[str] = "水電維修"

class TechLogin(BaseModel):
    phone: str
    password: str

class TechVerifyUpdate(BaseModel):
    phone: str
    status: str  # '已通過' 或 '已拒絕'

class AdminAuth(BaseModel):
    password: str

# --- 師傅認證與登入 API ---
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
    VALUES (?, ?, ?, ?, ?, ?, ?, 100, '待審核', ?)
    """, (data.phone, data.password, data.name, data.idCardNo, data.idCardPhoto or "", data.licensePhoto or "", data.skill or "水電維修", created_at))
    conn.commit()
    conn.close()

    msg = (
        f"🛡️ 【新師傅實名認證申請通知】\n"
        f"------------------------\n"
        f"👤 師傅姓名：{data.name}\n"
        f"📞 聯絡電話：{data.phone}\n"
        f"🆔 身分證號：{data.idCardNo}\n"
        f"🔧 專業項目：{data.skill}\n"
        f"------------------------\n"
        f"請管理員儘速登入後台審核證件！"
    )
    send_line_notification(msg)

    return {
        "success": True,
        "message": "實名註冊成功！資料已送出審核（已贈送 100 點體驗點數），待管理員審核通過後即可開始接單！",
        "tech": {
            "phone": data.phone,
            "name": data.name,
            "points": 100,
            "verifiedStatus": "待審核"
        }
    }

@app.post("/api/tech/login")
def login_tech(data: TechLogin):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM technicians WHERE phone = ? AND password = ?", (data.phone, data.password))
    tech = cursor.fetchone()
    conn.close()

    if not tech:
        return {"success": False, "message": "手機號碼或密碼錯誤！"}

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

@app.get("/api/tech/profile")
def get_tech_profile(phone: str):
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

# --- 管理員審核師傅 API ---
@app.post("/api/admin/verify-login")
def admin_verify_login(data: AdminAuth):
    if data.password == ADMIN_PASSWORD:
        return {"success": True, "token": "admin_authenticated_token"}
    return {"success": False, "message": "後台密碼錯誤！"}

@app.get("/api/admin/technicians")
def list_technicians():
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT phone, name, id_card_no, id_card_photo, license_photo, skill, points, verified_status, created_at FROM technicians ORDER BY created_at DESC")
    rows = cursor.fetchall()
    techs = [dict(r) for r in rows]
    conn.close()
    return {"success": True, "technicians": techs}

@app.post("/api/admin/verify-technician")
def verify_technician(data: TechVerifyUpdate):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE technicians SET verified_status = ? WHERE phone = ?", (data.status, data.phone))
    cursor.execute("SELECT name FROM technicians WHERE phone = ?", (data.phone,))
    tech = cursor.fetchone()
    conn.commit()
    conn.close()

    if tech:
        msg = (
            f"✅ 【師傅實名認證結果更新】\n"
            f"------------------------\n"
            f"👤 師傅：{tech['name']} ({data.phone})\n"
            f"📌 審核狀態：{data.status}\n"
            f"------------------------\n"
            f"師傅已可於工作台進行權限操作。"
        )
        send_line_notification(msg)
    return {"success": True}

# --- 案件管理與扣點搶單 API ---
@app.post("/api/cases")
def create_case(data: CaseCreate):
    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"QT{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    case_id = f"CASE-{trade_no[-6:]}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO cases (id, trade_no, client_name, client_phone, address, item, description, deposit_amount, photo, status, technician, payment_status, unlocked_by, created_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '待派工', '未指派', '未收款', '', ?)
    """, (case_id, trade_no, data.clientName, data.clientPhone, data.address, data.item, data.description, data.depositAmount or 500, data.photo, created_at))
    conn.commit()
    conn.close()

    photo_tag = "📷 【已附現場照片】" if data.photo else "📷 【未附現場照片】"

    msg = (
        f"🔔 【QT30 新進預約報修單】\n"
        f"------------------------\n"
        f"📌 案件編號：{case_id}\n"
        f"👤 客戶姓名：{data.clientName}\n"
        f"📞 聯絡電話：{data.clientPhone}\n"
        f"📍 修繕地址：{data.address}\n"
        f"🔧 報修項目：{data.item}\n"
        f"💰 客戶預算：NT$ {data.depositAmount}\n"
        f"📝 狀況描述：{data.description}\n"
        f"{photo_tag}\n"
        f"------------------------\n"
        f"⚡ 實名認證師傅已可於大廳搶單！"
    )
    send_line_notification(msg)
    return {"success": True, "case": {"id": case_id, "tradeNo": trade_no, "depositAmount": data.depositAmount}}

@app.get("/api/cases")
def get_cases(phone: Optional[str] = None):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cases ORDER BY created_at DESC")
    rows = cursor.fetchall()
    cases = []
    for r in rows:
        unlocked = False
        if phone and r["unlocked_by"] and phone in r["unlocked_by"].split(","):
            unlocked = True

        disp_phone = r["client_phone"] if (unlocked or not phone) else (r["client_phone"][:4] + "****" + r["client_phone"][-2:] if len(r["client_phone"]) >= 6 else "***")
        disp_address = r["address"] if (unlocked or not phone) else (r["address"][:6] + "******" if len(r["address"]) >= 6 else "***")

        cases.append({
            "id": r["id"],
            "tradeNo": r["trade_no"],
            "clientName": r["client_name"],
            "clientPhone": disp_phone,
            "address": disp_address,
            "item": r["item"],
            "description": r["description"],
            "depositAmount": r["deposit_amount"],
            "photo": r["photo"],
            "status": r["status"],
            "technician": r["technician"],
            "paymentStatus": r["payment_status"],
            "unlocked": unlocked,
            "createdAt": r["created_at"]
        })
    conn.close()
    return {"success": True, "cases": cases}

@app.post("/api/cases/{case_id}/unlock")
def unlock_case(case_id: str, phone: str):
    COST = 50
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM technicians WHERE phone = ?", (phone,))
    tech = cursor.fetchone()
    if not tech:
        conn.close()
        raise HTTPException(status_code=404, detail="找不到師傅帳號")

    if tech["verified_status"] != "已通過":
        conn.close()
        return {"success": False, "message": "您的實名認證尚未審核通過，暫無法扣點搶單！請耐心等候審核。"}

    if tech["points"] < COST:
        conn.close()
        return {"success": False, "message": f"點數不足！搶單需 {COST} 點，目前剩餘 {tech['points']} 點，請先線上儲值。"}

    cursor.execute("SELECT * FROM cases WHERE id = ?", (case_id,))
    c = cursor.fetchone()
    if not c:
        conn.close()
        raise HTTPException(status_code=404, detail="找不到案件")

    unlocked_by = c["unlocked_by"] or ""
    unlocked_list = [p for p in unlocked_by.split(",") if p]
    if phone not in unlocked_list:
        unlocked_list.append(phone)
        new_unlocked_str = ",".join(unlocked_list)
        new_points = tech["points"] - COST
        cursor.execute("UPDATE technicians SET points = ? WHERE phone = ?", (new_points, phone))
        cursor.execute("UPDATE cases SET unlocked_by = ?, technician = ? WHERE id = ?", (new_unlocked_str, tech["name"], case_id))
        conn.commit()

        msg = (
            f"⚡ 【實名師傅成功搶單通知】\n"
            f"------------------------\n"
            f"🔧 師傅：{tech['name']} ({phone})\n"
            f"📌 案件編號：{c['id']}\n"
            f"👤 客戶姓名：{c['client_name']}\n"
            f"📞 聯絡電話：{c['client_phone']}\n"
            f"📍 施工地址：{c['address']}\n"
            f"💰 扣除點數：{COST} 點 (剩餘 {new_points} 點)\n"
            f"------------------------\n"
            f"請師傅立即聯絡客戶安排到府估價！"
        )
        send_line_notification(msg)

    cursor.execute("SELECT * FROM cases WHERE id = ?", (case_id,))
    latest_case = cursor.fetchone()
    cursor.execute("SELECT points FROM technicians WHERE phone = ?", (phone,))
    cur_points = cursor.fetchone()["points"]
    conn.close()

    return {
        "success": True,
        "points": cur_points,
        "case": {
            "id": latest_case["id"],
            "clientName": latest_case["client_name"],
            "clientPhone": latest_case["client_phone"],
            "address": latest_case["address"],
            "item": latest_case["item"],
            "description": latest_case["description"],
            "photo": latest_case["photo"]
        }
    }

# --- 綠界儲值與付款 API ---
@app.post("/api/tech/topup")
def create_topup_order(data: TopupCreate, request: Request):
    amount = data.amount
    points = amount
    if amount == 1000:
        points = 1100
    elif amount == 3000:
        points = 3500

    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"TOP{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO topup_orders (trade_no, phone, amount, points, status, created_at) VALUES (?, ?, ?, ?, '未付款', ?)",
                   (trade_no, data.phone, amount, points, created_at))
    conn.commit()
    conn.close()

    base_url = str(request.base_url).rstrip('/')
    trade_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    params = {
        "MerchantID": ECPAY_MERCHANT_ID,
        "MerchantTradeNo": trade_no,
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(amount),
        "TradeDesc": ecpay_url_encode("QT30師傅點數儲值"),
        "ItemName": f"QT30 接單點數 {points} 點",
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
    <head><title>前往綠界儲值支付...</title><meta charset="utf-8"></head>
    <body onload="document.getElementById('ecpay_form').submit();" style="display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;background:#0f172a;color:#fff;">
        <div style="text-align:center;padding:30px;background:#1e293b;border-radius:12px;border:1px solid #334155;">
            <h2 style="color:#38bdf8;">正在前往綠界官方安全收銀台...</h2>
            <p>儲值方案：<b>NT$ {amount}</b> (獲取 <b>{points} 點</b>)</p>
            <form id="ecpay_form" method="POST" action="{ECPAY_PAYMENT_URL}">
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

    if rtn_code == "1":
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM topup_orders WHERE trade_no = ?", (trade_no,))
        order = cursor.fetchone()
        if order and order["status"] != "已付款":
            cursor.execute("UPDATE topup_orders SET status = '已付款' WHERE trade_no = ?", (trade_no,))
            cursor.execute("UPDATE technicians SET points = points + ? WHERE phone = ?", (order["points"], order["phone"]))
            cursor.execute("SELECT points, name FROM technicians WHERE phone = ?", (order["phone"],))
            tech = cursor.fetchone()
            conn.commit()

            msg = (
                f"💎 【QT30 師傅線上儲值成功！】\n"
                f"------------------------\n"
                f"🔧 儲值師傅：{tech['name']} ({order['phone']})\n"
                f"💰 儲值金額：NT$ {order['amount']}\n"
                f"🎁 增加點數：+{order['points']} 點\n"
                f"⚡ 目前總餘額：{tech['points']} 點\n"
                f"------------------------\n"
                f"款項已入帳綠界正式戶頭！"
            )
            send_line_notification(msg)
        conn.close()
    return "1|OK"

# --- 案件修改與客戶付款 ---
@app.patch("/api/cases/{case_id}")
def update_case(case_id: str, data: CaseUpdate):
    conn = get_db()
    cursor = conn.cursor()
    updates, params = [], []
    if data.status is not None:
        updates.append("status = ?")
        params.append(data.status)
    if data.technician is not None:
        updates.append("technician = ?")
        params.append(data.technician)
    if data.depositAmount is not None:
        updates.append("deposit_amount = ?")
        params.append(data.depositAmount)
    if data.paymentStatus is not None:
        updates.append("payment_status = ?")
        params.append(data.paymentStatus)
        
    if not updates:
        conn.close()
        return {"success": True}
        
    params.append(case_id)
    cursor.execute(f"UPDATE cases SET {', '.join(updates)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"success": True}

@app.get("/api/pay/{case_id}", response_class=HTMLResponse)
def get_payment_page(case_id: str, request: Request):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM cases WHERE id = ?", (case_id,))
    target = cursor.fetchone()
    conn.close()

    if not target:
        raise HTTPException(status_code=404, detail="找不到案件")

    base_url = str(request.base_url).rstrip('/')
    trade_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    params = {
        "MerchantID": ECPAY_MERCHANT_ID,
        "MerchantTradeNo": target["trade_no"],
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(target["deposit_amount"]),
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
            <p>案件編號：<b>{target['id']}</b> | 應付金額：<b>NT$ {target['deposit_amount']}</b></p>
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
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE cases SET payment_status = '已付款' WHERE trade_no = ?", (trade_no,))
        cursor.execute("SELECT * FROM cases WHERE trade_no = ?", (trade_no,))
        c = cursor.fetchone()
        conn.commit()
        conn.close()

        if c:
            msg = (
                f"🎉 【QT30 款項已成功入帳！】\n"
                f"------------------------\n"
                f"📌 案件編號：{c['id']}\n"
                f"👤 客戶：{c['client_name']}\n"
                f"💰 入帳金額：NT$ {c['deposit_amount']}\n"
                f"💳 付款狀態：綠界扣款成功\n"
                f"------------------------\n"
                f"款項已入帳，請安排師傅前往施工！"
            )
            send_line_notification(msg)
    return "1|OK"

# --- 據點卡位 API ---
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
    cursor.execute("INSERT INTO spots (id, name, lat, lng, radius_km, technician_phone, technician_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
                   (new_id, spot.name, spot.lat, spot.lng, spot.radiusKm, spot.technicianPhone, spot.technicianName))
    conn.commit()
    conn.close()
    return {"success": True, "spot": {"id": new_id, "name": spot.name, "lat": spot.lat, "lng": spot.lng, "radiusKm": spot.radiusKm, "technicianName": spot.technicianName}}

# --- 消費端發案頁面 (/app) ---
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
          <p class="text-blue-100 text-sm mt-1">填單立即為您安排通過實名認證的專業師傅</p>
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
            <p>• 系統已即時推播給本區域實名認證師傅。</p>
            <p>• 師傅將會儘速透過電話與您聯絡確認到府估價時間。</p>
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

# --- 師傅端工作台 (/tech) 實名註冊 / 登入 / 接單大廳 ---
@app.get("/tech", response_class=HTMLResponse)
def serve_tech_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 師傅接單工作台</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
      <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    </head>
    <body class="bg-slate-950 text-slate-100 min-h-screen">
      <!-- 頂部導航 -->
      <header class="bg-slate-900 border-b border-slate-800 p-4 px-6 sticky top-0 z-40">
        <div class="max-w-6xl mx-auto flex flex-wrap justify-between items-center gap-4">
          <div>
            <div class="text-[11px] text-blue-400 font-bold uppercase tracking-wider">QT30 全國專業師傅平台</div>
            <div class="flex items-center space-x-2 mt-0.5">
              <span id="headerPhone" class="font-mono text-xs bg-slate-800 text-slate-300 px-2 py-0.5 rounded border border-slate-700">0912345678</span>
              <span id="headerName" class="font-bold text-base text-white">王師傅</span>
              <span id="verifyBadge" class="text-[11px] font-bold px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">✓ 實名認證</span>
            </div>
          </div>
          <div class="flex items-center space-x-3">
            <div class="text-right">
              <span class="text-[11px] text-slate-400">點數餘額</span>
              <div class="font-black text-amber-400 text-lg leading-none mt-0.5"><span id="pointsBalance">0</span> <span class="text-xs font-normal text-amber-200/80">點</span></div>
            </div>
            <button onclick="switchTab('topup')" class="bg-gradient-to-r from-amber-500 to-amber-600 hover:from-amber-600 text-slate-950 font-black text-xs px-3.5 py-2 rounded-xl shadow transition">
              💳 儲值
            </button>
            <button onclick="logoutTech()" class="bg-slate-800 hover:bg-slate-700 text-slate-300 text-xs px-3 py-2 rounded-xl border border-slate-700 transition">
              登出
            </button>
          </div>
        </div>
      </header>

      <!-- 審核狀態橫幅提示 -->
      <div id="pendingBanner" class="hidden bg-amber-950/80 border-b border-amber-800 p-3 text-center text-xs text-amber-200">
        ⚠️ 您的實名認證資料正在由管理員審核中。審核通過前，暫時無法扣點搶單與卡位。
      </div>

      <!-- 主畫面 -->
      <div class="max-w-6xl mx-auto p-4 sm:p-6 space-y-6">
        <div class="flex border-b border-slate-800 space-x-2 sm:space-x-4">
          <button id="tabBtn-hall" onclick="switchTab('hall')" class="tab-btn px-4 py-3 font-bold text-sm text-blue-400 border-b-2 border-blue-500 flex items-center gap-1.5">
            ⚡ 接單大廳 (扣點搶單)
          </button>
          <button id="tabBtn-map" onclick="switchTab('map')" class="tab-btn px-4 py-3 font-bold text-sm text-slate-400 hover:text-slate-200 border-b-2 border-transparent flex items-center gap-1.5">
            🗺️ 跨區地圖卡位
          </button>
          <button id="tabBtn-topup" onclick="switchTab('topup')" class="tab-btn px-4 py-3 font-bold text-sm text-slate-400 hover:text-slate-200 border-b-2 border-transparent flex items-center gap-1.5">
            💎 點數儲值專區
          </button>
        </div>

        <!-- 分頁 1: 接單大廳 -->
        <section id="tab-hall" class="space-y-4">
          <div class="flex justify-between items-center bg-slate-900 p-4 rounded-xl border border-slate-800">
            <div>
              <h2 class="text-base font-bold text-white">🔥 即時可接修繕案件</h2>
              <p class="text-xs text-slate-400 mt-0.5">點擊「扣 50 點搶單」即可解鎖客戶電話與完整地址</p>
            </div>
            <button onclick="loadHallCases()" class="bg-slate-800 hover:bg-slate-700 text-xs px-3 py-1.5 rounded-lg border border-slate-700 transition">
              🔄 重新整理
            </button>
          </div>
          <div id="casesList" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </section>

        <!-- 分頁 2: 地圖卡位 -->
        <section id="tab-map" class="hidden space-y-4">
          <div class="bg-slate-900 border border-slate-800 p-4 rounded-xl flex justify-between items-center">
            <div>
              <h2 class="text-base font-bold text-white">📍 全台專屬社區卡位地圖</h2>
              <p class="text-xs text-slate-400 mt-0.5">點擊地圖任意區域自訂服務據點，享 15 分鐘優先搶單權！</p>
            </div>
            <button onclick="document.getElementById('addSpotModal').classList.remove('hidden')" class="bg-blue-600 hover:bg-blue-700 text-xs font-bold px-3 py-1.5 rounded-lg transition">
              ➕ 自訂據點
            </button>
          </div>
          <div class="bg-slate-900 border border-slate-800 rounded-2xl overflow-hidden">
            <div id="map" class="h-[450px] w-full bg-slate-950"></div>
          </div>
          <div id="spotList" class="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-3 gap-3"></div>
        </section>

        <!-- 分頁 3: 儲值專區 -->
        <section id="tab-topup" class="hidden space-y-6">
          <div class="bg-slate-900 border border-slate-800 p-6 rounded-2xl text-center space-y-2">
            <h2 class="text-xl font-bold text-white">💳 購買接單點數 (綠界線上支付)</h2>
            <p class="text-xs text-slate-400">扣款成功後秒入帳，可用於搶單與社區卡位</p>
          </div>

          <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 flex flex-col justify-between">
              <div>
                <div class="text-sm font-bold text-slate-300">體驗小額包</div>
                <div class="text-3xl font-black text-white mt-2">NT$ 500</div>
                <div class="text-xs text-blue-400 font-bold mt-1">獲得 500 點</div>
                <p class="text-xs text-slate-400 mt-4">適合剛加入平台的新師傅，可解鎖約 10 筆案件。</p>
              </div>
              <button onclick="doTopup(500)" class="mt-6 w-full bg-slate-800 hover:bg-slate-700 text-white font-bold py-3 rounded-xl text-sm transition">
                線上刷卡 NT$ 500
              </button>
            </div>

            <div class="bg-gradient-to-b from-blue-950/60 to-slate-900 border-2 border-blue-500 rounded-2xl p-6 flex flex-col justify-between relative shadow-lg">
              <span class="absolute -top-3 right-4 bg-blue-500 text-white text-[10px] font-black px-2.5 py-0.5 rounded-full">超值推薦</span>
              <div>
                <div class="text-sm font-bold text-blue-300">熱門進階包</div>
                <div class="text-3xl font-black text-white mt-2">NT$ 1,000</div>
                <div class="text-xs text-emerald-400 font-bold mt-1">獲得 1,100 點 (加贈 100 點)</div>
                <p class="text-xs text-slate-400 mt-4">主打方案！搶單 + 社區卡位必備。</p>
              </div>
              <button onclick="doTopup(1000)" class="mt-6 w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-xl text-sm shadow transition">
                線上刷卡 NT$ 1,000
              </button>
            </div>

            <div class="bg-slate-900 border border-slate-800 rounded-2xl p-6 flex flex-col justify-between">
              <div>
                <div class="text-sm font-bold text-amber-300">旗艦工班包</div>
                <div class="text-3xl font-black text-white mt-2">NT$ 3,000</div>
                <div class="text-xs text-emerald-400 font-bold mt-1">獲得 3,500 點 (加贈 500 點)</div>
                <p class="text-xs text-slate-400 mt-4">工程行首選，享全區域大量接單權益。</p>
              </div>
              <button onclick="doTopup(3000)" class="mt-6 w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-black py-3 rounded-xl text-sm shadow transition">
                線上刷卡 NT$ 3,000
              </button>
            </div>
          </div>
        </section>
      </div>

      <!-- 登入 / 實名註冊 彈窗 (未登入時強制跳出) -->
      <div id="authModal" class="hidden fixed inset-0 bg-black/85 flex justify-center items-center z-50 p-4 overflow-y-auto">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 space-y-4 my-8">
          <div class="text-center">
            <h2 class="text-xl font-bold text-white">QT30 師傅工作台登入</h2>
            <p class="text-xs text-slate-400 mt-1">嚴格實名認證體系，保障師傅與客戶權益</p>
          </div>

          <div class="flex border-b border-slate-800">
            <button id="authTab-login" onclick="toggleAuthTab('login')" class="w-1/2 py-2.5 font-bold text-sm text-blue-400 border-b-2 border-blue-500">師傅登入</button>
            <button id="authTab-register" onclick="toggleAuthTab('register')" class="w-1/2 py-2.5 font-bold text-sm text-slate-400 border-b-2 border-transparent">實名註冊</button>
          </div>

          <!-- 登入表單 -->
          <form id="loginForm" class="space-y-3">
            <div>
              <label class="block text-xs font-semibold text-slate-300">手機號碼</label>
              <input type="tel" id="loginPhone" required placeholder="例如：0912345678" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-lg text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-300">登入密碼</label>
              <input type="password" id="loginPassword" required placeholder="請輸入密碼" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-lg text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500">
            </div>
            <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 rounded-lg text-sm shadow transition">
              登入工作台
            </button>
            <p class="text-[11px] text-slate-500 text-center">預設測試帳號：手機 0912345678 / 密碼 123456</p>
          </form>

          <!-- 實名註冊表單 -->
          <form id="registerForm" class="hidden space-y-3">
            <div>
              <label class="block text-xs font-semibold text-slate-300">真實姓名</label>
              <input type="text" id="regName" required placeholder="例如：林大明" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-xs text-white">
            </div>
            <div class="grid grid-cols-2 gap-2">
              <div>
                <label class="block text-xs font-semibold text-slate-300">手機號碼</label>
                <input type="tel" id="regPhone" required placeholder="09xxxxxxxx" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-xs text-white">
              </div>
              <div>
                <label class="block text-xs font-semibold text-slate-300">身分證字號</label>
                <input type="text" id="regIdCard" required placeholder="身分證字號" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-xs text-white">
              </div>
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-300">設定密碼</label>
              <input type="password" id="regPassword" required placeholder="至少 6 位數" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-xs text-white">
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-300">主修專業項目</label>
              <select id="regSkill" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-xs text-white">
                <option value="水電維修">水電維修</option>
                <option value="泥作防水">泥作防水</option>
                <option value="冷氣空調">冷氣空調</option>
                <option value="油漆粉刷">油漆粉刷</option>
                <option value="裝潢木作">裝潢木作</option>
              </select>
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-300">上傳身分證照片 (KYC 實名審核)</label>
              <input type="file" id="regIdPhoto" accept="image/*" class="w-full mt-1 p-1 bg-slate-950 border border-dashed border-slate-700 rounded text-[11px] text-slate-400">
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-300">上傳技師證照 / 營業執照 (選填)</label>
              <input type="file" id="regLicensePhoto" accept="image/*" class="w-full mt-1 p-1 bg-slate-950 border border-dashed border-slate-700 rounded text-[11px] text-slate-400">
            </div>
            <button type="submit" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-2.5 rounded-lg text-xs shadow transition">
              送出實名註冊 (贈 100 點)
            </button>
          </form>
        </div>
      </div>

      <!-- 新增據點彈窗 -->
      <div id="addSpotModal" class="hidden fixed inset-0 bg-black/75 flex justify-center items-center z-50 p-4">
        <div class="bg-slate-900 border border-slate-800 rounded-2xl max-w-md w-full p-6 space-y-4">
          <h3 class="text-lg font-bold text-white">📍 自訂服務地區 / 卡位據點</h3>
          <div>
            <label class="block text-xs font-semibold text-slate-300">地區 / 社區名稱</label>
            <input type="text" id="newSpotName" placeholder="例如：新北市淡水新市鎮" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-lg text-sm text-white">
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs font-semibold text-slate-300">緯度 (Lat)</label>
              <input type="number" step="0.0001" id="newSpotLat" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-xs text-white">
            </div>
            <div>
              <label class="block text-xs font-semibold text-slate-300">經度 (Lng)</label>
              <input type="number" step="0.0001" id="newSpotLng" class="w-full mt-1 p-2 bg-slate-950 border border-slate-700 rounded-lg text-xs text-white">
            </div>
          </div>
          <div>
            <label class="block text-xs font-semibold text-slate-300">服務範圍半徑 (公里)</label>
            <input type="number" id="newSpotRadius" value="5" class="w-full mt-1 p-2.5 bg-slate-950 border border-slate-700 rounded-lg text-sm text-white">
          </div>
          <div class="flex space-x-2 pt-2">
            <button onclick="document.getElementById('addSpotModal').classList.add('hidden')" class="w-1/2 bg-slate-800 hover:bg-slate-700 text-slate-300 font-semibold py-2.5 rounded-lg text-xs transition">
              取消
            </button>
            <button onclick="saveNewSpot()" class="w-1/2 bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 rounded-lg text-xs shadow transition">
              確認卡位
            </button>
          </div>
        </div>
      </div>

      <!-- 圖片放大彈窗 -->
      <div id="imgModal" class="hidden fixed inset-0 bg-black/80 flex justify-center items-center z-50 p-4" onclick="this.classList.add('hidden')">
        <img id="modalImg" src="" class="max-w-full max-h-[85vh] rounded-xl shadow-2xl">
      </div>

      <script>
        let currentTech = JSON.parse(localStorage.getItem('qt30_tech_user') || 'null');
        let map, markers = [];

        function checkAuth() {
          if (!currentTech) {
            document.getElementById('authModal').classList.remove('hidden');
          } else {
            document.getElementById('authModal').classList.add('hidden');
            loadProfile();
            loadHallCases();
          }
        }

        function toggleAuthTab(tab) {
          if (tab === 'login') {
            document.getElementById('loginForm').classList.remove('hidden');
            document.getElementById('registerForm').classList.add('hidden');
            document.getElementById('authTab-login').className = 'w-1/2 py-2.5 font-bold text-sm text-blue-400 border-b-2 border-blue-500';
            document.getElementById('authTab-register').className = 'w-1/2 py-2.5 font-bold text-sm text-slate-400 border-b-2 border-transparent';
          } else {
            document.getElementById('loginForm').classList.add('hidden');
            document.getElementById('registerForm').classList.remove('hidden');
            document.getElementById('authTab-register').className = 'w-1/2 py-2.5 font-bold text-sm text-emerald-400 border-b-2 border-emerald-500';
            document.getElementById('authTab-login').className = 'w-1/2 py-2.5 font-bold text-sm text-slate-400 border-b-2 border-transparent';
          }
        }

        document.getElementById('loginForm').addEventListener('submit', async (e) => {
          e.preventDefault();
          const phone = document.getElementById('loginPhone').value;
          const password = document.getElementById('loginPassword').value;
          try {
            const res = await fetch('/api/tech/login', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ phone, password })
            });
            const data = await res.json();
            if (data.success) {
              currentTech = data.tech;
              localStorage.setItem('qt30_tech_user', JSON.stringify(currentTech));
              document.getElementById('authModal').classList.add('hidden');
              loadProfile();
              loadHallCases();
            } else {
              alert(data.message);
            }
          } catch(e) {
            alert('登入失敗');
          }
        });

        document.getElementById('registerForm').addEventListener('submit', async (e) => {
          e.preventDefault();
          const readBase64 = (fileInput) => new Promise((resolve) => {
            const file = fileInput.files[0];
            if (!file) return resolve('');
            const reader = new FileReader();
            reader.onload = (evt) => resolve(evt.target.result);
            reader.readAsDataURL(file);
          });

          const idPhoto = await readBase64(document.getElementById('regIdPhoto'));
          const licensePhoto = await readBase64(document.getElementById('regLicensePhoto'));

          const payload = {
            name: document.getElementById('regName').value,
            phone: document.getElementById('regPhone').value,
            idCardNo: document.getElementById('regIdCard').value,
            password: document.getElementById('regPassword').value,
            skill: document.getElementById('regSkill').value,
            idCardPhoto: idPhoto,
            licensePhoto: licensePhoto
          };

          try {
            const res = await fetch('/api/tech/register', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });
            const data = await res.json();
            alert(data.message);
            if (data.success) {
              currentTech = data.tech;
              localStorage.setItem('qt30_tech_user', JSON.stringify(currentTech));
              document.getElementById('authModal').classList.add('hidden');
              loadProfile();
              loadHallCases();
            }
          } catch(e) {
            alert('註冊失敗');
          }
        });

        function logoutTech() {
          localStorage.removeItem('qt30_tech_user');
          currentTech = null;
          location.reload();
        }

        async function loadProfile() {
          if (!currentTech) return;
          try {
            const res = await fetch('/api/tech/profile?phone=' + currentTech.phone);
            const data = await res.json();
            if (data.success) {
              currentTech = data.tech;
              document.getElementById('pointsBalance').innerText = data.tech.points;
              document.getElementById('headerName').innerText = data.tech.name;
              document.getElementById('headerPhone').innerText = data.tech.phone;
              
              const vBadge = document.getElementById('verifyBadge');
              const banner = document.getElementById('pendingBanner');
              if (data.tech.verifiedStatus === '已通過') {
                vBadge.className = 'text-[11px] font-bold px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800';
                vBadge.innerText = '✓ 實名認證通過';
                banner.classList.add('hidden');
              } else {
                vBadge.className = 'text-[11px] font-bold px-2 py-0.5 rounded-full bg-amber-950 text-amber-400 border border-amber-800';
                vBadge.innerText = '⏳ 實名審核中';
                banner.classList.remove('hidden');
              }
            }
          } catch(e) {}
        }

        function viewPhoto(src) {
          document.getElementById('modalImg').src = src;
          document.getElementById('imgModal').classList.remove('hidden');
        }

        function switchTab(tab) {
          ['hall', 'map', 'topup'].forEach(t => {
            document.getElementById('tab-' + t).classList.add('hidden');
            const btn = document.getElementById('tabBtn-' + t);
            btn.className = 'tab-btn px-4 py-3 font-bold text-sm text-slate-400 hover:text-slate-200 border-b-2 border-transparent flex items-center gap-1.5';
          });
          document.getElementById('tab-' + tab).classList.remove('hidden');
          const activeBtn = document.getElementById('tabBtn-' + tab);
          activeBtn.className = 'tab-btn px-4 py-3 font-bold text-sm text-blue-400 border-b-2 border-blue-500 flex items-center gap-1.5';

          if (tab === 'map') {
            setTimeout(() => {
              if (!map) initMap();
              else map.invalidateSize();
            }, 100);
          }
        }

        async function loadHallCases() {
          if (!currentTech) return;
          const list = document.getElementById('casesList');
          try {
            const res = await fetch('/api/cases?phone=' + currentTech.phone);
            const data = await res.json();
            if (!data.cases || data.cases.length === 0) {
              list.innerHTML = '<div class="col-span-2 p-12 bg-slate-900 rounded-2xl border border-slate-800 text-center text-slate-500 text-sm">目前尚無等待報修的案件</div>';
              return;
            }
            list.innerHTML = data.cases.map(c => `
              <div class="bg-slate-900 border ${c.unlocked ? 'border-emerald-500/50 bg-emerald-950/10' : 'border-slate-800'} rounded-2xl p-5 flex flex-col justify-between space-y-4 shadow-sm">
                <div>
                  <div class="flex justify-between items-start gap-2">
                    <div>
                      <span class="inline-block px-2.5 py-0.5 bg-blue-900/60 border border-blue-700/50 text-blue-300 font-bold text-xs rounded-md">${c.item}</span>
                      <span class="font-mono text-xs text-slate-500 ml-1.5">${c.id}</span>
                    </div>
                    <span class="text-xs font-bold px-2 py-0.5 rounded ${c.unlocked ? 'bg-emerald-900/80 text-emerald-300 border border-emerald-700' : 'bg-slate-800 text-slate-400'}">
                      ${c.unlocked ? '✓ 已解鎖' : '🔒 未解鎖'}
                    </span>
                  </div>

                  <div class="mt-3 flex gap-3 items-start">
                    ${c.photo ? `
                      <img src="${c.photo}" onclick="viewPhoto('${c.photo}')" class="w-20 h-20 object-cover rounded-xl border border-slate-700 cursor-pointer hover:opacity-80 transition shrink-0" title="點擊放大">
                    ` : `
                      <div class="w-20 h-20 bg-slate-800 rounded-xl border border-slate-700 flex items-center justify-center text-xs text-slate-500 shrink-0">無照片</div>
                    `}
                    <div class="space-y-1 text-xs">
                      <div class="font-semibold text-slate-200">📍 修繕地址：<span class="${c.unlocked ? 'text-white font-bold' : 'text-slate-400 font-mono'}">${c.address}</span></div>
                      <div class="text-slate-300">👤 客戶稱呼：<b>${c.clientName}</b></div>
                      <div class="text-slate-300">📞 聯絡電話：<span class="${c.unlocked ? 'text-emerald-400 font-bold font-mono text-sm' : 'text-slate-400 font-mono'}">${c.clientPhone}</span></div>
                      <div class="text-amber-300 font-semibold">💰 客戶預算：NT$ ${c.depositAmount}</div>
                    </div>
                  </div>

                  <p class="text-xs text-slate-400 mt-3 bg-slate-950/60 p-2.5 rounded-xl border border-slate-800/80 leading-relaxed">
                    📝 <b>狀況描述：</b>${c.description}
                  </p>
                </div>

                <div class="pt-2 border-t border-slate-800">
                  ${c.unlocked ? `
                    <div class="flex gap-2">
                      <a href="tel:${c.clientPhone}" class="flex-1 bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-2.5 rounded-xl text-xs text-center transition flex items-center justify-center gap-1">
                        📞 立即撥打電話
                      </a>
                    </div>
                  ` : `
                    <button onclick="unlockCase('${c.id}')" class="w-full bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 text-white font-bold py-2.5 rounded-xl text-xs shadow transition flex items-center justify-center gap-1.5">
                      💎 扣 50 點搶單 (解鎖電話與地址)
                    </button>
                  `}
                </div>
              </div>
            `).join('');
          } catch(e) {}
        }

        async function unlockCase(caseId) {
          if (!confirm('確認扣除 50 點數以解鎖此案件的客戶聯絡方式？')) return;
          try {
            const res = await fetch(`/api/cases/${caseId}/unlock?phone=${currentTech.phone}`, { method: 'POST' });
            const data = await res.json();
            if (data.success) {
              alert('🎉 搶單解鎖成功！請立即與客戶聯繫。');
              loadProfile();
              loadHallCases();
            } else {
              alert(data.message || '解鎖失敗');
              if (data.message.includes('點數不足')) switchTab('topup');
            }
          } catch(e) {
            alert('請求失敗，請稍後再試');
          }
        }

        async function doTopup(amount) {
          if (!currentTech) return;
          try {
            const res = await fetch('/api/tech/topup', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ phone: currentTech.phone, amount: amount })
            });
            const html = await res.text();
            document.open();
            document.write(html);
            document.close();
          } catch(e) {
            alert('儲值發起失敗');
          }
        }

        function initMap() {
          map = L.map('map').setView([24.5, 121.0], 8);
          L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            maxZoom: 18,
            attribution: '© OpenStreetMap'
          }).addTo(map);

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
              listDiv.innerHTML = '<div class="col-span-3 text-center text-slate-500 py-4 text-xs">目前尚無卡位據點</div>';
              return;
            }

            listDiv.innerHTML = data.spots.map(s => `
              <div class="p-3.5 bg-slate-900 border border-slate-800 rounded-xl flex justify-between items-center">
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
          } catch(e) {}
        }

        async function saveNewSpot() {
          if (!currentTech) return;
          const name = document.getElementById('newSpotName').value.trim();
          const lat = parseFloat(document.getElementById('newSpotLat').value);
          const lng = parseFloat(document.getElementById('newSpotLng').value);
          const radius = parseInt(document.getElementById('newSpotRadius').value) || 5;

          if (!name || isNaN(lat) || isNaN(lng)) {
            alert('請填寫完整資訊！');
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
                technicianPhone: currentTech.phone,
                technicianName: currentTech.name
              })
            });
            const data = await res.json();
            if (data.success) {
              alert('🎉 成功卡位服務地區：' + name);
              document.getElementById('addSpotModal').classList.add('hidden');
              document.getElementById('newSpotName').value = '';
              loadSpots();
            }
          } catch(e) {
            alert('儲存失敗');
          }
        }

        checkAuth();
      </script>
    </body>
    </html>
    """

# --- 派工管理與實名審核後台 (/admin) ---
@app.get("/admin", response_class=HTMLResponse)
def serve_admin_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 派工管理與實名審核後台</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
      <!-- 後台登入密碼彈窗 -->
      <div id="adminAuthModal" class="hidden fixed inset-0 bg-black/80 flex justify-center items-center z-50 p-4">
        <div class="bg-white rounded-2xl max-w-sm w-full p-6 shadow-2xl space-y-4">
          <div class="text-center">
            <div class="w-12 h-12 bg-blue-100 text-blue-600 rounded-full flex items-center justify-center mx-auto text-xl font-bold">🔒</div>
            <h2 class="text-xl font-bold text-slate-800 mt-2">管理員密碼驗證</h2>
            <p class="text-xs text-slate-500 mt-1">請輸入後台管理密碼以進入派工後台</p>
          </div>
          <form id="adminAuthForm" class="space-y-3">
            <input type="password" id="adminPasswordInput" placeholder="請輸入管理密碼 (預設 admin888)" class="w-full p-3 border border-slate-300 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-blue-500">
            <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 rounded-lg text-sm transition shadow">
              驗證登入
            </button>
          </form>
        </div>
      </div>

      <div class="max-w-7xl mx-auto">
        <header class="flex flex-col sm:flex-row justify-between items-center mb-6 bg-white p-6 rounded-2xl shadow-sm gap-4">
          <div>
            <h1 class="text-2xl font-black text-slate-800">QT30 管理員總控制台</h1>
            <p class="text-sm text-slate-500 mt-1">修繕案件派工、金流管控與師傅實名制 KYC 審核</p>
          </div>
          <div class="flex space-x-2">
            <button onclick="switchAdminTab('cases')" id="adminTabBtn-cases" class="px-4 py-2 rounded-lg text-sm font-bold bg-blue-600 text-white shadow">
              📋 案件派工管理
            </button>
            <button onclick="switchAdminTab('techs')" id="adminTabBtn-techs" class="px-4 py-2 rounded-lg text-sm font-bold bg-slate-200 text-slate-700 hover:bg-slate-300">
              🛡️ 師傅實名審核
            </button>
            <button onclick="logoutAdmin()" class="px-3 py-2 rounded-lg text-xs font-semibold bg-rose-50 text-rose-600 border border-rose-200">
              登出後台
            </button>
          </div>
        </header>

        <!-- 區塊 1: 案件管理 -->
        <section id="adminSec-cases" class="bg-white rounded-2xl shadow-sm overflow-hidden border border-slate-200">
          <div class="p-4 border-b border-slate-200 flex justify-between items-center bg-slate-50">
            <h2 class="font-bold text-slate-800 text-sm">📋 全站報修案件清單</h2>
            <button onclick="loadCases()" class="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg shadow transition">
              🔄 重新整理清單
            </button>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm text-slate-600">
              <thead class="bg-slate-100 text-slate-700 font-bold border-b border-slate-200">
                <tr>
                  <th class="p-4">案件編號 / 時間</th>
                  <th class="p-4">客戶資訊</th>
                  <th class="p-4">現場照片</th>
                  <th class="p-4">修繕項目 / 內容</th>
                  <th class="p-4">預算金額 / 付款狀態</th>
                  <th class="p-4">接單師傅</th>
                  <th class="p-4">案件狀態</th>
                  <th class="p-4 text-center">操作 / 收款</th>
                </tr>
              </thead>
              <tbody id="caseTableBody" class="divide-y divide-slate-100">
                <tr><td colspan="8" class="p-8 text-center text-slate-400">載入中...</td></tr>
              </tbody>
            </table>
          </div>
        </section>

        <!-- 區塊 2: 師傅實名審核 -->
        <section id="adminSec-techs" class="hidden bg-white rounded-2xl shadow-sm overflow-hidden border border-slate-200">
          <div class="p-4 border-b border-slate-200 flex justify-between items-center bg-slate-50">
            <h2 class="font-bold text-slate-800 text-sm">🛡️ 師傅實名制 KYC 審核名單</h2>
            <button onclick="loadTechs()" class="bg-blue-600 hover:bg-blue-700 text-white text-xs font-semibold px-3 py-1.5 rounded-lg shadow transition">
              🔄 重新整理師傅名單
            </button>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm text-slate-600">
              <thead class="bg-slate-100 text-slate-700 font-bold border-b border-slate-200">
                <tr>
                  <th class="p-4">師傅姓名 / 電話</th>
                  <th class="p-4">身分證號 / 專業</th>
                  <th class="p-4">身分證照片 (KYC)</th>
                  <th class="p-4">技師證照</th>
                  <th class="p-4">點數餘額</th>
                  <th class="p-4">審核狀態</th>
                  <th class="p-4 text-center">審核操作</th>
                </tr>
              </thead>
              <tbody id="techTableBody" class="divide-y divide-slate-100">
                <tr><td colspan="7" class="p-8 text-center text-slate-400">載入中...</td></tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <div id="imgModal" class="hidden fixed inset-0 bg-black bg-opacity-75 flex justify-center items-center z-50 p-4" onclick="this.classList.add('hidden')">
        <img id="modalImg" src="" class="max-w-full max-h-[85vh] rounded-lg shadow-2xl">
      </div>

      <script>
        function checkAdminAuth() {
          const token = sessionStorage.getItem('qt30_admin_token');
          if (!token) {
            document.getElementById('adminAuthModal').classList.remove('hidden');
          } else {
            document.getElementById('adminAuthModal').classList.add('hidden');
            loadCases();
          }
        }

        document.getElementById('adminAuthForm').addEventListener('submit', async (e) => {
          e.preventDefault();
          const pwd = document.getElementById('adminPasswordInput').value;
          const res = await fetch('/api/admin/verify-login', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ password: pwd })
          });
          const data = await res.json();
          if (data.success) {
            sessionStorage.setItem('qt30_admin_token', data.token);
            document.getElementById('adminAuthModal').classList.add('hidden');
            loadCases();
          } else {
            alert(data.message);
          }
        });

        function logoutAdmin() {
          sessionStorage.removeItem('qt30_admin_token');
          location.reload();
        }

        function switchAdminTab(tab) {
          if (tab === 'cases') {
            document.getElementById('adminSec-cases').classList.remove('hidden');
            document.getElementById('adminSec-techs').classList.add('hidden');
            document.getElementById('adminTabBtn-cases').className = 'px-4 py-2 rounded-lg text-sm font-bold bg-blue-600 text-white shadow';
            document.getElementById('adminTabBtn-techs').className = 'px-4 py-2 rounded-lg text-sm font-bold bg-slate-200 text-slate-700 hover:bg-slate-300';
            loadCases();
          } else {
            document.getElementById('adminSec-cases').classList.add('hidden');
            document.getElementById('adminSec-techs').classList.remove('hidden');
            document.getElementById('adminTabBtn-techs').className = 'px-4 py-2 rounded-lg text-sm font-bold bg-blue-600 text-white shadow';
            document.getElementById('adminTabBtn-cases').className = 'px-4 py-2 rounded-lg text-sm font-bold bg-slate-200 text-slate-700 hover:bg-slate-300';
            loadTechs();
          }
        }

        function viewPhoto(src) {
          document.getElementById('modalImg').src = src;
          document.getElementById('imgModal').classList.remove('hidden');
        }

        function copyPayLink(caseId) {
          const url = window.location.origin + '/api/pay/' + caseId;
          navigator.clipboard.writeText(url).then(() => {
            alert('💳 專屬付款網址已複製！\\n' + url);
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
                    <img src="${c.photo}" onclick="viewPhoto('${c.photo}')" class="w-14 h-14 object-cover rounded-lg border border-slate-200 cursor-pointer hover:opacity-80 transition" title="點擊放大">
                  ` : `<span class="text-xs text-slate-300">無照片</span>`}
                </td>
                <td class="p-4">
                  <span class="inline-block px-2 py-0.5 bg-slate-100 text-slate-700 rounded text-xs font-semibold">${c.item}</span>
                  <p class="text-xs text-slate-500 mt-1 max-w-xs truncate">${c.description}</p>
                </td>
                <td class="p-4">
                  <div class="flex items-center space-x-1">
                    <span class="text-xs text-slate-400">NT$</span>
                    <input type="number" id="amt-${c.id}" value="${c.depositAmount}" class="w-20 border border-slate-300 rounded px-1.5 py-0.5 text-xs font-bold text-slate-800">
                  </div>
                  <span class="inline-block mt-1 px-2 py-0.5 rounded text-xs font-bold ${c.paymentStatus === '已付款' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}">
                    ${c.paymentStatus}
                  </span>
                </td>
                <td class="p-4">
                  <input type="text" id="tech-${c.id}" value="${c.technician || ''}" placeholder="未指派" class="border border-slate-300 rounded px-2 py-1 text-xs w-24">
                </td>
                <td class="p-4">
                  <select id="status-${c.id}" class="border border-slate-300 rounded px-2 py-1 text-xs">
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
                    🔗 付款連結
                  </button>
                </td>
              </tr>
            `).join('');
          } catch(e) {}
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
              alert('案件已更新！');
              loadCases();
            }
          } catch(e) {
            alert('更新失敗');
          }
        }

        async function loadTechs() {
          const tbody = document.getElementById('techTableBody');
          try {
            const res = await fetch('/api/admin/technicians');
            const data = await res.json();
            if (!data.technicians || data.technicians.length === 0) {
              tbody.innerHTML = '<tr><td colspan="7" class="p-8 text-center text-slate-400">目前尚無註冊師傅</td></tr>';
              return;
            }
            tbody.innerHTML = data.technicians.map(t => `
              <tr class="hover:bg-slate-50 transition">
                <td class="p-4">
                  <div class="font-bold text-slate-800">${t.name}</div>
                  <div class="text-xs font-mono text-slate-500">${t.phone}</div>
                </td>
                <td class="p-4">
                  <div class="font-mono text-xs font-bold text-slate-700">${t.id_card_no}</div>
                  <span class="inline-block mt-0.5 px-2 py-0.5 bg-blue-50 text-blue-700 rounded text-xs">${t.skill}</span>
                </td>
                <td class="p-4">
                  ${t.id_card_photo ? `
                    <img src="${t.id_card_photo}" onclick="viewPhoto('${t.id_card_photo}')" class="w-12 h-12 object-cover rounded border border-slate-200 cursor-pointer hover:opacity-80" title="點擊放大核對">
                  ` : `<span class="text-xs text-slate-300">未附照片</span>`}
                </td>
                <td class="p-4">
                  ${t.license_photo ? `
                    <img src="${t.license_photo}" onclick="viewPhoto('${t.license_photo}')" class="w-12 h-12 object-cover rounded border border-slate-200 cursor-pointer hover:opacity-80" title="點擊放大證照">
                  ` : `<span class="text-xs text-slate-300">未附證照</span>`}
                </td>
                <td class="p-4">
                  <span class="font-bold text-amber-600 font-mono text-sm">${t.points} 點</span>
                </td>
                <td class="p-4">
                  <span class="inline-block px-2.5 py-1 rounded text-xs font-bold ${t.verified_status === '已通過' ? 'bg-emerald-100 text-emerald-700' : (t.verified_status === '已拒絕' ? 'bg-rose-100 text-rose-700' : 'bg-amber-100 text-amber-700')}">
                    ${t.verified_status}
                  </span>
                </td>
                <td class="p-4 text-center space-x-1 whitespace-nowrap">
                  <button onclick="setTechVerify('${t.phone}', '已通過')" class="bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold px-2.5 py-1.5 rounded transition">
                    ✓ 通過
                  </button>
                  <button onclick="setTechVerify('${t.phone}', '已拒絕')" class="bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold px-2.5 py-1.5 rounded transition">
                    ✕ 拒絕
                  </button>
                </td>
              </tr>
            `).join('');
          } catch(e) {}
        }

        async function setTechVerify(phone, status) {
          if (!confirm(`確認將此師傅設為【${status}】？`)) return;
          try {
            const res = await fetch('/api/admin/verify-technician', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ phone, status })
            });
            const data = await res.json();
            if (data.success) {
              alert('審核狀態更新完成！');
              loadTechs();
            }
          } catch(e) {
            alert('操作失敗');
          }
        }

        checkAdminAuth();
      </script>
    </body>
    </html>
    """

@app.get("/", response_class=HTMLResponse)
def serve_home():
    return """
    <script>window.location.href = '/app';</script>
    """
