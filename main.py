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

app = FastAPI(title="QT30 全國修繕派工與社區卡位平台 (全台五大區升級版)")

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

# --- 綠界金鑰 ---
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
    region: Optional[str] = "北北基"
    community: Optional[str] = "淡海新市鎮"
    item: Optional[str] = "水電維修"
    areaPing: Optional[float] = 1.0
    needSupervision: Optional[bool] = False
    agreeSupervisionTerms: Optional[bool] = False
    description: Optional[str] = "無詳細描述"
    baseAmount: Optional[int] = 1500
    photo: Optional[str] = None

class CaseUpdate(BaseModel):
    status: Optional[str] = None
    technician: Optional[str] = None
    baseAmount: Optional[int] = None
    needSupervision: Optional[bool] = None
    paymentStatus: Optional[str] = None

class ClaimCommunityRequest(BaseModel):
    communityId: str
    techName: str
    techPhone: str
    specialty: str

class TechClaimRequest(BaseModel):
    caseId: str
    techName: str
    techPhone: str

class TopupOrderRequest(BaseModel):
    techPhone: str
    amount: int
    points: int

# --- 全台核心指標社區資料庫 (北北基 / 桃竹 / 大台中 / 南台灣 / 東部) ---
COMMUNITIES = [
    # 1. 北北基
    {"id": "COM-N01", "region": "北北基", "name": "宏盛水悅", "lat": 25.1950, "lng": 121.4330, "address": "新北市淡水區沙崙路一段", "units": "1,200 戶", "slots": {"水電維修": "王師傅", "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-N02", "region": "北北基", "name": "海洋都心 (1~3期)", "lat": 25.1985, "lng": 121.4372, "address": "新北市淡水區新市五路三段", "units": "3,000 戶", "slots": {"水電維修": None, "泥作防水": "阿國師傅", "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-N03", "region": "北北基", "name": "台北灣全區 (江南/四季)", "lat": 25.1915, "lng": 121.4320, "address": "新北市淡水區新市一路一段", "units": "2,500 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-N04", "region": "北北基", "name": "日勝幸福站 (浮洲合宜)", "lat": 25.0035, "lng": 121.4485, "address": "新北市板橋區合宜路", "units": "4,400 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-N05", "region": "北北基", "name": "遠雄未來城/未來市", "lat": 25.0760, "lng": 121.3725, "address": "新北市林口區文化三路一段", "units": "1,800 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-N06", "region": "北北基", "name": "新店美河市", "lat": 24.9725, "lng": 121.5310, "address": "新北市新店區中央路", "units": "2,200 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-N07", "region": "北北基", "name": "基隆城上城", "lat": 25.1150, "lng": 121.7220, "address": "基隆市安樂區麥金路", "units": "1,890 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},

    # 2. 桃竹苗
    {"id": "COM-H01", "region": "桃竹苗", "name": "桃園藝文中悅一品聚落", "lat": 24.9985, "lng": 121.2980, "address": "桃園市桃園區中正路", "units": "1,200 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-H02", "region": "桃竹苗", "name": "青埔高鐵明日苑/聯上世界", "lat": 25.0125, "lng": 121.2140, "address": "桃園市中壢區高鐵南路", "units": "2,100 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-H03", "region": "桃竹苗", "name": "新竹關埔東方明珠 / 十詠康橋", "lat": 24.7895, "lng": 121.0150, "address": "新竹市東區慈雲路", "units": "1,500 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-H04", "region": "桃竹苗", "name": "竹北國泰 TWIN PARK / 豐邑高鐵", "lat": 24.8080, "lng": 121.0380, "address": "新竹縣竹北市復興三路二段", "units": "1,600 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},

    # 3. 大台中
    {"id": "COM-C01", "region": "大台中", "name": "總太 2020 / 心之所向 (大坑造鎮)", "lat": 24.1685, "lng": 120.7320, "address": "台中市北屯區環太東路", "units": "3,800 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-C02", "region": "大台中", "name": "大城十方 / 登陽溪上月 (北屯機捷)", "lat": 24.1850, "lng": 120.7050, "address": "台中市北屯區敦富路", "units": "1,800 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-C03", "region": "大台中", "name": "鄉林皇居 / 聯聚方庭 (七期聚落)", "lat": 24.1670, "lng": 120.6385, "address": "台中市西屯區台灣大道三段", "units": "1,000 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-C04", "region": "大台中", "name": "太子雲世紀 (中科造鎮)", "lat": 24.1910, "lng": 120.6120, "address": "台中市西屯區西屯路三段", "units": "1,200 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},

    # 4. 南台灣 (台南 / 高雄)
    {"id": "COM-S01", "region": "南台灣", "name": "台南善化 LM 特區 (南科造鎮)", "lat": 23.1250, "lng": 120.3150, "address": "台南市善化區陽光大道", "units": "2,800 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-S02", "region": "南台灣", "name": "台南安平新悅城 (五期重劃)", "lat": 22.9850, "lng": 120.1720, "address": "台南市安平區國平路", "units": "1,500 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-S03", "region": "南台灣", "name": "高雄美術館聚落 (美術帝國/御桂園)", "lat": 22.6580, "lng": 120.2920, "address": "高雄市鼓山區美術東二路", "units": "2,200 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-S04", "region": "南台灣", "name": "高雄農 16 特區 (京城國寶/曼哈頓)", "lat": 22.6610, "lng": 120.3040, "address": "高雄市鼓山區神農路", "units": "1,800 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}},
    {"id": "COM-S05", "region": "南台灣", "name": "高雄左營高鐵 (鑫高鐵/站前特區)", "lat": 22.6880, "lng": 120.3120, "address": "高雄市左營區華夏路", "units": "1,400 戶", "slots": {"水電維修": None, "泥作防水": None, "油漆粉刷": None, "冷氣空調": None}}
]

cases_db = []
techs_db = {
    "0912345678": {"name": "王師傅 (北部水電)", "phone": "0912345678", "points": 800, "claimedCommunities": ["COM-N01"]},
    "0988776655": {"name": "阿國師傅 (泥作防水)", "phone": "0988776655", "points": 1500, "claimedCommunities": ["COM-N02"]}
}
topup_orders = {}

def compute_case_finances(base_amt: int, need_sup: bool):
    supervision_fee = int(base_amt * 0.08) if need_sup else 0
    total_amount = base_amt + supervision_fee
    pts_required = max(50, int(base_amt * 0.03))
    return supervision_fee, total_amount, pts_required

# --- API 路由 ---
@app.get("/api/communities")
def get_communities(region: Optional[str] = None):
    if region and region != "全台":
        filtered = [c for c in COMMUNITIES if c["region"] == region]
        return {"success": True, "communities": filtered}
    return {"success": True, "communities": COMMUNITIES}

@app.post("/api/communities/claim")
def claim_community(req: ClaimCommunityRequest):
    com = next((c for c in COMMUNITIES if c["id"] == req.communityId), None)
    if not com:
        raise HTTPException(status_code=404, detail="找不到該社區")

    tech = techs_db.get(req.techPhone)
    if not tech or tech.get("points", 0) < 1000:
        return {"success": False, "needTopup": True, "msg": "卡位需預先儲值 NT$ 1,000（實得 1,050 點）！請先完成儲值。"}

    current_occupant = com["slots"].get(req.specialty)
    if current_occupant and current_occupant != req.techName:
        return {"success": False, "msg": f"抱歉！【{com['name']}】的【{req.specialty}】已被【{current_occupant}】卡位。"}

    com["slots"][req.specialty] = req.techName
    if "claimedCommunities" not in tech:
        tech["claimedCommunities"] = []
    if req.communityId not in tech["claimedCommunities"]:
        tech["claimedCommunities"].append(req.communityId)

    msg = f"🎖️ 【QT30 社區卡位】{req.techName} 成功駐點【{com['region']}·{com['name']}】專屬【{req.specialty}】師傅！"
    send_line_notification(msg)
    return {"success": True, "msg": f"🎉 恭喜！您已成功卡位【{com['name']}】專屬【{req.specialty}】席位！"}

@app.post("/api/cases")
def create_case(data: CaseCreate):
    timestamp_str = datetime.now().strftime("%Y%m%d%H%M%S")
    trade_no = f"QT{timestamp_str[-10:]}{int(time.time()*1000)%1000:03d}"
    case_id = f"CASE-{trade_no[-6:]}"

    base_amt = data.baseAmount or 1500
    need_sup = data.needSupervision or False
    sup_fee, total_amt, pts = compute_case_finances(base_amt, need_sup)

    lat, lng = 25.0400, 121.5300
    matched_com = next((c for c in COMMUNITIES if c["name"] in (data.address + (data.community or ""))), None)
    if matched_com:
        lat, lng = matched_com["lat"], matched_com["lng"]

    new_case = {
        "id": case_id,
        "tradeNo": trade_no,
        "status": "待派工",
        "technician": "未指派",
        "technicianPhone": "",
        "paymentStatus": "未付款",
        "baseAmount": base_amt,
        "supervisionFee": sup_fee,
        "depositAmount": total_amt,
        "pointsRequired": pts,
        "areaPing": data.areaPing or 1.0,
        "needSupervision": need_sup,
        "agreeSupervisionTerms": data.agreeSupervisionTerms or False,
        "lat": lat,
        "lng": lng,
        "photo": data.photo,
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **data.dict()
    }
    cases_db.insert(0, new_case)

    sup_info = f"👷‍♂️ 【QT30 官方監工 (8%)]: NT$ {sup_fee:,} (已簽署免責條款)" if need_sup else "👷‍♂️ 【一般派工 (無平台監工)】"

    msg = (
        f"🔔 【QT30 新進報修單 - {new_case.get('region', '北北基')}】\n"
        f"------------------------\n"
        f"📌 案件編號：{new_case['id']}\n"
        f"👤 客戶姓名：{new_case['clientName']} ({new_case['clientPhone']})\n"
        f"📍 修繕地址：{new_case['address']}\n"
        f"🔧 工項坪數：{new_case['item']} ({new_case['areaPing']} 坪)\n"
        f"💵 師傅工程款：NT$ {base_amt:,}\n"
        f"{sup_info}\n"
        f"💳 應付總額：NT$ {total_amt:,}\n"
        f"🎯 師傅接單扣點：{pts} 點\n"
        f"------------------------\n"
        f"⚡ 駐點師傅優先推播中！"
    )
    send_line_notification(msg)

    email_html = f"""
    <h2>【QT30 修繕發案備份 - {new_case.get('region', '北北基')}】</h2>
    <p><b>案件編號：</b> {new_case['id']}</p>
    <p><b>客戶：</b> {new_case['clientName']}（{new_case['clientPhone']}）</p>
    <p><b>地址：</b> {new_case['address']}</p>
    <p><b>工項：</b> {new_case['item']}（{new_case['areaPing']} 坪）</p>
    <hr>
    <p><b>🔨 師傅工程款：</b> NT$ {base_amt:,}</p>
    <p><b>🏢 平台 8% 監工費：</b> NT$ {sup_fee:,}</p>
    <p><b>💰 應付總額：</b> NT$ {total_amt:,}</p>
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
            if data.baseAmount is not None:
                c["baseAmount"] = data.baseAmount
            if data.needSupervision is not None:
                c["needSupervision"] = data.needSupervision
            
            sup_fee, total_amt, pts = compute_case_finances(c["baseAmount"], c["needSupervision"])
            c["supervisionFee"] = sup_fee
            c["depositAmount"] = total_amt
            c["pointsRequired"] = pts

            if data.paymentStatus is not None:
                c["paymentStatus"] = data.paymentStatus
            return {"success": True, "case": c}
    raise HTTPException(status_code=404, detail="找不到案件")

@app.get("/api/tech/profile")
def get_tech_profile(phone: str):
    if phone not in techs_db:
        techs_db[phone] = {"name": f"師傅 ({phone[-4:]})", "phone": phone, "points": 300, "claimedCommunities": []}
    return {"success": True, "profile": techs_db[phone]}

@app.post("/api/tech/claim")
def claim_case(req: TechClaimRequest):
    case = next((c for c in cases_db if c["id"] == req.caseId), None)
    if not case:
        raise HTTPException(status_code=404, detail="案件不存在")
    if case["status"] != "待派工":
        raise HTTPException(status_code=400, detail="該案件已被接單")

    tech = techs_db.get(req.techPhone)
    if not tech:
        techs_db[req.techPhone] = {"name": req.techName, "phone": req.techPhone, "points": 300, "claimedCommunities": []}
        tech = techs_db[req.techPhone]

    cost_pts = case.get("pointsRequired", 50)
    if tech["points"] < cost_pts:
        return {"success": False, "msg": f"點數不足！本案需扣 {cost_pts} 點，目前餘額 {tech['points']} 點。"}

    tech["points"] -= cost_pts
    case["status"] = "施工中"
    case["technician"] = req.techName
    case["technicianPhone"] = req.techPhone

    msg = f"⚡ 【QT30 接單】{req.techName} 扣除 {cost_pts} 點接下案件 {case['id']}！"
    send_line_notification(msg)
    return {"success": True, "msg": "接單成功！已解鎖客戶完整資訊", "case": case, "remainingPoints": tech["points"]}

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
        "TradeDesc": ecpay_url_encode("QT30師傅點數儲值與社區卡位"),
        "ItemName": f"QT30 接單點數 {req.points} 點 (社區卡位)",
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
            <h2 style="color:#0284c7;">正在前往綠界官方收銀台...</h2>
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
            techs_db[phone] = {"name": f"師傅 ({phone[-4:]})", "phone": phone, "points": pts, "claimedCommunities": []}
        send_line_notification(f"💰 【QT30 儲值成功】師傅 {phone} 儲值 NT$ {order['amount']}，已入帳 {pts} 點！")
    return "1|OK"

@app.get("/api/pay/{case_id}", response_class=HTMLResponse)
def get_payment_page(case_id: str, request: Request):
    target = next((c for c in cases_db if c["id"] == case_id), None)
    if not target:
        raise HTTPException(status_code=404, detail="找不到案件")

    base_url = str(request.base_url).rstrip('/')
    trade_date = datetime.now().strftime("%Y/%m/%d %H:%M:%S")

    item_desc = f"{target['item']}工程款"
    if target.get("needSupervision"):
        item_desc += f"#QT30專業監工費(第三方查核)"

    params = {
        "MerchantID": ECPAY_MERCHANT_ID,
        "MerchantTradeNo": target["tradeNo"],
        "MerchantTradeDate": trade_date,
        "PaymentType": "aio",
        "TotalAmount": str(target["depositAmount"]),
        "TradeDesc": ecpay_url_encode("QT30修繕工程款項"),
        "ItemName": item_desc,
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
            <div style="text-align:left;background:#f1f5f9;padding:15px;border-radius:8px;font-size:13px;margin:15px 0;">
                <p style="margin:4px 0;">案件編號：<b>{target['id']}</b></p>
                <p style="margin:4px 0;">🔨 師傅施工款：<b>NT$ {target['baseAmount']:,}</b></p>
                {f"<p style='margin:4px 0;color:#0284c7;'>🏢 QT30 專業監工費 (8%)：<b>NT$ {target['supervisionFee']:,}</b></p>" if target.get('needSupervision') else ""}
                <hr style="margin:8px 0;border:0;border-top:1px solid #cbd5e1;">
                <p style="margin:4px 0;font-size:15px;color:#0f172a;"><b>應付總額：NT$ {target['depositAmount']:,}</b></p>
            </div>
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
                    f"🎉 【QT30 款項入帳】案件 {c['id']} 客戶 {c['clientName']} 已成功支付 NT$ {c['depositAmount']}！"
                )
                send_line_notification(msg)
                break
    return "1|OK"

# ==================== 頁面路由 ====================

# 1. 師傅端工作台 (/tech) - 跨區地圖與指標造鎮卡位
@app.get("/tech", response_class=HTMLResponse)
def serve_tech_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 全國師傅接單與社區卡位地圖</title>
      <script src="https://cdn.tailwindcss.com"></script>
      <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
      <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
    </head>
    <body class="bg-slate-100 min-h-screen pb-16">
      
      <!-- 頂部師傅狀態 -->
      <header class="bg-slate-900 text-white p-4 sticky top-0 z-40 shadow-md">
        <div class="max-w-5xl mx-auto flex justify-between items-center">
          <div>
            <span class="text-xs text-slate-400">QT30 全國師傅工作台</span>
            <div class="flex items-center space-x-2 mt-0.5">
              <input type="text" id="techPhone" value="0912345678" onchange="loadProfile()" class="bg-slate-800 text-white text-xs px-2 py-1 rounded border border-slate-700 w-28 focus:outline-none">
              <span id="techNameDisplay" class="font-bold text-sm text-blue-300">王師傅</span>
            </div>
          </div>
          <div class="text-right">
            <div class="text-xs text-slate-400">點數餘額</div>
            <div class="text-lg font-black text-amber-400"><span id="ptsBalance">0</span> <span class="text-xs text-slate-300">點</span></div>
          </div>
        </div>
      </header>

      <!-- 導覽列 -->
      <div class="max-w-5xl mx-auto px-4 mt-4">
        <div class="flex bg-white rounded-xl shadow-sm p-1 border border-slate-200 text-xs sm:text-sm font-semibold">
          <button onclick="switchTab('map')" id="tabBtn-map" class="flex-1 py-2.5 text-center rounded-lg bg-blue-600 text-white shadow-sm transition">🗺️ 跨區地圖卡位</button>
          <button onclick="switchTab('jobs')" id="tabBtn-jobs" class="flex-1 py-2.5 text-center rounded-lg text-slate-600 hover:text-blue-600 transition">⚡ 接單大廳</button>
          <button onclick="switchTab('topup')" id="tabBtn-topup" class="flex-1 py-2.5 text-center rounded-lg text-slate-600 hover:text-blue-600 transition">💰 儲值享折扣</button>
        </div>
      </div>

      <!-- 主要內容 -->
      <main class="max-w-5xl mx-auto px-4 mt-4 space-y-4">
        
        <!-- 頁籤 1：跨區地圖與社區卡位 -->
        <section id="tab-map" class="space-y-4">
          <div class="bg-gradient-to-r from-blue-900 to-indigo-900 p-5 rounded-2xl text-white shadow-md flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3">
            <div>
              <h2 class="text-lg font-black flex items-center gap-2">📍 全台指標造鎮社區「專屬師傅」卡位專區</h2>
              <p class="text-xs text-blue-200 mt-1">涵蓋北北基、桃竹苗、大台中與南台灣（南科/高美）！儲值 1,000 元搶佔專屬席位享 15 分鐘優先接單權！</p>
            </div>
            <button onclick="buyPoints(1000, 1050)" class="bg-amber-400 hover:bg-amber-500 text-slate-900 text-xs font-bold px-4 py-2.5 rounded-lg shadow whitespace-nowrap">
              💳 儲值 $1,000 卡位
            </button>
          </div>

          <!-- 區域切換按鈕組 -->
          <div class="flex gap-2 overflow-x-auto pb-1 text-xs font-bold">
            <button onclick="filterRegion('全台', 23.8, 121.0, 7.5)" class="region-btn bg-slate-800 text-white px-3.5 py-2 rounded-lg shadow-sm transition whitespace-nowrap">🌐 全台總覽</button>
            <button onclick="filterRegion('北北基', 25.10, 121.50, 11)" class="region-btn bg-white hover:bg-slate-100 text-slate-700 px-3.5 py-2 rounded-lg border border-slate-200 transition whitespace-nowrap">🏙️ 北北基</button>
            <button onclick="filterRegion('桃竹苗', 24.85, 121.15, 11)" class="region-btn bg-white hover:bg-slate-100 text-slate-700 px-3.5 py-2 rounded-lg border border-slate-200 transition whitespace-nowrap">🔬 桃竹苗</button>
            <button onclick="filterRegion('大台中', 24.16, 120.66, 12)" class="region-btn bg-white hover:bg-slate-100 text-slate-700 px-3.5 py-2 rounded-lg border border-slate-200 transition whitespace-nowrap">🌳 大台中</button>
            <button onclick="filterRegion('南台灣', 22.80, 120.25, 10.5)" class="region-btn bg-white hover:bg-slate-100 text-slate-700 px-3.5 py-2 rounded-lg border border-slate-200 transition whitespace-nowrap">☀️ 南台灣 (台南/高雄)</button>
          </div>

          <!-- 免費開源地圖容器 -->
          <div class="bg-white p-3 rounded-2xl shadow-sm border border-slate-200">
            <div id="taiwanMap" class="w-full h-80 sm:h-96 rounded-xl border border-slate-100 z-10"></div>
          </div>

          <!-- 社區卡位清單 -->
          <div class="bg-white p-5 rounded-2xl shadow-sm border border-slate-200">
            <div class="flex justify-between items-center mb-3">
              <h3 class="font-bold text-slate-800 text-sm">指標造鎮社區列表 (<span id="currentRegionLabel">全台</span>)</h3>
              <span class="text-xs text-slate-400">點擊卡位即刻鎖定該社區派工</span>
            </div>
            <div id="communityList" class="grid grid-cols-1 md:grid-cols-2 gap-3.5">
              <div class="p-4 text-center text-slate-400 text-xs">載入社區中...</div>
            </div>
          </div>
        </section>

        <!-- 頁籤 2：接單大廳 -->
        <section id="tab-jobs" class="hidden space-y-3">
          <div id="jobList" class="space-y-3"></div>
        </section>

        <!-- 頁籤 3：儲值方案 -->
        <section id="tab-topup" class="hidden space-y-4">
          <div class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
            <h3 class="font-bold text-slate-800 text-base">點數儲值中心（1 點 = 1 元）</h3>
            <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5 mt-4">
              <div class="p-4 border-2 border-blue-500 rounded-xl bg-blue-50/40 flex justify-between items-center">
                <div>
                  <span class="text-xs font-bold text-blue-700">★ 社區卡位首選</span>
                  <div class="text-xl font-black text-slate-900 mt-1">NT$ 1,000</div>
                  <div class="text-xs text-emerald-600 font-semibold">實得 1,050 點 (95折)</div>
                </div>
                <button onclick="buyPoints(1000, 1050)" class="bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold px-4 py-2 rounded-lg shadow">儲值 $1,000</button>
              </div>
              <div class="p-4 border border-amber-400 bg-amber-50/40 rounded-xl flex justify-between items-center">
                <div>
                  <span class="text-xs font-bold text-amber-700">🔥 全職首選</span>
                  <div class="text-xl font-black text-slate-900 mt-1">NT$ 5,000</div>
                  <div class="text-xs text-emerald-600 font-semibold">實得 5,800 點 (86折)</div>
                </div>
                <button onclick="buyPoints(5000, 5800)" class="bg-amber-500 hover:bg-amber-600 text-slate-900 text-xs font-bold px-4 py-2 rounded-lg">儲值 $5,000</button>
              </div>
              <div class="sm:col-span-2 p-5 bg-slate-900 text-white rounded-xl flex justify-between items-center">
                <div>
                  <span class="text-xs font-bold text-amber-400">👑 VIP 旗艦大戶</span>
                  <div class="text-xl font-black mt-1">NT$ 12,000</div>
                  <div class="text-xs text-slate-300">實得 15,000 點 (享 8 折扣點)</div>
                </div>
                <button onclick="buyPoints(12000, 15000)" class="bg-amber-400 hover:bg-amber-500 text-slate-900 text-xs font-bold px-5 py-2.5 rounded-lg shadow">儲值 $12,000</button>
              </div>
            </div>
          </div>
        </section>

      </main>

      <script>
        let currentProfile = { name: "王師傅", phone: "0912345678", points: 800, claimedCommunities: ["COM-N01"] };
        let mapInstance = null;
        let markersGroup = null;
        let activeRegion = "全台";

        function initMap() {
          if (mapInstance) return;
          mapInstance = L.map('taiwanMap').setView([23.8, 121.0], 7.5);
          L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap' }).addTo(mapInstance);
          markersGroup = L.layerGroup().addTo(mapInstance);
          loadMapMarkers("全台");
        }

        async function loadMapMarkers(region) {
          try {
            const url = region && region !== '全台' ? `/api/communities?region=${encodeURIComponent(region)}` : '/api/communities';
            const res = await fetch(url);
            const data = await res.json();
            if (data.success) {
              renderCommunities(data.communities);
              markersGroup.clearLayers();
              data.communities.forEach(com => {
                const marker = L.marker([com.lat, com.lng]).addTo(markersGroup);
                marker.bindPopup(`
                  <div class="p-1 text-xs">
                    <span class="bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded font-bold">${com.region}</span>
                    <b class="text-slate-800 text-sm block mt-1">${com.name}</b>
                    <p class="text-slate-500 mt-0.5">${com.address} (${com.units || '大型社區'})</p>
                    <div class="mt-1.5 pt-1 border-t border-slate-200">
                      水電：<b>${com.slots['水電維修'] || '❌ 待卡位'}</b> | 防水：<b>${com.slots['泥作防水'] || '❌ 待卡位'}</b>
                    </div>
                  </div>
                `);
              });
            }
          } catch(e) {}
        }

        function filterRegion(reg, lat, lng, zoom) {
          activeRegion = reg;
          document.getElementById('currentRegionLabel').innerText = reg;
          document.querySelectorAll('.region-btn').forEach(btn => {
            if (btn.innerText.includes(reg) || (reg === '全台' && btn.innerText.includes('全台'))) {
              btn.className = "region-btn bg-slate-800 text-white px-3.5 py-2 rounded-lg shadow-sm transition whitespace-nowrap";
            } else {
              btn.className = "region-btn bg-white hover:bg-slate-100 text-slate-700 px-3.5 py-2 rounded-lg border border-slate-200 transition whitespace-nowrap";
            }
          });
          mapInstance.flyTo([lat, lng], zoom, { duration: 1 });
          loadMapMarkers(reg);
        }

        function renderCommunities(comms) {
          const list = document.getElementById('communityList');
          list.innerHTML = comms.map(c => `
            <div class="p-4 rounded-xl border border-slate-200 bg-slate-50/60 flex flex-col justify-between space-y-2.5">
              <div>
                <div class="flex justify-between items-center">
                  <div>
                    <span class="text-[10px] bg-blue-600 text-white px-1.5 py-0.5 rounded font-bold mr-1">${c.region}</span>
                    <span class="font-bold text-slate-800 text-sm">${c.name}</span>
                  </div>
                  <span class="text-[10px] bg-slate-200 text-slate-700 px-2 py-0.5 rounded font-semibold">${c.units || '大型指標'}</span>
                </div>
                <div class="text-xs text-slate-500 mt-1 truncate">📍 ${c.address}</div>
                <div class="mt-2 space-y-1 text-xs">
                  ${Object.keys(c.slots).map(item => `
                    <div class="flex justify-between items-center py-1 border-b border-slate-100">
                      <span class="text-slate-600">${item}</span>
                      ${c.slots[item] ? `<span class="text-emerald-700 font-bold">✓ 駐點：${c.slots[item]}</span>` : `<button onclick="claimSlot('${c.id}', '${item}')" class="bg-blue-600 hover:bg-blue-700 text-white text-[11px] font-bold px-2.5 py-0.5 rounded">立即卡位</button>`}
                    </div>
                  `).join('')}
                </div>
              </div>
            </div>
          `).join('');
        }

        async function claimSlot(comId, specialty) {
          if (currentProfile.points < 1000) {
            if (confirm(`卡位需要帳戶內有 1,000 點保證金（目前 ${currentProfile.points} 點）\\n是否前往儲值 NT$ 1,000（實得 1,050 點）？`)) {
              buyPoints(1000, 1050);
            }
            return;
          }
          if (!confirm(`確定卡位此指標社區的【${specialty}】專屬席位嗎？`)) return;
          try {
            const res = await fetch('/api/communities/claim', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ communityId: comId, techName: currentProfile.name, techPhone: currentProfile.phone, specialty: specialty })
            });
            const data = await res.json();
            alert(data.msg);
            loadMapMarkers(activeRegion);
          } catch(e) {}
        }

        function switchTab(tabId) {
          ['map', 'jobs', 'topup'].forEach(t => {
            document.getElementById('tab-' + t).classList.add('hidden');
            const btn = document.getElementById('tabBtn-' + t);
            btn.className = "flex-1 py-2.5 text-center rounded-lg text-slate-600 hover:text-blue-600 transition";
          });
          document.getElementById('tab-' + tabId).classList.remove('hidden');
          const activeBtn = document.getElementById('tabBtn-' + tabId);
          activeBtn.className = "flex-1 py-2.5 text-center rounded-lg bg-blue-600 text-white shadow-sm transition";

          if (tabId === 'map') {
            setTimeout(() => { if (mapInstance) mapInstance.invalidateSize(); else initMap(); }, 100);
          } else if (tabId === 'jobs') {
            loadJobs();
          }
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
              container.innerHTML = '<div class="bg-white p-8 rounded-xl text-center text-slate-400">目前尚無案件</div>';
              return;
            }
            container.innerHTML = data.cases.map(c => `
              <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm space-y-2">
                <div class="flex justify-between items-start">
                  <div>
                    <span class="px-2 py-0.5 bg-blue-100 text-blue-700 text-xs font-bold rounded">${c.item}</span>
                    <span class="text-xs text-slate-400 ml-1 font-mono">${c.id}</span>
                    <div class="text-xs text-slate-500 mt-1">📍 ${c.address}</div>
                  </div>
                  <div class="text-right">
                    <div class="text-sm font-black text-slate-800">施工款 NT$ ${(c.baseAmount || 0).toLocaleString()}</div>
                    <div class="text-xs text-rose-600 font-bold mt-0.5">接單扣點：${c.pointsRequired || 50} 點</div>
                  </div>
                </div>
                <div class="pt-2 border-t border-slate-100 flex justify-between items-center text-xs">
                  <span class="text-slate-500">狀態：<b>${c.status}</b></span>
                  ${c.status === '待派工' ? `
                    <button onclick="claimJob('${c.id}', ${c.pointsRequired || 50})" class="bg-blue-600 hover:bg-blue-700 text-white font-bold px-3 py-1.5 rounded-lg shadow">
                      ⚡ 扣 ${c.pointsRequired || 50} 點接單
                    </button>
                  ` : `<span class="text-emerald-600 font-bold">已由 ${c.technician} 接單</span>`}
                </div>
              </div>
            `).join('');
          } catch(e) {}
        }

        async function claimJob(caseId, pts) {
          if (!confirm(`確定扣除 ${pts} 點接單？`)) return;
          try {
            const res = await fetch('/api/tech/claim', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ caseId: caseId, techName: currentProfile.name, techPhone: currentProfile.phone })
            });
            const data = await res.json();
            alert(data.msg);
            loadProfile();
            loadJobs();
          } catch(e) {}
        }

        function buyPoints(amount, points) {
          const form = document.createElement('form');
          form.method = 'POST';
          form.action = '/api/tech/topup';
          const fields = { techPhone: currentProfile.phone, amount: amount, points: points };
          for (const k in fields) {
            const inp = document.createElement('input');
            inp.type = 'hidden';
            inp.name = k;
            inp.value = fields[k];
            form.appendChild(inp);
          }
          document.body.appendChild(form);
          form.submit();
        }

        loadProfile();
        initMap();
      </script>
    </body>
    </html>
    """

# 2. 客戶端預約 (/app)
@app.get("/app", response_class=HTMLResponse)
def serve_app_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 房屋修繕預約</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
      <div class="max-w-md mx-auto bg-white rounded-2xl shadow-xl overflow-hidden">
        <div class="bg-blue-600 p-6 text-white text-center">
          <h1 class="text-2xl font-bold">QT30 全國社區修繕預約</h1>
          <p class="text-blue-100 text-sm mt-1">北北基 · 桃竹苗 · 大台中 · 南台灣 專屬師傅快速到府</p>
        </div>
        
        <form id="caseForm" class="p-6 space-y-4">
          <div>
            <label class="block text-sm font-semibold text-gray-700">服務地區</label>
            <select id="region" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm">
              <option value="北北基">北北基 (台北 / 新北 / 淡水 / 基隆 / 林口)</option>
              <option value="桃竹苗">桃竹苗 (桃園 / 青埔 / 新竹竹科 / 竹北)</option>
              <option value="大台中">大台中 (北屯機捷 / 西屯七期 / 中科 / 南屯)</option>
              <option value="南台灣">南台灣 (台南善化南科 / 高雄美術館 / 農16 / 左營)</option>
            </select>
          </div>

          <div>
            <label class="block text-sm font-semibold text-gray-700">聯絡姓名</label>
            <input type="text" id="clientName" required placeholder="例如：王先生" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-sm font-semibold text-gray-700">聯絡電話</label>
            <input type="tel" id="clientPhone" required placeholder="例如：0912345678" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
          </div>
          <div>
            <label class="block text-sm font-semibold text-gray-700">社區名稱與修繕地址</label>
            <input type="text" id="address" required placeholder="例如：台南LM特區陽光大道 / 淡水宏盛水悅 8 樓" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none">
          </div>
          
          <div class="grid grid-cols-2 gap-2">
            <div>
              <label class="block text-sm font-semibold text-gray-700">修繕工項</label>
              <select id="item" onchange="calculatePrice()" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm">
                <option value="水電維修" data-price="1500">水電檢修 (基礎 $1,500)</option>
                <option value="油漆粉刷" data-price="1500">油漆粉刷 ($1,500/坪)</option>
                <option value="泥作防水" data-price="8000">泥作防水 ($8,000/坪)</option>
                <option value="冷氣空調" data-price="2000">冷氣清洗 ($2,000/台)</option>
                <option value="裝潢木作" data-price="25000">裝潢木作 ($25,000/坪)</option>
                <option value="其他綜合修繕" data-price="1000">其他綜合修繕</option>
              </select>
            </div>
            <div>
              <label class="block text-sm font-semibold text-gray-700">預估坪數/數量</label>
              <input type="number" id="areaPing" value="1" min="1" oninput="calculatePrice()" class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none text-sm">
            </div>
          </div>

          <!-- 8% 監工選項 -->
          <div class="bg-blue-50 p-4 rounded-xl border border-blue-200 space-y-2">
            <label class="flex items-start space-x-2.5 cursor-pointer">
              <input type="checkbox" id="needSupervision" onchange="calculatePrice()" class="mt-1 w-4 h-4 text-blue-600 rounded">
              <div class="text-xs text-gray-700">
                <span class="font-bold text-blue-900">加選「QT30 官方專業監工與驗收紀錄」(+8%)</span>
                <p class="text-gray-500 mt-0.5">由 QT30 官方指派特約工務至現場進行第三方外觀查核與施工拍照記錄。</p>
              </div>
            </label>

            <div id="termsNotice" class="hidden text-[11px] text-slate-600 bg-white p-2.5 rounded-lg border border-blue-100">
              <label class="flex items-center space-x-1.5 cursor-pointer">
                <input type="checkbox" id="agreeSupervisionTerms" checked class="w-3.5 h-3.5 text-blue-600 rounded">
                <span>我已同意 <a href="javascript:void(0)" onclick="openTermsModal()" class="text-blue-600 underline font-bold">《QT30 官方監工服務條款》</a>（第三方進度查核，瑕疵擔保由師傅負責）</span>
              </label>
            </div>
          </div>

          <!-- 金額拆解框 -->
          <div class="bg-slate-50 p-3.5 rounded-xl border border-slate-200 space-y-1.5 text-xs">
            <div class="flex justify-between text-slate-600">
              <span>🔨 師傅施工工程款：</span>
              <span class="font-bold">NT$ <span id="dispBase">1,500</span></span>
            </div>
            <div id="supRow" class="hidden flex justify-between text-blue-700">
              <span>🏢 QT30 專業監工費 (8%)：</span>
              <span class="font-bold">NT$ <span id="dispSup">120</span></span>
            </div>
            <div class="border-t border-slate-200 pt-1.5 flex justify-between items-center text-sm">
              <span class="font-bold text-slate-800">預估應付總額：</span>
              <span class="text-lg font-black text-blue-600">NT$ <span id="dispTotal">1,500</span></span>
            </div>
            <input type="hidden" id="baseAmount" value="1500">
          </div>

          <div>
            <label class="block text-sm font-semibold text-gray-700">狀況描述</label>
            <textarea id="description" rows="2" placeholder="請簡述損壞情況..." class="w-full mt-1 p-3 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"></textarea>
          </div>

          <div>
            <label class="block text-sm font-semibold text-gray-700">上傳現場照片</label>
            <input type="file" id="photoInput" accept="image/*" class="w-full mt-1 p-2 border border-dashed border-gray-400 rounded-lg text-sm bg-gray-50 cursor-pointer">
            <div id="previewContainer" class="mt-2 hidden">
              <img id="imagePreview" src="" class="w-full h-36 object-cover rounded-lg border">
            </div>
          </div>

          <button type="submit" id="submitBtn" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3.5 rounded-lg shadow transition">
            立即送出預約
          </button>
        </form>

        <div id="resultModal" class="hidden p-8 bg-green-50 text-center space-y-3">
          <div class="w-14 h-14 bg-green-100 text-green-600 rounded-full flex items-center justify-center mx-auto text-2xl font-bold">✓</div>
          <h3 class="text-lg font-bold text-green-800">預約單已送出！</h3>
          <p class="text-xs text-gray-600">已通知該社區駐點專屬師傅，將儘速與您聯繫。</p>
          <button onclick="location.reload()" class="mt-3 w-full bg-gray-100 hover:bg-gray-200 text-gray-700 font-semibold py-2 rounded-lg text-xs">
            再填寫一筆
          </button>
        </div>
      </div>

      <!-- 條款彈窗 -->
      <div id="termsModal" class="hidden fixed inset-0 bg-black bg-opacity-60 flex justify-center items-center z-50 p-4">
        <div class="bg-white max-w-lg w-full rounded-2xl p-6 space-y-4 max-h-[85vh] overflow-y-auto">
          <div class="flex justify-between items-center border-b pb-3">
            <h3 class="font-bold text-slate-800 text-base">《QT30 官方專業監工服務條款》</h3>
            <button onclick="closeTermsModal()" class="text-slate-400 hover:text-slate-600 text-xl font-bold">✕</button>
          </div>
          <div class="text-xs text-slate-600 space-y-3 leading-relaxed">
            <p><b>第 1 條（服務定位）</b><br>QT30 8% 專業監工服務為「第三方獨立外觀進度查核與拍照存證」，平台非工程承攬人或連帶保證人。</p>
            <p><b>第 2 條（責任排除）</b><br>施工工法、建材耐用度及法定瑕疵擔保責任，均由承攬施作師傅全權依法負責。</p>
            <p><b>第 3 條（賠償上限）</b><br>監工查核爭議賠償上限以業主實際支付之 8% 監工費為限。</p>
          </div>
          <button onclick="closeTermsModal()" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 rounded-lg text-xs">我已理解並同意</button>
        </div>
      </div>

      <script>
        let base64Photo = null;
        function openTermsModal() { document.getElementById('termsModal').classList.remove('hidden'); }
        function closeTermsModal() { document.getElementById('termsModal').classList.add('hidden'); }

        function calculatePrice() {
          const itemSelect = document.getElementById('item');
          const unitPrice = parseInt(itemSelect.options[itemSelect.selectedIndex].getAttribute('data-price')) || 1500;
          const ping = parseFloat(document.getElementById('areaPing').value) || 1;
          const needSup = document.getElementById('needSupervision').checked;

          let base = Math.round(unitPrice * ping);
          let sup = needSup ? Math.round(base * 0.08) : 0;
          let total = base + sup;

          document.getElementById('dispBase').innerText = base.toLocaleString();
          document.getElementById('dispSup').innerText = sup.toLocaleString();
          document.getElementById('dispTotal').innerText = total.toLocaleString();
          document.getElementById('baseAmount').value = base;

          if (needSup) {
            document.getElementById('supRow').classList.remove('hidden');
            document.getElementById('termsNotice').classList.remove('hidden');
          } else {
            document.getElementById('supRow').classList.add('hidden');
            document.getElementById('termsNotice').classList.add('hidden');
          }
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
          const needSup = document.getElementById('needSupervision').checked;
          const agreeTerms = document.getElementById('agreeSupervisionTerms').checked;
          if (needSup && !agreeTerms) {
            alert('加選監工服務需同意服務條款');
            return;
          }

          const btn = document.getElementById('submitBtn');
          btn.disabled = true;
          const data = {
            region: document.getElementById('region').value,
            clientName: document.getElementById('clientName').value,
            clientPhone: document.getElementById('clientPhone').value,
            address: document.getElementById('address').value,
            item: document.getElementById('item').value,
            areaPing: parseFloat(document.getElementById('areaPing').value) || 1,
            needSupervision: needSup,
            agreeSupervisionTerms: agreeTerms,
            baseAmount: parseInt(document.getElementById('baseAmount').value) || 1500,
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
            }
          } catch(e) {
            alert('送出失敗');
            btn.disabled = false;
          }
        });
        calculatePrice();
      </script>
    </body>
    </html>
    """

# 3. 管理員後台 (/admin)
@app.get("/admin", response_class=HTMLResponse)
def serve_admin_page():
    return """
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>QT30 全國派工管理後台</title>
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-100 min-h-screen p-4 sm:p-8">
      <div class="max-w-7xl mx-auto">
        <header class="flex flex-col sm:flex-row justify-between items-center mb-6 bg-white p-6 rounded-2xl shadow-sm gap-4">
          <div>
            <h1 class="text-2xl font-black text-slate-800">QT30 全國派工管理後台</h1>
            <p class="text-sm text-slate-500 mt-1">北北基 · 桃竹苗 · 大台中 · 南台灣 | 獨立核算工程款、8% 官方監工與 3% 派工扣點</p>
          </div>
          <div class="flex gap-2">
            <a href="/tech" target="_blank" class="bg-slate-800 hover:bg-slate-900 text-white text-xs font-bold px-4 py-2.5 rounded-lg shadow transition">
              🗺️ 開啟跨區地圖
            </a>
            <button onclick="loadCases()" class="bg-blue-600 hover:bg-blue-700 text-white text-xs font-bold px-4 py-2.5 rounded-lg shadow transition">
              🔄 重新整理
            </button>
          </div>
        </header>

        <div class="bg-white rounded-2xl shadow-sm overflow-hidden border border-slate-200">
          <div class="overflow-x-auto">
            <table class="w-full text-left text-sm text-slate-600">
              <thead class="bg-slate-50 text-slate-700 font-bold border-b border-slate-200">
                <tr>
                  <th class="p-4">編號 / 地區</th>
                  <th class="p-4">客戶資訊</th>
                  <th class="p-4">現場照片</th>
                  <th class="p-4">工項 / 坪數</th>
                  <th class="p-4">🔨 師傅工程款</th>
                  <th class="p-4">🏢 平台 8% 監工費</th>
                  <th class="p-4">💰 應收總額 / 扣點</th>
                  <th class="p-4">付款狀態</th>
                  <th class="p-4">接單師傅</th>
                  <th class="p-4">案件狀態</th>
                  <th class="p-4 text-center">操作</th>
                </tr>
              </thead>
              <tbody id="caseTableBody" class="divide-y divide-slate-100">
                <tr><td colspan="11" class="p-8 text-center text-slate-400">載入中...</td></tr>
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
            alert('💳 專屬付款網址已複製！\\n明細包含工程款與 8% 監工費：\\n' + url);
          });
        }

        async function loadCases() {
          const tbody = document.getElementById('caseTableBody');
          try {
            const res = await fetch('/api/cases');
            const data = await res.json();
            if (!data.cases || data.cases.length === 0) {
              tbody.innerHTML = '<tr><td colspan="11" class="p-8 text-center text-slate-400">目前尚無案件</td></tr>';
              return;
            }
            tbody.innerHTML = data.cases.map(c => `
              <tr class="hover:bg-slate-50 transition">
                <td class="p-4">
                  <span class="font-mono font-bold text-blue-600">${c.id}</span>
                  <div class="text-[10px] bg-slate-100 text-slate-600 px-1.5 py-0.5 rounded font-bold mt-1 inline-block">${c.region || '北北基'}</div>
                </td>
                <td class="p-4">
                  <div class="font-bold text-slate-800">${c.clientName}</div>
                  <div class="text-xs text-slate-500">${c.clientPhone}</div>
                  <div class="text-xs text-slate-400">${c.address}</div>
                </td>
                <td class="p-4">
                  ${c.photo ? `
                    <img src="${c.photo}" onclick="viewPhoto('${c.photo}')" class="w-12 h-12 object-cover rounded-lg border border-slate-200 cursor-pointer hover:opacity-80" title="點擊放大">
                  ` : `<span class="text-xs text-slate-300">無照片</span>`}
                </td>
                <td class="p-4">
                  <span class="inline-block px-2 py-0.5 bg-slate-100 text-slate-700 rounded text-xs font-semibold">${c.item}</span>
                  <div class="text-xs text-slate-500 mt-0.5">${c.areaPing || 1} 坪</div>
                </td>
                <td class="p-4">
                  <div class="flex items-center space-x-1">
                    <span class="text-xs text-slate-400">NT$</span>
                    <input type="number" id="baseAmt-${c.id}" value="${c.baseAmount}" class="w-20 border border-slate-300 rounded px-1.5 py-0.5 text-xs font-bold text-slate-800 focus:outline-none">
                  </div>
                </td>
                <td class="p-4">
                  <label class="flex items-center space-x-1 cursor-pointer">
                    <input type="checkbox" id="sup-${c.id}" ${c.needSupervision ? 'checked' : ''} class="w-3.5 h-3.5 text-blue-600 rounded">
                    <span class="text-xs font-bold text-blue-700">NT$ ${(c.supervisionFee || 0).toLocaleString()}</span>
                  </label>
                  <div class="text-[10px] text-slate-400 mt-0.5">${c.needSupervision ? '(指派工務)' : '(無監工)'}</div>
                </td>
                <td class="p-4">
                  <div class="font-black text-slate-900 text-xs">NT$ ${(c.depositAmount || 0).toLocaleString()}</div>
                  <div class="text-[11px] text-emerald-600 font-semibold mt-0.5">扣點：${c.pointsRequired || 50} 點</div>
                </td>
                <td class="p-4">
                  <span class="inline-block px-2 py-0.5 rounded text-xs font-bold ${c.paymentStatus === '已付款' ? 'bg-emerald-100 text-emerald-700' : 'bg-amber-100 text-amber-700'}">
                    ${c.paymentStatus}
                  </span>
                </td>
                <td class="p-4">
                  <input type="text" id="tech-${c.id}" value="${c.technician || ''}" placeholder="未指派" class="border border-slate-300 rounded px-2 py-1 text-xs w-20 focus:outline-none">
                </td>
                <td class="p-4">
                  <select id="status-${c.id}" class="border border-slate-300 rounded px-2 py-1 text-xs focus:outline-none">
                    <option value="待派工" ${c.status === '待派工' ? 'selected' : ''}>待派工</option>
                    <option value="施工中" ${c.status === '施工中' ? 'selected' : ''}>施工中</option>
                    <option value="已完工" ${c.status === '已完工' ? 'selected' : ''}>已完工</option>
                    <option value="已結案" ${c.status === '已結案' ? 'selected' : ''}>已結案</option>
                  </select>
                </td>
                <td class="p-4 text-center space-y-1">
                  <button onclick="saveCase('${c.id}')" class="block w-full bg-slate-800 hover:bg-slate-900 text-white text-xs font-semibold px-2 py-1 rounded transition">
                    💾 存檔
                  </button>
                  <button onclick="copyPayLink('${c.id}')" class="block w-full bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold px-2 py-1 rounded transition">
                    🔗 付款連結
                  </button>
                </td>
              </tr>
            `).join('');
          } catch(e) {
            tbody.innerHTML = '<tr><td colspan="11" class="p-8 text-center text-rose-500">載入失敗</td></tr>';
          }
        }

        async function saveCase(id) {
          const tech = document.getElementById('tech-' + id).value;
          const status = document.getElementById('status-' + id).value;
          const baseAmt = parseInt(document.getElementById('baseAmt-' + id).value) || 1000;
          const needSup = document.getElementById('sup-' + id).checked;

          try {
            const res = await fetch('/api/cases/' + id, {
              method: 'PATCH',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ technician: tech, status: status, baseAmount: baseAmt, needSupervision: needSup })
            });
            const data = await res.json();
            if (data.success) {
              alert('案件更新成功！');
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
    return """<script>window.location.href = '/tech';</script>"""
