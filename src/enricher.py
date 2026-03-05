"""
enricher.py — Enrich closed trades with historical price context + implied volatility

Adds per trade:
  underlying_price_entry    closing price of underlying on entry_date
  underlying_price_close    closing price on close_date (or expiry_date if expired)
  otm_pct                   how far OTM at entry: positive = OTM (good for seller)
  underlying_move_pct       % move entry → close
  move_in_our_favor         bool: put → stock went up; call → stock went down
  pre_entry_move_1d/3d/5d   underlying % move in days before entry
  had_big_move_before       bool: ±3%+ move in 5 days before entry
  big_move_direction        'down' | 'up' | None
  big_move_magnitude        largest single-day abs move in 5 days before entry
  implied_vol               back-calculated IV from option premium via Black-Scholes (%)
  iv_rank_approx            0-100: where this IV sits in trailing 52-week range
  iv_regime                 'low' | 'normal' | 'elevated' | 'high'

Caches price data in output/price_cache.parquet.
"""

import numpy as np
import pandas as pd
import yfinance as yf
from pathlib import Path
from datetime import timedelta
from scipy.stats import norm
from scipy.optimize import brentq

CACHE_PATH       = Path("output/price_cache.parquet")
BIG_MOVE_THRESH  = 3.0
RISK_FREE_RATE   = 0.045  # approx 3-month T-bill


# ── Black-Scholes IV ───────────────────────────────────────────────────────────

def _bs_price(S, K, T, r, sigma, opt_type="PUT"):
    if T <= 0 or sigma <= 0:
        return max(0.0, (K - S) if opt_type == "PUT" else (S - K))
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if opt_type == "PUT":
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)


def _implied_vol(premium_per_share, S, K, T, r, opt_type="PUT"):
    """Back-calculate IV from observed premium. Returns decimal or None."""
    if any(x is None or (isinstance(x, float) and np.isnan(x))
           for x in [premium_per_share, S, K, T]):
        return None
    if T <= 0 or premium_per_share <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(0.0, (K - S) if opt_type == "PUT" else (S - K))
    if premium_per_share <= intrinsic:
        return None
    try:
        iv = brentq(
            lambda sig: _bs_price(S, K, T, r, sig, opt_type) - premium_per_share,
            1e-6, 20.0, xtol=1e-6, maxiter=100,
        )
        return round(iv, 4) if 0.01 < iv < 10.0 else None
    except (ValueError, RuntimeError):
        return None


def _iv_rank(iv_today, iv_series):
    if iv_today is None or len(iv_series) < 10:
        return None
    lo, hi = np.nanmin(iv_series), np.nanmax(iv_series)
    if hi <= lo:
        return 50.0
    return round(float(np.clip((iv_today - lo) / (hi - lo) * 100, 0, 100)), 1)


def _iv_regime(iv_rank):
    if iv_rank is None: return None
    if iv_rank < 25:    return "low"
    if iv_rank < 50:    return "normal"
    if iv_rank < 75:    return "elevated"
    return "high"


def _trailing_rvols(pt, ticker, before_date, iv_cache, n_days=252):
    """
    Approximate 52-week IV series via 20-day rolling realized vol.
    Cached per (ticker, date) to avoid recomputation.
    """
    key = (ticker, str(pd.Timestamp(before_date).date()))
    if key in iv_cache:
        return iv_cache[key]
    target = pd.Timestamp(before_date).normalize()
    sub = pt[(pt["ticker"] == ticker) & (pt["date"] < target)].sort_values("date")
    closes = sub.tail(n_days + 25)["close"].values
    if len(closes) < 25:
        iv_cache[key] = np.array([])
        return np.array([])
    rvols = []
    for i in range(20, len(closes)):
        lr = np.diff(np.log(closes[i-20:i]))
        rvols.append(np.std(lr) * np.sqrt(252))
    result = np.array(rvols)
    iv_cache[key] = result
    return result


# ── Price Cache ────────────────────────────────────────────────────────────────

def _load_cache():
    if CACHE_PATH.exists():
        return pd.read_parquet(CACHE_PATH)
    return pd.DataFrame(columns=["ticker", "date", "close"])


def _save_cache(cache):
    CACHE_PATH.parent.mkdir(exist_ok=True)
    cache.to_parquet(CACHE_PATH, index=False)


