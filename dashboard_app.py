import streamlit as st
from datetime import datetime, timedelta, timezone
import boto3

KST = timezone(timedelta(hours=9))

st.set_page_config(layout="wide", page_title="통합 포트폴리오 대시보드")


@st.cache_data(ttl=timedelta(minutes=10))
def get_password_from_aws():
    """AWS Parameter Store에서 비밀번호를 가져오기 (캐시 적용)."""
    try:
        ssm = boto3.client("ssm", region_name="ap-northeast-2")
        response = ssm.get_parameter(
            Name="/stock-dashboard/DASHBOARD_PASSWORD",
            WithDecryption=True
        )
        return response["Parameter"]["Value"]
    except Exception as e:
        st.error(f"비밀번호를 불러올 수 없습니다: {e}")
        return None


@st.cache_resource
def load_chart_dependencies():
    """인증 이후에만 무거운 시각화 라이브러리를 로드."""
    import pandas as pd
    import plotly.express as px
    import plotly.graph_objects as go

    try:
        from streamlit_plotly_events import plotly_events as _plotly_events
        plotly_events_available = True
    except ImportError:
        plotly_events_available = False

        def _plotly_events(fig, key=None, click_event=True, **kwargs):
            return []

    return pd, px, go, _plotly_events, plotly_events_available


def check_password():
    """AWS Parameter Store에서 비밀번호를 가져와 인증"""
    
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

pd, px, go, plotly_events, PLOTLY_EVENTS_AVAILABLE = load_chart_dependencies()

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

@st.cache_data(ttl=timedelta(minutes=5))
def load_data():
    import os
    from stock import collect_all_assets
    from currency_api import get_exchange_rates

    # 키움증권 데이터 건너뛰기 옵션 (필요시)
    skip_kiwoom = os.getenv("SKIP_KIWOOM", "false").lower() == "true"
    
    # 1. 데이터 수집
    assets_list = collect_all_assets(skip_kiwoom=skip_kiwoom)
    last_updated = datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S')
    
    if not assets_list:
        st.error("API로부터 자산 정보를 가져오는 데 실패했습니다.")
        return pd.DataFrame(), {}, None, ""

    df = pd.DataFrame(assets_list)

    # 2. 환율 정보 가져오기
    symbols_in_data = df['currency'].unique().tolist()
    rates, last_update_time = get_exchange_rates(symbols=symbols_in_data, base_currency='KRW')

    if not rates:
        st.warning("실시간 환율을 가져올 수 없어 기본 환율을 적용합니다.")
        rates = {'KRW': 1, 'USD': 0.000724, 'HKD': 0.005545}
    
    exchange_rates_to_krw = {s: 1 / r if r != 0 else 0 for s, r in rates.items()}
    exchange_rates_to_krw['KRW'] = 1
    
    # 3. [핵심 수정] 모든 계산을 강제로 실수형(float)으로 변환하여 수행
    # 이렇게 해야 '문자열'로 인식되어 합계가 안 구해지는 문제를 막을 수 있습니다.
    df['eval_amount_krw'] = df.apply(lambda r: float(r['eval_amount']) * exchange_rates_to_krw.get(r['currency'], 1), axis=1)
    df['profit_loss_krw'] = df.apply(lambda r: float(r['profit_loss']) * exchange_rates_to_krw.get(r['currency'], 1), axis=1)
    
    df['principal_krw'] = df.apply(
        lambda r: (float(r['avg_buy_price']) * float(r['quantity']) * exchange_rates_to_krw.get(r['currency'], 1))
        if r['asset_type'] == 'stock' and float(r['avg_buy_price']) > 0 else (r['eval_amount_krw'] - r['profit_loss_krw']),
        axis=1
    )
    df.loc[df['asset_type'] == 'cash', 'principal_krw'] = df['eval_amount_krw']
    
    # 4. [안전 장치] pandas의 숫자 변환 함수로 한 번 더 확실하게 처리
    df['eval_amount_krw'] = pd.to_numeric(df['eval_amount_krw'], errors='coerce').fillna(0)
    
    # 5. 국가 정보 추가 (차트용)
    def get_country(row):
        if row['market'] == 'domestic':
            return '🇰🇷 대한민국'
        elif row['currency'] == 'USD':
            return '🇺🇸 미국'
        elif row['currency'] == 'HKD':
            return '🇭🇰 홍콩'
        else:
            return '기타'
            
    df['country'] = df.apply(get_country, axis=1)
    
    return df, exchange_rates_to_krw, last_update_time, last_updated


def _months_between(start_date, end_date):
    if end_date < start_date:
        return 0
    return (end_date.year - start_date.year) * 12 + (end_date.month - start_date.month)


