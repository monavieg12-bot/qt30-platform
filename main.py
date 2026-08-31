import os
import uuid
import json
import shutil
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Request, Depends, Response, Form, UploadFile, File
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from database import SessionLocal, init_db, ExpertModel, DemandModel
from ecpay_sdk import ECPayPaymentSDK

UPLOAD_DIR = "uploads"
os.makedirs(UPLOAD_DIR, exist_ok=True)

init_db()

app = FastAPI(title="QT30社區修繕達人網 API (含牌照審核管理)", version="2.1.0")
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")
ecpay_sdk = ECPayPaymentSDK()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# 1. 資料傳輸模型
# ==========================================

class PurchaseLeadRequest(BaseModel):
    demand_id: str
    expert_id: str

class VerifyExpertRequest(BaseModel):
    expert_id: str
    action: str  # "approve" 或 "reject"

class QuoteItem(BaseModel):
    item: str
    unit: str
    qty: int
    price: int
    note: Optional[str] = ""

class CreateQuoteRequest(BaseModel):
    demand_id: str
    expert_id: str
    items: List[QuoteItem]

def calculate_dynamic_price(base_price: int, rating: float, dispute_rate: float, competitor_count: int) -> int:
    rating_mult = 0.8 if rating >= 4.8 else 1.0 if rating >= 4.0 else 1.5
    comp_mult = 1.0 if competitor_count == 0 else 0.95 if competitor_count == 1 else 0.90
    dispute_mult = 1.2 if dispute_rate > 0.05 else 1.0
    return max(50, int(base_price * rating_mult * comp_mult * dispute_mult))

MOCK_MARKET_PRICES = [
    {
        "id": "price_1",
        "category": "房屋修繕",
        "title": "30年老屋衛浴防水管線翻新",
        "region": "台北市大安區",
        "budget": 180000,
        "supervision_fee": 14400,
        "total": 194400,
        "has_supervision": True,
        "details": [
            {"item": "舊有壁磚地磚打除至紅磚層清運", "unit": "間", "qty": 1, "price": 35000, "note": "含廢棄物清運"},
            {"item": "彈性水泥防水層塗刷三道 (試水48小時)", "unit": "式", "qty": 1, "price": 28000, "note": "南星牌彈泥"},
            {"item": "冷熱不鏽鋼水管抽換配置", "unit": "式", "qty": 1, "price": 22000, "note": "壓接不鏽鋼管"},
            {"item": "國產防滑地磚與壁磚鋪貼", "unit": "坪", "qty": 6, "price": 6500, "note": "含填縫"},
            {"item": "TOTO 乾濕分離衛浴設備安裝", "unit": "套", "qty": 1, "price": 56000, "note": "含淋浴拉門"}
        ]
    }
]

# ==========================================
# 2. 前端介面 (含牌照認證與管理審核模組)
# ==========================================