def _fetch_prices(tickers, start, end):
    if not tickers:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    print(f"  Fetching: {tickers}")
    raw = yf.download(tickers, start=start, end=end,
                      auto_adjust=True, progress=False)
    if raw.empty:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if not isinstance(raw.columns, pd.MultiIndex):
        close.columns = tickers
    df = close.reset_index().melt(id_vars="Date", var_name="ticker", value_name="close")
    df = df.rename(columns={"Date": "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.normalize()
    return df.dropna(subset=["close"])[["ticker", "date", "close"]]


def _get_prices(trades):
    cache = _load_cache()
    tickers = trades["ticker"].dropna().unique().tolist()
    all_dates = pd.concat([trades["entry_date"].dropna(),
                           trades["close_date"].dropna(),
                           trades["expiry_date"].dropna()])
    # Extra 400 days for 52-week IV rank history
    global_start = (all_dates.min() - timedelta(days=400)).strftime("%Y-%m-%d")
    global_end   = (all_dates.max() + timedelta(days=2)).strftime("%Y-%m-%d")

    if not cache.empty:
        cached = set(cache[
            (cache["date"] >= global_start) & (cache["date"] <= global_end)
        ]["ticker"].unique())
        missing = [t for t in tickers if t not in cached]
    else:
        missing = tickers

    if missing:
        new = _fetch_prices(missing, global_start, global_end)
        if not new.empty:
            cache = pd.concat([cache, new], ignore_index=True)
            cache = cache.drop_duplicates(subset=["ticker", "date"])
            _save_cache(cache)
            print(f"  Cache updated: {len(cache)} rows")
    else:
        print(f"  All prices from cache ({len(cache)} rows)")
    return cache


def _nearest_price(pt, ticker, date):
    if pd.isna(date) or not ticker: return None
    target = pd.Timestamp(date).normalize()
    sub = pt[(pt["ticker"] == ticker) & (pt["date"] <= target)].sort_values("date")
    return float(sub.iloc[-1]["close"]) if not sub.empty else None


def _prior_closes(pt, ticker, before_date, n):
    target = pd.Timestamp(before_date).normalize()
    sub = pt[(pt["ticker"] == ticker) & (pt["date"] < target)].sort_values("date")
    return sub.tail(n)["close"].values


# ── Per-Row Enrichment ─────────────────────────────────────────────────────────

def _enrich_row(row, pt, iv_cache):
    ticker   = row["ticker"]
    opt_type = row["opt_type"]
    strike   = row["strike"]
    entry_dt = row["entry_date"]
    close_dt = row["close_date"] if pd.notna(row.get("close_date")) else row.get("expiry_date")
    dte      = row.get("dte")

    price_entry = _nearest_price(pt, ticker, entry_dt)
    price_close = _nearest_price(pt, ticker, close_dt)

    # OTM%
    otm_pct = None
    if price_entry and strike:
        otm_pct = ((price_entry - strike) / price_entry * 100 if opt_type == "PUT"
                   else (strike - price_entry) / price_entry * 100)

    # Underlying move
    move_pct, favor = None, None
    if price_entry and price_close:
        move_pct = (price_close - price_entry) / price_entry * 100
        favor = (move_pct >= 0) if opt_type == "PUT" else (move_pct <= 0)

    # Pre-entry moves
    prior = _prior_closes(pt, ticker, entry_dt, 7)
    pre_1d = (prior[-1] - prior[-2]) / prior[-2] * 100 if len(prior) >= 2 else None
    pre_3d = (prior[-1] - prior[-4]) / prior[-4] * 100 if len(prior) >= 4 else None
    pre_5d = (prior[-1] - prior[-6]) / prior[-6] * 100 if len(prior) >= 6 else None

    # Big move flag
    had_big, big_dir, big_mag = False, None, None
    if len(prior) >= 2:
        dmoves = [(prior[i] - prior[i-1]) / prior[i-1] * 100
                  for i in range(max(1, len(prior)-5), len(prior))]
        mags = [abs(m) for m in dmoves]
        if mags and max(mags) >= BIG_MOVE_THRESH:
            had_big = True
            big_mag = round(max(mags), 2)
            biggest = dmoves[mags.index(max(mags))]
            big_dir = "down" if biggest < 0 else "up"

    # IV back-calculation
    iv = iv_rank_val = iv_regime_val = None
    contracts = float(row.get("contracts") or 1)
    premium   = row.get("premium_collected")

    if premium and price_entry and strike and dte and dte > 0 and opt_type in ("PUT", "CALL"):
        pps = premium / (contracts * 100)  # premium per share
        T   = dte / 365.0
        iv  = _implied_vol(pps, price_entry, strike, T, RISK_FREE_RATE, opt_type)

    if iv is not None:
        hist = _trailing_rvols(pt, ticker, entry_dt, iv_cache)
        if len(hist) >= 10:
            iv_rank_val   = _iv_rank(iv, hist)
            iv_regime_val = _iv_regime(iv_rank_val)

    return pd.Series({
        "underlying_price_entry": round(price_entry, 4) if price_entry else None,
        "underlying_price_close": round(price_close, 4) if price_close else None,
        "otm_pct":                round(otm_pct, 2)    if otm_pct   is not None else None,
        "underlying_move_pct":    round(move_pct, 2)   if move_pct  is not None else None,
        "move_in_our_favor":      favor,
        "pre_entry_move_1d":      round(pre_1d, 2)     if pre_1d    is not None else None,
        "pre_entry_move_3d":      round(pre_3d, 2)     if pre_3d    is not None else None,
        "pre_entry_move_5d":      round(pre_5d, 2)     if pre_5d    is not None else None,
        "had_big_move_before":    had_big,
        "big_move_direction":     big_dir,
        "big_move_magnitude":     big_mag,
        "implied_vol":            round(iv * 100, 1)   if iv        is not None else None,
        "iv_rank_approx":         iv_rank_val,
        "iv_regime":              iv_regime_val,
    })


def enrich(trades, verbose=True):
    """
    Add price context + IV columns to a trades DataFrame.
    Works on any persona — pass trades from analyzer.analyze().
    """
    enrichable = trades[
        trades["ticker"].notna() &
        trades["entry_date"].notna() &
        (trades["ticker"] != "") &
        trades["opt_type"].isin(["PUT", "CALL"])
    ].copy()

    if verbose:
        print(f"\nEnriching {len(enrichable)} trades across "
              f"{enrichable['ticker'].nunique()} tickers...")

    pt       = _get_prices(enrichable)
    iv_cache = {}

    enriched_cols = enrichable.apply(
        lambda row: _enrich_row(row, pt, iv_cache), axis=1
    )

    result = trades.copy()
    for col in enriched_cols.columns:
        result[col] = None
        result.loc[enriched_cols.index, col] = enriched_cols[col]

    if verbose:
        filled  = result["underlying_price_entry"].notna().sum()
        iv_done = result["implied_vol"].notna().sum()
        big_mv  = result["had_big_move_before"].fillna(False).sum()
        print(f"  Prices resolved:     {filled}/{len(enrichable)}")
        print(f"  IV back-calculated:  {iv_done}/{len(enrichable)}")
        print(f"  After ±{BIG_MOVE_THRESH}% move:   {big_mv} ({big_mv/len(enrichable)*100:.1f}%)")

    return result


# ── Summaries ──────────────────────────────────────────────────────────────────

def big_move_analysis(enriched):
    closed = enriched[
        enriched["outcome"].isin(["WIN","LOSS","EXPIRED_WIN"]) &
        enriched["had_big_move_before"].notna()
    ].copy()
    closed["won"] = closed["outcome"].isin(["WIN","EXPIRED_WIN"])
    closed["entry_context"] = closed["had_big_move_before"].map(
        {True: "After big move", False: "Calm entry"})
    return closed.groupby(["persona","mode","entry_context"], dropna=False).agg(
        trades   =("won", "count"),
        win_rate =("won", lambda x: round(x.mean()*100, 1)),
        avg_prem =("premium_collected", lambda x: round(x.mean(), 0)),
        avg_pnl  =("net_pnl", lambda x: round(x.mean(), 0)),
        avg_otm  =("otm_pct", lambda x: round(x.mean(), 1)),
        avg_iv   =("implied_vol", lambda x: round(x.mean(), 1)),
    ).reset_index()


def iv_analysis(enriched):
    closed = enriched[
        enriched["outcome"].isin(["WIN","LOSS","EXPIRED_WIN"]) &
        enriched["iv_regime"].notna()
    ].copy()
    closed["won"] = closed["outcome"].isin(["WIN","EXPIRED_WIN"])
    return closed.groupby(["persona","mode","iv_regime"], dropna=False).agg(
        trades   =("won", "count"),
        win_rate =("won", lambda x: round(x.mean()*100, 1)),
        avg_prem =("premium_collected", lambda x: round(x.mean(), 0)),
        avg_pnl  =("net_pnl", lambda x: round(x.mean(), 0)),
    ).reset_index()


if __name__ == "__main__":
    from src.analyzer import analyze
    trades, _ = analyze("Arjuna")
    enriched  = enrich(trades)
    print("\n=== BIG MOVE ANALYSIS ===")
    print(big_move_analysis(enriched).to_string(index=False))
    print("\n=== IV REGIME ANALYSIS ===")
    print(iv_analysis(enriched).to_string(index=False))
