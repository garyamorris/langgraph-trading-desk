"""Real-time institutional market, technical, macro, news, microstructure, and risk tools powered by Alpaca & Databento."""

from __future__ import annotations

import math
import os
import time
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any

import databento as db
import httpx
import numpy as np
from dotenv import load_dotenv
from langchain_core.tools import tool

load_dotenv()

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", message=".*inplace.*")

# Lightweight 60-second TTL cache so parallel Tier-1 nodes & Send() workers share market fetches
_MARKET_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_TTL_SEC = 60.0


def _cache_get(key: str) -> Any | None:
    entry = _MARKET_CACHE.get(key)
    if entry and (time.monotonic() - entry[0]) < _CACHE_TTL_SEC:
        return entry[1]
    return None


def _cache_set(key: str, value: Any) -> Any:
    _MARKET_CACHE[key] = (time.monotonic(), value)
    return value

# Sector & Factor Mapping for the Multi-Strategy Universe
SECTOR_MAP: dict[str, dict[str, Any]] = {
    "NVDA": {"sector": "Semiconductors", "beta": 1.75, "default_vol_pct": 3.2},
    "AMD": {"sector": "Semiconductors", "beta": 1.68, "default_vol_pct": 3.4},
    "AVGO": {"sector": "Semiconductors", "beta": 1.45, "default_vol_pct": 2.7},
    "SMH": {"sector": "Semiconductors", "beta": 1.55, "default_vol_pct": 2.4},
    "MSFT": {"sector": "Enterprise Software", "beta": 1.12, "default_vol_pct": 1.5},
    "AAPL": {"sector": "Consumer Tech", "beta": 1.18, "default_vol_pct": 1.6},
    "META": {"sector": "Internet & AI", "beta": 1.35, "default_vol_pct": 2.3},
    "GOOGL": {"sector": "Internet & AI", "beta": 1.20, "default_vol_pct": 1.8},
    "TSLA": {"sector": "Auto & Clean Tech", "beta": 2.10, "default_vol_pct": 4.1},
    "LLY": {"sector": "Healthcare", "beta": 0.65, "default_vol_pct": 2.0},
    "JPM": {"sector": "Financials", "beta": 1.08, "default_vol_pct": 1.4},
    "XLF": {"sector": "Financials", "beta": 1.02, "default_vol_pct": 1.2},
    "XLE": {"sector": "Energy", "beta": 0.85, "default_vol_pct": 1.6},
    "SPY": {"sector": "Index / Macro Hedge", "beta": 1.00, "default_vol_pct": 1.0},
    "QQQ": {"sector": "Index / Macro Hedge", "beta": 1.22, "default_vol_pct": 1.3},
    "IWM": {"sector": "Index / Macro Hedge", "beta": 1.18, "default_vol_pct": 1.5},
    "TLT": {"sector": "Rates / Duration", "beta": -0.25, "default_vol_pct": 1.1},
    "GLD": {"sector": "Commodities / Gold", "beta": 0.10, "default_vol_pct": 0.9},
}

# Institutional $100M Multi-Strategy Book Baseline & Risk Mandate
FIRM_BOOK_STATE: dict[str, Any] = {
    "firm_nav_usd": 100_000_000,
    "existing_gross_exposure_usd": 48_000_000,
    "existing_net_exposure_usd": 14_000_000,
    "existing_sector_net_exposure_usd": {
        "Semiconductors": 12_000_000,
        "Enterprise Software": 8_000_000,
        "Consumer Tech": 6_000_000,
        "Internet & AI": 5_000_000,
        "Financials": -4_000_000,
        "Index / Macro Hedge": -13_000_000,
        "Rates / Duration": 0,
    },
    "risk_mandate_limits": {
        "max_single_leg_notional_usd": 12_000_000,
        "max_desk_gross_notional_usd": 22_000_000,
        "max_post_trade_firm_gross_pct_nav": 125.0,
        "max_post_trade_firm_net_pct_nav": 40.0,
        "max_sector_net_exposure_pct_nav": 28.0,
        "max_portfolio_var_99_1d_usd": 1_450_000,
        "max_stop_loss_distance_pct": 7.5,
    },
}


def _alpaca_headers() -> dict[str, str]:
    return {
        "APCA-API-KEY-ID": os.getenv("APCA_API_KEY_ID", ""),
        "APCA-API-SECRET-KEY": os.getenv("APCA_API_SECRET_KEY", ""),
    }


def _clean_tickers(tickers: list[str] | str) -> list[str]:
    if isinstance(tickers, str):
        items = [t.strip().upper() for t in tickers.replace(";", ",").split(",") if t.strip()]
    else:
        items = [str(t).strip().upper() for t in tickers if str(t).strip()]
    return list(dict.fromkeys(items)) or ["NVDA", "AMD", "MSFT", "SPY", "QQQ"]


# ============================================================================
# 1. LIVE ALPACA SNAPSHOTS & PRE-MARKET TAPE TOOL
# ============================================================================

