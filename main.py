import os
from typing import List, Optional
from datetime import datetime
from fastapi import FastAPI, Request, Form, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, RedirectResponse
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
    name = Column(String(50), default="陳師傅 (永豐工程統包修繕)")
    phone = Column(String(20), default="0912-345-678")
    points = Column(Integer, default=100)
    rating = Column(Float, default=4.9)
    review_count = Column(Integer, default=12)
    verified = Column(Boolean, default=True)
    license_type = Column(String(100), default="甲種電匠 / 內政部室內裝修專業施工技術人員")
    bio = Column(Text, default="從事水電、泥作與統包工程逾 15 年，具備國家專業證照，堅持按圖施工、材料透明、絕不惡意追加款項。")
    quotes = relationship("Quote", back_populates="master")

class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(100))
    category = Column(String(50))
    city = Column(String(20), default="雙北地區")
    district = Column(String(20), default="")
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
    master = relationship("Master", back_populates="quotes")

class Review(Base):
    __tablename__ = "reviews"
    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"))
    master_id = Column(Integer, ForeignKey("masters.id"), nullable=True)
    customer_name = Column(String(50))
    rating = Column(Integer, default=5)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    job = relationship("Job", back_populates="reviews")

Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        master = db.query(Master).first()
        if not master:
            master = Master(
                name="陳師傅 (永豐工程統包修繕)",
                phone="0912-345-678",
                points=150,
                rating=4.9,
                review_count=12,
                verified=True,
                license_type="甲種電匠 / 內政部室內裝修專業施工技術人員",
                bio="從事水電配管、泥作打底、浴室翻新與全室統包工程逾 15 年，具備國家雙證照，堅持工法扎實、用料實在、施工過程透明回報。"
            )
            db.add(master)
            db.commit()
            db.refresh(master)

            # 建立 2 筆過往得標歷史示範案例，展現完整履歷
            demo_job_1 = Job(
                title="主臥衛浴乾濕分離整修與防水翻新",
                category="衛浴整修",
                city="新北市",
                district="板橋區",
                location="新北市板橋區",
                budget_range="NT$ 80,000 - 120,000",
                description="舊浴缸打除清運、重作試水防水層、更換乾濕分離強化玻璃拉門與面盆設備。",
                customer_name="張小姐",
                customer_phone="0911-222-333",
                status="AWARDED"
            )
            db.add(demo_job_1)
            db.commit()
            db.refresh(demo_job_1)

            demo_quote_1 = Quote(
                job_id=demo_job_1.id,
                master_id=master.id,
                amount=95000,
                breakdown="舊浴缸打除與廢棄物清運、彈性水泥防水塗刷三道（試水48小時）、國產30x60防滑地壁磚鋪設、一字三拉強化玻璃淋浴拉門安裝、全套保固一年。",
                days_required="5 工作天",
                is_awarded=True
            )
            db.add(demo_quote_1)

            demo_review_1 = Review(
                job_id=demo_job_1.id,
                master_id=master.id,
                customer_name="張小姐 (板橋案主)",
                rating=5,
                comment="陳師傅非常細心！試水測試確認完全不漏水才貼磚，每天施工完都會把現場整理乾淨，報價明細清清楚楚，完全沒有加收奇怪費用！"
            )
            db.add(demo_review_1)

            demo_job_2 = Job(
                title="30年老公寓全室電線重新抽拉換新",
                category="水電",
                city="台北市",
                district="松山區",
                location="台北市松山區",
                budget_range="NT$ 40,000 - 70,000",
                description="30年老屋全室電線老舊，需抽換太平洋線材、更換士林電機無熔絲開關與接地線配置。",
                customer_name="王先生",
                customer_phone="0922-333-444",
                status="AWARDED"
            )
            db.add(demo_job_2)
            db.commit()
            db.refresh(demo_job_2)

            demo_quote_2 = Quote(
                job_id=demo_job_2.id,
                master_id=master.id,
                amount=52000,
                breakdown="全室採用太平洋 2.0mm 實心線抽拉換新、廚房與冷氣專用迴路 3 組、總開關箱配置士林電機漏電保護開關、Panasonic 螢光開關面板 12 組換新。",
                days_required="2 工作天",
                is_awarded=True
            )
            db.add(demo_quote_2)

            demo_review_2 = Review(
                job_id=demo_job_2.id,
                master_id=master.id,
                customer_name="王先生 (松山案主)",
                rating=5,
                comment="用電安全真的不能省，師傅專業解說迴路配置，完工後每組插座都逐一用儀器量測電壓與接地，非常值得推薦！"
            )
            db.add(demo_review_2)
            db.commit()

        yield db
    finally:
        db.close()

app = FastAPI(title="QT30 智慧修繕媒合平台")

