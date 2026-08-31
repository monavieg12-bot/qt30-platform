from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
import shutil
import os

from database import Base, engine, SessionLocal, User, Case, LeadUnlock, init_db

init_db()

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

# 確保預設有一位測試師傅
@app.on_event("startup")
def setup_default_user():
    db = SessionLocal()
    user = db.query(User).filter(User.username == "金牌水電行-阿銘").first()
    if not user:
        user = User(username="金牌水電行-阿銘", role="expert", points=150, is_verified=True)
        db.add(user)
        db.commit()
    db.close()

# 扣點解鎖名單 API
@app.post("/api/cases/{case_id}/unlock")
def unlock_case(case_id: int, db: Session = Depends(get_db)):
    case = db.query(Case).filter(Case.id == case_id).first()
    expert = db.query(User).filter(User.username == "金牌水電行-阿銘").first()

    if not case:
        raise HTTPException(status_code=404, detail="找不到此案件")
    if not expert:
        raise HTTPException(status_code=404, detail="找不到專家帳號")

    # 檢查是否已經解鎖過
    existing = db.query(LeadUnlock).filter(
        LeadUnlock.case_id == case_id,
        LeadUnlock.expert_id == expert.id
    ).first()
    if existing:
        return {
            "status": "success",
            "message": "您先前已解鎖過此案件！",
            "contact": {"name": case.client_name, "phone": case.client_phone, "line": case.client_line or "無"}
        }

    # 檢查搶單名額是否已滿
    if case.current_unlocks >= case.max_unlocks:
        raise HTTPException(status_code=400, detail="名額已搶完（上限 3 位）！")

    # 檢查點數餘額
    if expert.points < case.unlock_fee:
        raise HTTPException(status_code=400, detail=f"點數不足！解鎖需 {case.unlock_fee} 點，目前餘額 {expert.points} 點")

    # 執行扣點與名額累加
    expert.points -= case.unlock_fee
    case.current_unlocks += 1
    new_unlock = LeadUnlock(case_id=case.id, expert_id=expert.id)
    db.add(new_unlock)
    db.commit()

    return {
        "status": "success",
        "message": f"成功解鎖！扣除 {case.unlock_fee} 點，剩餘 {expert.points} 點",
        "contact": {"name": case.client_name, "phone": case.client_phone, "line": case.client_line or "無"}
    }

# 發布需求 API
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
    image_url = None
    if file and file.filename:
        file_location = f"uploads/{file.filename}"
        with open(file_location, "wb+") as buffer:
            shutil.copyfileobj(file.file, buffer)
        image_url = f"/{file_location}"

    new_case = Case(
        title=title,
        category=category,
        district=district,
        budget=budget,
        client_name=client_name,
        client_phone=client_phone,
        client_line=client_line,
        description=description,
        image_url=image_url,
        unlock_fee=30,
        max_unlocks=3
    )
    db.add(new_case)
    db.commit()
    return {"status": "success", "message": "發案成功！案件已進入大廳供師傅搶單。"}

