"""
patterns.py — Discover trading strategies from enriched trade history

Mines enriched trades to find clusters of conditions with statistically
meaningful win rates. Each cluster becomes a named strategy in the registry.

Strategy Registry columns:
  strategy_id, strategy_name, mode, sector, iv_regime, after_big_move,
  dte_bucket, n_trades, win_rate, avg_premium, avg_pnl, avg_otm_pct,
  avg_iv_rank, avg_dte, avg_hold_days, personas, persona_breakdown,
  confidence, conditions
"""

import json
import pandas as pd
import numpy as np
from pathlib import Path

OUTPUT_DIR = Path("output")
MIN_TRADES = 5


def _dte_bucket(dte):
    if pd.isna(dte):  return "unknown"
    if dte <= 7:      return "0-7"
    if dte <= 14:     return "8-14"
    if dte <= 45:     return "15-45"
    if dte <= 90:     return "46-90"
    if dte <= 180:    return "91-180"
    if dte <= 365:    return "181-365"
    return "365+"


def _otm_bucket(otm_pct):
    if pd.isna(otm_pct): return "unknown"
    if otm_pct < 0:      return "ITM"
    if otm_pct < 5:      return "near_0-5pct"
    if otm_pct < 10:     return "mid_5-10pct"
    if otm_pct < 20:     return "far_10-20pct"
    return "deep_20pct+"


def _pre_move_bucket(move_5d):
    if pd.isna(move_5d): return "unknown"
    if move_5d <= -5:    return "big_drop_5pct+"
    if move_5d <= -3:    return "drop_3-5pct"
    if move_5d <= -1:    return "slight_drop"
    if move_5d <= 1:     return "flat"
    if move_5d <= 3:     return "slight_rise"
    return "rising_3pct+"


def build_features(trades: pd.DataFrame) -> pd.DataFrame:
    """
    Prepare closed trades for strategy clustering.
    Works with or without enrichment columns.
    """
    closed = trades[
        trades["outcome"].isin(["WIN", "LOSS", "EXPIRED_WIN", "ASSIGNED"]) &
        trades["mode"].notna() &
        ~trades["mode"].isin(["unknown", None])
    ].copy()

    closed["won"] = closed["outcome"].isin(["WIN", "EXPIRED_WIN"])

    # Always-available features
    closed["dte_bucket"]  = closed["dte"].apply(_dte_bucket)

    # Enrichment-dependent features (graceful fallback)
    if "otm_pct" in closed.columns:
        closed["otm_bucket"] = closed["otm_pct"].apply(_otm_bucket)
    else:
        closed["otm_bucket"] = "unknown"

    if "pre_entry_move_5d" in closed.columns:
        closed["pre_move_cat"] = closed["pre_entry_move_5d"].apply(_pre_move_bucket)
    else:
        closed["pre_move_cat"] = "unknown"

    if "iv_regime" in closed.columns:
        closed["iv_regime_cat"] = closed["iv_regime"].fillna("unknown")
    else:
        closed["iv_regime_cat"] = "unknown"

    if "had_big_move_before" in closed.columns:
        closed["after_big_move"] = closed["had_big_move_before"].fillna(False)
    else:
        closed["after_big_move"] = False

    return closed


def _strategy_name(mode, iv_regime, sector, after_big_move, dte_bucket):
    labels = {"A": "Short-Dated Put", "B": "Long-Dated Put",
              "C": "Mid-Range Put", "CC": "Covered Call"}
    base = labels.get(mode, mode)
    iv   = f"({iv_regime} IV)" if iv_regime not in (None, "unknown") else ""
    drop = "post-drop" if after_big_move else "calm"
    sec  = sector.replace("_", " ").title() if sector not in (None, "other") else ""
    dte  = f"[{dte_bucket} DTE]" if dte_bucket not in (None, "unknown") else ""
    return " — ".join(p for p in [f"{base} {iv}".strip(), sec, f"{drop} {dte}".strip()] if p)


def _confidence(n):
    if n >= 20: return "high"
    if n >= 10: return "medium"
    return "low"


