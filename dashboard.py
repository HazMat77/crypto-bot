"""
Bot Dashboard — Streamlit
==========================
Live monitoring dashboard for the trading bot.

Run with:
    streamlit run dashboard.py

Features:
  - Live pool balance per exchange
  - Open positions with unrealised P&L
  - Today's trades and P&L
  - Monthly summary
  - News sentiment scores per coin
  - Correlation matrix
  - Backtest results viewer
  - Config editor

Install Streamlit:
    pip install streamlit plotly
"""

import sys
import json
import glob
from pathlib import Path
from datetime import datetime, date

import bootstrap
bootstrap.ensure_installed(optional=True)  # pandas, plotly (streamlit itself must
                                            # already be present — it's what launched this file)

import pandas as pd

# ── Check Streamlit is installed ──────────────────────────────────────────
try:
    import streamlit as st
    import plotly.graph_objects as go
    import plotly.express as px
except ImportError:
    print("Install dashboard dependencies: pip install streamlit plotly")
    print("Then run: streamlit run dashboard.py")
    sys.exit(1)

# ── Page config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="CryptoTradingBot",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .metric-card {
        background: #1e2130; border-radius: 8px;
        padding: 12px 16px; margin: 4px 0;
    }
    .pos { color: #1D9E75; font-weight: 600; }
    .neg { color: #D85A30; font-weight: 600; }
    .neutral { color: #888780; }
    div[data-testid="stMetricValue"] { font-size: 1.4rem; }
</style>
""", unsafe_allow_html=True)


# ══════════════════════════════════════════════════════════════════════════════
#  DATA LOADERS
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=30)
def load_today_logs():
    """Load today's log file and parse trade/signal lines."""
    today    = date.today().strftime("%Y%m%d")
    log_path = f"logs/bot_{today}.log"
    if not Path(log_path).exists():
        # Try most recent log
        logs = sorted(glob.glob("logs/bot_*.log"), reverse=True)
        if not logs:
            return [], []
        log_path = logs[0]

    trades  = []
    signals = []
    try:
        with open(log_path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                if "SELL" in line and "Pool" in line:
                    trades.append(line.strip())
                elif "BUY SIGNAL" in line or "SELL SIGNAL" in line:
                    signals.append(line.strip())
    except Exception:
        pass
    return trades, signals


@st.cache_data(ttl=30)
def load_backtest_results():
    """Load any backtest CSV files in current directory."""
    results = {}
    for csv_path in glob.glob("backtest_*.csv"):
        try:
            symbol  = csv_path.replace("backtest_", "").replace(".csv", "").replace("_", "-")
            df      = pd.read_csv(csv_path)
            results[symbol] = df
        except Exception:
            pass
    return results


@st.cache_data(ttl=60)
def load_config():
    """Load config.py values for display."""
    cfg = {}
    try:
        import config as c
        cfg = {
            "PAPER_TRADING":        c.PAPER_TRADING,
            "PAPER_STARTING_USDT":  c.PAPER_STARTING_USDT,
            "RSI_BUY":              c.RSI_BUY,
            "RSI_SELL":             c.RSI_SELL,
            "MA_PERIOD":            c.MA_PERIOD,
            "STOP_LOSS_PCT":        getattr(c, "STOP_LOSS_PCT", 0.06),
            "TAKE_PROFIT_PCT":      getattr(c, "TAKE_PROFIT_PCT", 0.04),
            "TRAILING_STOP_PCT":    getattr(c, "TRAILING_STOP_PCT", 0.03),
            "MAX_HOLD_HOURS":       getattr(c, "MAX_HOLD_HOURS", 48),
            "POLL_SECONDS":         c.POLL_SECONDS,
            "CANDLE_INTERVAL":      c.CANDLE_INTERVAL,
            "AI_ENABLED":           c.AI_ENABLED,
            "TELEGRAM_ENABLED":     c.TELEGRAM_ENABLED,
            "LISTING_HUNTER":       getattr(c, "LISTING_HUNTER_ENABLED", False),
        }
    except Exception as e:
        cfg = {"error": str(e)}
    return cfg


@st.cache_data(ttl=1800)
def load_news_scores():
    """Get current news sentiment scores."""
    try:
        from news_aggregator import score_coins_by_news_and_data
        symbols = ["BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT",
                   "ADA-USDT","DOT-USDT","AVAX-USDT","LINK-USDT","LTC-USDT",
                   "BCH-USDT","UNI-USDT","ATOM-USDT","XLM-USDT","RVN-USDT"]
        return score_coins_by_news_and_data(symbols)
    except Exception as e:
        return {"error": str(e)}


# ══════════════════════════════════════════════════════════════════════════════
#  SIDEBAR
# ══════════════════════════════════════════════════════════════════════════════

st.sidebar.title("🤖 Bot Dashboard")
st.sidebar.caption(f"Updated: {datetime.now().strftime('%H:%M:%S')}")

page = st.sidebar.radio("Navigation", [
    "📊 Overview",
    "📈 Backtest Results",
    "🎲 Monte Carlo",
    "🔗 Correlation",
    "📰 News Scores",
    "⚙️ Config",
])

if st.sidebar.button("🔄 Refresh Data"):
    st.cache_data.clear()
    st.rerun()

cfg = load_config()
mode_tag = "📄 PAPER" if cfg.get("PAPER_TRADING", True) else "💰 LIVE"
st.sidebar.markdown(f"**Mode:** {mode_tag}")
st.sidebar.markdown(f"**AI:** {'✅ ON' if cfg.get('AI_ENABLED') else '❌ OFF'}")
st.sidebar.markdown(f"**Telegram:** {'✅' if cfg.get('TELEGRAM_ENABLED') else '❌'}")
st.sidebar.markdown(f"**Listings:** {'✅' if cfg.get('LISTING_HUNTER') else '❌'}")


# ══════════════════════════════════════════════════════════════════════════════
#  OVERVIEW PAGE
# ══════════════════════════════════════════════════════════════════════════════

if page == "📊 Overview":
    st.title("📊 Bot Overview")

    # Config summary
    col1, col2, col3, col4, col5 = st.columns(5)
    with col1:
        st.metric("Mode", mode_tag)
    with col2:
        st.metric("Pool", f"${cfg.get('PAPER_STARTING_USDT', 0):.2f}")
    with col3:
        st.metric("RSI Buy/Sell", f"{cfg.get('RSI_BUY')}/{cfg.get('RSI_SELL')}")
    with col4:
        st.metric("Stop Loss", f"{cfg.get('STOP_LOSS_PCT', 0)*100:.0f}%")
    with col5:
        st.metric("Take Profit", f"{cfg.get('TAKE_PROFIT_PCT', 0)*100:.0f}%")

    st.divider()

    # Log activity
    trades, signals = load_today_logs()
    col_a, col_b = st.columns(2)

    with col_a:
        st.subheader(f"📋 Today's Activity ({len(trades)} trades)")
        if trades:
            for t in trades[-20:]:
                color = "green" if "BUY" not in t else "blue"
                st.text(t[-100:])
        else:
            st.info("No trades logged yet today. Check logs/ folder.")

    with col_b:
        st.subheader(f"📡 Recent Signals ({len(signals)})")
        if signals:
            for s in signals[-15:]:
                st.text(s[-100:])
        else:
            st.info("No signals logged yet.")

    # Log files
    st.divider()
    st.subheader("📁 Log Files")
    logs = sorted(glob.glob("logs/bot_*.log"), reverse=True)
    if logs:
        selected = st.selectbox("Select log file", logs)
        if selected and st.button("View log"):
            try:
                with open(selected, encoding="utf-8") as f:
                    content = f.read()
                st.text_area("Log contents", content[-5000:], height=400)
            except Exception as e:
                st.error(f"Could not read log: {e}")
    else:
        st.info("No log files found. Start the bot to generate logs.")


# ══════════════════════════════════════════════════════════════════════════════
#  BACKTEST RESULTS PAGE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📈 Backtest Results":
    st.title("📈 Backtest Results")

    bt_results = load_backtest_results()

    if not bt_results:
        st.warning("No backtest results found. Run the backtester first:")
        st.code("python backtest.py --all-coins --days 90")
    else:
        # Summary table
        summary_rows = []
        for symbol, df in bt_results.items():
            if df.empty:
                continue
            wins     = (df["pnl_net"] >= 0).sum()
            losses   = (df["pnl_net"] < 0).sum()
            total    = len(df)
            net      = df["pnl_net"].sum()
            fees     = df["fees"].sum()
            wr       = wins / total * 100 if total > 0 else 0
            summary_rows.append({
                "Symbol":    symbol,
                "Trades":    total,
                "Win Rate":  f"{wr:.1f}%",
                "Net P&L":   f"${net:+.4f}",
                "Fees":      f"${fees:.4f}",
                "Wins":      wins,
                "Losses":    losses,
            })

        if summary_rows:
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True)

        # Detailed view
        selected = st.selectbox("View detailed trades", list(bt_results.keys()))
        if selected:
            df = bt_results[selected]
            st.dataframe(df, use_container_width=True)

            # Cumulative P&L chart
            if "pnl_net" in df.columns:
                fig = go.Figure()
                fig.add_trace(go.Scatter(
                    y=df["pnl_net"].cumsum(),
                    mode="lines+markers",
                    name="Cumulative P&L",
                    line=dict(color="#1D9E75", width=2),
                ))
                fig.update_layout(title=f"{selected} — Cumulative P&L",
                                  xaxis_title="Trade #",
                                  yaxis_title="Cumulative P&L (USDT)")
                st.plotly_chart(fig, use_container_width=True)

    st.divider()
    st.subheader("▶️ Run Backtest")
    col1, col2, col3 = st.columns(3)
    with col1:
        bt_symbol = st.selectbox("Symbol", ["BTC-USDT","ETH-USDT","SOL-USDT",
                                            "XRP-USDT","DOGE-USDT","All coins"])
    with col2:
        bt_days = st.slider("Days of history", 30, 365, 90)
    with col3:
        bt_optimize = st.checkbox("Run optimizer (walk-forward)")

    if st.button("▶️ Run Backtest Now"):
        with st.spinner("Fetching data and running backtest..."):
            try:
                from backtest import get_data, Backtester, print_results, GridSearchOptimizer
                import io, contextlib

                sym   = "BTC-USDT" if bt_symbol == "All coins" else bt_symbol
                df_bt = get_data(sym, "15min", bt_days)

                if bt_optimize:
                    opt = GridSearchOptimizer(sym, days=max(bt_days, 180))
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        results = opt.run()
                    st.text(buf.getvalue())
                else:
                    bt  = Backtester(sym, df_bt)
                    r   = bt.run()
                    buf = io.StringIO()
                    with contextlib.redirect_stdout(buf):
                        print_results(r)
                    st.text(buf.getvalue())
                st.cache_data.clear()
            except Exception as e:
                st.error(f"Backtest failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  MONTE CARLO PAGE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🎲 Monte Carlo":
    st.title("🎲 Monte Carlo Simulation")
    st.caption("Estimates probability distribution of outcomes based on your backtest trade history")

    bt_results = load_backtest_results()

    if not bt_results:
        st.warning("Run a backtest first to generate trade history for Monte Carlo.")
        st.code("python backtest.py --symbol BTC-USDT --days 90")
    else:
        col1, col2, col3 = st.columns(3)
        with col1:
            mc_symbol = st.selectbox("Use trades from", list(bt_results.keys()))
        with col2:
            mc_sims   = st.slider("Simulations", 100, 5000, 1000, step=100)
        with col3:
            mc_pool   = st.number_input("Starting pool ($)", 20.0, 10000.0, 100.0)

        if st.button("🎲 Run Monte Carlo"):
            with st.spinner(f"Running {mc_sims:,} simulations..."):
                try:
                    from monte_carlo import MonteCarlo
                    df    = bt_results[mc_symbol]
                    trade_list = df.to_dict("records")

                    mc    = MonteCarlo(trade_list, starting_pool=mc_pool)
                    mc.run(simulations=mc_sims)
                    s     = mc.summary()

                    # Metrics
                    col_a, col_b, col_c, col_d = st.columns(4)
                    with col_a:
                        st.metric("Prob. of Profit", f"{s['prob_profit_pct']}%")
                    with col_b:
                        st.metric("Median Outcome", f"${s['median']:.2f}")
                    with col_c:
                        st.metric("Best Case (95th)", f"${s['best_case_p95']:.2f}")
                    with col_d:
                        st.metric("Worst Case (5th)", f"${s['worst_case_p5']:.2f}")

                    # Distribution chart
                    finals = [r["final_pool"] for r in mc.results]
                    fig    = px.histogram(finals, nbins=50,
                                         title="Final Pool Distribution",
                                         labels={"value":"Final Pool (USDT)"},
                                         color_discrete_sequence=["#185FA5"])
                    fig.add_vline(x=mc_pool, line_dash="dash", line_color="red",
                                  annotation_text="Start")
                    fig.add_vline(x=s["median"], line_dash="solid", line_color="green",
                                  annotation_text="Median")
                    st.plotly_chart(fig, use_container_width=True)

                    # Summary table
                    summary_df = pd.DataFrame([{
                        "Metric": k, "Value": v
                    } for k, v in s.items() if k != "simulations"])
                    st.dataframe(summary_df, use_container_width=True)

                except Exception as e:
                    st.error(f"Monte Carlo failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  CORRELATION PAGE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "🔗 Correlation":
    st.title("🔗 Portfolio Correlation")
    st.caption("Check how correlated your active coins are — high correlation = concentrated risk")

    default_coins = ["BTC-USDT","ETH-USDT","SOL-USDT","XRP-USDT","DOGE-USDT",
                     "ADA-USDT","DOT-USDT","AVAX-USDT","LINK-USDT","LTC-USDT"]

    col1, col2 = st.columns(2)
    with col1:
        lookback = st.slider("Lookback days", 7, 90, 30)
    with col2:
        symbols_input = st.multiselect("Coins to analyse", default_coins,
                                       default=default_coins[:6])

    if st.button("🔗 Check Correlation") and symbols_input:
        with st.spinner("Fetching price data..."):
            try:
                from portfolio_correlation import CorrelationChecker
                cc = CorrelationChecker(lookback_days=lookback)
                corr = cc.check(symbols_input)

                if not corr.empty:
                    # Heatmap
                    fig = px.imshow(corr,
                                    title=f"Correlation Matrix ({lookback}d)",
                                    color_continuous_scale="RdYlGn",
                                    zmin=-1, zmax=1,
                                    text_auto=".2f")
                    st.plotly_chart(fig, use_container_width=True)

                    # High correlation warnings
                    coins   = list(corr.columns)
                    warnings = []
                    for i, c1 in enumerate(coins):
                        for j, c2 in enumerate(coins):
                            if j <= i:
                                continue
                            val = corr.loc[c1, c2]
                            if val >= 0.80:
                                warnings.append(f"⚠️ {c1}/{c2}: {val:.2f} — high correlation")
                            elif val <= 0.30:
                                pass

                    if warnings:
                        st.warning("\n".join(warnings))
                    else:
                        st.success("✅ No highly correlated pairs found")

                    # Raw matrix
                    st.dataframe(corr.round(2), use_container_width=True)

            except Exception as e:
                st.error(f"Correlation check failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  NEWS SCORES PAGE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "📰 News Scores":
    st.title("📰 News Sentiment Scores")
    st.caption("Combined score from The Block, CoinDesk, Blockworks, Cointelegraph, Bloomberg, Forbes, Messari, CoinGecko, CoinMarketCap")

    if st.button("🔄 Fetch Latest Scores"):
        st.cache_data.clear()

    with st.spinner("Fetching news and scoring coins..."):
        scores = load_news_scores()

    if "error" in scores:
        st.error(f"Could not fetch scores: {scores['error']}")
    elif scores:
        # Bar chart
        df_scores = pd.DataFrame([
            {"Coin": k, "Score": v,
             "Sentiment": "Bullish" if v > 1 else "Bearish" if v < -1 else "Neutral"}
            for k, v in sorted(scores.items(), key=lambda x: x[1], reverse=True)
        ])

        fig = px.bar(df_scores, x="Coin", y="Score",
                     color="Sentiment",
                     color_discrete_map={
                         "Bullish": "#1D9E75",
                         "Neutral": "#888780",
                         "Bearish": "#D85A30",
                     },
                     title="News Sentiment Scores (-5 to +5)")
        fig.add_hline(y=0, line_dash="dash", line_color="gray")
        st.plotly_chart(fig, use_container_width=True)

        st.dataframe(df_scores, use_container_width=True)
    else:
        st.info("No scores available — check internet connection")


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIG PAGE
# ══════════════════════════════════════════════════════════════════════════════

elif page == "⚙️ Config":
    st.title("⚙️ Bot Configuration")

    if "error" in cfg:
        st.error(f"Could not load config: {cfg['error']}")
    else:
        st.subheader("Current Settings")
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("**Trading**")
            st.json({k: v for k, v in cfg.items()
                     if k in ("PAPER_TRADING","PAPER_STARTING_USDT",
                               "RSI_BUY","RSI_SELL","MA_PERIOD","POLL_SECONDS",
                               "CANDLE_INTERVAL")})

        with col2:
            st.markdown("**Risk Controls**")
            st.json({k: v for k, v in cfg.items()
                     if k in ("STOP_LOSS_PCT","TAKE_PROFIT_PCT",
                               "TRAILING_STOP_PCT","MAX_HOLD_HOURS")})

        st.subheader("Features")
        col3, col4, col5 = st.columns(3)
        with col3:
            st.markdown(f"**AI:** {'✅ Enabled' if cfg.get('AI_ENABLED') else '❌ Disabled'}")
        with col4:
            st.markdown(f"**Telegram:** {'✅ Enabled' if cfg.get('TELEGRAM_ENABLED') else '❌ Disabled'}")
        with col5:
            st.markdown(f"**Listing Hunter:** {'✅ Enabled' if cfg.get('LISTING_HUNTER') else '❌ Disabled'}")

        st.divider()
        st.info("To change settings, edit **config.py** in Notepad and restart the bot.")

        st.subheader("Quick Presets")
        col_a, col_b, col_c = st.columns(3)
        with col_a:
            st.markdown("**Conservative (live)**")
            st.code("RSI_BUY  = 35\nRSI_SELL = 65\nSTOP_LOSS_PCT = 0.05\nTAKE_PROFIT_PCT = 0.03")
        with col_b:
            st.markdown("**Balanced (recommended)**")
            st.code("RSI_BUY  = 38\nRSI_SELL = 62\nSTOP_LOSS_PCT = 0.06\nTAKE_PROFIT_PCT = 0.04")
        with col_c:
            st.markdown("**Aggressive (testing)**")
            st.code("RSI_BUY  = 45\nRSI_SELL = 55\nSTOP_LOSS_PCT = 0.08\nTAKE_PROFIT_PCT = 0.06")

        st.divider()
        st.subheader("📋 Recommended Workflow")
        st.markdown("""
1. **Backtest** → `python backtest.py --all-coins --optimize --days 180`
2. **Copy** recommended params to config.py
3. **Monte Carlo** → verify probability of profit > 60%
4. **Correlation** → ensure active coins aren't all moving together
5. **Paper trade** 1-2 weeks watching Telegram
6. **Go live** with small allocation ($100-200)
7. **Repeat optimizer** monthly
        """)
