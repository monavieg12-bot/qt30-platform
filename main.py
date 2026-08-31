import os
import time
import urllib.parse
import hashlib
import requests
from fastapi import FastAPI, HTTPException, Request, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

app = FastAPI(title="QT30 派工與金流平台")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------- LINE 推播設定 -----------------
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

# ----------------- 綠界 ECPay 金流設定 (官方測試環境) -----------------
ECPAY_MERCHANT_ID = os.getenv("ECPAY_MERCHANT_ID", "2000132")
ECPAY_HASH_KEY = os.getenv("ECPAY_HASH_KEY", "5294y06JbISpM5x9")
ECPAY_HASH_IV = os.getenv("ECPAY_HASH_IV", "v77hoKGq4kWxNNIS")
ECPAY_PAYMENT_URL = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5"

def ecpay_url_encode(s: str) -> str:
    encoded = urllib.parse.quote_plus(s)
    # 綠界特定字元置換規則
    replacements = {
        '%2D': '-', '%5F': '_', '%2E': '.', '%21': '!', '%2A': '*',
        '%28': '(', '%29': ')', '%20': '+'
    }
    for old, new in replacements.items():
        encoded = encoded.replace(old, new)
    return encoded

def generate_check_mac_value(params: dict, hash_key: str, hash_iv: str) -> str:
    # 1. 依照鍵值 A-Z 排序
    sorted_params = sorted(params.items(), key=lambda x: x[0])
    # 2. 組合字串
    raw_str = f"HashKey={hash_key}&" + "&".join([f"{k}={v}" for k, v in sorted_params]) + f"&HashIV={hash_iv}"
    # 3. URL Encode
    encoded_str = ecpay_url_encode(raw_str)
    # 4. 轉小寫
    lower_str = encoded_str.lower()
    # 5. SHA256 加密並轉大寫
    return hashlib.sha256(lower_str.encode('utf-8')).hexdigest().upper()

# ----------------- 資料庫與模型 -----------------
class CaseCreate(BaseModel):
    clientName: Optional[str] = "未填寫"
    clientPhone: Optional[str] = "未填寫"
    address: Optional[str] = "未填寫"
    item: Optional[str] = "一般修繕"
    description: Optional[str] = "無詳細描述"
    depositAmount: Optional[int] = 500  # 預設修繕定金 500 元

class CaseUpdate(BaseModel):
    status: Optional[str] = None
    technician: Optional[str] = None
    paymentStatus: Optional[str] = None

cases_db = []

# 建立案件 API（同時發送 LINE 通知）
@app.post("/api/cases")
def create_case(data: CaseCreate):
    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"QT{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    case_id = f"CASE-{trade_no[-6:]}"

    new_case = {
        "id": case_id,
        "tradeNo": trade_no,
        "status": "待派工",
        "paymentStatus": "未付款",
        "depositAmount": data.depositAmount,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **data.dict()
    }
    cases_db.insert(0, new_case)

    # 觸發 LINE 推播
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
        f"⚡ 系統已為客戶生成綠界測試付款訂單！"
    )
    send_line_notification(msg)
    return {"success": True, "case": new_case}

# 綠界付款跳轉產生器
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

    # 自動提交表單導向綠界收銀台
    inputs_html = "".join([f'<input type="hidden" name="{k}" value="{v}" />' for k, v in params.items()])
    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head><title>前往綠界金流支付...</title><meta charset="utf-8"></head>
    <body onload="document.getElementById('ecpay_form').submit();" style="display:flex;justify-content:center;align-items:center;height:100vh;font-family:sans-serif;">
        <div style="text-align:center;">
            <h2>正在跳轉至綠界金流安全收銀台...</h2>
            <p>案件編號：{target['id']} | 金額：NT$ {target['depositAmount']}</p>
            <form id="ecpay_form" method="POST" action="{ECPAY_PAYMENT_URL}">
                {inputs_html}
            </form>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

# 綠界付款成功回傳 Webhook
@app.post("/api/ecpay/callback")
async def ecpay_callback(request: Request):
    form_data = await request.form()
    data = dict(form_data)
    trade_no = data.get("MerchantTradeNo")
    rtn_code = data.get("RtnCode")

    if rtn_code == "1":  # 付款成功
        for c in cases_db:
            if c["tradeNo"] == trade_no:
                c["paymentStatus"] = "已付款"
                # LINE 支付成功通知
                msg = (
                    f"💳 【QT30 案件已付款通知】\n"
                    f"------------------------\n"
                    f"📌 案件編號：{c['id']}\n"
                    f"👤 客戶：{c['clientName']}\n"
                    f"💰 付款金額：NT$ {c['depositAmount']}\n"
                    f"🎉 付款狀態：已完成付款 (綠界測試金流)\n"
                    f"------------------------\n"
                    f"請儘速確認派工！"
                )
                send_line_notification(msg)
                break
    return "1|OK"

# 案件列表 API
@app.get("/api/cases")
def get_cases():
    return {"success": True, "cases": cases_db}

# 更新案件狀態
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

# 靜態檔案路由
if os.path.exists("public"):
    app.mount("/static", StaticFiles(directory="public"), name="static")

@app.get("/app")
def serve_app():
    if os.path.exists("public/app.html"):
        return FileResponse("public/app.html")
    return {"message": "QT30 發案系統運作中"}

@app.get("/admin")
def serve_admin():
    if os.path.exists("public/admin.html"):
        return FileResponse("public/admin.html")
    return {"message": "QT30 派工後台運作中"}

@app.get("/")
def serve_index():
    if os.path.exists("public/index.html"):
        return FileResponse("public/index.html")
    return {"message": "QT30 派工平台金流與 LINE 系統已連線！"}
