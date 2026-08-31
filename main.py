from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from datetime import datetime
import shutil
import os
import requests

# ----------------- 1. 資料庫模型與設定 -----------------
DATABASE_URL = "sqlite:///./platform_v2.db"

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

class Case(Base):
    __tablename__ = "cases"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    district = Column(String(50), nullable=False)
    budget = Column(Integer, nullable=False)
    description = Column(Text, nullable=True)
    image_url = Column(String(255), nullable=True)
    
    # 案主私密聯絡資訊
    client_name = Column(String(50), nullable=False)
    client_phone = Column(String(20), nullable=False)
    client_line = Column(String(50), nullable=True)
    
    # 搶單參數
    unlock_fee = Column(Integer, default=30)
    max_unlocks = Column(Integer, default=3)
    current_unlocks = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    unlocks = relationship("LeadUnlock", back_populates="case")

class LeadUnlock(Base):
    __tablename__ = "lead_unlocks"
    id = Column(Integer, primary_key=True, index=True)
    case_id = Column(Integer, ForeignKey("cases.id"))
    expert_id = Column(Integer, ForeignKey("users.id"))
    unlocked_at = Column(DateTime, default=datetime.utcnow)
    case = relationship("Case", back_populates="unlocks")
    expert = relationship("User", back_populates="unlocks")

Base.metadata.create_all(bind=engine)

# ----------------- 2. FastAPI 核心應用 -----------------
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

