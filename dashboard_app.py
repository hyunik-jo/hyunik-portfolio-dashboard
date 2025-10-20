from pathlib import Path
import streamlit as st
import pandas as pd
import plotly.express as px
from datetime import datetime, timedelta, timezone
from stock import collect_all_assets
import boto3

KST = timezone(timedelta(hours=9))

try:
    from currency_api import get_exchange_rates
except ImportError:
    st.error("`currency_api.py` 파일을 찾을 수 없습니다.")
    st.stop()

# 비밀번호 확인 함수
def check_password():
    """AWS Parameter Store에서 비밀번호를 가져와 인증"""
    
    def get_password_from_aws():
        """AWS Parameter Store에서 비밀번호 가져오기"""
        try:
            ssm = boto3.client('ssm', region_name='ap-northeast-2')
            response = ssm.get_parameter(
                Name='/stock-dashboard/DASHBOARD_PASSWORD',
                WithDecryption=True
            )
            return response['Parameter']['Value']
        except Exception as e:
            st.error(f"비밀번호를 불러올 수 없습니다: {e}")
            return None
    
    def password_entered():
        """비밀번호 입력 확인"""
        correct_password = get_password_from_aws()
        if correct_password and st.session_state["password"] == correct_password:
            st.session_state["password_correct"] = True
            del st.session_state["password"]  # 보안을 위해 삭제
        else:
            st.session_state["password_correct"] = False

    # 이미 인증됨
    if st.session_state.get("password_correct", False):
        return True

    # 로그인 화면
    st.markdown("""
    <style>
        .login-container {
            max-width: 400px;
            margin: 100px auto;
            padding: 40px;
            background: #1E1E1E;
            border-radius: 10px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
        }
        .login-title {
            text-align: center;
            font-size: 2rem;
            margin-bottom: 30px;
        }
    </style>
    """, unsafe_allow_html=True)
    
    st.markdown('<div class="login-container">', unsafe_allow_html=True)
    st.markdown('<div class="login-title">🔒 포트폴리오 대시보드</div>', unsafe_allow_html=True)
    
    st.text_input(
        "비밀번호를 입력하세요",
        type="password",
        on_change=password_entered,
        key="password",
        placeholder="비밀번호 입력"
    )
    
    if "password_correct" in st.session_state and not st.session_state["password_correct"]:
        st.error("❌ 비밀번호가 틀렸습니다.")
    
    st.markdown('</div>', unsafe_allow_html=True)
    
    return False

# 비밀번호 확인
if not check_password():
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
    import os
    skip_kiwoom = os.getenv("SKIP_KIWOOM", "false").lower() == "true"
    
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

# 새로고침 버튼
col_title, col_refresh = st.columns([4, 1])
with col_title:
    st.title("💼 통합 포트폴리오 대시보드")
