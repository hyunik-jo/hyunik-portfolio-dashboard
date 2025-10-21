from pathlib import Path
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta, timezone
from stock import collect_all_assets
import boto3

# streamlit-plotly-events 라이브러리를 optional로 임포트
try:
    from streamlit_plotly_events import plotly_events
    PLOTLY_EVENTS_AVAILABLE = True
except ImportError:
    PLOTLY_EVENTS_AVAILABLE = False
    def plotly_events(fig, key=None, click_event=True):
        return []

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
            del st.session_state["password"]
        else:
            st.session_state["password_correct"] = False

    if st.session_state.get("password_correct", False):
        return True

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

if not check_password():
    st.stop()

st.set_page_config(layout="wide", page_title="통합 포트폴리오 대시보드")

st.markdown("""
<style>
    .main-metric { font-size: 2.5rem; font-weight: bold; }
    .negative-text { color: #FF4B4B; }
    
    @media (max-width: 1024px) and (min-width: 769px) {
        .main-metric { font-size: 1.5rem; }
        [data-testid="stMetricValue"] {
            font-size: 1.2rem !important;
        }
        [data-testid="stMetricLabel"] {
            font-size: 0.9rem !important;
        }
        [data-testid="stMetricDelta"] {
            font-size: 0.85rem !important;
        }
    }
    
    @media (max-width: 768px) {
        .main-metric { font-size: 1.8rem; }
        [data-testid="stMetricValue"] {
            font-size: 1rem !important;
        }
        [data-testid="stMetricLabel"] {
            font-size: 0.8rem !important;
        }
        [data-testid="stMetricDelta"] {
            font-size: 0.75rem !important;
        }
    }
    
    @media (max-width: 1024px) {
        .js-plotly-plot .plotly .gtitle {
            font-size: 14px !important;
        }
    }
</style>
""", unsafe_allow_html=True)

DIR_PATH = Path(__file__).resolve().parent

@st.cache_data(ttl=timedelta(minutes=5))
def load_data() -> tuple[pd.DataFrame, dict, datetime | None, str]:
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

st.title("💼 통합 포트폴리오 대시보드")

col1, col2, col3 = st.columns([5, 1, 0.5])
with col2:
    if st.button("🔄", help="데이터 새로고침"):
        st.cache_data.clear()
        st.rerun()

df, exchange_rates, rates_updated_time, portfolio_last_updated = load_data()

