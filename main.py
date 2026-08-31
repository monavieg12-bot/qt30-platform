import os
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./qt30_platform.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

engine = create_engine(
    DATABASE_URL, 
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Master(Base):
    __tablename__ = "masters"
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), default="專業水電修繕師傅")
    phone = Column(String(20), default="0912-345-678")
    points = Column(Integer, default=100)
    rating = Column(Float, default=4.9)
    review_count = Column(Integer, default=28)
    verified = Column(Boolean, default=True)  # 實名認證
    license_type = Column(String(50), default="甲種電匠 / 乙級室內配線")  # 專業證照

class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100))
    category = Column(String(50))
    location = Column(String(50))
    budget_range = Column(String(50))
    description = Column(Text)
    customer_name = Column(String(50))
    customer_phone = Column(String(20))
    status = Column(String(20), default="MATCHING")  # MATCHING, AWARDED, COMPLETED
    created_at = Column(DateTime, default=datetime.utcnow)
    quotes = relationship("Quote", back_populates="job")
    reviews = relationship("Review", back_populates="job")

class Quote(Base):
    __tablename__ = "quotes"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    master_id = Column(Integer, ForeignKey("masters.id"))
    amount = Column(Integer)
    breakdown = Column(Text)
    days_required = Column(String(30))
    is_awarded = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    job = relationship("Job", back_populates="quotes")
    master = relationship("Master")

class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    customer_name = Column(String(50))
    rating = Column(Integer, default=5)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    job = relationship("Job", back_populates="reviews")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        # 初始化預設師傅
        if not db.query(Master).first():
            default_master = Master(
                name="陳師傅 (永豐水電修繕)",
                phone="0912-345-678",
                points=150,
                rating=4.9,
                review_count=32,
                verified=True,
                license_type="甲種電匠 / 乙級水電雙證照"
            )
            db.add(default_master)
            db.commit()
        yield db
    finally:
        db.close()

app = FastAPI(title="QT30 智慧修繕媒合平台")

