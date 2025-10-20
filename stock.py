import os
import json
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple
import boto3

# 상수
KST = timezone(timedelta(hours=9))
TOKEN_BUFFER_SEC = 300  # 5분

# 디렉토리 경로 설정 (EC2 환경에서도 작동)
try:
    DIR_PATH = Path(__file__).resolve().parent
except NameError:
    DIR_PATH = Path(os.getcwd())

# 토큰 저장 디렉토리 생성
TOKEN_DIR = DIR_PATH / "tokens"
TOKEN_DIR.mkdir(exist_ok=True)

# ==================== 유틸리티 ====================
def split_account(account: str) -> Tuple[str, str]:
    account = account.strip()
    return account.split("-", 1) if "-" in account else (account[:8], account[8:])

def now_kst() -> datetime:
    return datetime.now(KST)

def today_kst_str() -> str:
    return now_kst().strftime("%Y%m%d")

def safe_float(val, default=0.0) -> float:
    try: return float(val or default)
    except (ValueError, TypeError): return default

def safe_int(val, default=0) -> int:
    try: return int(float(val or default))
    except (ValueError, TypeError): return default

# ==================== Base API 클래스 ====================
class BaseAPI:
    def __init__(self, app_key: str, app_secret: str, account_prefix: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.token_file = TOKEN_DIR / f"token_{account_prefix}_{self.__class__.__name__}.json"
        self._token: Optional[str] = None

    def get_token(self) -> str:
        """토큰 반환 (파일 캐시 → 신규 발급)"""
        # 파일에서 로드
        if self.token_file.exists():
            try:
                with open(self.token_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                expires_at = data.get("expires_at", 0)
                if expires_at > now_kst().timestamp():
                    self._token = self._extract_token(data)
                    if self._token:
                        print(f"[{self.token_file.name}] 토큰 재사용")
                        return self._token
            except (json.JSONDecodeError, KeyError):
                pass
        
        # 신규 발급
        print(f"[{self.token_file.name}] 새 토큰 발급 중...")
        return self._issue_token()

    def _extract_token(self, data: dict) -> Optional[str]:
        return data.get("access_token") or data.get("token")

    def _issue_token(self) -> str:
        raise NotImplementedError

    def _save_token(self, data: dict, expires_at: float):
        token = self._extract_token(data)
        if token:
            data["expires_at"] = expires_at
            with open(self.token_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._token = token
            print(f"[{self.token_file.name}] 토큰 저장 완료")

# ==================== 한국투자증권(KIS) ====================
class KISApi(BaseAPI):
    BASE_URL = "https://openapi.koreainvestment.com:9443"
    
    def __init__(self, app_key: str, app_secret: str, account_no: str, account_type: str):
        super().__init__(app_key, app_secret, f"KIS_{account_type}")
        self.account_no = account_no
        self.account_type = account_type

    def _issue_token(self) -> str:
        url = f"{self.BASE_URL}/oauth2/tokenP"
        headers = {"content-type": "application/json"}
        body = {"grant_type": "client_credentials", "appkey": self.app_key, "appsecret": self.app_secret}
        res = requests.post(url, headers=headers, json=body, timeout=15)
        res.raise_for_status()
        data = res.json()
        expires_at = now_kst().timestamp() + int(data["expires_in"]) - TOKEN_BUFFER_SEC
        self._save_token(data, expires_at)
        return self._token
    
    def _request(self, method: str, endpoint: str, tr_id: str, params: dict = None) -> Optional[dict]:
        headers = {
            "Content-Type": "application/json",
            "authorization": f"Bearer {self.get_token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id
        }
        
        url = f"{self.BASE_URL}{endpoint}"
        res = requests.request(method, url, headers=headers, params=params, timeout=20)
        
        if res.status_code == 200:
            data = res.json()
            if data.get("rt_cd") == "0":
                return data
        
        print(f"[KIS 오류] {endpoint}: {res.text}")
        return None

    def get_domestic_balance(self) -> List[dict]:
        cano, prdt = split_account(self.account_no)
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }
        
        data = self._request("GET", "/uapi/domestic-stock/v1/trading/inquire-balance", 
                           "TTTC8434R", params)
        return self._parse_domestic(data) if data else []

    def get_overseas_balance(self) -> List[dict]:
        cano, prdt = split_account(self.account_no)
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "WCRC_FRCR_DVSN_CD": "02",
            "TR_MKET_CD": "00",
            "NATN_CD": "000",
            "INQR_DVSN_CD": "00"
        }
        
        data = self._request("GET", "/uapi/overseas-stock/v1/trading/inquire-present-balance",
                           "CTRP6504R", params)
        return self._parse_overseas(data) if data else []
    
    def _parse_domestic(self, data: dict) -> List[dict]:
        result = []
        output = data.get("output", data)

        base_asset = {
            "broker": "한국투자증권",
            "account_type": "개인" if self.account_type == "P" else "법인",
            "account_label": "조현익(한투)" if self.account_type == "P" else "뮤사이(한투)",
        }

        for stock in output.get("output1", []):
            qty = safe_int(stock.get("hldg_qty"))
            if qty > 0:
                asset = base_asset.copy()
                asset.update({
                    "market": "domestic",
                    "asset_type": "stock",
                    "ticker": stock.get("pdno"),
                    "name": stock.get("prdt_name"),
                    "quantity": qty,
                    "avg_buy_price": safe_float(stock.get("pchs_avg_pric")),
                    "current_price": safe_float(stock.get("prpr")),
                    "eval_amount": safe_int(stock.get("evlu_amt")),
                    "profit_loss": safe_int(stock.get("evlu_pfls_amt")),
                    "profit_rate": safe_float(stock.get("evlu_pfls_rt")),
                    "currency": "KRW"
                })
                result.append(asset)
        
        if output.get("output2"):
            cash_list = output.get("output2", [])
            cash_amt = safe_int(cash_list[0].get("nxdy_excc_amt"))
            if cash_amt > 0:
                asset = base_asset.copy()
                asset.update({
                    "market": "domestic",
                    "asset_type": "cash",
                    "ticker": "KRW",
                    "name": "원화 예수금",
                    "quantity": 1,
                    "avg_buy_price": cash_amt,
                    "current_price": cash_amt,
                    "eval_amount": cash_amt,
                    "profit_loss": 0,
                    "profit_rate": 0.0,
                    "currency": "KRW"
                })
                result.append(asset)
        
        return result
    
    def _parse_overseas(self, data: dict) -> List[dict]:
        result = []
        
        for stock in data.get("output1", []):
            qty = safe_int(stock.get("ccld_qty_smtl1"))
            if qty > 0:
                currency = stock.get("buy_crcy_cd") or "USD"
                avg_buy_price = safe_float(stock.get("avg_unpr3"))
                if avg_buy_price == 0:
                    avg_buy_price = safe_float(stock.get("pchs_avg_pric"))
                
                profit_loss_raw = safe_float(stock.get("evlu_pfls_amt2"))
                profit_rate_raw = safe_float(stock.get("evlu_pfls_rt1"))
                
                result.append({
                    "broker": "한국투자증권",
                    "account_type": "개인" if self.account_type == "P" else "법인",
                    "account_label": "조현익(한투)" if self.account_type == "P" else "뮤사이(한투)",
                    "market": "overseas",
                    "asset_type": "stock",
                    "ticker": stock.get("pdno"),
                    "name": stock.get("prdt_name"),
                    "quantity": qty,
                    "avg_buy_price": avg_buy_price,
                    "current_price": safe_float(stock.get("ovrs_now_pric1")),
                    "eval_amount": safe_float(stock.get("frcr_evlu_amt2")),
                    "profit_loss": profit_loss_raw,
                    "profit_rate": profit_rate_raw,
                    "currency": currency
                })
        
        for cash in data.get("output2", []):
            amt = safe_float(cash.get("frcr_dncl_amt_2"))
            ccy = cash.get("crcy_cd") or "USD"
            if amt > 0:
                result.append({
                    "broker": "한국투자증권",
                    "account_type": "개인" if self.account_type == "P" else "법인",
                    "account_label": "조현익(한투)" if self.account_type == "P" else "뮤사이(한투)",
                    "market": "overseas",
                    "asset_type": "cash",
                    "ticker": ccy,
                    "name": f"{ccy} 예수금",
                    "quantity": 1,
                    "avg_buy_price": amt,
                    "current_price": amt,
                    "eval_amount": amt,
                    "profit_loss": 0,
                    "profit_rate": 0.0,
                    "currency": ccy
                })
        
        return result

# ==================== 키움증권 ====================
class KiwoomAPI(BaseAPI):
    BASE_URL = "https://api.kiwoom.com"
    
    def __init__(self, app_key: str, app_secret: str, account_no: str):
        super().__init__(app_key, app_secret, "KIWOOM_C")
        self.account_no = account_no

    def _extract_token(self, data: dict) -> Optional[str]:
        return data.get("token") or data.get("access_token")

    def _issue_token(self) -> str:
        url = f"{self.BASE_URL}/oauth2/token"
        headers = {"Content-Type": "application/json;charset=UTF-8"}
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret
        }
        
        res = requests.post(url, headers=headers, json=body, timeout=20)
        res.raise_for_status()
        data = res.json()
        
        expires_at = now_kst().timestamp() + 1800
        if "expires_dt" in data:
            try:
                dt = datetime.strptime(data["expires_dt"], "%Y%m%d%H%M%S").replace(tzinfo=KST)
                expires_at = dt.timestamp()
            except ValueError:
                pass
        
        expires_at -= TOKEN_BUFFER_SEC
        self._save_token(data, expires_at)
        return self._token

    def get_domestic_balance(self, qry_dt: str = None) -> List[dict]:
        url = f"{self.BASE_URL}/api/dostk/acnt"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.get_token()}",
            "cont-yn": "N",
            "next-key": "",
            "api-id": "ka01690"
        }
        body = {"qry_dt": qry_dt or today_kst_str()}
        
        res = requests.post(url, headers=headers, json=body, timeout=20)
        res.raise_for_status()
        raw_data = res.json()
        
        return self._parse_domestic(raw_data)

    @staticmethod
    def _parse_domestic(data: dict) -> List[dict]:
        result = []
        
        if data.get("return_code") != 0:
            print(f"[키움 오류] {data.get('return_msg')}")
            return result
        
        for stock in data.get("day_bal_rt", []):
            qty = safe_int(stock.get("rmnd_qty"))
            if qty > 0:
                name = stock.get("stk_nm", stock.get("stk_cd", "알 수 없음"))
                
                result.append({
                    "broker": "키움증권",
                    "account_type": "법인",
                    "account_label": "뮤사이(키움)",
                    "market": "domestic",
                    "asset_type": "stock",
                    "ticker": stock.get("stk_cd"),
                    "name": name,
                    "quantity": qty,
                    "avg_buy_price": safe_float(stock.get("buy_uv")),
                    "current_price": safe_float(stock.get("cur_prc")),
                    "eval_amount": safe_int(stock.get("evlt_amt")),
                    "profit_loss": safe_int(stock.get("evltv_prft")),
                    "profit_rate": safe_float(stock.get("prft_rt")),
                    "currency": "KRW"
                })
        
        dbst_bal = safe_int(data.get("dbst_bal"))
        if dbst_bal > 0:
            result.append({
                "broker": "키움증권",
                "account_type": "법인",
                "account_label": "뮤사이(키움)",
                "market": "domestic",
                "asset_type": "cash",
                "ticker": "KRW",
                "name": "원화 예수금",
                "quantity": 1,
                "avg_buy_price": dbst_bal,
                "current_price": dbst_bal,
                "eval_amount": dbst_bal,
                "profit_loss": 0,
                "profit_rate": 0.0,
                "currency": "KRW"
            })
        
        return result

