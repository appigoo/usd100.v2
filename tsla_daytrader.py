import streamlit as st
import yfinance as yf
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timezone, timedelta
import time
import requests
from bs4 import BeautifulSoup
import re
import random

st.set_page_config(
    page_title="TSLA/TSLL Day Trader – $100/day",
    page_icon="⚡",
    layout="wide"
)

st.markdown("""
<style>
  .signal-buy  { color: #00e676; font-size: 1.8rem; font-weight: 700; }
  .signal-sell { color: #ff1744; font-size: 1.8rem; font-weight: 700; }
  .signal-hold { color: #ffab00; font-size: 1.8rem; font-weight: 700; }
  .session-badge {
    display: inline-block; padding: 4px 14px;
    border-radius: 20px; font-size: 0.85rem; font-weight: 600;
  }
  .stAlert { border-radius: 10px; }
</style>
""", unsafe_allow_html=True)

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  TRADING SESSION DETECTOR
# ╚══════════════════════════════════════════════════════════════════════════════

def is_dst_us(dt_utc: datetime) -> bool:
    year = dt_utc.year
    mar = datetime(year, 3, 8, 7, 0, tzinfo=timezone.utc)
    mar += timedelta(days=(6 - mar.weekday()) % 7)
    nov = datetime(year, 11, 1, 6, 0, tzinfo=timezone.utc)
    nov += timedelta(days=(6 - nov.weekday()) % 7)
    return mar <= dt_utc < nov

def get_et_time() -> datetime:
    now_utc = datetime.now(timezone.utc)
    offset = timedelta(hours=-4) if is_dst_us(now_utc) else timedelta(hours=-5)
    return now_utc.astimezone(timezone(offset))

def get_trading_session() -> dict:
    et = get_et_time()
    dow = et.weekday()
    hm = et.hour + et.minute / 60.0
    dst = is_dst_us(datetime.now(timezone.utc))
    tz_str = "夏令时 EDT" if dst else "冬令时 EST"

    if dow == 5 or (dow == 6 and hm < 20.0):
        return dict(session="CLOSED", label="休市（周末）", color="#555",
                    use_scraper=False, et=et, tz=tz_str, dst=dst)
    if 4.0 <= hm < 9.5:
        return dict(session="PRE", label="盘前交易 Pre-Market", color="#7c4dff",
                    use_scraper=True, et=et, tz=tz_str, dst=dst)
    if 9.5 <= hm < 16.0:
        return dict(session="REGULAR", label="正式交易 Regular", color="#00e676",
                    use_scraper=False, et=et, tz=tz_str, dst=dst)
    if 16.0 <= hm < 20.0:
        return dict(session="POST", label="盘后交易 After-Hours", color="#ff9100",
                    use_scraper=True, et=et, tz=tz_str, dst=dst)
    if hm >= 20.0 or hm < 4.0:
        if dow in (0, 1, 2, 3, 6):
            return dict(session="NIGHT", label="夜盘交易 Night Session", color="#40c4ff",
                        use_scraper=True, et=et, tz=tz_str, dst=dst)

    return dict(session="CLOSED", label="休市", color="#555",
                use_scraper=False, et=et, tz=tz_str, dst=dst)

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  IMPROVED SCRAPER — Pre-Market 強化版
# ╚══════════════════════════════════════════════════════════════════════════════

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

def get_random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "en-GB,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://finance.yahoo.com/",
    }