HTML_CONTENT = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>QT30社區修繕達人網 - 台灣修繕實價登錄 ✕ 免費發案</title>
    <link rel="canonical" href="http://127.0.0.1:8000/">
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-50 text-slate-800 p-6">
    <div class="max-w-6xl mx-auto">
        <header class="mb-6 border-b pb-4 flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
            <div>
                <h1 class="text-2xl font-bold text-sky-700">🏠 QT30社區修繕達人網</h1>
                <p class="text-sm text-slate-500">透明實價行情 ✕ 專業牌照認證 ✕ 現場照片直覺媒合</p>
            </div>
            <div class="flex items-center gap-3">
                <button onclick="openLicenseModal()" class="px-3 py-2 bg-indigo-600 hover:bg-indigo-700 text-white rounded-xl text-xs font-bold shadow-sm transition">
                    📜 牌照認證申請
                </button>
                <button onclick="openTopupModal()" class="px-3 py-2 bg-emerald-600 hover:bg-emerald-700 text-white rounded-xl text-xs font-bold shadow-sm transition">
                    💳 線上儲值點數
                </button>
                <div class="flex bg-slate-200 p-1 rounded-xl text-sm font-semibold">
                    <button onclick="switchTab('main')" id="tabMainBtn" class="px-4 py-2 rounded-lg bg-white shadow-sm text-sky-700 transition">
                        發案與接案大廳
                    </button>
                    <button onclick="switchTab('market')" id="tabMarketBtn" class="px-4 py-2 rounded-lg text-slate-600 hover:text-sky-700 transition">
                        📈 實價登錄報價牆
                    </button>
                </div>
            </div>
        </header>

        <!-- 管理員快速審核測試列 -->
        <div id="adminBar" class="mb-6 p-4 bg-slate-800 text-white rounded-xl flex flex-col sm:flex-row justify-between items-start sm:items-center gap-3 text-xs shadow-md">
            <div>
                <span class="font-bold text-amber-400">🛡️ 平台管理員快速審核面板：</span>
                <span id="adminExpertInfo">載入審核狀態中...</span>
            </div>
            <div class="flex gap-2">
                <button onclick="verifyExpert('approve')" class="px-3 py-1.5 bg-emerald-500 hover:bg-emerald-600 font-bold rounded-lg transition">
                    ✓ 通過牌照審核
                </button>
                <button onclick="verifyExpert('reject')" class="px-3 py-1.5 bg-rose-500 hover:bg-rose-600 font-bold rounded-lg transition">
                    ✗ 駁回審核
                </button>
            </div>
        </div>

        <!-- 分頁一：發案與接案大廳 -->
        <div id="pageMain" class="grid grid-cols-1 md:grid-cols-2 gap-8">
            <div class="bg-white p-6 rounded-xl border border-slate-200 shadow-sm">
                <h2 class="text-lg font-bold text-slate-800 mb-4">✍️ 消費者免費發布需求</h2>
                <form id="demandForm" class="space-y-4">
                    <div>
                        <label class="block text-sm font-medium mb-1">修繕/搬家項目名稱</label>
                        <input id="title" class="w-full border rounded-lg p-2 text-sm" placeholder="例如：衛浴防水抓漏翻修" required>
                    </div>
                    <div class="grid grid-cols-2 gap-4">
                        <div>
                            <label class="block text-sm font-medium mb-1">服務分類</label>
                            <select id="category" class="w-full border rounded-lg p-2 text-sm">
                                <option value="房屋修繕">房屋修繕</option>
                                <option value="搬家服務">搬家服務</option>
                            </select>
                        </div>
                        <div>
                            <label class="block text-sm font-medium mb-1">服務地區</label>
                            <input id="region" class="w-full border rounded-lg p-2 text-sm" placeholder="例如：台北市信義區" required>
                        </div>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">施工門牌 (成交前對外遮蔽)</label>
                        <input id="detailed_address" class="w-full border rounded-lg p-2 text-sm" placeholder="例如：信義路四段100號5樓" required>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">工程預算 (TWD)</label>
                        <input id="budget" type="number" class="w-full border rounded-lg p-2 text-sm" placeholder="例如：180000" required>
                    </div>
                    <div>
                        <label class="block text-sm font-medium mb-1">📸 上傳現場照片 (可多選)</label>
                        <input id="photos" type="file" multiple accept="image/*" class="w-full border rounded-lg p-2 text-xs text-slate-500 file:mr-2 file:py-1 file:px-3 file:rounded-md file:border-0 file:text-xs file:font-semibold file:bg-sky-50 file:text-sky-700 hover:file:bg-sky-100">
                    </div>
                    <div class="p-3 bg-sky-50 rounded-lg border border-sky-100">
                        <label class="flex items-center space-x-2 text-sm font-medium text-sky-900">
                            <input id="has_supervision" type="checkbox" class="rounded text-sky-600">
                            <span>加購 8% 專業合格監工服務</span>
                        </label>
                        <p id="feeEstimate" class="text-xs font-semibold text-orange-600 mt-2 hidden"></p>
                    </div>
                    <button id="submitBtn" type="submit" class="w-full bg-orange-500 hover:bg-orange-600 text-white font-bold py-2.5 rounded-lg transition shadow-md">
                        免費送出並開始媒合
                    </button>
                </form>
            </div>

            <div class="space-y-4">
                <div class="flex justify-between items-center">
                    <h2 class="text-lg font-bold text-slate-800">📋 專家接案大廳</h2>
                    <span id="expertStatus" class="text-xs text-slate-500 font-medium">載入專家資料中...</span>
                </div>
                <div id="demandList" class="space-y-3">
                    <div class="text-center py-6 text-slate-400 text-sm">載入中...</div>
                </div>
            </div>
        </div>

        <!-- 分頁二：實價登錄行情牆 -->
        <div id="pageMarket" class="hidden space-y-6">
            <div class="bg-gradient-to-r from-sky-600 to-indigo-700 text-white p-6 rounded-2xl shadow-md flex justify-between items-center">
                <div>
                    <h2 class="text-xl font-bold">🔍 QT30社區修繕達人網「去識別化」真實估價單行情牆</h2>
                    <p class="text-xs text-sky-100 mt-1">公開透明工料單價，工項清晰可查！</p>
                </div>
                <button onclick="switchTab('main')" class="bg-white text-sky-700 font-bold px-4 py-2 rounded-xl text-xs shadow hover:bg-sky-50 transition">
                    + 我也要免費發案
                </button>
            </div>
            <div id="marketPriceList" class="space-y-4"></div>
        </div>

        <!-- 牌照認證申請彈窗 -->
        <div id="licenseModal" class="fixed inset-0 bg-slate-900/50 backdrop-blur-sm hidden flex items-center justify-center p-4 z-50">
            <div class="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl">
                <div class="flex justify-between items-center border-b pb-3 mb-4">
                    <h3 class="text-lg font-bold text-slate-800">📜 專家裝修牌照/證照認證</h3>
                    <button onclick="closeLicenseModal()" class="text-slate-400 hover:text-slate-600 text-xl font-bold">&times;</button>
                </div>
                <p class="text-xs text-slate-500 mb-4">通過認證後將獲得「合格認證」徽章，並具備解鎖「8% 監工案件」接案權限。</p>

                <form id="licenseForm" class="space-y-3">
                    <div>
                        <label class="block text-xs font-bold text-slate-700 mb-1">牌照或技術士證號</label>
                        <input id="licenseNumber" class="w-full border rounded-lg p-2 text-xs" placeholder="例如：內營室技字第 40E1234567 號" required>
                    </div>
                    <div>
                        <label class="block text-xs font-bold text-slate-700 mb-1">證書照片或掃描檔</label>
                        <input id="licenseFile" type="file" accept="image/*" class="w-full border rounded-lg p-2 text-xs" required>
                    </div>
                    <div class="pt-4 flex justify-end gap-3">
                        <button type="button" onclick="closeLicenseModal()" class="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-lg">取消</button>
                        <button type="submit" class="px-5 py-2 text-sm font-bold bg-indigo-600 hover:bg-indigo-700 text-white rounded-lg transition shadow-md">
                            提交審核
                        </button>
                    </div>
                </form>
            </div>
        </div>

        <!-- 估價單彈窗 -->
        <div id="quoteModal" class="fixed inset-0 bg-slate-900/50 backdrop-blur-sm hidden flex items-center justify-center p-4 z-50">
            <div class="bg-white rounded-2xl max-w-2xl w-full p-6 shadow-xl max-h-[90vh] overflow-y-auto">
                <div class="flex justify-between items-center border-b pb-3 mb-4">
                    <h3 class="text-lg font-bold text-slate-800">📝 開立制式線上估價單</h3>
                    <button onclick="closeQuoteModal()" class="text-slate-400 hover:text-slate-600 text-xl font-bold">&times;</button>
                </div>
                <div id="modalDemandInfo" class="mb-4 p-3 bg-slate-50 rounded-lg text-xs text-slate-600"></div>

                <div class="space-y-3 mb-4">
                    <div class="flex justify-between items-center">
                        <label class="text-sm font-bold text-slate-700">施作工項明細</label>
                        <button type="button" onclick="addQuoteRow()" class="text-xs text-sky-600 font-bold hover:underline">+ 新增工項</button>
                    </div>
                    <div id="quoteItemsContainer" class="space-y-2"></div>
                </div>

                <div class="border-t pt-3 flex justify-between items-center mb-6">
                    <span class="text-sm font-semibold text-slate-600">工程款合計</span>
                    <span id="quoteTotalDisplay" class="text-xl font-black text-sky-700">TWD $0</span>
                </div>

                <div class="flex justify-end gap-3">
                    <button type="button" onclick="closeQuoteModal()" class="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-lg">取消</button>
                    <button id="quoteSubmitBtn" type="button" onclick="submitQuote()" class="px-5 py-2 text-sm font-bold bg-sky-600 hover:bg-sky-700 text-white rounded-lg transition shadow-md">
                        送出估價單 (並同步至實價牆)
                    </button>
                </div>
            </div>
        </div>

        <!-- 綠界金流儲值彈窗 -->
        <div id="topupModal" class="fixed inset-0 bg-slate-900/50 backdrop-blur-sm hidden flex items-center justify-center p-4 z-50">
            <div class="bg-white rounded-2xl max-w-md w-full p-6 shadow-xl">
                <div class="flex justify-between items-center border-b pb-3 mb-4">
                    <h3 class="text-lg font-bold text-slate-800">💳 綠界科技線上儲值點數</h3>
                    <button onclick="closeTopupModal()" class="text-slate-400 hover:text-slate-600 text-xl font-bold">&times;</button>
                </div>
                <form id="topupForm" action="/api/v1/payments/create" method="POST" target="_blank" class="space-y-3">
                    <input type="hidden" name="expert_id" value="exp_1">
                    <label class="block p-3 border rounded-xl cursor-pointer hover:border-emerald-500 bg-slate-50">
                        <div class="flex items-center justify-between">
                            <div>
                                <input type="radio" name="package" value="500" checked class="mr-2 text-emerald-600">
                                <span class="font-bold text-sm">體驗方案</span>
                            </div>
                            <span class="text-emerald-600 font-bold text-sm">NT$ 500 (500 點)</span>
                        </div>
                    </label>
                    <label class="block p-3 border rounded-xl cursor-pointer hover:border-emerald-500 bg-slate-50">
                        <div class="flex items-center justify-between">
                            <div>
                                <input type="radio" name="package" value="1000" class="mr-2 text-emerald-600">
                                <span class="font-bold text-sm">超值方案 (贈10%)</span>
                            </div>
                            <span class="text-emerald-600 font-bold text-sm">NT$ 1,000 (1,100 點)</span>
                        </div>
                    </label>
                    <div class="pt-4 flex justify-end gap-3">
                        <button type="button" onclick="closeTopupModal()" class="px-4 py-2 text-sm text-slate-600 hover:bg-slate-100 rounded-lg">取消</button>
                        <button type="submit" class="px-5 py-2 text-sm font-bold bg-emerald-600 hover:bg-emerald-700 text-white rounded-lg transition shadow-md">
                            前往綠界刷卡
                        </button>
                    </div>
                </form>
            </div>
        </div>
    </div>

    <script>
        const EXPERT_ID = "exp_1";
        let currentQuoteDemandId = null;

        function switchTab(tab) {
            const pageMain = document.getElementById('pageMain');
            const pageMarket = document.getElementById('pageMarket');
            const btnMain = document.getElementById('tabMainBtn');
            const btnMarket = document.getElementById('tabMarketBtn');

            if (tab === 'main') {
                pageMain.classList.remove('hidden');
                pageMarket.classList.add('hidden');
                btnMain.className = "px-4 py-2 rounded-lg bg-white shadow-sm text-sky-700 transition";
                btnMarket.className = "px-4 py-2 rounded-lg text-slate-600 hover:text-sky-700 transition";
            } else {
                pageMain.classList.add('hidden');
                pageMarket.classList.remove('hidden');
                btnMarket.className = "px-4 py-2 rounded-lg bg-white shadow-sm text-sky-700 transition";
                btnMain.className = "px-4 py-2 rounded-lg text-slate-600 hover:text-sky-700 transition";
                loadMarketPrices();
            }
        }

        const budgetInput = document.getElementById('budget');
        const supCheck = document.getElementById('has_supervision');
        const feeEstimate = document.getElementById('feeEstimate');

        function updateEstimate() {
            const b = parseInt(budgetInput.value) || 0;
            if (supCheck.checked && b > 0) {
                const sup = Math.floor(b * 0.08);
                feeEstimate.textContent = "預估 8% 監工費：$" + sup.toLocaleString() + " 元 ｜ 預估合約總額：$" + (b + sup).toLocaleString() + " 元";
                feeEstimate.classList.remove('hidden');
            } else {
                feeEstimate.classList.add('hidden');
            }
        }
        budgetInput.addEventListener('input', updateEstimate);
        supCheck.addEventListener('change', updateEstimate);

        async function loadExpertInfo() {
            try {
                const res = await fetch('/api/v1/experts/' + EXPERT_ID);
                if (res.ok) {
                    const data = await res.json();
                    let badge = "";
                    if (data.verification_status === 'approved') {
                        badge = '<span class="bg-emerald-100 text-emerald-800 font-bold px-2 py-0.5 rounded text-xs">🛡️ 合格裝修認證</span>';
                    } else if (data.verification_status === 'pending') {
                        badge = '<span class="bg-amber-100 text-amber-800 font-bold px-2 py-0.5 rounded text-xs">⏳ 牌照審核中</span>';
                    } else {
                        badge = '<span class="bg-slate-200 text-slate-600 font-medium px-2 py-0.5 rounded text-xs">未認證</span>';
                    }

                    document.getElementById('expertStatus').innerHTML = "操作身分：<b>" + data.name + " (" + data.rating + "★)</b> " + badge + " ｜ 剩餘點數：<span class='text-emerald-600 font-bold'>" + data.wallet_points + " 點</span>";
                    
                    document.getElementById('adminExpertInfo').innerHTML = "目前測試專家：<b>" + data.name + "</b> ｜ 狀態：<b>" + data.verification_status + "</b> ｜ 牌照權限：" + (data.is_licensed ? '<span class="text-emerald-400">已開通(可接監工案)</span>' : '<span class="text-rose-400">未開通</span>');
                }
            } catch(e) {}
        }

        async function verifyExpert(action) {
            const res = await fetch('/api/v1/admin/experts/verify', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ expert_id: EXPERT_ID, action: action })
            });

            if (res.ok) {
                alert(action === 'approve' ? '✅ 審核已通過！專家已獲得監工接案資格。' : '❌ 已駁回該專家牌照審核。');
                loadExpertInfo();
                loadDemands();
            }
        }

        async function loadDemands() {
            const listDiv = document.getElementById('demandList');
            loadExpertInfo();
            try {
                const res = await fetch('/api/v1/demands');
                if (!res.ok) throw new Error('伺服器回應異常');
                const result = await res.json();

                if (!result.data || result.data.length === 0) {
                    listDiv.innerHTML = '<p class="text-sm text-slate-400">目前大廳尚無案件，請由左側發布...</p>';
                    return;
                }

                listDiv.innerHTML = result.data.map(d => {
                    const supText = d.has_supervision ? '<span class="text-xs text-orange-600 font-normal">(含8%監工)</span>' : '';
                    const photoBadge = d.photos && d.photos.length > 0 ? '<span class="text-xs bg-sky-100 text-sky-700 px-2 py-0.5 rounded font-medium">📸 附 ' + d.photos.length + ' 張照片</span>' : '';

                    return `
                    <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
                        <div class="flex justify-between items-start">
                            <h3 class="font-bold text-slate-800">${d.title}</h3>
                            <div class="flex gap-1.5 items-center">
                                ${photoBadge}
                                <span class="text-xs bg-slate-100 text-slate-600 px-2 py-0.5 rounded">${d.category}</span>
                            </div>
                        </div>
                        <p class="text-xs text-slate-500 mt-1">地區：${d.region} (門牌已遮蔽)</p>
                        <p class="text-sm font-semibold text-slate-700 mt-1">預算：TWD $${d.budget.toLocaleString()} ${supText}</p>
                        <div class="mt-3 pt-3 border-t border-slate-100 flex justify-between items-center gap-2">
                            <span class="text-xs text-slate-400">已有 ${d.unlocked_count} 人解鎖</span>
                            <div class="flex gap-2">
                                <button onclick="unlockDemand('${d.id}')" class="bg-sky-600 hover:bg-sky-700 text-white text-xs font-bold px-3 py-1.5 rounded-lg transition shadow-sm">
                                    🔑 扣點解鎖
                                </button>
                                <button onclick="openQuoteModal('${d.id}', '${d.title}', ${d.has_supervision})" class="bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-bold px-3 py-1.5 rounded-lg transition shadow-sm">
                                    📝 開估價單
                                </button>
                            </div>
                        </div>
                    </div>
                    `;
                }).join('');
            } catch (err) {
                listDiv.innerHTML = '<p class="text-sm text-red-500 font-medium">載入失敗，請確認後端伺服器運行中。</p>';
            }
        }

        // 提交牌照認證
        document.getElementById('licenseForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const formData = new FormData();
            formData.append('expert_id', EXPERT_ID);
            formData.append('license_number', document.getElementById('licenseNumber').value);
            formData.append('file', document.getElementById('licenseFile').files[0]);

            const res = await fetch('/api/v1/experts/license/submit', {
                method: 'POST',
                body: formData
            });

            if (res.ok) {
                alert('🎉 牌照已送出審核！請透過頂部管理員面板測試核准流程。');
                closeLicenseModal();
                loadExpertInfo();
            } else {
                alert('上傳失敗，請重試');
            }
        });

        // 發案送出
        document.getElementById('demandForm').addEventListener('submit', async (e) => {
            e.preventDefault();
            const btn = document.getElementById('submitBtn');
            btn.disabled = true;
            btn.textContent = '處理中...';

            const formData = new FormData();
            formData.append('title', document.getElementById('title').value);
            formData.append('category', document.getElementById('category').value);
            formData.append('budget', document.getElementById('budget').value);
            formData.append('region', document.getElementById('region').value);
            formData.append('detailed_address', document.getElementById('detailed_address').value);
            formData.append('has_supervision', document.getElementById('has_supervision').checked);

            const fileInput = document.getElementById('photos');
            for (let i = 0; i < fileInput.files.length; i++) {
                formData.append('files', fileInput.files[i]);
            }

            try {
                const res = await fetch('/api/v1/demands', {
                    method: 'POST',
                    body: formData
                });

                if (res.ok) {
                    alert('🎉 發案成功！已持久化存入資料庫');
                    document.getElementById('demandForm').reset();
                    updateEstimate();
                    loadDemands();
                } else {
                    alert('發案失敗，請重試');
                }
            } catch (err) {
                alert('連線失敗，請檢查後端是否正常運行');
            } finally {
                btn.disabled = false;
                btn.textContent = '免費送出並開始媒合';
            }
        });

        async function unlockDemand(demandId) {
            try {
                const res = await fetch('/api/v1/leads/purchase', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ demand_id: demandId, expert_id: EXPERT_ID })
                });

                const result = await res.json();
                if (res.ok) {
                    let photoLinksText = "";
                    if (result.photos && result.photos.length > 0) {
                        photoLinksText = "\\n\\n📸 現場照片 (" + result.photos.length + " 張)：\\n" + result.photos.map((url, idx) => "照片" + (idx+1) + ": http://127.0.0.1:8000" + url).join("\\n");
                    }

                    alert("✅ 解鎖成功！\\n實扣點數：" + result.points_deducted + " 點\\n剩餘點數：" + result.remaining_points + " 點\\n地址：" + result.contact_info.detailed_address + "\\n電話：" + result.contact_info.phone + photoLinksText);
                    loadDemands();
                } else {
                    alert('❌ ' + result.detail);
                }
            } catch (err) {
                alert('連線失敗，請檢查後端伺服器');
            }
        }

        function openLicenseModal() {
            document.getElementById('licenseModal').classList.remove('hidden');
        }
        function closeLicenseModal() {
            document.getElementById('licenseModal').classList.add('hidden');
        }
        function openTopupModal() {
            document.getElementById('topupModal').classList.remove('hidden');
        }
        function closeTopupModal() {
            document.getElementById('topupModal').classList.add('hidden');
        }
        function openQuoteModal(demandId, title, hasSupervision) {
            currentQuoteDemandId = demandId;
            const supNote = hasSupervision ? '此案件含 8% 監工服務 (系統將自動增列)' : '一般案件';
            document.getElementById('modalDemandInfo').innerHTML = "<b>針對案件：</b> " + title + " <br><b>監工規格：</b> " + supNote;
            document.getElementById('quoteItemsContainer').innerHTML = '';
            addQuoteRow();
            calculateQuoteTotal();
            document.getElementById('quoteModal').classList.remove('hidden');
        }
        function closeQuoteModal() {
            document.getElementById('quoteModal').classList.add('hidden');
        }

        function addQuoteRow() {
            const container = document.getElementById('quoteItemsContainer');
            const row = document.createElement('div');
            row.className = 'quote-row grid grid-cols-12 gap-2 items-center bg-slate-50 p-2 rounded-lg text-xs';
            row.innerHTML = `
                <input class="col-span-4 border rounded p-1.5 q-item" placeholder="工項名稱" required>
                <input class="col-span-2 border rounded p-1.5 q-unit" placeholder="單位" value="式" required>
                <input class="col-span-2 border rounded p-1.5 q-qty" type="number" placeholder="數量" value="1" oninput="calculateQuoteTotal()" required>
                <input class="col-span-3 border rounded p-1.5 q-price" type="number" placeholder="單價" value="10000" oninput="calculateQuoteTotal()" required>
                <button type="button" onclick="this.parentElement.remove(); calculateQuoteTotal();" class="col-span-1 text-red-500 font-bold text-center">&times;</button>
                <input class="col-span-12 border rounded p-1.5 q-note text-slate-500" placeholder="工法或品牌材料備註">
            `;
            container.appendChild(row);
            calculateQuoteTotal();
        }

        function calculateQuoteTotal() {
            const rows = document.querySelectorAll('.quote-row');
            let total = 0;
            rows.forEach(r => {
                const qty = parseInt(r.querySelector('.q-qty').value) || 0;
                const price = parseInt(r.querySelector('.q-price').value) || 0;
                total += qty * price;
            });
            document.getElementById('quoteTotalDisplay').textContent = "TWD $" + total.toLocaleString();
            return total;
        }

        async function submitQuote() {
            const btn = document.getElementById('quoteSubmitBtn');
            const rows = document.querySelectorAll('.quote-row');
            const items = [];
            rows.forEach(r => {
                items.push({
                    item: r.querySelector('.q-item').value,
                    unit: r.querySelector('.q-unit').value,
                    qty: parseInt(r.querySelector('.q-qty').value) || 1,
                    price: parseInt(r.querySelector('.q-price').value) || 0,
                    note: r.querySelector('.q-note').value || ''
                });
            });

            if (items.length === 0 || !items[0].item) {
                alert('請至少填寫一項工項名稱！');
                return;
            }

            btn.disabled = true;
            btn.textContent = '送出中...';

            try {
                const res = await fetch('/api/v1/quotes', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        demand_id: currentQuoteDemandId,
                        expert_id: EXPERT_ID,
                        items: items
                    })
                });

                if (res.ok) {
                    alert('🎉 估價單送出成功！已自動同步寫入「實價登錄報價牆」');
                    closeQuoteModal();
                    switchTab('market');
                } else {
                    const err = await res.json();
                    alert('❌ ' + err.detail);
                }
            } catch (err) {
                alert('連線失敗，請檢查後端伺服器');
            } finally {
                btn.disabled = false;
                btn.textContent = '送出估價單 (並同步至實價牆)';
            }
        }

        async function loadMarketPrices() {
            const container = document.getElementById('marketPriceList');
            try {
                const res = await fetch('/api/v1/market-prices');
                const result = await res.json();

                container.innerHTML = result.data.map(p => {
                    const supLabel = p.has_supervision ? '✓ 內含 8% 合格監工管理費' : '一般施作案件';
                    const rowsHtml = p.details.map(d => `
                        <tr class="hover:bg-slate-50">
                            <td class="p-2.5 font-medium text-slate-800">${d.item}</td>
                            <td class="p-2.5 text-center text-slate-500">${d.unit}</td>
                            <td class="p-2.5 text-center text-slate-500">${d.qty}</td>
                            <td class="p-2.5 text-right text-slate-600">$${d.price.toLocaleString()}</td>
                            <td class="p-2.5 text-right font-semibold text-slate-800">$${(d.qty * d.price).toLocaleString()}</td>
                            <td class="p-2.5 text-slate-400 italic">${d.note}</td>
                        </tr>
                    `).join('');

                    return `
                    <div class="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
                        <div class="p-5 flex flex-col md:flex-row justify-between items-start md:items-center gap-4 bg-slate-50/50">
                            <div>
                                <span class="text-xs font-bold text-sky-600 bg-sky-50 px-2.5 py-1 rounded-full">${p.category}</span>
                                <h3 class="text-lg font-bold text-slate-800 mt-2">${p.region} | ${p.title}</h3>
                                <p class="text-xs text-slate-500 mt-1">${supLabel}</p>
                            </div>
                            <div class="text-left md:text-right">
                                <p class="text-xs text-slate-400">成交總金額 (TWD)</p>
                                <p class="text-2xl font-black text-sky-700">$${p.total.toLocaleString()}</p>
                                <button onclick="copyTemplate('${p.title}', '${p.category}', ${p.budget})" class="mt-2 text-xs bg-orange-500 hover:bg-orange-600 text-white font-semibold px-3 py-1.5 rounded-lg transition shadow-sm">
                                    🚀 一鍵帶入此範本發案
                                </button>
                            </div>
                        </div>

                        <div class="border-t border-slate-200 p-5">
                            <h4 class="text-xs font-bold text-slate-500 uppercase tracking-wider mb-3">📋 真實完工估價單工料明細</h4>
                            <div class="overflow-x-auto">
                                <table class="w-full text-xs text-left">
                                    <thead class="bg-slate-100 text-slate-600 border-b">
                                        <tr>
                                            <th class="p-2.5">施作工項</th>
                                            <th class="p-2.5 text-center">單位</th>
                                            <th class="p-2.5 text-center">數量</th>
                                            <th class="p-2.5 text-right">單價 (TWD)</th>
                                            <th class="p-2.5 text-right">小計 (TWD)</th>
                                            <th class="p-2.5">工法與材料備註</th>
                                        </tr>
                                    </thead>
                                    <tbody class="divide-y divide-slate-100">
                                        ${rowsHtml}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </div>
                    `;
                }).join('');
            } catch (err) {
                container.innerHTML = '<p class="text-sm text-red-500">載入實價登錄行情失敗。</p>';
            }
        }

        function copyTemplate(title, category, budget) {
            switchTab('main');
            document.getElementById('title').value = title;
            document.getElementById('category').value = category;
            document.getElementById('budget').value = budget;
            updateEstimate();
            window.scrollTo({ top: 0, behavior: 'smooth' });
        }

        loadDemands();
    </script>