if not df.empty:
    if portfolio_last_updated:
        st.caption(f"📅 포트폴리오 최종 조회: {portfolio_last_updated}")

    tab1, tab2 = st.tabs(["📊 포트폴리오 현황", "📈 성과 분석"])
    
    with tab1:
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

        filtered_df = df if selected_account == '전체' else df[df['account_label'] == selected_account]

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

        st.subheader("🎯 포트폴리오 구성")
        
        col_chart1, col_chart2, col_chart3 = st.columns(3)

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
                    textfont=dict(size=12, family='Arial')
                )
                fig.update_layout(
                    height=450, 
                    showlegend=True, 
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.15,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=10)
                    ),
                    margin=dict(l=10, r=10, t=50, b=80)
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
                    textfont=dict(size=12, family='Arial')
                )
                fig.update_layout(
                    height=450, 
                    showlegend=True, 
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.15,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=10)
                    ),
                    margin=dict(l=10, r=10, t=50, b=80)
                )
                st.plotly_chart(fig, use_container_width=True)

        with col_chart3:
            # stock만 필터링
            stock_only_df = filtered_df[
                (filtered_df['asset_type'] == 'stock') & 
                (filtered_df['market'].notna())
            ].copy()
            
            if not stock_only_df.empty:
                # market 기준으로 그룹화
                market_summary = stock_only_df.groupby('market', as_index=False)['eval_amount_krw'].sum()
                
                # 라벨 매핑
                market_label_map = {
                    'domestic': '국내',
                    'overseas': '해외'
                }
                market_summary['market_label'] = market_summary['market'].map(market_label_map)
                
                # 색상 맵
                market_colors_map = {
                    'domestic': '#003478',
                    'overseas': '#B22234'
                }
                
                # 차트 생성
                fig = px.pie(
                    market_summary, 
                    names='market_label', 
                    values='eval_amount_krw',
                    title='국내/해외 비중',
                    hole=0.35,
                    color='market',
                    color_discrete_map=market_colors_map
                )
                
                fig.update_traces(
                    textposition='inside',
                    texttemplate='<b>%{label}</b><br>%{percent}',
                    textfont=dict(size=14, family='Arial')
                )
                
                fig.update_layout(
                    height=450,
                    showlegend=True,
                    legend=dict(
                        orientation="h",
                        yanchor="top",
                        y=-0.15,
                        xanchor="center",
                        x=0.5,
                        font=dict(size=10)
                    ),
                    margin=dict(l=10, r=10, t=50, b=80)
                )
                
                # plotly_events 사용
                if PLOTLY_EVENTS_AVAILABLE:
                    selected_points = plotly_events(
                        fig, 
                        click_event=True,
                        hover_event=False,
                        select_event=False,
                        key="market_pie_chart",
                        override_height=450
                    )
                    
                    # 클릭 이벤트 처리
                    if selected_points and len(selected_points) > 0:
                        if 'pointNumber' in selected_points[0]:
                            point_index = selected_points[0]['pointNumber']
                            selected_market = market_summary.iloc[point_index]['market']
                            
                            if st.session_state.get('selected_market') != selected_market:
                                st.session_state['selected_market'] = selected_market
                                st.rerun()
                else:
                    st.plotly_chart(fig, use_container_width=True)
            else:
                st.warning("주식 데이터가 없습니다.")

        # 선택된 market의 종목 구성 표시
        if 'selected_market' in st.session_state:
            st.markdown("---")
            selected_market = st.session_state['selected_market']
            market_name = '국내' if selected_market == 'domestic' else '해외'
            
            st.subheader(f"📊 {market_name} 종목 구성")
            
            # 선택된 market의 주식만 필터링
            selected_market_stocks = filtered_df[
                (filtered_df['market'] == selected_market) & 
                (filtered_df['asset_type'] == 'stock')
            ].copy()
            
            if not selected_market_stocks.empty:
                # Top 10 종목 선택
                top_stocks = selected_market_stocks.nlargest(10, 'eval_amount_krw').copy()
                
                # 표시용 이름 설정
                top_stocks['display_name'] = top_stocks['name']
                
                # 색상 설정
                if selected_market == 'domestic':
                    colors = ['#003478', '#0047AB', '#4169E1', '#5B9BD5', '#6FA8DC',
                             '#93C5FD', '#A8DADC', '#B4D7E8', '#C9E4F7', '#DBEAFE']
                else:
                    colors = ['#B22234', '#DC143C', '#E63946', '#F08080', '#FA8072',
                             '#FFB6C1', '#FFC0CB', '#FFD1DC', '#FFE4E1', '#FFF0F5']
                
                col1, col2 = st.columns(2)
                
                with col1:
                    # 파이 차트
                    fig_detail = px.pie(
                        top_stocks, 
                        names='display_name', 
                        values='eval_amount_krw',
                        title=f'{market_name} Top 10 종목',
                        hole=0.35,
                        color_discrete_sequence=colors
                    )
                    fig_detail.update_traces(
                        textposition='inside',
                        texttemplate='<b>%{label}</b><br>%{percent}',
                        textfont=dict(size=11, family='Arial')
                    )
                    fig_detail.update_layout(
                        height=400,
                        showlegend=True,
                        legend=dict(
                            orientation="v",
                            yanchor="middle",
                            y=0.5,
                            xanchor="left",
                            x=1.05,
                            font=dict(size=9)
                        )
                    )
                    st.plotly_chart(fig_detail, use_container_width=True)
                
                with col2:
                    # 바 차트
                    fig_bar = px.bar(
                        top_stocks.sort_values('eval_amount_krw', ascending=True),
                        y='display_name',
                        x='eval_amount_krw',
                        orientation='h',
                        title=f'{market_name} Top 10 평가금액',
                        color='eval_amount_krw',
                        color_continuous_scale=['#FFE4E1', '#B22234'] if selected_market == 'overseas' else ['#DBEAFE', '#003478']
                    )
                    fig_bar.update_layout(
                        height=400,
                        showlegend=False,
                        xaxis_title="평가금액 (원)",
                        yaxis_title="",
                        coloraxis_showscale=False
                    )
                    fig_bar.update_traces(
                        text=top_stocks.sort_values('eval_amount_krw', ascending=True)['eval_amount_krw'].apply(lambda x: f'₩{x:,.0f}'),
                        textposition='outside'
                    )
                    st.plotly_chart(fig_bar, use_container_width=True)
                
                # 상세 테이블
                st.markdown("#### 📋 상세 내역")
                detail_table = selected_market_stocks.copy()
                detail_table = detail_table.sort_values('eval_amount_krw', ascending=False)
                
                detail_table['종목명'] = detail_table['name']
                detail_table['티커'] = detail_table['ticker']
                detail_table['평가금액'] = detail_table['eval_amount_krw'].apply(lambda x: f"₩{x:,.0f}")
                detail_table['비중(%)'] = (detail_table['eval_amount_krw'] / detail_table['eval_amount_krw'].sum() * 100).apply(lambda x: f"{x:.2f}%")
                detail_table['수익률(%)'] = detail_table.apply(
                    lambda row: f"{(row['profit_loss_krw'] / (row['eval_amount_krw'] - row['profit_loss_krw']) * 100):+.2f}%" 
                    if (row['eval_amount_krw'] - row['profit_loss_krw']) > 0 else "0.00%",
                    axis=1
                )
                
                st.dataframe(
                    detail_table[['종목명', '티커', '평가금액', '비중(%)', '수익률(%)']],
                    hide_index=True,
                    use_container_width=True
                )
                
                # 선택 해제 버튼
                if st.button("🔙 전체 보기로 돌아가기"):
                    del st.session_state['selected_market']
                    st.rerun()
            else:
                st.info(f"{market_name}에 보유 중인 주식이 없습니다.")

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
                    expander_title = f"**{account_label}** | 평가: ₩{account_eval:,.0f} | 손익: :red[{pl_display