"""
analyzer.py — Match SELL_OPEN → BUY_CLOSE/EXPIRED/ASSIGNED and compute P&L

Key output per closed trade:
  trade_id, persona, year, ticker, sector, mode, dte_category
  entry_date, expiry_date, close_date, hold_days
  opt_type, strike, contracts
  premium_collected, cost_to_close, net_pnl, pnl_pct
  outcome: WIN | LOSS | ASSIGNED | EXPIRED (full win)
"""

import pandas as pd
import numpy as np
from src.parser import load_persona


# ── Trade Matching ─────────────────────────────────────────────────────────────

def _match_trades(df: pd.DataFrame) -> pd.DataFrame:
    """
    Match each SELL_OPEN to its closing event using a FIFO queue
    per (persona, ticker, expiry, strike, opt_type).

    Handles:
      - Normal BUY_CLOSE
      - EXPIRED (full win, cost_to_close = 0)
      - ASSIGNED (stock received at strike — not a P&L close, flagged separately)
      - Partial closes (multiple BUY_CLOSE against one SELL_OPEN batch)
      - Same-day scaling (multiple SELL_OPEN, same key)
    """

    sells = df[
        (df["action_type"] == "SELL_OPEN") & ~df["is_correction"]
    ].copy().sort_values("date")

    closes = df[
        df["action_type"].isin(["BUY_CLOSE", "EXPIRED", "ASSIGNED"]) &
        ~df["is_correction"]
    ].copy().sort_values("date")

    records = []
    trade_id = 0

    # Group sells into queues keyed by position identity
    from collections import defaultdict, deque
    queues = defaultdict(deque)  # key → deque of sell rows

    for _, row in sells.iterrows():
        key = (
            row["persona"],
            row["ticker"],
            str(row["expiry"].date()) if pd.notna(row["expiry"]) else "?",
            row["strike"],
            row["opt_type"],
        )
        # Each sell row represents abs(Quantity) contracts
        contracts = abs(row["Quantity"]) if pd.notna(row["Quantity"]) else 1
        queues[key].append({
            "row": row,
            "contracts_remaining": contracts,
            "trade_id_base": None,
        })

    # Now match closes into the queues
    matched_close_indices = set()

    for _, close_row in closes.iterrows():
        key = (
            close_row["persona"],
            close_row["ticker"],
            str(close_row["expiry"].date()) if pd.notna(close_row["expiry"]) else "?",
            close_row["strike"],
            close_row["opt_type"],
        )

        if key not in queues or not queues[key]:
            # Unmatched close — record as orphan
            records.append(_make_orphan(close_row))
            continue

        contracts_to_close = abs(close_row["Quantity"]) if pd.notna(close_row["Quantity"]) else 1

        while contracts_to_close > 0 and queues[key]:
            open_entry = queues[key][0]
            open_row = open_entry["row"]

            used = min(contracts_to_close, open_entry["contracts_remaining"])
            open_entry["contracts_remaining"] -= used
            contracts_to_close -= used

            trade_id += 1
            frac = used / (abs(open_row["Quantity"]) if pd.notna(open_row["Quantity"]) else 1)

            premium = abs(open_row["Amount ($)"]) * frac if pd.notna(open_row["Amount ($)"]) else None

            if close_row["action_type"] == "EXPIRED":
                cost_to_close = 0.0
                outcome = "EXPIRED_WIN"
            elif close_row["action_type"] == "ASSIGNED":
                cost_to_close = None   # assignment cost is in stock purchase row
                outcome = "ASSIGNED"
            else:
                cost_to_close = abs(close_row["Amount ($)"]) * frac if pd.notna(close_row["Amount ($)"]) else None
                outcome = None  # determined below

            net_pnl = None
            if premium is not None and cost_to_close is not None:
                net_pnl = premium - cost_to_close

            if outcome is None:
                if net_pnl is not None:
                    outcome = "WIN" if net_pnl >= 0 else "LOSS"

            pnl_pct = None
            if net_pnl is not None and premium and premium > 0:
                pnl_pct = net_pnl / premium * 100

            hold_days = None
            if pd.notna(open_row["date"]) and pd.notna(close_row["date"]):
                hold_days = (close_row["date"] - open_row["date"]).days

            records.append({
                "trade_id":         trade_id,
                "persona":          open_row["persona"],
                "year":             open_row["year"],
                "source_file":      open_row["source_file"],
                "ticker":           open_row["ticker"],
                "sector":           open_row["sector"],
                "mode":             open_row["mode"],
                "opt_type":         open_row["opt_type"],
                "dte_category":     open_row["dte_category"],
                "dte":              open_row["dte"],
                "strike":           open_row["strike"],
                "contracts":        used,
                "entry_date":       open_row["date"],
                "expiry_date":      open_row["expiry"],
                "close_date":       close_row["date"],
                "hold_days":        hold_days,
                "close_type":       close_row["action_type"],
                "premium_collected": premium,
                "cost_to_close":    cost_to_close,
                "net_pnl":          net_pnl,
                "pnl_pct":          pnl_pct,
                "outcome":          outcome,
                "entry_price":      open_row["Price ($)"],
                "close_price":      close_row["Price ($)"] if close_row["action_type"] == "BUY_CLOSE" else None,
            })

            if open_entry["contracts_remaining"] == 0:
                queues[key].popleft()

    # Any remaining opens are still open positions
    for key, q in queues.items():
        for entry in q:
            open_row = entry["row"]
            trade_id += 1
            records.append({
                "trade_id":         trade_id,
                "persona":          open_row["persona"],
                "year":             open_row["year"],
                "source_file":      open_row["source_file"],
                "ticker":           open_row["ticker"],
                "sector":           open_row["sector"],
                "mode":             open_row["mode"],
                "opt_type":         open_row["opt_type"],
                "dte_category":     open_row["dte_category"],
                "dte":              open_row["dte"],
                "strike":           open_row["strike"],
                "contracts":        entry["contracts_remaining"],
                "entry_date":       open_row["date"],
                "expiry_date":      open_row["expiry"],
                "close_date":       pd.NaT,
                "hold_days":        None,
                "close_type":       "OPEN",
                "premium_collected": abs(open_row["Amount ($)"]) if pd.notna(open_row["Amount ($)"]) else None,
                "cost_to_close":    None,
                "net_pnl":          None,
                "pnl_pct":          None,
                "outcome":          "OPEN",
                "entry_price":      open_row["Price ($)"],
                "close_price":      None,
            })

    return pd.DataFrame(records)