# ==================== 메인 실행 ====================
def load_account_config(prefix: str, broker: str) -> Optional[Dict]:
    """AWS Parameter Store에서 인증 정보를 로드합니다."""
    key_suffix = "HANTOO" if broker == "kis" else "KIWOOM"
    param_names = {
        "app_key": f"/stock-dashboard/{prefix}_{key_suffix}_APP_KEY",
        "app_secret": f"/stock-dashboard/{prefix}_{key_suffix}_APP_SECRET",
        "account_no": f"/stock-dashboard/{prefix}_{key_suffix}_ACCOUNT_NO"
    }

    try:
        ssm_client = boto3.client('ssm', region_name='ap-northeast-2')
        response = ssm_client.get_parameters(
            Names=list(param_names.values()),
            WithDecryption=True
        )

        retrieved_params = {p['Name']: p['Value'] for p in response['Parameters']}

        if len(retrieved_params) != len(param_names):
            print(f"[{prefix}] 일부 파라미터를 찾을 수 없습니다.")
            return None

        return {
            "app_key": retrieved_params[param_names["app_key"]],
            "app_secret": retrieved_params[param_names["app_secret"]],
            "account_no": retrieved_params[param_names["account_no"]],
            "prefix": prefix,
            "broker": broker
        }

    except Exception as e:
        print(f"[{prefix}] AWS Parameter Store에서 설정 로드 중 오류 발생: {e}")
        return None