COMMON_CSS = """
:root {
    --primary: #2563eb;
    --primary-hover: #1d4ed8;
    --success: #10b981;
    --bg: #0f172a;
    --card-bg: #1e293b;
    --border: #334155;
    --text: #f8fafc;
    --text-muted: #94a3b8;
    --accent: #8b5cf6;
}
* { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
body { background-color: var(--bg); color: var(--text); padding-bottom: 60px; line-height: 1.6; }
.container { max-width: 960px; margin: 0 auto; padding: 20px; }
header { border-bottom: 1px solid var(--border); padding: 20px 0; margin-bottom: 25px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }
.brand { font-size: 24px; font-weight: 800; color: #60a5fa; text-decoration: none; display: flex; align-items: center; gap: 8px; }
.master-profile { background: #0f172a; border: 1px solid var(--border); padding: 8px 16px; border-radius: 9999px; display: flex; align-items: center; gap: 10px; font-size: 14px; text-decoration: none; color: white; transition: 0.2s; }
.master-profile:hover { border-color: #60a5fa; background: #1e293b; }
.badge-points { background: #d97706; color: white; padding: 2px 8px; border-radius: 12px; font-weight: bold; }
.card { background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 20px; margin-bottom: 24px; }
.form-grid { display: grid; grid-template-columns: repeat(2, 1fr); gap: 15px; }
.full-width { grid-column: span 2; }
label { display: block; font-size: 13px; color: var(--text-muted); margin-bottom: 5px; }
input, select, textarea { width: 100%; padding: 10px; border-radius: 8px; border: 1px solid var(--border); background: #0f172a; color: white; outline: none; font-size: 14px; }
input:focus, textarea:focus, select:focus { border-color: var(--primary); }
.btn-primary { background: var(--primary); color: white; border: none; padding: 12px 20px; border-radius: 8px; font-weight: bold; cursor: pointer; text-decoration: none; display: inline-block; }
.btn-primary:hover { background: var(--primary-hover); }
.job-card { position: relative; }
.job-header { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px; }
.job-title { font-size: 18px; color: #e2e8f0; }
.job-meta { font-size: 13px; color: var(--text-muted); display: flex; gap: 10px; margin-top: 5px; flex-wrap: wrap; align-items: center; }
.badge-loc { background: #1e3a8a; color: #bfdbfe; padding: 2px 8px; border-radius: 4px; font-weight: 500; }
.badge-cat { background: #3730a3; color: #c7d2fe; padding: 2px 8px; border-radius: 4px; font-weight: 500; }
.job-desc { background: #0f172a; padding: 12px; border-radius: 8px; line-height: 1.6; font-size: 14px; margin-bottom: 15px; }
.contact-box { display: flex; gap: 20px; font-size: 13px; background: #1e1b4b; border: 1px solid #3730a3; padding: 8px 12px; border-radius: 6px; margin-bottom: 15px; color: #c7d2fe; }
.section-title { font-size: 14px; font-weight: bold; color: #93c5fd; margin-bottom: 10px; }
.quote-item { background: #0f172a; border: 1px solid var(--border); border-radius: 8px; padding: 14px; margin-bottom: 12px; }
.quote-awarded { border-color: #a855f7; background: #241142; }
.quote-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; flex-wrap: wrap; gap: 8px; }
.quote-price { font-size: 18px; font-weight: 800; color: #34d399; }
.quote-body { font-size: 13px; line-height: 1.6; color: #cbd5e1; }
.rating-star { color: #fbbf24; font-weight: bold; font-size: 13px; margin-left: 5px; }
.badge-verify { background: #065f46; color: #6ee7b7; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 4px; }
.badge-license { background: #1e3a8a; color: #93c5fd; font-size: 11px; padding: 2px 6px; border-radius: 4px; margin-left: 4px; }
.tag-awarded { display: inline-block; background: #7e22ce; color: white; font-size: 12px; padding: 3px 8px; border-radius: 4px; margin-top: 6px; font-weight: bold; }
.btn-award { background: #8b5cf6; color: white; border: none; padding: 6px 12px; border-radius: 6px; cursor: pointer; font-size: 12px; }
.btn-award:hover { background: #7c3aed; }
.quote-form-box { background: #1e1e38; border: 1px dashed #4f46e5; border-radius: 8px; padding: 15px; margin-top: 15px; }
.grid-form { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-top: 8px; }
.btn-quote { background: #10b981; color: white; border: none; padding: 10px; border-radius: 6px; width: 100%; font-weight: bold; cursor: pointer; }
.btn-quote:hover { background: #059669; }
.badge { font-size: 12px; padding: 4px 8px; border-radius: 6px; font-weight: bold; }
.bg-green { background: #064e3b; color: #6ee7b7; }
.bg-purple { background: #581c87; color: #d8b4fe; }
.bg-gray { background: #334155; color: #94a3b8; }
.review-form-box { background: #0f172a; border-radius: 8px; padding: 12px; margin-top: 12px; border: 1px solid #334155; }
.review-item { border-left: 3px solid #fbbf24; padding-left: 10px; margin-bottom: 8px; font-size: 13px; }
.review-form { display: flex; gap: 8px; margin-top: 8px; flex-wrap: wrap; }
.template-btn { background: #1e293b; color: #e2e8f0; border: 1px solid #475569; padding: 6px 12px; border-radius: 6px; font-size: 12px; cursor: pointer; margin-right: 6px; margin-bottom: 8px; transition: 0.2s; }
.template-btn:hover { background: #2563eb; border-color: #3b82f6; }
.template-group { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 8px; }
.filter-bar { display: flex; gap: 10px; background: #0f172a; padding: 12px; border-radius: 8px; margin-bottom: 15px; flex-wrap: wrap; align-items: center; }
.agree-label { font-size: 12px; color: var(--text-muted); display: flex; align-items: center; gap: 6px; margin-top: 4px; }
.agree-label a { color: #60a5fa; text-decoration: underline; }
.master-link { color: #93c5fd; text-decoration: none; font-weight: bold; }
.master-link:hover { text-decoration: underline; color: #60a5fa; }
footer { margin-top: 50px; border-top: 1px solid var(--border); padding-top: 25px; text-align: center; font-size: 13px; color: var(--text-muted); }
footer a { color: #94a3b8; text-decoration: none; margin: 0 10px; }
footer a:hover { color: #60a5fa; text-decoration: underline; }
"""

