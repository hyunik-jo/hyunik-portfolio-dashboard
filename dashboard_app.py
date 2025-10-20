from pathlib import Path
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, timezone
from stock import collect_all_assets

KST = timezone(timedelta(hours=9))

try:
    from currency_api import get_exchange_rates
except ImportError:
    st.error("`currency_api.py` 파일을 찾을 수 없습니다.")
    st.stop()

# 페이지 설정
st.set_page_config(layout="wide", page_title="통합 포트폴리오 대시보드")

# 커스텀 CSS
st.markdown("""
<style>
    .main-metric { font-size: 2.5rem; font-weight: bold; }
    .negative-text { color: #FF4B4B; }
    @media (max-width: 768px) {
        .main-metric { font-size: 1.8rem; }
    }
</style>
""", unsafe_allow_html=True)

DIR_PATH = Path(__file__).resolve().parent

# 데이터 로딩
@st.cache_data(ttl=timedelta(minutes=5))
def load_data() -> tuple[pd.DataFrame, dict, datetime | None, str]:
    """
    stock.py를 직접 실행하여 실시간 자산 데이터를 가져오고,
    환율 정보를 적용하여 데이터프레임으로 반환합니다.
    """
    assets_list = collect_all_assets()
    last_updated = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    
    if not assets_list:
        st.error("API로부터 자산 정보를 가져오는 데 실패했습니다.")
        return pd.DataFrame(), {}, None, ""

    df = pd.DataFrame(assets_list)

    symbols_in_data = df['currency'].unique().tolist()
    rates, last_update_time = get_exchange_rates(symbols=symbols_in_data, base_currency='KRW')

    if not rates:
        st.warning("실시간 환율을 가져올 수 없어 기본 환율을 적용합니다.")
        rates = {'KRW': 1, 'USD': 0.000724, 'HKD': 0.005545}
    
    exchange_rates_to_krw = {s: 1 / r if r != 0 else 0 for s, r in rates.items()}
    exchange_rates_to_krw['KRW'] = 1
    
    df['eval_amount_krw'] = df.apply(lambda r: r['eval_amount'] * exchange_rates_to_krw.get(r['currency'], 1), axis=1)
    df['profit_loss_krw'] = df.apply(lambda r: r['profit_loss'] * exchange_rates_to_krw.get(r['currency'], 1), axis=1)
    
    df['principal_krw'] = df.apply(
        lambda r: (r['avg_buy_price'] * r['quantity'] * exchange_rates_to_krw.get(r['currency'], 1))
        if r['asset_type'] == 'stock' and r['avg_buy_price'] > 0 else (r['eval_amount_krw'] - r['profit_loss_krw']),
        axis=1
    )
    df.loc[df['asset_type'] == 'cash', 'principal_krw'] = df['eval_amount_krw']
    
    return df, exchange_rates_to_krw, last_update_time, last_updated

df, exchange_rates, rates_updated_time, portfolio_last_updated = load_data()

if not df.empty:
    st.title("💼 통합 포트폴리오 대시보드")
    
    if portfolio_last_updated:
        st.caption(f"📅 포트폴리오 최종 조회: {portfolio_last_updated}")

    # 사이드바
    st.sidebar.header("필터 옵션")
    account_list = ['전체'] + sorted(df['account_label'].unique().tolist())
    selected_account = st.sidebar.selectbox('계좌 선택', account_list)
    
    st.sidebar.markdown("---")
    st.sidebar.subheader("현재 환율")
    if rates_updated_time:
        st.sidebar.caption(f"업데이트: {rates_updated_time.strftime('%Y-%m-%d %H:%M')}")
    
    if exchange_rates:
        for currency, rate_to_krw in sorted(exchange_rates.items()):
            if currency != 'KRW':
                st.sidebar.metric(f"{currency}/KRW", f"{rate_to_krw:,.2f}원")

    # 데이터 필터링
    filtered_df = df if selected_account == '전체' else df[df['account_label'] == selected_account]

    # 총 자산 요약
    st.subheader("📊 총 자산 요약")
    
    total_eval_krw = filtered_df['eval_amount_krw'].sum()
    total_principal_krw = filtered_df['principal_krw'].sum()
    total_pl_krw = total_eval_krw - total_principal_krw
    total_return_rate = (total_pl_krw / total_principal_krw * 100) if total_principal_krw else 0
    total_cash_krw = filtered_df[filtered_df['asset_type'] == 'cash']['eval_amount_krw'].sum()

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("총 평가액", f"₩{total_eval_krw:,.0f}")
    col2.metric("투자 원금", f"₩{total_principal_krw:,.0f}")
    
    pl_color = "normal" if total_pl_krw >= 0 else "inverse"
    col3.metric("총 손익", f"₩{total_pl_krw:,.0f}", delta=f"{total_return_rate:+.1f}%", delta_color=pl_color)
    col4.metric("수익률", f"{total_return_rate:+.1f}%")
    col5.metric("예수금", f"₩{total_cash_krw:,.0f}")

    # 포트폴리오 구성
    st.subheader("🎯 포트폴리오 구성")
    col_chart1, col_chart2 = st.columns(2)

    with col_chart1:
        if not filtered_df.empty:
            account_summary = filtered_df.groupby('account_label')['eval_amount_krw'].sum().reset_index()
            
            color_map = {}
            for account in account_summary['account_label']:
                if '조현익' in account:
                    color_map[account] = '#c7b273'
                elif '뮤사이' in account:
                    if '키움' in account:
                        color_map[account] = '#BFBFBF'
                    elif '한투' in account:
                        color_map[account] = '#E5E5E5'
                    else:
                        color_map[account] = '#D3D3D3'
                else:
                    color_map[account] = None