def discover_strategies(features: pd.DataFrame) -> pd.DataFrame:
    """Cluster trades by condition profile and compute strategy stats."""
    group_keys = ["mode", "iv_regime_cat", "sector", "after_big_move", "dte_bucket"]

    records = []
    for keys, group in features.groupby(group_keys, dropna=False):
        mode, iv_regime, sector, after_big_move, dte_bucket = keys
        n = len(group)
        if n < MIN_TRADES:
            continue

        wins = group["won"].sum()
        win_rate = round(wins / n * 100, 1)

        persona_stats = {}
        for persona, pg in group.groupby("persona"):
            persona_stats[str(persona)] = {
                "trades":   int(len(pg)),
                "win_rate": round(pg["won"].sum() / len(pg) * 100, 1),
                "avg_prem": round(float(pg["premium_collected"].mean()), 0),
            }

        def safe_mean(col):
            return round(float(group[col].mean()), 1) if col in group and group[col].notna().any() else None

        records.append({
            "strategy_id":       f"{mode}_{str(iv_regime)[:3]}_{str(sector)[:6]}_{str(after_big_move)[0]}_{dte_bucket}".lower(),
            "strategy_name":     _strategy_name(mode, iv_regime, sector, after_big_move, dte_bucket),
            "mode":              mode,
            "sector":            sector,
            "iv_regime":         iv_regime,
            "after_big_move":    bool(after_big_move),
            "dte_bucket":        dte_bucket,
            "n_trades":          n,
            "win_rate":          win_rate,
            "avg_premium":       round(float(group["premium_collected"].mean()), 0),
            "avg_pnl":           round(float(group["net_pnl"].mean()), 0),
            "avg_otm_pct":       safe_mean("otm_pct"),
            "avg_iv_rank":       safe_mean("iv_rank_approx"),
            "avg_dte":           safe_mean("dte"),
            "avg_hold_days":     safe_mean("hold_days"),
            "personas":          ", ".join(sorted(group["persona"].unique())),
            "persona_breakdown": json.dumps(persona_stats),
            "confidence":        _confidence(n),
            "conditions":        json.dumps({
                "mode": mode, "iv_regime": iv_regime, "sector": sector,
                "after_big_move": bool(after_big_move), "dte_bucket": dte_bucket,
            }),
        })

    if not records:
        return pd.DataFrame()

    df = pd.DataFrame(records)
    conf_order = {"high": 0, "medium": 1, "low": 2}
    df["_co"] = df["confidence"].map(conf_order)
    return df.sort_values(["_co", "win_rate"], ascending=[True, False]).drop(columns=["_co"]).reset_index(drop=True)


def persona_comparison(features: pd.DataFrame) -> pd.DataFrame:
    """Head-to-head stats per (persona × mode × sector)."""
    return features.groupby(["persona", "mode", "sector"]).agg(
        trades   =("won", "count"),
        win_rate =("won", lambda x: round(x.mean() * 100, 1)),
        avg_prem =("premium_collected", lambda x: round(x.mean(), 0)),
        avg_pnl  =("net_pnl", lambda x: round(x.mean(), 0)),
        avg_otm  =("otm_pct", lambda x: round(x.mean(), 1)) if "otm_pct" in features else ("won", lambda x: None),
        avg_dte  =("dte", lambda x: round(x.mean(), 1)),
    ).reset_index()


def best_conditions(strategies: pd.DataFrame, mode: str, top_n: int = 5) -> pd.DataFrame:
    sub = strategies[
        (strategies["mode"] == mode) &
        (strategies["confidence"].isin(["high", "medium"]))
    ].sort_values("win_rate", ascending=False)
    return sub.head(top_n)[["strategy_name","n_trades","win_rate",
                              "avg_premium","avg_pnl","personas","confidence"]]


def run_patterns(persona: str = "all", data_dir: str = "tradingData",
                 save: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Full pipeline: load → parse → enrich → cluster → save."""
    from src.analyzer import analyze
    from src.enricher import enrich

    trades, _ = analyze(persona, data_dir=data_dir)
    enriched  = enrich(trades)
    features  = build_features(enriched)
    strategies = discover_strategies(features)

    if save:
        OUTPUT_DIR.mkdir(exist_ok=True)
        label = persona.lower()
        enriched.to_csv(OUTPUT_DIR / f"{label}_enriched.csv", index=False)
        strategies.to_csv(OUTPUT_DIR / f"{label}_strategies.csv", index=False)
        persona_comparison(features).to_csv(OUTPUT_DIR / f"{label}_persona_comparison.csv", index=False)
        print(f"  Saved → output/{label}_enriched.csv, _strategies.csv, _persona_comparison.csv")

    return strategies, features


if __name__ == "__main__":
    from src.analyzer import analyze
    from src.enricher import enrich

    trades, _  = analyze("Arjuna")
    # Test without enrichment first (no yfinance needed)
    features   = build_features(trades)
    strategies = discover_strategies(features)

    print(f"\n{'='*60}")
    print(f"STRATEGY REGISTRY — {len(strategies)} strategies (pre-enrichment)")
    print(f"{'='*60}")
    for mode in ["A", "B", "C", "CC"]:
        top = best_conditions(strategies, mode)
        if len(top):
            print(f"\n── Mode {mode}")
            print(top.to_string(index=False))
