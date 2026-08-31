from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey, Boolean
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from datetime import datetime
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
    work_detail = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    case = relationship("Case", back_populates="bids")
    expert = relationship("User", back_populates="bids")

Base.metadata.create_all(bind=engine)

# ================= 2. 伺服器核心 =================
app = FastAPI(title="QT30社區修繕達人網 - 公開競標平台")

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

# ================= 3. 名單、解鎖與競標 API =================
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
        
        # 整理公開競標報價資訊
        bids_list = []
        for b in c.bids:
            bids_list.append({
                "expert_name": b.expert.username if b.expert else "專業師傅",
                "amount": b.bid_amount,
                "detail": b.work_detail,
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
            "unlock_fee": c.unlock_fee,
            "bids_count": len(c.bids),
            "bids": bids_list,
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
        description=description, image_url=img_url, unlock_fee=30
    )
    db.add(new_c)
    db.commit()
    return {"status": "success", "message": "發案成功！已進入公開比價大廳。"}

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

    if expert.points < case.unlock_fee:
        raise HTTPException(status_code=400, detail=f"點數不足！餘額 {expert.points} 點，解鎖需 {case.unlock_fee} 點")

    expert.points -= case.unlock_fee
    new_u = LeadUnlock(case_id=case.id, expert_id=expert.id)
    db.add(new_u)
    db.commit()

    return {
        "status": "success",
        "message": f"成功解鎖！扣除 {case.unlock_fee} 點，剩餘 {expert.points} 點",
        "points": expert.points,
        "contact": {"name": case.client_name, "phone": case.client_phone, "line": case.client_line or "無"}
    }

@app.post("/api/cases/{case_id}/bid")
def submit_bid(
    case_id: int,
    bid_amount: int = Form(...),
    work_detail: str = Form(...),
    db: Session = Depends(get_db)
):
    case = db.query(Case).filter(Case.id == case_id).first()
    expert = db.query(User).filter(User.username == "金牌水電行-阿銘").first()

    if not case:
        raise HTTPException(status_code=404, detail="找不到此案件")
    if not expert:
        raise HTTPException(status_code=404, detail="專家帳號異常")

    new_bid = Bid(case_id=case.id, expert_id=expert.id, bid_amount=bid_amount, work_detail=work_detail)
    db.add(new_bid)
    db.commit()
    return {"status": "success", "message": "已成功公開送出報價與施工明細！"}

# ================= 4. 前端單一介面 =================
@app.get("/", response_class=HTMLResponse)
def index_ui():
    return """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QT30社區修繕達人網 - 即時公開競標與修繕行情大廳</title>
    <meta name="description" content="全台專業水電維修、抓漏防水、冷氣裝修透明報價與工法明細公開競標平台。">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen font-sans">
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
                <span class="bg-blue-600 text-white text-xs px-2 py-1 rounded">發案</span> 免費刊登修繕需求（公開比價）
            </h2>
            <form id="postForm" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <input type="text" name="title" required placeholder="需求項目（例：衛浴抓漏防水翻修）" class="border p-2.5 rounded-lg text-sm bg-gray-50 focus:bg-white">
                <select name="category" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                    <option value="水電維修">水電維修</option>
                    <option value="防水抓漏">防水抓漏</option>
                    <option value="冷氣空調">冷氣空調</option>
                    <option value="油漆粉刷">油漆粉刷</option>
                </select>
                <input type="text" name="district" required placeholder="服務地區（例：新北市淡水區）" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                <input type="number" name="budget" required placeholder="預估預算金額（元）" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                <input type="text" name="client_name" required placeholder="聯絡人稱呼（例：陳先生）" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                <input type="text" name="client_phone" required placeholder="聯絡電話（例：0912345678）" class="border p-2.5 rounded-lg text-sm bg-gray-50">
                <input type="text" name="client_line" placeholder="LINE ID（選填）" class="border p-2.5 rounded-lg text-sm bg-gray-50 md:col-span-2">
                <textarea name="description" rows="2" placeholder="詳細情況補充說明..." class="border p-2.5 rounded-lg text-sm bg-gray-50 md:col-span-2"></textarea>
                <button type="submit" class="md:col-span-2 bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-xl transition shadow">
                    🚀 免費送出修繕需求
                </button>
            </form>
        </section>

        <!-- 搶單與競標大廳 -->
        <section>
            <div class="flex justify-between items-center mb-3">
                <h2 class="text-lg font-bold text-gray-900">⚡ 即時公開競標大廳（無名額限制）</h2>
                <button onclick="fetchLeads()" class="text-xs bg-white border border-gray-300 hover:bg-gray-50 px-3 py-1.5 rounded-lg shadow-sm">🔄 重新整理</button>
            </div>
            <div id="leadsList" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </section>
    </main>

    <!-- 報價彈窗 -->
    <div id="bidModal" class="fixed inset-0 bg-black/50 hidden items-center justify-center p-4 z-50">
        <div class="bg-white rounded-2xl p-6 max-w-md w-full space-y-4">
            <h3 class="font-bold text-base text-gray-900">📝 填寫公開報價與施工明細</h3>
            <form id="bidForm" class="space-y-3">
                <input type="hidden" id="modalCaseId" name="case_id">
                <div>
                    <label class="block text-xs font-semibold text-gray-700 mb-1">報價金額 (NT$)</label>
                    <input type="number" name="bid_amount" required placeholder="例：18000" class="w-full border p-2 rounded-lg text-sm">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-gray-700 mb-1">工法、材料與施工明細說明</label>
                    <textarea name="work_detail" required rows="3" placeholder="例：含高壓灌注防水針、進口環氧樹脂填補、保固兩年..." class="w-full border p-2 rounded-lg text-sm"></textarea>
                </div>
                <div class="flex gap-2 pt-2">
                    <button type="button" onclick="closeBidModal()" class="w-1/2 bg-gray-100 text-gray-700 py-2 rounded-xl text-xs font-bold">取消</button>
                    <button type="submit" class="w-1/2 bg-blue-600 text-white py-2 rounded-xl text-xs font-bold hover:bg-blue-700">公開送出報價</button>
                </div>
            </form>
        </div>
    </div>

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
                const bidsHtml = item.bids.map(b => `
                    <div class="p-2.5 bg-gray-50 border border-gray-200 rounded-lg text-xs space-y-1">
                        <div class="flex justify-between items-center font-bold">
                            <span class="text-blue-700">🛠️ ${b.expert_name}</span>
                            <span class="text-red-600 font-mono">NT$ ${b.amount.toLocaleString()}</span>
                        </div>
                        <p class="text-gray-600 text-[11px] leading-relaxed">${b.detail}</p>
                        <div class="text-gray-400 text-[10px] text-right">${b.time}</div>
                    </div>
                `).join('');

                const div = document.createElement('article');
                div.setAttribute('itemscope', '');
                div.setAttribute('itemtype', 'https://schema.org/Offer');
                div.className = "bg-white p-5 rounded-2xl border border-gray-200 shadow-sm flex flex-col justify-between";
                
                div.innerHTML = `
                    <div>
                        <div class="flex justify-between items-center mb-2">
                            <span class="bg-blue-50 text-blue-700 font-semibold text-xs px-2 py-1 rounded-md border border-blue-200">${item.category}</span>
                            <span class="text-xs font-bold text-emerald-700 bg-emerald-50 px-2 py-1 rounded">
                                已投標師傅：${item.bids_count} 位
                            </span>
                        </div>
                        <h3 class="font-bold text-gray-900 text-base mb-1" itemprop="name">${item.title}</h3>
                        <p class="text-xs text-gray-500 mb-2">📍 ${item.district} ｜ 💰 案主預算：NT$ ${item.budget.toLocaleString()}</p>
                        ${item.description ? `<p class="text-xs text-gray-600 bg-gray-50 p-2.5 rounded-lg mb-3" itemprop="description">${item.description}</p>` : ''}
                        
                        <!-- 業主聯絡資訊（需解鎖） -->
                        <div class="p-3 bg-amber-50/80 border border-amber-200 rounded-xl space-y-1 text-xs mb-3">
                            <div class="text-amber-900 font-bold mb-1">📋 業主聯絡資料</div>
                            <div>稱呼：<span class="font-semibold text-gray-900">${item.contact.name}</span></div>
                            <div>電話：<span class="font-mono font-bold ${item.is_unlocked ? 'text-blue-600 text-sm' : 'text-gray-500'}">${item.contact.phone}</span></div>
                            <div>LINE：<span class="font-semibold text-gray-900">${item.contact.line}</span></div>
                        </div>

                        <!-- 公開競標報價與明細列表 -->
                        <div class="space-y-1.5 mb-2">
                            <div class="text-xs font-bold text-gray-700 flex justify-between">
                                <span>💬 師傅公開報價明細 (${item.bids_count})</span>
                            </div>
                            <div class="space-y-1.5 max-h-40 overflow-y-auto pr-1">
                                ${item.bids.length > 0 ? bidsHtml : '<div class="text-xs text-gray-400 italic py-1">尚無師傅提出報價明細，成為首位報價者！</div>'}
                            </div>
                        </div>
                    </div>

                    <div class="mt-4 pt-2 border-t border-gray-100 flex gap-2">
                        ${item.is_unlocked 
                            ? `<button disabled class="flex-1 bg-emerald-600 text-white font-bold py-2.5 rounded-xl text-xs">✅ 已解鎖聯絡</button>`
                            : `<button onclick="doUnlock(${item.id})" class="flex-1 bg-amber-500 hover:bg-amber-600 text-white font-bold py-2.5 rounded-xl text-xs transition shadow-sm">
                                🔓 扣 ${item.unlock_fee} 點解鎖聯絡
                               </button>`
                        }
                        <button onclick="openBidModal(${item.id})" class="flex-1 bg-blue-600 hover:bg-blue-700 text-white font-bold py-2.5 rounded-xl text-xs transition shadow-sm">
                            📝 公開參與報價
                        </button>
                    </div>
                `;
                box.appendChild(div);
            });
        }

        async function doUnlock(id) {
            if (!confirm("確定要消耗 30 點解鎖業主聯絡電話與 LINE 嗎？")) return;
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
</html>