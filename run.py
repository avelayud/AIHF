#!/usr/bin/env python3
"""
run.py — Main CLI for the AIHF trading analysis pipeline.

Usage:
    python run.py                          # Arjuna, all years
    python run.py --persona Sundar         # Sundar only
    python run.py --persona all            # all personas combined
    python run.py --persona all --compare  # side-by-side persona comparison
    python run.py --year 2025              # filter to one year
    python run.py --output csv             # save results to output/
    python run.py --output csv --persona Arjuna --year 2025

Outputs:
    Prints summary tables to terminal.
    With --output csv: saves trades + stats to output/<persona>_<year>_*.csv
"""

import argparse
import sys
import pandas as pd
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).parent))

from src.parser import load_persona, UNIVERSE
from src.analyzer import analyze, summarize

DATA_DIR = "tradingData"
OUTPUT_DIR = Path("output")


def print_header(title: str):
    print(f"\n{'─'*58}")
    print(f"  {title}")
    print(f"{'─'*58}")


def print_section(trades: pd.DataFrame, persona: str, year_filter: int | None):
    label = f"{persona}" + (f" · {year_filter}" if year_filter else " · all years")
    print_header(f"ARJUNA HEDGE FUND — {label.upper()}")

    if year_filter:
        trades = trades[trades["year"] == year_filter]

    closed = trades[trades["outcome"].isin(["WIN", "LOSS", "EXPIRED_WIN", "ASSIGNED"])]
    total_premium = trades["premium_collected"].sum()
    total_pnl = trades["net_pnl"].sum()
    n_closed = len(closed)
    wins = closed["outcome"].isin(["WIN", "EXPIRED_WIN"]).sum()
    wr = wins / n_closed * 100 if n_closed else 0

    print(f"\n  {'Total trades (closed):':28} {n_closed}")
    print(f"  {'Overall win rate:':28} {wr:.1f}%")
    print(f"  {'Total premium collected:':28} ${total_premium:,.0f}")
    print(f"  {'Net P&L (closed trades):':28} ${total_pnl:,.0f}")
    print(f"  {'Open positions:':28} {(trades['outcome']=='OPEN').sum()}")
    print(f"  {'Assignments:':28} {(trades['outcome']=='ASSIGNED').sum()}")

    print_header("BY STRATEGY MODE")
    mode_stats = summarize(trades, ["mode"]).sort_values("total_premium", ascending=False)
    print(f"\n  {'Mode':<6} {'Trades':>7} {'Win%':>7} {'Premium':>12} {'Net P&L':>12} {'Avg Hold':>9}")
    print(f"  {'─'*6} {'─'*7} {'─'*7} {'─'*12} {'─'*12} {'─'*9}")
    for _, r in mode_stats.iterrows():
        mode_label = {
            "A": "A  (0-14 DTE puts)",
            "B": "B  (365+ DTE puts)",
            "C": "C  (mid-range)",
            "CC": "CC (covered calls)",
        }.get(str(r["mode"]), str(r["mode"]))
        wr_s = f"{r['win_rate_pct']:.1f}%" if pd.notna(r["win_rate_pct"]) else "—"
        pnl_s = f"${r['total_pnl']:,.0f}" if pd.notna(r["total_pnl"]) else "—"
        hold_s = f"{r['avg_hold_days']:.1f}d" if pd.notna(r["avg_hold_days"]) else "—"
        print(f"  {mode_label:<24} {int(r['total_trades']):>5}   {wr_s:>6}  ${r['total_premium']:>10,.0f}  {pnl_s:>12}  {hold_s:>8}")

    print_header("BY TICKER (top 15 by premium)")
    ticker_stats = summarize(trades, ["ticker", "sector"]).sort_values("total_premium", ascending=False)
    print(f"\n  {'Ticker':<8} {'Sector':<20} {'Trades':>6} {'Win%':>7} {'Premium':>12} {'Net P&L':>12}")
    print(f"  {'─'*8} {'─'*20} {'─'*6} {'─'*7} {'─'*12} {'─'*12}")
    for _, r in ticker_stats.head(15).iterrows():
        wr_s = f"{r['win_rate_pct']:.1f}%" if pd.notna(r["win_rate_pct"]) else "—"
        pnl_s = f"${r['total_pnl']:,.0f}" if pd.notna(r["total_pnl"]) else "—"
        sector = str(r.get("sector", "")).replace("_", " ")
        print(f"  {str(r['ticker']):<8} {sector:<20} {int(r['total_trades']):>6}  {wr_s:>6}  ${r['total_premium']:>10,.0f}  {pnl_s:>12}")

    print_header("BY YEAR")
    year_stats = summarize(trades, ["year"]).sort_values("year")
    print(f"\n  {'Year':<6} {'Trades':>7} {'Win%':>7} {'Premium':>12} {'Net P&L':>12}")
    print(f"  {'─'*6} {'─'*7} {'─'*7} {'─'*12} {'─'*12}")
    for _, r in year_stats.iterrows():
        wr_s = f"{r['win_rate_pct']:.1f}%" if pd.notna(r["win_rate_pct"]) else "—"
        pnl_s = f"${r['total_pnl']:,.0f}" if pd.notna(r["total_pnl"]) else "—"
        print(f"  {int(r['year']):<6}  {int(r['total_trades']):>6}  {wr_s:>6}  ${r['total_premium']:>10,.0f}  {pnl_s:>12}")

    open_pos = trades[trades["outcome"] == "OPEN"]
    if len(open_pos):
        print_header(f"OPEN POSITIONS ({len(open_pos)} legs)")
        print(f"\n  {'Ticker':<7} {'Mode':<5} {'Type':<5} {'Strike':>8} {'Expiry':<12} {'Cts':>4} {'Premium':>10}")
        print(f"  {'─'*7} {'─'*5} {'─'*5} {'─'*8} {'─'*12} {'─'*4} {'─'*10}")
        for _, r in open_pos.sort_values("expiry_date").iterrows():
            exp = r["expiry_date"].strftime("%Y-%m-%d") if pd.notna(r["expiry_date"]) else "—"
            prem = f"${r['premium_collected']:,.0f}" if pd.notna(r["premium_collected"]) else "—"
            print(f"  {str(r['ticker']):<7} {str(r['mode']):<5} {str(r['opt_type']):<5} {r['strike']:>8.1f} {exp:<12} {int(r['contracts']):>4} {prem:>10}")