@app.get("/", response_class=HTMLResponse)
async def home_page(db: Session = Depends(get_db)):
    jobs = db.query(Job).order_by(Job.id.desc()).all()
    master = db.query(Master).first()

    jobs_html = ""
    for j in jobs:
        status_badge = {
            "MATCHING": '<span class="badge bg-green">🟢 招募報價中</span>',
            "AWARDED": '<span class="badge bg-purple">🏆 業主已選定報價</span>',
            "COMPLETED": '<span class="badge bg-gray">✅ 案件已完工結案</span>'
        }.get(j.status, "")

        quotes_list_html = ""
        for q in j.quotes:
            awarded_mark = '<span class="tag-awarded">🎉 業主得標採納</span>' if q.is_awarded else ''
            quotes_list_html += f"""
            <div class="quote-item {'quote-awarded' if q.is_awarded else ''}">
                <div class="quote-header">
                    <div>
                        <strong>{q.master.name}</strong> 
                        <span class="rating-star">⭐ {q.master.rating}</span>
                        <span class="badge-verify">🛡️ 實名認證</span>
                        <span class="badge-license">📜 {q.master.license_type}</span>
                    </div>
                    <div class="quote-price">NT$ {q.amount:,}</div>
                </div>
                <div class="quote-body">
                    <p><strong>🛠️ 工項工料明細：</strong>{q.breakdown}</p>
                    <p><strong>⏱️ 預估施工工期：</strong>{q.days_required}</p>
                    {awarded_mark}
                </div>
                {'' if (j.status != 'MATCHING') else f'''
                <form action="/award-quote" method="post" style="margin-top: 8px;">
                    <input type="hidden" name="quote_id" value="{q.id}">
                    <button type="submit" class="btn-award">🎯 採納此師傅報價</button>
                </form>
                '''}
            </div>
            """

        reviews_list_html = ""
        for r in j.reviews:
            reviews_list_html += f"""
            <div class="review-item">
                <div><strong>{r.customer_name}</strong> 給予評價：{'⭐' * r.rating}</div>
                <p class="review-text">「{r.comment}」</p>
            </div>
            """

        phone_display = j.customer_phone if j.status == 'AWARDED' else f"{j.customer_phone[:4]}***{j.customer_phone[-3:]}（得標後解鎖完整聯絡電話）"

        jobs_html += f"""
        <div class="card job-card">
            <div class="job-header">
                <div>
                    <h3 class="job-title">{j.title}</h3>
                    <div class="job-meta">
                        <span>📍 {j.location}</span>
                        <span>🏷️ {j.category}</span>
                        <span>💰 預算範圍：{j.budget_range}</span>
                        <span>🕒 {j.created_at.strftime('%Y-%m-%d %H:%M')}</span>
                    </div>
                </div>
                <div>{status_badge}</div>
            </div>
            
            <p class="job-desc">{j.description}</p>
            
            <div class="contact-box">
                <span>👤 案主：{j.customer_name}</span>
                <span>📞 電話：<strong>{phone_display}</strong></span>
            </div>

            <!-- 師傅報價區 -->
            <div class="section-title">💬 師傅專業報價與工項明細 ({len(j.quotes)})</div>
            <div class="quotes-container">
                {quotes_list_html if j.quotes else '<p class="text-muted">尚無師傅報價，搶先送出報價爭取案源！</p>'}
            </div>

            <!-- 師傅快速搶單報價 Form (僅在招募中顯示) -->
            {f'''
            <div class="quote-form-box">
                <h4>👷 師傅快速搶單報價 (消耗 10 點數)</h4>
                <form action="/submit-quote" method="post" class="grid-form">
                    <input type="hidden" name="job_id" value="{j.id}">
                    <div class="form-group">
                        <label>總報價金額 (NTD)</label>
                        <input type="number" name="amount" placeholder="例如：4500" required min="100">
                    </div>
                    <div class="form-group">
                        <label>預計工期</label>
                        <input type="text" name="days_required" placeholder="例如：半天或 1 工作天" required>
                    </div>
                    <div class="form-group full-width">
                        <label>工項與材料明細說明 (透明度高更易得標)</label>
                        <textarea name="breakdown" rows="2" placeholder="例如：含國產水龍頭本體換新、高壓軟管更新、舊管線拆除清運" required></textarea>
                    </div>
                    <div class="full-width">
                        <button type="submit" class="btn-quote">🚀 送出報價 (扣 10 點)</button>
                    </div>
                </form>
            </div>
            ''' if j.status == 'MATCHING' else ''}

            <!-- 完工評價區 -->
            {f'''
            <div class="review-form-box">
                <h4>⭐ 業主完工驗收評價</h4>
                {reviews_list_html}
                <form action="/submit-review" method="post" class="review-form">
                    <input type="hidden" name="job_id" value="{j.id}">
                    <input type="text" name="customer_name" placeholder="您的姓名或暱稱" required style="width: 150px;">
                    <select name="rating" style="width: 120px;">
                        <option value="5">⭐⭐⭐⭐⭐ (5星)</option>
                        <option value="4">⭐⭐⭐⭐ (4星)</option>
                        <option value="3">⭐⭐⭐ (3星)</option>
                    </select>
                    <input type="text" name="comment" placeholder="分享本次施工品質與滿意度..." required style="flex: 1;">
                    <button type="submit" class="btn-primary" style="padding: 6px 14px;">送出評價</button>
                </form>
            </div>
            ''' if j.status == 'AWARDED' else ''}
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>QT30 智慧修繕媒合平台</title>
        <style>
            :root {{
                --primary: #2563eb;
                --primary-hover: #1d4ed8;
                --success: #10b981;
                --bg: #0f172a;
                --card-bg: #1e293b;
                --border: #334155;
                --text: #f8fafc;
                --text-muted: #94a3b8;
                --accent: #8b5cf6;
            }}
            * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }}
            body {{ background-color: var(--bg); color: var(--text); padding-bottom: 60px; }}
            .container {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
            header {{ border-bottom: 1px solid var(--border); padding: 20px 0; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; }}
            .brand {{ font-size: 24px; font-weight: 800; color: #60a5fa; display: flex; align-items: center; gap: 8px; }}
            .master-profile {{ background: #0f172a; border: 1px solid var(--border); padding: 8px 16px; border-radius: 9999px; display: flex; align-items: center; gap: 10px; font-size: 14px; }}
            .badge-points {{ background: #d97706; color: white; padding: 2px 8px; border-radius: 12px; font-weight: bold; }}
            .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 24px; }}
            .form-grid {{ display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }}
            .full-width {{ grid-column: span 2; }}
            label {{ display: block; font-size: 13px; color: var(--text-muted); margin-bottom: 5px; }}
            input, select, textarea {{ width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: #0f172a; color: white; outline: none; }}
            input:focus, textarea:focus {{ border-color: var(--primary); }}
            .btn-primary {{ background: var(--primary); color: white; border: none; padding: 12px 20px; border-radius: 8px; font-weight: bold; cursor: pointer; }}
            .btn-primary:hover {{ background: var(--primary-hover); }}
            .job-card {{ position: relative; }}
            .job-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }}
            .job-title {{ font-size: 18px; color: #e2e8f0; }}
            .job-meta {{ font-size: 13px; color: var(--text-muted); display: flex; gap: 12px; margin-top: 5px; flex-wrap: wrap; }}
            .job-desc {{ background: #0f172a; padding: 12px; border-radius: 8px; line-height: 1.6; font-size: 14px; margin-bottom: 15px; }}
            .contact-box {{ display: flex; gap: 20px; font-size: 13px; background: #1e1b4b; border: 1px solid #3730a3; padding: 8px 12px; border-radius: 6px; margin-bottom: 15px; color: #c7d2fe; }}
            .section-title {{ font-size: 14px; font-weight: bold; color: #93c5fd; margin-bottom: 10px; }}
            .quote-item {{ background: #0f172a; border: 1px solid var(--border); border-radius: 8px; padding: 12px; margin-bottom: 10px; }}
            .quote-awarded {{ border-color: #a855f7; background: #2e1065; }}
            .quote-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }}
            .quote-price {{ font-size: 18px; font-weight: 800; color: #34d399; }}
            .quote-body {{ font-size: 13px; line-height: 1.6; color: #cbd5e1; }}
            .rating-star {{ color: #fbbf24; font-weight: bold; font-size: 13px; margin-left: 5px; }}
            .badge-verify {{ background: #065f46; color: #6ee7b7; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 4px; }}
            .badge-license {{ background: #1e3a8a; color: #93c5fd; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 4px; }}
            .tag-awarded {{ display: inline-block; background: #7e22ce; color: white; font-size: 12px; padding: 3px 8px; border-radius: 4px; margin-top: 6px; }}
            .btn-award {{ background: #8b5cf6; color: white; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }}
            .btn-award:hover {{ background: #7c3aed; }}
            .quote-form-box {{ background: #1e1e38; border: 1px dashed #4f46e5; border-radius: 8px; padding: 15px; margin-top: 15px; }}
            .grid-form {{ display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 8px; }}
            .btn-quote {{ background: #10b981; color: white; border: none; padding: 10px; border-radius: 6px; width: 100%; font-weight: bold; cursor: pointer; }}
            .btn-quote:hover {{ background: #059669; }}
            .badge {{ font-size: 12px; padding: 4px 8px; border-radius: 6px; font-weight: bold; }}
            .bg-green {{ background: #064e3b; color: #6ee7b7; }}
            .bg-purple {{ background: #581c87; color: #d8b4fe; }}
            .bg-gray {{ background: #334155; color: #94a3b8; }}
            .review-form-box {{ background: #0f172a; border-radius: 8px; padding: 12px; margin-top: 12px; border: 1px solid #334155; }}
            .review-item {{ border-left: 3px solid #fbbf24; padding-left: 10px; margin-bottom: 8px; font-size: 13px; }}
            .review-form {{ display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }}
            .template-btn {{ background: #334155; color: #e2e8f0; border: none; padding: 5px 10px; border-radius: 6px; font-size: 12px; cursor: pointer; margin-right: 6px; margin-bottom: 6px; }}
            .template-btn:hover {{ background: #475569; }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <div class="brand">⚡ QT30 專業修繕媒合</div>
                <div class="master-profile">
                    <span>👷 <strong>{master.name}</strong></span>
                    <span class="badge-verify">🛡️ 實名核實</span>
                    <span class="badge-points">點數: {master.points} 點</span>
                </div>
            </header>

            <!-- 業主發案表單 -->
            <div class="card">
                <h2>📝 業主快速刊登修繕需求</h2>
                <div style="margin: 10px 0;">
                    <span style="font-size: 12px; color: var(--text-muted);">快速載入範本：</span>
                    <button class="template-btn" onclick="setTpl('衛浴防水抓漏工程', '抓漏防水', '台北市信義區', 'NT$ 15,000 - 30,000', '主臥浴室牆角持續滲水至隔壁臥室地板，需完整儀器抓漏與高壓灌注防水。')">🚿 衛浴防水抓漏</button>
                    <button class="template-btn" onclick="setTpl('全室老舊電線重新抽拉重配', '水電工程', '新北市板橋區', 'NT$ 35,000 - 60,000', '30年公寓全室配線更新、總開關箱配電更換太平洋線材、新增接地線。')">⚡ 全室重拉電線</button>
                    <button class="template-btn" onclick="setTpl('加壓馬達更換靜音變頻款', '給排水工程', '桃園市中壢區', 'NT$ 6,000 - 10,000', '頂樓加壓馬達運轉噪音極大且漏水，需拆除並更換為 1/2HP 靜音變頻馬達。')">🚰 加壓馬達更換</button>
                </div>

                <form action="/create-job" method="post" class="form-grid">
                    <div class="form-group">
                        <label>案件標題</label>
                        <input type="text" id="title" name="title" placeholder="例如：衛浴龍頭漏水換新" required>
                    </div>
                    <div class="form-group">
                        <label>修繕類別</label>
                        <select id="category" name="category">
                            <option value="水電配線">⚡ 水電工程 / 電線抽拉</option>
                            <option value="抓漏防水">🚿 衛浴漏水 / 防水高壓灌注</option>
                            <option value="給排水衛浴">🚰 水龍頭 / 加壓馬達 / 衛浴更換</option>
                            <option value="通水管馬桶">🚽 通馬桶 / 水管包通疏通</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>施工地區</label>
                        <input type="text" id="location" name="location" placeholder="例如：新北市淡水區" required>
                    </div>
                    <div class="form-group">
                        <label>預估預算</label>
                        <input type="text" id="budget_range" name="budget_range" placeholder="例如：NT$ 3,000 - 5,000" required>
                    </div>
                    <div class="form-group">
                        <label>業主聯絡姓名</label>
                        <input type="text" name="customer_name" placeholder="例如：林先生" required>
                    </div>
                    <div class="form-group">
                        <label>聯絡電話 (得標師傅才可看見)</label>
                        <input type="tel" name="customer_phone" placeholder="例如：0987-654-321" required pattern="[0-9-]+" minlength="9">
                    </div>
                    <div class="form-group full-width">
                        <label>問題詳情與現況說明</label>
                        <textarea id="description" name="description" rows="3" placeholder="請詳細描述損壞情況、樓層、管線材質..." required></textarea>
                    </div>
                    <div class="full-width">
                        <button type="submit" class="btn-primary" style="width: 100%;">📢 免費發布修繕案件</button>
                    </div>
                </form>
            </div>

            <!-- 案件列表 -->
            <h2>📋 最新發布案件與報價明細</h2>
            <div style="margin-top: 15px;">
                {jobs_html if jobs_html else '<p class="text-muted">目前暫無案件，請透過上方表單刊登第一筆需求！</p>'}
            </div>
        </div>

        <script>
            function setTpl(t, c, l, b, d) {{
                document.getElementById('title').value = t;
                document.getElementById('location').value = l;
                document.getElementById('budget_range').value = b;
                document.getElementById('description').value = d;
            }}
        </script>
    </body>
    </html>
    """
    return html

