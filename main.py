from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from datetime import datetime
import json
import shutil
import os

# ================= 1. 資料庫設定 =================
DATABASE_URL = "sqlite:///./leads_platform.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    role = Column(String(20), default="expert")
    points = Column(Integer, default=150)
    is_verified = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    unlocks = relationship("LeadUnlock", back_populates="expert")
    bids = relationship("Bid", back_populates="expert")

class Case(Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    district = Column(String(50), nullable=False)
    budget = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(255), nullable=True)
    status = Column(String(20), default="OPEN")
    
    # 案主私密聯絡資訊
    client_name = Column(String(50), nullable=False)
    client_phone = Column(String(20), nullable=False)
    client_line = Column(String(50), nullable=True)
    
    unlock_fee = Column(Integer, default=30)
    created_at = Column(DateTime, default=datetime.utcnow)
    unlocks = relationship("LeadUnlock", back_populates="case")
    bids = relationship("Bid", back_populates="case", cascade="all, delete-orphan")

class LeadUnlock(Base):
    __tablename__ = "lead_unlocks"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    expert_id = Column(Integer, ForeignKey("users.id"))
    unlocked_at = Column(DateTime, default=datetime.utcnow)
    case = relationship("Case", back_populates="unlocks")
    expert = relationship("User", back_populates="unlocks")

class Bid(Base):
    __tablename__ = "bids"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    expert_id = Column(Integer, ForeignKey("users.id"))
    bid_amount = Column(Integer, nullable=False)
    materials = Column(String(200), nullable=True)
    work_duration = Column(String(50), nullable=True)
    warranty_period = Column(String(50), nullable=True)
    work_detail = Column(Text, nullable=False)
    is_selected = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    case = relationship("Case", back_populates="bids")
    expert = relationship("User", back_populates="bids")

Base.metadata.create_all(bind=engine)

# ================= 2. 伺服器核心 =================
app = FastAPI(title="QT30社區修繕達人網")

if not os.path.exists("uploads"):
    os.makedirs("uploads")
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.on_event("startup")
def init_default_data():
    db = SessionLocal()
    user = db.query(User).filter(User.username == "金牌水電行-阿銘").first()
    if not user:
        user = User(username="金牌水電行-阿銘", role="expert", points=150, is_verified=True)
        db.add(user)
        db.commit()
    db.close()

# ================= 3. API 路由 =================
@app.get("/api/cases")
def get_cases(db: Session = Depends(get_db)):
    cases = db.query(Case).order_by(Case.id.desc()).all()
    expert = db.query(User).filter(User.username == "金牌水電行-阿銘").first()
    
    data = []
    for c in cases:
        unlocked = False
        if expert:
            unlocked = db.query(LeadUnlock).filter(
                LeadUnlock.case_id == c.id, 
                LeadUnlock.expert_id == expert.id
            ).first() is not None
            
        masked_phone = c.client_phone[:4] + "****" + c.client_phone[-2:] if len(c.client_phone) >= 6 else "***"
        masked_name = c.client_name[0] + "*" * (len(c.client_name) - 1) if len(c.client_name) > 1 else c.client_name
        
        bids_list = []
        for b in c.bids:
            bids_list.append({
                "id": b.id,
                "expert_name": b.expert.username if b.expert else "專業師傅",
                "amount": b.bid_amount,
                "materials": b.materials or "未指定",
                "duration": b.work_duration or "面議",
                "warranty": b.warranty_period or "無保固",
                "detail": b.work_detail,
                "is_selected": b.is_selected,
                "time": b.created_at.strftime("%m/%d %H:%M")
            })

        data.append({
            "id": c.id,
            "title": c.title,
            "category": c.category,
            "district": c.district,
            "budget": c.budget,
            "description": c.description,
            "image_url": c.image_url,
            "status": c.status,
            "unlock_fee": c.unlock_fee,
            "bids_count": len(c.bids),
            "bids": bids_list,
            "is_unlocked": unlocked,
            "contact": {
                "name": c.client_name if unlocked else masked_name,
                "phone": c.client_phone if unlocked else masked_phone,
                "line": c.client_line if unlocked else "現勘解鎖後查看"
            }
        })
    return data