@st.cache_data(ttl=20)
def scrape_uk_yahoo(ticker: str) -> dict:
    result = {
        "price": None,
        "regular_price": None,
        "pre_price": None,
        "post_price": None,
        "source": "Unknown",
        "method": "None",
        "raw": {},
        "error": None,
    }

    headers = get_random_headers()
    ticker = ticker.upper()

    # 1. Primary: Quote API
    api_urls = [
        f"https://query1.finance.yahoo.com/v8/finance/quote?symbols={ticker}",
        f"https://query2.finance.yahoo.com/v8/finance/quote?symbols={ticker}",
    ]

    for url in api_urls:
        try:
            resp = requests.get(url, headers=headers, timeout=12)
            if resp.status_code != 200:
                continue
            data = resp.json()

            quote_list = data.get("quoteResponse", {}).get("result", [])
            if not quote_list:
                continue

            q = quote_list[0]

            def safe_float(key):
                v = q.get(key)
                try:
                    return float(v) if v is not None else None
                except (TypeError, ValueError):
                    return None

            result["regular_price"] = safe_float("regularMarketPrice")
            result["pre_price"] = safe_float("preMarketPrice")
            result["post_price"] = safe_float("postMarketPrice")

            result["raw"] = {
                "regularMarketPrice": result["regular_price"],
                "preMarketPrice": result["pre_price"],
                "postMarketPrice": result["post_price"],
            }

            result["source"] = url
            result["method"] = "Quote API"

            # Pre-Market 優先邏輯
            if result["pre_price"] is not None:
                result["price"] = result["pre_price"]
            elif result["post_price"] is not None:
                result["price"] = result["post_price"]
            else:
                result["price"] = result["regular_price"]

            if result["price"] is not None:
                return result

        except Exception as e:
            result["error"] = f"Quote API 失敗: {e}"
            continue

    # 2. Backup: Chart API (includePrePost=true) — 對 Pre/Post 非常有效
    try:
        chart_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=2d&interval=5m&includePrePost=true"
        resp = requests.get(chart_url, headers=headers, timeout=12)
        if resp.status_code == 200:
            data = resp.json()
            chart_result = data.get("chart", {}).get("result", [{}])[0]
            meta = chart_result.get("meta", {})
            indicators = chart_result.get("indicators", {}).get("quote", [{}])[0]

            if indicators and indicators.get("close"):
                closes = [c for c in indicators["close"] if c is not None]
                if closes:
                    last_close = closes[-1]

                    current_period = meta.get("currentTradingPeriod", {})
                    pre = current_period.get("pre", {})
                    post = current_period.get("post", {})

                    now_ts = int(time.time())

                    if pre.get("start") and pre.get("end") and pre["start"] <= now_ts <= pre["end"]:
                        result["price"] = last_close
                        result["pre_price"] = last_close
                        result["method"] = "Chart API (Pre-Market)"
                    elif post.get("start") and post.get("end") and post["start"] <= now_ts <= post["end"]:
                        result["price"] = last_close
                        result["post_price"] = last_close
                        result["method"] = "Chart API (Post-Market)"
                    else:
                        result["price"] = last_close
                        result["method"] = "Chart API (Latest)"

                    result["regular_price"] = meta.get("regularMarketPrice")
                    result["source"] = chart_url
                    result["raw"]["chart_last_close"] = last_close

                    if result["price"] is not None:
                        return result
    except Exception as e:
        result["error"] = f"Chart API 失敗: {e}"

    # 3. Final Fallback: HTML scraping
    html_urls = (
        ["https://finance.yahoo.com/quote/TSLL/", "https://uk.finance.yahoo.com/quote/TSLL/"]
        if ticker == "TSLL"
        else ["https://uk.finance.yahoo.com/quote/TSLA/", "https://finance.yahoo.com/quote/TSLA/"]
    )

    for url in html_urls:
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            result["source"] = url
            result["method"] = "HTML Scrape"

            for tag in soup.find_all("fin-streamer"):
                field = tag.get("data-field", "")
                value_str = tag.get("data-value") or tag.get_text(strip=True)
                try:
                    val = float(str(value_str).replace(",", "").strip())
                    if not (1 <= val <= 9999):
                        continue
                except:
                    continue

                if field == "regularMarketPrice":
                    result["regular_price"] = val
                elif field == "preMarketPrice":
                    result["pre_price"] = val
                elif field == "postMarketPrice":
                    result["post_price"] = val

            result["price"] = result["pre_price"] or result["post_price"] or result["regular_price"]

            if result["price"] is not None:
                return result

        except Exception as e:
            result["error"] = f"HTML fallback 失敗: {e}"
            continue

    result["error"] = result.get("error") or "所有來源均失敗"
    return result

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  OHLCV DATA
# ╚══════════════════════════════════════════════════════════════════════════════

