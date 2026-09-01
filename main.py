import os
import json
import requests
from typing import List, Dict

# ==========================================
# 1. 2026 平台標準 13 大工種庫 (極致優化 SEO 關鍵字)
# ==========================================
VALID_CATEGORIES = [
    "拆除工程",
    "泥作工程",
    "貼磚工程",
    "水電維修",
    "木工裝潢",
    "系統櫃工程",
    "防水工程",
    "抓漏探測",
    "老屋翻修",
    "浴室翻修",
    "油漆粉刷",
    "氣密窗工程",
    "鐵工工程"
]

# ==========================================
# 2. 備用本地規則引擎 (針對 13 大工種進行精準台灣在地修繕詞庫匹配)
# ==========================================
LOCAL_RULES = {
    "拆除工程": ["拆除", "打牆", "打石", "木作拆除", "隔間拆", "清運", "垃圾清運", "廢棄物"],
    "泥作工程": ["泥作", "水泥", "粉刷牆面", "砌磚", "打底", "粉光", "修補地磚", "灌漿", "粉刷"],
    "貼磚工程": ["貼磚", "地磚", "壁磚", "磁磚", "大理石", "拋光石英磚", "磁磚澎拱", "空心磚"],
    "水電維修": ["水電", "漏水", "水管", "馬桶", "開關", "插座", "跳電", "熱水器", "水龍頭", "電箱", "燈具", "配電", "洗臉盆"],
    "木工裝潢": ["木工", "天花板", "矽酸鈣板", "木隔間", "門框", "包樑", "木地板", "窗簾盒"],
    "系統櫃工程": ["系統櫃", "衣櫃", "書櫃", "鞋櫃", "電視櫃", "收納櫃", "客製家具", "E1級V313"],
    "防水工程": ["防水", "PU防水", "外牆防水", "屋頂防水", "防水漆", "彈性水泥", "防水層", "剪力牆"],
    "抓漏探測": ["抓漏", "滲水", "漏水檢測", "打針", "高壓灌注", "紅外線顯像", "管道漏水", "天花板滴水"],
    "老屋翻修": ["老屋", "翻修", "整棟翻新", "統包", "老屋改造", "全屋裝潢", "格局重整", "拉皮", "老翻新"],
    "浴室翻修": ["浴室翻修", "衛浴整修", "乾濕分離", "浴缸拆除", "防滑地磚", "浴室", "廁所翻新"],
    "油漆粉刷": ["油漆", "壁癌", "脫漆", "補土", "裂縫", "刷漆", "得利", "青葉", "虹牌", "批土", "發霉", "牆壁發霉", "漆"],
    "氣密窗工程": ["氣密窗", "隔音窗", "落地窗", "鋁門窗", "防夾手", "紗窗", "採光罩"],
    "鐵工工程": ["鐵工", "鐵皮屋", "鐵捲門", "鋼架", "欄杆", "防盜窗", "遮雨棚", "鋼骨"]
}

def local_diagnose(description: str) -> List[str]:
    """本地關鍵字診斷器 (Fallback)"""
    matched = []
    # 精準匹配
    for category, keywords in LOCAL_RULES.items():
        for kw in keywords:
            if kw in description:
                matched.append(category)
                break
                
    # 複合邏輯與降噪優化
    if "老屋" in description or "老屋翻修" in matched:
        # 如果是老屋整修，直接返回統包「老屋翻修」大項即可
        return ["老屋翻修"]
        
    if "浴室" in description and ("貼磚" in description or "防水" in description or "馬桶" in description):
        # 浴室的大範圍修繕，自動歸類為「浴室翻修」
        return ["浴室翻修"]

    # 如果沒對上任何東西，預設為水電維修
    if not matched:
        return ["水電維修"]
    return matched

