import requests
import streamlit as st
from datetime import datetime, timedelta, timezone
import logging

logging.getLogger('streamlit').setLevel(logging.ERROR)
KST = timezone(timedelta(hours=9))

@st.cache_data(ttl=timedelta(minutes=10))
def get_exchange_rates(symbols: list, base_currency: str = 'KRW') -> tuple[dict | None, datetime | None]:
    """
    실시간 환율 정보와 최종 업데이트 시간을 API로부터 가져옵니다.
    반환값: (환율 딕셔너리, 업데이트 시간 datetime 객체)
    """
    url = f"https://open.er-api.com/v6/latest/{base_currency}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        
        if data.get("result") == "success":
            all_rates = data['rates']
            last_update_unix = data.get("time_last_update_unix")
            last_update_dt = datetime.fromtimestamp(last_update_unix, tz=KST) if last_update_unix else None

            required_symbols = set(symbols)
            required_symbols.add('USD') # 비교를 위해 USD는 항상 포함
            required_symbols.add(base_currency)
            
            filtered_rates = {
                symbol: rate 
                for symbol, rate in all_rates.items() 
                if symbol in required_symbols
            }
            print(f"환율 정보 API 호출 성공! (기준: {base_currency})")
            return filtered_rates, last_update_dt
        else:
            st.error("환율 정보를 가져오는 데 실패했습니다.")
            return None, None
    except requests.exceptions.RequestException as e:
        st.error(f"환율 API 호출 중 오류 발생: {e}")
        return None, None