def fetch_data(ticker, interval="5m", period="1d"):
    try:
        df = yf.download(
            ticker, interval=interval, period=period,
            auto_adjust=True, prepost=True, progress=False
        )
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.dropna(inplace=True)
        return df
    except Exception as e:
        st.error(f"yfinance 數據獲取失敗: {e}")
        return None

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  INDICATORS + SIGNAL + CHART + TTS 等（保持原樣，未修改）
# ╚══════════════════════════════════════════════════════════════════════════════

def compute_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))

def compute_macd(series, fast=12, slow=26, signal=9):
    ef = series.ewm(span=fast).mean()
    es = series.ewm(span=slow).mean()
    m = ef - es
    return m, m.ewm(span=signal).mean()

def compute_bollinger(series, period=20, std=2):
    mid = series.rolling(period).mean()
    sd = series.rolling(period).std()
    return mid + std*sd, mid, mid - std*sd

def compute_vwap(df):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    return (tp * df["Volume"]).cumsum() / df["Volume"].cumsum()

def generate_signal(df, target_profit, shares):
    close = df["Close"]
    rsi = compute_rsi(close)
    macd, macd_sig = compute_macd(close)
    bb_up, bb_mid, bb_lo = compute_bollinger(close)
    vwap = compute_vwap(df)

    lr = float(rsi.iloc[-1])
    lm = float(macd.iloc[-1])
    lms = float(macd_sig.iloc[-1])
    lc = float(close.iloc[-1])
    lbl = float(bb_lo.iloc[-1])
    lbu = float(bb_up.iloc[-1])
    lv = float(vwap.iloc[-1])

    score = 0
    reasons = []

    if lr < 35:   score += 2; reasons.append(f"RSI 超賣 ({lr:.1f})")
    elif lr > 65: score -= 2; reasons.append(f"RSI 超買 ({lr:.1f})")

    if lm > lms:  score += 1; reasons.append("MACD 金叉 ↑")
    else:         score -= 1; reasons.append("MACD 死叉 ↓")

    if lc < lbl:   score += 2; reasons.append("價格跌破布林下軌")
    elif lc > lbu: score -= 2; reasons.append("價格突破布林上軌")

    if lc > lv:  score += 1; reasons.append(f"價格高於 VWAP (${lv:.2f})")
    else:        score -= 1; reasons.append(f"價格低於 VWAP (${lv:.2f})")

    pm = target_profit / shares
    if score >= 3:
        action = "BUY"
        entry = lc
        tp = round(entry + pm, 2)
        sl = round(entry - pm*0.5, 2)
    elif score <= -3:
        action = "SELL/SHORT"
        entry = lc
        tp = round(entry - pm, 2)
        sl = round(entry + pm*0.5, 2)
    else:
        action = "HOLD"
        entry = lc
        tp = round(entry + pm, 2)
        sl = round(entry - pm*0.5, 2)

    return dict(action=action, score=score, entry=entry, take_profit=tp, stop_loss=sl,
                rsi=lr, macd=lm, macd_sig=lms,
                bb_upper=lbu, bb_lower=lbl, vwap=lv, reasons=reasons,
                rsi_series=rsi, macd_series=macd, macd_sig_series=macd_sig,
                bb_up=bb_up, bb_lo=bb_lo, bb_mid=bb_mid, vwap_series=vwap)