@app.get("/", response_class=HTMLResponse)
async def home_page(
    filter_city: Optional[str] = Query(None),
    filter_cat: Optional[str] = Query(None),
    db: Session = Depends(get_db)
):
    query = db.query(Job)
    if filter_city and filter_city != "全部地區":
        query = query.filter(Job.location.contains(filter_city))
    if filter_cat and filter_cat != "全部工種":
        query = query.filter(Job.category == filter_cat)
        
    jobs = query.order_by(Job.id.desc()).all()
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
                        <a href="/master/{q.master.id}" class="master-link">👷 {q.master.name}</a> 
                        <span class="rating-star">⭐ {q.master.rating} ({q.master.review_count}則評價)</span>
                        <span class="badge-verify">🛡️ 實名認證</span>
                        <span class="badge-license">📜 {q.master.license_type}</span>
                    </div>
                    <div class="quote-price">NT$ {q.amount:,}</div>
                </div>
                <div class="quote-body">
                    <p><strong>🛠️ 工項工料明細：</strong>{q.breakdown}</p>
                    <p><strong>⏱️ 預估施工工期：</strong>{q.days_required}</p>
                    <div style="margin-top: 6px; display: flex; justify-content: space-between; align-items: center;">
                        <div>{awarded_mark}</div>
                        <a href="/master/{q.master.id}" style="font-size: 12px; color: #60a5fa; text-decoration: underline;">👉 查看該廠商過往得標案例與履歷</a>
                    </div>
                </div>
                {'' if (j.status != 'MATCHING') else f'''
                <form action="/award-quote" method="post" style="margin-top: 10px;">
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
                        <span class="badge-loc">📍 {j.location}</span>
                        <span class="badge-cat">🏷️ {j.category}</span>
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

            <!-- 師傅快速搶單報價 Form -->
            {f'''
            <div class="quote-form-box">
                <h4>👷 師傅快速搶單報價 (消耗 10 點數)</h4>
                <form action="/submit-quote" method="post" class="grid-form">
                    <input type="hidden" name="job_id" value="{j.id}">
                    <div class="form-group">
                        <label>總報價金額 (NTD)</label>
                        <input type="number" name="amount" placeholder="例如：45000" required min="100">
                    </div>
                    <div class="form-group">
                        <label>預計工期</label>
                        <input type="text" name="days_required" placeholder="例如：3-5 工作天" required>
                    </div>
                    <div class="form-group full-width">
                        <label>工項與材料明細說明 (透明度高更易得標)</label>
                        <textarea name="breakdown" rows="2" placeholder="例如：含工帶料、保護工程、拆除廢棄物清運、保固一年" required></textarea>
                    </div>
                    <div class="full-width">
                        <label class="agree-label">
                            <input type="checkbox" required checked style="width: auto;"> 我已確認報價內容屬實，並同意遵循 <a href="/terms" target="_blank">平台服務條款</a>。
                        </label>
                    </div>
                    <div class="full-width" style="margin-top: 8px;">
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
                    <input type="text" name="customer_name" placeholder="您的姓名" required style="width: 150px;">
                    <select name="rating" style="width: 120px;">
                        <option value="5">⭐⭐⭐⭐⭐ (5星)</option>
                        <option value="4">⭐⭐⭐⭐ (4星)</option>
                        <option value="3">⭐⭐⭐ (3星)</option>
                    </select>
                    <input type="text" name="comment" placeholder="分享本次施工品質與師傅服務態度..." required style="flex: 1;">
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
        <title>QT30 專業裝潢修繕媒合平台</title>
        <style>{COMMON_CSS}</style>
    </head>
    <body>
        <div class="container">
            <header>
                <a href="/" class="brand">⚡ QT30 專業裝潢修繕媒合</a>
                <a href="/master/{master.id}" class="master-profile">
                    <span>👷 <strong>{master.name}</strong></span>
                    <span class="badge-verify">🛡️ ⭐ {master.rating}</span>
                    <span class="badge-points">點數: {master.points} 點</span>
                </a>
            </header>

            <!-- 業主發案表單 -->
            <div class="card">
                <h2>📝 業主快速刊登裝潢與修繕需求</h2>
                <div style="margin: 14px 0;">
                    <span style="font-size: 13px; color: #93c5fd; font-weight: bold;">⚡ 17 大工種範本（點擊自動填寫）：</span>
                    <div class="template-group">
                        <button class="template-btn" onclick="setTpl('全室隔間磚牆打除與廢棄物清運', '拆除', '新北市', '板橋區', 'NT$ 35,000 - 60,000', '室內 25 坪兩道磚牆隔間打除、全室木作裝潢天花板拆除，需含公共區域保護板鋪設與合法廢棄物清運。')">🔨 拆除工程</button>
                        <button class="template-btn" onclick="setTpl('浴室打底貼磚與客廳地磚修補', '泥作', '台北市', '中山區', 'NT$ 40,000 - 80,000', '主臥衛浴地壁磚打除後重新水泥砂漿粉光打底，貼 30x60 止滑磁磚，客廳 3 塊地磚澎拱重貼。')">🧱 泥作工程</button>
                        <button class="template-btn" onclick="setTpl('老屋全室電線重拉與總開關箱更新', '水電', '新北市', '中和區', 'NT$ 45,000 - 75,000', '35 年老屋全室電線抽拉換新、新增 2.0mm 專用迴路 3 組、總開關箱配置士林電機漏電斷路器。')">⚡ 水電工程</button>
                        <button class="template-btn" onclick="setTpl('全室牆面批土打磨與得利乳膠漆粉刷', '油漆', '台北市', '信義區', 'NT$ 30,000 - 55,000', '室內實際坪數 22 坪，全室牆面裂縫修補、全批土二次、打磨平整後噴塗或滾塗得利竹炭乳膠漆 2 道。')">🎨 油漆工程</button>
                        <button class="template-btn" onclick="setTpl('頂樓女兒牆與地面高分子防水工程', '防水', '桃園市', '桃園區', 'NT$ 38,000 - 65,000', '頂樓 20 坪地面素地整理、打除劣化層，施作底漆加抗裂玻璃纖維網，三道彈性水泥聚氨酯防水面漆。')">🚿 防水工程</button>
                        <button class="template-btn" onclick="setTpl('客廳與臥室更換大和賞隔音氣密窗 (共3樘)', '窗戶', '新北市', '新莊區', 'NT$ 32,000 - 50,000', '舊鋁窗老舊漏風，更換為大和賞 8mm 強化隔音氣密窗，採用乾式施工包框工法，含紗窗。')">🪟 窗戶/氣密窗</button>
                        <button class="template-btn" onclick="setTpl('日立一對二冷暖變頻冷氣安裝移機', '冷氣', '台北市', '大安區', 'NT$ 12,000 - 20,000', '搬家需拆卸原冷氣並安裝至新家客廳與主臥，含新銅管拉線、洗洞孔洞封閉、抽真空與冷媒回填。')">❄️ 冷氣空調</button>
                        <button class="template-btn" onclick="setTpl('客廳立體造型天花板與隱形暗門施作', '木工', '台中市', '西屯區', 'NT$ 45,000 - 85,000', '客廳施作日本麗仕矽酸鈣板平釘天花板、間接照明燈溝，主臥入口製作一樘隱藏暗門與木作格柵。')">🪵 木工工程</button>
                        <button class="template-btn" onclick="setTpl('主臥整面 E1 級防潮防焰系統衣櫃訂製', '系統櫃', '新竹市', '東區', 'NT$ 50,000 - 90,000', '寬度 280cm x 高 240cm 頂天立地系統衣櫃，奧地利 Blum 緩衝鉸鏈、內建抽屜 4 組與掛衣桿。')">🗄️ 系統櫃訂製</button>
                        <button class="template-btn" onclick="setTpl('新成屋 2 房 1 廳輕裝潢工程 (無拆除)', '室內裝潢', '新北市', '三重區', 'NT$ 250,000 - 450,000', '包含全室平釘天花板、SPC石塑地板直鋪、電視牆木作、全室得利乳膠漆、主臥系統衣櫃。')">🛋️ 室內裝潢</button>
                        <button class="template-btn" onclick="setTpl('預售屋客變與室內空間規劃設計 (30坪)', '室內設計', '台中市', '南屯區', 'NT$ 60,000 - 120,000', '包含全室 2D 平面配置圖、水電迴路客變圖、3D 渲染立體擬真圖 4 張、完整施工發包工程圖面。')">📐 室內設計</button>
                        <button class="template-btn" onclick="setTpl('40年老公寓全室基礎工程翻新 (28坪)', '老屋翻新', '台北市', '松山區', 'NT$ 1,200,000 - 1,800,000', '全室拆除見底、泥作隔間重砌、冷熱水不鏽鋼管與全室重配電、兩間衛浴重做、全室氣密窗更換。')">🏚️ 老屋翻新</button>
                        <button class="template-btn" onclick="setTpl('主臥衛浴乾濕分離整修與 TOTO 衛浴換新', '衛浴整修', '高雄市', '左營區', 'NT$ 85,000 - 130,000', '舊浴缸打除清運、重作試水防水層、更換乾濕分離強化玻璃拉門、TOTO 單體馬桶與面盆浴櫃。')">🛁 衛浴整修</button>
                        <button class="template-btn" onclick="setTpl('裝潢後全室細清與粉塵高溫除蟎清潔', '清潔', '新北市', '淡水區', 'NT$ 12,000 - 22,000', '全室裝修完工細清，含櫃體內部殘膠粉塵去除、窗框溝縫吸塵刷洗、地板打蠟保護、衛浴除水垢。')">🧹 清潔工程</button>
                        <button class="template-btn" onclick="setTpl('家庭 3 房 2 廳精緻包裝搬家 (含大型家具拆裝)', '搬家', '台北市', '文山區', 'NT$ 15,000 - 28,000', '大型家具防撞膠膜包覆保護、65吋電視與雙門冰箱防護拆裝、舊家 4 樓無電梯搬至新家 8 樓電梯大樓。')">📦 搬家服務</button>
                        <button class="template-btn" onclick="setTpl('中古屋 3 房 2 廳全室統包工程 (含監工)', '統包', '台南市', '東區', 'NT$ 600,000 - 950,000', '統包拆除、水電、泥作、木作天花板、油漆與木地板，需提供完整工程合約、分期驗收進度表與一年保固。')">🏗️ 統包工程</button>
                        <button class="template-btn" onclick="setTpl('大門更換電子鎖與陽台升降曬衣架安裝', '其他修繕', '新北市', '新店區', 'NT$ 5,000 - 9,000', '大門安裝指紋密碼電子鎖（含洗孔調整）、後陽台手搖雙桿不鏽鋼升降曬衣架鑽孔固定安裝。')">🛠️ 其他修繕</button>
                    </div>
                </div>

                <form action="/create-job" method="post" class="form-grid">
                    <div class="form-group">
                        <label>案件標題</label>
                        <input type="text" id="title" name="title" placeholder="例如：主臥衛浴整修更換乾濕分離" required>
                    </div>
                    <div class="form-group">
                        <label>工程工種分類</label>
                        <select id="category" name="category">
                            <option value="拆除">🔨 拆除工程</option>
                            <option value="泥作">🧱 泥作工程</option>
                            <option value="水電">⚡ 水電工程</option>
                            <option value="油漆">🎨 油漆工程</option>
                            <option value="防水">🚿 防水工程</option>
                            <option value="窗戶">🪟 窗戶/氣密窗</option>
                            <option value="冷氣">❄️ 冷氣空調</option>
                            <option value="木工">🪵 木工工程</option>
                            <option value="系統櫃">🗄️ 系統櫃訂製</option>
                            <option value="室內裝潢">🛋️ 室內裝潢</option>
                            <option value="室內設計">📐 室內設計</option>
                            <option value="老屋翻新">🏚️ 老屋翻新</option>
                            <option value="衛浴整修">🛁 衛浴整修</option>
                            <option value="清潔">🧹 清潔工程</option>
                            <option value="搬家">📦 搬家服務</option>
                            <option value="統包">🏗️ 統包工程</option>
                            <option value="其他修繕">🛠️ 其他修繕</option>
                        </select>
                    </div>

                    <div class="form-group">
                        <label>施工縣市</label>
                        <select id="city_select" name="city" onchange="onCityChange()" required>
                            <option value="新北市">新北市</option>
                            <option value="台北市">台北市</option>
                            <option value="基隆市">基隆市</option>
                            <option value="桃園市">桃園市</option>
                            <option value="新竹市">新竹市</option>
                            <option value="新竹縣">新竹縣</option>
                            <option value="苗栗縣">苗栗縣</option>
                            <option value="台中市">台中市</option>
                            <option value="彰化縣">彰化縣</option>
                            <option value="南投縣">南投縣</option>
                            <option value="雲林縣">雲林縣</option>
                            <option value="嘉義市">嘉義市</option>
                            <option value="嘉義縣">嘉義縣</option>
                            <option value="台南市">台南市</option>
                            <option value="高雄市">高雄市</option>
                            <option value="屏東縣">屏東縣</option>
                            <option value="宜蘭縣">宜蘭縣</option>
                            <option value="花蓮縣">花蓮縣</option>
                            <option value="台東縣">台東縣</option>
                            <option value="澎湖縣">澎湖縣</option>
                            <option value="金門縣">金門縣</option>
                            <option value="連江縣">連江縣</option>
                        </select>
                    </div>
                    <div class="form-group">
                        <label>行政區</label>
                        <select id="district_select" name="district" required>
                        </select>
                    </div>

                    <div class="form-group">
                        <label>預估預算</label>
                        <input type="text" id="budget_range" name="budget_range" placeholder="例如：NT$ 30,000 - 60,000" required>
                    </div>
                    <div class="form-group">
                        <label>業主聯絡姓名</label>
                        <input type="text" name="customer_name" placeholder="例如：林先生" required>
                    </div>
                    <div class="form-group full-width">
                        <label>聯絡電話 (得標師傅才可看見完整號碼)</label>
                        <input type="tel" name="customer_phone" placeholder="例如：0987-654-321" required pattern="[0-9-]+" minlength="9">
                    </div>
                    <div class="form-group full-width">
                        <label>問題詳情與現況說明</label>
                        <textarea id="description" name="description" rows="3" placeholder="請詳細描述施工坪數、樓層（有無電梯）、預計施工日期、材料需求..." required></textarea>
                    </div>
                    <div class="full-width">
                        <label class="agree-label">
                            <input type="checkbox" required checked style="width: auto;"> 我已閱讀並同意 <a href="/terms" target="_blank">服務條款</a> 與 <a href="/privacy" target="_blank">隱私權政策</a>。
                        </label>
                    </div>
                    <div class="full-width" style="margin-top: 10px;">
                        <button type="submit" class="btn-primary" style="width: 100%;">📢 免費發布裝潢/修繕需求</button>
                    </div>
                </form>
            </div>

            <!-- 師傅篩選與案件列表 -->
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap;">
                <h2>📋 最新發布案件 ({len(jobs)})</h2>
                <form method="get" class="filter-bar" style="margin-bottom: 0;">
                    <span style="font-size: 13px; color: var(--text-muted);">師傅篩選：</span>
                    <select name="filter_city" style="width: 120px; padding: 6px;" onchange="this.form.submit()">
                        <option value="全部地區">全部地區</option>
                        <option value="台北市" {'selected' if filter_city=='台北市' else ''}>台北市</option>
                        <option value="新北市" {'selected' if filter_city=='新北市' else ''}>新北市</option>
                        <option value="基隆市" {'selected' if filter_city=='基隆市' else ''}>基隆市</option>
                        <option value="桃園市" {'selected' if filter_city=='桃園市' else ''}>桃園市</option>
                        <option value="新竹" {'selected' if filter_city=='新竹' else ''}>新竹縣市</option>
                        <option value="台中市" {'selected' if filter_city=='台中市' else ''}>台中市</option>
                        <option value="台南市" {'selected' if filter_city=='台南市' else ''}>台南市</option>
                        <option value="高雄市" {'selected' if filter_city=='高雄市' else ''}>高雄市</option>
                    </select>
                    <select name="filter_cat" style="width: 120px; padding: 6px;" onchange="this.form.submit()">
                        <option value="全部工種">全部工種</option>
                        <option value="拆除" {'selected' if filter_cat=='拆除' else ''}>拆除</option>
                        <option value="泥作" {'selected' if filter_cat=='泥作' else ''}>泥作</option>
                        <option value="水電" {'selected' if filter_cat=='水電' else ''}>水電</option>
                        <option value="油漆" {'selected' if filter_cat=='油漆' else ''}>油漆</option>
                        <option value="防水" {'selected' if filter_cat=='防水' else ''}>防水</option>
                        <option value="冷氣" {'selected' if filter_cat=='冷氣' else ''}>冷氣</option>
                        <option value="衛浴整修" {'selected' if filter_cat=='衛浴整修' else ''}>衛浴整修</option>
                        <option value="統包" {'selected' if filter_cat=='統包' else ''}>統包</option>
                    </select>
                </form>
            </div>

            <div style="margin-top: 15px;">
                {jobs_html if jobs_html else '<p class="text-muted">目前暫無符合條件的案件，請透過上方表單刊登第一筆需求！</p>'}
            </div>

            <!-- 頁尾合規連結 -->
            <footer>
                <p>© 2026 QT30 專業修繕媒合平台. All Rights Reserved.</p>
                <div style="margin-top: 8px;">
                    <a href="/terms">服務條款</a> |
                    <a href="/privacy">隱私權政策</a> |
                    <a href="/disclaimer">免責聲明與交易安全</a>
                </div>
            </footer>
        </div>

        <script>
            const taiwanDistricts = {{
                "台北市": ["中正區", "大同區", "中山區", "松山區", "大安區", "萬華區", "信義區", "士林區", "北投區", "內湖區", "南港區", "文山區"],
                "新北市": ["板橋區", "三重區", "中和區", "永和區", "新莊區", "新店區", "樹林區", "鶯歌區", "三峽區", "淡水區", "汐止區", "瑞芳區", "土城區", "蘆洲區", "五股區", "泰山區", "林口區", "深坑區", "石碇區", "坪林區", "三芝區", "石門區", "八里區", "平溪區", "雙溪區", "貢寮區", "金山區", "萬里區", "烏來區"],
                "基隆市": ["仁愛區", "信義區", "中正區", "中山區", "安樂區", "暖暖區", "七堵區"],
                "桃園市": ["桃園區", "中壢區", "大溪區", "楊梅區", "蘆竹區", "大園區", "龜山區", "八德區", "龍潭區", "平鎮區", "新屋區", "觀音區", "復興區"],
                "新竹市": ["東區", "北區", "香山區"],
                "新竹縣": ["竹北市", "竹東鎮", "新埔鎮", "關西鎮", "湖口鄉", "新豐鄉", "芎林鄉", "橫山鄉", "北埔鄉", "寶山鄉", "峨眉鄉", "尖石鄉", "五峰鄉"],
                "苗栗縣": ["苗栗市", "頭份市", "竹南鎮", "後龍鎮", "通霄鎮", "苑裡鎮", "卓蘭鎮", "造橋鄉", "西湖鄉", "頭屋鄉", "公館鄉", "銅鑼鄉", "三義鄉", "大湖鄉", "獅潭鄉", "三灣鄉", "南庄鄉", "泰安鄉"],
                "台中市": ["中區", "東區", "南區", "西區", "北區", "北屯區", "西屯區", "南屯區", "太平區", "大里區", "霧峰區", "烏日區", "豐原區", "后里區", "石岡區", "東勢區", "和平區", "新社區", "潭子區", "大雅區", "神岡區", "大肚區", "沙鹿區", "龍井區", "梧棲區", "清水區", "大甲區", "外埔區", "大安區"],
                "彰化縣": ["彰化市", "員林市", "和美鎮", "鹿港鎮", "溪湖鎮", "二林鎮", "田中鎮", "北斗鎮", "花壇鄉", "芬園鄉", "大村鄉", "永靖鄉", "伸港鄉", "線西鄉", "福興鄉", "秀水鄉", "埔心鄉", "埔鹽鄉", "大城鄉", "芳苑鄉", "竹塘鄉", "社頭鄉", "二水鄉", "田尾鄉", "埤頭鄉", "溪州鄉"],
                "南投縣": ["南投市", "埔里鎮", "草屯鎮", "竹山鎮", "集集鎮", "名間鄉", "鹿谷鄉", "中寮鄉", "魚池鄉", "國姓鄉", "水里鄉", "信義鄉", "仁愛鄉"],
                "雲林縣": ["斗六市", "斗南鎮", "虎尾鎮", "西螺鎮", "土庫鎮", "北港鎮", "古坑鄉", "大埤鄉", "莿桐鄉", "林內鄉", "二崙鄉", "崙背鄉", "麥寮鄉", "東勢鄉", "褒忠鄉", "台西鄉", "元長鄉", "四湖鄉", "口湖鄉", "水林鄉"],
                "嘉義市": ["東區", "西區"],
                "嘉義縣": ["太保市", "朴子市", "布袋鎮", "大林鎮", "民雄鄉", "溪口鄉", "新港鄉", "六腳鄉", "東石鄉", "義竹鄉", "鹿草鄉", "水上鄉", "中埔鄉", "竹崎鄉", "梅山鄉", "番路鄉", "大埔鄉", "阿里山鄉"],
                "台南市": ["中西區", "東區", "南區", "北區", "安平區", "安南區", "永康區", "歸仁區", "新化區", "左鎮區", "玉井區", "楠西區", "南化區", "仁德區", "關廟區", "龍崎區", "官田區", "麻豆區", "佳里區", "西港區", "七股區", "將軍區", "學甲區", "北門區", "新營區", "後壁區", "白河區", "東山區", "六甲區", "下營區", "柳營區", "鹽水區", "善化區", "大內區", "山上區", "新市區", "安定區"],
                "高雄市": ["新興區", "前金區", "苓雅區", "鹽埕區", "鼓山區", "旗津區", "前鎮區", "三民區", "楠梓區", "小港區", "左營區", "仁武區", "大社區", "岡山區", "路竹區", "阿蓮區", "田寮區", "燕巢區", "橋頭區", "梓官區", "彌陀區", "永安區", "湖內區", "鳳山區", "大寮區", "林園區", "鳥松區", "大樹區", "旗山區", "美濃區", "六龜區", "內門區", "杉林區", "甲仙區", "桃源區", "那瑪夏區", "茂林區", "茄萣區"],
                "屏東縣": ["屏東市", "潮州鎮", "東港鎮", "恆春鎮", "萬丹鄉", "長治鄉", "麟洛鄉", "九如鄉", "里港鄉", "鹽埔鄉", "高樹鄉", "萬巒鄉", "內埔鄉", "竹田鄉", "新埤鄉", "枋寮鄉", "新園鄉", "崁頂鄉", "林邊鄉", "南州鄉", "佳冬鄉", "琉球鄉", "車城鄉", "滿州鄉", "枋山鄉", "三地門鄉", "霧台鄉", "瑪家鄉", "泰武鄉", "來義鄉", "春日鄉", "獅子鄉", "牡丹鄉"],
                "宜蘭縣": ["宜蘭市", "羅東鎮", "蘇澳鎮", "頭城鎮", "礁溪鄉", "壯圍鄉", "員山鄉", "冬山鄉", "五結鄉", "三星鄉", "大同鄉", "南澳鄉"],
                "花蓮縣": ["花蓮市", "鳳林鎮", "玉里鎮", "新城鄉", "吉安鄉", "壽豐鄉", "光復鄉", "豐濱鄉", "瑞穗鄉", "富里鄉", "秀林鄉", "萬榮鄉", "卓溪鄉"],
                "台東縣": ["台東市", "成功鎮", "關山鎮", "卑南鄉", "大武鄉", "太麻里鄉", "東河鄉", "長濱鄉", "鹿野鄉", "池上鄉", "綠島鄉", "延平鄉", "海端鄉", "達仁鄉", "金峰鄉", "蘭嶼鄉"],
                "澎湖縣": ["馬公市", "湖西鄉", "白沙鄉", "西嶼鄉", "望安鄉", "七美鄉"],
                "金門縣": ["金城鎮", "金湖鎮", "金沙鎮", "金寧鄉", "烈嶼鄉", "烏坵鄉"],
                "連江縣": ["南竿鄉", "北竿鄉", "莒光鄉", "東引鄉"]
            }};

            function onCityChange(defaultDistrict = null) {{
                const city = document.getElementById('city_select').value;
                const distSelect = document.getElementById('district_select');
                distSelect.innerHTML = '';
                const districts = taiwanDistricts[city] || [];
                districts.forEach(d => {{
                    const opt = document.createElement('option');
                    opt.value = d;
                    opt.textContent = d;
                    if(defaultDistrict && d === defaultDistrict) opt.selected = true;
                    distSelect.appendChild(opt);
                }});
            }}

            function setTpl(t, c, city, dist, b, d) {{
                document.getElementById('title').value = t;
                document.getElementById('category').value = c;
                document.getElementById('city_select').value = city;
                onCityChange(dist);
                document.getElementById('budget_range').value = b;
                document.getElementById('description').value = d;
            }}

            window.onload = function() {{
                onCityChange("淡水區");
            }};
        </script>
    </body>
    </html>
    """
    return html

# 廠商/師傅公開履歷與得標案例庫頁面
@app.get("/master/{master_id}", response_class=HTMLResponse)
async def master_profile_page(master_id: int, db: Session = Depends(get_db)):
    master = db.query(Master).filter(Master.id == master_id).first()
    if not master:
        raise HTTPException(status_code=404, detail="找不到該廠商資料")

    # 撈取該師傅所有得標過的案件與明細
    awarded_quotes = db.query(Quote).filter(Quote.master_id == master.id, Quote.is_awarded == True).all()

    # 撈取該師傅所有收到的評價
    reviews = []
    for q in awarded_quotes:
        if q.job.reviews:
            reviews.extend(q.job.reviews)

    awarded_cards_html = ""
    for aq in awarded_quotes:
        job = aq.job
        review_for_job = next((r for r in job.reviews), None)
        review_box = f"""
        <div style="background: #1e1b4b; border-left: 3px solid #fbbf24; padding: 10px; border-radius: 6px; margin-top: 10px; font-size: 13px;">
            <div><strong>{review_for_job.customer_name}</strong> 評價：{'⭐' * review_for_job.rating}</div>
            <p style="color: #c7d2fe; margin-top: 4px;">「{review_for_job.comment}」</p>
        </div>
        """ if review_for_job else '<p style="font-size: 12px; color: var(--text-muted); margin-top: 6px;">⏳ 施工驗收中，待業主評分</p>'

        awarded_cards_html += f"""
        <div class="card" style="background: #0f172a; border-color: #3b82f6;">
            <div style="display: flex; justify-content: space-between; align-items: flex-start; flex-wrap: wrap; gap: 8px;">
                <div>
                    <h3 style="color: #e2e8f0; font-size: 17px;">🏆 {job.title}</h3>
                    <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px; display: flex; gap: 10px;">
                        <span>📍 {job.location}</span>
                        <span>🏷️ {job.category}</span>
                        <span>🕒 結案時間：{aq.created_at.strftime('%Y-%m-%d')}</span>
                    </div>
                </div>
                <div style="font-size: 20px; font-weight: 800; color: #34d399;">得標金額：NT$ {aq.amount:,}</div>
            </div>
            
            <div style="background: #1e293b; padding: 12px; border-radius: 8px; margin: 12px 0; font-size: 13px; line-height: 1.6;">
                <p><strong>🛠️ 得標工項與工料明細：</strong>{aq.breakdown}</p>
                <p><strong>⏱️ 完工工期：</strong>{aq.days_required}</p>
            </div>

            <!-- 案主真實評價反饋 -->
            {review_box}
        </div>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <title>{master.name} - 專業廠商履歷與得標案例庫</title>
        <style>{COMMON_CSS}
            .profile-header {{ display: flex; gap: 20px; align-items: center; flex-wrap: wrap; }}
            .avatar {{ width: 80px; height: 80px; border-radius: 50%; background: #2563eb; display: flex; align-items: center; justify-content: center; font-size: 36px; }}
            .stat-box {{ display: flex; gap: 15px; margin-top: 15px; }}
            .stat-item {{ background: #0f172a; border: 1px solid var(--border); padding: 10px 18px; border-radius: 8px; text-align: center; }}
            .stat-num {{ font-size: 20px; font-weight: 800; color: #60a5fa; }}
            .stat-title {{ font-size: 12px; color: var(--text-muted); }}
        </style>
    </head>
    <body>
        <div class="container">
            <header>
                <a href="/" class="brand">⚡ QT30 專業裝潢修繕媒合</a>
                <a href="/" class="btn-primary" style="padding: 6px 14px; font-size: 13px;">回媒合大廳</a>
            </header>

            <!-- 廠商主履歷卡片 -->
            <div class="card" style="border-top: 4px solid #3b82f6;">
                <div class="profile-header">
                    <div class="avatar">👷</div>
                    <div>
                        <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
                            <h2 style="font-size: 22px;">{master.name}</h2>
                            <span class="badge-verify">🛡️ 實名核實合格</span>
                            <span class="badge-license">📜 {master.license_type}</span>
                        </div>
                        <p style="color: #94a3b8; font-size: 13px; margin-top: 4px;">📞 官方派工電話：{master.phone}</p>
                    </div>
                </div>

                <div class="stat-box">
                    <div class="stat-item">
                        <div class="stat-num">⭐ {master.rating}</div>
                        <div class="stat-title">綜合滿意度</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-num">{len(awarded_quotes)} 件</div>
                        <div class="stat-title">歷史得標完工</div>
                    </div>
                    <div class="stat-item">
                        <div class="stat-num">{len(reviews)} 則</div>
                        <div class="stat-title">業主真實好評</div>
                    </div>
                </div>

                <div style="margin-top: 20px; background: #0f172a; padding: 15px; border-radius: 8px; border: 1px solid var(--border);">
                    <h4 style="color: #93c5fd; margin-bottom: 6px;">🏢 廠商簡介與施工理念</h4>
                    <p style="font-size: 14px; color: #cbd5e1; line-height: 1.6;">{master.bio}</p>
                </div>
            </div>

            <!-- 公開得標歷史與案例明細 -->
            <h2 style="margin-bottom: 15px;">📜 公開得標案例庫與工項明細 ({len(awarded_quotes)})</h2>
            {awarded_cards_html if awarded_cards_html else '<div class="card"><p class="text-muted">尚無得標紀錄，得標後將自動收錄於此！</p></div>'}

            <footer>
                <p>© 2026 QT30 專業修繕媒合平台. All Rights Reserved.</p>
            </footer>
        </div>
    </body>
    </html>
    """
    return html

# 法律條款獨立頁面
@app.get("/terms", response_class=HTMLResponse)
async def terms_page():
    return f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <title>服務條款 - QT30 專業修繕媒合平台</title>
        <style>{COMMON_CSS} .card {{ line-height: 1.8; }} h3 {{ color: #93c5fd; margin: 15px 0 5px; }}</style>
    </head>
    <body>
        <div class="container">
            <header>
                <a href="/" class="brand">⚡ QT30 專業裝潢修繕媒合</a>
                <a href="/" class="btn-primary" style="padding: 6px 14px; font-size: 13px;">回首頁</a>
            </header>
            <div class="card">
                <h2>📜 QT30 平台服務條款（Terms of Service）</h2>
                <p style="color: var(--text-muted); font-size: 13px;">最後更新日期：2026 年 8 月</p>
                <hr style="border: 0; border-top: 1px solid var(--border); margin: 15px 0;">

                <h3>第一條：平台性質與居間角色</h3>
                <p>QT30 修繕平台（以下稱「本平台」）係提供業主（需求發案方）與專業裝修師傅/廠商（承攬報價方）之資訊交流與媒合撮合服務平台。本平台並非工程之承攬人、代理人或委託人，不介入雙方實質工程契約之訂定、履行或驗收。</p>

                <h3>第二條：發案與報價規範</h3>
                <p>1. 業主發布需求應據實填寫現況、預算與聯絡資訊，不得發布虛假、詐欺或違反公共秩序之內容。<br>
                2. 師傅送出之報價、工期與材料明細應具專業信實，得標後應依誠信原則與業主接洽簽約施工。</p>

                <h3>第三條：點數儲值與扣點機制</h3>
                <p>師傅參與報價消耗之平台點數，屬資訊媒合服務費用。一旦點數扣除並送出報價，除系統不可抗力錯誤外，不得要求返還點數。</p>

                <h3>第四條：爭議處理與準據法</h3>
                <p>本服務條款之解釋與適用，悉依中華民國法律為準據法。因工程承攬所生之一切履約糾紛，由業主與施工廠商自行依法協商解決。</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.get("/privacy", response_class=HTMLResponse)
async def privacy_page():
    return f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <title>隱私權政策 - QT30 專業修繕媒合平台</title>
        <style>{COMMON_CSS} .card {{ line-height: 1.8; }} h3 {{ color: #93c5fd; margin: 15px 0 5px; }}</style>
    </head>
    <body>
        <div class="container">
            <header>
                <a href="/" class="brand">⚡ QT30 專業裝潢修繕媒合</a>
                <a href="/" class="btn-primary" style="padding: 6px 14px; font-size: 13px;">回首頁</a>
            </header>
            <div class="card">
                <h2>🔒 QT30 隱私權保護政策（Privacy Policy）</h2>
                <p style="color: var(--text-muted); font-size: 13px;">最後更新日期：2026 年 8 月</p>
                <hr style="border: 0; border-top: 1px solid var(--border); margin: 15px 0;">

                <h3>一、個人資料之蒐集目的</h3>
                <p>本平台依據《個人資料保護法》蒐集業主與廠商之姓名、聯絡電話、施工地址與評價資訊，法定特定目的為「契約、類似契約或其他法律關係事務」及「消費者、客戶管理與服務」。</p>

                <h3>二、電話號碼遮蔽與安全機制</h3>
                <p>為保障業主隱私，所有公開案件列表中業主電話均經過字元遮蔽（如：0987***321）。僅在業主明確點選「採納此師傅報價」後，系統始對該名得標師傅揭露完整電話以利後續工勘聯繫。</p>

                <h3>三、資料保密與第三方共用</h3>
                <p>除司法機關依法調閱或工程派工必要外，本平台絕不將您的個人資料販售、交換或出租予任何無關之第三方。</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.get("/disclaimer", response_class=HTMLResponse)
async def disclaimer_page():
    return f"""
    <!DOCTYPE html>
    <html lang="zh-TW">
    <head>
        <meta charset="UTF-8">
        <title>免責聲明 - QT30 專業修繕媒合平台</title>
        <style>{COMMON_CSS} .card {{ line-height: 1.8; }} h3 {{ color: #93c5fd; margin: 15px 0 5px; }}</style>
    </head>
    <body>
        <div class="container">
            <header>
                <a href="/" class="brand">⚡ QT30 專業裝潢修繕媒合</a>
                <a href="/" class="btn-primary" style="padding: 6px 14px; font-size: 13px;">回首頁</a>
            </header>
            <div class="card">
                <h2>⚠️ 免責聲明與交易安全宣導</h2>
                <p style="color: var(--text-muted); font-size: 13px;">最後更新日期：2026 年 8 月</p>
                <hr style="border: 0; border-top: 1px solid var(--border); margin: 15px 0;">

                <h3>1. 工程承攬安全提醒</h3>
                <p>建議業主於施工前，務必與施工師傅簽訂內政部頒布之《住宅套房裝修工程標準定型化契約》，並約定分期付款（訂金、各期進度款、尾款驗收），切勿於未開工前全額預付工程款。</p>

                <h3>2. 證照與身分核對</h3>
                <p>平台之實名標籤係由廠商主動提供並由系統初步核對，業主於現場簽約時，仍得要求師傅出示身分證件或專業技術士證照以供確認。</p>
            </div>
        </div>
    </body>
    </html>
    """

@app.post("/create-job")
async def create_job(
    title: str = Form(...),
    category: str = Form(...),
    city: str = Form(...),
    district: str = Form(...),
    budget_range: str = Form(...),
    customer_name: str = Form(...),
    customer_phone: str = Form(...),
    description: str = Form(...),
    db: Session = Depends(get_db)
):
    full_location = f"{city}{district}"
    job = Job(
        title=title,
        category=category,
        city=city,
        district=district,
        location=full_location,
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
    job = db.query(Job).filter(Job.id == job_id).first()
    awarded_quote = next((q for q in job.quotes if q.is_awarded), None)
    master_id = awarded_quote.master_id if awarded_quote else None

    review = Review(
        job_id=job_id,
        master_id=master_id,
        customer_name=customer_name,
        rating=rating,
        comment=comment
    )
    db.add(review)

    # 動態重新計算師傅平均星級
    if master_id:
        master = db.query(Master).filter(Master.id == master_id).first()
        if master:
            all_master_reviews = db.query(Review).filter(Review.master_id == master.id).all()
            total_ratings = sum(r.rating for r in all_master_reviews) + rating
            master.review_count = len(all_master_reviews) + 1
            master.rating = round(total_ratings / master.review_count, 1)

    db.commit()
    return RedirectResponse(url="/", status_code=303)