# 取得案件清單（聯絡資訊自動遮罩保護）
@app.get("/api/cases")
def list_cases(db: Session = Depends(get_db)):
    cases = db.query(Case).order_by(Case.id.desc()).all()
    expert = db.query(User).filter(User.username == "金牌水電行-阿銘").first()
    
    result = []
    for c in cases:
        unlocked = False
        if expert:
            unlocked = db.query(LeadUnlock).filter(
                LeadUnlock.case_id == c.id,
                LeadUnlock.expert_id == expert.id
            ).first() is not None

        masked_phone = c.client_phone[:4] + "****" + c.client_phone[-2:] if len(c.client_phone) >= 6 else "***"
        masked_name = c.client_name[0] + "*" * (len(c.client_name) - 1)

        result.append({
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
    return result

# 前端單一頁面
@app.get("/", response_class=HTMLResponse)
def index_page():
    return """
<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QT30社區修繕達人網 - 名單媒合平台</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 text-gray-800">
    <header class="bg-blue-600 text-white p-4 shadow-md sticky top-0 z-50">
        <div class="max-w-5xl mx-auto flex justify-between items-center">
            <h1 class="text-2xl font-bold">🏠 QT30 社區修繕達人網</h1>
            <div class="bg-blue-800 px-4 py-2 rounded-lg text-sm font-semibold">
                師傅：金牌水電行-阿銘 | 點數餘額：<span id="userPoints" class="text-yellow-300 text-base">150</span> 點
            </div>
        </div>
    </header>

    <main class="max-w-5xl mx-auto p-4 space-y-8">
        <!-- 消費者快速發案區 -->
        <section class="bg-white p-6 rounded-xl shadow-sm border border-gray-100">
            <h2 class="text-xl font-bold text-gray-800 mb-4">📢 消費者免費發布需求</h2>
            <form id="caseForm" class="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                    <label class="block text-sm font-medium text-gray-700">需求名稱</label>
                    <input type="text" name="title" required placeholder="例如：衛浴抓漏防水翻修" class="mt-1 w-full border rounded-lg p-2">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">服務分類</label>
                    <select name="category" class="mt-1 w-full border rounded-lg p-2">
                        <option value="水電維修">水電維修</option>
                        <option value="防水抓漏">防水抓漏</option>
                        <option value="冷氣空調">冷氣空調</option>
                        <option value="油漆粉刷">油漆粉刷</option>
                    </select>
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">服務地區</label>
                    <input type="text" name="district" required placeholder="例如：新北市淡水區" class="mt-1 w-full border rounded-lg p-2">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">預估預算 (元)</label>
                    <input type="number" name="budget" required placeholder="例如：15000" class="mt-1 w-full border rounded-lg p-2">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">您的稱呼</label>
                    <input type="text" name="client_name" required placeholder="例如：陳先生" class="mt-1 w-full border rounded-lg p-2">
                </div>
                <div>
                    <label class="block text-sm font-medium text-gray-700">聯絡電話</label>
                    <input type="text" name="client_phone" required placeholder="例如：0912345678" class="mt-1 w-full border rounded-lg p-2">
                </div>
                <div class="md:col-span-2">
                    <label class="block text-sm font-medium text-gray-700">LINE ID (選填)</label>
                    <input type="text" name="client_line" placeholder="例如：water_chen" class="mt-1 w-full border rounded-lg p-2">
                </div>
                <div class="md:col-span-2">
                    <label class="block text-sm font-medium text-gray-700">需求詳細描述</label>
                    <textarea name="description" rows="2" placeholder="詳細說明損壞情況..." class="mt-1 w-full border rounded-lg p-2"></textarea>
                </div>
                <div class="md:col-span-2">
                    <label class="block text-sm font-medium text-gray-700">現場照片 (選填)</label>
                    <input type="file" name="file" accept="image/*" class="mt-1 w-full border p-2 rounded-lg bg-gray-50">
                </div>
                <div class="md:col-span-2">
                    <button type="submit" class="w-full bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 rounded-lg">🚀 免費送出修繕需求</button>
                </div>
            </form>
        </section>

        <!-- 師傅搶單大廳 -->
        <section>
            <div class="flex justify-between items-center mb-4">
                <h2 class="text-xl font-bold text-gray-800">⚡ 即時名單搶單大廳（限量 3 名師傅）</h2>
                <button onclick="loadCases()" class="text-sm bg-gray-200 hover:bg-gray-300 px-3 py-1 rounded">🔄 重新整理</button>
            </div>
            <div id="caseList" class="grid grid-cols-1 md:grid-cols-2 gap-4"></div>
        </section>
    </main>

    <script>
        async function loadCases() {
            const res = await fetch('/api/cases');
            const cases = await res.json();
            const container = document.getElementById('caseList');
            container.innerHTML = '';

            cases.forEach(c => {
                const isFull = c.current_unlocks >= c.max_unlocks;
                const card = document.createElement('div');
                card.className = "bg-white p-5 rounded-xl border border-gray-200 shadow-sm flex flex-col justify-between";
                
                card.innerHTML = `
                    <div>
                        <div class="flex justify-between items-start">
                            <span class="bg-blue-100 text-blue-800 text-xs px-2.5 py-0.5 rounded font-bold">${c.category}</span>
                            <span class="text-sm font-semibold ${isFull ? 'text-red-500' : 'text-green-600'}">
                                搶單名額：${c.current_unlocks} / ${c.max_unlocks}
                            </span>
                        </div>
                        <h3 class="text-lg font-bold text-gray-900 mt-2">${c.title}</h3>
                        <p class="text-sm text-gray-500">📍 ${c.district} | 預算：NT$ ${c.budget.toLocaleString()}</p>
                        <p class="text-sm text-gray-700 mt-2 bg-gray-50 p-2 rounded">${c.description || '無詳細說明'}</p>
                        ${c.image_url ? `<img src="${c.image_url}" class="mt-2 w-full h-40 object-cover rounded-lg">` : ''}
                        
                        <div class="mt-4 p-3 bg-yellow-50 border border-yellow-200 rounded-lg">
                            <p class="text-xs font-bold text-yellow-800 mb-1">📋 發案業主聯絡資訊：</p>
                            <p class="text-sm">稱呼：<span class="font-bold">${c.contact.name}</span></p>
                            <p class="text-sm">電話：<span class="font-bold ${c.is_unlocked ? 'text-blue-600' : 'text-gray-500'}">${c.contact.phone}</span></p>
                            <p class="text-sm">LINE：<span class="font-bold">${c.contact.line}</span></p>
                        </div>
                    </div>

                    <div class="mt-4">
                        ${c.is_unlocked 
                            ? `<button disabled class="w-full bg-gray-400 text-white font-bold py-2 rounded-lg cursor-not-allowed">✅ 已解鎖聯絡方式</button>`
                            : isFull 
                                ? `<button disabled class="w-full bg-red-400 text-white font-bold py-2 rounded-lg cursor-not-allowed">❌ 名額已滿</button>`
                                : `<button onclick="unlockCase(${c.id})" class="w-full bg-amber-500 hover:bg-amber-600 text-white font-bold py-2 rounded-lg transition">
                                    🔓 消耗 ${c.unlock_fee} 點解鎖聯絡方式
                                   </button>`
                        }
                    </div>
                `;
                container.appendChild(card);
            });
        }

        async function unlockCase(caseId) {
            if (!confirm("確定要消耗點數解鎖此案主的真實電話與聯絡方式嗎？")) return;
            const res = await fetch(`/api/cases/${caseId}/unlock`, { method: 'POST' });
            const data = await res.json();
            if (res.ok) {
                alert(data.message);
                loadCases();
            } else {
                alert(data.detail);
            }
        }

        document.getElementById('caseForm').onsubmit = async (e) => {
            e.preventDefault();
            const formData = new FormData(e.target);
            const res = await fetch('/api/cases', { method: 'POST', body: formData });
            const data = await res.json();
            alert(data.message);
            e.target.reset();
            loadCases();
        };

        loadCases();
    </script>
</body>
</html>
    """