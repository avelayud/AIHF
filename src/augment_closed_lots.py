"""
augment_closed_lots.py — Feature pipeline for closed-lot trades.

Adds:
- underlying_entry_close / underlying_exit_close
- underlying_move_pct
- moneyness_pct_at_entry
- implied_vol_entry_pct / implied_vol_exit_pct (approx via Black-Scholes)
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf
from scipy.optimize import brentq
from scipy.stats import norm

CACHE_PATH = Path("output/price_cache.parquet")
R = 0.045


def _bs_price(S, K, T, r, sigma, opt_type):
    if T <= 0 or sigma <= 0:
        return max(0.0, (K - S) if opt_type == "PUT" else (S - K))
    d1 = (np.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * np.sqrt(T))
    d2 = d1 - sigma * np.sqrt(T)
    if opt_type == "PUT":
        return K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    return S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2)


def _implied_vol(premium_per_share, S, K, T, opt_type):
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in [premium_per_share, S, K, T]):
        return None
    if premium_per_share <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    try:
        iv = brentq(lambda sig: _bs_price(S, K, T, R, sig, opt_type) - premium_per_share, 1e-6, 5.0)
        return round(iv * 100, 2)
    except Exception:
        return None


def _load_cache():
    if CACHE_PATH.exists():
        return pd.read_parquet(CACHE_PATH)
    return pd.DataFrame(columns=["ticker", "date", "close"])


def _save_cache(df):
    CACHE_PATH.parent.mkdir(exist_ok=True)
    df.to_parquet(CACHE_PATH, index=False)


def _fetch_prices(tickers, start, end):
    if not tickers:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    raw = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)
    if raw.empty:
        return pd.DataFrame(columns=["ticker", "date", "close"])
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]
    if not isinstance(raw.columns, pd.MultiIndex):
        close.columns = tickers
    out = close.reset_index().melt(id_vars="Date", var_name="ticker", value_name="close")
    out = out.rename(columns={"Date": "date"})
    out["date"] = pd.to_datetime(out["date"]).dt.normalize()
    return out.dropna(subset=["close"])[["ticker", "date", "close"]]


def _nearest(px, ticker, dt):
    if pd.isna(dt) or not ticker:
        return None
    d = pd.Timestamp(dt).normalize()
    s = px[(px["ticker"] == ticker) & (px["date"] <= d)].sort_values("date")
    return float(s.iloc[-1]["close"]) if not s.empty else None


def augment_trades(trades: pd.DataFrame, save_csv: str | None = None, verbose: bool = True) -> pd.DataFrame:
    df = trades.copy()
    rows = df[df["ticker"].notna() & df["entry_date"].notna() & df["close_date"].notna()].copy()
    if rows.empty:
        return df

    tickers = sorted(rows["ticker"].dropna().unique().tolist())
    start = (rows["entry_date"].min() - timedelta(days=10)).strftime("%Y-%m-%d")
    end = (rows["close_date"].max() + timedelta(days=3)).strftime("%Y-%m-%d")

    cache = _load_cache()
    cached_t = set(cache["ticker"].unique()) if not cache.empty else set()
    miss = [t for t in tickers if t not in cached_t]
    if miss:
        new = _fetch_prices(miss, start, end)
        if not new.empty:
            cache = pd.concat([cache, new], ignore_index=True).drop_duplicates(["ticker", "date"])
            _save_cache(cache)

    if verbose:
        print(f"Augmenting {len(rows)} trades across {len(tickers)} tickers")

    rec = []
    for idx, r in rows.iterrows():
        pe = _nearest(cache, r["ticker"], r["entry_date"])
        px = _nearest(cache, r["ticker"], r["close_date"])
        move = ((px - pe) / pe * 100) if pe and px else None
        mny = None
        if pe and pd.notna(r.get("strike")) and r.get("opt_type") in ["PUT", "CALL"]:
            mny = ((pe - r["strike"]) / pe * 100) if r["opt_type"] == "PUT" else ((r["strike"] - pe) / pe * 100)

        iv_entry = iv_exit = None
        if r.get("opt_type") in ["PUT", "CALL"] and pd.notna(r.get("strike")):
            qty = float(r.get("contracts") or 1)
            T1 = max(((pd.Timestamp(r.get("expiry_date")) - pd.Timestamp(r["entry_date"])).days / 365.0), 1/365) if pd.notna(r.get("expiry_date")) else None
            T2 = max(((pd.Timestamp(r.get("expiry_date")) - pd.Timestamp(r["close_date"])).days / 365.0), 1/365) if pd.notna(r.get("expiry_date")) else None
            prem_open = (float(r.get("cost_to_close") or 0) / (qty * 100)) if qty else None
            prem_close = (float(r.get("premium_collected") or 0) / (qty * 100)) if qty else None
            iv_entry = _implied_vol(prem_open, pe, float(r["strike"]), T1, r["opt_type"]) if T1 else None
            iv_exit = _implied_vol(prem_close, px, float(r["strike"]), T2, r["opt_type"]) if T2 else None

        rec.append((idx, pe, px, move, mny, iv_entry, iv_exit))

    out = pd.DataFrame(rec, columns=["idx", "underlying_entry_close", "underlying_exit_close", "underlying_move_pct", "moneyness_pct_at_entry", "implied_vol_entry_pct", "implied_vol_exit_pct"]).set_index("idx")
    for c in out.columns:
        df[c] = None
        df.loc[out.index, c] = out[c]

    if save_csv:
        Path(save_csv).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(save_csv, index=False)
        if verbose:
            print(f"Saved augmented trades: {save_csv}")
    return df
