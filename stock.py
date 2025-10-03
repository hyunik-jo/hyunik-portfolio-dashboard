import os
import json
import requests
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from dotenv import load_dotenv
import streamlit as st


# 상수
KST = timezone(timedelta(hours=9))
TOKEN_BUFFER_SEC = 300  # 5분
try:
    DIR_PATH = Path(__file__).resolve().parent
except NameError:
    DIR_PATH = Path(os.getcwd())
ENV_PATH = DIR_PATH / ".env"
load_dotenv(dotenv_path=ENV_PATH)


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
# stock.py 파일의 BaseAPI 클래스 전체를 아래 코드로 덮어쓰세요.
class BaseAPI:
    def __init__(self, app_key: str, app_secret: str, account_prefix: str):
        self.app_key = app_key
        self.app_secret = app_secret
        self.token_key = f"token_{account_prefix}_{self.__class__.__name__}"
        self._token: Optional[str] = None

    def get_token(self) -> str:
        token_info = st.session_state.get(self.token_key, {})
        if token_info and token_info.get("expires_at", 0) > now_kst().timestamp():
            print(f"[{self.token_key}] 세션 상태 토큰 재사용.")
            return token_info["token"]
        
        print(f"[{self.token_key}] 새 토큰 발급 중...")
        return self._issue_token()

    def _extract_token(self, data: dict) -> Optional[str]:
        return data.get("access_token") or data.get("token")

    def _issue_token(self) -> str:
        raise NotImplementedError

    def _save_token(self, data: dict, expires_at: float):
        token = self._extract_token(data)
        if token:
            st.session_state[self.token_key] = {"token": token, "expires_at": expires_at}
            self._token = token
            print(f"[{self.token_key}] 토큰을 세션 상태에 저장 완료.")
# ==================== 한국투자증권(KIS) ====================
class KISApi(BaseAPI):
    BASE_URL = "https://openapi.koreainvestment.com:9443"
    
    def __init__(self, app_key: str, app_secret: str, account_no: str, account_type: str):
        super().__init__(app_key, app_secret, f"KIS_{account_type}")
        self.account_no = account_no
        self.account_type = account_type
        self.base_asset_info = {
            "broker": "한국투자증권",
            "account_type": "개인" if self.account_type == "P" else "법인",
            "account_label": "조현익(한투)" if self.account_type == "P" else "뮤사이(한투)",
        }

    def _issue_token(self) -> str:
        # (이하 KISApi의 모든 메소드는 기존과 동일하게 유지)
        # ...
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
        """KIS API 공통 요청"""
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
        """국내 주식 잔고 조회"""
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
        """해외 주식 잔고 조회 (체결기준)"""
        cano, prdt = split_account(self.account_no)
        params = {
            "CANO": cano,
            "ACNT_PRDT_CD": prdt,
            "WCRC_FRCR_DVSN_CD": "02",  # 외화 기준
            "TR_MKET_CD": "00",
            "NATN_CD": "000",
            "INQR_DVSN_CD": "00"
        }
        
        data = self._request("GET", "/uapi/overseas-stock/v1/trading/inquire-present-balance",
                           "CTRP6504R", params)
        return self._parse_overseas(data) if data else []


    
    def _parse_domestic(self, data: dict) -> List[dict]:
        """KIS 국내 잔고 파싱 → 표준 포맷"""
        result = []
        output = data.get("output", data)

        # 공통 정보 템플릿
        base_asset = {
            "broker": "한국투자증권",
            "account_type": "개인" if self.account_type == "P" else "법인",
            "account_label": "조현익(한투)" if self.account_type == "P" else "뮤사이(한투)",
        }

        # 보유 주식 (output1 배열)
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
        
        # 예수금 (output2 배열)
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
        """해외 잔고 응답 파싱 → 표준 포맷"""
        result = []
        
        # 보유 주식
        for stock in data.get("output1", []):
            qty = safe_int(stock.get("ccld_qty_smtl1"))
            if qty > 0:
                currency = stock.get("buy_crcy_cd") or "USD"
                
                # avg_unpr3를 우선 사용 (실제 평단가)
                avg_buy_price = safe_float(stock.get("avg_unpr3"))
                if avg_buy_price == 0:
                    # avg_unpr3가 없으면 pchs_avg_pric 시도
                    avg_buy_price = safe_float(stock.get("pchs_avg_pric"))
                
                # 수익률 계산 (API 응답값 우선, 없으면 계산)
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
        
        # 외화 예수금
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
        # account_prefix를 생성하여 BaseAPI에 전달
        super().__init__(app_key, app_secret, "KIWOOM_C")
        self.account_no = account_no

    def _extract_token(self, data: dict) -> Optional[str]:
        """키움은 'token' 필드 사용"""
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
        
        # expires_dt (YYYYMMDDHHMMSS) 파싱
        expires_at = now_kst().timestamp() + 1800  # 기본 30분
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
        """국내 잔고 조회 (일별잔고수익률 api-id: ka01690) → 표준 포맷 반환"""
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
        
        # 원문 저장 (디버깅용)
        raw_file = DIR_PATH / f"kiwoom_domestic_{body['qry_dt']}.json"
        with open(raw_file, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, ensure_ascii=False, indent=2)
        print(f"[키움 원문] {raw_file}")
        
        return self._parse_domestic(raw_data)

