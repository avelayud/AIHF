"""
parser.py — Load Fidelity exports into a normalized DataFrame.

Supports:
- Closed Positions exports (new primary source)
- Activity exports (legacy fallback)
"""

from __future__ import annotations

import re
from io import StringIO
from pathlib import Path

import pandas as pd

UNIVERSE = {
    "tech_core": ["NVDA", "AMD", "GOOGL", "GOOG", "META", "AMZN", "AAPL", "TSLA", "ORCL", "MSFT", "CRM"],
    "tech_growth": ["CRWV", "HOOD", "PLTR", "RDDT", "FIG"],
    "leveraged": ["TQQQ", "SOXL", "UPRO", "SPXL", "QQQ", "SPY"],
    "long_term_quality": ["COST", "UNH", "BRK", "BRKB", "V", "MA", "JPM", "WMT"],
    "crypto_proxy": ["IBIT", "MSTR", "COIN"],
}

TICKER_SECTOR: dict[str, str] = {}
for _sector, _tickers in UNIVERSE.items():
    for _t in _tickers:
        TICKER_SECTOR[_t] = _sector


def get_sector(ticker: str) -> str:
    return TICKER_SECTOR.get(str(ticker).upper(), "other")


def _extract_ticker(symbol: str) -> str:
    if pd.isna(symbol):
        return ""
    s = str(symbol).strip().lstrip("-")
    m = re.match(r"^([A-Z]+)", s)
    return m.group(1) if m else s


def _parse_option_symbol(symbol: str) -> dict:
    result = {"expiry": pd.NaT, "opt_type": None, "strike": None}
    if pd.isna(symbol):
        return result
    s = str(symbol).replace(" ", "").replace("-", "")
    m = re.search(r"(\d{6})([CP])(\d+\.?\d*)", s)
    if not m:
        return result
    ds, cp, strike_s = m.group(1), m.group(2), m.group(3)
    try:
        result["expiry"] = pd.Timestamp(year=int("20" + ds[:2]), month=int(ds[2:4]), day=int(ds[4:6]))
    except Exception:
        pass
    result["opt_type"] = "PUT" if cp == "P" else "CALL"
    try:
        result["strike"] = float(strike_s)
    except Exception:
        pass
    return result


def _mode_closed(opt_type: str | None, ticker: str) -> str:
    if opt_type == "CALL":
        return "CC"
    if opt_type == "PUT":
        return "PUT"
    return "STOCK"


def _parse_year_from_filename(path: Path) -> int | None:
    m = re.search(r"(20\d{2})", path.name)
    return int(m.group(1)) if m else None


def _read_lines(path: Path) -> list[str]:
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.readlines()


def _detect_format(path: Path) -> str:
    lines = _read_lines(path)
    head = "\n".join(lines[:6]).lower()
    if "closed positions" in head:
        return "closed_positions"
    return "activity"


def _to_num(series: pd.Series) -> pd.Series:
    s = (
        series.astype(str)
        .str.replace(",", "", regex=False)
        .str.replace("$", "", regex=False)
        .str.replace("--", "", regex=False)
        .str.strip()
    )
    s = s.replace({"": None, "None": None, "nan": None})
    return pd.to_numeric(s, errors="coerce")


def _load_closed_positions(path: Path) -> pd.DataFrame:
    lines = _read_lines(path)

    header_idx = next((i for i, l in enumerate(lines) if l.strip().startswith("Symbol,Quantity,")), None)
    if header_idx is None:
        raise ValueError(f"No closed-positions header found in {path}")

    footer_idx = next(
        (i for i, l in enumerate(lines[header_idx + 1 :], start=header_idx + 1) if l.startswith("Totals,") or l.startswith("Disclosure")),
        len(lines),
    )

    csv_text = "".join(lines[header_idx:footer_idx])
    df = pd.read_csv(StringIO(csv_text), dtype=str)
    df = df.dropna(how="all")

    rename_map = {
        "$ Cost": "cost",
        "$ Proceeds": "proceeds",
        "$ Short-term G/L": "st_gl",
        "$ Long-term G/L": "lt_gl",
        "$ Total G/L": "total_gl",
        "Avg Cost": "avg_cost",
        "Avg Proceeds": "avg_proceeds",
        "Last": "last",
        "Account": "account",
        "Description": "description",
        "Quantity": "quantity",
        "Symbol": "symbol",
    }
    df = df.rename(columns=rename_map)

    for col in ["quantity", "cost", "proceeds", "st_gl", "lt_gl", "total_gl", "avg_cost", "avg_proceeds", "last"]:
        if col in df.columns:
            df[col] = _to_num(df[col])

    df["symbol"] = df["symbol"].astype(str).str.strip()
    df["ticker"] = df["symbol"].apply(_extract_ticker)
    df["sector"] = df["ticker"].apply(get_sector)

    sym = df["symbol"].apply(_parse_option_symbol)
    df["expiry"] = sym.apply(lambda x: x["expiry"])
    df["opt_type"] = sym.apply(lambda x: x["opt_type"])
    df["strike"] = sym.apply(lambda x: x["strike"])

    desc = df.get("description", pd.Series(index=df.index, dtype=str)).fillna("").str.upper()
    df.loc[df["opt_type"].isna() & desc.str.contains(" PUT "), "opt_type"] = "PUT"
    df.loc[df["opt_type"].isna() & desc.str.contains(" CALL "), "opt_type"] = "CALL"

    file_year = _parse_year_from_filename(path)
    df["year"] = file_year
    df["date"] = pd.NaT
    df["action_type"] = "CLOSED_POSITION"
    df["is_correction"] = False
    df["mode"] = df.apply(lambda r: _mode_closed(r.get("opt_type"), r.get("ticker")), axis=1)
    df["dte"] = None
    df["dte_category"] = None
    df["source_file"] = path.name
    df["dataset_type"] = "closed_positions"

    return df