@app.post("/create-job")
async def create_job(
    title: str = Form(...),
    category: str = Form(...),
    location: str = Form(...),
    budget_range: str = Form(...),
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    description: str = Form(...),
    db: Session = Depends(get_db)
):
    job = Job(
        title=title,
        category=category,
        location=location,
        budget_range=budget_range,
        customer_name=customer_name,
        customer_phone=customer_phone,
        description=description,
        status="MATCHING"
    )
    db.add(job)
    db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/submit-quote")
async def submit_quote(
    job_id: int = Form(...),
    amount: int = Form(...),
    breakdown: str = Form(...),
    days_required: str = Form(...),
    db: Session = Depends(get_db)
):
    master = db.query(Master).first()
    if master.points < 10:
        raise HTTPException(status_code=400, detail="點數不足，請先儲值點數！")
    
    master.points -= 10
    quote = Quote(
        job_id=job_id,
        master_id=master.id,
        amount=amount,
        breakdown=breakdown,
        days_required=days_required
    )
    db.add(quote)
    db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/award-quote")
async def award_quote(quote_id: int = Form(...), db: Session = Depends(get_db)):
    quote = db.query(Quote).filter(Quote.id == quote_id).first()
    if quote:
        quote.is_awarded = True
        quote.job.status = "AWARDED"
        db.commit()
    return RedirectResponse(url="/", status_code=303)

@app.post("/submit-review")
async def submit_review(
    job_id: int = Form(...),
    customer_name: str = Form(...),
    rating: int = Form(...),
    comment: str = Form(...),
    db: Session = Depends(get_db)
):
    review = Review(
        job_id=job_id,
        customer_name=customer_name,
        rating=rating,
        comment=comment
    )
    db.add(review)
    db.commit()
    return RedirectResponse(url="/", status_code=303)