# stock.py 파일의 KiwoomAPI 클래스 안에 아래 코드로 덮어쓰세요.

    @staticmethod
    def _parse_domestic(data: dict) -> List[dict]:
        """키움 국내 잔고 파싱 → 표준 포맷"""
        result = []
        
        if data.get("return_code") != 0:
            print(f"[키움 오류] {data.get('return_msg')}")
            return result
        
        # 보유 주식 (day_bal_rt 배열)
        for stock in data.get("day_bal_rt", []):
            qty = safe_int(stock.get("rmnd_qty"))
            if qty > 0:
                # --- 수정된 부분 ---
                # 불필요한 인코딩 로직을 제거하고 API 응답값을 그대로 사용합니다.
                name = stock.get("stk_nm", stock.get("stk_cd", "알 수 없음"))
                # -----------------
                
                result.append({
                    "broker": "키움증권",
                    "account_type": "법인",
                    "account_label": "뮤사이(키움)", # 또는 "KIWOOM(C)"
                    "market": "domestic",
                    "asset_type": "stock",
                    "ticker": stock.get("stk_cd"),
                    "name": name, # 수정된 name 변수 사용
                    "quantity": qty,
                    "avg_buy_price": safe_float(stock.get("buy_uv")),
                    "current_price": safe_float(stock.get("cur_prc")),
                    "eval_amount": safe_int(stock.get("evlt_amt")),
                    "profit_loss": safe_int(stock.get("evltv_prft")),
                    "profit_rate": safe_float(stock.get("prft_rt")),
                    "currency": "KRW"
                })
        
        # 예수금 (dbst_bal)
        dbst_bal = safe_int(data.get("dbst_bal"))
        if dbst_bal > 0:
            result.append({
                "broker": "키움증권",
                "account_type": "법인",
                "account_label": "뮤사이(키움)", # 또는 "KIWOOM(C)"
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
    """환경변수 또는 Streamlit Secrets에서 계정 정보 로드 (수정된 버전)"""
    key_suffix = "HANTOO" if broker == "kis" else "KIWOOM"
    
    app_key_name = f"{prefix}_{key_suffix}_APP_KEY"
    app_secret_name = f"{prefix}_{key_suffix}_APP_SECRET"
    account_no_name = f"{prefix}_{key_suffix}_ACCOUNT_NO"
    
    # Streamlit Cloud 환경인지 확인하여 Secrets 우선 사용
    if hasattr(st, 'secrets') and all(k in st.secrets for k in [app_key_name, app_secret_name, account_no_name]):
        print(f"[{prefix}] Streamlit Secrets에서 설정 로드 중...")
        app_key = st.secrets[app_key_name]
        app_secret = st.secrets[app_secret_name]
        account_no = st.secrets[account_no_name]
    else:
        # 로컬 환경에서는 기존 방식(.env)으로 값을 가져옴
        print(f"[{prefix}] 로컬 .env 파일에서 설정 로드 중...")
        app_key = os.getenv(app_key_name)
        app_secret = os.getenv(app_secret_name)
        account_no = os.getenv(account_no_name)

    if all([app_key, app_secret, account_no]):
        return {"app_key": app_key, "app_secret": app_secret, "account_no": account_no, "prefix": prefix, "broker": broker}
    
    print(f"[{prefix}] {broker} 계정 설정을 찾을 수 없습니다.")
    return None
# stock.py 파일의 맨 아래 main 함수와 그 주변 부분을 아래 코드로 교체하세요.

# ... (파일 상단의 KISApi, KiwoomAPI 클래스 등은 그대로 둡니다) ...

def collect_all_assets():
    """
    모든 증권사 API를 호출하여 통합된 자산 목록을 반환하는 함수.
    """
    all_assets = []
    
    # 한국투자증권 (개인, 법인)
    for prefix in ["P", "C"]:
        config = load_account_config(prefix, "kis")
        if not config:
            continue
        
        # --- 수정: token_file 인자 제거 및 account_prefix 이름 변경 ---
        api = KISApi(
            config["app_key"], 
            config["app_secret"], 
            config["account_no"], 
            prefix # account_type을 prefix로 전달
        )
        # ----------------------------------------------------
        
        # --- 수정: api.base_asset_info 대신 직접 라벨 생성 및 api.account_type 사용 ---
        label = f"한국투자증권({'개인' if api.account_type == 'P' else '법인'})"
        print(f"[{label}] 데이터 수집 중...")
        # ----------------------------------------------------------------

        all_assets.extend(api.get_domestic_balance())
        all_assets.extend(api.get_overseas_balance())
    
    # 키움증권 (법인)
    kiw_config = load_account_config("C", "kiwoom")
    if kiw_config:
        api = KiwoomAPI(
            kiw_config["app_key"], 
            kiw_config["app_secret"],
            kiw_config["account_no"]
        )

        # --- 수정: api.base_asset_info 대신 직접 라벨 생성 ---
        print(f"[키움증권(법인)] 데이터 수집 중...")
        # -----------------------------------------------
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