def _categorize_action(action: str) -> str:
    a = str(action).upper()
    if "CORR DESCRIPTION" in a or "CXL DESCRIPTION" in a:
        return "CORRECTION"
    if "SOLD OPENING" in a:
        return "SELL_OPEN"
    if "BOUGHT CLOSING" in a:
        return "BUY_CLOSE"
    if "BOUGHT OPENING" in a:
        return "BUY_OPEN"
    if "SOLD CLOSING" in a:
        return "SELL_CLOSE"
    if "EXPIRED" in a:
        return "EXPIRED"
    if "ASSIGNED" in a:
        return "ASSIGNED"
    if "YOU SOLD" in a:
        return "STOCK_SELL"
    if "YOU BOUGHT" in a:
        return "STOCK_BUY"
    return "OTHER"


def _get_opt_type(action: str) -> str | None:
    a = str(action).upper()
    if " PUT " in a or a.endswith("PUT"):
        return "PUT"
    if " CALL " in a or a.endswith("CALL"):
        return "CALL"
    return None


def _dte_category(dte: float) -> str:
    if pd.isna(dte):
        return "unknown"
    if dte <= 7:
        return "0-7 DTE"
    if dte <= 14:
        return "8-14 DTE"
    if dte <= 45:
        return "15-45 DTE"
    if dte <= 90:
        return "46-90 DTE"
    if dte <= 180:
        return "91-180 DTE"
    if dte <= 365:
        return "181-365 DTE"
    return "365+ DTE"


def _mode_activity(opt_type: str | None, dte: float) -> str:
    if pd.isna(opt_type) or pd.isna(dte):
        return "unknown"
    if opt_type == "PUT":
        if dte <= 14:
            return "A"
        if dte >= 365:
            return "B"
        return "C"
    if opt_type == "CALL":
        return "CC"
    return "unknown"


def _load_activity(path: Path) -> pd.DataFrame:
    lines = _read_lines(path)
    header_idx = next((i for i, l in enumerate(lines) if "Run Date" in l), None)
    if header_idx is None:
        raise ValueError(f"No 'Run Date' header found in {path}")

    footer_idx = next((i for i, l in enumerate(lines) if "The data and information" in l), len(lines))
    data = "".join(lines[header_idx:footer_idx])
    df = pd.read_csv(StringIO(data), dtype=str)
    df = df.dropna(how="all")
    df = df[df["Run Date"].notna() & (df["Run Date"].astype(str).str.strip() != "")]

    df["date"] = pd.to_datetime(df["Run Date"], errors="coerce")
    for col in ["Price ($)", "Quantity", "Commission ($)", "Fees ($)", "Amount ($)", "Cash Balance ($)"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    df["action_type"] = df["Action"].fillna("").apply(_categorize_action)
    df["opt_type_action"] = df["Action"].fillna("").apply(_get_opt_type)
    df["ticker"] = df["Symbol"].apply(_extract_ticker)
    df["sector"] = df["ticker"].apply(get_sector)

    sym = df["Symbol"].apply(_parse_option_symbol)
    df["expiry"] = sym.apply(lambda x: x["expiry"])
    df["opt_type"] = sym.apply(lambda x: x["opt_type"])
    df["strike"] = sym.apply(lambda x: x["strike"])

    mask = df["opt_type"].isna() & df["opt_type_action"].notna()
    df.loc[mask, "opt_type"] = df.loc[mask, "opt_type_action"]

    df["dte"] = (df["expiry"] - df["date"]).dt.days
    df["dte_category"] = df["dte"].apply(_dte_category)
    df["mode"] = df.apply(lambda r: _mode_activity(r["opt_type"], r["dte"]) if r["action_type"] == "SELL_OPEN" else None, axis=1)
    df["is_correction"] = df["action_type"] == "CORRECTION"
    df["year"] = df["date"].dt.year
    df["source_file"] = path.name
    df["dataset_type"] = "activity"
    return df


def load_persona(persona: str, data_dir: str = "tradingData") -> pd.DataFrame:
    root = Path(data_dir)
    dirs = sorted([d for d in root.iterdir() if d.is_dir()]) if persona.lower() == "all" else [root / persona]

    frames = []
    for d in dirs:
        if not d.exists():
            print(f"  [WARN] Not found: {d}")
            continue
        for csv_path in sorted(d.glob("*.csv")):
            print(f"  Loading {csv_path.relative_to(root)}")
            fmt = _detect_format(csv_path)
            raw = _load_closed_positions(csv_path) if fmt == "closed_positions" else _load_activity(csv_path)
            raw["persona"] = d.name
            frames.append(raw)

    if not frames:
        raise FileNotFoundError(f"No data loaded for persona '{persona}'")

    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    df = load_persona("Arjuna")
    print(df[["dataset_type", "source_file"]].value_counts().to_string())
    print(df.head(3).to_string(index=False))