def build_chart(df, sig):
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True,
                        row_heights=[0.55, 0.25, 0.20], vertical_spacing=0.03,
                        subplot_titles=["價格 + 布林 + VWAP", "RSI (14)", "MACD"])

    fig.add_trace(go.Candlestick(x=df.index, open=df["Open"], high=df["High"],
        low=df["Low"], close=df["Close"],
        increasing_line_color="#00e676", decreasing_line_color="#ff1744",
        name="K線"), row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=sig["bb_up"], line=dict(color="#7c4dff", width=1, dash="dot"), name="BB上軌"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sig["bb_lo"], line=dict(color="#7c4dff", width=1, dash="dot"),
        fill="tonexty", fillcolor="rgba(124,77,255,0.07)", name="BB下軌"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sig["bb_mid"], line=dict(color="#7c4dff", width=0.5), name="BB中軌", showlegend=False), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sig["vwap_series"], line=dict(color="#ffab00", width=1.5), name="VWAP"), row=1, col=1)

    lc = {"BUY":"#00e676","SELL/SHORT":"#ff1744","HOLD":"#ffab00"}.get(sig["action"],"#fff")
    for price, label in [(sig["entry"],"入場"),(sig["take_profit"],"止盈"),(sig["stop_loss"],"止損")]:
        fig.add_hline(y=price, line_color=lc, line_dash="dash", line_width=1,
                      annotation_text=f"{label} ${price}", annotation_position="right", row=1, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=sig["rsi_series"], line=dict(color="#40c4ff", width=1.5), name="RSI"), row=2, col=1)
    fig.add_hline(y=70, line_color="#ff1744", line_dash="dot", line_width=0.8, row=2, col=1)
    fig.add_hline(y=30, line_color="#00e676", line_dash="dot", line_width=0.8, row=2, col=1)

    diff = sig["macd_series"] - sig["macd_sig_series"]
    fig.add_trace(go.Bar(x=df.index, y=diff,
        marker_color=["#00e676" if v >= 0 else "#ff1744" for v in diff],
        name="MACD柱", showlegend=False), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sig["macd_series"], line=dict(color="#00e676", width=1), name="MACD"), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=sig["macd_sig_series"], line=dict(color="#ff1744", width=1), name="Signal"), row=3, col=1)

    fig.update_layout(height=680, template="plotly_dark",
                      paper_bgcolor="#0e1117", plot_bgcolor="#161b22",
                      showlegend=True, legend=dict(orientation="h", y=1.02, x=0),
                      xaxis_rangeslider_visible=False, font=dict(size=11))
    return fig

def inject_tts(text: str, lang: str = "zh-CN", rate: float = 0.95):
    safe = text.replace("'", "\\'").replace("\n", " ").replace('"', '\\"')
    uid = abs(hash(text + str(time.time()))) % 10_000_000
    st.components.v1.html(f"""
    <div id="tts_{uid}" style="display:none;"></div>
    <script>
    (function() {{
      if (!window.speechSynthesis) return;
      window.speechSynthesis.cancel();
      var u = new SpeechSynthesisUtterance('{safe}');
      u.lang = '{lang}'; u.rate = {rate}; u.pitch = 1.0;
      function speak() {{
        var vv = window.speechSynthesis.getVoices();
        var zh = vv.find(function(v){{ return v.lang.startsWith('{lang[:2]}'); }});
        if (zh) u.voice = zh;
        window.speechSynthesis.speak(u);
      }}
      if (window.speechSynthesis.getVoices().length > 0) {{ speak(); }}
      else {{ window.speechSynthesis.onvoiceschanged = speak; }}
    }})();
    </script>
    """, height=0)

def build_speech_text(ticker, sig, shares, lang, session_label):
    a = sig["action"]
    p = sig["entry"]
    tp = sig["take_profit"]
    sl = sig["stop_loss"]
    sc = abs(sig["score"])
    r = "，".join(sig["reasons"][:2])
    if lang.startswith("en"):
        prefix = f"Session: {session_label}. "
        if a == "BUY":
            return f"{prefix}BUY signal on {ticker}, strength {sc}/6. Price {p:.2f}. Buy {shares} shares. TP {tp:.2f}, SL {sl:.2f}."
        elif a == "SELL/SHORT":
            return f"{prefix}SELL signal on {ticker}, strength {sc}/6. Price {p:.2f}. Sell {shares} shares. TP {tp:.2f}, SL {sl:.2f}."
        else:
            return f"{prefix}{ticker} no clear signal. Score {sig['score']}. Price {p:.2f}."
    else:
        prefix = f"當前{session_label}，"
        if a == "BUY":
            return f"{prefix}交易信號！{ticker} 买入，強度 {sc} 分。價格 {p:.2f} 美元，买入 {shares} 股。止盈 {tp:.2f}，止損 {sl:.2f}。依據：{r}。"
        elif a == "SELL/SHORT":
            return f"{prefix}交易信號！{ticker} 賣出，強度 {sc} 分。價格 {p:.2f} 美元，賣出 {shares} 股。止盈 {tp:.2f}，止損 {sl:.2f}。依據：{r}。"
        else:
            return f"{prefix}{ticker} 無明確信號，建議觀望。強度 {sig['score']} 分，價格 {p:.2f} 美元。"

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  SESSION STATE
# ╚══════════════════════════════════════════════════════════════════════════════