def collect_all_assets(skip_kiwoom=False):
    """모든 증권사 API를 호출하여 통합된 자산 목록을 반환하는 함수."""
    all_assets = []
    
    # 한국투자증권 (개인, 법인)
    for prefix in ["P", "C"]:
        config = load_account_config(prefix, "kis")
        if not config:
            continue
        
        api = KISApi(
            config["app_key"], 
            config["app_secret"], 
            config["account_no"], 
            prefix
        )
        
        label = f"한국투자증권({'개인' if api.account_type == 'P' else '법인'})"
        print(f"[{label}] 데이터 수집 중...")

        all_assets.extend(api.get_domestic_balance())
        all_assets.extend(api.get_overseas_balance())
    
    # 키움증권 (법인)
    kiw_config = load_account_config("C", "kiwoom")
    if skip_kiwoom:
        print("[키움증권(법인)] IP 제한으로 인해 스킵됨")
    else:
        kiw_config = load_account_config("C", "kiwoom")
        if kiw_config:
            api = KiwoomAPI(
                kiw_config["app_key"], 
                kiw_config["app_secret"],
                kiw_config["account_no"]
            )

            print(f"[키움증권(법인)] 데이터 수집 중...")
            try:
                all_assets.extend(api.get_domestic_balance())
            except Exception as e:
                print(f"[오류] 키움증권 데이터 수집 실패: {e}")
                
    return all_assets

def main():
    """
    로컬에서 직접 실행할 때만 사용되는 함수 (테스트용).
    수집된 데이터를 portfolio_unified.json 파일로 저장합니다.
    """
    print("=" * 60 + "\n로컬 데이터 수집 시작\n" + "=" * 60)
    assets = collect_all_assets()
    
    output_data = {
        "last_updated": datetime.now(KST).isoformat(),
        "last_updated_readable": datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S'),
        "assets": assets
    }
    
    output_file = DIR_PATH / "portfolio_unified.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)
    
    print(f"\n총 {len(assets)}건 수집 완료 → {output_file}")

if __name__ == "__main__":
    main()