def compare_personas(data_dir: str):
    """Print side-by-side stats for all available personas."""
    root = Path(data_dir)
    personas = sorted([d.name for d in root.iterdir() if d.is_dir()])
    if not personas:
        print("No persona directories found.")
        return

    all_stats = {}
    for p in personas:
        try:
            trades, _ = analyze(p, data_dir=data_dir)
            all_stats[p] = trades
        except FileNotFoundError as e:
            print(f"  [SKIP] {p}: {e}")

    print_header("PERSONA COMPARISON")
    print(f"\n  {'Metric':<30}", end="")
    for p in all_stats: print(f"  {p:>14}", end="")
    print()
    print(f"  {'─'*30}", end="")
    for _ in all_stats: print(f"  {'─'*14}", end="")
    print()

    def row(label, fn):
        print(f"  {label:<30}", end="")
        for p, t in all_stats.items():
            try: val = fn(t)
            except Exception: val = "—"
            print(f"  {val:>14}", end="")
        print()

    closed = lambda t: t[t["outcome"].isin(["WIN","LOSS","EXPIRED_WIN","ASSIGNED"])]
    wr = lambda t: f"{(closed(t)['outcome'].isin(['WIN','EXPIRED_WIN']).sum() / len(closed(t)) * 100):.1f}%" if len(closed(t)) else "—"

    row("Total closed trades",    lambda t: str(len(closed(t))))
    row("Overall win rate",       wr)
    row("Total premium collected",lambda t: f"${t['premium_collected'].sum():,.0f}")
    row("Net P&L",                lambda t: f"${t['net_pnl'].sum():,.0f}")
    row("Assignments",            lambda t: str((t['outcome']=='ASSIGNED').sum()))
    row("Open positions",         lambda t: str((t['outcome']=='OPEN').sum()))
    row("Avg hold days (closed)", lambda t: f"{closed(t)['hold_days'].mean():.1f}d")
    row("Mode A win rate",        lambda t: f"{(lambda g: g['outcome'].isin(['WIN','EXPIRED_WIN']).sum()/len(g)*100 if len(g) else 0)(closed(t)[closed(t)['mode']=='A']):.1f}%")
    row("Mode B win rate",        lambda t: f"{(lambda g: g['outcome'].isin(['WIN','EXPIRED_WIN']).sum()/len(g)*100 if len(g) else 0)(closed(t)[closed(t)['mode']=='B']):.1f}%")
    row("Mode CC win rate",       lambda t: f"{(lambda g: g['outcome'].isin(['WIN','EXPIRED_WIN']).sum()/len(g)*100 if len(g) else 0)(closed(t)[closed(t)['mode']=='CC']):.1f}%")


def save_output(trades: pd.DataFrame, persona: str, year_filter: int | None):
    OUTPUT_DIR.mkdir(exist_ok=True)
    suffix = f"{persona}_{year_filter}" if year_filter else persona

    if year_filter:
        trades = trades[trades["year"] == year_filter]

    trades_path = OUTPUT_DIR / f"{suffix}_trades.csv"
    trades.to_csv(trades_path, index=False)
    print(f"\n  Saved: {trades_path}")

    for group_by in [["mode"], ["ticker"], ["year"]]:
        stats = summarize(trades, group_by)
        label = "_".join(group_by)
        stats_path = OUTPUT_DIR / f"{suffix}_stats_by_{label}.csv"
        stats.to_csv(stats_path, index=False)
        print(f"  Saved: {stats_path}")


def main():
    parser = argparse.ArgumentParser(description="AIHF Trading Analysis Pipeline")
    parser.add_argument("--persona", default="Arjuna",
                        help="Persona name (dir in tradingData/), or 'all'")
    parser.add_argument("--year", type=int, default=None,
                        help="Filter to a specific year (e.g. 2025)")
    parser.add_argument("--compare", action="store_true",
                        help="Side-by-side comparison of all personas")
    parser.add_argument("--output", choices=["csv"], default=None,
                        help="Save results to output/ directory")
    parser.add_argument("--data-dir", default=DATA_DIR,
                        help=f"Root data directory (default: {DATA_DIR})")
    args = parser.parse_args()

    if args.compare:
        compare_personas(args.data_dir)
        return

    trades, raw = analyze(args.persona, data_dir=args.data_dir)
    print_section(trades, args.persona, args.year)

    if args.output == "csv":
        save_output(trades, args.persona, args.year)


if __name__ == "__main__":
    main()
