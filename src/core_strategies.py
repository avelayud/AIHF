"""
core_strategies.py — Distinct strategy definitions and metrics.
"""

from __future__ import annotations

import pandas as pd

WIN_OUTCOMES = ["WIN", "EXPIRED_WIN"]
RESOLVED = ["WIN", "LOSS", "EXPIRED_WIN"]


def _pct(n, d):
    return round(float(n / d * 100), 2) if d else None


def summarize_core_strategies(trades: pd.DataFrame) -> pd.DataFrame:
    closed = trades[trades["outcome"].isin(RESOLVED)].copy()
    if closed.empty:
        return pd.DataFrame()

    closed["won"] = closed["outcome"].isin(WIN_OUTCOMES)

    defs = [
        {
            "strategy_name": "NVDA Wheel Engine",
            "description": "Recurring NVDA short puts and covered calls for premium compounding.",
            "conditions": {"ticker": "NVDA", "opt_type": ["PUT", "CALL"]},
            "mask": (closed["ticker"] == "NVDA") & (closed["opt_type"].isin(["PUT", "CALL"])),
        },
        {
            "strategy_name": "Structured Income Puts",
            "description": "Mid-duration premium selling (Mode C / 15-364 DTE puts).",
            "conditions": {"mode": "C", "opt_type": "PUT"},
            "mask": (closed["mode"] == "C") & (closed["opt_type"] == "PUT"),
        },
        {
            "strategy_name": "Long-Dated Discount Entry Puts",
            "description": "Far-dated cash-secured puts (Mode B / 365+ DTE) to get paid for long-term entry.",
            "conditions": {"mode": "B", "opt_type": "PUT"},
            "mask": (closed["mode"] == "B") & (closed["opt_type"] == "PUT"),
        },
        {
            "strategy_name": "Covered Call Overlay",
            "description": "Call-writing overlay on held names to monetize upside caps.",
            "conditions": {"mode": "CC", "opt_type": "CALL"},
            "mask": (closed["mode"] == "CC") & (closed["opt_type"] == "CALL"),
        },
    ]

    rows = []
    for d in defs:
        g = closed[d["mask"]].copy()
        if g.empty:
            continue
        n = len(g)
        wins = int(g["won"].sum())
        rows.append(
            {
                "strategy_name": d["strategy_name"],
                "description": d["description"],
                "conditions": d["conditions"],
                "transactions": int(n),
                "win_rate": _pct(wins, n),
                "realized_pnl": round(float(g["net_pnl"].sum()), 0),
                "premium_net": round(float(g["premium_collected"].sum() - g["cost_to_close"].fillna(0).sum()), 0),
                "avg_return_pct": round(float(g["pnl_pct"].mean()), 2) if g["pnl_pct"].notna().any() else None,
                "avg_notional_return_pct": round(float(g["return_on_notional_pct"].mean()), 3)
                if g["return_on_notional_pct"].notna().any()
                else None,
                "avg_hold_days": round(float(g["hold_days"].mean()), 1) if g["hold_days"].notna().any() else None,
            }
        )

    return pd.DataFrame(rows)
