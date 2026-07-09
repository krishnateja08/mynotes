"""
============================================================
  BTST (Buy Today Sell Tomorrow) Stock Screener  v3 (Fixed)
  India — Nifty 100 (NSE)  |  USA — S&P 500 Top 100 (NYSE/NASDAQ)
============================================================
Requirements:
    pip install yfinance pandas pandas-ta requests tabulate colorama

Usage:
    python btst_screener.py              # scans both markets (BTST + ORB)
    python btst_screener.py --india      # India only
    python btst_screener.py --usa        # USA only
    python btst_screener.py --no-orb     # skip ORB intraday scan
    python btst_screener.py --min-score 70   # GOOD picks only (score ≥ 70)
    python btst_screener.py --min-score 80   # HIGH conviction only (score ≥ 80)
    python btst_screener.py --backtest   # replay past CSV picks (last 30 days)
    python btst_screener.py --backtest --days 60   # extend backtest window
    python btst_screener.py --backtest --india      # India backtest only

Bug-fixes & Improvements in v3:
    ✅ FIX: Closing Marubozu — only upper shadow checked (was incorrectly
             requiring both shadows to be tiny, making it a Full Marubozu)
    ✅ FIX: MACD histogram direction — expanding histogram scores higher than
             shrinking; a shrinking positive histogram is now penalised (was
             all positive histograms scored identically)
    ✅ FIX: MACD crossover detection — fresh MACD line crossing signal line
             now detected separately and scored at full weight
    ✅ FIX: Friday RVOL guard — now requires ≥ 2 Friday data-points before
             using Friday-adjusted average (was silently dividing by zero)
    ✅ FIX: Dynamic VIX threshold — uses 60-day rolling median × 1.30 instead
             of hardcoded 18/20 (adapts to volatility regime)
    ✅ NEW:  Earnings filter — stocks with earnings announced tomorrow are
             automatically flagged with Has_Earnings=True and score is cut
             by 50% (overnight gap risk from earnings is the #1 BTST killer)
    ✅ NEW:  Minimum R:R filter — filter_and_rank() now enforces RR_Ratio ≥ 1.5
             (no trade taken where reward < 1.5× the risk)
    ✅ NEW:  Position sizing — output now includes Shares and Position_Value
             columns based on 1% capital-at-risk per trade (capital = ₹5L / $10k)
    ✅ NEW:  MACD line vs signal-line crossover used as primary conviction signal

Output:
    btst_report_YYYY-MM-DD.html    (combined HTML with BTST + ORB tabs)
    btst_india_YYYY-MM-DD.csv
    btst_usa_YYYY-MM-DD.csv
    orb_india_YYYY-MM-DD.csv
    orb_usa_YYYY-MM-DD.csv
============================================================
"""

import yfinance as yf
import pandas as pd
import pandas_ta as ta
import warnings
import sys
import json
import argparse
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo          # stdlib — Python 3.9+
from tabulate import tabulate
from colorama import Fore, Style, init
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# hub_client.py — reads live quotes / OHLCV from your local tick_hub.py (India,
# :5001) and schwab_proxy.py (USA, :5010) instead of yfinance. See its module
# docstring for exactly which pieces still use yfinance and why.
import hub_client

warnings.filterwarnings("ignore")
init(autoreset=True)

# Global lock — prevents concurrent yf.download() calls from racing on
# yfinance's internal shared _DFS dict (RuntimeError: dict changed size during iteration)
_YF_LOCK = threading.Lock()

# ══════════════════════════════════════════════════════════
# SYMBOL LISTS
# ══════════════════════════════════════════════════════════

# Synced to match tick_hub.py's NIFTY_500_WATCHLIST exactly (100 symbols) —
# every symbol here MUST also exist in tick_hub's watchlist, or its token
# won't resolve and the hub can't serve live price/OHLCV for it.
NIFTY100_SYMBOLS = [
    "ADANIPOWER.NS", "INFY.NS",       "WIPRO.NS",       "ETERNAL.NS",
    "JIOFIN.NS",     "HDFCBANK.NS",   "UNIONBANK.NS",   "TATASTEEL.NS",
    "KOTAKBANK.NS",  "VEDL.NS",
    "CANBK.NS",      "ITC.NS",        "COALINDIA.NS",   "IRFC.NS",
    "ICICIBANK.NS",  "SBIN.NS",       "HINDZINC.NS",    "VBL.NS",
    "ADANIGREEN.NS", "ONGC.NS",
    "RELIANCE.NS",   "BEL.NS",        "PNB.NS",         "MOTHERSON.NS",
    "HCLTECH.NS",    "BPCL.NS",       "POWERGRID.NS",   "SUNPHARMA.NS",
    "GAIL.NS",       "SHRIRAMFIN.NS",
    "IOC.NS",        "PFC.NS",        "ADANIENSOL.NS",  "BANKBARODA.NS",
    "TATAPOWER.NS",  "BHARTIARTL.NS", "NTPC.NS",        "TATACAP.NS",
    "TMPV.NS",       "DRREDDY.NS",
    "SBILIFE.NS",    "TCS.NS",        "RECLTD.NS",      "HINDALCO.NS",
    "TMCV.NS",       "CIPLA.NS",      "CGPOWER.NS",     "BAJFINANCE.NS",
    "GODREJCP.NS",   "AMBUJACEM.NS",
    "TECHM.NS",      "AXISBANK.NS",   "NESTLEIND.NS",   "HDFCLIFE.NS",
    "MAXHEALTH.NS",  "M&M.NS",        "ADANIPORTS.NS",  "MAZDOCK.NS",
    "ADANIENT.NS",   "INDHOTEL.NS",
    "LT.NS",         "DLF.NS",        "JSWSTEEL.NS",    "HINDUNILVR.NS",
    "TRENT.NS",      "LODHA.NS",      "TATACONSUM.NS",  "CHOLAFIN.NS",
    "JINDALSTEL.NS", "GRASIM.NS",
    "HYUNDAI.NS",    "HDFCAMC.NS",    "UNITDSPR.NS",    "TITAN.NS",
    "LTM.NS",        "BAJAJFINSV.NS", "HAL.NS",         "TVSMOTOR.NS",
    "INDIGO.NS",     "ZYDUSLIFE.NS",
    "MUTHOOTFIN.NS", "ENRIN.NS",      "PIDILITIND.NS",  "CUMMINSIND.NS",
    "BRITANNIA.NS",  "MARUTI.NS",     "ASIANPAINT.NS",  "EICHERMOT.NS",
    "APOLLOHOSP.NS", "ULTRACEMCO.NS",
    "ABB.NS",        "DIVISLAB.NS",   "SIEMENS.NS",     "SOLARINDS.NS",
    "TORNTPHARM.NS", "DMART.NS",      "BAJAJ-AUTO.NS",  "BAJAJHLDNG.NS",
    "BOSCHLTD.NS",   "SHREECEM.NS",
]

# USA symbols — synced from master_watchlist.py (138 symbols across 11 sectors)
# Removed (not in master watchlist): ABT, ACN, ADI, AON, APH, BRK-B, BSX, CME,
#   DHR, ELV, IBM, ICE, INTU, KLAC, MCO, PFE, PH, TXN, UBER, ZTS
# Added: AEP, AMT, APD, C, CBRE, CHTR, COF, COP, CTAS, CVS, D, DIS, DLR, DOW,
#   EBAY, ECL, EOG, EQIX, EXC, FAST, FCX, FDX, GEV, GOOG, HOOD, INTC, KMI, LMT,
#   LULU, MAR, MDT, MNST, NEM, NKE, NUE, O, PEG, PGR, PM, PSA, PSX, RBLX, ROST,
#   SBUX, SLB, SMCI, SNPS, SOFI, SPG, SPOT, SRE, TMUS, UPS, VST, VTR, WEC, WELL, XEL
SP500_TOP100_SYMBOLS = [
    # Technology (17)
    "NVDA", "MSFT", "AAPL", "AVGO", "AMD", "ORCL", "ADBE", "PANW",
    "NOW", "SNPS", "CRM", "CSCO", "INTC", "QCOM", "AMAT", "LRCX", "SMCI",
    # Communication Services (12)
    "GOOGL", "GOOG", "META", "NFLX", "CMCSA", "DIS",
    "TMUS", "VZ", "T", "CHTR", "SPOT", "RBLX",
    # Consumer Discretionary (13)
    "AMZN", "TSLA", "HD", "MCD", "TJX", "BKNG",
    "LOW", "SBUX", "NKE", "MAR", "ROST", "EBAY", "LULU",
    # Consumer Staples (10)
    "WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "MDLZ", "CL", "MNST",
    # Health Care (16)
    "LLY", "UNH", "JNJ", "MRK", "ABBV", "TMO", "AMGN", "BMY",
    "GILD", "ISRG", "VRTX", "CVS", "CI", "MDT", "SYK", "REGN",
    # Financials (18)
    "JPM", "BAC", "MS", "GS", "V", "MA", "AXP", "BLK",
    "SPGI", "C", "WFC", "SCHW", "COF", "PGR", "CB", "MMC", "HOOD", "SOFI",
    # Industrials (15)
    "GE", "CAT", "UNP", "HON", "LMT", "UPS", "RTX", "DE",
    "FDX", "BA", "GEV", "ETN", "ADP", "FAST", "CTAS",
    # Energy (12)
    "XOM", "CVX", "COP", "NEE", "SO", "DUK", "CEG", "VST",
    "SLB", "EOG", "KMI", "PSX",
    # Materials (8)
    "LIN", "FCX", "SHW", "NEM", "APD", "ECL", "NUE", "DOW",
    # Real Estate (10)
    "PLD", "AMT", "EQIX", "DLR", "WELL", "SPG", "PSA", "O", "CBRE", "VTR",
    # Utilities (7)
    "EXC", "XEL", "AEP", "SRE", "D", "PEG", "WEC",
]

# ── Index / VIX symbols ────────────────────────────────────
INDIA_INDEX  = "^NSEI"
INDIA_VIX    = "^INDIAVIX"
USA_INDEX    = "^GSPC"      # S&P 500
USA_VIX      = "^VIX"       # CBOE VIX

# ── Sector index / ETF maps ────────────────────────────────
# Maps each stock symbol → its sector benchmark ticker.
# Rebuilt to MIRROR tick_hub.py's own SECTOR_MAP + SECTOR_INDEX exactly (same
# groupings, same index tickers — including its OIL&GAS→NIFTY_OIL_AND_GAS.NS
# and TELECOM→^CNXIT quirks) so every NIFTY100_SYMBOLS entry resolves and the
# screener's sector bonus always agrees with what the hub itself would report.
_INDIA_SECTOR_LABEL: dict[str, str] = {
    # Banking & Finance
    "HDFCBANK":"BANK",    "ICICIBANK":"BANK",   "SBIN":"BANK",
    "AXISBANK":"BANK",    "KOTAKBANK":"BANK",   "BAJFINANCE":"BANK",
    "BAJAJFINSV":"BANK",  "SBILIFE":"BANK",     "HDFCLIFE":"BANK",
    "SHRIRAMFIN":"BANK",  "MUTHOOTFIN":"BANK",  "CHOLAFIN":"BANK",
    "HDFCAMC":"BANK",     "PFC":"BANK",         "RECLTD":"BANK",
    "IRFC":"BANK",        "TATACAP":"BANK",     "JIOFIN":"BANK",
    "BANKBARODA":"BANK",  "CANBK":"BANK",       "UNIONBANK":"BANK",
    "PNB":"BANK",
    # IT & Technology
    "INFY":"IT",    "TCS":"IT",    "HCLTECH":"IT",
    "TECHM":"IT",   "WIPRO":"IT",  "LTM":"IT",
    # Oil & Gas
    "RELIANCE":"OIL&GAS",  "ONGC":"OIL&GAS",  "BPCL":"OIL&GAS",
    "GAIL":"OIL&GAS",      "IOC":"OIL&GAS",
    # Auto & Auto Ancillary
    "MARUTI":"AUTO",    "M&M":"AUTO",       "BAJAJ-AUTO":"AUTO",
    "EICHERMOT":"AUTO", "TVSMOTOR":"AUTO",  "MOTHERSON":"AUTO",
    "BOSCHLTD":"AUTO",  "TMCV":"AUTO",      "TMPV":"AUTO",
    "HYUNDAI":"AUTO",
    # Pharma & Healthcare
    "SUNPHARMA":"PHARMA",  "CIPLA":"PHARMA",      "DRREDDY":"PHARMA",
    "DIVISLAB":"PHARMA",   "APOLLOHOSP":"PHARMA", "TORNTPHARM":"PHARMA",
    "ZYDUSLIFE":"PHARMA",  "MAXHEALTH":"PHARMA",
    # Metals & Mining
    "HINDALCO":"METALS",   "TATASTEEL":"METALS",  "JSWSTEEL":"METALS",
    "VEDL":"METALS",       "HINDZINC":"METALS",   "JINDALSTEL":"METALS",
    "COALINDIA":"METALS",
    # FMCG & Retail
    "HINDUNILVR":"FMCG",  "ITC":"FMCG",        "NESTLEIND":"FMCG",
    "BRITANNIA":"FMCG",   "TATACONSUM":"FMCG",  "GODREJCP":"FMCG",
    "VBL":"FMCG",         "DMART":"FMCG",       "UNITDSPR":"FMCG",
    # Power & Energy
    "NTPC":"POWER",       "POWERGRID":"POWER",  "TATAPOWER":"POWER",
    "ADANIGREEN":"POWER", "ADANIENSOL":"POWER", "ADANIPOWER":"POWER",
    # Infra, Capital Goods & Defence
    "LT":"INFRA",       "ADANIENT":"INFRA",   "ADANIPORTS":"INFRA",
    "BEL":"INFRA",      "HAL":"INFRA",        "SIEMENS":"INFRA",
    "ABB":"INFRA",      "CGPOWER":"INFRA",    "CUMMINSIND":"INFRA",
    "MAZDOCK":"INFRA",  "SOLARINDS":"INFRA",  "ENRIN":"INFRA",
    # Cement
    "ULTRACEMCO":"CEMENT", "GRASIM":"CEMENT",
    "AMBUJACEM":"CEMENT",  "SHREECEM":"CEMENT",
    # Consumer Discretionary
    "TITAN":"CONS",      "TRENT":"CONS",      "ASIANPAINT":"CONS",
    "PIDILITIND":"CONS", "INDHOTEL":"CONS",   "DLF":"CONS",
    "LODHA":"CONS",
    # Telecom
    "BHARTIARTL":"TELECOM",
    # Others / Diversified
    "BAJAJHLDNG":"OTHER", "ETERNAL":"OTHER",
    "INDIGO":"OTHER",
}

_INDIA_SECTOR_INDEX: dict[str, str] = {
    "BANK":     "^NSEBANK",
    "IT":       "^CNXIT",
    "AUTO":     "^CNXAUTO",
    "PHARMA":   "^CNXPHARMA",
    "METALS":   "^CNXMETAL",
    "FMCG":     "^CNXFMCG",
    "OIL&GAS":  "NIFTY_OIL_AND_GAS.NS",
    "POWER":    "^CNXENERGY",
    "INFRA":    "^CNXINFRA",
    "CEMENT":   "^CNXCONSUM",
    "CONS":     "^CNXCONSUM",
    "TELECOM":  "^CNXIT",
    "OTHER":    None,
}

INDIA_SECTOR_MAP: dict[str, str] = {
    f"{sym}.NS": _INDIA_SECTOR_INDEX[label]
    for sym, label in _INDIA_SECTOR_LABEL.items()
    if _INDIA_SECTOR_INDEX.get(label)
}
USA_SECTOR_MAP: dict[str, str] = {
    # Technology → XLK
    **{s: "XLK" for s in [
        "NVDA","MSFT","AAPL","AVGO","AMD","ORCL","ADBE","PANW",
        "NOW","SNPS","CRM","CSCO","INTC","QCOM","AMAT","LRCX","SMCI",
    ]},
    # Communication Services → XLC
    **{s: "XLC" for s in [
        "GOOGL","GOOG","META","NFLX","CMCSA","DIS",
        "TMUS","VZ","T","CHTR","SPOT","RBLX",
    ]},
    # Consumer Discretionary → XLY
    **{s: "XLY" for s in [
        "AMZN","TSLA","HD","MCD","TJX","BKNG",
        "LOW","SBUX","NKE","MAR","ROST","EBAY","LULU",
    ]},
    # Consumer Staples → XLP
    **{s: "XLP" for s in [
        "WMT","PG","KO","PEP","COST","PM","MO","MDLZ","CL","MNST",
    ]},
    # Healthcare → XLV
    **{s: "XLV" for s in [
        "LLY","UNH","JNJ","MRK","ABBV","TMO","AMGN","BMY",
        "GILD","ISRG","VRTX","CVS","CI","MDT","SYK","REGN",
    ]},
    # Financials → XLF
    **{s: "XLF" for s in [
        "JPM","BAC","MS","GS","V","MA","AXP","BLK",
        "SPGI","C","WFC","SCHW","COF","PGR","CB","MMC","HOOD","SOFI",
    ]},
    # Industrials → XLI
    **{s: "XLI" for s in [
        "GE","CAT","UNP","HON","LMT","UPS","RTX","DE",
        "FDX","BA","GEV","ETN","ADP","FAST","CTAS",
    ]},
    # Energy → XLE
    **{s: "XLE" for s in [
        "XOM","CVX","COP","NEE","SO","DUK","CEG","VST",
        "SLB","EOG","KMI","PSX",
    ]},
    # Materials → XLB
    **{s: "XLB" for s in [
        "LIN","FCX","SHW","NEM","APD","ECL","NUE","DOW",
    ]},
    # Real Estate → XLRE
    **{s: "XLRE" for s in [
        "PLD","AMT","EQIX","DLR","WELL","SPG","PSA","O","CBRE","VTR",
    ]},
    # Utilities → XLU
    **{s: "XLU" for s in [
        "EXC","XEL","AEP","SRE","D","PEG","WEC",
    ]},
}

LOOKBACK_DAYS  = 365          # extended to 1 year for 52-week high calculation
AVG_VOL_PERIOD = 10

WEIGHTS = {
    "volume_surge":   20,
    "rsi_zone":       15,
    "macd_bullish":   15,
    "above_ema":      15,
    "price_breakout": 15,
    "near_52w_high":  10,
    "adx_trend":      10,
    "sector_bonus":   10,   # scaled: +5 green sector, +10 if top-3 sector of day
    "candle_pattern": 10,   # Morning Star=10, Engulfing=8, Hammer=6
    "marubozu":       12,   # Closing Marubozu — strongest BTST signal
    "rel_strength":    5,   # stock beats index % change today
    "weekly_confirm":  8,   # close > weekly EMA20 (multi-timeframe)
    "gap_up":          8,   # gap-up open that held by close (+5 mild / +8 strong)
}

# ── Max theoretical raw score (used for normalization to 0-100) ──────
# Core signals max: vol(20)+rsi(15)+macd(15)+ema(15)+breakout(15)+52w(10)+adx(10) = 100
# Bonus signals max: sector(10)+candle(10)+marubozu(12)+rs(5)+mtf(8)+gap(8) = 53
# Total = 153 pts.  Normalized score = raw / MAX_RAW_SCORE * 100  → 0-100 scale.
MAX_RAW_SCORE   = 153.0   # denominator for BTST score normalisation → 0-100
MAX_ORB_SCORE   = 95.0    # max theoretical ORB score (used for HTML progress bar)

# ── TA fallback values (used when indicator calc returns None / too few bars) ──
RSI_FALLBACK        = 50    # neutral RSI — neither overbought nor oversold
ATR_FALLBACK_FACTOR = 0.5   # ATR ≈ 50% of today's high-low range when ATR unavailable

# ── Market detection heuristic ─────────────────────────────
# India large-caps rarely exceed ~5M shares/day; US mega-caps easily do.
# Used in filter_and_rank to pick the right liquidity gate without a country flag.
INDIA_USA_VOL_THRESHOLD = 5_000_000