def _make_orphan(row):
    return {
        "trade_id": -1, "persona": row.get("persona"), "year": row.get("year"),
        "ticker": row.get("ticker"), "sector": row.get("sector"),
        "mode": None, "opt_type": row.get("opt_type"),
        "outcome": "ORPHAN_CLOSE", "close_type": row["action_type"],
        "entry_date": pd.NaT, "close_date": row["date"],
        "premium_collected": None, "cost_to_close": abs(row["Amount ($)"]) if pd.notna(row.get("Amount ($)")) else None,
        "net_pnl": None, "pnl_pct": None, "hold_days": None,
        "contracts": abs(row["Quantity"]) if pd.notna(row.get("Quantity")) else None,
        "strike": row.get("strike"), "dte": row.get("dte"), "dte_category": row.get("dte_category"),
        "expiry_date": row.get("expiry"), "entry_price": None, "close_price": None, "source_file": row.get("source_file"),
    }


# ── Summary Stats ──────────────────────────────────────────────────────────────

def win_rate(group: pd.DataFrame) -> float:
    closed = group[group["outcome"].isin(["WIN", "LOSS", "EXPIRED_WIN"])]
    if len(closed) == 0: return None
    wins = closed["outcome"].isin(["WIN", "EXPIRED_WIN"]).sum()
    return wins / len(closed) * 100


def summarize(trades: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    """Aggregate trade stats grouped by any combination of columns."""
    closed = trades[trades["outcome"].isin(["WIN", "LOSS", "EXPIRED_WIN", "ASSIGNED"])]

    def agg(g):
        total = len(g)
        cl = g[g["outcome"].isin(["WIN", "LOSS", "EXPIRED_WIN"])]
        wins = cl["outcome"].isin(["WIN", "EXPIRED_WIN"]).sum()
        return pd.Series({
            "total_trades":       total,
            "closed_trades":      len(cl),
            "win_rate_pct":       wins / len(cl) * 100 if len(cl) > 0 else None,
            "assigned_count":     (g["outcome"] == "ASSIGNED").sum(),
            "total_premium":      g["premium_collected"].sum(),
            "total_pnl":          g["net_pnl"].sum(),
            "avg_pnl":            g["net_pnl"].mean(),
            "avg_premium":        g["premium_collected"].mean(),
            "avg_hold_days":      g["hold_days"].mean(),
            "avg_pnl_pct":        g["pnl_pct"].mean(),
            "max_loss":           g["net_pnl"].min(),
            "max_win":            g["net_pnl"].max(),
        })

    return closed.groupby(by, dropna=False).apply(agg).reset_index()


# ── Main Entry Point ───────────────────────────────────────────────────────────

def analyze(persona: str = "Arjuna", data_dir: str = "tradingData") -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Full pipeline: load → parse → match → stats.
    Returns: (trades_df, raw_df)
    """
    raw = load_persona(persona, data_dir=data_dir)
    trades = _match_trades(raw)
    return trades, raw


if __name__ == "__main__":
    trades, raw = analyze("Arjuna")

    closed = trades[trades["outcome"].isin(["WIN", "LOSS", "EXPIRED_WIN", "ASSIGNED"])]
    open_pos = trades[trades["outcome"] == "OPEN"]

    print(f"\n{'='*55}")
    print(f"TRADE OUTCOMES — ALL YEARS")
    print(f"{'='*55}")
    print(trades["outcome"].value_counts().to_string())

    print(f"\n{'='*55}")
    print(f"WIN RATE BY MODE")
    print(f"{'='*55}")
    mode_stats = summarize(trades, ["mode"])
    print(mode_stats[["mode","total_trades","win_rate_pct","total_premium","total_pnl","avg_hold_days"]].to_string(index=False))

    print(f"\n{'='*55}")
    print(f"WIN RATE BY TICKER (top 12)")
    print(f"{'='*55}")
    ticker_stats = summarize(trades, ["ticker"]).sort_values("total_premium", ascending=False)
    print(ticker_stats.head(12)[["ticker","total_trades","win_rate_pct","total_premium","total_pnl","avg_pnl_pct"]].to_string(index=False))

    print(f"\n{'='*55}")
    print(f"BY YEAR")
    print(f"{'='*55}")
    year_stats = summarize(trades, ["year"])
    print(year_stats[["year","total_trades","win_rate_pct","total_premium","total_pnl"]].to_string(index=False))

    print(f"\n{'='*55}")
    print(f"OPEN POSITIONS ({len(open_pos)} legs)")
    print(f"{'='*55}")
    if len(open_pos):
        print(open_pos[["ticker","mode","opt_type","strike","expiry_date","contracts","premium_collected"]].to_string(index=False))
