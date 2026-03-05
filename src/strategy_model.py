"""
strategy_model.py — Build strategy definitions from augmented closed-lot trades.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

RESOLVED = ["WIN", "LOSS", "EXPIRED_WIN"]


def build_strategy_model(augmented: pd.DataFrame) -> pd.DataFrame:
    df = augmented[augmented["outcome"].isin(RESOLVED)].copy()
    if df.empty:
        return pd.DataFrame()

    df["won"] = df["outcome"].isin(["WIN", "EXPIRED_WIN"])
    df["move_bucket"] = pd.cut(
        pd.to_numeric(df["underlying_move_pct"], errors="coerce"),
        bins=[-999, -3, -1, 1, 3, 999],
        labels=["<=-3%", "-3%..-1%", "-1%..1%", "1%..3%", ">=3%"],
    )
    df["iv_bucket"] = pd.cut(
        pd.to_numeric(df["implied_vol_entry_pct"], errors="coerce"),
        bins=[-1, 20, 40, 60, 1000],
        labels=["low", "normal", "elevated", "high"],
    )

    group_cols = ["mode", "opt_type", "ticker", "move_bucket", "iv_bucket"]
    out = (
        df.groupby(group_cols, dropna=False)
        .agg(
            transactions=("trade_id", "count"),
            win_rate=("won", lambda x: round(float(x.mean() * 100), 2)),
            realized_pnl=("net_pnl", lambda x: round(float(x.sum()), 0)),
            premium_net=("premium_collected", lambda x: round(float(x.sum()), 0)),
            avg_return_pct=("pnl_pct", lambda x: round(float(x.mean()), 2)),
            avg_hold_days=("hold_days", lambda x: round(float(x.mean()), 1)),
        )
        .reset_index()
    )

    out = out[out["transactions"] >= 5].sort_values(["win_rate", "realized_pnl"], ascending=[False, False])
    out.insert(0, "strategy_name", out.apply(lambda r: f"{r['ticker']} {r['mode']} {r['move_bucket']} {r['iv_bucket']}", axis=1))
    return out


def run_strategy_model(augmented_csv: str = "output/augmented_trades.csv", output_csv: str = "output/strategy_model.csv") -> pd.DataFrame:
    df = pd.read_csv(augmented_csv)
    model = build_strategy_model(df)
    Path(output_csv).parent.mkdir(parents=True, exist_ok=True)
    model.to_csv(output_csv, index=False)
    print(f"Saved strategy model: {output_csv}")
    return model


if __name__ == "__main__":
    run_strategy_model()