# ── Enhancement constants ─────────────────────────────────
AD_RATIO_MIN = 1.5   # min Advance/Decline ratio for high-conviction BTST trades
SECTOR_TOP_N = 3     # top-N sectors by % gain get the full sector bonus

# ── Liquidity thresholds (absolute avg daily volume) ──────
LIQUIDITY_MIN_INDIA = 100_000    # shares/day — filters truly illiquid stocks only
LIQUIDITY_MIN_USA   = 1_000_000  # shares/day — S&P 500 should always pass

# ── Entry quality thresholds ──────────────────────────────
ENTRY_MAX_EMA_DIST_PCT  = 6.0    # avoid if close > 6% above EMA20 (overextended)
ENTRY_MAX_DAY_CHG_PCT   = 8.0    # avoid if day change > 8% (unless high vol breakout)
ENTRY_HIGH_VOL_EXEMPTION = 2.5   # vol_ratio >= this exempts the overextension check

# ── Score conviction tiers ────────────────────────────────
SCORE_HIGH_CONVICTION = 80
SCORE_GOOD            = 70
SCORE_MODERATE        = 55

# ── Market direction scalar multipliers ───────────────────
MKT_MULT_STRONG  = 1.10   # index +1% or better
MKT_MULT_NEUTRAL = 1.00   # 0% to +1%
MKT_MULT_SOFT    = 0.92   # –0.5% to 0%
MKT_MULT_WEAK    = 0.80   # below –0.5%

# ── Position sizing (FIX: professional risk management) ───
# Risk 1% of capital per trade — adjust CAPITAL to your actual portfolio size.
CAPITAL_INDIA      = 500_000   # ₹5 lakh default (adjust to your actual capital)
CAPITAL_USA        = 10_000    # $10,000 default (adjust to your actual capital)
RISK_PER_TRADE_PCT = 1.0       # never risk more than 1% of capital on a single BTST

# ── Minimum R:R required to take a trade (FIX: new filter) ──
MIN_RR_RATIO = 1.5   # reward must be ≥ 1.5× risk — a 1:1 trade is not worth BTST risk

IST = ZoneInfo("Asia/Kolkata")
EST = ZoneInfo("America/New_York")

# ══════════════════════════════════════════════════════════
# ORB (Opening Range Breakout) CONFIG  — intraday 5-min
# ══════════════════════════════════════════════════════════
# Opening Range = first ORB_BARS × 5-min candles after market open
#   India: 9:15–9:30 AM IST  (3 bars)
#   USA  : 9:30–9:45 AM EST  (3 bars)

ORB_BARS = 3          # legacy default (India, 5-min bars) — kept for the old
                      # single-symbol score_orb_stock() path, unused elsewhere

# tick_hub (India) serves native 5-min candles → opening range = 3 × 5m = 15min.
# schwab_proxy (USA) only serves 15-min candles (no 5m) → opening range =
# 1 × 15m = 15min. Same real-world opening-range duration, different bar count.
ORB_BARS_INDIA = 3
ORB_BARS_USA   = 1

# After this many hours from market open, ORB results are stale/unactionable.
# India open 9:15 AM IST → valid until ~1:15 PM IST
# USA   open 9:30 AM EST → valid until ~1:30 PM EST
ORB_SCAN_WINDOW_HOURS = 4

ORB_WEIGHTS = {
    "breakout_strength": 25,   # how far price is above ORB high
    "volume_surge":      20,   # current bar vol vs ORB avg vol
    "rsi_5m":            15,   # RSI on 5m >= 55
    "adx_5m":            10,   # ADX on 5m >= 25
    "orb_range_tight":   10,   # tight ORB = higher conviction
    "open_candle_bull":   8,   # first bar of day was green
    "sector_bonus":        7,  # sector index green (shared with BTST)
}
# Max ORB score ~95 pts


# ══════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════

def hdr(msg):
    print(f"\n{Fore.CYAN}{'─'*62}\n  {msg}\n{'─'*62}{Style.RESET_ALL}")