# ----------------- 3. 業務邏輯 API -----------------
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
        
        data.append({
            "id": c.id,
            "title": c.title,
            "category": c.category,
            "district": c.district,
            "budget": c.budget,
            "description": c.description,
            "image_url": c.image_url,
            "unlock_fee": c.unlock_fee,
            "max_unlocks": c.max_unlocks,
            "current_unlocks": c.current_unlocks,
            "is_unlocked": unlocked,
            "contact": {
                "name": c.client_name if unlocked else masked_name,
                "phone": c.client_phone if unlocked else masked_phone,
                "line": c.client_line if unlocked else "解鎖後查看"
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
        description=description, image_url=img_url, unlock_fee=30, max_unlocks=3
    )
    db.add(new_c)
    db.commit()
    return {"status": "success", "message": "發案成功！已進入搶單大廳。"}

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
            "status": "success", "message": "您已解鎖過此案件！",
            "contact": {"name": case.client_name, "phone": case.client_phone, "line": case.client_line or "無"}
        }

    if case.current_unlocks >= case.max_unlocks:
        raise HTTPException(status_code=400, detail="搶單名額已滿（上限 3 位）！")

    if expert.points < case.unlock_fee:
        raise HTTPException(status_code=400, detail=f"點數不足！餘額 {expert.points} 點，解鎖需 {case.unlock_fee} 點")

    expert.points -= case.unlock_fee
    case.current_unlocks += 1
    new_u = LeadUnlock(case_id=case.id, expert_id=expert.id)
    db.add(new_u)
    db.commit()

    return {
        "status": "success",
        "message": f"成功解鎖！扣除 {case.unlock_fee} 點，剩餘 {expert.points} 點",
        "contact": {"name": case.client_name, "phone": case.client_phone, "line": case.client_line or "無"}
    }

# ----------------- 4. 前端介面 -----------------
@app.get("/", response_class=HTMLResponse)
def index_ui():
    return """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QT30社區修繕達人網 - 名單媒合平台</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen">
    <header class="bg-slate-900 text-white p-4 shadow-lg sticky top-0 z-50">
        <div class="max-w-5xl mx-auto flex justify-between items-center">
            <h1 class="text-xl font-bold tracking-wide">🏠 QT30 社區修繕達人網</h1>
            <div class="bg-slate-800 border border-slate-700 px-4 py-1.5 rounded-full text-sm font-medium">
                師傅：<span class="text-blue-400 font-bold">阿銘</span> | 餘額：<span id="pts" class="text-amber-400 font-extrabold text-base">150</span> 點
            </div>
        </div>
    </header>

    <main class="max-w-5xl mx-auto p-4 space-y-6">
        <!-- 發案區 -->
        <section class="bg-white p-6 rounded-2xl shadow-sm border border-gray-200">
            <h2 class="text-lg font-bold text-gray-900 mb-3 flex items-center gap-2">
                <span class="bg-blue-600 text-white text-xs px-2 py-1 rounded">發案</span> 免費刊登修繕需求
            </h2>
            <form id="postForm" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <input type="text" name="title" required placeholder="需求項目（例：廚房水管漏水換新）" class="border p-2.5 rounded-lg text-sm bg-gray-50 focus:bg-white">
                <select name="category" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                    <option value="水電維修">水電維修</option>
                    <option value="防水抓漏">防水抓漏</option>
                    <option value="冷氣空調">冷氣空調</option>
                    <option value="油漆粉刷">油漆粉刷</option>
                </select>
                <input type="text" name="district" required placeholder="服務地區（例：新北市淡水區）" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                <input type="number" name="budget" required placeholder="預估預算金額（元）" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                <input type="text" name="client_name" required placeholder="聯絡人稱呼（例：林小姐）" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                <input type="text" name="client_phone" required placeholder="聯絡電話（例：0912345678）" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                <input type="text" name="client_line" placeholder="LINE ID（選填）" class="border p-2.5 rounded-lg text-sm bg-gray-50 md:col-span-2">
                <textarea name="description" rows="2" placeholder="詳細情況補充說明..." class="border p-2.5 rounded-lg text-sm bg-gray-50 md:col-span-2"></textarea>
                <button type="submit" class="md:col-span-2 bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-xl transition shadow">
                    🚀 免費送出並媒合師傅
                </button>
            </form>
        </section>

        <!-- 搶單大廳 -->
        <section>
            <div class="flex justify-between items-center mb-3">
                <h2 class="text-lg font-bold text-gray-900">⚡ 即時搶單大廳（每案限量 3 位師傅）</h2>
                <button onclick="fetchLeads()" class="text-xs bg-white border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg shadow-sm">🔄 重新整理</button>
            </div>
            <div id="leadsList" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </section>
    </main>

    <script>
        async function fetchLeads() {
            const res = await fetch('/api/cases');
            const list = await res.json();
            const box = document.getElementById('leadsList');
            box.innerHTML = '';

            if (list.length === 0) {
                box.innerHTML = '<div class="col-span-2 bg-white p-8 text-center text-gray-400 rounded-xl">目前大廳尚無案件，請於上方發布測試！</div>';
                return;
            }

            list.forEach(item => {
                const full = item.current_unlocks >= item.max_unlocks;
                const div = document.createElement('div');
                div.className = "bg-white p-5 rounded-2xl border border-gray-200 shadow-sm flex flex-col justify-between";
                
                div.innerHTML = `
                    <div>
                        <div class="flex justify-between items-center mb-2">
                            <span class="bg-blue-50 text-blue-700 font-semibold text-xs px-2 py-1 rounded-md border border-blue-200">${item.category}</span>
                            <span class="text-xs font-bold ${full ? 'text-red-600 bg-red-50' : 'text-emerald-700 bg-emerald-50'} px-2 py-1 rounded">
                                搶單名額：${item.current_unlocks} / ${item.max_unlocks}
                            </span>
                        </div>
                        <h3 class="font-bold text-gray-900 text-base mb-1">${item.title}</h3>
                        <p class="text-xs text-gray-500 mb-3">📍 ${item.district} ｜ 💰 預算：NT$ ${item.budget.toLocaleString()}</p>
                        ${item.description ? `<p class="text-xs text-gray-600 bg-gray-50 p-2.5 rounded-lg mb-3">${item.description}</p>` : ''}
                        
                        <div class="p-3 bg-amber-50/80 border border-amber-200 rounded-xl space-y-1 text-xs">
                            <div class="text-amber-900 font-bold mb-1">📋 業主聯絡資料</div>
                            <div>稱呼：<span class="font-semibold text-gray-900">${item.contact.name}</span></div>
                            <div>電話：<span class="font-mono font-bold ${item.is_unlocked ? 'text-blue-600 text-sm' : 'text-gray-500'}">${item.contact.phone}</span></div>
                            <div>LINE：<span class="font-semibold text-gray-900">${item.contact.line}</span></div>
                        </div>
                    </div>

                    <div class="mt-4 pt-2">
                        ${item.is_unlocked 
                            ? `<button disabled class="w-full bg-emerald-600 text-white font-bold py-2.5 rounded-xl text-xs">✅ 已解鎖聯絡方式</button>`
                            : full 
                                ? `<button disabled class="w-full bg-gray-300 text-gray-500 font-bold py-2.5 rounded-xl text-xs">❌ 名額已搶完</button>`
                                : `<button onclick="doUnlock(${item.id})" class="w-full bg-amber-500 hover:bg-amber-600 active:scale-[0.98] text-white font-bold py-2.5 rounded-xl text-xs transition shadow-sm">
                                    🔓 消耗 ${item.unlock_fee} 點解鎖聯絡方式
                                   </button>`
                        }
                    </div>
                `;
                box.appendChild(div);
            });
        }

        async function doUnlock(id) {
            if (!confirm("確定要消耗 30 點解鎖業主聯絡電話嗎？")) return;
            const res = await fetch(`/api/cases/${id}/unlock`, { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
                alert(data.message);
                fetchLeads();
            } else {
                alert(data.detail || "解鎖失敗");
            }
        }

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
</html>
    """
