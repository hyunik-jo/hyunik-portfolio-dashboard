import streamlit as st
from datetime import datetime, timedelta, timezone
import boto3
import hmac

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
        password_input = st.session_state.get("password", "")
        if correct_password and hmac.compare_digest(password_input, correct_password):
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


def highlight_negative_style(val):
    if isinstance(val, str):
        if "-₩" in val or (val.startswith("-") and "%" in val):
            return "color: #FF4B4B"
    return ""



st.title("💼 통합 포트폴리오 대시보드")

col1, col2, col3 = st.columns([5, 1, 0.5])
with col2:
    if st.button("🔄", help="데이터 새로고침"):
        load_data.clear()
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
                            st.plotly_chart(fig, use_container_width=True)
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
                    st.plotly_chart(fig_detail, use_container_width=True)
                
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
                    
                    st.plotly_chart(fig_bar, use_container_width=True)
                
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
                    use_container_width=True
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
                    
                    styled_df = display_with_total.style.map(highlight_negative_style, subset=['손익', '수익률(%)'])
                    
                    st.dataframe(
                        styled_df,
                        hide_index=True,
                        use_container_width=True
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
            
            styled_summary = display_summary_with_total.style.map(highlight_negative_style, subset=['손익', '수익률(%)'])
            
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

    with tab2:
        st.subheader("📈 투자 성과 분석")
        st.info("🚧 준비 중입니다. 곧 벤치마크 대비 수익률 분석 기능이 추가됩니다.")
        
        st.markdown("""
        ### 📋 구현 예정 기능
        
        1. **수익률 비교**
           - 내 포트폴리오 vs KOSPI
           - 내 포트폴리오 vs NASDAQ
           - 내 포트폴리오 vs S&P 500
        
        2. **기간별 성과**
           - 1개월, 3개월, 6개월, 1년, 전체
           - 월별/분기별 수익률 추이
        
        3. **리스크 지표**
           - 변동성 (표준편차)
           - 샤프 비율
           - 최대 낙폭 (MDD)
        
        4. **거래 내역 분석**
           - 입출금 내역 제외
           - 순수 투자 수익률 계산
           - 매매 손익 분석
        """)
        
        st.markdown("---")
        st.subheader("현재 포트폴리오 기본 통계")
        
        col1, col2, col3, col4 = st.columns(4)
        
        total_eval = df['eval_amount_krw'].sum()
        total_principal = df['principal_krw'].sum()
        total_return = ((total_eval - total_principal) / total_principal * 100) if total_principal > 0 else 0
        stock_count = len(df[df['asset_type'] == 'stock'])
        
        col1.metric("보유 종목 수", f"{stock_count}개")
        col2.metric("총 투자 원금", f"₩{total_principal:,.0f}")
        col3.metric("총 평가 금액", f"₩{total_eval:,.0f}")
        col4.metric("누적 수익률", f"{total_return:+.2f}%")

else:
    st.header("⚠️ 데이터를 로드하는 데 실패했습니다.")
    st.info("API 연결을 확인하거나 잠시 후 다시 시도해주세요.")