def _flatten(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def _load_prev_scores(prefix: str, date_str: str) -> dict[str, float]:
    """
    Load the most recent previous day's top CSV to get prior BTST scores.
    Walks back up to 4 days (handles weekends / holidays).
    Returns {Symbol: BTST_Score} or {} if no file found.
    """
    base = datetime.strptime(date_str, "%Y-%m-%d")
    for days_back in range(1, 5):
        prev = (base - timedelta(days=days_back)).strftime("%Y-%m-%d")
        try:
            df = pd.read_csv(f"btst_{prefix}_{prev}.csv")
            if "Symbol" in df.columns and "BTST_Score" in df.columns:
                return dict(zip(df["Symbol"].astype(str), df["BTST_Score"].astype(float)))
        except FileNotFoundError:
            continue
        except Exception:
            break
    return {}


# ══════════════════════════════════════════════════════════
# FIX: EARNINGS FILTER  — skip stocks reporting tomorrow
# ══════════════════════════════════════════════════════════

def check_earnings_tomorrow(symbol: str) -> bool:
    """
    Returns True if the stock has an earnings announcement on the next
    trading day.  Earnings = the #1 overnight gap risk for BTST.
    Uses yfinance .calendar — may return empty for Indian stocks.
    Silently returns False on any failure so screener keeps running.
    """
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None or cal.empty:
            return False
        # .calendar returns a DataFrame; 'Earnings Date' is a column
        if "Earnings Date" in cal.columns:
            dates = pd.to_datetime(cal["Earnings Date"], errors="coerce").dropna()
        elif "Earnings Date" in cal.index:
            dates = pd.to_datetime(cal.loc["Earnings Date"], errors="coerce")
            if not hasattr(dates, "__iter__"):
                dates = pd.Series([dates])
        else:
            return False
        tomorrow = (datetime.now(tz=IST) + timedelta(days=1)).date()
        for d in dates:
            if hasattr(d, "date") and d.date() == tomorrow:
                return True
        return False
    except Exception:
        return False   # fail-open — never block a scan on calendar failure


# ══════════════════════════════════════════════════════════
# MARKET HEALTH CHECK
# ══════════════════════════════════════════════════════════

def check_market(market: str = "india") -> tuple[bool, float, float]:
    """
    Returns (is_safe, index_chg_pct, vix_value)
    market: 'india' | 'usa'
    """
    idx_sym = INDIA_INDEX if market == "india" else USA_INDEX
    vix_sym = INDIA_VIX   if market == "india" else USA_VIX
    label   = "Nifty 50"  if market == "india" else "S&P 500"
    # FIX: use dynamic VIX threshold — 60-day rolling median × 1.30
    # Hardcoded 18/20 blocked valid BTST days when volatility was elevated but normal.
    # A percentile-based gate adapts to the current volatility regime.
    VIX_STATIC_FALLBACK = 18 if market == "india" else 20

    hdr(f"Market Health — {label.upper()}")

    try:
        with _YF_LOCK:
            combined = yf.download(
                [idx_sym, vix_sym],
                period="5d",
                interval="1d",
                progress=False,
                auto_adjust=True,
                group_by="ticker",
                threads=False,
            )
        if isinstance(combined.columns, pd.MultiIndex):
            idx = combined[idx_sym].dropna()
            vix = combined[vix_sym].dropna()
        else:
            idx = combined.dropna()
            vix = pd.DataFrame()
    except Exception:
        print(f"  {Fore.YELLOW}⚠  Could not fetch market data{Style.RESET_ALL}")
        return True, 0.0, 0.0   # fail-open: assume safe market so scan still runs

    if idx.empty:
        return True, 0.0, 0.0   # fail-open: no data returned, proceed without blocking

    close = float(idx["Close"].iloc[-1])
    prev  = float(idx["Close"].iloc[-2]) if len(idx) >= 2 else close
    chg   = round((close - prev) / prev * 100, 2)
    vix_v = float(vix["Close"].iloc[-1]) if not vix.empty else 0.0

    # FIX: dynamic VIX threshold — 60-day median × 1.30 (adapts to regime)
    if not vix.empty and len(vix) >= 20:
        vix_median = float(vix["Close"].tail(60).median())
        vix_thr    = round(vix_median * 1.30, 1)
    else:
        vix_thr    = VIX_STATIC_FALLBACK

    bullish  = chg >= -0.5
    vix_safe = vix_v < vix_thr if vix_v > 0 else True
    safe     = bullish and vix_safe

    col = Fore.GREEN if bullish else Fore.RED
    print(f"  {label} Change : {col}{chg:+.2f}%{Style.RESET_ALL}  (Close: {close:,.2f})")
    print(f"  VIX            : {'🟢' if vix_safe else '🔴'} {vix_v:.2f}  "
          f"({'Safe' if vix_safe else 'HIGH — caution!'})")
    if not bullish:
        print(f"  {Fore.RED}⚠  Market weak today — higher BTST risk{Style.RESET_ALL}")
    if not vix_safe:
        print(f"  {Fore.RED}⚠  VIX > {vix_thr} — avoid overnight positions{Style.RESET_ALL}")

    return safe, chg, vix_v


# ══════════════════════════════════════════════════════════
# BATCH DOWNLOAD — all tickers in one request
# ══════════════════════════════════════════════════════════

def _batch_download(symbols: list) -> dict:
    """
    Returns dict {symbol: DataFrame} of 1-year daily OHLCV (needed for the
    52-week-high check).

    USA  → schwab_proxy's /ohlcv?tf=1d (hub already seeds+caches a full year
           of daily bars per symbol — no yfinance call, no extra Schwab hits).
    India→ still yfinance directly. tick_hub has no daily-history endpoint
           (it only serves intraday 1m/5m/15m candles built from live ticks),
           and per your call this one piece stays on yfinance rather than
           adding a new endpoint to tick_hub.
    """
    is_usa = not any(s.endswith(".NS") for s in symbols)

    if is_usa:
        result = hub_client.us_daily_batch(symbols)
        result = {sym: df for sym, df in result.items() if len(df) >= 20}
        return result

    # ── India — unchanged yfinance path ──────────────────────────────────
    with _YF_LOCK:
        raw = yf.download(
            symbols,
            period="1y",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=False,        # yfinance internal threading disabled — we manage concurrency
        )
    result = {}
    for sym in symbols:
        try:
            df = raw[sym].dropna() if isinstance(raw.columns, pd.MultiIndex) else raw.dropna()
            if len(df) >= 20:
                result[sym] = df
        except Exception:
            pass
    return result


# ══════════════════════════════════════════════════════════
# SECTOR PERFORMANCE  — fetch all relevant sector indices/ETFs
# ══════════════════════════════════════════════════════════

def fetch_sector_perf(market: str) -> dict[str, float]:
    """
    Returns {sector_ticker: pct_change_today} for all sector benchmarks.
    Positive = sector up today, negative = down.

    USA  → schwab_proxy's cached /ohlcv?tf=1d series for each sector ETF
           (same cache the 52-week-high check reads — no extra Schwab hits).
    India→ still yfinance (sector indices like ^NSEBANK/^CNXIT have no daily
           history endpoint on tick_hub — same gap as the 52-week-high check).
    """
    sector_map = INDIA_SECTOR_MAP if market == "india" else USA_SECTOR_MAP
    tickers    = list(set(sector_map.values()))

    print(f"  📊  Fetching sector data ({len(tickers)} indices/ETFs) …", flush=True)

    if market == "usa":
        result = hub_client.us_sector_daily(tickers)
        up_count = sum(1 for v in result.values() if v > 0)
        print(f"  ✅  Sectors: {up_count}/{len(result)} green today.")
        return result

    try:
        with _YF_LOCK:
            raw = yf.download(
                tickers,
                period="5d",
                interval="1d",
                progress=False,
                auto_adjust=True,
                group_by="ticker",
                threads=False,
            )
    except Exception:
        return {}

    result: dict[str, float] = {}
    for ticker in tickers:
        try:
            df = raw[ticker].dropna() if isinstance(raw.columns, pd.MultiIndex) else raw.dropna()
            if len(df) >= 2:
                prev_c = float(df["Close"].iloc[-2])
                cur_c  = float(df["Close"].iloc[-1])
                result[ticker] = round((cur_c - prev_c) / prev_c * 100, 3) if prev_c > 0 else 0.0
        except Exception:
            pass

    up_count = sum(1 for v in result.values() if v > 0)
    print(f"  ✅  Sectors: {up_count}/{len(result)} green today.")
    return result


def _top_sectors(sector_perf: dict[str, float], n: int = SECTOR_TOP_N) -> set[str]:
    """Return the set of ticker symbols for the top-N performing sectors today."""
    sorted_secs = sorted(sector_perf.items(), key=lambda x: x[1], reverse=True)
    return {t for t, _ in sorted_secs[:n]}


# ══════════════════════════════════════════════════════════
# SCORE A SINGLE STOCK (from pre-downloaded DataFrame)
# ══════════════════════════════════════════════════════════

def score_stock_from_df(symbol: str, df: pd.DataFrame,
                        sector_bonus: float = 0.0,
                        index_chg: float = 0.0,
                        breadth_ok: bool = True) -> dict | None:
    try:
        df = df.copy()
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        close  = float(df["Close"].iloc[-1])
        high   = float(df["High"].iloc[-1])
        low    = float(df["Low"].iloc[-1])
        volume = float(df["Volume"].iloc[-1])

        # ── Enhancement 1: Relative Volume (RVOL) ────────────────
        # Compare today's volume against a 14-day rolling average of daily volume.
        # Additionally, if today is a Friday (weekday=4), use a 5-day
        # Friday-only average to correct for the weekly cycle effect.
        avg_vol_10 = df["Volume"].iloc[-AVG_VOL_PERIOD-1:-1].mean()
        weekday    = df.index[-1].weekday() if hasattr(df.index[-1], "weekday") else -1

        if weekday == 4 and len(df) >= 10:
            # FIX: Friday RVOL — require at least 2 Friday data-points before use.
            # Original code silently used an empty list average on sparse history.
            friday_vols = [
                float(df["Volume"].iloc[i])
                for i in range(-2, -len(df)-1, -1)
                if hasattr(df.index[i], "weekday") and df.index[i].weekday() == 4
            ][:4]
            if len(friday_vols) >= 2:
                avg_vol = sum(friday_vols) / len(friday_vols)
            else:
                avg_vol = avg_vol_10   # FIX: fall back when fewer than 2 Fridays found
        else:
            avg_vol = avg_vol_10

        vol_ratio = volume / avg_vol if avg_vol > 0 else 0
        s_vol     = (WEIGHTS["volume_surge"] if vol_ratio >= 1.5 else
                     WEIGHTS["volume_surge"] * 0.5 if vol_ratio >= 1.1 else 0)

        # ── RSI ───────────────────────────────────────────────────
        rsi_s = ta.rsi(df["Close"], length=14)
        rsi   = float(rsi_s.iloc[-1]) if rsi_s is not None else RSI_FALLBACK
        s_rsi = (WEIGHTS["rsi_zone"] if 55 <= rsi <= 75 else
                 WEIGHTS["rsi_zone"] * 0.5 if 50 <= rsi < 55 else 0)

        # ── MACD ──────────────────────────────────────────────────
        # FIX: Three-tier MACD scoring:
        #   1. Fresh crossover (MACD line crosses above signal line) → FULL points
        #   2. Histogram positive AND expanding (momentum building)  → 0.85×
        #   3. Histogram positive BUT shrinking (momentum fading)    → 0.40×
        #      (shrinking histogram is a bearish divergence — was scored 0.7× before)
        #   4. Histogram negative → 0 pts
        macd_df   = ta.macd(df["Close"], fast=12, slow=26, signal=9)
        s_macd    = 0
        macd_hist = None
        macd_crossover = False
        if macd_df is not None and not macd_df.empty:
            hcol = [c for c in macd_df.columns if "MACDh" in c]
            mcol = [c for c in macd_df.columns if c.startswith("MACD_")
                    and "MACDs" not in c and "MACDh" not in c]
            scol = [c for c in macd_df.columns if "MACDs" in c]
            if hcol:
                macd_hist = float(macd_df[hcol[0]].iloc[-1])
                prev_h    = float(macd_df[hcol[0]].iloc[-2])
                hist_growing = macd_hist > prev_h   # FIX: histogram expanding?
                # FIX: detect MACD line crossing above signal line (fresh crossover)
                if mcol and scol and len(macd_df) >= 2:
                    ml_now  = float(macd_df[mcol[0]].iloc[-1])
                    ml_prev = float(macd_df[mcol[0]].iloc[-2])
                    sl_now  = float(macd_df[scol[0]].iloc[-1])
                    sl_prev = float(macd_df[scol[0]].iloc[-2])
                    macd_crossover = (ml_prev <= sl_prev) and (ml_now > sl_now)
                if macd_crossover:
                    s_macd = WEIGHTS["macd_bullish"]          # fresh crossover — strongest
                elif macd_hist > 0 and hist_growing:
                    s_macd = WEIGHTS["macd_bullish"] * 0.85   # expanding positive hist
                elif macd_hist > 0:
                    s_macd = WEIGHTS["macd_bullish"] * 0.40   # FIX: shrinking = weak signal
                else:
                    s_macd = 0

        # ── EMA ───────────────────────────────────────────────────
        ema20 = float(ta.ema(df["Close"], length=20).iloc[-1])
        ema50 = float(ta.ema(df["Close"], length=50).iloc[-1])
        s_ema = (WEIGHTS["above_ema"] if close > ema20 and close > ema50
                 else WEIGHTS["above_ema"] * 0.5 if close > ema20 else 0)

        # ── Price breakout (intraday range position) ──────────────
        rng   = high - low
        pos   = (close - low) / rng if rng > 0 else 0
        s_brk = (WEIGHTS["price_breakout"] if pos >= 0.90
                 else WEIGHTS["price_breakout"] * 0.6 if pos >= 0.75 else 0)

        # ── ADX ───────────────────────────────────────────────────
        adx_df  = ta.adx(df["High"], df["Low"], df["Close"], length=14)
        adx_val = 0.0
        s_adx   = 0
        if adx_df is not None and not adx_df.empty:
            ac = [c for c in adx_df.columns if c.startswith("ADX_")]
            if ac:
                adx_val = float(adx_df[ac[0]].iloc[-1])
                s_adx   = (WEIGHTS["adx_trend"] if adx_val >= 25
                           else WEIGHTS["adx_trend"] * 0.5 if adx_val >= 20 else 0)

        # ── 52-Week High Proximity ────────────────────────────────
        w52_high   = float(df["High"].max())
        proximity  = close / w52_high if w52_high > 0 else 0
        near_52w   = proximity >= 0.95
        s_52w      = (WEIGHTS["near_52w_high"] if proximity >= 0.95 else
                      WEIGHTS["near_52w_high"] * 0.5 if proximity >= 0.90 else 0)

        # ── ATR (used for penalty + SL/Target) ───────────────────
        atr_s   = ta.atr(df["High"], df["Low"], df["Close"], length=14)
        atr_val = float(atr_s.iloc[-1]) if atr_s is not None and not atr_s.empty else (rng * ATR_FALLBACK_FACTOR)

        # ── Day change ────────────────────────────────────────────
        prev_close = float(df["Close"].iloc[-2])
        day_chg    = (close - prev_close) / prev_close * 100

        # ── Enhancement 4: Closing Marubozu + final-hour fade check ──
        # Marubozu: open ≈ low AND close ≈ high (body fills ≥85% of range).
        # Also detect "fade" = stock weakens into close (bearish for overnight).
        candle_name  = ""
        s_candle     = 0
        s_marubozu   = 0.0
        is_marubozu  = False
        final_hr_fade = False
        try:
            o0    = float(df["Open"].iloc[-1])
            o1    = float(df["Open"].iloc[-2])
            c1    = prev_close
            o2    = float(df["Open"].iloc[-3])
            c2    = float(df["Close"].iloc[-3])
            body0 = abs(close - o0)
            body1 = abs(c1 - o1)
            body2 = abs(c2 - o2)
            lower_shadow0 = min(o0, close) - low
            upper_shadow0 = high - max(o0, close)

            # FIX: Closing Marubozu — only the upper shadow matters.
            # A closing marubozu = green candle that closes near the HIGH.
            # The lower shadow can be large (stock dipped and recovered — bullish).
            # The original code required BOTH shadows to be tiny, which is a
            # "Full Marubozu" (much rarer).  This fix captures more valid signals.
            if (rng > 0 and close > o0 and
                    body0 >= rng * 0.85 and
                    (high - close) <= rng * 0.05):   # only upper shadow check
                is_marubozu  = True
                s_marubozu   = WEIGHTS["marubozu"]
                candle_name  = "Marubozu"

            # Morning Star (3-candle, strongest reversal)
            morning_star = (
                c2 < o2 and
                body1 <= body2 * 0.4 and
                close > o0 and
                close > (o2 + c2) / 2 and
                body0 >= body2 * 0.5
            )
            # Bullish Engulfing
            bullish_engulfing = (
                c1 < o1 and
                close > o0 and
                o0 <= c1 and
                close >= o1
            )
            # Hammer
            hammer = (
                rng > 0 and
                body0 <= rng * 0.35 and
                lower_shadow0 >= 2.0 * body0 and
                upper_shadow0 <= body0 * 0.6 and
                close > o0
            )

            if not is_marubozu:
                if morning_star:
                    s_candle, candle_name = WEIGHTS["candle_pattern"],       "Morning Star"
                elif bullish_engulfing:
                    s_candle, candle_name = WEIGHTS["candle_pattern"] * 0.8, "Engulfing"
                elif hammer:
                    s_candle, candle_name = WEIGHTS["candle_pattern"] * 0.6, "Hammer"

            # Final-hour fade: if we have intraday high available via proxy
            # (daily high much higher than close = stock gave back gains into close)
            # Proxy: if high > close*1.01 AND close < (high + low)/2  → fade detected
            if high > close * 1.008 and pos < 0.60:
                final_hr_fade = True

        except Exception:
            pass   # fewer than 3 rows or missing Open — skip

        # ── Relative Strength vs Index ────────────────────────────
        s_rs = WEIGHTS["rel_strength"] if day_chg > index_chg else 0.0

        # ── Multi-Timeframe Confirmation (weekly) ─────────────────
        s_mtf        = 0.0
        weekly_align = False
        try:
            weekly_close = df["Close"].resample("W").last().dropna()
            if len(weekly_close) >= 20:
                wema20       = float(ta.ema(weekly_close, length=20).iloc[-1])
                weekly_align = close > wema20
                s_mtf        = WEIGHTS["weekly_confirm"] if weekly_align else 0.0
        except Exception:
            pass

        # ── Enhancement 3: Smart ATR penalty ─────────────────────
        # Standard: penalise big moves that overextend.
        # Exception: if vol_ratio > 3.0 (institutional breakaway gap), skip penalty
        # because high-volume breakouts have strong follow-through overnight.
        total   = s_vol + s_rsi + s_macd + s_ema + s_brk + s_52w + s_adx
        atr_pct = (atr_val / close * 100) if close > 0 else 4.0
        if day_chg > max(1.5 * atr_pct, 4.0):
            if vol_ratio >= 3.0:
                pass   # breakaway gap on massive volume — do NOT penalise
            else:
                total *= 0.6

        # ── Gap-up and hold ───────────────────────────────────────
        gap_pct  = 0.0
        gap_held = False
        s_gap    = 0.0
        try:
            open0    = float(df["Open"].iloc[-1])
            gap_pct  = (open0 - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
            gap_held = close >= open0   # gap held = price closed at or above today's open (gap not filled)
            if gap_pct >= 1.0 and gap_held and pos >= 0.60:
                s_gap = WEIGHTS["gap_up"]
            elif gap_pct >= 0.5 and gap_held:
                s_gap = WEIGHTS["gap_up"] * 0.625
        except Exception:
            pass

        # ── Additive bonuses (applied after penalty) ─────────────
        total += sector_bonus + s_candle + s_marubozu + s_rs + s_mtf + s_gap

        # ── Enhancement 4 cont: final-hour fade penalty ───────────
        if final_hr_fade:
            total *= 0.85   # 15% penalty for late-day weakness

        # ── Enhancement 5: Market Breadth penalty ─────────────────
        # Narrow market (A/D < threshold): reduce score to deprioritise
        # BTST trades when the rally is not broad-based.
        if not breadth_ok:
            total *= 0.80   # 20% score haircut in narrow-breadth sessions

        # ── Market Direction Scalar (replaces binary safe/unsafe) ─
        # Continuously adjusts score based on how strong the index is today.
        # Bullish market boosts conviction; weak market reduces it.
        if index_chg >= 1.0:
            total *= MKT_MULT_STRONG   # strongly bullish: +10% boost
        elif index_chg >= 0.0:
            total *= MKT_MULT_NEUTRAL  # mildly bullish: no change
        elif index_chg >= -0.5:
            total *= MKT_MULT_SOFT     # flat/slightly weak: –8%
        else:
            total *= MKT_MULT_WEAK     # clearly weak: –20%

        # ── Entry Quality Flag ────────────────────────────────────
        # Flag overextended entries: stock too far above the 20-day EMA
        # OR large single-day move without volume confirmation.
        ema_dist_pct = (close - ema20) / ema20 * 100 if ema20 > 0 else 0.0
        entry_overextended = (
            (ema_dist_pct > ENTRY_MAX_EMA_DIST_PCT and vol_ratio < ENTRY_HIGH_VOL_EXEMPTION) or
            (day_chg > ENTRY_MAX_DAY_CHG_PCT and vol_ratio < ENTRY_HIGH_VOL_EXEMPTION)
        )
        if entry_overextended:
            total *= 0.70   # heavy penalty — still shows up but ranks low

        # ── Stop-Loss and Target ──────────────────────────────────
        stop_loss = round(max(low, close - atr_val), 2)
        # Next-day target range: 1.5% to 3% above close (min of ATR-based and 3% cap)
        target_atr  = close + 1.5 * atr_val
        target_pct3 = close * 1.03
        target      = round(min(target_atr, target_pct3), 2)
        # Conservative SL: also cap loss at –2% to enforce discipline
        sl_pct2     = close * 0.98
        stop_loss   = round(max(stop_loss, sl_pct2), 2)
        risk        = close - stop_loss
        reward      = target - close
        rr_ratio    = round(reward / risk, 2) if risk > 0 else 0.0

        # ── Normalise to 0-100 scale ─────────────────────────────
        # Raw total can exceed 100 when many bonuses stack; normalising makes
        # the conviction tiers (80 / 70 / 55) meaningful relative to max possible.
        raw_total = total
        total     = min(round(raw_total / MAX_RAW_SCORE * 100, 1), 100.0)

        # ── FIX: Earnings tomorrow → 50% score penalty ────────────
        # Holding overnight into earnings is the #1 BTST risk (10–20% gap).
        # We don't hard-filter (some traders accept the risk) but rank it very low.
        has_earnings = check_earnings_tomorrow(symbol)
        if has_earnings:
            total = round(total * 0.50, 1)

        # ── Conviction label ──────────────────────────────────────
        if total >= SCORE_HIGH_CONVICTION:
            conviction = "HIGH"
        elif total >= SCORE_GOOD:
            conviction = "GOOD"
        elif total >= SCORE_MODERATE:
            conviction = "MODERATE"
        else:
            conviction = "WEAK"

        clean_sym = (symbol.replace(".NS", "")
                           .replace("-", ".")
                           .replace("BRK.B", "BRK-B"))

        # ── FIX: Position sizing based on 1% capital-at-risk ──────
        # Shares = (Capital × Risk%) / (Close − Stop_Loss)
        # Tells you exactly how many shares to buy so a SL hit costs 1% of capital.
        is_india_sym = symbol.endswith(".NS")
        capital      = CAPITAL_INDIA if is_india_sym else CAPITAL_USA
        risk_amt     = capital * RISK_PER_TRADE_PCT / 100.0
        risk_per_share = close - stop_loss
        suggested_shares = int(risk_amt / risk_per_share) if risk_per_share > 0 else 0
        position_value   = round(suggested_shares * close, 2)

        return {
            "Symbol":             clean_sym,
            "Close":              round(close, 2),
            "Change%":            round(day_chg, 2),
            "Volume_Ratio":       round(vol_ratio, 2),
            "Avg_Vol":            round(avg_vol, 0),
            "RSI":                round(rsi, 1),
            "MACD_Hist":          round(macd_hist, 4) if macd_hist is not None else None,
            "MACD_Crossover":     macd_crossover,
            "EMA20":              round(ema20, 2),
            "EMA50":              round(ema50, 2),
            "EMA_Dist%":          round(ema_dist_pct, 2),
            "ADX":                round(adx_val, 1),
            "Range_Pos%":         round(pos * 100, 1),
            "ATR":                round(atr_val, 2),
            "52W_High":           round(w52_high, 2),
            "Near_52W_High":      near_52w,
            "Candle":             candle_name,
            "Marubozu":           is_marubozu,
            "FinalHrFade":        final_hr_fade,
            "RS_Beat":            day_chg > index_chg,
            "Weekly_Align":       weekly_align,
            "Gap_Up":             gap_pct >= 0.5 and gap_held,
            "Gap_Pct":            round(gap_pct, 2),
            "Sector_Align":       sector_bonus > 0,
            "Breadth_OK":         breadth_ok,
            "Entry_Overextended": entry_overextended,
            "Has_Earnings":       has_earnings,
            "Stop_Loss":          stop_loss,
            "Target":             target,
            "Target%":            round((target / close - 1) * 100, 2),
            "SL%":                round((1 - stop_loss / close) * 100, 2),
            "RR_Ratio":           rr_ratio,
            "Shares":             suggested_shares,
            "Position_Value":     position_value,
            "Conviction":         conviction,
            "BTST_Score":         round(total, 1),
        }
    except Exception as e:
        print(f"  {Fore.YELLOW}⚠  Skipping {symbol}: {e}{Style.RESET_ALL}")
        return None


# ══════════════════════════════════════════════════════════
# ORB SCORE — single stock, live 5-min intraday data
# ══════════════════════════════════════════════════════════

def score_orb_stock(symbol: str, sector_bonus: float = 0.0) -> dict | None:
    """
    Download today's 5-min bars, identify the Opening Range (first ORB_BARS bars),
    and score bullish breakouts above the ORB High.
    Returns None if no breakout or insufficient data.
    Same fixes as score_orb_stock_from_df — uses best breakout bar, not last bar.
    """
    try:
        # ── Fetch intraday 5-min data (2 days to guarantee today's bars) ──
        with _YF_LOCK:
            raw = yf.download(symbol, period="2d", interval="5m",
                              progress=False, auto_adjust=True, threads=False)
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        raw = raw.dropna()
        if len(raw) < ORB_BARS + 2:
            return None

        # ── Localise index to market timezone ────────────────────────
        is_india = symbol.endswith(".NS")
        tz       = IST if is_india else EST

        if raw.index.tzinfo is None:
            raw.index = raw.index.tz_localize("UTC")
        raw.index = raw.index.tz_convert(tz)

        # ── Keep only today's bars ────────────────────────────────────
        today_date = datetime.now(tz=tz).date()
        df = raw[raw.index.date == today_date].copy()
        if len(df) < ORB_BARS + 1:
            return None          # market not open long enough yet

        # ── Opening Range (first ORB_BARS bars) ──────────────────────
        orb           = df.iloc[:ORB_BARS]
        orb_high      = float(orb["High"].max())
        orb_low       = float(orb["Low"].min())
        orb_range     = orb_high - orb_low
        orb_range_pct = (orb_range / orb_high * 100) if orb_high > 0 else 0.0

        # ── Fix 1 & 2: Find FIRST breakout bar after ORB ─────────────
        post_orb      = df.iloc[ORB_BARS:]
        if post_orb.empty:
            return None
        breakout_bars = post_orb[post_orb["High"] > orb_high]
        if breakout_bars.empty:
            return None   # price never crossed ORB high today

        # First breakout bar = actual real-time entry signal (no look-ahead bias)
        brk_bar_idx = breakout_bars.index[0]
        brk_bar     = df.loc[brk_bar_idx]
        brk_bar_pos = df.index.get_loc(brk_bar_idx)

        price   = float(brk_bar["Close"])
        brk_vol = float(brk_bar["Volume"])    # Fix 2: breakout bar volume

        # ── 1. Breakout strength ──────────────────────────────────────
        brk_pct = (price - orb_high) / orb_high * 100
        s_brk   = (ORB_WEIGHTS["breakout_strength"]        if brk_pct >= 1.0 else
                   ORB_WEIGHTS["breakout_strength"] * 0.72 if brk_pct >= 0.5 else
                   ORB_WEIGHTS["breakout_strength"] * 0.48)

        # ── 2. Volume surge at breakout bar vs ORB avg ───────────────
        orb_avg_vol = float(orb["Volume"].mean())
        vol_ratio   = brk_vol / orb_avg_vol if orb_avg_vol > 0 else 1.0
        s_vol = (ORB_WEIGHTS["volume_surge"]        if vol_ratio >= 2.0 else
                 ORB_WEIGHTS["volume_surge"] * 0.70 if vol_ratio >= 1.5 else
                 ORB_WEIGHTS["volume_surge"] * 0.40 if vol_ratio >= 1.2 else 0)

        # ── 3. RSI at breakout bar position (Fix 5) ──────────────────
        df_to_brk = df.iloc[:brk_bar_pos + 1]
        rsi_s = ta.rsi(df_to_brk["Close"], length=9)
        rsi   = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.empty else float(RSI_FALLBACK)
        s_rsi = (ORB_WEIGHTS["rsi_5m"]        if rsi >= 55 else
                 ORB_WEIGHTS["rsi_5m"] * 0.53 if rsi >= 50 else 0)

        # ── 4. ADX at breakout bar position (Fix 5) ──────────────────
        adx_df  = ta.adx(df_to_brk["High"], df_to_brk["Low"],
                         df_to_brk["Close"], length=7)
        adx_val = 0.0
        s_adx   = 0
        if adx_df is not None and not adx_df.empty:
            ac = [c for c in adx_df.columns if c.startswith("ADX_")]
            if ac:
                adx_val = float(adx_df[ac[0]].iloc[-1])
                s_adx   = (ORB_WEIGHTS["adx_5m"]        if adx_val >= 25 else
                           ORB_WEIGHTS["adx_5m"] * 0.50 if adx_val >= 20 else 0)

        # ── 5. ORB range quality ──────────────────────────────────────
        s_range = (ORB_WEIGHTS["orb_range_tight"]        if orb_range_pct <= 1.0 else
                   ORB_WEIGHTS["orb_range_tight"] * 0.70 if orb_range_pct <= 1.5 else
                   ORB_WEIGHTS["orb_range_tight"] * 0.40 if orb_range_pct <= 2.0 else 0)

        # ── 6. First bar of the day was bullish ───────────────────────
        first    = df.iloc[0]
        s_candle = (ORB_WEIGHTS["open_candle_bull"]
                    if float(first["Close"]) > float(first["Open"]) else 0)

        total = s_brk + s_vol + s_rsi + s_adx + s_range + s_candle + sector_bonus

        # ── ATR at breakout bar ───────────────────────────────────────
        atr_s   = ta.atr(df_to_brk["High"], df_to_brk["Low"],
                         df_to_brk["Close"], length=7)
        atr_val = (float(atr_s.iloc[-1])
                   if atr_s is not None and not atr_s.empty else orb_range)

        # ── Stop Loss and Target ──────────────────────────────────────
        stop_loss     = round(max(orb_low, price - atr_val), 2)
        target        = round(orb_high + 1.5 * orb_range, 2)
        risk          = price - stop_loss
        if risk <= 0:
            return None
        reward        = target - price
        rr_ratio      = round(reward / risk, 2) if reward > 0 else 0.0
        current_price = float(df.iloc[-1]["Close"])
        status        = "Target Hit ✅" if current_price >= target else "Active"

        clean_sym = (symbol.replace(".NS", "")
                           .replace("-", ".")
                           .replace("BRK.B", "BRK-B"))

        return {
            "Symbol":       clean_sym,
            "Status":       status,
            "Price":        round(price, 2),
            "Last_Price":   round(current_price, 2),
            "ORB_High":     round(orb_high, 2),
            "ORB_Low":      round(orb_low, 2),
            "ORB_Range%":   round(orb_range_pct, 2),
            "Brk_Pct":      round(brk_pct, 2),
            "Vol_Ratio":    round(vol_ratio, 2),
            "RSI_5m":       round(rsi, 1),
            "ADX_5m":       round(adx_val, 1),
            "Sector_Align": sector_bonus > 0,
            "Stop_Loss":    stop_loss,
            "Target":       target,
            "RR_Ratio":     rr_ratio,
            "ORB_Score":    round(total, 1),
        }
    except Exception as e:
        print(f"  {Fore.YELLOW}⚠  ORB skip {symbol}: {e}{Style.RESET_ALL}")
        return None


# ══════════════════════════════════════════════════════════
# RUN SCREENER  — batch download + parallel scoring
# ══════════════════════════════════════════════════════════

def fetch_advance_decline(market: str) -> float:
    """
    Fetch Advance/Decline ratio for the market.
    India: uses ^NSEI constituent proxies via Nifty 500 breadth (^CRSLDX vs NSEI).
    USA  : uses NYSE A/D via ^NYAD (ratio of advances to declines).
    Returns float ratio (advances/declines). Returns 0.0 on failure (treated as unknown).
    """
    # Best available A/D proxies on Yahoo Finance
    ad_sym = "^NYAD" if market == "usa" else "^NSEI"  # NYAD is a direct A/D line for NYSE
    try:
        if market == "usa":
            with _YF_LOCK:
                raw = yf.download("^NYAD", period="5d", interval="1d",
                                  progress=False, auto_adjust=True, threads=False)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            raw = raw.dropna()
            if len(raw) >= 2:
                today_chg = float(raw["Close"].iloc[-1]) - float(raw["Close"].iloc[-2])
                # NYAD cumulative line changes by thousands on strong days.
                # >2000 = very broad rally; >0 = more advances than declines; <=0 = narrow/falling.
                return 2.5 if today_chg > 2000 else 1.8 if today_chg > 0 else 0.8
        else:
            return 0.0   # will be computed from cache in run_screener
    except Exception:
        pass
    return 0.0


def run_screener(symbols: list, label: str,
                 index_chg: float = 0.0) -> tuple[pd.DataFrame, dict]:
    """
    Returns (results_df, sector_perf) so the caller can pass sector_perf
    to run_orb_screener and avoid a duplicate fetch.
    """
    hdr(f"Scanning {len(symbols)} {label} stocks (batch + parallel) …")

    market     = "india" if any(s.endswith(".NS") for s in symbols) else "usa"
    sector_map = INDIA_SECTOR_MAP if market == "india" else USA_SECTOR_MAP

    # Step 1+2 in PARALLEL: batch OHLCV download + sector perf fetch overlap
    print(f"  📥  Fetching 1-year OHLCV + sector data in parallel …", flush=True)
    with ThreadPoolExecutor(max_workers=3) as pre:
        f_cache   = pre.submit(_batch_download, symbols)
        f_sector  = pre.submit(fetch_sector_perf, market)
        f_ad      = pre.submit(fetch_advance_decline, market)
        cache       = f_cache.result()
        sector_perf = f_sector.result()
        ad_ratio_raw = f_ad.result()

    # ── Advance/Decline breadth ──────────────────────────────────
    # For India: compute from cache (what % of stocks closed up today)
    if market == "india" and cache:
        advances = sum(
            1 for df in cache.values()
            if len(df) >= 2 and float(df["Close"].iloc[-1]) > float(df["Close"].iloc[-2])
        )
        declines = len(cache) - advances
        ad_ratio = advances / max(declines, 1)
    else:
        ad_ratio = ad_ratio_raw

    breadth_ok = ad_ratio >= AD_RATIO_MIN or ad_ratio == 0.0  # 0.0 = unknown → don't block
    if ad_ratio > 0:
        col = Fore.GREEN if breadth_ok else Fore.YELLOW
        print(f"  📈  A/D Ratio: {col}{ad_ratio:.2f}x{Style.RESET_ALL}  "
              f"({'Broad rally ✅' if breadth_ok else 'Narrow market ⚠ — lower conviction'})")

    # ── Top-N sectors for scaled bonus ──────────────────────────
    top_sec_set = _top_sectors(sector_perf, SECTOR_TOP_N)

    print(f"  ✅  Downloaded {len(cache)}/{len(symbols)}. Scoring …", flush=True)

    def _sector_bonus(sym: str) -> float:
        sec_ticker = sector_map.get(sym)
        if not sec_ticker:
            return 0.0
        sec_chg = sector_perf.get(sec_ticker, None)
        if sec_chg is None or sec_chg <= 0:
            return 0.0                               # sector red → no bonus
        # Full bonus if in top-N sectors; half bonus otherwise
        return float(WEIGHTS["sector_bonus"]) if sec_ticker in top_sec_set \
               else float(WEIGHTS["sector_bonus"]) * 0.5

    # Step 3: score all stocks in parallel (TA calcs are CPU-bound)
    results = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {
            pool.submit(score_stock_from_df, sym, df,
                        _sector_bonus(sym), index_chg, breadth_ok): sym
            for sym, df in cache.items()
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            print(f"  ⚙️  Scoring [{done:>3}/{len(cache)}]", end="\r", flush=True)
            r = future.result()
            if r:
                results.append(r)

    print(" " * 60, end="\r")
    print(f"  ✅  Scored {len(results)} stocks successfully.")
    return pd.DataFrame(results), sector_perf


# ══════════════════════════════════════════════════════════
# BATCH INTRADAY DOWNLOAD — all tickers in ONE 5-min call
# ══════════════════════════════════════════════════════════

def _batch_download_intraday(symbols: list, orb_bars: int = ORB_BARS_INDIA) -> dict:
    """
    Returns {symbol: DataFrame} with today's intraday bars, tz-aware index.
    Warns if called outside the ORB valid window (first ORB_SCAN_WINDOW_HOURS after open).

    India → tick_hub's native 5-min candles (built live from ticks, /ohlcv/all).
    USA   → schwab_proxy's 15-min candles (its only intraday timeframe;
            /ohlcv?tf=15m). No yfinance call on either path.
    """
    is_india = any(s.endswith(".NS") for s in symbols)
    tz = IST if is_india else EST
    tf = "5m" if is_india else "15m"

    # ── Time-of-day guard: warn if outside ORB valid window ──────────
    now_local  = datetime.now(tz=tz)
    open_hour  = 9 if is_india else 9
    open_min   = 15 if is_india else 30
    market_open = now_local.replace(hour=open_hour, minute=open_min,
                                    second=0, microsecond=0)
    hours_since_open = (now_local - market_open).total_seconds() / 3600
    if hours_since_open > ORB_SCAN_WINDOW_HOURS:
        print(f"  {Fore.YELLOW}⚠  ORB Warning: Running {hours_since_open:.1f}h after market open. "
              f"Breakout bars are from earlier today — results are historical, not live.{Style.RESET_ALL}")
    elif hours_since_open < 0:
        print(f"  {Fore.YELLOW}⚠  ORB Warning: Market not yet open. No intraday bars available.{Style.RESET_ALL}")

    print(f"  📥  Fetching {tf} bars for {len(symbols)} tickers from "
          f"{'tick_hub' if is_india else 'schwab_proxy'} …", flush=True)
    try:
        if is_india:
            raw_result = hub_client.nse_intraday_batch(symbols, tf=tf)
        else:
            raw_result = hub_client.us_intraday_batch(symbols, tf=tf)
    except Exception as e:
        print(f"  ⚠  Intraday hub fetch failed: {e}")
        return {}

    today_date = datetime.now(tz=tz).date()
    result = {}
    for sym, df in raw_result.items():
        try:
            if df.empty:
                continue
            if df.index.tzinfo is None:
                df.index = df.index.tz_localize("UTC")
            df.index = df.index.tz_convert(tz)
            df = df[df.index.date == today_date]
            if len(df) >= orb_bars + 1:
                result[sym] = df
        except Exception:
            pass

    print(f"  ✅  Intraday data: {len(result)}/{len(symbols)} symbols with enough bars.")
    return result


def score_orb_stock_from_df(symbol: str, df: pd.DataFrame, sector_bonus: float = 0.0,
                            orb_bars: int = ORB_BARS_INDIA) -> dict | None:
    """
    Score ORB for a single stock using a pre-downloaded intraday DataFrame.
    `orb_bars` = how many opening bars define the opening range — 3×5m bars
    for India (tick_hub), 1×15m bar for USA (schwab_proxy); both equal a
    15-minute opening range in real time, just different bar granularity.

    Fixes applied vs original:
    1. Breakout detection scans ALL post-ORB bars for the best breakout bar
       (not just the last bar). Catches stocks that broke out in the morning
       but pulled back by the time screener runs at EOD.
    2. Volume check uses the BREAKOUT bar's volume, not the last bar's volume.
       EOD bars have naturally low volume and would fail every vol filter.
    3. RSI and ADX computed at the breakout bar index, not at EOD.
    4. Stocks that already hit target are shown as "Target Hit" instead of dropped.
    """
    try:
        is_india  = symbol.endswith(".NS")
        orb       = df.iloc[:orb_bars]
        orb_high  = float(orb["High"].max())
        orb_low   = float(orb["Low"].min())
        orb_range = orb_high - orb_low
        orb_range_pct = (orb_range / orb_high * 100) if orb_high > 0 else 0.0

        # ── Post-ORB bars (everything after the opening range) ───────
        post_orb = df.iloc[orb_bars:]
        if post_orb.empty:
            return None

        # ── Fix 1 & 2: Find the FIRST breakout bar after ORB ────────
        # Use the first bar that crosses ORB high — this is the actual entry
        # signal in real trading.  Using highest-close bar introduces look-ahead
        # bias (you cannot know which bar will peak at the time of breakout).
        # "Target Hit" display below still uses the current last price.
        breakout_bars = post_orb[post_orb["High"] > orb_high]
        if breakout_bars.empty:
            return None   # price never crossed ORB high at any point today

        # First breakout bar = the earliest bar whose High exceeded ORB high
        brk_bar_idx = breakout_bars.index[0]
        brk_bar     = df.loc[brk_bar_idx]
        brk_bar_pos = df.index.get_loc(brk_bar_idx)   # integer position in df

        price   = float(brk_bar["Close"])
        brk_vol = float(brk_bar["Volume"])             # Fix 2: use breakout bar volume

        # ── 1. Breakout strength (measured from breakout bar close) ──
        brk_pct = (price - orb_high) / orb_high * 100
        s_brk   = (ORB_WEIGHTS["breakout_strength"]        if brk_pct >= 1.0 else
                   ORB_WEIGHTS["breakout_strength"] * 0.72 if brk_pct >= 0.5 else
                   ORB_WEIGHTS["breakout_strength"] * 0.48)

        # ── 2. Volume surge at breakout bar vs ORB avg ───────────────
        orb_avg_vol = float(orb["Volume"].mean())
        vol_ratio   = brk_vol / orb_avg_vol if orb_avg_vol > 0 else 1.0
        s_vol = (ORB_WEIGHTS["volume_surge"]        if vol_ratio >= 2.0 else
                 ORB_WEIGHTS["volume_surge"] * 0.70 if vol_ratio >= 1.5 else
                 ORB_WEIGHTS["volume_surge"] * 0.40 if vol_ratio >= 1.2 else 0)

        # ── 3. RSI at breakout bar position (Fix 5) ──────────────────
        # Compute RSI on bars up to and including the breakout bar,
        # so momentum reflects conditions at the moment of breakout.
        df_to_brk = df.iloc[:brk_bar_pos + 1]
        rsi_s = ta.rsi(df_to_brk["Close"], length=9)   # length=9 suits intraday 5-min
        rsi   = float(rsi_s.iloc[-1]) if rsi_s is not None and not rsi_s.empty else float(RSI_FALLBACK)
        s_rsi = (ORB_WEIGHTS["rsi_5m"]        if rsi >= 55 else
                 ORB_WEIGHTS["rsi_5m"] * 0.53 if rsi >= 50 else 0)

        # ── 4. ADX at breakout bar position (Fix 5) ──────────────────
        adx_df  = ta.adx(df_to_brk["High"], df_to_brk["Low"],
                         df_to_brk["Close"], length=7)
        adx_val = 0.0
        s_adx   = 0
        if adx_df is not None and not adx_df.empty:
            ac = [c for c in adx_df.columns if c.startswith("ADX_")]
            if ac:
                adx_val = float(adx_df[ac[0]].iloc[-1])
                s_adx   = (ORB_WEIGHTS["adx_5m"]        if adx_val >= 25 else
                           ORB_WEIGHTS["adx_5m"] * 0.50 if adx_val >= 20 else 0)

        # ── 5. ORB range quality (tighter = cleaner breakout) ────────
        s_range = (ORB_WEIGHTS["orb_range_tight"]        if orb_range_pct <= 1.0 else
                   ORB_WEIGHTS["orb_range_tight"] * 0.70 if orb_range_pct <= 1.5 else
                   ORB_WEIGHTS["orb_range_tight"] * 0.40 if orb_range_pct <= 2.0 else 0)

        # ── 6. First bar of the day was bullish ───────────────────────
        first    = df.iloc[0]
        s_candle = (ORB_WEIGHTS["open_candle_bull"]
                    if float(first["Close"]) > float(first["Open"]) else 0)

        total = s_brk + s_vol + s_rsi + s_adx + s_range + s_candle + sector_bonus

        # ── ATR at breakout bar ───────────────────────────────────────
        atr_s   = ta.atr(df_to_brk["High"], df_to_brk["Low"],
                         df_to_brk["Close"], length=7)
        atr_val = (float(atr_s.iloc[-1])
                   if atr_s is not None and not atr_s.empty else orb_range)

        # ── Stop Loss and Target ──────────────────────────────────────
        stop_loss = round(max(orb_low, price - atr_val), 2)
        target    = round(orb_high + 1.5 * orb_range, 2)
        risk      = price - stop_loss

        # Fix 3: if price already exceeded target, show as "Target Hit" not drop it
        current_price = float(df.iloc[-1]["Close"])   # actual last price for display
        if risk <= 0:
            return None   # stop-loss is above entry — invalid setup
        reward   = target - price
        rr_ratio = round(reward / risk, 2) if reward > 0 else 0.0
        status   = "Target Hit ✅" if current_price >= target else "Active"

        clean_sym = (symbol.replace(".NS", "")
                           .replace("-", ".")
                           .replace("BRK.B", "BRK-B"))

        return {
            "Symbol":        clean_sym,
            "Status":        status,
            "Price":         round(price, 2),       # breakout bar close
            "Last_Price":    round(current_price, 2),  # current EOD price
            "ORB_High":      round(orb_high, 2),
            "ORB_Low":       round(orb_low, 2),
            "ORB_Range%":    round(orb_range_pct, 2),
            "Brk_Pct":       round(brk_pct, 2),
            "Vol_Ratio":     round(vol_ratio, 2),
            "RSI_5m":        round(rsi, 1),
            "ADX_5m":        round(adx_val, 1),
            "Sector_Align":  sector_bonus > 0,
            "Stop_Loss":     stop_loss,
            "Target":        target,
            "RR_Ratio":      rr_ratio,
            "ORB_Score":     round(total, 1),
        }
    except Exception as e:
        print(f"  {Fore.YELLOW}⚠  ORB skip {symbol}: {e}{Style.RESET_ALL}")
        return None


# ══════════════════════════════════════════════════════════
# RUN ORB SCREENER  — batch intraday download + parallel score
# ══════════════════════════════════════════════════════════

def run_orb_screener(symbols: list, label: str,
                     sector_perf: dict | None = None) -> pd.DataFrame:
    market   = "india" if any(s.endswith(".NS") for s in symbols) else "usa"
    orb_bars = ORB_BARS_INDIA if market == "india" else ORB_BARS_USA
    tf_label = "5-min" if market == "india" else "15-min"
    hdr(f"ORB Scan — {len(symbols)} {label} stocks ({tf_label} bars) …")

    sector_map = INDIA_SECTOR_MAP if market == "india" else USA_SECTOR_MAP
    if sector_perf is None:          # fallback: fetch if not passed in
        sector_perf = fetch_sector_perf(market)
    else:
        print(f"  ♻️  Reusing sector perf from BTST scan (skipping re-fetch).")

    def _sector_bonus(sym: str) -> float:
        sec     = sector_map.get(sym)
        sec_chg = sector_perf.get(sec, None) if sec else None
        if sec_chg is None or sec_chg <= 0:
            return 0.0
        return float(ORB_WEIGHTS["sector_bonus"])

    # One batch intraday fetch instead of N individual downloads
    intraday_cache = _batch_download_intraday(symbols, orb_bars=orb_bars)

    results = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = {
            pool.submit(score_orb_stock_from_df, sym, df, _sector_bonus(sym), orb_bars): sym
            for sym, df in intraday_cache.items()
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            print(f"  ⚙️  ORB [{done:>3}/{len(intraday_cache)}]", end="\r", flush=True)
            r = future.result()
            if r:
                results.append(r)

    print(" " * 60, end="\r")
    breakouts = len(results)
    print(f"  ✅  ORB: {breakouts} confirmed breakout(s) found out of {len(symbols)} scanned.")
    return pd.DataFrame(results)


def filter_and_rank_orb(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter ORB candidates: valid R:R, decent volume, momentum; rank by score.
    'Target Hit' stocks bypass the RR filter — they already worked, always show them.
    """
    if df.empty:
        return df

    # Separate "Target Hit" rows — always include these regardless of RR
    target_hit = df[df.get("Status", pd.Series(dtype=str)) == "Target Hit ✅"].copy() \
        if "Status" in df.columns else pd.DataFrame()

    # Active breakouts must pass quality filters
    active = df[df.get("Status", pd.Series(dtype=str)) != "Target Hit ✅"].copy() \
        if "Status" in df.columns else df.copy()

    active_filtered = active[
        (active["RSI_5m"]   >= 50) &
        (active["Vol_Ratio"] >= 1.2) &
        (active["RR_Ratio"]  >= 1.5)
    ].copy()

    # Combine: target hits first (best signal), then active breakouts by score
    combined = pd.concat([target_hit, active_filtered], ignore_index=True)
    combined.sort_values("ORB_Score", ascending=False, inplace=True)
    return combined.head(15)


# ══════════════════════════════════════════════════════════
# FILTER & RANK
# ══════════════════════════════════════════════════════════

def filter_and_rank(df: pd.DataFrame, min_score: float = 0.0) -> pd.DataFrame:
    """
    Filter and rank BTST candidates.

    Hard filters (stocks excluded if they fail):
      1. RSI ≥ 48
      2. Volume ratio ≥ 1.1 (relative volume)
      3. Absolute liquidity — avg daily volume above market threshold
      4. Day change > –0.5% and ≤ 8.0%
      5. Close above EMA20
      6. Optional minimum score threshold (--min-score arg)

    Soft signals (already baked into score via penalties):
      - Entry_Overextended → 70% score penalty (ranks low, still visible)
      - FinalHrFade        → 15% score penalty
      - Market direction   → continuous scalar multiplier

    Stocks are ranked by BTST_Score descending; top 15 returned.
    """
    if df.empty:
        return df

    # Symbols are already cleaned (no .NS suffix) — detect market by avg volume scale.
    # India stocks typically have avg daily volume in the hundreds of thousands;
    # USA large-caps run in the millions. Use the Avg_Vol median to decide.
    median_vol = df["Avg_Vol"].median() if "Avg_Vol" in df.columns else 0
    is_india   = median_vol < INDIA_USA_VOL_THRESHOLD
    liq_min    = LIQUIDITY_MIN_INDIA if is_india else LIQUIDITY_MIN_USA

    f = df[
        (df["RSI"] >= 48) &
        (df["Volume_Ratio"] >= 1.1) &
        (df["Avg_Vol"] >= liq_min) &        # ← liquidity: absolute volume gate
        (df["Change%"] > -0.5) &            # not too red
        (df["Change%"] <= 8.0) &            # very extreme moves still excluded
        (df["Close"] > df["EMA20"])
        # Entry_Overextended is NOT a hard filter — it already applies a 70% score
        # penalty inside score_stock_from_df(), so overextended stocks rank low
        # naturally. Keeping it as a soft signal avoids over-filtering on weak days.
    ].copy()

    # FIX: enforce minimum R:R — no trade where reward < 1.5× risk
    # A poor R:R means even a 60% win rate won't be profitable long-term.
    if "RR_Ratio" in f.columns:
        rr_dropped = len(f) - len(f[f["RR_Ratio"] >= MIN_RR_RATIO])
        if rr_dropped > 0:
            print(f"  ⚖️   Dropped {rr_dropped} candidates with R:R < {MIN_RR_RATIO}x.")
        f = f[f["RR_Ratio"] >= MIN_RR_RATIO]

    # FIX: warn about (but keep visible) any stocks with earnings tomorrow
    if "Has_Earnings" in f.columns:
        earnings_flags = f[f["Has_Earnings"] == True]
        if not earnings_flags.empty:
            syms = ", ".join(earnings_flags["Symbol"].tolist())
            print(f"  ⚠️   EARNINGS RISK: {syms} — reporting tomorrow. "
                  f"Score already halved. Trade with extreme caution or skip.")

    if min_score > 0:
        f = f[f["BTST_Score"] >= min_score]    # ← score threshold filter

    f.sort_values("BTST_Score", ascending=False, inplace=True)

    # Print how many were filtered out for transparency
    dropped = len(df) - len(f)
    if dropped > 0:
        print(f"  🔍  Filtered out {dropped} candidates "
              f"(liquidity/overextension/score checks).")

    return f.head(15)



# ══════════════════════════════════════════════════════════
# CONSOLE PRINT REPORT
# ══════════════════════════════════════════════════════════

def print_report(df: pd.DataFrame, label: str, market_ok: bool, idx_chg: float):
    tz   = IST if "INDIA" in label.upper() else EST
    now  = datetime.now(tz=tz)
    tzn  = "IST" if "INDIA" in label.upper() else "EST"
    hdr(f"{label} BTST Report  |  {now.strftime('%d-%b-%Y %I:%M %p')} {tzn}")

    col = Fore.GREEN if market_ok else Fore.RED
    print(f"  Market: {col}{'BULLISH' if market_ok else 'CAUTION'} ({idx_chg:+.2f}%){Style.RESET_ALL}")

    if df.empty:
        print(f"\n  {Fore.YELLOW}No strong candidates found.{Style.RESET_ALL}")
        return

    cols = ["Symbol", "Conviction", "Close", "Change%", "Volume_Ratio", "RSI", "ADX",
            "Range_Pos%", "Near_52W_High", "Gap_Up", "Candle", "MACD_Crossover",
            "RS_Beat", "Weekly_Align", "Sector_Align", "Has_Earnings",
            "Stop_Loss", "SL%", "Target", "Target%", "RR_Ratio",
            "Shares", "Position_Value", "BTST_Score"]
    available = [c for c in cols if c in df.columns]
    disp = df[available].reset_index(drop=True)
    disp.index += 1
    print(f"\n{Fore.CYAN}  TOP BTST CANDIDATES{Style.RESET_ALL}\n")
    print(tabulate(disp, headers="keys", tablefmt="fancy_grid", floatfmt=".2f", showindex=True))

    # Next-day exit guidance
    is_india = "INDIA" in label.upper()
    exit_time = "9:20 AM IST" if is_india else "9:35 AM EST"
    print(f"\n  {Fore.YELLOW}⚡  NEXT-DAY EXIT RULES:{Style.RESET_ALL}")
    print(f"     • Target:   +{df['Target%'].mean():.1f}% avg  (see Target% column per stock)")
    print(f"     • Stop-loss: –{df['SL%'].mean():.1f}% avg  (see SL% column per stock)")
    print(f"     • Gap-down exit: If opens below entry, EXIT by {exit_time} — don't hold.")
    print(f"     • First 15-min low breaks SL → exit immediately, don't wait for EOD.")



# ══════════════════════════════════════════════════════════
# SAVE CSV
# ══════════════════════════════════════════════════════════

def save_csv(top_df: pd.DataFrame, full_df: pd.DataFrame, prefix: str, date_str: str):
    top_df.to_csv(f"btst_{prefix}_{date_str}.csv", index=False)
    full_df.sort_values("BTST_Score", ascending=False).to_csv(
        f"btst_{prefix}_full_{date_str}.csv", index=False)
    print(f"  💾  {prefix.upper()} CSVs saved.")


def save_meta(prefix: str, date_str: str, ok: bool, chg: float, vix: float):
    """Save market health metadata so --html-only can reconstruct the report."""
    meta = {"ok": ok, "chg": round(chg, 4), "vix": round(vix, 4)}
    with open(f"btst_{prefix}_meta_{date_str}.json", "w") as f:
        json.dump(meta, f)
    print(f"  📝  {prefix.upper()} metadata saved.")


def load_meta(prefix: str, date_str: str) -> tuple[bool, float, float]:
    """Load saved market health metadata. Returns (ok, chg, vix) or safe defaults."""
    try:
        with open(f"btst_{prefix}_meta_{date_str}.json") as f:
            m = json.load(f)
        return bool(m.get("ok", True)), float(m.get("chg", 0.0)), float(m.get("vix", 0.0))
    except Exception:
        return True, 0.0, 0.0   # fail-open: missing/corrupt meta file → assume safe, no chg, no vix


# ══════════════════════════════════════════════════════════
# HTML TABLE ROWS BUILDER
# ══════════════════════════════════════════════════════════

def _rows(df: pd.DataFrame, currency: str = "₹",
          prev_scores: dict | None = None) -> str:
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rows   = ""
    prev_scores = prev_scores or {}

    for rank, (_, row) in enumerate(df.iterrows(), 1):
        medal  = medals.get(rank, f"#{rank}")
        chg_c  = "#00ff88" if row["Change%"] >= 0 else "#ff6b6b"
        chg_a  = "▲" if row["Change%"] >= 0 else "▼"

        vol_cls = "bg" if row["Volume_Ratio"] >= 1.5 else "by" if row["Volume_Ratio"] >= 1.2 else "br"
        rsi_cls = "bg" if 55 <= row["RSI"] <= 75 else "by" if row["RSI"] >= 50 else "br"

        sc  = row["BTST_Score"]
        bc  = "#00e676" if sc >= 70 else "#ffca28" if sc >= 50 else "#ff5252"
        pct = sc   # score is already normalised to 0-100

        # ── 52W high badge ───────────────────────────────────────
        near52  = row.get("Near_52W_High", False)
        badge52 = '<span class="badge bg">🚀 52W</span>' if near52 else '<span class="badge br">—</span>'

        # ── Sector alignment — colour-coded TD background ────────
        sec_align = row.get("Sector_Align", False)
        sec_bg    = "rgba(0,255,136,0.12)" if sec_align else "rgba(255,107,107,0.10)"
        sec_txt   = "#00ff88"              if sec_align else "#ff6b6b"
        sec_bdr   = "rgba(0,255,136,0.35)" if sec_align else "rgba(255,107,107,0.30)"
        sec_label = "▲ Bull"               if sec_align else "▼ Bear"
        badge_sec = (f'<td style="text-align:center">'
                     f'<span style="display:inline-block;background:{sec_bg};color:{sec_txt};border:1px solid {sec_bdr};border-radius:4px;font-family:var(--mono);font-size:.65rem;font-weight:700;padding:2px 7px;letter-spacing:.5px;white-space:nowrap">'
                     f'{sec_label}</span></td>')

        # ── Gap-up badge ─────────────────────────────────────────
        gap_up  = row.get("Gap_Up", False)
        gap_pct_val = row.get("Gap_Pct", 0.0)
        if gap_up and gap_pct_val >= 1.0:
            badge_gap = f'<span class="badge bg">⬆ {gap_pct_val:+.1f}%</span>'
        elif gap_up:
            badge_gap = f'<span class="badge by">⬆ {gap_pct_val:+.1f}%</span>'
        else:
            badge_gap = '<span class="badge br" style="color:var(--muted)">—</span>'

        # ── Candlestick pattern badge ────────────────────────────
        candle = str(row.get("Candle", "")) if row.get("Candle") else ""
        if candle == "Morning Star":
            badge_candle = '<span class="badge bg">⭐ M-Star</span>'
        elif candle == "Engulfing":
            badge_candle = '<span class="badge bg">🕯 Engulf</span>'
        elif candle == "Hammer":
            badge_candle = '<span class="badge by">🔨 Hammer</span>'
        else:
            badge_candle = '<span class="badge br" style="color:var(--muted)">—</span>'

        # ── MACD crossover badge ─────────────────────────────────
        macd_cross  = row.get("MACD_Crossover", False)
        badge_macd  = ('<span class="badge bg">⚡ X-over</span>' if macd_cross
                       else '<span class="badge br" style="color:var(--muted)">—</span>')

        # ── Earnings warning badge ───────────────────────────────
        has_earnings = row.get("Has_Earnings", False)
        badge_earn   = ('<span class="badge br" style="font-weight:800">⚠ EARNS</span>'
                        if has_earnings else "")

        # ── Position sizing ──────────────────────────────────────
        shares     = row.get("Shares", 0)
        pos_val    = row.get("Position_Value", 0)
        pos_str    = (f'{shares:,} sh &nbsp; {currency}{pos_val:,.0f}'
                      if shares > 0 else "—")

        # ── Relative Strength badge ──────────────────────────────
        rs_beat = row.get("RS_Beat", False)
        badge_rs = ('<span class="badge bg">📈 RS+</span>' if rs_beat
                    else '<span class="badge br" style="color:var(--muted)">—</span>')

        # ── Weekly MTF badge ─────────────────────────────────────
        w_align   = row.get("Weekly_Align", False)
        badge_mtf = ('<span class="badge bg">✅ W</span>' if w_align
                     else '<span class="badge br" style="color:var(--muted)">—</span>')

        # ── Score trend arrow ────────────────────────────────────
        sym = str(row.get("Symbol", ""))
        prev_sc = prev_scores.get(sym)
        if prev_sc is not None:
            diff = sc - prev_sc
            if diff >= 2:
                arrow = f'<span style="color:#00ff88;font-size:.8rem"> ▲{diff:+.0f}</span>'
            elif diff <= -2:
                arrow = f'<span style="color:#ff6b6b;font-size:.8rem"> ▼{diff:+.0f}</span>'
            else:
                arrow = '<span style="color:var(--muted);font-size:.8rem"> ●</span>'
        else:
            arrow = ""

        # ── Stop-loss / Target / R:R ─────────────────────────────
        sl      = row.get("Stop_Loss", 0)
        tgt     = row.get("Target", 0)
        rr      = row.get("RR_Ratio", 0)
        tgt_pct = row.get("Target%", 0.0)
        sl_pct  = row.get("SL%", 0.0)
        rr_col  = "#00ff88" if rr >= 2 else "#ffd740" if rr >= 1.5 else "#ff6b6b"

        # ── Conviction badge (with overextended warning) ─────────
        conviction   = str(row.get("Conviction", ""))
        overextended = bool(row.get("Entry_Overextended", False))
        conv_map = {
            "HIGH":     ('<span class="badge bg" style="font-weight:800">🔥 HIGH</span>'),
            "GOOD":     ('<span class="badge bg">✅ GOOD</span>'),
            "MODERATE": ('<span class="badge by">⚡ MOD</span>'),
            "WEAK":     ('<span class="badge br">— WEAK</span>'),
        }
        badge_conv = conv_map.get(conviction, "")
        if overextended:
            badge_conv += ' <span class="badge br" title="Price overextended vs EMA — lower quality entry">⚠ OX</span>'

        rows += f"""
        <tr>
          <td class="rnk">{medal}</td>
          <td class="sym">{sym}{badge_earn}</td>
          <td>{badge_conv}</td>
          <td class="num">{currency}{row['Close']:,.2f}</td>
          <td><span style="color:{chg_c};font-weight:700;font-family:var(--mono);font-size:.78rem">{chg_a} {abs(row['Change%']):.2f}%</span></td>
          <td><span class="badge {vol_cls}">{row['Volume_Ratio']:.2f}x</span></td>
          <td><span class="badge {rsi_cls}">{row['RSI']:.1f}</span></td>
          <td class="num">{row['ADX']:.1f}</td>
          <td class="num">{row['Range_Pos%']:.1f}%</td>
          <td>{badge52}</td>
          <td>{badge_gap}</td>
          <td>{badge_candle}</td>
          <td>{badge_macd}</td>
          <td>{badge_rs}</td>
          <td>{badge_mtf}</td>
          {badge_sec}
          <td class="num" style="line-height:1.55">
            <span style="color:#ff6b6b" title="Stop-loss: –{sl_pct:.1f}% from entry">{currency}{sl:,.2f} <span style="font-size:.72rem;opacity:.8">–{sl_pct:.1f}%</span></span><br>
            <span style="color:#00ff88" title="Target: +{tgt_pct:.1f}% from entry">{currency}{tgt:,.2f} <span style="font-size:.72rem;opacity:.8">+{tgt_pct:.1f}%</span></span>
          </td>
          <td class="num" style="color:{rr_col};font-weight:700">{rr:.1f}x</td>
          <td class="num" style="font-family:var(--mono);font-size:.78rem;color:#c8d4e0">{pos_str}</td>
          <td>
            <div class="bw">
              <div class="bt"><div class="b" style="width:{pct:.0f}%;background:{bc}"></div></div>
              <span class="bl">{sc:.1f}{arrow}</span>
            </div>
          </td>
        </tr>"""
    return rows


def _rows_orb(df: pd.DataFrame, currency: str = "₹") -> str:
    """Build HTML table rows for ORB candidates."""
    medals = {1: "🥇", 2: "🥈", 3: "🥉"}
    rows   = ""
    for rank, (_, row) in enumerate(df.iterrows(), 1):
        medal   = medals.get(rank, f"#{rank}")
        sc      = row["ORB_Score"]
        bc      = "#00ff88" if sc >= 60 else "#ffd740" if sc >= 40 else "#ff6b6b"
        pct     = min(sc / MAX_ORB_SCORE * 100, 100)

        vol_cls = "bg" if row["Vol_Ratio"] >= 2.0 else "by" if row["Vol_Ratio"] >= 1.5 else "br"
        rsi_cls = "bg" if row["RSI_5m"] >= 55    else "by" if row["RSI_5m"] >= 50    else "br"

        sec_align = row.get("Sector_Align", False)
        sec_bg    = "rgba(0,255,136,0.12)" if sec_align else "rgba(255,107,107,0.10)"
        sec_txt   = "#00ff88" if sec_align else "#ff6b6b"
        sec_label = "✅ Green" if sec_align else "❌ Red"

        rr     = row.get("RR_Ratio", 0)
        rr_col = "#00ff88" if rr >= 2 else "#ffd740" if rr >= 1.5 else "#ff6b6b"
        sl     = row.get("Stop_Loss", 0)
        tgt    = row.get("Target", 0)

        brk_pct = row.get("Brk_Pct", 0)
        brk_col = "#00ff88" if brk_pct >= 1.0 else "#ffd740"

        rows += f"""
        <tr>
          <td class="rnk">{medal}</td>
          <td class="sym">{row['Symbol']}</td>
          <td class="num">{currency}{row['Price']:,.2f}</td>
          <td class="num" style="color:var(--green);font-weight:700">{currency}{row['ORB_High']:,.2f}</td>
          <td class="num" style="color:var(--red)">{currency}{row['ORB_Low']:,.2f}</td>
          <td class="num">{row['ORB_Range%']:.2f}%</td>
          <td><span style="color:{brk_col};font-weight:700">+{brk_pct:.2f}%</span></td>
          <td><span class="badge {vol_cls}">{row['Vol_Ratio']:.2f}x</span></td>
          <td><span class="badge {rsi_cls}">{row['RSI_5m']:.1f}</span></td>
          <td class="num">{row['ADX_5m']:.1f}</td>
          <td style="background:{sec_bg};text-align:center">
            <span style="color:{sec_txt};font-weight:700;font-size:.72rem">{sec_label}</span>
          </td>
          <td class="num" style="color:#ff5252">{currency}{sl:,.2f}</td>
          <td class="num" style="color:#00e676">{currency}{tgt:,.2f}</td>
          <td class="num" style="color:{rr_col};font-weight:700">{rr:.1f}x</td>
          <td>
            <div class="bw">
              <div class="bt"><div class="b" style="width:{pct:.0f}%;background:{bc}"></div></div>
              <span class="bl">{sc:.1f}</span>
            </div>
          </td>
        </tr>"""
    return rows


def _summary_cards(top_df: pd.DataFrame, total_scanned: int, tab_id: str) -> str:
    if top_df.empty or "BTST_Score" not in top_df.columns:
        strong = moderate = weak = 0
    else:
        strong   = len(top_df[top_df["BTST_Score"] >= 70])
        moderate = len(top_df[(top_df["BTST_Score"] >= 50) & (top_df["BTST_Score"] < 70)])
        weak     = len(top_df[top_df["BTST_Score"] < 50])
    return f"""
    <div class="cards" id="cards-{tab_id}">
      <div class="card cg"><span class="card-ico">🟢</span><div class="card-val" style="color:var(--green)">{strong}</div><div class="card-lbl">Strong Picks (≥70)</div></div>
      <div class="card cy"><span class="card-ico">🟡</span><div class="card-val" style="color:var(--yellow)">{moderate}</div><div class="card-lbl">Moderate (50–69)</div></div>
      <div class="card cr"><span class="card-ico">🔴</span><div class="card-val" style="color:var(--red)">{weak}</div><div class="card-lbl">Weak (&lt;50)</div></div>
      <div class="card cb"><span class="card-ico">📋</span><div class="card-val" style="color:var(--blue)">{len(top_df)}</div><div class="card-lbl">Total Candidates</div></div>
      <div class="card cm"><span class="card-ico">🔍</span><div class="card-val" style="color:var(--text)">{total_scanned}</div><div class="card-lbl">Stocks Scanned</div></div>
    </div>"""


# ══════════════════════════════════════════════════════════
# GENERATE COMBINED HTML REPORT
# ══════════════════════════════════════════════════════════

def generate_html_report(
    india_top, india_full, india_ok, india_chg, india_vix,
    usa_top,   usa_full,   usa_ok,   usa_chg,   usa_vix,
    date_str: str,
    orb_india_df: pd.DataFrame | None = None,
    orb_usa_df:   pd.DataFrame | None = None,
):
    now_ist  = datetime.now(tz=IST)
    now_est  = datetime.now(tz=EST)
    time_ist = now_ist.strftime("%d %b %Y, %I:%M %p IST")
    time_est = now_est.strftime("%d %b %Y, %I:%M %p EST")
    html_file = f"btst_report_{date_str}.html"

    india_rows = _rows(india_top, "₹", _load_prev_scores("india", date_str)) if not india_top.empty else "<tr><td colspan='17' style='text-align:center;color:var(--muted);padding:30px'>No candidates found today</td></tr>"
    usa_rows   = _rows(usa_top,   "$", _load_prev_scores("usa",   date_str)) if not usa_top.empty   else "<tr><td colspan='17' style='text-align:center;color:var(--muted);padding:30px'>No candidates found today</td></tr>"

    # ── ORB rows ──────────────────────────────────────────────
    _orb_empty = "<tr><td colspan='15' style='text-align:center;color:var(--muted);padding:30px'>No ORB breakouts detected — market may not be open yet, or no confirmed breakouts this session.</td></tr>"
    orb_india  = orb_india_df if orb_india_df is not None else pd.DataFrame()
    orb_usa    = orb_usa_df   if orb_usa_df   is not None else pd.DataFrame()
    orb_india_rows = _rows_orb(orb_india, "₹") if not orb_india.empty else _orb_empty
    orb_usa_rows   = _rows_orb(orb_usa,   "$") if not orb_usa.empty   else _orb_empty
    orb_india_count = len(orb_india)
    orb_usa_count   = len(orb_usa)

    india_cards = _summary_cards(india_top, len(india_full), "india")
    usa_cards   = _summary_cards(usa_top,   len(usa_full),   "usa")

    india_m_col = "#00e676" if india_ok else "#ff5252"
    usa_m_col   = "#00e676" if usa_ok   else "#ff5252"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BTST Screener — {date_str}</title>
<link href="https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=Syne:wght@400;600;800&display=swap" rel="stylesheet">
<style>
  :root {{
    --bg:     #080c10; --surf:  #0d1117; --surf2: #161b22;
    --border: #30363d; --green: #00ff88; --yellow:#ffd740;
    --red:    #ff6b6b; --blue:  #60d4ff; --text:  #ffffff;
    --muted:  #a0aab4; --r:     12px;
    --mono: 'Space Mono',monospace; --sans: 'Syne',sans-serif;
  }}
  *,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
  html{{scroll-behavior:smooth}}
  body{{background:var(--bg);color:var(--text);font-family:var(--sans);padding-bottom:60px;-webkit-font-smoothing:antialiased}}

  /* HEADER — compact single bar */
  .header{{background:linear-gradient(90deg,#0d1117 0%,#0a1628 50%,#0d1117 100%);border-bottom:1px solid var(--border);padding:10px clamp(14px,3vw,32px);display:flex;align-items:center;gap:14px;flex-wrap:nowrap;overflow:hidden}}
  .logo-inline{{display:flex;flex-direction:column;justify-content:center;flex-shrink:0}}
  .logo-inline h1{{font-size:1.1rem;font-weight:800;letter-spacing:-.5px;line-height:1.15;white-space:nowrap}}
  .logo-inline h1 span{{color:var(--green)}}
  .logo-inline p{{font-family:var(--mono);font-size:.6rem;color:#7888a0;letter-spacing:1.2px;text-transform:uppercase;margin-top:1px}}
  .hdr-divider{{width:1px;height:28px;background:var(--border);flex-shrink:0}}
  .pills-inline{{display:flex;gap:6px;flex:1;flex-wrap:nowrap;overflow:hidden;align-items:center;min-width:0}}
  .pill{{display:flex;align-items:center;gap:5px;background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:999px;padding:3px 10px;font-family:var(--mono);font-size:.7rem;color:#c8d4e0;white-space:nowrap}}
  .dot{{width:6px;height:6px;border-radius:50%;flex-shrink:0}}
  .dot.live{{animation:blink 2s infinite}}
  @keyframes blink{{0%,100%{{opacity:1}}50%{{opacity:.25}}}}
  .hdr-ts{{display:flex;align-items:center;gap:8px;flex-shrink:0;margin-left:auto;font-family:var(--mono)}}
  .hdr-clock{{font-size:.88rem;font-weight:700;color:var(--blue);white-space:nowrap}}
  .tz-tag{{display:inline-block;background:rgba(96,212,255,.15);color:var(--blue);border:1px solid rgba(96,212,255,.4);border-radius:4px;font-size:.62rem;padding:1px 5px;letter-spacing:1px;font-weight:700;vertical-align:middle}}
  .hdr-date{{font-size:.68rem;color:#7888a0;white-space:nowrap}}

  /* MARKET PILLS — per-tab, shown/hidden via JS */
  .market-pills{{display:none;gap:6px;align-items:center;flex:1;min-width:0;overflow:hidden}}
  .market-pills.active{{display:flex}}

  /* TABS BAR */
  .tabs-bar{{background:var(--surf);border-bottom:1px solid var(--border);padding:0 clamp(14px,3vw,32px);display:flex;align-items:center;gap:2px}}
  .tab-btn{{display:flex;align-items:center;gap:6px;background:transparent;border:none;border-bottom:2px solid transparent;padding:9px 16px;cursor:pointer;font-family:var(--mono);font-size:clamp(.72rem,1.3vw,.82rem);font-weight:700;color:var(--muted);transition:color .2s,border-color .2s;white-space:nowrap;margin-bottom:-1px}}
  .tab-btn .flag{{font-size:.95rem}}
  .tab-btn.active.india-btn{{color:var(--green);border-bottom-color:var(--green)}}
  .tab-btn.active.usa-btn{{color:var(--blue);border-bottom-color:var(--blue)}}
  .tab-btn.active.orb-btn{{color:#f9a825;border-bottom-color:#f9a825}}
  .tab-btn:hover:not(.active){{color:var(--text)}}
  .tabs-bar-spacer{{flex:1}}
  .tabs-bar-label{{font-family:var(--mono);font-size:.62rem;color:#7888a0;letter-spacing:.5px;white-space:nowrap}}

  /* CONTENT */
  .content{{padding:clamp(20px,4vw,36px) clamp(14px,5vw,48px)}}

  /* TAB PANELS */
  .tab-panel{{display:none;animation:fadeUp .35s ease}}
  .tab-panel.active{{display:block}}

  /* CARDS */
  .cards{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin-bottom:20px}}
  @media(max-width:860px){{.cards{{grid-template-columns:repeat(3,1fr)}}}}
  @media(max-width:520px){{.cards{{grid-template-columns:repeat(2,1fr)}}}}
  .card{{background:var(--surf);border:1px solid var(--border);border-radius:var(--r);padding:10px 14px 9px;position:relative;overflow:hidden;transition:transform .2s,box-shadow .2s;animation:fadeUp .4s ease both}}
  .card:hover{{transform:translateY(-2px);box-shadow:0 6px 18px rgba(0,0,0,.4)}}
  .card::after{{content:'';position:absolute;bottom:0;left:0;right:0;height:2px;border-radius:0 0 var(--r) var(--r)}}
  .card.cg::after{{background:var(--green)}} .card.cy::after{{background:var(--yellow)}}
  .card.cr::after{{background:var(--red)}}   .card.cb::after{{background:var(--blue)}}
  .card.cm::after{{background:var(--muted)}}
  .card-ico{{font-size:.9rem;margin-bottom:4px;display:block}}
  .card-val{{font-family:var(--mono);line-height:1;font-size:clamp(1.1rem,2.2vw,1.5rem);font-weight:700}}
  .card-lbl{{font-size:clamp(.6rem,1.1vw,.7rem);color:var(--muted);text-transform:uppercase;letter-spacing:.8px;margin-top:4px;line-height:1.3}}
  .card:nth-child(1){{animation-delay:.04s}} .card:nth-child(2){{animation-delay:.08s}}
  .card:nth-child(3){{animation-delay:.12s}} .card:nth-child(4){{animation-delay:.16s}}
  .card:nth-child(5){{animation-delay:.20s}}

  /* SECTION HEADER */
  .sh{{display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap}}
  .sh-title{{font-size:clamp(1rem,2.2vw,1.2rem);font-weight:700;white-space:nowrap;color:var(--text)}}
  .sh-line{{flex:1;height:1px;background:var(--border);min-width:16px}}
  .sh-sub{{font-family:var(--mono);font-size:clamp(.72rem,1.4vw,.82rem);color:#b8c8d8;white-space:nowrap}}

  /* TABLE */
  .scroll-hint{{display:none;font-family:var(--mono);font-size:.62rem;color:var(--muted);text-align:right;margin-bottom:6px}}
  @media(max-width:680px){{.scroll-hint{{display:block}}}}
  .tw{{overflow-x:auto;border-radius:var(--r);border:1px solid var(--border);-webkit-overflow-scrolling:touch}}
  table{{width:100%;border-collapse:collapse;font-size:clamp(.85rem,1.6vw,.98rem)}}
  thead tr{{background:var(--surf2)}}
  th{{font-family:var(--mono);font-size:clamp(.68rem,1.2vw,.76rem);text-transform:uppercase;letter-spacing:.9px;color:#c8d0db;padding:clamp(12px,1.6vw,16px) clamp(10px,1.5vw,16px);text-align:left;white-space:nowrap;border-bottom:1px solid var(--border)}}
  tbody tr{{background:var(--surf);border-bottom:1px solid var(--border);transition:background .15s;animation:fadeUp .3s ease both}}
  tbody tr:hover{{background:var(--surf2)}}
  tbody tr:last-child{{border-bottom:none}}
  td{{padding:clamp(11px,1.6vw,15px) clamp(10px,1.5vw,16px);white-space:nowrap;color:var(--text)}}
  td.rnk{{font-size:1.15rem;text-align:center;width:46px}}
  td.sym{{font-family:var(--mono);font-weight:700;font-size:clamp(.88rem,1.6vw,1rem);color:var(--blue);letter-spacing:.5px}}
  td.num{{font-family:var(--mono);font-size:clamp(.82rem,1.5vw,.94rem);color:#dde4ed}}
  tbody tr:nth-child(1){{animation-delay:.05s}} tbody tr:nth-child(2){{animation-delay:.09s}}
  tbody tr:nth-child(3){{animation-delay:.13s}} tbody tr:nth-child(4){{animation-delay:.17s}}
  tbody tr:nth-child(5){{animation-delay:.21s}} tbody tr:nth-child(6){{animation-delay:.25s}}
  tbody tr:nth-child(7){{animation-delay:.29s}} tbody tr:nth-child(8){{animation-delay:.33s}}
  tbody tr:nth-child(9){{animation-delay:.37s}} tbody tr:nth-child(10){{animation-delay:.41s}}

  /* BADGES */
  .badge{{display:inline-block;padding:4px 10px;border-radius:999px;font-family:var(--mono);font-size:clamp(.7rem,1.2vw,.8rem);font-weight:700}}
  .bg{{background:rgba(0,255,136,.15);color:#00ff88;border:1px solid rgba(0,255,136,.4)}}
  .by{{background:rgba(255,215,64,.15);color:#ffd740;border:1px solid rgba(255,215,64,.4)}}
  .br{{background:rgba(255,107,107,.15);color:#ff6b6b;border:1px solid rgba(255,107,107,.4)}}

  /* SCORE BAR */
  .bw{{display:flex;align-items:center;gap:9px;min-width:115px}}
  .bt{{flex:1;height:7px;background:var(--border);border-radius:99px;overflow:hidden}}
  .b{{height:100%;border-radius:99px}}
  .bl{{font-family:var(--mono);font-size:clamp(.78rem,1.4vw,.9rem);font-weight:700;min-width:34px;text-align:right;color:var(--text)}}

  /* LEGEND */
  .legend{{display:flex;flex-wrap:wrap;gap:10px 18px;margin-top:18px;padding:clamp(12px,2vw,18px) clamp(12px,2vw,20px);background:var(--surf);border:1px solid var(--border);border-radius:var(--r)}}
  .li{{display:flex;align-items:center;gap:7px;font-family:var(--mono);font-size:clamp(.78rem,1.4vw,.9rem);color:#d8e4f0;font-weight:500}}
  .ld{{width:10px;height:10px;border-radius:2px;flex-shrink:0}}

  /* PARAMS GRID */
  .pg{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-top:28px}}
  @media(max-width:780px){{.pg{{grid-template-columns:repeat(2,1fr)}}}}
  @media(max-width:460px){{.pg{{grid-template-columns:1fr}}}}
  .pc{{background:var(--surf);border:1px solid var(--border);border-radius:10px;padding:clamp(11px,2vw,16px)}}
  .pn{{font-family:var(--mono);font-size:clamp(.7rem,1.3vw,.82rem);color:var(--blue);text-transform:uppercase;letter-spacing:.9px;margin-bottom:5px;display:flex;justify-content:space-between;align-items:center}}
  .pw{{font-family:var(--mono);font-size:clamp(.66rem,1.2vw,.76rem);color:var(--green);font-weight:700}}
  .pd{{font-size:clamp(.78rem,1.4vw,.88rem);color:#b0bcc8;line-height:1.6}}

  /* DISCLAIMER */
  .disc{{margin-top:26px;padding:clamp(12px,2vw,16px) clamp(13px,2vw,20px);background:rgba(255,215,64,.05);border:1px solid rgba(255,215,64,.22);border-radius:8px;font-size:clamp(.76rem,1.4vw,.84rem);color:#b0bcc8;line-height:1.75}}
  .disc strong{{color:var(--yellow)}}

  /* FOOTER */
  .footer{{text-align:center;padding:clamp(18px,3vw,28px) clamp(14px,5vw,48px) 0;font-family:var(--mono);font-size:clamp(.66rem,1.2vw,.76rem);color:var(--muted);border-top:1px solid var(--border);margin-top:40px;line-height:2.2}}

  @keyframes fadeUp{{from{{opacity:0;transform:translateY(14px)}}to{{opacity:1;transform:translateY(0)}}}}
</style>
</head>
<body>

<!-- HEADER — compact single bar -->
<div class="header">
  <div class="logo-inline">
    <h1>BTST <span>Screener</span></h1>
    <p>India · USA · Buy Today Sell Tomorrow</p>
  </div>
  <div class="hdr-divider"></div>

  <!-- India status pills (shown by default) -->
  <div class="market-pills active" id="pills-india">
    <div class="pill"><div class="dot live" style="background:{india_m_col}"></div>Market:&nbsp;<strong style="color:{india_m_col}">{'BULLISH' if india_ok else 'CAUTION'}</strong></div>
    <div class="pill"><div class="dot" style="background:#40c4ff"></div>Nifty 50:&nbsp;<strong style="color:#40c4ff">{'+' if india_chg>=0 else ''}{india_chg:.2f}%</strong></div>
    <div class="pill"><div class="dot" style="background:{'#00e676' if india_vix<20 else '#ff5252'}"></div>India VIX:&nbsp;<strong style="color:{'#00e676' if india_vix<20 else '#ff5252'}">{india_vix:.2f}</strong></div>
    <div class="pill"><div class="dot" style="background:#7d8590"></div>Scanned:&nbsp;<strong style="color:#e6edf3">{len(india_full)}</strong></div>
    <div class="pill"><div class="dot" style="background:#7d8590"></div>Picks:&nbsp;<strong style="color:#e6edf3">{len(india_top)}</strong></div>
  </div>

  <!-- USA status pills -->
  <div class="market-pills" id="pills-usa">
    <div class="pill"><div class="dot live" style="background:{usa_m_col}"></div>Market:&nbsp;<strong style="color:{usa_m_col}">{'BULLISH' if usa_ok else 'CAUTION'}</strong></div>
    <div class="pill"><div class="dot" style="background:#40c4ff"></div>S&amp;P 500:&nbsp;<strong style="color:#40c4ff">{'+' if usa_chg>=0 else ''}{usa_chg:.2f}%</strong></div>
    <div class="pill"><div class="dot" style="background:{'#00e676' if usa_vix<20 else '#ff5252'}"></div>CBOE VIX:&nbsp;<strong style="color:{'#00e676' if usa_vix<20 else '#ff5252'}">{usa_vix:.2f}</strong></div>
    <div class="pill"><div class="dot" style="background:#7d8590"></div>Scanned:&nbsp;<strong style="color:#e6edf3">{len(usa_full)}</strong></div>
    <div class="pill"><div class="dot" style="background:#7d8590"></div>Picks:&nbsp;<strong style="color:#e6edf3">{len(usa_top)}</strong></div>
  </div>

  <!-- ORB status pills -->
  <div class="market-pills" id="pills-orb">
    <div class="pill"><div class="dot live" style="background:#f9a825"></div>Mode:&nbsp;<strong style="color:#f9a825">INTRADAY</strong></div>
    <div class="pill"><div class="dot" style="background:#f9a825"></div>Strategy:&nbsp;<strong style="color:#e6edf3">ORB 5-min</strong></div>
    <div class="pill"><div class="dot" style="background:#7d8590"></div>🇮🇳 Breakouts:&nbsp;<strong style="color:#e6edf3">{orb_india_count}</strong></div>
    <div class="pill"><div class="dot" style="background:#7d8590"></div>🇺🇸 Breakouts:&nbsp;<strong style="color:#e6edf3">{orb_usa_count}</strong></div>
  </div>

  <div class="hdr-ts">
    <span class="hdr-clock"><span id="live-clock">{now_ist.strftime('%I:%M:%S %p')}</span> <span class="tz-tag">IST</span></span>
    <span class="hdr-date">{now_ist.strftime('%d %b %Y')}</span>
  </div>
</div>

<!-- TABS BAR -->
<div class="tabs-bar">
  <button class="tab-btn india-btn active" onclick="switchTab('india')">
    <span class="flag">🇮🇳</span> India <span style="font-size:.6rem;opacity:.55">NIFTY 100</span>
  </button>
  <button class="tab-btn usa-btn" onclick="switchTab('usa')">
    <span class="flag">🇺🇸</span> USA <span style="font-size:.6rem;opacity:.55">S&amp;P 500</span>
  </button>
  <button class="tab-btn orb-btn" onclick="switchTab('orb')">
    <span class="flag">📊</span> ORB <span style="font-size:.6rem;opacity:.55">5-MIN</span>
  </button>
  <div class="tabs-bar-spacer"></div>
  <span class="tabs-bar-label">Last generated · Auto-report</span>
</div>

<!-- CONTENT -->
<div class="content">

  <!-- INDIA PANEL -->
  <div class="tab-panel active" id="panel-india">
    {india_cards}
    <div class="sh">
      <div class="sh-title">🎯 Top BTST Candidates — India</div>
      <div class="sh-line"></div>
      <div class="sh-sub">Entry window: 3:00–3:20 PM IST</div>
    </div>
    <div style="margin:10px 0 14px;padding:12px 16px;background:rgba(255,193,7,0.06);border:1px solid rgba(255,193,7,0.22);border-radius:8px;display:flex;flex-wrap:wrap;gap:10px 24px;align-items:flex-start">
      <div style="font-family:var(--mono);font-size:.72rem;font-weight:700;color:#ffd740;white-space:nowrap;padding-top:1px">⚡ EXIT RULES · INDIA</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px 20px;flex:1;font-family:var(--mono);font-size:.72rem">
        <span style="color:#a0aab4">🎯 Target: <strong style="color:#00ff88">see Target% col</strong></span>
        <span style="color:#a0aab4">🛑 SL: <strong style="color:#ff6b6b">see SL% col</strong></span>
        <span style="color:#ff6b6b;font-weight:700">⚠ Gap-down open → exit by 9:20 AM IST, do not hold</span>
        <span style="color:#a0aab4">📉 First 15-min low breaks SL → exit immediately</span>
      </div>
    </div>
    <p class="scroll-hint">← swipe to see all columns</p>
    <div class="tw">
      <table>
        <thead><tr><th>#</th><th>Symbol</th><th>Conviction</th><th>Close (₹)</th><th>Change</th><th>Vol Ratio</th><th>RSI</th><th>ADX</th><th>Range Pos</th><th>52W High</th><th>Gap</th><th>Candle</th><th>MACD</th><th>RS</th><th>Weekly</th><th>Sector</th><th>SL / Target</th><th>R:R</th><th>Position Size</th><th>BTST Score</th></tr></thead>
        <tbody>{india_rows}</tbody>
      </table>
    </div>
    {_legend()}
  </div>

  <!-- USA PANEL -->
  <div class="tab-panel" id="panel-usa">
    {usa_cards}
    <div class="sh">
      <div class="sh-title">🎯 Top BTST Candidates — USA</div>
      <div class="sh-line"></div>
      <div class="sh-sub">Entry window: 3:30–4:00 PM EST</div>
    </div>
    <div style="margin:10px 0 14px;padding:12px 16px;background:rgba(255,193,7,0.06);border:1px solid rgba(255,193,7,0.22);border-radius:8px;display:flex;flex-wrap:wrap;gap:10px 24px;align-items:flex-start">
      <div style="font-family:var(--mono);font-size:.72rem;font-weight:700;color:#ffd740;white-space:nowrap;padding-top:1px">⚡ EXIT RULES · USA</div>
      <div style="display:flex;flex-wrap:wrap;gap:8px 20px;flex:1;font-family:var(--mono);font-size:.72rem">
        <span style="color:#a0aab4">🎯 Target: <strong style="color:#00ff88">see Target% col</strong></span>
        <span style="color:#a0aab4">🛑 SL: <strong style="color:#ff6b6b">see SL% col</strong></span>
        <span style="color:#ff6b6b;font-weight:700">⚠ Gap-down open → exit by 9:35 AM EST, do not hold</span>
        <span style="color:#a0aab4">📉 First 15-min low breaks SL → exit immediately</span>
      </div>
    </div>
    <p class="scroll-hint">← swipe to see all columns</p>
    <div class="tw">
      <table>
        <thead><tr><th>#</th><th>Symbol</th><th>Conviction</th><th>Close ($)</th><th>Change</th><th>Vol Ratio</th><th>RSI</th><th>ADX</th><th>Range Pos</th><th>52W High</th><th>Gap</th><th>Candle</th><th>MACD</th><th>RS</th><th>Weekly</th><th>Sector</th><th>SL / Target</th><th>R:R</th><th>Position Size</th><th>BTST Score</th></tr></thead>
        <tbody>{usa_rows}</tbody>
      </table>
    </div>
    {_legend()}
  </div>

  <!-- ORB PANEL -->
  <style>
    .orb-sub{{margin:20px 0 10px;padding:10px 14px;background:rgba(249,168,37,.05);border:1px solid rgba(249,168,37,.18);border-radius:8px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
    .orb-sub-title{{font-family:var(--mono);font-size:.72rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#f9a825}}
    .orb-chip{{font-family:var(--mono);font-size:.65rem;padding:2px 8px;border-radius:4px;background:rgba(249,168,37,.12);border:1px solid rgba(249,168,37,.25);color:#f9a825}}
    .orb-info{{font-size:.72rem;color:var(--muted);line-height:1.5;margin-top:8px;padding:9px 13px;background:var(--surf);border:1px solid var(--border);border-radius:7px}}
    .orb-info strong{{color:var(--text)}}
  </style>

  <div class="tab-panel" id="panel-orb">

    <!-- ORB explainer banner -->
    <div class="orb-info">
      <strong>⚡ Opening Range Breakout (ORB) — Intraday Strategy</strong><br>
      Opening Range = first 15 min of trading (3 × 5-min bars).
      &nbsp;·&nbsp; <strong>Buy</strong> when price breaks above ORB High with volume confirmation.
      &nbsp;·&nbsp; <strong>Target</strong> = ORB High + 1.5 × ORB Range.
      &nbsp;·&nbsp; <strong>Stop Loss</strong> = ORB Low (or price − ATR, whichever is tighter).
      &nbsp;·&nbsp; Entry window: <strong>9:30–10:30 AM IST / EST</strong> &nbsp;·&nbsp; Exit by: <strong>3:00 PM IST / 3:30 PM EST</strong>.
    </div>

    <!-- India ORB -->
    <div class="orb-sub" style="margin-top:16px">
      <span class="orb-sub-title">🇮🇳 India ORB</span>
      <span class="orb-chip">Nifty 100 · 5-min</span>
      <span class="orb-chip">{orb_india_count} breakout(s)</span>
      <span style="font-family:var(--mono);font-size:.62rem;color:var(--muted);margin-left:auto">ORB Window: 9:15–9:30 AM IST</span>
    </div>
    <p class="scroll-hint">← swipe to see all columns</p>
    <div class="tw">
      <table>
        <thead><tr>
          <th>#</th><th>Symbol</th><th>Price (₹)</th>
          <th>ORB High</th><th>ORB Low</th><th>ORB Range%</th>
          <th>Brk Above</th><th>Vol Ratio</th><th>RSI 5m</th><th>ADX 5m</th>
          <th>Sector</th><th>Stop Loss</th><th>Target</th><th>R:R</th><th>ORB Score</th>
        </tr></thead>
        <tbody>{orb_india_rows}</tbody>
      </table>
    </div>

    <!-- USA ORB -->
    <div class="orb-sub" style="margin-top:24px">
      <span class="orb-sub-title">🇺🇸 USA ORB</span>
      <span class="orb-chip">S&amp;P 500 · 5-min</span>
      <span class="orb-chip">{orb_usa_count} breakout(s)</span>
      <span style="font-family:var(--mono);font-size:.62rem;color:var(--muted);margin-left:auto">ORB Window: 9:30–9:45 AM EST</span>
    </div>
    <p class="scroll-hint">← swipe to see all columns</p>
    <div class="tw">
      <table>
        <thead><tr>
          <th>#</th><th>Symbol</th><th>Price ($)</th>
          <th>ORB High</th><th>ORB Low</th><th>ORB Range%</th>
          <th>Brk Above</th><th>Vol Ratio</th><th>RSI 5m</th><th>ADX 5m</th>
          <th>Sector</th><th>Stop Loss</th><th>Target</th><th>R:R</th><th>ORB Score</th>
        </tr></thead>
        <tbody>{orb_usa_rows}</tbody>
      </table>
    </div>

    <!-- ORB legend -->
    <div class="legend" style="margin-top:16px">
      <div class="li"><div class="ld" style="background:#f9a825"></div>ORB Score ≥60 — Strong breakout signal</div>
      <div class="li"><div class="ld" style="background:var(--yellow)"></div>Score 40–59 — Moderate breakout</div>
      <div class="li"><div class="ld" style="background:var(--red)"></div>Score &lt;40 — Weak / avoid</div>
      <div class="li"><div class="ld" style="background:var(--green)"></div>ORB High — Resistance turned support on breakout</div>
      <div class="li"><div class="ld" style="background:var(--red)"></div>ORB Low — Invalidation / stop level</div>
      <div class="li"><div class="ld" style="background:var(--green)"></div>Brk Above — % price is above ORB High</div>
      <div class="li"><div class="ld" style="background:var(--blue)"></div>Vol Ratio ≥2× = Strong institutional buying</div>
      <div class="li"><div class="ld" style="background:var(--muted)"></div>ADX 5m &gt;25 = Intraday trend confirmed</div>
      <div class="li"><div class="ld" style="background:var(--muted)"></div>Tight ORB Range (&lt;1%) = Cleaner, more reliable breakout</div>
    </div>

  </div>

  <div class="sh" style="margin-top:36px;cursor:pointer;user-select:none" onclick="toggleAllScoring()" id="scoring-header">
    <div class="sh-title" style="display:flex;align-items:center;gap:10px">
      ⚙️ Scoring Parameters
      <span id="scoring-toggle-icon" style="font-size:.9rem;color:var(--blue);transition:transform .3s ease;display:inline-block;transform:rotate(-90deg)">▼</span>
    </div>
    <div class="sh-line"></div>
    <div class="sh-sub">Max score ≈ 138 pts · base 100 + sector (7) + candle (10) + RS (5) + weekly MTF (8) + gap-up (8) &nbsp;<span id="scoring-sub-hint">· click to expand</span></div>
  </div>

  <!-- ── Compact 4-col scoring params ── -->
  <style>
    /* Entire scoring block collapses */
    #scoring-body{{
      overflow:hidden;max-height:0;opacity:0;pointer-events:none;
      transition:max-height .5s ease, opacity .35s ease;
    }}
    #scoring-body.open{{max-height:3000px;opacity:1;pointer-events:auto}}
    #scoring-header:hover .sh-title{{color:var(--blue)}}

    .sp-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:9px;margin-top:16px}}
    @media(max-width:900px){{.sp-grid{{grid-template-columns:repeat(2,1fr)}}}}
    @media(max-width:480px){{.sp-grid{{grid-template-columns:1fr}}}}

    /* Sub-section labels (non-clickable) */
    .sp-section{{
      grid-column:1/-1;
      font-family:var(--mono);font-size:.72rem;letter-spacing:.14em;text-transform:uppercase;
      color:#c8d4e0;padding:5px 2px 7px;border-bottom:1px solid var(--border);margin-top:10px;
    }}

    /* Scoring parameter cards */
    .sp-card{{background:var(--surf);border:1px solid var(--border);border-radius:8px;padding:13px 15px;position:relative;overflow:hidden;transition:border-color .15s}}
    .sp-card:hover{{border-color:#2a3245}}
    .sp-card::before{{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--sp-accent,transparent);opacity:.75}}
    .sp-card.sg{{--sp-accent:var(--green)}} .sp-card.sr{{--sp-accent:var(--red)}} .sp-card.sc{{--sp-accent:var(--blue)}} .sp-card.sy{{--sp-accent:var(--yellow)}}
    .sp-top{{display:flex;align-items:flex-start;justify-content:space-between;gap:5px;margin-bottom:5px}}
    .sp-name{{font-family:var(--mono);font-size:.76rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase;color:#eef4ff}}
    .sp-badge{{font-family:var(--mono);font-size:.76rem;font-weight:700;padding:2px 8px;border-radius:4px;white-space:nowrap;flex-shrink:0}}
    .sp-badge.sg{{color:var(--green);background:rgba(0,230,118,.12)}} .sp-badge.sr{{color:var(--red);background:rgba(255,82,82,.12)}}
    .sp-badge.sc{{color:var(--blue);background:rgba(64,196,255,.12)}} .sp-badge.sy{{color:var(--yellow);background:rgba(255,202,40,.1)}}
    .sp-desc{{font-size:.86rem;color:#c4d4e4;line-height:1.5;margin-bottom:6px}}
    .sp-tags{{display:flex;flex-wrap:wrap;gap:4px}}
    .sp-tag{{font-family:var(--mono);font-size:.7rem;padding:2px 7px;border-radius:3px;background:var(--surf2);border:1px solid var(--border);color:#d8e4f0;white-space:nowrap}}
    .sp-tag.tg{{color:var(--green);border-color:rgba(0,230,118,.3);background:rgba(0,230,118,.07)}}
    .sp-tag.tr{{color:var(--red);border-color:rgba(255,82,82,.3);background:rgba(255,82,82,.07)}}
    .sp-tag.tc{{color:var(--blue);border-color:rgba(64,196,255,.25);background:rgba(64,196,255,.07)}}
    .sp-formula{{font-family:var(--mono);font-size:.72rem;color:var(--blue);background:rgba(64,196,255,.1);border:1px solid rgba(64,196,255,.2);padding:3px 9px;border-radius:4px;display:inline-block;margin-top:5px}}
    .sp-footer{{margin-top:10px;padding:10px 14px;background:var(--surf);border:1px solid var(--border);border-radius:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}}
    .sp-footer-lbl{{font-family:var(--mono);font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:#b8c8d8;margin-right:4px}}
    .sp-chip{{font-family:var(--mono);font-size:.72rem;font-weight:600;padding:3px 10px;border-radius:5px;border:1px solid}}
    .sp-chip.sg{{color:var(--green);background:rgba(0,230,118,.1);border-color:rgba(0,230,118,.2)}}
    .sp-chip.sr{{color:var(--red);background:rgba(255,82,82,.1);border-color:rgba(255,82,82,.2)}}
    .sp-chip.sc{{color:var(--blue);background:rgba(64,196,255,.1);border-color:rgba(64,196,255,.2)}}
    .sp-sep{{color:var(--border);margin:0 1px}}
    /* Collapsible body — closed by default */
    #scoring-body{{overflow:hidden;max-height:0;opacity:0;pointer-events:none;transition:max-height .55s ease,opacity .35s ease;margin-top:4px}}
    #scoring-body.open{{max-height:3000px;opacity:1;pointer-events:auto}}
    /* Sub-section labels inside grid */
    .sp-section{{grid-column:1/-1;font-family:var(--mono);font-size:.7rem;letter-spacing:.14em;text-transform:uppercase;color:#c8d4e0;padding:5px 2px 7px;border-bottom:1px solid var(--border);margin-top:10px}}
  </style>

  <div id="scoring-body">
  <div class="sp-grid">

    <!-- MOMENTUM & TREND -->
    <div class="sp-section">📈 Momentum &amp; Trend</div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">Volume Surge</span><span class="sp-badge sg">20 PTS</span></div>
      <div class="sp-desc">Confirms institutional participation.</div>
      <div class="sp-tags"><span class="sp-tag tg">Vol &gt;1.5× 10-day avg</span></div>
    </div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">EMA Alignment</span><span class="sp-badge sg">15 PTS</span></div>
      <div class="sp-desc">Confirms bullish structure.</div>
      <div class="sp-tags"><span class="sp-tag tg">Above 20 EMA</span><span class="sp-tag tg">Above 50 EMA</span></div>
    </div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">RSI Zone</span><span class="sp-badge sg">15 PTS</span></div>
      <div class="sp-desc">Strong momentum without being overbought.</div>
      <div class="sp-tags"><span class="sp-tag tg">RSI 55 – 75</span></div>
    </div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">MACD Signal</span><span class="sp-badge sg">15 PTS</span></div>
      <div class="sp-desc">Signals continuation.</div>
      <div class="sp-tags"><span class="sp-tag tg">+ve histogram</span><span class="sp-tag tg">Fresh bull crossover</span></div>
    </div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">ADX Trend</span><span class="sp-badge sg">10 PTS</span></div>
      <div class="sp-desc">Reduces overnight whipsaw risk.</div>
      <div class="sp-tags"><span class="sp-tag tg">ADX &gt; 25</span></div>
    </div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">52-Week High</span><span class="sp-badge sg">10 PTS</span></div>
      <div class="sp-desc">Proximity to breakout zone.</div>
      <div class="sp-tags"><span class="sp-tag tg">Within 5% → Full</span><span class="sp-tag">Within 10% → Half</span></div>
    </div>

    <!-- PRICE ACTION -->
    <div class="sp-section">🕯 Price Action &amp; Structure</div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">Price Breakout</span><span class="sp-badge sg">15 PTS</span></div>
      <div class="sp-desc">Buyer dominance at close.</div>
      <div class="sp-tags"><span class="sp-tag tg">Close in top 5–10% of range</span></div>
    </div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">Candlestick Pattern</span><span class="sp-badge sg">+6–10 PTS</span></div>
      <div class="sp-desc">High-confidence reversal/continuation candles.</div>
      <div class="sp-tags"><span class="sp-tag tg">M-Star +10</span><span class="sp-tag tg">Engulfing +8</span><span class="sp-tag tg">Hammer +6</span></div>
    </div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">Gap-Up &amp; Hold</span><span class="sp-badge sg">+5–8 PTS</span></div>
      <div class="sp-desc">Open gapped above prior close and held.</div>
      <div class="sp-tags"><span class="sp-tag tg">≥1% gap + pos ≥60% → +8</span><span class="sp-tag">≥0.5% held → +5</span></div>
    </div>

    <div class="sp-card sg">
      <div class="sp-top"><span class="sp-name">Relative Strength</span><span class="sp-badge sg">+5 PTS</span></div>
      <div class="sp-desc">Outperformance vs the broader index.</div>
      <div class="sp-tags"><span class="sp-tag tg">Daily gain &gt; Index</span></div>
    </div>

    <!-- CONFIRMATION -->
    <div class="sp-section">🔗 Confirmation &amp; Alignment</div>

    <div class="sp-card sy">
      <div class="sp-top"><span class="sp-name">Sector Alignment</span><span class="sp-badge sy">+7 PTS</span></div>
      <div class="sp-desc">Bonus when sector index is also green on the day.</div>
      <div class="sp-tags"><span class="sp-tag">e.g. Nifty Bank, XLK</span></div>
    </div>

    <div class="sp-card sy">
      <div class="sp-top"><span class="sp-name">Weekly MTF Confirm</span><span class="sp-badge sy">+8 PTS</span></div>
      <div class="sp-desc">Weekly &amp; daily trends aligned. Reduces reversal risk.</div>
      <div class="sp-tags"><span class="sp-tag">Daily close &gt; Weekly EMA20</span></div>
    </div>

    <!-- RISK -->
    <div class="sp-section">⚠ Risk &amp; Dynamic Levels</div>

    <div class="sp-card sr">
      <div class="sp-top"><span class="sp-name">ATR Penalty</span><span class="sp-badge sr">−40%</span></div>
      <div class="sp-desc">Avoids chasing overextended stocks. Triggered if day's move exceeds 1.5× ATR.</div>
      <div class="sp-tags"><span class="sp-tag tr">Move &gt; 1.5× ATR → penalty</span></div>
    </div>

    <div class="sp-card sc">
      <div class="sp-top"><span class="sp-name">Stop Loss</span><span class="sp-badge sc">DYNAMIC</span></div>
      <div class="sp-desc">Limits overnight gap risk. Tighter of the two values.</div>
      <div class="sp-formula">max( Today's Low,  Close − 1×ATR )</div>
    </div>

    <div class="sp-card sc">
      <div class="sp-top"><span class="sp-name">Target</span><span class="sp-badge sc">DYNAMIC</span></div>
      <div class="sp-desc">Respects each stock's typical daily volatility range.</div>
      <div class="sp-formula">Close + 1.5× ATR</div>
    </div>

  </div>

  <!-- Score breakdown footer -->
  <div class="sp-footer">
    <span class="sp-footer-lbl">Score Breakdown</span>
    <span class="sp-chip sg">Base 100</span><span class="sp-sep">+</span>
    <span class="sp-chip sg">Sector +7</span><span class="sp-sep">+</span>
    <span class="sp-chip sg">Candle +10</span><span class="sp-sep">+</span>
    <span class="sp-chip sg">RS +5</span><span class="sp-sep">+</span>
    <span class="sp-chip sg">Weekly MTF +8</span><span class="sp-sep">+</span>
    <span class="sp-chip sg">Gap-Up +8</span><span class="sp-sep">=</span>
    <span class="sp-chip sg" style="font-size:.76rem;padding:3px 10px">Max 138 PTS</span>
    <span class="sp-chip sr" style="margin-left:auto">ATR Penalty −40%</span>
    <span class="sp-chip sc">SL: Dynamic</span>
    <span class="sp-chip sc">Target: Dynamic</span>
  </div>

  </div><!-- /scoring-body -->

  <!-- DISCLAIMER -->
  <div class="disc">
    <strong>⚠ Disclaimer:</strong> This report is for educational and research purposes only.
    It does <strong>not</strong> constitute financial advice or any recommendation to buy or sell securities.
    Equity trading involves significant risk. Past patterns do not guarantee future performance.
    For Indian markets, consult a <strong>SEBI-registered advisor</strong>.
    For US markets, consult a <strong>FINRA/SEC-registered advisor</strong> before placing any trades.
  </div>
</div>

<!-- FOOTER -->
<div class="footer">
  Generated by BTST Screener v3 (Fixed) · Python + yfinance + pandas-ta
  &nbsp;|&nbsp; India: {time_ist} &nbsp;·&nbsp; USA: {time_est}
  <br>
  Signals: Vol(20) · RSI(15) · MACD(15) · EMA(15) · Breakout(15) · ADX(10) · 52W(10) · Gap(+8) · Sector(+7) · Candle(+10) · RS(+5) · Weekly MTF(+8) · Marubozu(+12)
  <br>
  v3 Fixes: Closing Marubozu · MACD direction · MACD crossover · Dynamic VIX · Earnings filter · Min R:R 1.5x · Position sizing · Friday RVOL guard
</div>

<script>
  // Live IST clock — updates every second
  function updateLiveClock() {{
    const now = new Date();
    // Convert to IST (UTC+5:30)
    const istOffset = 5.5 * 60 * 60 * 1000;
    const ist = new Date(now.getTime() + (now.getTimezoneOffset() * 60 * 1000) + istOffset);
    let h = ist.getHours();
    const ampm = h >= 12 ? 'PM' : 'AM';
    h = h % 12 || 12;
    const mm = String(ist.getMinutes()).padStart(2, '0');
    const ss = String(ist.getSeconds()).padStart(2, '0');
    const el = document.getElementById('live-clock');
    if (el) el.textContent = String(h).padStart(2,'0') + ':' + mm + ':' + ss + ' ' + ampm;
  }}
  updateLiveClock();
  setInterval(updateLiveClock, 1000);

  function switchTab(tab) {{
    // panels
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    document.getElementById('panel-' + tab).classList.add('active');
    // buttons
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    document.querySelector('.' + tab + '-btn').classList.add('active');
    // pills — only show pills row that exists for this tab
    document.querySelectorAll('.market-pills').forEach(p => p.classList.remove('active'));
    const pillEl = document.getElementById('pills-' + tab);
    if (pillEl) pillEl.classList.add('active');
  }}

  // Scoring Parameters — single toggle for entire section
  function toggleAllScoring() {{
    const body = document.getElementById('scoring-body');
    const icon = document.getElementById('scoring-toggle-icon');
    const sub  = document.getElementById('scoring-sub-hint');
    if (body.classList.contains('open')) {{
      body.classList.remove('open');
      icon.style.transform = 'rotate(-90deg)';
      if (sub) sub.textContent = '· click to expand';
    }} else {{
      body.classList.add('open');
      icon.style.transform = 'rotate(0deg)';
      if (sub) sub.textContent = '· click to collapse';
    }}
  }}

</script>
</body>
</html>"""

    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  🌐  HTML Report saved → {Fore.CYAN}{html_file}{Style.RESET_ALL}")

    # Also save as index.html → GitHub Pages serves it at the root URL
    # e.g. https://krishnateja08.github.io/BTST-Screener/ (no filename needed)
    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  🔗  index.html updated → root URL now serves latest report")

    return html_file


def _legend() -> str:
    return """
    <div class="legend">
      <div class="li"><div class="ld" style="background:var(--green)"></div>Score ≥70 — Strong Signal</div>
      <div class="li"><div class="ld" style="background:var(--yellow)"></div>Score 50–69 — Moderate</div>
      <div class="li"><div class="ld" style="background:var(--red)"></div>Score &lt;50 — Avoid</div>
      <div class="li"><div class="ld" style="background:var(--blue)"></div>Vol Ratio &gt;1.5× = Conviction buying</div>
      <div class="li"><div class="ld" style="background:var(--muted)"></div>RSI 55–75 = Ideal momentum zone</div>
      <div class="li"><div class="ld" style="background:var(--muted)"></div>ADX &gt;25 = Confirmed trend</div>
      <div class="li"><div class="ld" style="background:var(--muted)"></div>Range Pos &gt;90% = Closing near high</div>
      <div class="li"><div class="ld" style="background:var(--green)"></div>⬆ = Gap-up open that held by close</div>
      <div class="li"><div class="ld" style="background:var(--green)"></div>⭐/🕯/🔨 = Candle pattern detected</div>
      <div class="li"><div class="ld" style="background:var(--green)"></div>RS+ = Stock beat index today</div>
      <div class="li"><div class="ld" style="background:var(--green)"></div>✅ W = Above weekly EMA20</div>
      <div class="li"><div class="ld" style="background:var(--muted)"></div>▲/▼ in score = vs previous session</div>
    </div>"""


# ══════════════════════════════════════════════════════════
# BACKTEST — replay past CSV picks against next-day actuals
# ══════════════════════════════════════════════════════════

def run_backtest(prefix: str, days: int = 30):
    """
    For each past btst_{prefix}_YYYY-MM-DD.csv found in the last `days` calendar days:
      - Load picks: Symbol, Close (entry), Stop_Loss, Target, BTST_Score
      - Download next trading day's OHLC via yfinance
      - Classify each pick: WIN (High ≥ Target), LOSS (Low ≤ SL or gap-down open ≤ SL),
        NEUTRAL (neither touched)
    Prints hit rate, score-stratified stats, score↔return correlation,
    top-5 wins / worst-5 losses, and saves a backtest CSV.
    """
    tz_now   = datetime.now(tz=IST if prefix == "india" else EST)
    today    = tz_now.date()
    sym_sfx  = ".NS" if prefix == "india" else ""

    hdr(f"BACKTEST — {prefix.upper()} | Scanning last {days} calendar days")

    # ── 1. Collect all available past CSVs ───────────────────
    all_rows: list[dict] = []
    dates_found: list[str] = []

    for d in range(1, days + 1):
        date      = today - timedelta(days=d)
        date_str  = date.strftime("%Y-%m-%d")
        csv_path  = f"btst_{prefix}_{date_str}.csv"
        try:
            df = pd.read_csv(csv_path)
            if df.empty or "Symbol" not in df.columns:
                continue
            for _, row in df.iterrows():
                all_rows.append({
                    "date_str": date_str,
                    "Symbol":   str(row.get("Symbol", "")),
                    "Entry":    float(row.get("Close", 0)),
                    "SL":       float(row.get("Stop_Loss", 0)),
                    "Target":   float(row.get("Target", 0)),
                    "Score":    float(row.get("BTST_Score", 0)),
                })
            dates_found.append(date_str)
        except FileNotFoundError:
            continue
        except Exception:
            continue

    if not all_rows:
        print(f"\n  {Fore.YELLOW}No past CSVs found in the last {days} days.")
        print(f"  Run the screener daily for a few sessions first, then backtest.{Style.RESET_ALL}")
        return

    print(f"  📂  Found {len(dates_found)} past session(s), "
          f"{len(all_rows)} total pick(s) to evaluate.")

    # ── 2. Batch-download price history for all unique symbols ─
    raw_syms  = list({r["Symbol"] for r in all_rows if r["Symbol"]})
    dl_syms   = [s + sym_sfx for s in raw_syms]

    print(f"  📥  Downloading 1-year history for {len(dl_syms)} symbols …", flush=True)
    cache_raw = _batch_download(dl_syms)

    # Normalise keys → clean symbol (same logic as score_stock_from_df)
    norm_cache: dict[str, pd.DataFrame] = {}
    for sym, df in cache_raw.items():
        clean = sym.replace(".NS", "").replace("-", ".").replace("BRK.B", "BRK-B")
        df_copy = df.copy()
        df_copy.index = pd.to_datetime(df_copy.index)
        norm_cache[clean] = df_copy

    print(f"  ✅  History loaded for {len(norm_cache)} symbols. Evaluating picks …")

    # ── 3. Evaluate each pick against the next trading day ────
    results: list[dict] = []

    for r in all_rows:
        sym   = r["Symbol"]
        entry = r["Entry"]
        sl    = r["SL"]
        tgt   = r["Target"]
        score = r["Score"]
        pick_date = pd.Timestamp(r["date_str"])

        if sym not in norm_cache or entry <= 0 or sl <= 0 or tgt <= 0:
            continue

        df_sym     = norm_cache[sym]
        future     = df_sym[df_sym.index > pick_date]
        if future.empty:
            continue          # no next-day data yet (picked today)

        nxt        = future.iloc[0]
        nxt_open   = float(nxt.get("Open",  nxt["Close"]))   # fallback: use close if Open missing (split-adjusted data gap)
        nxt_high   = float(nxt["High"])
        nxt_low    = float(nxt["Low"])
        nxt_close  = float(nxt["Close"])
        nxt_date   = future.index[0].strftime("%Y-%m-%d")

        # Gap-down through SL at open → instant loss
        if nxt_open <= sl:
            outcome = "LOSS"
        elif nxt_high >= tgt:
            outcome = "WIN"
        elif nxt_low <= sl:
            outcome = "LOSS"
        else:
            outcome = "NEUTRAL"

        actual_chg = (nxt_close - entry) / entry * 100 if entry > 0 else 0.0

        results.append({
            "Date":       r["date_str"],
            "Symbol":     sym,
            "Score":      round(score, 1),
            "Entry":      round(entry, 2),
            "SL":         round(sl,    2),
            "Target":     round(tgt,   2),
            "Next_Open":  round(nxt_open,  2),
            "Next_High":  round(nxt_high,  2),
            "Next_Low":   round(nxt_low,   2),
            "Next_Close": round(nxt_close, 2),
            "Actual_%":   round(actual_chg, 2),
            "Outcome":    outcome,
            "Next_Date":  nxt_date,
        })

    if not results:
        print(f"  {Fore.YELLOW}No picks could be evaluated "
              f"(next-day data unavailable — try again tomorrow).{Style.RESET_ALL}")
        return

    res_df = pd.DataFrame(results)

    # ── 4. Aggregate stats ────────────────────────────────────
    total   = len(res_df)
    wins    = (res_df["Outcome"] == "WIN").sum()
    losses  = (res_df["Outcome"] == "LOSS").sum()
    neutral = (res_df["Outcome"] == "NEUTRAL").sum()
    hit_rt  = wins / total * 100 if total else 0.0
    loss_rt = losses / total * 100 if total else 0.0
    avg_chg = res_df["Actual_%"].mean()

    # ── Win / Loss averages for expectancy ───────────────────
    win_df  = res_df[res_df["Outcome"] == "WIN"]
    loss_df = res_df[res_df["Outcome"] == "LOSS"]
    avg_win  = win_df["Actual_%"].mean()  if len(win_df)  else 0.0
    avg_loss = loss_df["Actual_%"].mean() if len(loss_df) else 0.0
    # Expectancy = (win_rate × avg_win) + (loss_rate × avg_loss)
    # A positive number means profitable per trade in expectation.
    expectancy = (hit_rt / 100 * avg_win) + (loss_rt / 100 * avg_loss)

    # ── Max Drawdown ──────────────────────────────────────────
    # Simulates holding a portfolio of all picks simultaneously;
    # tracks the worst single-pick return as a simple per-trade drawdown.
    max_drawdown = res_df["Actual_%"].min() if total else 0.0

    # ── Score-stratified tiers ────────────────────────────────
    def _tier_stats(subset: pd.DataFrame, label: str) -> list:
        if subset.empty:
            return [label, 0, 0, "—", "—", "—"]
        n      = len(subset)
        w      = (subset["Outcome"] == "WIN").sum()
        l      = (subset["Outcome"] == "LOSS").sum()
        wr     = w / n * 100
        lr     = l / n * 100
        aw     = subset[subset["Outcome"] == "WIN"]["Actual_%"].mean()
        al     = subset[subset["Outcome"] == "LOSS"]["Actual_%"].mean()
        exp    = (wr / 100 * (aw if not pd.isna(aw) else 0)) + \
                 (lr / 100 * (al if not pd.isna(al) else 0))
        avg_c  = subset["Actual_%"].mean()
        return [label, n, w, f"{wr:.1f}%",
                f"{avg_c:+.2f}%",
                f"{exp:+.3f}%"]

    hc = res_df[res_df["Score"] >= SCORE_HIGH_CONVICTION]
    gc = res_df[(res_df["Score"] >= SCORE_GOOD) & (res_df["Score"] < SCORE_HIGH_CONVICTION)]
    mc = res_df[(res_df["Score"] >= SCORE_MODERATE) & (res_df["Score"] < SCORE_GOOD)]
    wk = res_df[res_df["Score"] < SCORE_MODERATE]

    # Score ↔ return correlation
    corr = (res_df[["Score", "Actual_%"]].corr().iloc[0, 1]
            if len(res_df) >= 5 else float("nan"))

    hdr(f"BACKTEST RESULTS — {prefix.upper()}")
    print(f"  Sessions analysed  : {len(dates_found)}  ({dates_found[-1]} → {dates_found[0]})")
    print(f"  Total picks        : {total}")
    print()

    w_col  = Fore.GREEN if hit_rt >= 55 else Fore.YELLOW if hit_rt >= 45 else Fore.RED
    e_col  = Fore.GREEN if expectancy > 0 else Fore.RED
    dd_col = Fore.RED   if max_drawdown < -3 else Fore.YELLOW if max_drawdown < -1.5 else Fore.GREEN
    c_col  = Fore.GREEN if avg_chg > 0 else Fore.RED

    print(f"  {'Wins  (Target hit)':<24}: {Fore.GREEN}{wins:>4}{Style.RESET_ALL}  "
          f"  avg gain  {Fore.GREEN}{avg_win:>+6.2f}%{Style.RESET_ALL}")
    print(f"  {'Losses (SL hit)':<24}: {Fore.RED}{losses:>4}{Style.RESET_ALL}  "
          f"  avg loss  {Fore.RED}{avg_loss:>+6.2f}%{Style.RESET_ALL}")
    print(f"  {'Neutral (neither)':<24}: {Fore.YELLOW}{neutral:>4}{Style.RESET_ALL}")
    print(f"  {'Overall Hit Rate':<24}: {w_col}{hit_rt:>6.1f}%{Style.RESET_ALL}")
    print(f"  {'Avg Next-Day Chg':<24}: {c_col}{avg_chg:>+6.2f}%{Style.RESET_ALL}")
    print(f"  {'Expectancy / trade':<24}: {e_col}{expectancy:>+6.3f}%{Style.RESET_ALL}"
          f"  {'✅ profitable edge' if expectancy > 0 else '⚠ negative edge — review filters'}")
    print(f"  {'Max Drawdown (1 pick)':<24}: {dd_col}{max_drawdown:>+6.2f}%{Style.RESET_ALL}")
    print()

    # Score-stratified table with expectancy per tier
    strat_rows = [
        _tier_stats(hc, f"Score ≥ {SCORE_HIGH_CONVICTION} (HIGH)"),
        _tier_stats(gc, f"Score {SCORE_GOOD}–{SCORE_HIGH_CONVICTION-1} (GOOD)"),
        _tier_stats(mc, f"Score {SCORE_MODERATE}–{SCORE_GOOD-1} (MOD)"),
        _tier_stats(wk, f"Score < {SCORE_MODERATE} (WEAK)"),
    ]
    print(tabulate(strat_rows,
                   headers=["Tier", "Picks", "Wins", "Hit Rate", "Avg Chg", "Expectancy"],
                   tablefmt="simple"))
    print()

    if not pd.isna(corr):
        corr_col = Fore.GREEN if corr > 0.15 else Fore.YELLOW if corr > 0 else Fore.RED
        corr_lbl = ("✅ Score predicts returns"  if corr > 0.15 else
                    "↔ Weak positive link"       if corr > 0    else
                    "⚠ Score not yet predictive")
        print(f"  Score ↔ Return corr : {corr_col}{corr:+.3f}  {corr_lbl}{Style.RESET_ALL}")
        print()

    # Top-5 wins
    top_wins = (win_df
                .nlargest(5, "Actual_%")
                [["Date", "Symbol", "Score", "Entry", "Target", "Actual_%"]]
                .reset_index(drop=True))
    if not top_wins.empty:
        print(f"  {Fore.GREEN}── Top 5 Winning Picks ──{Style.RESET_ALL}")
        print(tabulate(top_wins,
                       headers=["Date", "Symbol", "Score", "Entry", "Target", "Actual %"],
                       tablefmt="simple", floatfmt=".2f"))
        print()

    # Worst-5 losses
    worst = (loss_df
             .nsmallest(5, "Actual_%")
             [["Date", "Symbol", "Score", "Entry", "SL", "Actual_%"]]
             .reset_index(drop=True))
    if not worst.empty:
        print(f"  {Fore.RED}── Worst 5 Losses ──{Style.RESET_ALL}")
        print(tabulate(worst,
                       headers=["Date", "Symbol", "Score", "Entry", "SL", "Actual %"],
                       tablefmt="simple", floatfmt=".2f"))
        print()

    # Save backtest CSV
    out_path = f"btst_{prefix}_backtest_{today}.csv"
    res_df.to_csv(out_path, index=False)
    print(f"  💾  Full backtest results → {Fore.CYAN}{out_path}{Style.RESET_ALL}")


# ══════════════════════════════════════════════════════════
# DISCLAIMER
# ══════════════════════════════════════════════════════════

def print_disclaimer():
    print(f"\n{Fore.YELLOW}{'─'*62}")
    print("  ⚠  DISCLAIMER: Educational/research purposes only.")
    print("  Not financial advice. Always do your own due diligence.")
    print(f"{'─'*62}{Style.RESET_ALL}\n")


# ══════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════

def _scan_india(date_str: str, run_orb: bool = True, min_score: float = 0.0):
    """Run India BTST scan (+ optional ORB scan). Returns (ok, chg, vix, top_df, full_df, orb_df)."""
    ok, chg, vix = check_market("india")
    full, sector_perf = run_screener(NIFTY100_SYMBOLS, "Nifty 100", chg)
    top  = pd.DataFrame()
    if not full.empty:
        top = filter_and_rank(full, min_score=min_score)
        print_report(top, "INDIA", ok, chg)
        save_csv(top, full, "india", date_str)
        save_meta("india", date_str, ok, chg, vix)
    # ORB scan — reuse sector_perf from BTST scan (no duplicate fetch)
    orb_top = pd.DataFrame()
    if run_orb:
        orb_raw = run_orb_screener(NIFTY100_SYMBOLS, "Nifty 100", sector_perf=sector_perf)
        orb_top = filter_and_rank_orb(orb_raw)
        if not orb_top.empty:
            orb_top.to_csv(f"orb_india_{date_str}.csv", index=False)
            print(f"  💾  ORB India CSV saved → orb_india_{date_str}.csv")
    return ok, chg, vix, top, full, orb_top


def _scan_usa(date_str: str, run_orb: bool = True, min_score: float = 0.0):
    """Run USA BTST scan (+ optional ORB scan). Returns (ok, chg, vix, top_df, full_df, orb_df)."""
    ok, chg, vix = check_market("usa")
    full, sector_perf = run_screener(SP500_TOP100_SYMBOLS, "S&P 500 Top 100", chg)
    top  = pd.DataFrame()
    if not full.empty:
        top = filter_and_rank(full, min_score=min_score)
        print_report(top, "USA", ok, chg)
        save_csv(top, full, "usa", date_str)
        save_meta("usa", date_str, ok, chg, vix)
    # ORB scan — reuse sector_perf from BTST scan (no duplicate fetch)
    orb_top = pd.DataFrame()
    if run_orb:
        orb_raw = run_orb_screener(SP500_TOP100_SYMBOLS, "S&P 500 Top 100", sector_perf=sector_perf)
        orb_top = filter_and_rank_orb(orb_raw)
        if not orb_top.empty:
            orb_top.to_csv(f"orb_usa_{date_str}.csv", index=False)
            print(f"  💾  ORB USA CSV saved → orb_usa_{date_str}.csv")
    return ok, chg, vix, top, full, orb_top


def main():
    parser = argparse.ArgumentParser(description="BTST Screener — India + USA")
    parser.add_argument("--india",     action="store_true", help="Scan India only")
    parser.add_argument("--usa",       action="store_true", help="Scan USA only")
    parser.add_argument("--no-orb",    action="store_true", help="Skip ORB intraday scan")
    parser.add_argument("--html-only", action="store_true",
                        help="Skip scanning — read existing CSVs and regenerate HTML report")
    parser.add_argument("--backtest",  action="store_true",
                        help="Replay past CSV picks against next-day actuals")
    parser.add_argument("--days",      type=int, default=30,
                        help="Calendar days to look back for backtest (default: 30)")
    parser.add_argument("--min-score", type=float, default=0.0,
                        help=f"Only show picks with BTST Score >= this value "
                             f"(e.g. --min-score 70 for GOOD, --min-score 80 for HIGH conviction)")
    args      = parser.parse_args()
    run_india = not args.usa   or args.india
    run_usa   = not args.india or args.usa
    do_orb    = not args.no_orb   # True by default; False when --no-orb passed
    min_score = args.min_score

    print(f"\n{Fore.CYAN}{'='*62}")
    print("   BTST SCREENER  |  India (Nifty 100)  +  USA (S&P 500 Top 100)")
    if min_score > 0:
        tier = "HIGH conviction" if min_score >= SCORE_HIGH_CONVICTION else \
               "GOOD+"          if min_score >= SCORE_GOOD else \
               "MODERATE+"
        print(f"   Score filter    |  ≥ {min_score:.0f}  ({tier})")
    print(f"{'='*62}{Style.RESET_ALL}")

    IST_NOW  = datetime.now(tz=IST)
    date_str = IST_NOW.strftime("%Y-%m-%d")

    # ── BACKTEST MODE ──────────────────────────────────────
    if args.backtest:
        if run_india:
            run_backtest("india", args.days)
        if run_usa:
            run_backtest("usa", args.days)
        print_disclaimer()
        return

    # ── HTML-ONLY MODE (used by CI commit job) ─────────────
    if args.html_only:
        hdr("HTML-ONLY MODE — reading saved CSVs + metadata")

        def _load_csv(prefix):
            top_path  = f"btst_{prefix}_{date_str}.csv"
            full_path = f"btst_{prefix}_full_{date_str}.csv"
            try:
                top  = pd.read_csv(top_path)
                full = pd.read_csv(full_path)
                print(f"  ✅  Loaded {prefix.upper()} CSVs ({len(top)} top / {len(full)} full)")
                return top, full
            except FileNotFoundError:
                print(f"  ⚠️   {prefix.upper()} CSVs not found for {date_str} — using empty")
                return pd.DataFrame(), pd.DataFrame()

        def _load_orb_csv(prefix):
            try:
                df = pd.read_csv(f"orb_{prefix}_{date_str}.csv")
                print(f"  ✅  Loaded ORB {prefix.upper()} CSV ({len(df)} picks)")
                return df
            except FileNotFoundError:
                return pd.DataFrame()

        india_top,  india_full = _load_csv("india")
        usa_top,    usa_full   = _load_csv("usa")
        india_ok,   india_chg, india_vix = load_meta("india", date_str)
        usa_ok,     usa_chg,   usa_vix   = load_meta("usa",   date_str)
        orb_india = _load_orb_csv("india")
        orb_usa   = _load_orb_csv("usa")

        generate_html_report(
            india_top, india_full, india_ok, india_chg, india_vix,
            usa_top,   usa_full,   usa_ok,   usa_chg,   usa_vix,
            date_str,
            orb_india_df=orb_india,
            orb_usa_df=orb_usa,
        )
        print_disclaimer()
        return

    # ── LIVE SCAN MODE ─────────────────────────────────────
    # tick_hub.py is only needed for India's ORB (intraday) scan — India's
    # daily/BTST data still comes from yfinance. schwab_proxy.py is needed
    # for USA any time USA is scanned (daily history + sector perf + ORB all
    # read from it now). Fail fast with a clear message instead of failing
    # deep inside a scan.
    try:
        hub_client.check_hubs_alive(
            need_india=(run_india and do_orb),
            need_usa=run_usa,
        )
    except hub_client.HubUnavailableError as e:
        print(f"\n{Fore.RED}{e}{Style.RESET_ALL}\n")
        sys.exit(1)

    india_ok, india_chg, india_vix = True, 0.0, 0.0
    india_full = india_top = pd.DataFrame()
    usa_ok,    usa_chg,   usa_vix   = True, 0.0, 0.0
    usa_full   = usa_top  = pd.DataFrame()
    orb_india  = orb_usa  = pd.DataFrame()

    if run_india and run_usa:
        hdr("Running India + USA scans in PARALLEL")
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_india = pool.submit(_scan_india, date_str, do_orb, min_score)
            f_usa   = pool.submit(_scan_usa,   date_str, do_orb, min_score)
            india_ok, india_chg, india_vix, india_top, india_full, orb_india = f_india.result()
            usa_ok,   usa_chg,   usa_vix,   usa_top,   usa_full,   orb_usa   = f_usa.result()

    elif run_india:
        india_ok, india_chg, india_vix, india_top, india_full, orb_india = _scan_india(date_str, do_orb, min_score)

    elif run_usa:
        usa_ok, usa_chg, usa_vix, usa_top, usa_full, orb_usa = _scan_usa(date_str, do_orb, min_score)

    # ── HTML ───────────────────────────────────────────────
    if run_india and run_usa:
        generate_html_report(
            india_top, india_full, india_ok, india_chg, india_vix,
            usa_top,   usa_full,   usa_ok,   usa_chg,   usa_vix,
            date_str,
            orb_india_df=orb_india,
            orb_usa_df=orb_usa,
        )

    print_disclaimer()


if __name__ == "__main__":
    main()