with col_refresh:
    if st.button("🔄 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

df, exchange_rates, rates_updated_time, portfolio_last_updated = load_data()

if not df.empty:
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
            
            fig = px.pie(account_summary, names='account_label', values='eval_amount_krw', 
                        title='계좌별 비중', hole=0.35,
                        color='account_label',
                        color_discrete_map=color_map)
            fig.update_traces(
                textposition='inside', 
                texttemplate='<b>%{label}</b><br>%{percent}',
                textfont=dict(size=14, family='Arial')
            )
            fig.update_layout(
                height=600, 
                showlegend=True, 
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.1,
                    xanchor="center",
                    x=0.5,
                    font=dict(size=12)
                ),
                margin=dict(l=20, r=20, t=60, b=100)
            )
            st.plotly_chart(fig, use_container_width=True)

    with col_chart2:
        stock_df = filtered_df[filtered_df['asset_type']=='stock']
        if not stock_df.empty:
            top_stocks = stock_df.nlargest(10, 'eval_amount_krw').copy()
            
            top_stocks['display_name'] = top_stocks.apply(
                lambda row: row['name'] if row['market'] == 'domestic' else row['ticker'], 
                axis=1
            )
            
            stock_colors = [
                '#8B9DC3', '#A8B5C7', '#9CA8B8', '#B8C5D6', '#9EAAB5',
                '#C9D6E3', '#7B8FA3', '#A6B4C4', '#BCC9D8', '#8C9CAD'
            ]
            
            fig = px.pie(top_stocks, names='display_name', values='eval_amount_krw', 
                        title='종목별 비중 (Top 10)', hole=0.35,
                        color_discrete_sequence=stock_colors)
            fig.update_traces(
                textposition='inside', 
                texttemplate='<b>%{label}</b><br>%{percent}',
                textfont=dict(size=14, family='Arial')
            )
            fig.update_layout(
                height=600, 
                showlegend=True, 
                legend=dict(
                    orientation="h",
                    yanchor="top",
                    y=-0.1,
                    xanchor="center",
                    x=0.5,
                    font=dict(size=12)
                ),
                margin=dict(l=20, r=20, t=60, b=100)
            )
            st.plotly_chart(fig, use_container_width=True)

    # 계좌별 상세 보유 현황
    st.markdown("---")
    st.subheader("📋 계좌별 상세 보유 현황")
    
    stock_only = filtered_df[filtered_df['asset_type'] == 'stock'].copy()
    
    if not stock_only.empty:
        for account_label in sorted(stock_only['account_label'].unique()):
            account_stocks = stock_only[stock_only['account_label'] == account_label]
            
            account_eval = account_stocks['eval_amount_krw'].sum()
            account_principal = account_stocks['principal_krw'].sum()
            account_pl = account_eval - account_principal
            account_pl_rate = (account_pl / account_principal * 100) if account_principal > 0 else 0
            
            pl_display = f"+₩{account_pl:,.0f}" if account_pl >= 0 else f"-₩{abs(account_pl):,.0f}"
            rate_display = f"{account_pl_rate:+.1f}%"
            
            if account_pl < 0:
                expander_title = f"**{account_label}** | 평가: ₩{account_eval:,.0f} | 손익: :red[{pl_display}] (:red[{rate_display}])"
            else:
                expander_title = f"**{account_label}** | 평가: ₩{account_eval:,.0f} | 손익: {pl_display} ({rate_display})"
            
            with st.expander(expander_title, expanded=False):
                display_stocks = account_stocks[['name', 'ticker', 'quantity', 'avg_buy_price', 
                                                 'current_price', 'principal_krw', 'eval_amount_krw', 
                                                 'profit_loss_krw']].copy()
                
                display_stocks['weight'] = (display_stocks['eval_amount_krw'] / account_eval * 100).round(1)
                display_stocks['profit_rate'] = (
                    (display_stocks['profit_loss_krw'] / display_stocks['principal_krw'] * 100)
                    .fillna(0).round(1)
                )
                
                display_stocks['종목명'] = display_stocks['name']
                display_stocks['티커'] = display_stocks['ticker']
                display_stocks['수량'] = display_stocks['quantity'].apply(lambda x: f"{int(x):,}")
                display_stocks['평단가'] = display_stocks['avg_buy_price'].apply(lambda x: f"{x:,.2f}")
                display_stocks['현재가'] = display_stocks['current_price'].apply(lambda x: f"{x:,.2f}")
                display_stocks['투자원금'] = display_stocks['principal_krw'].apply(lambda x: f"₩{x:,.0f}")
                display_stocks['평가금액'] = display_stocks['eval_amount_krw'].apply(lambda x: f"₩{x:,.0f}")
                display_stocks['손익'] = display_stocks['profit_loss_krw'].apply(
                    lambda x: f"+₩{x:,.0f}" if x >= 0 else f"-₩{abs(x):,.0f}"
                )
                display_stocks['수익률(%)'] = display_stocks['profit_rate'].apply(lambda x: f"{x:+.1f}%")
                display_stocks['비중(%)'] = display_stocks['weight'].apply(lambda x: f"{x:.1f}%")
                
                total_principal_sum = display_stocks['principal_krw'].sum()
                total_eval_sum = display_stocks['eval_amount_krw'].sum()
                total_pl_sum = display_stocks['profit_loss_krw'].sum()
                
                total_row = pd.DataFrame([{
                    '종목명': '**합계**',
                    '티커': '',
                    '수량': '',
                    '평단가': '',
                    '현재가': '',
                    '투자원금': f"₩{total_principal_sum:,.0f}",
                    '평가금액': f"₩{total_eval_sum:,.0f}",
                    '손익': f"+₩{total_pl_sum:,.0f}" if total_pl_sum >= 0 else f"-₩{abs(total_pl_sum):,.0f}",
                    '수익률(%)': f"{account_pl_rate:+.1f}%",
                    '비중(%)': '100.0%'
                }])
                
                display_with_total = pd.concat([
                    display_stocks[['종목명', '티커', '수량', '평단가', '현재가', 
                                   '투자원금', '평가금액', '손익', '수익률(%)', '비중(%)']],
                    total_row
                ], ignore_index=True)
                
                def highlight_negative(val):
                    if isinstance(val, str):
                        if '-₩' in val or (val.startswith('-') and '%' in val):
                            return 'color: #FF4B4B'
                    return ''
                
                styled_df = display_with_total.style.map(highlight_negative, subset=['손익', '수익률(%)'])
                
                st.dataframe(
                    styled_df,
                    hide_index=True,
                    use_container_width=True
                )
    
    # 전체 종목 요약
    st.markdown("---")
    st.subheader("📈 전체 종목 요약")
    
    if not stock_only.empty:
        stock_summary = stock_only.groupby(['ticker', 'name']).agg({
            'eval_amount_krw': 'sum',
            'principal_krw': 'sum',
            'quantity': 'sum'
        }).reset_index()
        
        stock_summary['profit_loss_krw'] = stock_summary['eval_amount_krw'] - stock_summary['principal_krw']
        stock_summary['profit_rate'] = (
            (stock_summary['profit_loss_krw'] / stock_summary['principal_krw'] * 100)
            .fillna(0).round(1)
        )
        stock_summary['weight'] = (stock_summary['eval_amount_krw'] / stock_summary['eval_amount_krw'].sum() * 100).round(1)
        
        stock_summary = stock_summary.sort_values('eval_amount_krw', ascending=False).reset_index(drop=True)
        
        display_summary = stock_summary.copy()
        display_summary['종목명'] = display_summary['name']
        display_summary['티커'] = display_summary['ticker']
        display_summary['수량'] = display_summary['quantity'].apply(lambda x: f"{int(x):,}")
        display_summary['투자원금'] = display_summary['principal_krw'].apply(lambda x: f"₩{x:,.0f}")
        display_summary['평가금액'] = display_summary['eval_amount_krw'].apply(lambda x: f"₩{x:,.0f}")
        display_summary['손익'] = display_summary['profit_loss_krw'].apply(
            lambda x: f"+₩{x:,.0f}" if x >= 0 else f"-₩{abs(x):,.0f}"
        )
        display_summary['수익률(%)'] = display_summary['profit_rate'].apply(lambda x: f"{x:+.1f}%")
        display_summary['비중(%)'] = display_summary['weight'].apply(lambda x: f"{x:.1f}%")
        
        total_stock_principal = stock_summary['principal_krw'].sum()
        total_stock_eval = stock_summary['eval_amount_krw'].sum()
        total_stock_pl = total_stock_eval - total_stock_principal
        total_stock_rate = (total_stock_pl / total_stock_principal * 100) if total_stock_principal > 0 else 0
        
        total_row_summary = pd.DataFrame([{
            '종목명': '**합계**',
            '티커': '',
            '수량': f"{int(stock_summary['quantity'].sum()):,}",
            '투자원금': f"₩{total_stock_principal:,.0f}",
            '평가금액': f"₩{total_stock_eval:,.0f}",
            '손익': f"+₩{total_stock_pl:,.0f}" if total_stock_pl >= 0 else f"-₩{abs(total_stock_pl):,.0f}",
            '수익률(%)': f"{total_stock_rate:+.1f}%",
            '비중(%)': '100.0%'
        }])
        
        display_summary_with_total = pd.concat([
            display_summary[['종목명', '티커', '수량', '투자원금', '평가금액', '손익', '수익률(%)', '비중(%)']],
            total_row_summary
        ], ignore_index=True)
        
        def highlight_negative(val):
            if isinstance(val, str):
                if '-₩' in val or (val.startswith('-') and '%' in val):
                    return 'color: #FF4B4B'
            return ''
        
        styled_summary = display_summary_with_total.style.map(highlight_negative, subset=['손익', '수익률(%)'])
        
        st.dataframe(
            styled_summary,
            hide_index=True,
            use_container_width=True
        )
        
        csv = display_summary.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 CSV 다운로드",
            data=csv,
            file_name=f"portfolio_summary_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )
    
    # 예수금
    st.markdown("---")
    st.subheader("💰 예수금 현황")
    
    cash_df = filtered_df[filtered_df['asset_type'] == 'cash'].copy()
    if not cash_df.empty:
        account_cash_summary = cash_df.groupby('account_label')['eval_amount_krw'].sum().reset_index()
        account_cash_summary = account_cash_summary.sort_values('eval_amount_krw', ascending=False)
        
        for _, row in account_cash_summary.iterrows():
            account = row['account_label']
            account_total_krw = row['eval_amount_krw']
            
            account_cash_detail = cash_df[cash_df['account_label'] == account].copy()
            
            with st.expander(f"**{account}** | 총 예수금: ₩{account_total_krw:,.0f}", expanded=False):
                detail_display = account_cash_detail[['currency', 'eval_amount', 'eval_amount_krw']].copy()
                detail_display['통화'] = detail_display['currency']
                detail_display['보유액'] = detail_display.apply(
                    lambda r: f"{r['currency']} {r['eval_amount']:,.2f}", axis=1
                )
                detail_display['원화환산'] = detail_display['eval_amount_krw'].apply(lambda x: f"₩{x:,.0f}")
                
                st.dataframe(
                    detail_display[['통화', '보유액', '원화환산']],
                    hide_index=True,
                    use_container_width=True
                )

else:
    st.header("⚠️ 데이터를 로드하는 데 실패했습니다.")
    st.info("API 연결을 확인하거나 잠시 후 다시 시도해주세요.")  