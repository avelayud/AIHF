"""
analyzer.py — Build trade-level analytics from parser output.

- For closed_positions datasets: each CSV row is already a closed trade/position outcome.
- For activity datasets: fallback to SELL_OPEN → BUY_CLOSE/EXPIRED/ASSIGNED matching.
"""

from __future__ import annotations

from collections import defaultdict, deque

import pandas as pd

from src.parser import load_persona


def _closed_positions_to_trades(df: pd.DataFrame) -> pd.DataFrame:
    records = []
    trade_id = 0

    for _, row in df.iterrows():
        trade_id += 1
        qty = abs(float(row.get("quantity") or 1))
        cost = float(row.get("cost") or 0)
        proceeds = float(row.get("proceeds") or 0)
        net_pnl = float(row.get("total_gl") or (proceeds - cost))

        base_notional = None
        if pd.notna(row.get("strike")):
            base_notional = abs(float(row["strike"]) * 100 * qty)
        elif pd.notna(row.get("avg_cost")):
            base_notional = abs(float(row["avg_cost"]) * qty)

        if pd.notna(row.get("year")):
            close_date = pd.Timestamp(year=int(row["year"]), month=12, day=31)
        else:
            close_date = pd.NaT

        pnl_pct = (net_pnl / cost * 100) if cost else None
        notional_ret = (net_pnl / base_notional * 100) if base_notional else None

        records.append(
            {
                "trade_id": trade_id,
                "persona": row.get("persona"),
                "year": int(row["year"]) if pd.notna(row.get("year")) else None,
                "source_file": row.get("source_file"),
                "ticker": row.get("ticker"),
                "sector": row.get("sector"),
                "mode": row.get("mode"),
                "opt_type": row.get("opt_type"),
                "dte_category": None,
                "dte": None,
                "strike": row.get("strike"),
                "contracts": qty,
                "entry_date": pd.NaT,
                "expiry_date": row.get("expiry"),
                "close_date": close_date,
                "hold_days": None,
                "close_type": "CLOSED_POSITION",
                "premium_collected": proceeds,
                "cost_to_close": cost,
                "net_pnl": net_pnl,
                "pnl_pct": pnl_pct,
                "outcome": "WIN" if net_pnl >= 0 else "LOSS",
                "entry_price": row.get("avg_cost"),
                "close_price": row.get("avg_proceeds"),
                "notional_at_entry": base_notional,
                "return_on_notional_pct": notional_ret,
                "raw_symbol": row.get("symbol"),
                "description": row.get("description"),
                "dataset_type": "closed_positions",
            }
        )

    return pd.DataFrame(records)


def _make_orphan(row):
    return {
        "trade_id": -1,
        "persona": row.get("persona"),
        "year": row.get("year"),
        "ticker": row.get("ticker"),
        "sector": row.get("sector"),
        "mode": None,
        "opt_type": row.get("opt_type"),
        "outcome": "ORPHAN_CLOSE",
        "close_type": row["action_type"],
        "entry_date": pd.NaT,
        "close_date": row["date"],
        "premium_collected": None,
        "cost_to_close": abs(row["Amount ($)"]) if pd.notna(row.get("Amount ($)")) else None,
        "net_pnl": None,
        "pnl_pct": None,
        "hold_days": None,
        "contracts": abs(row["Quantity"]) if pd.notna(row.get("Quantity")) else None,
        "strike": row.get("strike"),
        "dte": row.get("dte"),
        "dte_category": row.get("dte_category"),
        "expiry_date": row.get("expiry"),
        "entry_price": None,
        "close_price": None,
        "source_file": row.get("source_file"),
        "notional_at_entry": None,
        "return_on_notional_pct": None,
        "dataset_type": "activity",
    }


