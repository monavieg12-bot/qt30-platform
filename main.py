import os
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

app = FastAPI(title="QT30 派工平台")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# LINE 推播設定
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

# 資料結構
class CaseCreate(BaseModel):
    clientName: Optional[str] = "未填寫"
    clientPhone: Optional[str] = "未填寫"
    address: Optional[str] = "未填寫"
    item: Optional[str] = "未填寫"
    description: Optional[str] = "無詳細描述"

class CaseUpdate(BaseModel):
    status: Optional[str] = None
    technician: Optional[str] = None

cases_db = []

@app.post("/api/cases")
def create_case(data: CaseCreate):
    case_id = f"CASE-{int(datetime.now().timestamp()) % 1000000:06d}"
    new_case = {
        "id": case_id,
        "status": "待派工",
        "createdAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        **data.dict()
    }
    cases_db.insert(0, new_case)

    # 觸發 LINE 推播
    msg = (
        f"🔔 【QT30 新修繕案件通報】\n"
        f"------------------------\n"
        f"📌 案件編號：{new_case['id']}\n"
        f"👤 報修客戶：{new_case['clientName']}\n"
        f"📞 聯絡電話：{new_case['clientPhone']}\n"
        f"📍 修繕地址：{new_case['address']}\n"
        f"🔧 修繕項目：{new_case['item']}\n"
        f"📝 案件描述：{new_case['description']}\n"
        f"------------------------\n"
        f"⚡ 請至管理後台安排派工師傅！"
    )
    send_line_notification(msg)
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
            return {"success": True, "case": c}
    raise HTTPException(status_code=404, detail="找不到案件")

# 靜態頁面掛載（若有 public 資料夾）
if os.path.exists("public"):
    app.mount("/static", StaticFiles(directory="public"), name="static")

@app.get("/app")
def serve_app():
    if os.path.exists("public/app.html"):
        return FileResponse("public/app.html")
    return {"message": "QT30 平台發案系統運作中"}

@app.get("/admin")
def serve_admin():
    if os.path.exists("public/admin.html"):
        return FileResponse("public/admin.html")
    return {"message": "QT30 派工後台運作中"}

@app.get("/")
def serve_index():
    if os.path.exists("public/index.html"):
        return FileResponse("public/index.html")
    return {"message": "QT30 平台後端已連線，LINE 推播已啟用！"}