def calculate_copyright_amortization(
    report_date,
    copyright_cost=251_420_000,
    purchase_date=datetime(2021, 4, 1, tzinfo=KST),
    useful_life_years=10,
):
    life_months = useful_life_years * 12
    monthly_amortization = copyright_cost / life_months
    elapsed_months = min(life_months, _months_between(purchase_date.date(), report_date))
    accumulated = monthly_amortization * elapsed_months
    net_book_value = max(0, copyright_cost - accumulated)
    return {
        "copyright_cost": copyright_cost,
        "monthly_amortization": monthly_amortization,
        "elapsed_months": elapsed_months,
        "accumulated_amortization": accumulated,
        "net_book_value": net_book_value,
    }


def calculate_kosme_loan_schedule(report_date, principal=100_000_000, annual_rate=0.025, amort_months=36, repayment_start_date=None):
    if repayment_start_date is None:
        repayment_start_date = datetime(report_date.year, 1, 1).date()
    monthly_principal = principal / amort_months if amort_months else 0
    paid_months = min(amort_months, max(0, _months_between(repayment_start_date, report_date)))
    repaid_principal = monthly_principal * paid_months
    remaining_principal = max(0, principal - repaid_principal)
    monthly_interest = remaining_principal * annual_rate / 12
    return {
        "monthly_principal": monthly_principal,
        "paid_months": paid_months,
        "remaining_principal": remaining_principal,
        "monthly_interest": monthly_interest,
    }


def calculate_twr(daily_nav_df, cashflow_df):
    if daily_nav_df.empty:
        return pd.DataFrame(), 0.0

    nav_df = daily_nav_df.copy()
    nav_df["date"] = pd.to_datetime(nav_df["date"]).dt.date
    nav_df = nav_df.sort_values("date").reset_index(drop=True)

    flow_df = cashflow_df.copy()
    if not flow_df.empty:
        flow_df["date"] = pd.to_datetime(flow_df["date"]).dt.date
        flow_by_date = flow_df.groupby("date")["amount_krw"].sum().to_dict()
    else:
        flow_by_date = {}

    records = []
    cumulative = 1.0
    prev_nav = None
    for _, row in nav_df.iterrows():
        nav = float(row["total_nav_krw"])
        d = row["date"]
        flow = float(flow_by_date.get(d, 0))
        if prev_nav is None or prev_nav <= 0:
            daily_ret = 0.0
        else:
            daily_ret = (nav - prev_nav - flow) / prev_nav
            cumulative *= (1 + daily_ret)
        records.append({
            "date": d,
            "total_nav_krw": nav,
            "external_flow_krw": flow,
            "daily_return": daily_ret,
            "cumulative_return": cumulative - 1,
        })
        prev_nav = nav

    result_df = pd.DataFrame(records)
    return result_df, (cumulative - 1)