def _match_activity_trades(df: pd.DataFrame) -> pd.DataFrame:
    sells = df[(df["action_type"] == "SELL_OPEN") & ~df["is_correction"]].copy().sort_values("date")
    closes = df[df["action_type"].isin(["BUY_CLOSE", "EXPIRED", "ASSIGNED"]) & ~df["is_correction"]].copy().sort_values("date")

    records = []
    trade_id = 0
    queues = defaultdict(deque)

    for _, row in sells.iterrows():
        key = (row["persona"], row["ticker"], str(row["expiry"].date()) if pd.notna(row["expiry"]) else "?", row["strike"], row["opt_type"])
        contracts = abs(row["Quantity"]) if pd.notna(row["Quantity"]) else 1
        queues[key].append({"row": row, "contracts_remaining": contracts})

    for _, close_row in closes.iterrows():
        key = (
            close_row["persona"],
            close_row["ticker"],
            str(close_row["expiry"].date()) if pd.notna(close_row["expiry"]) else "?",
            close_row["strike"],
            close_row["opt_type"],
        )

        if key not in queues or not queues[key]:
            records.append(_make_orphan(close_row))
            continue

        contracts_to_close = abs(close_row["Quantity"]) if pd.notna(close_row["Quantity"]) else 1
        close_total = contracts_to_close

        while contracts_to_close > 0 and queues[key]:
            open_entry = queues[key][0]
            open_row = open_entry["row"]

            used = min(contracts_to_close, open_entry["contracts_remaining"])
            open_entry["contracts_remaining"] -= used
            contracts_to_close -= used

            trade_id += 1
            open_qty = abs(open_row["Quantity"]) if pd.notna(open_row["Quantity"]) else 1
            frac_open = used / open_qty
            frac_close = used / close_total if close_total else 1.0

            premium = abs(open_row["Amount ($)"]) * frac_open if pd.notna(open_row.get("Amount ($)")) else None

            if close_row["action_type"] == "EXPIRED":
                cost_to_close = 0.0
                outcome = "EXPIRED_WIN"
            elif close_row["action_type"] == "ASSIGNED":
                cost_to_close = None
                outcome = "ASSIGNED"
            else:
                cost_to_close = abs(close_row["Amount ($)"]) * frac_close if pd.notna(close_row.get("Amount ($)")) else None
                outcome = None

            net_pnl = premium - cost_to_close if premium is not None and cost_to_close is not None else None
            if outcome is None and net_pnl is not None:
                outcome = "WIN" if net_pnl >= 0 else "LOSS"

            pnl_pct = (net_pnl / premium * 100) if (net_pnl is not None and premium and premium > 0) else None
            hold_days = (close_row["date"] - open_row["date"]).days if pd.notna(open_row["date"]) and pd.notna(close_row["date"]) else None
            notional = abs(open_row["strike"] * 100 * used) if pd.notna(open_row.get("strike")) else None
            notional_ret = (net_pnl / notional * 100) if (net_pnl is not None and notional) else None

            records.append(
                {
                    "trade_id": trade_id,
                    "persona": open_row["persona"],
                    "year": open_row["year"],
                    "source_file": open_row["source_file"],
                    "ticker": open_row["ticker"],
                    "sector": open_row["sector"],
                    "mode": open_row["mode"],
                    "opt_type": open_row["opt_type"],
                    "dte_category": open_row["dte_category"],
                    "dte": open_row["dte"],
                    "strike": open_row["strike"],
                    "contracts": used,
                    "entry_date": open_row["date"],
                    "expiry_date": open_row["expiry"],
                    "close_date": close_row["date"],
                    "hold_days": hold_days,
                    "close_type": close_row["action_type"],
                    "premium_collected": premium,
                    "cost_to_close": cost_to_close,
                    "net_pnl": net_pnl,
                    "pnl_pct": pnl_pct,
                    "outcome": outcome,
                    "entry_price": open_row.get("Price ($)"),
                    "close_price": close_row.get("Price ($)") if close_row["action_type"] == "BUY_CLOSE" else None,
                    "notional_at_entry": notional,
                    "return_on_notional_pct": notional_ret,
                    "raw_symbol": open_row.get("Symbol"),
                    "description": None,
                    "dataset_type": "activity",
                }
            )

            if open_entry["contracts_remaining"] == 0:
                queues[key].popleft()

    for _, q in queues.items():
        for entry in q:
            open_row = entry["row"]
            trade_id += 1
            records.append(
                {
                    "trade_id": trade_id,
                    "persona": open_row["persona"],
                    "year": open_row["year"],
                    "source_file": open_row["source_file"],
                    "ticker": open_row["ticker"],
                    "sector": open_row["sector"],
                    "mode": open_row["mode"],
                    "opt_type": open_row["opt_type"],
                    "dte_category": open_row["dte_category"],
                    "dte": open_row["dte"],
                    "strike": open_row["strike"],
                    "contracts": entry["contracts_remaining"],
                    "entry_date": open_row["date"],
                    "expiry_date": open_row["expiry"],
                    "close_date": pd.NaT,
                    "hold_days": None,
                    "close_type": "OPEN",
                    "premium_collected": abs(open_row["Amount ($)"]) if pd.notna(open_row.get("Amount ($)")) else None,
                    "cost_to_close": None,
                    "net_pnl": None,
                    "pnl_pct": None,
                    "outcome": "OPEN",
                    "entry_price": open_row.get("Price ($)"),
                    "close_price": None,
                    "notional_at_entry": abs(open_row["strike"] * 100 * entry["contracts_remaining"]) if pd.notna(open_row.get("strike")) else None,
                    "return_on_notional_pct": None,
                    "raw_symbol": open_row.get("Symbol"),
                    "description": None,
                    "dataset_type": "activity",
                }
            )

    return pd.DataFrame(records)


def summarize(trades: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    closed = trades[trades["outcome"].isin(["WIN", "LOSS", "EXPIRED_WIN", "ASSIGNED"])].copy()

    def agg(g):
        resolved = g[g["outcome"].isin(["WIN", "LOSS", "EXPIRED_WIN"])]
        wins = resolved["outcome"].isin(["WIN", "EXPIRED_WIN"]).sum()
        return pd.Series(
            {
                "total_trades": len(g),
                "resolved_trades": len(resolved),
                "win_rate_pct": (wins / len(resolved) * 100) if len(resolved) else None,
                "assigned_count": (g["outcome"] == "ASSIGNED").sum(),
                "total_premium": g["premium_collected"].sum(),
                "total_pnl": g["net_pnl"].sum(),
                "avg_pnl": g["net_pnl"].mean(),
                "avg_premium": g["premium_collected"].mean(),
                "avg_hold_days": g["hold_days"].mean(),
                "avg_pnl_pct": g["pnl_pct"].mean(),
                "avg_return_on_notional_pct": g["return_on_notional_pct"].mean(),
                "max_loss": g["net_pnl"].min(),
                "max_win": g["net_pnl"].max(),
            }
        )

    return closed.groupby(by, dropna=False).apply(agg).reset_index()


def analyze(persona: str = "Arjuna", data_dir: str = "tradingData") -> tuple[pd.DataFrame, pd.DataFrame]:
    raw = load_persona(persona, data_dir=data_dir)
    if "dataset_type" in raw.columns and raw["dataset_type"].eq("closed_positions").all():
        trades = _closed_positions_to_trades(raw)
    else:
        trades = _match_activity_trades(raw)
    return trades, raw


if __name__ == "__main__":
    t, _ = analyze("Arjuna")
    print(t.head(5).to_string(index=False))