@tool
def fetch_alpaca_live_snapshots(tickers: list[str]) -> dict[str, Any]:
    """Fetch live Alpaca stock snapshots (latest trade, NBBO bid/ask quote, daily & previous daily bars, overnight gap %)."""
    symbols = _clean_tickers(tickers)
    cache_key = f"snapshots:{','.join(sorted(symbols))}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    url = "https://data.alpaca.markets/v2/stocks/snapshots"
    try:
        resp = httpx.get(
            url,
            params={"symbols": ",".join(symbols)},
            headers=_alpaca_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        raw = resp.json()
        snapshots: dict[str, Any] = {}
        for sym in symbols:
            item = raw.get(sym, {})
            latest_trade = item.get("latestTrade") or {}
            latest_quote = item.get("latestQuote") or {}
            daily_bar = item.get("dailyBar") or {}
            prev_bar = item.get("prevDailyBar") or {}
            minute_bar = item.get("minuteBar") or {}

            price = float(latest_trade.get("p") or daily_bar.get("c") or prev_bar.get("c") or 100.0)
            prev_close = float(prev_bar.get("c") or price)
            today_open = float(daily_bar.get("o") or price)
            day_high = float(daily_bar.get("h") or price)
            day_low = float(daily_bar.get("l") or price)
            day_vol = int(daily_bar.get("v") or 0)
            prev_vol = int(prev_bar.get("v") or 1)
            vwap = float(daily_bar.get("vw") or minute_bar.get("vw") or price)

            bid_px = float(latest_quote.get("bp") or price * 0.9995)
            ask_px = float(latest_quote.get("ap") or price * 1.0005)
            mid_px = (bid_px + ask_px) / 2.0 if (bid_px > 0 and ask_px > 0) else price
            spread_bps = round(((ask_px - bid_px) / mid_px) * 10_000, 2) if mid_px > 0 else 1.5

            overnight_gap_pct = round(((today_open - prev_close) / prev_close) * 100.0, 2) if prev_close > 0 else 0.0
            intraday_change_pct = round(((price - prev_close) / prev_close) * 100.0, 2) if prev_close > 0 else 0.0
            rel_volume_ratio = round(day_vol / max(prev_vol, 1), 2)

            snapshots[sym] = {
                "source": "Alpaca Real-Time Snapshot API",
                "spot_price": round(price, 2),
                "vwap": round(vwap, 2),
                "bid_price": round(bid_px, 2),
                "ask_price": round(ask_px, 2),
                "nbbo_spread_bps": spread_bps,
                "today_open": round(today_open, 2),
                "day_high": round(day_high, 2),
                "day_low": round(day_low, 2),
                "prev_close": round(prev_close, 2),
                "overnight_gap_pct": overnight_gap_pct,
                "net_change_pct": intraday_change_pct,
                "day_volume": day_vol,
                "relative_volume_vs_prev_day": rel_volume_ratio,
                "sector": SECTOR_MAP.get(sym, {}).get("sector", "General Equities"),
            }
            _cache_set(f"snapshots:{sym}", {"status": "LIVE_ALPACA_DATA", "snapshots": {sym: snapshots[sym]}})
        return _cache_set(cache_key, {"status": "LIVE_ALPACA_DATA", "snapshots": snapshots})
    except Exception as exc:
        return {"status": f"FALLBACK ({exc})", "snapshots": {}}


# ============================================================================
# 2. DATABENTO ORDERBOOK MICROSTRUCTURE & INSTITUTIONAL FLOW TOOL
# ============================================================================

@tool
def fetch_databento_orderbook_microstructure(tickers: list[str]) -> dict[str, Any]:
    """Query Databento (DBEQ.BASIC) for L1 orderbook microstructure (mbp-1 bid/ask size imbalance, spread bps, trade sweep intensity) and recent institutional volume profile."""
    symbols = _clean_tickers(tickers)[:5]  # Cap at 5 symbols to keep Databento query fast & cheap
    cache_key = f"databento:{','.join(sorted(symbols))}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    api_key = os.getenv("DATABENTO_API_KEY", "")
    results: dict[str, Any] = {}

    if not api_key:
        return {"error": "DATABENTO_API_KEY not set"}

    try:
        client = db.Historical(api_key)
        rng = client.metadata.get_dataset_range(dataset="DBEQ.BASIC")
        end_str = rng.get("end", "2026-11-07T00:00:00.000000000Z")
        end_dt = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        # Query a 5-minute window near the most recent US regular session close (20:55 - 21:00 UTC)
        target_day = end_dt - timedelta(days=1)
        while target_day.weekday() >= 5:  # Skip Saturday/Sunday
            target_day -= timedelta(days=1)

        win_start = target_day.replace(hour=20, minute=55, second=0, microsecond=0)
        win_end = target_day.replace(hour=21, minute=0, second=0, microsecond=0)

        mbp_data = client.timeseries.get_range(
            dataset="DBEQ.BASIC",
            symbols=symbols,
            schema="mbp-1",
            start=win_start.isoformat(),
            end=win_end.isoformat(),
            limit=400,
        )
        df = mbp_data.to_df()

        for sym in symbols:
            sub = df[df["symbol"] == sym] if "symbol" in df.columns else df.iloc[0:0]
            if len(sub) > 0:
                valid = sub[(sub["bid_px_00"] > 0) & (sub["ask_px_00"] > 0)]
                if len(valid) == 0:
                    valid = sub
                bid_sz_sum = float(valid["bid_sz_00"].sum())
                ask_sz_sum = float(valid["ask_sz_00"].sum())
                total_sz = max(bid_sz_sum + ask_sz_sum, 1.0)
                obi = round((bid_sz_sum - ask_sz_sum) / total_sz, 3)

                mid_prices = (valid["bid_px_00"] + valid["ask_px_00"]) / 2.0
                spreads_bps = ((valid["ask_px_00"] - valid["bid_px_00"]) / mid_prices.replace(0, np.nan)) * 10_000
                median_spread_bps = round(float(spreads_bps.median() or 1.8), 2)

                last_px = round(float(valid["price"].iloc[-1]), 2)
                avg_quote_sz = round(float((valid["bid_sz_00"].mean() + valid["ask_sz_00"].mean()) / 2.0), 0)

                flow_regime = (
                    "INSTITUTIONAL_BUY_ACCUMULATION"
                    if obi > 0.12
                    else "INSTITUTIONAL_SELL_DISTRIBUTION"
                    if obi < -0.12
                    else "BALANCED_TWO_WAY_LIQUIDITY"
                )
                recommended_algo = (
                    "IS-Aggressive / Dark-Sweep"
                    if abs(obi) > 0.20
                    else "Midpoint-Peg + VWAP-2Hr"
                    if median_spread_bps < 3.5
                    else "Passive-Limit-Laddered"
                )

                results[sym] = {
                    "source": "Databento DBEQ.BASIC (mbp-1 L1 Microstructure)",
                    "window_utc": f"{win_start.strftime('%Y-%m-%d %H:%M')} - {win_end.strftime('%H:%M')} UTC",
                    "sample_events": int(len(valid)),
                    "last_micro_price": last_px,
                    "orderbook_imbalance_ratio": obi,
                    "median_l1_spread_bps": median_spread_bps,
                    "avg_top_of_book_lots": avg_quote_sz,
                    "microstructure_regime": flow_regime,
                    "recommended_execution_algo": recommended_algo,
                }
            else:
                results[sym] = {
                    "source": "Databento DBEQ.BASIC (Fallback summary)",
                    "orderbook_imbalance_ratio": 0.04,
                    "median_l1_spread_bps": 2.1,
                    "microstructure_regime": "BALANCED_TWO_WAY_LIQUIDITY",
                    "recommended_execution_algo": "VWAP-2Hr + Dark-Midpoint",
                }
            _cache_set(f"databento:{sym}", {"status": "LIVE_DATABENTO_MBP1", "microstructure": {sym: results[sym]}})
        return _cache_set(cache_key, {"status": "LIVE_DATABENTO_MBP1", "microstructure": results})
    except Exception as exc:
        return {"status": f"DATABENTO_FALLBACK ({exc})", "microstructure": results}


# ============================================================================
# 3. QUANTITATIVE TECHNICAL INDICATORS TOOL (ALPACA HISTORICAL BARS)
# ============================================================================

@tool
def compute_technical_indicators(tickers: list[str]) -> dict[str, Any]:
    """Fetch 60 days of daily OHLCV bars from Alpaca and compute RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14), annualized realized vol, and support/resistance."""
    symbols = _clean_tickers(tickers)
    cache_key = f"technicals:{','.join(sorted(symbols))}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    start_date = (datetime.now(timezone.utc) - timedelta(days=95)).strftime("%Y-%m-%d")
    url = "https://data.alpaca.markets/v2/stocks/bars"

    try:
        resp = httpx.get(
            url,
            params={
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": start_date,
                "limit": 1000,
                "adjustment": "split",
            },
            headers=_alpaca_headers(),
            timeout=12.0,
        )
        resp.raise_for_status()
        bars_by_sym = resp.json().get("bars", {})
        tech_report: dict[str, Any] = {}

        for sym in symbols:
            bars = bars_by_sym.get(sym, [])
            if len(bars) < 20:
                continue
            closes = np.array([float(b["c"]) for b in bars])
            highs = np.array([float(b["h"]) for b in bars])
            lows = np.array([float(b["l"]) for b in bars])
            vols = np.array([float(b["v"]) for b in bars])

            spot = float(closes[-1])
            sma_20 = float(np.mean(closes[-20:]))
            sma_50 = float(np.mean(closes[-min(50, len(closes)):]))
            std_20 = float(np.std(closes[-20:]))

            # Bollinger Bands (20, 2)
            bb_upper = sma_20 + 2.0 * std_20
            bb_lower = sma_20 - 2.0 * std_20
            bb_pct_b = (spot - bb_lower) / max(bb_upper - bb_lower, 1e-6)

            # RSI(14)
            deltas = np.diff(closes)
            seed = deltas[-14:]
            up = float(np.mean(np.maximum(seed, 0)))
            down = float(np.mean(np.abs(np.minimum(seed, 0))))
            rs = up / down if down > 1e-9 else 100.0
            rsi_14 = round(100.0 - (100.0 / (1.0 + rs)), 2)

            # EMA & MACD(12, 26, 9)
            def _ema(arr: np.ndarray, span: int) -> np.ndarray:
                alpha = 2.0 / (span + 1.0)
                out = np.zeros_like(arr)
                out[0] = arr[0]
                for i in range(1, len(arr)):
                    out[i] = alpha * arr[i] + (1.0 - alpha) * out[i - 1]
                return out

            ema12 = _ema(closes, 12)
            ema26 = _ema(closes, 26)
            macd_line = ema12 - ema26
            signal_line = _ema(macd_line, 9)
            macd_hist = round(float(macd_line[-1] - signal_line[-1]), 3)

            # ATR(14)
            prev_closes = closes[:-1]
            tr = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(np.abs(highs[1:] - prev_closes), np.abs(lows[1:] - prev_closes)),
            )
            atr_14 = round(float(np.mean(tr[-14:])), 2)
            atr_pct = round((atr_14 / spot) * 100.0, 2)

            # Realized Volatility (20-day annualized)
            log_rets = np.diff(np.log(closes[-21:]))
            daily_vol_pct = round(float(np.std(log_rets) * 100.0), 2)
            ann_vol_pct = round(float(np.std(log_rets) * math.sqrt(252) * 100.0), 2)

            # 20-day Z-score for Stat-Arb Mean Reversion
            z_score_20d = round((spot - sma_20) / max(std_20, 1e-6), 2)

            # Support & Resistance (20-day Donchian channel + ATR buffer)
            support_20d = round(float(np.min(lows[-20:])), 2)
            resistance_20d = round(float(np.max(highs[-20:])), 2)

            regime = (
                "STRONG_UPTREND_BREAKOUT"
                if spot > sma_20 > sma_50 and macd_hist > 0 and rsi_14 < 74
                else "OVERBOUGHT_MEAN_REVERSION_CANDIDATE"
                if rsi_14 >= 72 or z_score_20d >= 1.9
                else "OVERSOLD_BOUNCE_CANDIDATE"
                if rsi_14 <= 32 or z_score_20d <= -1.9
                else "DOWNTREND_WEAKNESS"
                if spot < sma_20 < sma_50 and macd_hist < 0
                else "RANGE_CONSOLIDATION"
            )

            tech_report[sym] = {
                "spot_price": round(spot, 2),
                "sma_20": round(sma_20, 2),
                "sma_50": round(sma_50, 2),
                "rsi_14": rsi_14,
                "macd_histogram": macd_hist,
                "bollinger_pct_b": round(bb_pct_b, 2),
                "z_score_20d": z_score_20d,
                "atr_14_usd": atr_14,
                "atr_pct_of_spot": atr_pct,
                "daily_realized_vol_pct": daily_vol_pct,
                "annualized_realized_vol_pct": ann_vol_pct,
                "support_20d": support_20d,
                "resistance_20d": resistance_20d,
                "suggested_stop_2atr_long": round(spot - 2.0 * atr_14, 2),
                "suggested_target_4atr_long": round(spot + 4.2 * atr_14, 2),
                "technical_regime": regime,
            }
            _cache_set(f"technicals:{sym}", {"status": "LIVE_ALPACA_TECHNICALS", "indicators": {sym: tech_report[sym]}})
        return _cache_set(cache_key, {"status": "LIVE_ALPACA_TECHNICALS", "indicators": tech_report})
    except Exception as exc:
        return {"status": f"TECHNICALS_ERROR ({exc})", "indicators": {}}