# ==========================================
# 3. 核心 AI 智慧導診路由器 (對接 OpenAI GPT-4o-mini)
# ==========================================
def ai_diagnose(description: str, api_key: str = None) -> Dict:
    """
    AI 13 大工種智慧導診核心邏輯
    - 若有提供 api_key，則呼叫 GPT-4o-mini 進行語意分析與精準工種路由。
    - 若無提供 api_key，自動切換至本地 13 大關鍵字規則引擎，確保系統 100% 穩定。
    """
    if not api_key:
        api_key = os.getenv("OPENAI_API_KEY", "")

    if not api_key:
        # 無 Key，走本地規則
        tags = local_diagnose(description)
        return {
            "status": "success",
            "engine": "Local Heuristics Engine (離線 13 大工種匹配)",
            "diagnosed_categories": tags,
            "description": description
        }

    # 有 Key，走 OpenAI 高階自然語言分析
    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    prompt = f"""
    你是一個台灣室內裝修與局部修繕平台的『13 大工種智慧派工路由器』。
    請根據消費者的主觀故障或裝修描述，從下方的【標準 13 大工種庫】中，挑選出最適合前往現場勘查並報價的 1~2 個工種。

    【標準 13 大工種庫】:
    {json.dumps(VALID_CATEGORIES, ensure_ascii=False)}

    【消費者主觀描述】:
    "{description}"

    【輸出規範】:
    1. 你只能從標準 13 大工種庫中挑選，不能自己創造新詞。
    2. 請以 JSON 格式輸出，結構必須為：{{"tags": ["工種1", "工種2"]}}。
    3. 不要輸出任何 Markdown 語法（如 ```json）或多餘解釋，只輸出純 JSON。
    """

    payload = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You are a precise JSON classifier for Taiwanese renovation and home repair services."},
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"}
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=5)
        if response.status_code == 200:
            result_json = response.json()
            content = json.loads(result_json["choices"][0]["message"]["content"])
            filtered_tags = [t for t in content.get("tags", []) if t in VALID_CATEGORIES]
            if not filtered_tags:
                filtered_tags = local_diagnose(description)
            return {
                "status": "success",
                "engine": "OpenAI GPT-4o-mini (13 大工種語意路由)",
                "diagnosed_categories": filtered_tags,
                "description": description
            }
    except Exception as e:
        print(f"[系統提示] AI 連線異常 ({e})，自動切換至本地 13 大工種診斷。")
    
    return {
        "status": "success",
        "engine": "Local Heuristics Engine (Fallback)",
        "diagnosed_categories": local_diagnose(description),
        "description": description
    }

# ==========================================
# 4. 測試案例展示
# ==========================================
if __name__ == "__main__":
    print("=" * 65)
    print("       QT30 AI 房屋修繕智慧診斷路由器 - 13 大工種全新升級測試")
    print("=" * 65)
    
    test_cases = [
        "浴室地板一直濕濕的，好像是馬桶水箱或者是底下水管在漏水，搞得地板都是積水",
        "買了一間 40 年的淡水老透天，想要全棟重新整理、拉皮、水電重拉跟格局重劃，預算大約 200 萬",
        "客廳牆壁一整片都在發霉剝落，起很多白色粉末，想要處理順便重新刷成白色的漆",
        "我想做一個貼壁磚和拋光石英磚的工程，大概有十坪左右",
        "要把舊的木作隔間牆打掉拆除，然後清運走，另外要做氣密窗隔音和不鏽鋼鐵門防盜"
    ]
    
    for idx, case in enumerate(test_cases, 1):
        res = ai_diagnose(case)
        print(f"\n【案例 {idx}】: {case}")
        print(f" ➔ 診斷引擎: {res['engine']}")
        print(f" ➔ 建議工種: {', '.join(res['diagnosed_categories'])}")
    
    print("\n" + "=" * 65)
    print(" [升級提示] 13 大精準 SEO 工種已完全封裝入庫，本 PoC 可直接無縫與後端整合！")
    print("=" * 65)
