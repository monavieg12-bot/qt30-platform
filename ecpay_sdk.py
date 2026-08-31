import hashlib
import urllib.parse
from datetime import datetime
import random

class ECPayPaymentSDK:
    def __init__(self, merchant_id: str = "2000132", 
                 hash_key: str = "5294y06JbBsc56c4", 
                 hash_iv: str = "v77hoKGq4kWxpxZt", 
                 is_sandbox: bool = True):
        self.merchant_id = merchant_id
        self.hash_key = hash_key
        self.hash_iv = hash_iv
        self.is_sandbox = is_sandbox
        self.api_url = "https://payment-stage.ecpay.com.tw/Cashier/AioCheckOut/V5" if is_sandbox else "https://payment.ecpay.com.tw/Cashier/AioCheckOut/V5"

    def _generate_mac_value(self, params: dict) -> str:
        # 依字典字母排序並以 & 串接
        sorted_params = sorted(params.items())
        raw_list = [f"{k}={v}" for k, v in sorted_params]
        raw_string = f"HashKey={self.hash_key}&" + "&".join(raw_list) + f"&HashIV={self.hash_iv}"
        
        # 依綠界標準做 URL 編碼與特殊字元轉換
        url_encoded = urllib.parse.quote_plus(raw_string)
        encoded_str = url_encoded.replace("%2D", "-").replace("%5F", "_").replace("%2E", ".").replace("%21", "!").replace("%2A", "*").replace("%28", "(").replace("%29", ")").lower()
        
        # SHA256 轉大寫
        return hashlib.sha256(encoded_str.encode('utf-8')).hexdigest().upper()

    def create_topup_order_params(self, expert_id: str, points_to_buy: int, twd_amount: int, return_url: str, client_back_url: str) -> dict:
        trade_no = f"TOPUP{datetime.now().strftime('%Y%m%d%H%M%S')}{random.randint(100, 999)}"
        params = {
            "MerchantID": self.merchant_id,
            "MerchantTradeNo": trade_no,
            "MerchantTradeDate": datetime.now().strftime("%Y/%m/%d %H:%M:%S"),
            "PaymentType": "aio",
            "TotalAmount": str(twd_amount),
            "TradeDesc": urllib.parse.quote(f"平台專家點數儲值-{points_to_buy}點"),
            "ItemName": f"專家點數儲值 {points_to_buy} 點",
            "ReturnURL": return_url,
            "ClientBackURL": client_back_url,
            "ChoosePayment": "ALL",
            "EncryptType": "1",
            "CustomField1": expert_id,
            "CustomField2": str(points_to_buy)
        }
        params["CheckMacValue"] = self._generate_mac_value(params)
        return params

    def verify_callback_signature(self, callback_params: dict) -> bool:
        params = callback_params.copy()
        received_mac = params.pop("CheckMacValue", None)
        if not received_mac:
            return False
        return self._generate_mac_value(params) == received_mac