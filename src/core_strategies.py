"""
core_strategies.py — Plain-English strategy groupings from closed trades.
"""

from __future__ import annotations

import pandas as pd

WIN_OUTCOMES = ["WIN", "EXPIRED_WIN"]
RESOLVED = ["WIN", "LOSS", "EXPIRED_WIN"]


def _pct(n, d):
    return round(float(n / d * 100), 1) if d else None


def summarize_core_strategies(trades: pd.DataFrame) -> pd.DataFrame:
    closed = trades[trades["outcome"].isin(RESOLVED)].copy()
    if closed.empty:
        return pd.DataFrame()

    closed["won"] = closed["outcome"].isin(WIN_OUTCOMES)

    defs = [
        {
            "strategy_name": "Put Premium Engine",
            "group": "Core Mode",
            "definition": "Sell puts for premium income.",
            "conditions": {"opt_type": "PUT"},
            "mask": closed["opt_type"] == "PUT",
        },
        {
            "strategy_name": "Covered Call Income",
            "group": "Overlay",
            "definition": "Write calls (CC) for income on held names.",
            "conditions": {"opt_type": "CALL", "mode": "CC"},
            "mask": closed["opt_type"] == "CALL",
        },
        {
            "strategy_name": "Stock Position Closures",
            "group": "Core Mode",
            "definition": "Common stock trades closed for realized gain/loss.",
            "conditions": {"mode": "STOCK"},
            "mask": closed["mode"] == "STOCK",
        },
        {
            "strategy_name": "NVDA Core Engine",
            "group": "Ticker Core",
            "definition": "All NVDA closed trades across stock/options.",
            "conditions": {"ticker": "NVDA"},
            "mask": closed["ticker"] == "NVDA",
        },
        {
            "strategy_name": "NVDA Put Premium",
            "group": "Ticker Pattern",
            "definition": "NVDA puts as the main recurring premium strategy.",
            "conditions": {"ticker": "NVDA", "opt_type": "PUT"},
            "mask": (closed["ticker"] == "NVDA") & (closed["opt_type"] == "PUT"),
        },
    ]

    rows = []
    for d in defs:
        g = closed[d["mask"]].copy()
        if g.empty:
            continue
        wins = int(g["won"].sum())
        rows.append(
            {
                "strategy_name": d["strategy_name"],
                "group": d["group"],
                "definition": d["definition"],
                "conditions": d["conditions"],
                "trades": int(len(g)),
                "wins": wins,
                "win_rate": _pct(wins, len(g)),
                "premium": round(float(g["premium_collected"].sum()), 0),
                "pnl": round(float(g["net_pnl"].sum()), 0),
                "avg_pnl": round(float(g["net_pnl"].mean()), 2),
                "avg_pnl_pct": round(float(g["pnl_pct"].mean()), 2) if g["pnl_pct"].notna().any() else None,
                "avg_return_on_notional_pct": round(float(g["return_on_notional_pct"].mean()), 3)
                if g["return_on_notional_pct"].notna().any()
                else None,
            }
        )

    if not rows:
        return pd.DataFrame()

    out = pd.DataFrame(rows)
    order = {"Core Mode": 0, "Overlay": 1, "Ticker Core": 2, "Ticker Pattern": 3}
    out["_o"] = out["group"].map(order).fillna(9)
    return out.sort_values(["_o", "premium"], ascending=[True, False]).drop(columns=["_o"]).reset_index(drop=True)