</body>
</html>
"""

# ==========================================
# 3. 核心 API 端點與牌照審核
# ==========================================

@app.get("/", response_class=HTMLResponse)
def render_index():
    return HTML_CONTENT

@app.get("/api/v1/experts/{expert_id}", summary="取得專家資料")
def get_expert_profile(expert_id: str, db: Session = Depends(get_db)):
    expert = db.query(ExpertModel).filter(ExpertModel.id == expert_id).first()
    if not expert:
        raise HTTPException(status_code=404, detail="找不到專家")
    return {
        "id": expert.id,
        "name": expert.name,
        "rating": expert.rating,
        "wallet_points": expert.wallet_points,
        "is_licensed": expert.is_licensed,
        "verification_status": expert.verification_status,
        "license_number": expert.license_number,
        "license_file_url": expert.license_file_url
    }

@app.post("/api/v1/experts/license/submit", summary="專家提交牌照審核")
async def submit_license(
    expert_id: str = Form(...),
    license_number: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    expert = db.query(ExpertModel).filter(ExpertModel.id == expert_id).first()
    if not expert:
        raise HTTPException(status_code=404, detail="找不到專家")

    ext = os.path.splitext(file.filename)[1]
    saved_filename = f"license_{expert_id}_{uuid.uuid4().hex[:6]}{ext}"
    file_path = os.path.join(UPLOAD_DIR, saved_filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    expert.license_number = license_number
    expert.license_file_url = f"/uploads/{saved_filename}"
    expert.verification_status = "pending"
    db.commit()

    return {"status": "success", "message": "牌照已提交審核"}

@app.post("/api/v1/admin/experts/verify", summary="管理員審核專家牌照")
def admin_verify_expert(req: VerifyExpertRequest, db: Session = Depends(get_db)):
    expert = db.query(ExpertModel).filter(ExpertModel.id == req.expert_id).first()
    if not expert:
        raise HTTPException(status_code=404, detail="找不到專家")

    if req.action == "approve":
        expert.verification_status = "approved"
        expert.is_licensed = True
    else:
        expert.verification_status = "rejected"
        expert.is_licensed = False

    db.commit()
    return {"status": "success", "verification_status": expert.verification_status, "is_licensed": expert.is_licensed}

@app.post("/api/v1/demands", summary="消費者發布需求")
async def create_demand(
    title: str = Form(...),
    category: str = Form(...),
    budget: int = Form(...),
    region: str = Form(...),
    detailed_address: str = Form(...),
    has_supervision: bool = Form(False),
    files: Optional[List[UploadFile]] = File(None),
    db: Session = Depends(get_db)
):
    demand_id = f"dem_{str(uuid.uuid4())[:6]}"
    supervision_fee = int(budget * 0.08) if has_supervision else 0
    total_contract_amount = budget + supervision_fee

    saved_photo_urls = []
    if files:
        for f in files:
            if f.filename:
                ext = os.path.splitext(f.filename)[1]
                saved_filename = f"{demand_id}_{uuid.uuid4().hex[:6]}{ext}"
                file_path = os.path.join(UPLOAD_DIR, saved_filename)
                with open(file_path, "wb") as buffer:
                    shutil.copyfileobj(f.file, buffer)
                saved_photo_urls.append(f"/uploads/{saved_filename}")

    new_demand = DemandModel(
        id=demand_id,
        title=title,
        category=category,
        budget=budget,
        region=region,
        detailed_address=detailed_address,
        has_supervision=has_supervision,
        supervision_fee=supervision_fee,
        total_contract_amount=total_contract_amount,
        base_lead_price=300
    )
    new_demand.unlocked_experts = []
    new_demand.photos = saved_photo_urls

    db.add(new_demand)
    db.commit()
    return {"status": "success", "data": {"demand_id": demand_id, "photos": saved_photo_urls}}

@app.get("/api/v1/demands", summary="專家大廳案件清單")
def list_demands(db: Session = Depends(get_db)):
    demands = db.query(DemandModel).all()
    results = []
    for d in demands:
        results.append({
            "id": d.id,
            "title": d.title,
            "category": d.category,
            "budget": d.budget,
            "region": d.region,
            "has_supervision": d.has_supervision,
            "supervision_fee": d.supervision_fee,
            "total_contract_amount": d.total_contract_amount,
            "unlocked_count": len(d.unlocked_experts),
            "photos": d.photos
        })
    return {"status": "success", "data": results}

@app.post("/api/v1/leads/purchase", summary="專家解鎖名單 (強制檢驗監工牌照資格)")
def purchase_lead(req: PurchaseLeadRequest, db: Session = Depends(get_db)):
    demand = db.query(DemandModel).filter(DemandModel.id == req.demand_id).first()
    expert = db.query(ExpertModel).filter(ExpertModel.id == req.expert_id).first()

    if not demand or not expert:
        raise HTTPException(status_code=404, detail="案件或專家不存在")
    if demand.has_supervision and not expert.is_licensed:
        raise HTTPException(status_code=403, detail="此案件包含 8% 監工服務，需通過官方牌照認證方可解鎖接案！")

    unlocked_list = demand.unlocked_experts
    if expert.id in unlocked_list:
        raise HTTPException(status_code=400, detail="您先前已解鎖過此案件")

    price = calculate_dynamic_price(demand.base_lead_price, expert.rating, expert.dispute_rate, len(unlocked_list))
    if expert.wallet_points < price:
        raise HTTPException(status_code=400, detail=f"點數不足 (需 {price} 點)")

    expert.wallet_points -= price
    unlocked_list.append(expert.id)
    demand.unlocked_experts = unlocked_list
    db.commit()

    return {
        "status": "success",
        "points_deducted": price,
        "remaining_points": expert.wallet_points,
        "photos": demand.photos,
        "contact_info": {
            "region": demand.region,
            "detailed_address": demand.detailed_address,
            "phone": "0912-345-678"
        }
    }

@app.post("/api/v1/quotes", summary="專家開立估價單")
def create_quote(req: CreateQuoteRequest, db: Session = Depends(get_db)):
    demand = db.query(DemandModel).filter(DemandModel.id == req.demand_id).first()
    if not demand:
        raise HTTPException(status_code=404, detail="找不到對應案件")

    subtotal = sum(item.qty * item.price for item in req.items)
    supervision_fee = int(subtotal * 0.08) if demand.has_supervision else 0
    total_amount = subtotal + supervision_fee

    new_market_record = {
        "id": f"price_{str(uuid.uuid4())[:6]}",
        "category": demand.category,
        "title": demand.title,
        "region": demand.region,
        "budget": subtotal,
        "supervision_fee": supervision_fee,
        "total": total_amount,
        "has_supervision": demand.has_supervision,
        "details": [item.dict() for item in req.items]
    }
    MOCK_MARKET_PRICES.insert(0, new_market_record)

    return {
        "status": "success",
        "message": "估價單開立成功",
        "data": {
            "subtotal": subtotal,
            "supervision_fee": supervision_fee,
            "total_amount": total_amount
        }
    }

@app.get("/api/v1/market-prices", summary="實價行情牆 API")
def get_market_prices():
    return {"status": "success", "data": MOCK_MARKET_PRICES}

@app.post("/api/v1/payments/create", summary="建立綠界刷卡訂單")
async def create_ecpay_payment(
    package: int = Form(...),
    expert_id: str = Form(...)
):
    points_map = {500: 500, 1000: 1100}
    points = points_map.get(package, package)
    order_id = f"QT{datetime.now().strftime('%Y%m%d%H%M%S')}{expert_id[-2:]}"

    return_url = "http://127.0.0.1:8000/api/v1/payments/callback"
    client_back_url = "http://127.0.0.1:8000/"

    ecpay_html = ecpay_sdk.generate_auto_submit_html(
        merchant_trade_no=order_id,
        total_amount=package,
        trade_desc=f"QT30專家儲值-{points}點",
        item_name=f"QT30接案點數 {points} 點",
        return_url=return_url,
        client_back_url=client_back_url
    )
    return HTMLResponse(content=ecpay_html)

@app.post("/api/v1/payments/callback", summary="綠界支付回傳")
async def ecpay_payment_callback(request: Request, db: Session = Depends(get_db)):
    form_data = await request.form()
    payload = dict(form_data)

    if not ecpay_sdk.verify_check_mac_value(payload):
        raise HTTPException(status_code=400, detail="CheckMacValue 驗證失敗")

    if payload.get("RtnCode") == "1":
        trade_amt = int(payload.get("TradeAmt", 0))
        points_map = {500: 500, 1000: 1100}
        points_to_add = points_map.get(trade_amt, trade_amt)

        expert = db.query(ExpertModel).filter(ExpertModel.id == "exp_1").first()
        if expert:
            expert.wallet_points += points_to_add
            db.commit()

        return PlainTextResponse("1|OK")

    return PlainTextResponse("0|Fail")