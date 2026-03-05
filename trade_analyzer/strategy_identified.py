#!/usr/bin/env python3
"""
strategy_identified.py

Analyze trade CSVs from trade_analyzer/data/<persona>/ and output a grounded
core-strategy report with win rates, returns, and action suggestions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.analyzer import analyze
from src.core_strategies import summarize_core_strategies

DATA_DIR = Path("trade_analyzer/data")
OUTPUT_PATH = Path("trade_analyzer/output/strategy_identified_report.md")
WIN_OUTCOMES = ["WIN", "EXPIRED_WIN"]
TRADE_OUTCOMES = ["WIN", "LOSS", "EXPIRED_WIN"]


def _fmt_money(v: float | None) -> str:
    if v is None or pd.isna(v):
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.0f}"


def _fmt_pct(v: float | None, places: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.{places}f}%"


def _fmt_num(v: float | None, places: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.{places}f}"


def build_report(persona: str = "Arjuna") -> str:
    trades, _ = analyze(persona, data_dir=str(DATA_DIR))
    closed = trades[trades["outcome"].isin(TRADE_OUTCOMES)].copy()
    closed["won"] = closed["outcome"].isin(WIN_OUTCOMES)

    if closed.empty:
        return "# Strategy Identified\n\nNo resolved option trades found."

    total_trades = len(closed)
    total_wins = int(closed["won"].sum())
    win_rate = round(total_wins / total_trades * 100, 1)
    total_premium = round(float(closed["premium_collected"].sum()), 0)
    total_pnl = round(float(closed["net_pnl"].sum()), 0)
    avg_trade_return = round(float(closed["pnl_pct"].mean()), 2)
    avg_notional_return = round(float(closed["return_on_notional_pct"].mean()), 3)

    mode_stats = (
        closed.groupby("mode", as_index=False)
        .agg(
            trades=("trade_id", "count"),
            wins=("won", "sum"),
            premium=("premium_collected", "sum"),
            pnl=("net_pnl", "sum"),
            avg_pnl_pct=("pnl_pct", "mean"),
            avg_notional_pct=("return_on_notional_pct", "mean"),
        )
        .sort_values("premium", ascending=False)
    )
    mode_stats["win_rate"] = (mode_stats["wins"] / mode_stats["trades"] * 100).round(1)

    ticker_stats = (
        closed.groupby("ticker", as_index=False)
        .agg(trades=("trade_id", "count"), premium=("premium_collected", "sum"), pnl=("net_pnl", "sum"), wins=("won", "sum"))
        .sort_values("premium", ascending=False)
    )
    ticker_stats["win_rate"] = (ticker_stats["wins"] / ticker_stats["trades"] * 100).round(1)

    top_ticker = ticker_stats.iloc[0]
    top_ticker_share = round(float(top_ticker["premium"] / total_premium * 100), 1) if total_premium else 0.0

    core = summarize_core_strategies(trades)

    suggestions: list[str] = []
    weak_modes = mode_stats[mode_stats["win_rate"] < 60]
    for _, r in weak_modes.iterrows():
        suggestions.append(
            f"Mode {r['mode']} win rate is {_fmt_pct(r['win_rate'])} on {int(r['trades'])} trades; tighten entry filters and reduce size until edge improves."
        )

    if top_ticker_share >= 50:
        suggestions.append(
            f"{top_ticker['ticker']} drives {top_ticker_share}% of premium; treat it as a dedicated monitoring stream rather than a generic scanner target."
        )

    cc = mode_stats[mode_stats["mode"] == "CC"]
    if not cc.empty:
        cc_row = cc.iloc[0]
        suggestions.append(
            f"Covered calls generated {_fmt_money(cc_row['premium'])} across {int(cc_row['trades'])} trades; keep call-writing as its own signal path tied to stock ownership."
        )

    if not suggestions:
        suggestions.append("No obvious weak core strategy. Keep current sizing and monitor drawdown clusters by ticker.")

    lines = [
        "# Strategy Identified",
        "",
        f"Data source: `{DATA_DIR / persona}`",
        "",
        "## Portfolio Truth Metrics",
        f"- Resolved option trades: **{total_trades}**",
        f"- Win rate (WIN + EXPIRED_WIN only): **{_fmt_pct(win_rate)}**",
        f"- Premium collected (resolved): **{_fmt_money(total_premium)}**",
        f"- Realized net P&L (resolved): **{_fmt_money(total_pnl)}**",
        f"- Average return per trade (net_pnl / premium): **{_fmt_pct(avg_trade_return,2)}**",
        f"- Average return on notional (net_pnl / strike notional): **{_fmt_pct(avg_notional_return,3)}**",
        "",
        "## Core Strategy Breakdown",
        "",
        "| Strategy | Transactions | Win Rate | Premium Net | Realized P&L | Avg Return % | Avg Notional % | Avg Hold Days |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]

    if core.empty:
        lines.append("| No rows | — | — | — | — | — | — | — |")
    else:
        for _, r in core.iterrows():
            lines.append(
                f"| {r['strategy_name']} | {int(r['transactions'])} | {_fmt_pct(r['win_rate'])} | {_fmt_money(r['premium_net'])} | {_fmt_money(r['realized_pnl'])} | {_fmt_pct(r['avg_return_pct'],2)} | {_fmt_pct(r['avg_notional_return_pct'],3)} | {_fmt_num(r['avg_hold_days'],1)} |"
            )

    lines += [
        "",
        "## Mode Summary",
        "",
        "| Mode | Trades | Win Rate | Premium | Net P&L | Avg P&L % | Avg Notional % |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in mode_stats.iterrows():
        lines.append(
            f"| {r['mode']} | {int(r['trades'])} | {_fmt_pct(r['win_rate'])} | {_fmt_money(r['premium'])} | {_fmt_money(r['pnl'])} | {_fmt_pct(r['avg_pnl_pct'],2)} | {_fmt_pct(r['avg_notional_pct'],3)} |"
        )

    lines += [
        "",
        "## Key Learnings",
        f"- Top premium ticker is **{top_ticker['ticker']}** with {_fmt_money(top_ticker['premium'])} ({top_ticker_share}% of premium).",
        f"- Top ticker win rate: **{_fmt_pct(top_ticker['win_rate'])}** across {int(top_ticker['trades'])} trades.",
        "- Win rate excludes ASSIGNED events to avoid distorting trade quality metrics.",
        "",
        "## Suggestions",
    ]
    for s in suggestions:
        lines.append(f"- {s}")

    return "\n".join(lines) + "\n"


def main():
    report = build_report("Arjuna")
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(report, encoding="utf-8")
    print(f"Written: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