def get_simple_benchmark_return(index_name, start_date, end_date):
    symbol_map = {
        "KOSPI": "^KS11",
        "NASDAQ": "^IXIC",
    }
    symbol = symbol_map.get(index_name)
    if not symbol:
        return None
    try:
        import urllib.parse
        encoded = urllib.parse.quote(symbol, safe="")
        url = f"https://stooq.com/q/d/l/?s={encoded}&i=d"
        price_df = pd.read_csv(url)
        price_df["Date"] = pd.to_datetime(price_df["Date"]).dt.date
        window = price_df[(price_df["Date"] >= start_date) & (price_df["Date"] <= end_date)].sort_values("Date")
        if len(window) < 2:
            return None
        start_close = float(window.iloc[0]["Close"])
        end_close = float(window.iloc[-1]["Close"])
        if start_close <= 0:
            return None
        return (end_close / start_close) - 1
    except Exception:
        return None



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

    tab1, tab2, tab3 = st.tabs(["📊 포트폴리오 현황", "🏢 법인 재무현황", "📈 통합 NAV/벤치마크"])
    
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
                st.plotly_chart(fig, width='stretch')

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
                st.plotly_chart(fig, width='stretch')

        with col_chart3:
            stock_only_df = filtered_df[
                (filtered_df['asset_type'] == 'stock') & 
                (filtered_df['market'].notna())
            ].copy()
            
            if not stock_only_df.empty:
                # 1. 데이터 타입 안전 변환
                stock_only_df['eval_amount_krw'] = pd.to_numeric(stock_only_df['eval_amount_krw'], errors='coerce').fillna(0)
                
                # 2. market 값을 정규화 (공백 제거, 소문자 변환)
                stock_only_df['market'] = stock_only_df['market'].astype(str).str.strip().str.lower()
                
                # 3. 유효한 market 값만 필터링 (domestic 또는 overseas만 허용)
                valid_markets = ['domestic', 'overseas']
                stock_only_df = stock_only_df[stock_only_df['market'].isin(valid_markets)]
                
                if not stock_only_df.empty:
                    # 4. market별로 직접 합계 계산 - 원본 데이터 그대로 사용
                    # 중복 없이 정확히 계산하기 위해 groupby 사용 (같은 종목이 여러 계좌에 있어도 각각 계산)
                    domestic_total = stock_only_df[stock_only_df['market'] == 'domestic']['eval_amount_krw'].sum()
                    overseas_total = stock_only_df[stock_only_df['market'] == 'overseas']['eval_amount_krw'].sum()
                    
                    # 디버깅: 실제 계산 값 확인
                    total_all = domestic_total + overseas_total
                    
                    # 값이 제대로 계산되었는지 검증
                    domestic_total = float(domestic_total) if not pd.isna(domestic_total) else 0.0
                    overseas_total = float(overseas_total) if not pd.isna(overseas_total) else 0.0
                    
                    # 5. 차트용 데이터프레임 직접 생성 (0보다 큰 값만)
                    market_data = []
                    if domestic_total > 0:
                        market_data.append({
                            'market': 'domestic', 
                            'market_label': '국내', 
                            'eval_amount_krw': float(domestic_total)
                        })
                    if overseas_total > 0:
                        market_data.append({
                            'market': 'overseas', 
                            'market_label': '해외', 
                            'eval_amount_krw': float(overseas_total)
                        })
                    
                    if market_data:
                        market_summary = pd.DataFrame(market_data)
                        
                        # 값이 제대로 설정되었는지 확인
                        market_summary['eval_amount_krw'] = pd.to_numeric(market_summary['eval_amount_krw'], errors='coerce').fillna(0)
                        
                        # 디버깅: 실제 계산된 값 확인 (개발 중에만)
                        # st.write(f"디버그 - 국내: {domestic_total:,.0f}, 해외: {overseas_total:,.0f}")
                        
                        # 6. 색상 맵 정의
                        market_colors_map = {
                            '국내': '#003478',
                            '해외': '#B22234'
                        }
                        
                        # 7. 차트 생성 - go.Figure로 직접 생성하여 값 전달 문제 해결
                        labels = market_summary['market_label'].tolist()
                        values = market_summary['eval_amount_krw'].tolist()
                        colors = [market_colors_map.get(label, '#808080') for label in labels]
                        
                        # 값이 제대로 숫자인지 확인
                        values = [float(v) for v in values]
                        
                        fig = go.Figure(data=[go.Pie(
                            labels=labels,
                            values=values,
                            hole=0.35,
                            marker=dict(colors=colors),
                            textposition='inside',
                            texttemplate='<b>%{label}</b><br>%{percent}',
                            textfont=dict(size=12, family='Arial'),
                            hovertemplate='<b>%{label}</b><br>평가금액: ₩%{value:,.0f}<br>비중: %{percent}<extra></extra>'
                        )])
                        
                        fig.update_layout(
                            title={
                                'text': '국내/해외 비중',
                                'font': {'color': 'white'}
                            },
                            height=450,
                            showlegend=True,
                            legend=dict(
                                orientation="h",
                                yanchor="top",
                                y=-0.15,
                                xanchor="center",
                                x=0.5,
                                font=dict(size=10, color='white')
                            ),
                            margin=dict(l=10, r=10, t=50, b=80),
                            paper_bgcolor='rgba(0,0,0,0)',
                            plot_bgcolor='rgba(0,0,0,0)',
                            font=dict(color='white')
                        )
                        
                        # 8. 클릭 이벤트 처리
                        if PLOTLY_EVENTS_AVAILABLE:
                            selected_points = plotly_events(
                                fig,
                                click_event=True,
                                hover_event=False,
                                select_event=False,
                                key="market_pie_chart",
                                override_height=450
                            )
                            
                            if selected_points and len(selected_points) > 0:
                                if 'pointNumber' in selected_points[0]:
                                    point_index = selected_points[0]['pointNumber']
                                    # 직접 만든 데이터프레임에서 market 값 가져오기
                                    selected_market = market_summary.iloc[point_index]['market']
                                    
                                    if st.session_state.get('selected_market') != selected_market:
                                        st.session_state['selected_market'] = selected_market
                                        st.rerun()
                        else:
                            st.plotly_chart(fig, width='stretch')
                    else:
                        st.info("표시할 데이터가 없습니다.")
                else:
                    st.info("유효한 주식 데이터가 없습니다.")
            else:
                st.warning("주식 데이터가 없습니다.")

        # 선택된 market의 종목 구성 표시
        if 'selected_market' in st.session_state:
            st.markdown("---")
            selected_market = st.session_state['selected_market']
            market_name = '국내' if selected_market == 'domestic' else '해외'
            
            st.subheader(f"📊 {market_name} 종목 구성")
            
            selected_market_stocks = filtered_df[
                (filtered_df['market'] == selected_market) & 
                (filtered_df['asset_type'] == 'stock')
            ].copy()
            
            if not selected_market_stocks.empty:
                top_stocks = selected_market_stocks.nlargest(10, 'eval_amount_krw').copy()
                top_stocks['display_name'] = top_stocks['name']
                
                # 파이 차트 색상
                if selected_market == 'domestic':
                    pie_colors = ['#003478', '#0047AB', '#4169E1', '#5B9BD5', '#6FA8DC',
                                 '#93C5FD', '#A8DADC', '#B4D7E8', '#C9E4F7', '#DBEAFE']
                else:
                    pie_colors = ['#B22234', '#DC143C', '#E63946', '#F08080', '#FA8072',
                                 '#FFB6C1', '#FFC0CB', '#FFD1DC', '#FFE4E1', '#FFF0F5']
                
                # 계좌별 색상 매핑
                account_color_map = {}
                for account in filtered_df['account_label'].unique():
                    if '조현익' in account:
                        account_color_map[account] = '#c7b273'
                    elif '뮤사이' in account:
                        if '키움' in account:
                            account_color_map[account] = '#BFBFBF'
                        elif '한투' in account:
                            account_color_map[account] = '#E5E5E5'
                        else:
                            account_color_map[account] = '#D3D3D3'
                    else:
                        account_color_map[account] = '#1f77b4'
                
                col1, col2 = st.columns([1, 1])
                
                with col1:
                    fig_detail = px.pie(
                        top_stocks, 
                        names='display_name', 
                        values='eval_amount_krw',
                        title=f'{market_name} Top 10 종목',
                        hole=0.35,
                        color_discrete_sequence=pie_colors
                    )
                    fig_detail.update_traces(
                        textposition='inside',
                        texttemplate='<b>%{label}</b><br>%{percent}',
                        textfont=dict(size=12, family='Arial')
                    )
                    fig_detail.update_layout(
                        height=500,
                        showlegend=True,
                        legend=dict(
                            orientation="v",
                            yanchor="middle",
                            y=0.5,
                            xanchor="left",
                            x=1.05,
                            font=dict(size=10, family='Arial')
                        )
                    )
                    st.plotly_chart(fig_detail, width='stretch')
                
                with col2:
                    fig_bar = go.Figure()
                    
                    for idx, row in top_stocks.sort_values('eval_amount_krw', ascending=True).iterrows():
                        stock_name = row['display_name']
                        stock_detail = selected_market_stocks[
                            selected_market_stocks['name'] == row['name']
                        ]
                        
                        for _, detail_row in stock_detail.iterrows():
                            account = detail_row['account_label']
                            amount = detail_row['eval_amount_krw']
                            
                            fig_bar.add_trace(go.Bar(
                                y=[stock_name],
                                x=[amount],
                                name=account,
                                orientation='h',
                                marker=dict(color=account_color_map.get(account, '#1f77b4')),
                                text=f'₩{amount:,.0f}',
                                textposition='inside',
                                textfont=dict(size=10),
                                hovertemplate=f'<b>{account}</b><br>₩{amount:,.0f}<extra></extra>',
                                showlegend=True if idx == top_stocks.index[0] else False,
                                legendgroup=account
                            ))
                    
                    fig_bar.update_layout(
                        title=f'{market_name} Top 10 평가금액 (계좌별)',
                        height=500,
                        barmode='stack',
                        xaxis_title="평가금액 (원)",
                        yaxis_title="",
                        showlegend=True,
                        legend=dict(
                            title="계좌",
                            orientation="v",
                            yanchor="top",
                            y=1,
                            xanchor="left",
                            x=1.05,
                            font=dict(size=9, family='Arial')
                        ),
                        margin=dict(l=10, r=150, t=50, b=50)
                    )
                    
                    st.plotly_chart(fig_bar, width='stretch')
                
                st.markdown("#### 📋 상세 내역")
                detail_table = selected_market_stocks.copy()
                detail_table = detail_table.sort_values('eval_amount_krw', ascending=False)
                
                detail_table['종목명'] = detail_table['name']
                detail_table['티커'] = detail_table['ticker']
                detail_table['계좌'] = detail_table['account_label']
                detail_table['평가금액'] = detail_table['eval_amount_krw'].apply(lambda x: f"₩{x:,.0f}")
                detail_table['비중(%)'] = (detail_table['eval_amount_krw'] / detail_table['eval_amount_krw'].sum() * 100).apply(lambda x: f"{x:.2f}%")
                detail_table['수익률(%)'] = detail_table.apply(
                    lambda row: f"{(row['profit_loss_krw'] / (row['eval_amount_krw'] - row['profit_loss_krw']) * 100):+.2f}%" 
                    if (row['eval_amount_krw'] - row['profit_loss_krw']) > 0 else "0.00%",
                    axis=1
                )
                
                st.dataframe(
                    detail_table[['종목명', '티커', '계좌', '평가금액', '비중(%)', '수익률(%)']],
                    hide_index=True,
                    width='stretch'
                )
                
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
                        width='stretch'
                    )
        
        st.markdown("---")
        st.subheader("📈 전체 종목 요약")
        
        if not stock_only.empty:
            stock_summary = stock_only.groupby(['ticker', 'name', 'currency']).agg({
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
            display_summary['통화'] = display_summary['currency']
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
                '통화': '',
                '수량': f"{int(stock_summary['quantity'].sum()):,}",
                '투자원금': f"₩{total_stock_principal:,.0f}",
                '평가금액': f"₩{total_stock_eval:,.0f}",
                '손익': f"+₩{total_stock_pl:,.0f}" if total_stock_pl >= 0 else f"-₩{abs(total_stock_pl):,.0f}",
                '수익률(%)': f"{total_stock_rate:+.1f}%",
                '비중(%)': '100.0%'
            }])
            
            display_summary_with_total = pd.concat([
                display_summary[['종목명', '티커', '통화', '수량', '투자원금', '평가금액', '손익', '수익률(%)', '비중(%)']],
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
                width='stretch'
            )
            
            csv = display_summary.to_csv(index=False).encode('utf-8-sig')
            st.download_button(
                label="📥 CSV 다운로드",
                data=csv,
                file_name=f"portfolio_summary_{datetime.now().strftime('%Y%m%d')}.csv",
                mime="text/csv"
            )
        
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
                        width='stretch'
                    )

    with tab2:
        st.subheader("🏢 뮤사이(법인) 재무현황")
        st.caption("재무회계 기준의 실시간 점검용 약식 재무상태표/손익계산서입니다. 자동 수집 + 스케줄 계산 + 선택적 수동수정 모드를 제공합니다.")
        from stock import get_kis_collateral_loan_balance

        report_date = st.date_input("기준일", value=datetime.now(KST).date(), key="corp_report_date")
        musai_corp_df = df[df["account_label"].str.contains("뮤사이", na=False)].copy()
        musai_securities_krw = musai_corp_df[musai_corp_df["asset_type"].isin(["stock", "cash"])]["eval_amount_krw"].sum()
        kis_loan_result = get_kis_collateral_loan_balance(prefix="C")
        kis_collateral_loan_krw = float(kis_loan_result.get("loan_balance", 0.0))

        st.markdown("### 1) 재무상태표 핵심 항목")
        amort = calculate_copyright_amortization(report_date=report_date)

        col_a, col_b, col_c, col_d = st.columns(4)
        col_a.metric("저작권 원가", f"₩{amort['copyright_cost']:,.0f}")
        col_b.metric("월 상각액", f"₩{amort['monthly_amortization']:,.0f}")
        col_c.metric("감가상각 누계액", f"₩{amort['accumulated_amortization']:,.0f}")
        col_d.metric("무형자산(순액)", f"₩{amort['net_book_value']:,.0f}")

        st.metric("단기매매증권(키움+한투 주식/예수금 합산)", f"₩{musai_securities_krw:,.0f}")
        if kis_loan_result.get("success"):
            st.success(f"한투 담보대출 API 조회 성공: ₩{kis_collateral_loan_krw:,.0f}")
        else:
            st.warning("한투 담보대출 API 자동조회 실패(또는 필드 미확인). 필요시 아래 수동 수정 모드에서 보정하세요.")

        st.markdown("### 2) 단기차입금/차입 구조")
        edit_mode = st.toggle("수동 수정 모드 (특정 값 직접 보정)", value=False)
        col_l1, col_l2, col_l3 = st.columns(3)
        related_party_principal = col_l1.number_input("특수관계인 차입금 원금", min_value=0, value=300_000_000, step=10_000_000)
        related_party_rate = col_l2.number_input("특수관계인 차입 이자율(%)", min_value=0.0, value=4.6, step=0.1)
        rcps_rate = col_l3.number_input("RCPS 이자율(%)", min_value=0.0, value=2.0, step=0.1)

        col_l4, col_l5, col_l6, col_l7 = st.columns(4)
        rcps_1_principal = col_l4.number_input("RCPS 조합1 원금", min_value=0, value=180_000_000, step=10_000_000)
        rcps_2_principal = col_l5.number_input("RCPS 조합2 원금", min_value=0, value=262_800_000, step=10_000_000)
        kibo_principal = col_l6.number_input("기보 대출 원금", min_value=0, value=70_000_000, step=5_000_000)
        kibo_rate = col_l7.number_input("기보 대출 금리(%)", min_value=0.0, value=3.25, step=0.05)

        col_l8, col_l9, col_l10 = st.columns(3)
        kosme_principal = col_l8.number_input("중진공 대출 최초 원금", min_value=0, value=100_000_000, step=10_000_000)
        kosme_rate = col_l9.number_input("중진공 대출 금리(%)", min_value=0.0, value=2.5, step=0.1)
        kosme_start = col_l10.date_input("중진공 상환 시작일", value=datetime(report_date.year, 1, 1).date(), key="kosme_start")
        kis_collateral_loan_input = st.number_input(
            "한투 증권담보대출(수동 입력/보정)",
            min_value=0,
            value=int(kis_collateral_loan_krw),
            step=10_000_000,
            disabled=not edit_mode
        )
        if edit_mode:
            kis_collateral_loan_krw = float(kis_collateral_loan_input)
        collateral_loan_rate = st.number_input("증권담보대출 금리(%)", min_value=0.0, value=5.5, step=0.1)

        kosme = calculate_kosme_loan_schedule(
            report_date=report_date,
            principal=kosme_principal,
            annual_rate=kosme_rate / 100,
            amort_months=36,
            repayment_start_date=kosme_start,
        )

        related_party_interest_annual = related_party_principal * (related_party_rate / 100)
        rcps_1_interest_annual = rcps_1_principal * (rcps_rate / 100)
        rcps_2_interest_annual = rcps_2_principal * (rcps_rate / 100)
        kibo_interest_monthly = kibo_principal * (kibo_rate / 100) / 12
        collateral_interest_annual = kis_collateral_loan_krw * (collateral_loan_rate / 100)

        total_liabilities = (
            related_party_principal
            + kibo_principal
            + kosme["remaining_principal"]
            + rcps_1_principal
            + rcps_2_principal
            + kis_collateral_loan_krw
        )
        total_debt_for_weight = max(total_liabilities, 1)
        weighted_avg_rate = (
            (related_party_principal * (related_party_rate / 100))
            + (kibo_principal * (kibo_rate / 100))
            + (kosme["remaining_principal"] * (kosme_rate / 100))
            + (rcps_1_principal * (rcps_rate / 100))
            + (rcps_2_principal * (rcps_rate / 100))
            + (kis_collateral_loan_krw * (collateral_loan_rate / 100))
        ) / total_debt_for_weight * 100

        total_assets_est = musai_securities_krw + amort["net_book_value"]
        equity_est = total_assets_est - total_liabilities

        st.markdown("#### 차입/이자 계산 요약")
        debt_df = pd.DataFrame([
            {"구분": "특수관계인 차입금", "원금": related_party_principal, "금리(연%)": related_party_rate, "연간 이자": related_party_interest_annual, "월 이자(참고)": related_party_interest_annual / 12, "비고": "매년 3월말 지급"},
            {"구분": "RCPS 조합1", "원금": rcps_1_principal, "금리(연%)": rcps_rate, "연간 이자": rcps_1_interest_annual, "월 이자(참고)": rcps_1_interest_annual / 12, "비고": "만기일시(2027-12)"},
            {"구분": "RCPS 조합2", "원금": rcps_2_principal, "금리(연%)": rcps_rate, "연간 이자": rcps_2_interest_annual, "월 이자(참고)": rcps_2_interest_annual / 12, "비고": "만기일시(2028-12)"},
            {"구분": "기보 보증서 대출", "원금": kibo_principal, "금리(연%)": kibo_rate, "연간 이자": kibo_principal * (kibo_rate / 100), "월 이자(참고)": kibo_interest_monthly, "비고": "변동금리/월 납부"},
            {"구분": "중진공 대출", "원금": kosme['remaining_principal'], "금리(연%)": kosme_rate, "연간 이자": kosme['remaining_principal'] * (kosme_rate / 100), "월 이자(참고)": kosme['monthly_interest'], "비고": f"월 원금상환 {kosme['monthly_principal']:,.0f}원"},
            {"구분": "한투 증권담보대출", "원금": kis_collateral_loan_krw, "금리(연%)": collateral_loan_rate, "연간 이자": collateral_interest_annual, "월 이자(참고)": collateral_interest_annual / 12, "비고": "자동조회+수동보정"},
        ])
        st.dataframe(
            debt_df.style.format({"원금": "₩{:,.0f}", "금리(연%)": "{:.2f}", "연간 이자": "₩{:,.0f}", "월 이자(참고)": "₩{:,.0f}"}),
            hide_index=True,
            width='stretch',
        )

        bs_col1, bs_col2, bs_col3, bs_col4 = st.columns(4)
        bs_col1.metric("추정 총자산", f"₩{total_assets_est:,.0f}")
        bs_col2.metric("추정 부채총계", f"₩{total_liabilities:,.0f}")
        bs_col3.metric("가중평균 차입금리", f"{weighted_avg_rate:.2f}%")
        bs_col4.metric("추정 자본(순자산)", f"₩{equity_est:,.0f}")

        st.markdown("#### 📘 약식 재무상태표")
        bs_table = pd.DataFrame([
            {"구분": "자산", "항목": "단기매매증권(주식+예수금)", "금액": musai_securities_krw},
            {"구분": "자산", "항목": "무형자산(저작권 순액)", "금액": amort["net_book_value"]},
            {"구분": "자산", "항목": "자산총계", "금액": total_assets_est},
            {"구분": "부채", "항목": "특수관계인 차입금", "금액": related_party_principal},
            {"구분": "부채", "항목": "기보 대출", "금액": kibo_principal},
            {"구분": "부채", "항목": "중진공 대출(잔액)", "금액": kosme["remaining_principal"]},
            {"구분": "부채", "항목": "RCPS 조합1", "금액": rcps_1_principal},
            {"구분": "부채", "항목": "RCPS 조합2", "금액": rcps_2_principal},
            {"구분": "부채", "항목": "한투 증권담보대출", "금액": kis_collateral_loan_krw},
            {"구분": "부채", "항목": "부채총계", "금액": total_liabilities},
            {"구분": "자본", "항목": "자본(자산-부채)", "금액": equity_est},
        ])
        st.dataframe(bs_table.style.format({"금액": "₩{:,.0f}"}), hide_index=True, width='stretch')

        st.markdown("### 3) 손익계산서 핵심 항목(초기 빌드)")
        st.caption("매출/영업외수익/영업외비용 중 자동 수집이 어려운 값은 우선 수기 입력으로 시작합니다.")

        col_p1, col_p2 = st.columns(2)
        with col_p1:
            st.markdown("#### 매출")
            sales_csv = st.file_uploader("월별 매출 CSV 업로드 (columns: month,sales)", type=["csv"], key="sales_csv")
            manual_sales = st.number_input("당기 누적 매출(수기 입력)", min_value=0, value=32_376_011, step=1_000_000)
        with col_p2:
            st.markdown("#### 영업외 손익")
            interest_income = st.number_input("이자수익", min_value=0, value=9_635_962, step=100_000)
            dividend_income = st.number_input("배당금수익", min_value=0, value=11_743_963, step=100_000)
            sec_gain = st.number_input("단기매매증권처분이익", min_value=0, value=88_348_971, step=100_000)
            sec_loss = st.number_input("단기매매증권처분손실", min_value=0, value=34_899_317, step=100_000)
            interest_expense = st.number_input("이자비용", min_value=0, value=20_465_270, step=100_000)

        if sales_csv is not None:
            sales_df = pd.read_csv(sales_csv)
            if {"month", "sales"}.issubset(set(sales_df.columns)):
                recognized_sales = pd.to_numeric(sales_df["sales"], errors="coerce").fillna(0).sum()
                st.success(f"CSV 기준 누적 매출 합계: ₩{recognized_sales:,.0f}")
            else:
                st.warning("매출 CSV는 month,sales 컬럼이 필요합니다. 수기 입력값을 사용합니다.")
                recognized_sales = manual_sales
        else:
            recognized_sales = manual_sales

        operating_expense = st.number_input("판매비와관리비(누적)", min_value=0, value=72_326_973, step=500_000)
        operating_profit = recognized_sales - operating_expense
        non_operating_income = interest_income + dividend_income + sec_gain
        non_operating_expense = interest_expense + sec_loss
        pretax_income = operating_profit + non_operating_income - non_operating_expense

        pl_df = pd.DataFrame([
            {"항목": "매출액", "금액": recognized_sales},
            {"항목": "판매비와관리비", "금액": operating_expense},
            {"항목": "영업손익", "금액": operating_profit},
            {"항목": "이자수익", "금액": interest_income},
            {"항목": "배당금수익", "금액": dividend_income},
            {"항목": "단기매매증권처분이익", "금액": sec_gain},
            {"항목": "이자비용", "금액": -interest_expense},
            {"항목": "단기매매증권처분손실", "금액": -sec_loss},
            {"항목": "법인세차감전이익(추정)", "금액": pretax_income},
        ])
        st.dataframe(pl_df.style.format({"금액": "₩{:,.0f}"}), hide_index=True, width='stretch')

        st.info(
            "Google Spreadsheet 연동 가이드: "
            "1) 서비스계정 이메일을 시트 공유자(뷰어 이상)로 추가 "
            "2) 서버에 서비스계정 JSON을 안전하게 저장(또는 st.secrets) "
            "3) gspread로 시트 열기 후 I열 월매출을 DataFrame으로 집계 "
            "4) 집계값을 매출액에 바인딩 (실패 시 현재처럼 CSV/수기 fallback)."
        )

    with tab3:
        st.subheader("📈 통합 NAV 및 벤치마크 비교")
        st.caption("입출금(외부 현금흐름)을 제외한 TWR(Time-Weighted Return) 기반 NAV 수익률을 계산합니다.")

        st.markdown("#### 1) 입력 데이터")
        nav_file = st.file_uploader(
            "일별 NAV CSV (columns: date,total_nav_krw,domestic_nav_krw,overseas_nav_krw)",
            type=["csv"],
            key="nav_daily_csv",
        )
        flow_file = st.file_uploader(
            "현금흐름 CSV (columns: date,amount_krw,flow_type,scope) / flow_type: deposit|withdrawal",
            type=["csv"],
            key="nav_flow_csv",
        )
        st.caption("※ 외화 환전은 외부 입출금이 아닌 내부 이동으로 간주하여 cashflow CSV에서 제외하세요.")

        if nav_file is None:
            st.warning("NAV 계산을 위해 일별 NAV CSV 업로드가 필요합니다.")
        else:
            nav_df = pd.read_csv(nav_file)
            required_nav_cols = {"date", "total_nav_krw"}
            if not required_nav_cols.issubset(set(nav_df.columns)):
                st.error("NAV CSV에는 최소 date,total_nav_krw 컬럼이 필요합니다.")
            else:
                flow_df = pd.DataFrame(columns=["date", "amount_krw"])
                if flow_file is not None:
                    raw_flow = pd.read_csv(flow_file)
                    if {"date", "amount_krw", "flow_type"}.issubset(set(raw_flow.columns)):
                        raw_flow["amount_krw"] = pd.to_numeric(raw_flow["amount_krw"], errors="coerce").fillna(0)
                        raw_flow["flow_type"] = raw_flow["flow_type"].astype(str).str.lower().str.strip()
                        raw_flow["amount_krw"] = raw_flow.apply(
                            lambda r: abs(r["amount_krw"]) if r["flow_type"] == "deposit" else -abs(r["amount_krw"]),
                            axis=1,
                        )
                        flow_df = raw_flow[["date", "amount_krw"]]
                    else:
                        st.warning("현금흐름 CSV 형식이 맞지 않아 외부흐름 0으로 계산합니다.")

                twr_series_df, twr_total = calculate_twr(nav_df[["date", "total_nav_krw"]], flow_df)
                if twr_series_df.empty:
                    st.warning("계산 가능한 NAV 데이터가 없습니다.")
                else:
                    start_date = twr_series_df["date"].min()
                    end_date = twr_series_df["date"].max()

                    col_n1, col_n2, col_n3 = st.columns(3)
                    col_n1.metric("기간", f"{start_date} ~ {end_date}")
                    col_n2.metric("최종 NAV", f"₩{twr_series_df.iloc[-1]['total_nav_krw']:,.0f}")
                    col_n3.metric("TWR 수익률", f"{twr_total:+.2%}")

                    fig_nav = px.line(
                        twr_series_df,
                        x="date",
                        y="cumulative_return",
                        title="누적 NAV 수익률(TWR, 외부 입출금 제외)",
                    )
                    fig_nav.update_yaxes(tickformat=".2%")
                    st.plotly_chart(fig_nav, width='stretch')

                    st.markdown("#### 2) 벤치마크 비교")
                    kospi_ret = get_simple_benchmark_return("KOSPI", start_date, end_date)
                    nasdaq_ret = get_simple_benchmark_return("NASDAQ", start_date, end_date)

                    if kospi_ret is None:
                        kospi_ret = st.number_input("KOSPI 기간 수익률(수동입력, %)", value=0.0, step=0.1) / 100
                    if nasdaq_ret is None:
                        nasdaq_ret = st.number_input("NASDAQ 기간 수익률(수동입력, %)", value=0.0, step=0.1) / 100

                    benchmark_df = pd.DataFrame([
                        {"비교대상": "통합 NAV(TWR)", "수익률": twr_total},
                        {"비교대상": "KOSPI", "수익률": kospi_ret},
                        {"비교대상": "NASDAQ", "수익률": nasdaq_ret},
                    ])
                    st.dataframe(
                        benchmark_df.style.format({"수익률": "{:+.2%}"}),
                        hide_index=True,
                        width='stretch',
                    )

else:
    st.header("⚠️ 데이터를 로드하는 데 실패했습니다.")
    st.info("API 연결을 확인하거나 잠시 후 다시 시도해주세요.")