for k, v in [("last_spoken_signal", None), ("tts_enabled", True), ("tts_lang", "zh-CN"),
             ("scraper_debug", False)]:
    if k not in st.session_state:
        st.session_state[k] = v

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  PAGE HEADER
# ╚══════════════════════════════════════════════════════════════════════════════

st.title("⚡ TSLA / TSLL 日內交易助手")
st.caption("目標：每天賺 $100 | RSI + MACD + 布林帶 + VWAP | 盤前/盤後強化爬蟲")

sess = get_trading_session()
et_str = sess["et"].strftime("%Y-%m-%d %H:%M:%S")

badge_bg = {"PRE":"#3d1f8f","REGULAR":"#0d3b1f","POST":"#5c2a00",
            "NIGHT":"#002b4f","CLOSED":"#2a2a2a"}.get(sess["session"],"#222")

st.markdown(
    f'<span class="session-badge" style="background:{badge_bg};color:{sess["color"]};'
    f'border:1px solid {sess["color"]};">'
    f'🕐 {sess["label"]}  |  ET {et_str}  |  {sess["tz"]}</span>',
    unsafe_allow_html=True
)

if sess["session"] == "CLOSED":
    st.warning("⏸ 當前市場休市，數據僅供參考。")
elif sess["use_scraper"]:
    st.info(f"🕷 **{sess['label']}** — 使用強化爬蟲抓取延伸時段即時報價")

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  SIDEBAR
# ╚══════════════════════════════════════════════════════════════════════════════

with st.sidebar:
    st.header("⚙️ 交易設置")
    ticker = st.selectbox("選擇股票", ["TSLA", "TSLL"], index=0)
    interval = st.selectbox("K線週期", ["1m","2m","5m","15m","30m"], index=2)
    period = st.selectbox("數據範圍", ["1d","2d","5d"], index=0)

    st.divider()
    st.subheader("💰 目標計算")
    target = st.number_input("今日目標利潤 ($)", min_value=50, max_value=500, value=100, step=10)
    capital = st.number_input("可用資金 ($)", min_value=500, max_value=50000, value=3000, step=500)

    st.divider()
    st.subheader("🔊 語音播報")
    tts_on = st.toggle("啟用語音播報", value=st.session_state["tts_enabled"])
    st.session_state["tts_enabled"] = tts_on
    lang_choice = st.selectbox("播報語言", ["zh-CN 普通話","zh-TW 繁體中文","en-US 英文"], index=0)
    lang_map = {"zh-CN 普通話":"zh-CN","zh-TW 繁體中文":"zh-TW","en-US 英文":"en-US"}
    active_lang = lang_map[lang_choice]
    tts_rate = st.slider("語速", 0.5, 1.5, 0.95, 0.05)
    tts_only_action = st.checkbox("僅 BUY/SELL 時播報", value=True)

    st.divider()
    auto_refresh = st.checkbox("🔄 自動刷新 (30秒)", value=False)
    st.session_state["scraper_debug"] = st.checkbox("🐛 顯示爬蟲調試資訊", value=False)
    if st.button("🔍 立即分析", type="primary", use_container_width=True):
        st.session_state["last_spoken_signal"] = None
        st.cache_data.clear()
        st.rerun()

    st.divider()
    st.subheader("🗓 交易時段（富途）")
    if sess["dst"]:
        st.caption("☀️ 夏令時 EDT")
        st.markdown("- **盤前** 北京 16:00–21:30\n- **盤中** 北京 21:30–04:00\n- **盤後** 北京 04:00–08:00\n- **夜盤** 北京 08:00–16:00")
    else:
        st.caption("❄️ 冬令時 EST")
        st.markdown("- **盤前** 北京 17:00–22:30\n- **盤中** 北京 22:30–05:00\n- **盤後** 北京 05:00–09:00\n- **夜盤** 北京 09:00–17:00")

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  DATA FETCH
# ╚══════════════════════════════════════════════════════════════════════════════

scraped = None
scraper_ok = False
scraper_msg = ""