@app.post("/api/cases")
async def create_case(
    title: str = Form(...),
    category: str = Form(...),
    district: str = Form(...),
    budget: int = Form(...),
    client_name: str = Form(...),
    client_phone: str = Form(...),
    client_line: str = Form(None),
    description: str = Form(None),
    file: UploadFile = File(None),
    db: Session = Depends(get_db)
):
    img_url = None
    if file and file.filename:
        path = f"uploads/{file.filename}"
        with open(path, "wb+") as f:
            shutil.copyfileobj(file.file, f)
        img_url = f"/{path}"

    new_c = Case(
        title=title, category=category, district=district, budget=budget,
        client_name=client_name, client_phone=client_phone, client_line=client_line,
        description=description, image_url=img_url, unlock_fee=30, status="OPEN"
    )
    db.add(new_c)
    db.commit()
    return {"status": "success", "message": "發案成功！已開放師傅預約現勘與競標。"}

@app.post("/api/cases/{case_id}/unlock")
def unlock_case_lead(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    expert = db.query(User).filter(User.username == "金牌水電行-阿銘").first()

    if not case:
        raise HTTPException(status_code=404, detail="找不到此案件")
    if not expert:
        raise HTTPException(status_code=404, detail="專家帳號異常")

    existing = db.query(LeadUnlock).filter(
        LeadUnlock.case_id == case_id, LeadUnlock.expert_id == expert.id
    ).first()
    if existing:
        return {
            "status": "success", "message": "您已取得此案現勘權限與聯絡方式！",
            "contact": {"name": case.client_name, "phone": case.client_phone, "line": case.client_line or "無"}
        }

    if expert.points < case.unlock_fee:
        raise HTTPException(status_code=400, detail=f"點數不足！餘額 {expert.points} 點，解鎖需 {case.unlock_fee} 點，請先儲值！")

    expert.points -= case.unlock_fee
    new_u = LeadUnlock(case_id=case.id, expert_id=expert.id)
    db.add(new_u)
    db.commit()

    return {
        "status": "success",
        "message": f"成功取得現勘權！扣除 {case.unlock_fee} 點，剩餘 {expert.points} 點",
        "points": expert.points,
        "contact": {"name": case.client_name, "phone": case.client_phone, "line": case.client_line or "無"}
    }

@app.post("/api/cases/{case_id}/bid")
def submit_bid(
    case_id: int,
    bid_amount: int = Form(...),
    materials: str = Form(None),
    work_duration: str = Form(None),
    warranty_period: str = Form(None),
    work_detail: str = Form(...),
    db: Session = Depends(get_db)
):
    case = db.query(Case).filter(Case.id == case_id).first()
    expert = db.query(User).filter(User.username == "金牌水電行-阿銘").first()

    if not case:
        raise HTTPException(status_code=404, detail="找不到此案件")
    if not expert:
        raise HTTPException(status_code=404, detail="專家帳號異常")

    is_unlocked = db.query(LeadUnlock).filter(
        LeadUnlock.case_id == case_id, LeadUnlock.expert_id == expert.id
    ).first()
    if not is_unlocked:
        raise HTTPException(status_code=403, detail="請先消耗點數解鎖【預約現勘】後，方能公開上傳報價單！")

    if case.status == "CLOSED":
        raise HTTPException(status_code=400, detail="此案件業主已選定得標師傅，已停止受理新報價。")

    new_bid = Bid(
        case_id=case.id, 
        expert_id=expert.id, 
        bid_amount=bid_amount,
        materials=materials,
        work_duration=work_duration,
        warranty_period=warranty_period,
        work_detail=work_detail
    )
    db.add(new_bid)
    db.commit()
    return {"status": "success", "message": "現勘制式化報價已成功公開！"}

@app.post("/api/bids/{bid_id}/select")
def select_winner_bid(bid_id: int, db: Session = Depends(get_db)):
    bid = db.query(Bid).filter(Bid.id == bid_id).first()
    if not bid:
        raise HTTPException(status_code=404, detail="找不到此報價單")
    
    case = db.query(Case).filter(Case.id == bid.case_id).first()
    case.status = "CLOSED"
    bid.is_selected = True
    db.commit()
    return {"status": "success", "message": f"恭喜！已成功選定【{bid.expert.username}】承作本案！"}

@app.post("/api/user/recharge")
def recharge_points(amount: int = Form(...), db: Session = Depends(get_db)):
    expert = db.query(User).filter(User.username == "金牌水電行-阿銘").first()
    if not expert:
        raise HTTPException(status_code=404, detail="專家帳號異常")
    
    expert.points += amount
    db.commit()
    return {"status": "success", "message": f"儲值成功！已增加 {amount} 點", "points": expert.points}

# ================= 4. 前端單一介面 =================
@app.get("/", response_class=HTMLResponse)
def index_ui():
    html_content = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QT30社區修繕達人網 - 即時現勘預約與公開比價大廳</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-100 min-h-screen font-sans text-slate-800">
    <header class="bg-slate-900 text-white p-4 shadow-lg sticky top-0 z-50">
        <div class="max-w-5xl mx-auto flex justify-between items-center">
            <h1 class="text-xl font-bold tracking-wide">🏠 QT30 社區修繕達人網</h1>
            <div class="flex items-center gap-3">
                <div class="bg-slate-800 border border-slate-700 px-4 py-1.5 rounded-full text-sm font-medium">
                    師傅：<span class="text-blue-400 font-bold">阿銘</span> | 餘額：<span id="pts" class="text-amber-400 font-extrabold text-base">150</span> 點
                </div>
                <button onclick="openRechargeModal()" class="bg-amber-500 hover:bg-amber-600 text-slate-900 font-bold text-xs px-3 py-1.5 rounded-full shadow transition">
                    💳 儲值點數
                </button>
            </div>
        </div>
    </header>

    <main class="max-w-5xl mx-auto p-4 space-y-6">
        <section class="bg-white p-6 rounded-2xl shadow-sm border border-slate-200">
            <div class="flex flex-wrap justify-between items-center mb-3">
                <h2 class="text-lg font-bold text-slate-900 flex items-center gap-2">
                    <span class="bg-blue-600 text-white text-xs px-2 py-1 rounded">發案引導</span> 免費刊登需求（開放預約現勘比價）
                </h2>
                <span class="text-xs text-slate-400">💡 點選標籤自動套用</span>
            </div>

            <div class="flex flex-wrap gap-2 mb-4 p-3 bg-slate-50 border border-slate-200 rounded-xl">
                <span class="text-xs font-bold text-slate-500 self-center">常見範本：</span>
                <button type="button" onclick="applyTemplate('浴室地板滲水抓漏', '防水抓漏', 15000, '主臥衛浴淋浴區地面磁磚縫隙有滲漏現象，疑似防水層老化，需重新作防水處理並測試積水保固。')" class="text-xs bg-white hover:bg-blue-50 hover:text-blue-600 border border-slate-300 px-2.5 py-1 rounded-lg transition">🚿 衛浴防水抓漏</button>
                <button type="button" onclick="applyTemplate('室內配電箱無熔絲開關跳電更換', '水電維修', 4500, '家中開啟熱水器與微波爐時總開關常跳電，需檢測迴路負載並更換加大無熔絲開關與漏電斷路器。')" class="text-xs bg-white hover:bg-blue-50 hover:text-blue-600 border border-slate-300 px-2.5 py-1 rounded-lg transition">⚡ 開關箱跳電檢修</button>
                <button type="button" onclick="applyTemplate('全室冷氣深層拆洗保養 (2台分離式)', '冷氣空調', 5000, '客廳與主臥分離式冷氣出風有霉味且風量變小，需室內外機高壓深層清洗、高溫殺菌與排水管疏通。')" class="text-xs bg-white hover:bg-blue-50 hover:text-blue-600 border border-slate-300 px-2.5 py-1 rounded-lg transition">❄️ 冷氣拆洗保養</button>
            </div>

            <form id="postForm" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">需求項目標題 *</label>
                    <input type="text" id="formTitle" name="title" required placeholder="例：衛浴抓漏防水翻修" class="w-full border p-2.5 rounded-lg text-sm bg-slate-50 focus:bg-white focus:ring-2 focus:ring-blue-500 outline-none">
                </div>

                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">修繕類別 *</label>
                    <select id="formCategory" name="category" class="w-full border p-2.5 rounded-lg text-sm bg-slate-50">
                        <option value="水電維修">水電維修</option>
                        <option value="防水抓漏">防水抓漏</option>
                        <option value="冷氣空調">冷氣空調</option>
                        <option value="油漆粉刷">油漆粉刷</option>
                    </select>
                </div>

                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">服務地區 *</label>
                    <input type="text" name="district" required placeholder="例：新北市淡水區" class="w-full border p-2.5 rounded-lg text-sm bg-slate-50 focus:bg-white">
                </div>

                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">預估預算 (NT$) *</label>
                    <input type="number" id="formBudget" name="budget" required placeholder="例：15000" class="w-full border p-2.5 rounded-lg text-sm bg-slate-50 focus:bg-white">
                </div>

                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">聯絡人稱呼 *</label>
                    <input type="text" name="client_name" required placeholder="例：陳先生" class="w-full border p-2.5 rounded-lg text-sm bg-slate-50 focus:bg-white">
                </div>

                <div>
                    <label class="block text-xs font-bold text-slate-700 mb-1">聯絡電話 (師傅解鎖後約現勘) *</label>
                    <input type="text" name="client_phone" required placeholder="例：0912345678" class="w-full border p-2.5 rounded-lg text-sm bg-slate-50 focus:bg-white">
                </div>

                <div class="md:col-span-2">
                    <label class="block text-xs font-bold text-slate-700 mb-1">LINE ID（選填）</label>
                    <input type="text" name="client_line" placeholder="方便師傅加 LINE 傳現勘照片" class="w-full border p-2.5 rounded-lg text-sm bg-slate-50 focus:bg-white">
                </div>

                <div class="md:col-span-2 space-y-1">
                    <div class="flex justify-between items-center">
                        <label class="block text-xs font-bold text-slate-700">詳細情況說明</label>
                        <button type="button" onclick="aiEnhanceDescription()" class="text-xs bg-purple-50 text-purple-700 border border-purple-200 hover:bg-purple-100 font-bold px-3 py-1 rounded-full transition flex items-center gap-1">
                            ✨ AI 一鍵需求潤飾
                        </button>
                    </div>
                    <textarea id="formDescription" name="description" rows="3" placeholder="請描述損壞狀況..." class="w-full border p-2.5 rounded-lg text-sm bg-slate-50 focus:bg-white focus:ring-2 focus:ring-blue-500 outline-none"></textarea>
                </div>

                <button type="submit" class="md:col-span-2 bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-xl transition shadow">
                    🚀 免費送出需求（開放師傅預約現勘）
                </button>
            </form>
        </section>

        <section>
            <div class="flex justify-between items-center mb-3">
                <h2 class="text-lg font-bold text-slate-900">⚡ 即時公開競標大廳</h2>
                <button onclick="fetchLeads()" class="text-xs bg-white border border-slate-300 hover:bg-slate-50 px-3 py-1.5 rounded-lg shadow-sm">🔄 重新整理</button>
            </div>
            <div id="leadsList" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </section>
    </main>

    <div id="bidModal" class="fixed inset-0 bg-black/50 hidden items-center justify-center p-4 z-50 overflow-y-auto">
        <div class="bg-white rounded-2xl p-6 max-w-lg w-full space-y-4 shadow-xl my-8">
            <div class="flex justify-between items-center border-b pb-2">
                <h3 class="font-bold text-base text-slate-900">📋 現勘後．制式化報價單上傳</h3>
                <button type="button" onclick="aiFillBidForm()" class="text-xs bg-purple-100 text-purple-700 hover:bg-purple-200 font-bold px-2.5 py-1 rounded-full flex items-center gap-1">
                    ✨ AI 帶入標準工法
                </button>
            </div>
            
            <form id="bidForm" class="space-y-3">
                <input type="hidden" id="modalCaseId" name="case_id">
                
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-xs font-semibold text-slate-700 mb-1">報價總額 (NT$) *</label>
                        <input type="number" id="bid_amount" name="bid_amount" required placeholder="例：18000" class="w-full border p-2 rounded-lg text-sm bg-slate-50 focus:bg-white">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-700 mb-1">預計工期 *</label>
                        <input type="text" id="work_duration" name="work_duration" required placeholder="例：1~2 個工作天" class="w-full border p-2 rounded-lg text-sm bg-slate-50 focus:bg-white">
                    </div>
                </div>

                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-xs font-semibold text-slate-700 mb-1">主要材料規格 *</label>
                        <input type="text" id="materials" name="materials" required placeholder="例：進口雙劑型彈泥、高壓灌注針" class="w-full border p-2 rounded-lg text-sm bg-slate-50 focus:bg-white">
                    </div>
                    <div>
                        <label class="block text-xs font-semibold text-slate-700 mb-1">施工保固期 *</label>
                        <input type="text" id="warranty_period" name="warranty_period" required placeholder="例：完工驗收保固 2 年" class="w-full border p-2 rounded-lg text-sm bg-slate-50 focus:bg-white">
                    </div>
                </div>

                <div>
                    <label class="block text-xs font-semibold text-slate-700 mb-1">標準施工工法與驗收明細 *</label>
                    <textarea id="work_detail" name="work_detail" required rows="4" placeholder="1. 現場防護與素地打磨清理&#10;2. 裂縫高壓灌注阻水&#10;3. 防水底漆+面漆雙道塗佈&#10;4. 蓄水 48 小時測試驗收" class="w-full border p-2 rounded-lg text-sm bg-slate-50 focus:bg-white leading-relaxed"></textarea>
                </div>

                <div class="flex gap-2 pt-2">
                    <button type="button" onclick="closeBidModal()" class="w-1/2 bg-slate-100 text-slate-700 py-2.5 rounded-xl text-xs font-bold">取消</button>
                    <button type="submit" class="w-1/2 bg-blue-600 text-white py-2.5 rounded-xl text-xs font-bold hover:bg-blue-700">公開上傳報價單</button>
                </div>
            </form>
        </div>
    </div>

    <div id="rechargeModal" class="fixed inset-0 bg-black/50 hidden items-center justify-center p-4 z-50">
        <div class="bg-white rounded-2xl p-6 max-w-sm w-full space-y-4 shadow-xl text-center">
            <h3 class="font-bold text-base text-slate-900">💳 線上購買接案點數</h3>
            <p class="text-xs text-slate-500">1 點 = NT$ 1 元，每案現勘解鎖扣 30 點</p>
            <div class="grid grid-cols-2 gap-2">
                <button onclick="doRecharge(150)" class="p-3 border rounded-xl hover:border-blue-500 hover:bg-blue-50 text-xs font-bold">
                    💰 150 點<br><span class="text-slate-400 font-normal">NT$ 150</span>
                </button>
                <button onclick="doRecharge(300)" class="p-3 border rounded-xl hover:border-blue-500 hover:bg-blue-50 text-xs font-bold">
                    💰 300 點<br><span class="text-slate-400 font-normal">NT$ 300</span>
                </button>
                <button onclick="doRecharge(500)" class="p-3 border rounded-xl hover:border-blue-500 hover:bg-blue-50 text-xs font-bold col-span-2 bg-amber-50/50 border-amber-200">
                    🔥 550 點 (加贈50點)<br><span class="text-red-500 font-normal">特惠 NT$ 500</span>
                </button>
            </div>
            <button type="button" onclick="closeRechargeModal()" class="w-full bg-slate-100 text-slate-700 py-2 rounded-xl text-xs font-bold">關閉</button>
        </div>
    </div>

    <script>
        function applyTemplate(title, category, budget, desc) {
            document.getElementById('formTitle').value = title;
            document.getElementById('formCategory').value = category;
            document.getElementById('formBudget').value = budget;
            document.getElementById('formDescription').value = desc;
        }

        function aiEnhanceDescription() {
            const descElem = document.getElementById('formDescription');
            const current = descElem.value.trim();
            if (!current) {
                alert("請先輸入簡單描述！");
                return;
            }
            descElem.value = `【現況描述】${current}\\n【現勘需求】需專業師傅攜帶儀器到府評估，提供制式工法與材料報價。\\n【施工期盼】工法透明、責任施工並提供完工保固。`;
        }

        function aiFillBidForm() {
            document.getElementById('materials').value = "CNS標準防水材料、進口抗裂彈性水泥、環氧樹脂";
            document.getElementById('work_duration').value = "約 2 個工作天（含試水）";
            document.getElementById('warranty_period').value = "完工試水驗收後保固 2 年";
            document.getElementById('work_detail').value = "1. 【現場保護】：全室防塵鋪設與傢俱防護。\\n2. 【基底處理】：清除破損老化層，高壓吸塵清理素地。\\n3. 【專業工法】：裂縫高壓灌注阻斷水源，施作雙道彈泥防水層。\\n4. 【驗收標準】：蓄水 48 小時無滲漏即完成驗收並簽署保固書。";
        }

        async function fetchLeads() {
            const res = await fetch('/api/cases');
            const list = await res.json();
            const box = document.getElementById('leadsList');
            box.innerHTML = '';

            if (list.length === 0) {
                box.innerHTML = '<div class="col-span-2 bg-white p-8 text-center text-slate-400 rounded-xl">目前大廳尚無案件！</div>';
                return;
            }

            list.forEach(item => {
                const bidsHtml = item.bids.map(b => `
                    <div class="p-3 bg-slate-50 border ${b.is_selected ? 'border-emerald-500 bg-emerald-50/40' : 'border-slate-200'} rounded-xl text-xs space-y-1.5 mb-2 shadow-xs">
                        <div class="flex justify-between items-center font-bold">
                            <span class="text-blue-700">🛠️ ${b.expert_name} ${b.is_selected ? '<span class="text-emerald-700 bg-emerald-100 px-1.5 py-0.5 rounded text-[10px]">🏆 案主已選用得標</span>' : ''}</span>
                            <span class="text-red-600 font-mono text-sm">NT$ ${b.amount.toLocaleString()}</span>
                        </div>
                        <div class="grid grid-cols-2 gap-1 text-[11px] text-slate-500 bg-white p-1.5 rounded border border-slate-100">
                            <div>⏱️ 工期：${b.duration}</div>
                            <div>🛡️ 保固：${b.warranty}</div>
                            <div class="col-span-2">🧱 材料：${b.materials}</div>
                        </div>
                        <p class="text-slate-700 text-[11px] leading-relaxed whitespace-pre-line bg-white/60 p-2 rounded">${b.detail}</p>
                        
                        ${!b.is_selected && item.status !== 'CLOSED' ? `
                            <button onclick="doSelectWinner(${b.id})" class="w-full bg-emerald-600 hover:bg-emerald-700 text-white font-bold py-1.5 rounded-lg text-[11px] transition mt-1 shadow-xs">
                                🤝 案主點此：選用此師傅報價（結標）
                            </button>
                        ` : ''}
                        
                        <div class="text-slate-400 text-[10px] text-right">${b.time}</div>
                    </div>
                `).join('');

                const div = document.createElement('article');
                div.className = "bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex flex-col justify-between";
                
                div.innerHTML = `
                    <div>
                        <div class="flex justify-between items-center mb-2">
                            <span class="bg-blue-50 text-blue-700 font-semibold text-xs px-2 py-1 rounded-md border border-blue-200">${item.category}</span>
                            <span class="text-xs font-bold ${item.status === 'CLOSED' ? 'text-emerald-700 bg-emerald-50' : 'text-amber-700 bg-amber-50'} px-2 py-0.5 rounded">
                                ${item.status === 'CLOSED' ? '✅ 已結標' : '⚡ 競標中'}
                            </span>
                        </div>
                        <h3 class="font-bold text-slate-900 text-base mb-1">${item.title}</h3>
                        <p class="text-xs text-slate-500 mb-2">📍 ${item.district} ｜ 💰 案主預算：NT$ ${item.budget.toLocaleString()}</p>
                        ${item.description ? `<p class="text-xs text-slate-600 bg-slate-50 p-2.5 rounded-lg mb-3 whitespace-pre-line">${item.description}</p>` : ''}
                        
                        <div class="p-3 bg-amber-50/80 border border-amber-200 rounded-xl space-y-1 text-xs mb-3">
                            <div class="text-amber-900 font-bold mb-1">📋 業主聯絡資料（現勘專用）</div>
                            <div>稱呼：<span class="font-semibold text-slate-900">${item.contact.name}</span></div>
                            <div>電話：<span class="font-mono font-bold ${item.is_unlocked ? 'text-blue-600 text-sm' : 'text-slate-500'}">${item.contact.phone}</span></div>
                            <div>LINE：<span class="font-semibold text-slate-900">${item.contact.line}</span></div>
                        </div>

                        <div class="space-y-1.5 mb-2">
                            <div class="text-xs font-bold text-slate-700 flex justify-between">
                                <span>💬 師傅現勘制式報價單 (${item.bids_count})</span>
                            </div>
                            <div class="space-y-1 max-h-56 overflow-y-auto pr-1">
                                ${item.bids.length > 0 ? bidsHtml : '<div class="text-xs text-slate-400 italic py-1">尚未上傳報價單，解鎖現勘後即可公開報價！</div>'}
                            </div>
                        </div>
                    </div>

                    <div class="mt-4 pt-2 border-t border-slate-100 flex gap-2">
                        ${item.is_unlocked 
                            ? `<button disabled class="flex-1 bg-emerald-600 text-white font-bold py-2.5 rounded-xl text-xs">✅ 已取得現勘電話</button>`
                            : `<button onclick="doUnlock(${item.id})" class="flex-1 bg-amber-500 hover:bg-amber-600 text-white font-bold py-2.5 rounded-xl text-xs transition shadow-sm">
                                🔓 扣 ${item.unlock_fee} 點約現場勘驗
                               </button>`
                        }
                        <button onclick="${item.is_unlocked ? `openBidModal(${item.id})` : `alert('必須先解鎖現勘權限取得電話後，方能公開上傳報價單！')`}" class="flex-1 ${item.is_unlocked ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-slate-200 text-slate-400 cursor-not-allowed'} font-bold py-2.5 rounded-xl text-xs transition shadow-sm">
                            📋 上傳現勘制式報價
                        </button>
                    </div>
                `;
                box.appendChild(div);
            });
        }

        async function doUnlock(id) {
            if (!confirm("確定要消耗 30 點取得業主聯絡資訊並預約現場勘驗嗎？")) return;
            const res = await fetch(`/api/cases/${id}/unlock`, { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
                alert(data.message);
                if (data.points !== undefined) document.getElementById('pts').textContent = data.points;
                fetchLeads();
            } else {
                alert(data.detail || "解鎖失敗");
            }
        }

        async function doSelectWinner(bidId) {
            if (!confirm("確定要選用此師傅的報價方案並結標嗎？")) return;
            const res = await fetch(`/api/bids/${bidId}/select`, { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
                alert(data.message);
                fetchLeads();
            } else {
                alert(data.detail || "操作失敗");
            }
        }

        function openBidModal(caseId) {
            document.getElementById('modalCaseId').value = caseId;
            document.getElementById('bidModal').classList.remove('hidden');
            document.getElementById('bidModal').classList.add('flex');
        }

        function closeBidModal() {
            document.getElementById('bidModal').classList.add('hidden');
            document.getElementById('bidModal').classList.remove('flex');
            document.getElementById('bidForm').reset();
        }

        function openRechargeModal() {
            document.getElementById('rechargeModal').classList.remove('hidden');
            document.getElementById('rechargeModal').classList.add('flex');
        }

        function closeRechargeModal() {
            document.getElementById('rechargeModal').classList.add('hidden');
            document.getElementById('rechargeModal').classList.remove('flex');
        }

        async function doRecharge(amount) {
            const formData = new FormData();
            formData.append('amount', amount);
            const res = await fetch('/api/user/recharge', { method: 'POST', body: formData });
            const data = await res.json();
            if (res.ok) {
                alert(data.message);
                document.getElementById('pts').textContent = data.points;
                closeRechargeModal();
            }
        }

        document.getElementById('bidForm').onsubmit = async (e) => {
            e.preventDefault();
            const caseId = document.getElementById('modalCaseId').value;
            const formData = new FormData(e.target);
            const res = await fetch(`/api/cases/${caseId}/bid`, { method: 'POST', body: formData });
            const data = await res.json();
            if (res.ok) {
                alert(data.message);
                closeBidModal();
                fetchLeads();
            } else {
                alert(data.detail || "報價失敗");
            }
        };

        document.getElementById('postForm').onsubmit = async (e) => {
            e.preventDefault();
            const res = await fetch('/api/cases', { method: 'POST', body: new FormData(e.target) });
            const data = await res.json();
            alert(data.message);
            e.target.reset();
            fetchLeads();
        };

        fetchLeads();
    </script>
</body>
</html>"""
    return html_content