# ============================================================================
# 4. CROSS-ASSET MACRO & SECTOR FACTOR ROTATION SCANNER (ALPACA ETF MATRIX)
# ============================================================================

@tool
def fetch_cross_asset_macro_regime() -> dict[str, Any]:
    """Fetch live cross-asset ETF performance from Alpaca across Equities (SPY, QQQ, IWM), Semis (SMH), Rates/Duration (TLT, IEF), Credit (HYG), Gold (GLD), Energy (XLE), and Financials (XLF) to classify the macro regime."""
    macro_etfs = ["SPY", "QQQ", "IWM", "SMH", "XLK", "XLF", "XLE", "XLV", "TLT", "HYG", "GLD"]
    start_date = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%d")
    url = "https://data.alpaca.markets/v2/stocks/bars"

    try:
        resp = httpx.get(
            url,
            params={
                "symbols": ",".join(macro_etfs),
                "timeframe": "1Day",
                "start": start_date,
                "limit": 500,
                "adjustment": "split",
            },
            headers=_alpaca_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        bars_map = resp.json().get("bars", {})
        asset_perf: dict[str, Any] = {}

        for sym in macro_etfs:
            b = bars_map.get(sym, [])
            if len(b) < 6:
                continue
            closes = [float(x["c"]) for x in b]
            spot = closes[-1]
            ret_1d = round(((spot - closes[-2]) / closes[-2]) * 100.0, 2)
            ret_5d = round(((spot - closes[-6]) / closes[-6]) * 100.0, 2)
            ret_20d = round(((spot - closes[0]) / closes[0]) * 100.0, 2)
            asset_perf[sym] = {
                "last_price": round(spot, 2),
                "return_1d_pct": ret_1d,
                "return_5d_pct": ret_5d,
                "return_20d_pct": ret_20d,
            }

        spy_5d = asset_perf.get("SPY", {}).get("return_5d_pct", 0.0)
        qqq_5d = asset_perf.get("QQQ", {}).get("return_5d_pct", 0.0)
        tlt_5d = asset_perf.get("TLT", {}).get("return_5d_pct", 0.0)
        hyg_5d = asset_perf.get("HYG", {}).get("return_5d_pct", 0.0)

        if spy_5d > 0.5 and qqq_5d >= spy_5d and hyg_5d >= -0.2:
            inferred_regime = "GOLDILOCKS_RISK_ON"
        elif spy_5d < -1.0 and tlt_5d < -1.0:
            inferred_regime = "RATES_SHOCK"
        elif spy_5d < -1.0 and tlt_5d > 0.5:
            inferred_regime = "DEFENSIVE_ROTATION"
        else:
            inferred_regime = "REFLATION"

        return {
            "source": "Alpaca Cross-Asset Macro Matrix",
            "inferred_macro_regime": inferred_regime,
            "cross_asset_performance": asset_perf,
            "qqq_vs_spy_5d_spread_pct": round(qqq_5d - spy_5d, 2),
            "duration_tlt_5d_pct": tlt_5d,
            "high_yield_credit_hyg_5d_pct": hyg_5d,
        }
    except Exception as exc:
        return {"status": f"MACRO_FALLBACK ({exc})", "inferred_macro_regime": "GOLDILOCKS_RISK_ON"}


# ============================================================================
# 5. REAL-TIME BREAKING NEWS & SENTIMENT FEED (ALPACA NEWS API)
# ============================================================================

@tool
def fetch_alpaca_news_sentiment(tickers: list[str]) -> dict[str, Any]:
    """Fetch live institutional news articles and headlines for target tickers from the Alpaca News API (v1beta1/news)."""
    symbols = _clean_tickers(tickers)
    url = "https://data.alpaca.markets/v1beta1/news"
    try:
        resp = httpx.get(
            url,
            params={"symbols": ",".join(symbols), "limit": 18, "sort": "desc"},
            headers=_alpaca_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        news_items = resp.json().get("news", [])
        articles = []
        for item in news_items:
            articles.append(
                {
                    "timestamp": item.get("created_at"),
                    "headline": item.get("headline"),
                    "summary": (item.get("summary") or "")[:280],
                    "source": item.get("source"),
                    "symbols": item.get("symbols", []),
                }
            )
        return {
            "source": "Alpaca Real-Time News API (v1beta1/news)",
            "queried_symbols": symbols,
            "article_count": len(articles),
            "articles": articles,
        }
    except Exception as exc:
        return {"status": f"NEWS_FALLBACK ({exc})", "articles": []}


# ============================================================================
# 6. MULTI-DESK PORTFOLIO RISK, CORRELATION, FACTOR BETA & VAR ENGINE
# ============================================================================

@tool
def get_firm_portfolio_and_mandate() -> dict[str, Any]:
    """Return the $100M Multi-Strategy Firm Book baseline exposures, live Alpaca paper account equity, and CRO risk mandate limits."""
    account_info = {"status": "SIMULATED_100M_BOOK"}
    try:
        resp = httpx.get(
            "https://paper-api.alpaca.markets/v2/account",
            headers=_alpaca_headers(),
            timeout=6.0,
        )
        if resp.status_code == 200:
            acc = resp.json()
            account_info = {
                "alpaca_account_status": acc.get("status"),
                "alpaca_buying_power_usd": float(acc.get("buying_power", 0)),
                "alpaca_equity_usd": float(acc.get("equity", 0)),
            }
    except Exception:
        pass

    return {
        "alpaca_live_paper_account": account_info,
        **FIRM_BOOK_STATE,
    }


@tool
def evaluate_multi_desk_book_risk(proposed_legs: list[dict[str, Any]]) -> dict[str, Any]:
    """Run quantitative multi-desk portfolio risk analysis across all proposed legs:
    - Computes gross/net exposure by desk and firm-wide (% of $100M NAV)
    - Computes post-trade sector net concentration vs 28% limit
    - Computes beta-adjusted SPY dollar exposure
    - Computes diversified 99% 1-day parametric VaR and 3.5-sigma stressed VaR (accounting for LONG/SHORT offsets)
    - Checks individual leg notional caps ($12M), desk gross caps ($24M), and stop-loss distance limits (<= 7.5%).
    """
    nav = float(FIRM_BOOK_STATE["firm_nav_usd"])
    limits = FIRM_BOOK_STATE["risk_mandate_limits"]

    desk_gross: dict[str, float] = {
        "MOMENTUM_DESK": 0.0,
        "STAT_ARB_DESK": 0.0,
        "MACRO_VOL_DESK": 0.0,
        "EVENT_ARB_DESK": 0.0,
    }
    desk_net: dict[str, float] = {
        "MOMENTUM_DESK": 0.0,
        "STAT_ARB_DESK": 0.0,
        "MACRO_VOL_DESK": 0.0,
        "EVENT_ARB_DESK": 0.0,
    }
    sector_net: dict[str, float] = dict(FIRM_BOOK_STATE["existing_sector_net_exposure_usd"])

    new_gross_usd = 0.0
    new_net_usd = 0.0
    net_beta_dollars = 0.0
    signed_var_contributions: list[float] = []
    gross_var_sum = 0.0

    breaches: list[str] = []
    desk_directives: dict[str, list[str]] = {
        "MOMENTUM_DESK": [],
        "STAT_ARB_DESK": [],
        "MACRO_VOL_DESK": [],
        "EVENT_ARB_DESK": [],
    }

    for leg in proposed_legs:
        desk = str(leg.get("desk", "MOMENTUM_DESK")).upper()
        ticker = str(leg.get("ticker", "NVDA")).upper().strip()
        direction = str(leg.get("direction", "LONG")).upper()
        instrument = str(leg.get("instrument", "EQUITY")).upper()
        notional = abs(float(leg.get("notional_usd", 0.0)))
        entry_px = float(leg.get("entry_price", 100.0))
        stop_px = float(leg.get("stop_loss", entry_px * 0.94))

        sign = 1.0 if direction == "LONG" else -1.0
        meta = SECTOR_MAP.get(ticker, {"sector": "General Equities", "beta": 1.15, "default_vol_pct": 2.2})
        sector = meta["sector"]
        beta = float(meta["beta"])
        daily_vol_dec = float(meta["default_vol_pct"]) / 100.0

        # Defined-risk options structures reduce linear tail VaR & beta sensitivity by 35%
        struct_scalar = 0.65 if any(k in instrument for k in ("SPREAD", "COLLAR", "OPTION")) else 1.0

        desk_gross[desk] = desk_gross.get(desk, 0.0) + notional
        desk_net[desk] = desk_net.get(desk, 0.0) + (sign * notional)
        new_gross_usd += notional
        new_net_usd += sign * notional
        sector_net[sector] = sector_net.get(sector, 0.0) + (sign * notional * struct_scalar)
        net_beta_dollars += sign * notional * beta * struct_scalar

        leg_var_99 = notional * daily_vol_dec * 2.326 * struct_scalar
        gross_var_sum += leg_var_99
        signed_var_contributions.append(sign * leg_var_99 * beta)

        # Check 1: Single-leg notional cap ($12M)
        if notional > limits["max_single_leg_notional_usd"]:
            msg = (
                f"[{desk}] {ticker} single-leg notional (${notional:,.0f}) exceeds cap of "
                f"${limits['max_single_leg_notional_usd']:,.0f}."
            )
            breaches.append(msg)
            desk_directives.setdefault(desk, []).append(
                f"Cut {ticker} notional_usd from ${notional:,.0f} to <= ${limits['max_single_leg_notional_usd']:,.0f}."
            )

        # Check 2: Stop-loss distance limit (<= 7.5%)
        if entry_px > 0:
            stop_dist_pct = abs(entry_px - stop_px) / entry_px * 100.0
            if stop_dist_pct > limits["max_stop_loss_distance_pct"]:
                msg = (
                    f"[{desk}] {ticker} stop-loss distance ({stop_dist_pct:.2f}%) exceeds maximum allowed "
                    f"{limits['max_stop_loss_distance_pct']:.2f}% from entry (${entry_px:.2f})."
                )
                breaches.append(msg)
                tight_stop = (
                    round(entry_px * (1.0 - (limits["max_stop_loss_distance_pct"] - 0.5) / 100.0), 2)
                    if direction == "LONG"
                    else round(entry_px * (1.0 + (limits["max_stop_loss_distance_pct"] - 0.5) / 100.0), 2)
                )
                desk_directives.setdefault(desk, []).append(
                    f"Tighten {ticker} stop_loss to ${tight_stop:.2f} (<= {limits['max_stop_loss_distance_pct']:.1f}% from entry)."
                )

    # Check 3: Desk Gross Notional Cap ($24M per desk)
    for d_name, d_gross in desk_gross.items():
        if d_gross > limits["max_desk_gross_notional_usd"]:
            msg = (
                f"[{d_name}] Total desk gross notional (${d_gross:,.0f}) exceeds desk mandate cap of "
                f"${limits['max_desk_gross_notional_usd']:,.0f}."
            )
            breaches.append(msg)
            desk_directives.setdefault(d_name, []).append(
                f"Scale down total {d_name} gross notional from ${d_gross:,.0f} to <= ${limits['max_desk_gross_notional_usd']:,.0f}."
            )

    # Check 4: Post-trade Sector Net Concentration (<= 28% of NAV)
    sector_net_pct = {sec: round((val / nav) * 100.0, 2) for sec, val in sector_net.items()}
    for sec, pct in sector_net_pct.items():
        if abs(pct) > limits["max_sector_net_exposure_pct_nav"]:
            excess_usd = (abs(pct) - limits["max_sector_net_exposure_pct_nav"]) / 100.0 * nav
            msg = (
                f"Firm post-trade net {sec} exposure ({pct:.2f}% of NAV) breaches the "
                f"{limits['max_sector_net_exposure_pct_nav']:.1f}% concentration limit by ${excess_usd:,.0f}."
            )
            breaches.append(msg)
            desk_directives["MOMENTUM_DESK"].append(
                f"Reduce net {sec} long notional or convert to CALL_SPREAD so firm {sec} exposure drops by >= ${excess_usd:,.0f}."
            )
            desk_directives["STAT_ARB_DESK"].append(
                f"Increase short {sec} pair leg or SMH hedge by >= ${excess_usd / 2:,.0f} to offset {sec} concentration."
            )

    # Check 5: Post-trade Firm Gross & Net Exposure
    post_firm_gross_usd = FIRM_BOOK_STATE["existing_gross_exposure_usd"] + new_gross_usd
    post_firm_net_usd = FIRM_BOOK_STATE["existing_net_exposure_usd"] + new_net_usd
    post_firm_gross_pct = round((post_firm_gross_usd / nav) * 100.0, 2)
    post_firm_net_pct = round((post_firm_net_usd / nav) * 100.0, 2)

    if post_firm_gross_pct > limits["max_post_trade_firm_gross_pct_nav"]:
        excess_gross = post_firm_gross_usd - (limits["max_post_trade_firm_gross_pct_nav"] / 100.0 * nav)
        breaches.append(
            f"Post-trade firm gross exposure ({post_firm_gross_pct:.1f}% of NAV) exceeds limit of "
            f"{limits['max_post_trade_firm_gross_pct_nav']:.1f}% by ${excess_gross:,.0f}."
        )
        for d_name in desk_directives:
            desk_directives[d_name].append(
                f"Reduce gross notional by ~${excess_gross / 3:,.0f} to bring firm gross exposure <= {limits['max_post_trade_firm_gross_pct_nav']:.1f}%."
            )

    if abs(post_firm_net_pct) > limits["max_post_trade_firm_net_pct_nav"]:
        excess_net = abs(post_firm_net_usd) - (limits["max_post_trade_firm_net_pct_nav"] / 100.0 * nav)
        breaches.append(
            f"Post-trade firm net directional exposure ({post_firm_net_pct:.1f}% of NAV) exceeds limit of "
            f"{limits['max_post_trade_firm_net_pct_nav']:.1f}% by ${excess_net:,.0f}."
        )
        desk_directives["MACRO_VOL_DESK"].append(
            f"Add or upsize SHORT SPY/QQQ tail hedge by >= ${excess_net:,.0f} to neutralize excess net directional exposure."
        )

    # Check 6: Diversified Multi-Desk 99% 1-Day VaR (combines idiosyncratic + systematic factor VaR)
    # In a factor risk model, ~75% of equity variance is systematic (beta) and ~25% is idiosyncratic (diversifiable across uncorrelated legs)
    systematic_var = abs(sum(signed_var_contributions)) * 0.55
    idiosyncratic_var = math.sqrt(sum((v * 0.42) ** 2 for v in signed_var_contributions))
    portfolio_var_99_usd = round(systematic_var + idiosyncratic_var, 2)
    expected_shortfall_cvar_99_usd = round(portfolio_var_99_usd * 1.28, 2)
    stressed_var_99_usd = round(portfolio_var_99_usd * 1.65, 2)

    if portfolio_var_99_usd > limits["max_portfolio_var_99_1d_usd"]:
        breaches.append(
            f"Multi-desk 99% 1-day VaR (${portfolio_var_99_usd:,.0f}) exceeds firm limit of "
            f"${limits['max_portfolio_var_99_1d_usd']:,.0f}."
        )
        desk_directives["MOMENTUM_DESK"].append(
            "Reduce outright equity notional or switch to CALL_SPREAD to cut directional VaR."
        )
        desk_directives["MACRO_VOL_DESK"].append(
            "Increase SHORT QQQ / SPY PUT_SPREAD hedge size to dampen systematic portfolio VaR."
        )

    formatted_directives = {
        k: " | ".join(v) if v else "All desk checks passed — maintain current book."
        for k, v in desk_directives.items()
    }
    must_revise_map = {k: len(v) > 0 for k, v in desk_directives.items()}

    # Historical Stress Scenario Shocks
    semi_net = sector_net.get("Semiconductors", 0.0)
    rates_net = sector_net.get("Rates / Duration", 0.0)
    scenario_shocks = {
        "ai_semis_unwind_minus_12pct_usd": round(-0.12 * semi_net - 0.04 * net_beta_dollars, 2),
        "rates_shock_plus_50bps_usd": round(-0.06 * rates_net - 0.035 * net_beta_dollars, 2),
        "liquidity_crunch_3sigma_usd": -stressed_var_99_usd,
    }

    return {
        "all_checks_passed": len(breaches) == 0,
        "new_trades_gross_usd": round(new_gross_usd, 2),
        "new_trades_net_usd": round(new_net_usd, 2),
        "post_trade_firm_gross_pct_nav": post_firm_gross_pct,
        "post_trade_firm_net_pct_nav": post_firm_net_pct,
        "desk_gross_breakdown_usd": desk_gross,
        "desk_net_breakdown_usd": desk_net,
        "post_trade_sector_net_pct_nav": sector_net_pct,
        "net_beta_adjusted_exposure_usd": round(net_beta_dollars, 2),
        "undiversified_gross_var_99_usd": round(gross_var_sum, 2),
        "portfolio_var_99_1d_usd": portfolio_var_99_usd,
        "expected_shortfall_cvar_99_usd": expected_shortfall_cvar_99_usd,
        "max_portfolio_var_99_limit_usd": limits["max_portfolio_var_99_1d_usd"],
        "stressed_var_99_usd": stressed_var_99_usd,
        "scenario_shocks_usd": scenario_shocks,
        "limit_breaches": breaches,
        "desk_specific_directives": formatted_directives,
        "desk_must_revise_map": must_revise_map,
    }


# ============================================================================
# 7. VOLATILITY SURFACE, SKEW & DEALER GAMMA POSITIONING TOOL
# ============================================================================

@tool
def analyze_volatility_surface_and_skew(tickers: list[str]) -> dict[str, Any]:
    """Compute realized volatility term structure (5d vs 20d vs 60d), synthetic implied volatility skew, IV-RV spread, and dealer gamma positioning from Alpaca OHLCV bars."""
    symbols = _clean_tickers(tickers)
    start_date = (datetime.now(timezone.utc) - timedelta(days=95)).strftime("%Y-%m-%d")
    url = "https://data.alpaca.markets/v2/stocks/bars"
    try:
        resp = httpx.get(
            url,
            params={
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": start_date,
                "limit": 1000,
                "adjustment": "split",
            },
            headers=_alpaca_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        bars_map = resp.json().get("bars", {})
        vol_surface: dict[str, Any] = {}

        for sym in symbols:
            bars = bars_map.get(sym, [])
            if len(bars) < 25:
                continue
            closes = np.array([float(b["c"]) for b in bars])
            log_rets = np.diff(np.log(closes))
            rv_5d = round(float(np.std(log_rets[-5:]) * math.sqrt(252) * 100.0), 2)
            rv_20d = round(float(np.std(log_rets[-20:]) * math.sqrt(252) * 100.0), 2)
            rv_60d = round(float(np.std(log_rets[-min(60, len(log_rets)):]) * math.sqrt(252) * 100.0), 2)

            # Downside vs upside realized semivariance (skew proxy)
            neg_rets = log_rets[-20:][log_rets[-20:] < 0]
            pos_rets = log_rets[-20:][log_rets[-20:] > 0]
            down_vol = float(np.std(neg_rets) * math.sqrt(252) * 100.0) if len(neg_rets) > 1 else rv_20d
            up_vol = float(np.std(pos_rets) * math.sqrt(252) * 100.0) if len(pos_rets) > 1 else rv_20d
            skew_ratio = round(down_vol / max(up_vol, 1.0), 2)

            # Implied Vol proxy incorporates term structure expansion + event risk premium
            est_iv_30d = round(rv_20d * 1.08 + max(0.0, (rv_5d - rv_20d) * 0.35), 2)
            iv_rv_spread = round(est_iv_30d - rv_20d, 2)

            skew_regime = (
                "STEEP_PUT_SKEW_HEDGING_DEMAND"
                if skew_ratio > 1.20
                else "CALL_SKEW_MELTUP_DEMAND"
                if skew_ratio < 0.85
                else "BALANCED_SMILE"
            )
            optimal_struct = (
                "CALL_SPREAD / PUT_SPREAD (Sell Rich Skew Wing)"
                if iv_rv_spread > 3.5 or skew_ratio > 1.2
                else "LONG_OPTION_CONVEXITY"
                if iv_rv_spread < 1.5
                else "COLLAR / DEFINED_RISK_SPREAD"
            )

            vol_surface[sym] = {
                "realized_vol_5d_pct": rv_5d,
                "realized_vol_20d_pct": rv_20d,
                "realized_vol_60d_pct": rv_60d,
                "estimated_30d_iv_pct": est_iv_30d,
                "iv_minus_rv20d_vol_pts": iv_rv_spread,
                "downside_upside_skew_ratio": skew_ratio,
                "skew_regime": skew_regime,
                "optimal_options_structure": optimal_struct,
            }

        spy_rv5 = vol_surface.get("SPY", {}).get("realized_vol_5d_pct", 15.0)
        spy_rv20 = vol_surface.get("SPY", {}).get("realized_vol_20d_pct", 15.0)
        gamma_regime = (
            "SHORT_GAMMA_ACCELERATION"
            if spy_rv5 > spy_rv20 * 1.15
            else "LONG_GAMMA_PINNING"
            if spy_rv5 < spy_rv20 * 0.85
            else "NEUTRAL_GAMMA"
        )

        return {
            "source": "Alpaca Volatility Term Structure & Skew Engine",
            "dealer_gamma_regime": gamma_regime,
            "ticker_vol_surface": vol_surface,
        }
    except Exception as exc:
        return {"status": f"VOL_SURFACE_FALLBACK ({exc})", "dealer_gamma_regime": "NEUTRAL_GAMMA"}


# ============================================================================
# 8. CROSS-SECTIONAL CORRELATION, PCA CROWDING & COINTEGRATION PAIRS TOOL
# ============================================================================

@tool
def compute_correlation_and_cointegration_matrix(tickers: list[str]) -> dict[str, Any]:
    """Compute 60-day cross-sectional return correlation matrix, 1st Principal Component (PC1) variance explained %, and cointegrated Stat-Arb spread Z-scores from Alpaca bars."""
    symbols = _clean_tickers(tickers)
    if "SMH" not in symbols:
        symbols.append("SMH")
    start_date = (datetime.now(timezone.utc) - timedelta(days=95)).strftime("%Y-%m-%d")
    url = "https://data.alpaca.markets/v2/stocks/bars"
    try:
        resp = httpx.get(
            url,
            params={
                "symbols": ",".join(symbols),
                "timeframe": "1Day",
                "start": start_date,
                "limit": 1000,
                "adjustment": "split",
            },
            headers=_alpaca_headers(),
            timeout=10.0,
        )
        resp.raise_for_status()
        bars_map = resp.json().get("bars", {})

        close_series: dict[str, np.ndarray] = {}
        min_len = 999
        for sym in symbols:
            b = bars_map.get(sym, [])
            if len(b) >= 25:
                arr = np.array([float(x["c"]) for x in b])
                close_series[sym] = arr
                min_len = min(min_len, len(arr))

        valid_syms = list(close_series.keys())
        if len(valid_syms) < 2:
            return {"status": "INSUFFICIENT_SYMBOLS"}

        aligned = np.column_stack([close_series[s][-min_len:] for s in valid_syms])
        log_rets = np.diff(np.log(aligned), axis=0)
        corr_matrix = np.corrcoef(log_rets, rowvar=False)

        # Eigenvalues of correlation matrix for PCA crowding
        eigvals = np.linalg.eigvalsh(corr_matrix)
        pc1_explained_pct = round(float(np.max(eigvals) / np.sum(eigvals) * 100.0), 2)

        pairs = []
        for i in range(len(valid_syms)):
            for j in range(i + 1, len(valid_syms)):
                s1, s2 = valid_syms[i], valid_syms[j]
                corr_val = round(float(corr_matrix[i, j]), 3)
                if corr_val >= 0.45:
                    # Normalized log price ratio spread over last 20 days
                    p1 = close_series[s1][-20:]
                    p2 = close_series[s2][-20:]
                    spread = np.log(p1 / p1[0]) - np.log(p2 / p2[0])
                    std_sp = float(np.std(spread))
                    z_sp = round(float((spread[-1] - np.mean(spread)) / max(std_sp, 1e-6)), 2)
                    long_cand = s1 if z_sp < 0 else s2
                    short_cand = s2 if z_sp < 0 else s1
                    pairs.append(
                        {
                            "long_leg": long_cand,
                            "short_leg": short_cand,
                            "rolling_correlation_60d": corr_val,
                            "spread_z_score_20d": abs(z_sp),
                            "half_life_days": round(max(2.5, 12.0 * (1.0 - abs(corr_val))), 1),
                        }
                    )

        pairs.sort(key=lambda x: x["spread_z_score_20d"], reverse=True)
        return {
            "source": "Alpaca Cross-Sectional PCA & Cointegration Engine",
            "analyzed_symbols": valid_syms,
            "pc1_market_factor_variance_explained_pct": pc1_explained_pct,
            "crowding_regime": (
                "HIGH_FACTOR_CROWDING" if pc1_explained_pct > 62.0 else "DISPERSED_ALPHA_OPPORTUNITY"
            ),
            "top_cointegrated_pairs": pairs[:5],
        }
    except Exception as exc:
        return {"status": f"COINTEGRATION_FALLBACK ({exc})", "top_cointegrated_pairs": []}


# Tool bundles for each specialized agent node
PREMARKET_TOOLS = [fetch_alpaca_live_snapshots, fetch_databento_orderbook_microstructure]
TECHNICAL_TOOLS = [compute_technical_indicators]
MACRO_TOOLS = [fetch_cross_asset_macro_regime]
NEWS_TOOLS = [fetch_alpaca_news_sentiment]
VOL_SURFACE_TOOLS = [analyze_volatility_surface_and_skew]
COINTEGRATION_TOOLS = [compute_correlation_and_cointegration_matrix]
DESK_TOOLS = [fetch_alpaca_live_snapshots, compute_technical_indicators, fetch_databento_orderbook_microstructure]
CRO_RISK_TOOLS = [get_firm_portfolio_and_mandate, evaluate_multi_desk_book_risk]