if sess["use_scraper"]:
    with st.spinner(f"🕷 強化爬蟲抓取 {ticker} 延伸時段報價…"):
        scraped = scrape_uk_yahoo(ticker)

    if scraped.get("error"):
        scraper_msg = f"❌ 爬蟲錯誤：{scraped['error']} — 回退到 yfinance"
    elif scraped.get("price"):
        scraper_ok = True
        src_field = (
            "preMarketPrice" if scraped.get("pre_price") == scraped["price"] else
            "postMarketPrice" if scraped.get("post_price") == scraped["price"] else
            "regularMarketPrice"
        )
        scraper_msg = f"✅ 爬蟲成功 · 方法: {scraped.get('method')} · 字段: `{src_field}`"
    else:
        scraper_msg = "⚠️ 爬蟲未找到價格 — 回退到 yfinance"

with st.spinner(f"📊 載入 {ticker} K線數據…"):
    df = fetch_data(ticker, interval=interval, period=period)

if df is None or len(df) < 30:
    st.error("K線數據不足（<30 根），請稍後重試或更換週期。")
    st.stop()

# Patch last Close with live scraped price
if scraper_ok and scraped.get("price"):
    current_price = scraped["price"]
    df.iloc[-1, df.columns.get_loc("Close")] = current_price
else:
    current_price = float(df["Close"].iloc[-1])

shares_estimate = max(1, int(capital / current_price))
sig = generate_signal(df, target, shares_estimate)

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  TTS TRIGGER
# ╚══════════════════════════════════════════════════════════════════════════════

signal_key = f"{ticker}|{sig['action']}|{sig['entry']:.2f}"
skip_hold = tts_only_action and sig["action"] == "HOLD"
should_speak = tts_on and not skip_hold and signal_key != st.session_state["last_spoken_signal"]

if should_speak:
    txt = build_speech_text(ticker, sig, shares_estimate, active_lang, sess["label"])
    inject_tts(txt, lang=active_lang, rate=tts_rate)
    st.session_state["last_spoken_signal"] = signal_key

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  SCRAPER STATUS
# ╚══════════════════════════════════════════════════════════════════════════════

if sess["use_scraper"]:
    cols = st.columns([3, 1, 1, 1])
    with cols[0]:
        if scraper_ok:
            st.success(scraper_msg)
        elif "❌" in scraper_msg:
            st.error(scraper_msg)
        else:
            st.warning(scraper_msg)

    if scraper_ok and scraped:
        with cols[1]:
            st.metric("正式收盤", f"${scraped['regular_price']:.2f}" if scraped.get("regular_price") else "—")
        with cols[2]:
            st.metric("盤前報價", f"${scraped['pre_price']:.2f}" if scraped.get("pre_price") else "—")
        with cols[3]:
            st.metric("盤後報價", f"${scraped['post_price']:.2f}" if scraped.get("post_price") else "—")

    if st.session_state["scraper_debug"] and scraped:
        with st.expander("🐛 爬蟲調試資訊"):
            st.json({k: v for k, v in scraped.items() if k not in ["raw"]})
            st.json(scraped.get("raw", {}))

# ╔══════════════════════════════════════════════════════════════════════════════
# ║  METRICS + SIGNAL + CHART + TIPS（其餘保持原樣）
# ╚══════════════════════════════════════════════════════════════════════════════

st.divider()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("當前價格", f"${current_price:.2f}", delta="爬蟲即時" if scraper_ok else "yfinance")
c2.metric("RSI", f"{sig['rsi']:.1f}", delta="超賣" if sig["rsi"]<35 else ("超買" if sig["rsi"]>65 else "中性"))
c3.metric("VWAP", f"${sig['vwap']:.2f}")
c4.metric("可買股數", f"{shares_estimate} 股", delta=f"資金 ${capital:,}")
c5.metric("目標利潤", f"${target}", delta=f"每股需漲 ${target/shares_estimate:.2f}")

st.divider()

# TTS bar
icon_map = {"BUY":"🟢","SELL/SHORT":"🔴","HOLD":"🟡"}
bar_l, bar_r = st.columns([4, 1])
with bar_l:
    if not tts_on:
        st.warning("🔕 語音播報已關閉")
    elif should_speak:
        st.success(f"🔊 正在播報：{icon_map.get(sig['action'],'?')} **{sig['action']}** — {ticker} @ ${sig['entry']:.2f}（{sess['label']}）")
    elif skip_hold:
        st.info("🔇 HOLD 信號，已跳過播報")
    else:
        st.info(f"🔇 等待新信號（當前：{icon_map.get(sig['action'],'?')} {sig['action']}）")
with bar_r:
    if tts_on and st.button("🔊 重新播報", use_container_width=True):
        txt = build_speech_text(ticker, sig, shares_estimate, active_lang, sess["label"])
        inject_tts(txt, lang=active_lang, rate=tts_rate)

st.divider()

col_sig, col_detail = st.columns([1, 2])

with col_sig:
    css = {"BUY":"signal-buy","SELL/SHORT":"signal-sell","HOLD":"signal-hold"}.get(sig["action"],"signal-hold")
    st.markdown(f"""
    <div style="background:#161b22;border-radius:12px;padding:24px;text-align:center;border:1px solid #2d3748;">
      <div style="font-size:1rem;color:#8b949e;margin-bottom:8px;">信號強度: {sig['score']:+d}/6</div>
      <div class="{css}">{icon_map.get(sig['action'],'')} {sig['action']}</div>
      <div style="margin-top:16px;font-size:0.9rem;color:#8b949e;">
        {'&nbsp;'.join(['●']*abs(sig['score']) + ['○']*(6-abs(sig['score'])))}
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("### 📋 交易計劃")
    pc = "#00e676" if sig["action"]=="BUY" else ("#ff1744" if sig["action"]=="SELL/SHORT" else "#ffab00")
    st.markdown(f"""
    <div style="background:#161b22;border-radius:10px;padding:16px;border-left:4px solid {pc};">
      <p>🎯 <b>入場價</b>: ${sig['entry']:.2f}</p>
      <p>✅ <b>止盈價</b>: ${sig['take_profit']:.2f}</p>
      <p>🛑 <b>止損價</b>: ${sig['stop_loss']:.2f}</p>
      <p>📦 <b>股數</b>: {shares_estimate} 股</p>
      <p>💵 <b>預期盈利</b>: ${(sig['take_profit']-sig['entry'])*shares_estimate:.2f}</p>
      <p>⚠️ <b>最大虧損</b>: ${abs((sig['stop_loss']-sig['entry'])*shares_estimate):.2f}</p>
      <p>📊 <b>盈虧比</b>: {abs((sig['take_profit']-sig['entry'])/(sig['stop_loss']-sig['entry'])):.1f}x</p>
    </div>
    """, unsafe_allow_html=True)

with col_detail:
    st.markdown("### 📊 信號依據")
    for r in sig["reasons"]:
        ok = any(x in r for x in ["超賣","金叉","下軌","高於"])
        st.markdown(f"{'✅' if ok else '⚠️'} {r}")

    st.markdown("### 📈 K線圖表")
    if sess["use_scraper"]:
        st.caption("⚠️ 延伸時段流動性較低，K線僅供參考 · 最新收盤已替換為爬蟲即時價")
    st.plotly_chart(build_chart(df, sig), use_container_width=True)

st.divider()
st.markdown("### 💡 日內交易策略提示")
t1, t2, t3 = st.columns(3)
with t1:
    st.info("**TSLA vs TSLL**\n\nTSLL 是 TSLA 的 2x 槓桿 ETF，延伸時段流動性請先在券商確認。")
with t2:
    st.warning("**延伸時段注意**\n\n盤前/盤後/夜盤價差大、流動性低，止損建議設更寬，倉位縮小 50%。")
with t3:
    st.error("**風險提示**\n\n此工具僅供參考，不構成投資建議。延伸時段風險更高，請謹慎操作。")

if auto_refresh:
    time.sleep(30)
    st.rerun()

st.caption(
    f"最後更新: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | "
    f"ET: {et_str} | 時段: {sess['label']} | "
    f"價格來源: {'強化爬蟲 (' + scraped.get('method', 'Unknown') + ')' if scraper_ok else 'yfinance'}"
)
