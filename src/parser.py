"""
parser.py — Fidelity CSV → clean structured DataFrame

Handles:
  - Multi-year files (2024, 2025, 2026 YTD)
  - Multi-persona dirs (Arjuna/, Sundar/, ...)
  - BOM, blank rows, Fidelity footer disclaimers
  - CORR/CXL correction pairs (flagged, not double-counted)
"""

import re
import pandas as pd
from io import StringIO
from pathlib import Path


# ── Ticker Universe ────────────────────────────────────────────────────────────

UNIVERSE = {
    "tech_core":         ["NVDA", "AMD", "GOOGL", "GOOG", "META", "AMZN",
                          "AAPL", "TSLA", "ORCL", "MSFT", "CRM"],
    "tech_growth":       ["CRWV", "HOOD", "PLTR", "RDDT", "FIG"],
    "leveraged":         ["TQQQ", "SOXL", "UPRO", "SPXL", "QQQ", "SPY"],
    "long_term_quality": ["COST", "UNH", "BRK", "BRKB", "V", "MA", "JPM", "WMT"],
    "crypto_proxy":      ["IBIT", "MSTR", "COIN"],
}

TICKER_SECTOR: dict[str, str] = {}
for _sector, _tickers in UNIVERSE.items():
    for _t in _tickers:
        TICKER_SECTOR[_t] = _sector


def get_sector(ticker: str) -> str:
    return TICKER_SECTOR.get(ticker.upper(), "other")


# ── File Loading ───────────────────────────────────────────────────────────────

def _load_raw(path: Path) -> pd.DataFrame:
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        lines = f.readlines()

    header_idx = next(
        (i for i, l in enumerate(lines) if "Run Date" in l), None
    )
    if header_idx is None:
        raise ValueError(f"No 'Run Date' header found in {path}")

    footer_idx = next(
        (i for i, l in enumerate(lines) if "The data and information" in l),
        len(lines),
    )

    data = "".join(lines[header_idx:footer_idx])
    df = pd.read_csv(StringIO(data), dtype=str)
    df = df.dropna(how="all")
    df = df[df["Run Date"].notna() & (df["Run Date"].str.strip() != "")]
    df["source_file"] = path.name
    return df


def load_persona(persona: str, data_dir: str = "tradingData") -> pd.DataFrame:
    """
    Load + parse all CSV files for a persona.

    Args:
        persona:  "Arjuna" | "Sundar" | "all"
        data_dir: folder containing persona sub-dirs
    """
    root = Path(data_dir)

    if persona.lower() == "all":
        dirs = sorted([d for d in root.iterdir() if d.is_dir()])
    else:
        dirs = [root / persona]

    frames = []
    for d in dirs:
        if not d.exists():
            print(f"  [WARN] Not found: {d}")
            continue
        for csv_path in sorted(d.glob("*.csv")):
            print(f"  Loading {csv_path.relative_to(root)}")
            raw = _load_raw(csv_path)
            raw["persona"] = d.name
            frames.append(raw)

    if not frames:
        raise FileNotFoundError(f"No data loaded for persona '{persona}'")

    df = pd.concat(frames, ignore_index=True)
    return _parse(df)


# ── Field Parsing ──────────────────────────────────────────────────────────────

def _categorize_action(action: str) -> str:
    a = str(action).upper()
    if "CORR DESCRIPTION" in a or "CXL DESCRIPTION" in a:
        return "CORRECTION"          # corrected/cancelled entries — exclude from analysis
    if "SOLD OPENING" in a:          return "SELL_OPEN"
    if "BOUGHT CLOSING" in a:        return "BUY_CLOSE"
    if "BOUGHT OPENING" in a:        return "BUY_OPEN"
    if "SOLD CLOSING" in a:          return "SELL_CLOSE"
    if "EXPIRED" in a:               return "EXPIRED"
    if "ASSIGNED" in a:              return "ASSIGNED"
    if "DIVIDEND" in a:              return "DIVIDEND"
    if "REINVESTMENT" in a:          return "REINVESTMENT"
    if "ELECTRONIC FUNDS" in a:      return "TRANSFER"
    if "TRANSFERRED" in a:           return "TRANSFER"
    if "YOU SOLD" in a:              return "STOCK_SELL"
    if "YOU BOUGHT" in a:            return "STOCK_BUY"
    return "OTHER"


def _get_opt_type(action: str) -> str:
    a = str(action).upper()
    if " PUT " in a or a.endswith("PUT"):   return "PUT"
    if " CALL " in a or a.endswith("CALL"): return "CALL"
    return None


def _extract_ticker(symbol: str) -> str:
    if pd.isna(symbol): return ""
    s = str(symbol).strip().lstrip("-")
    m = re.match(r"^([A-Z]+)", s)
    return m.group(1) if m else s


def _parse_option_symbol(symbol: str) -> dict:
    """
    Parse '-NVDA260116P190' → {expiry: '2026-01-16', opt_type: 'PUT', strike: 190.0}
    Also handles fractional strikes like '-NVDA251212P177.5'
    """
    result = {"expiry": pd.NaT, "opt_type": None, "strike": None}
    if pd.isna(symbol): return result
    s = str(symbol).replace(" ", "").replace("-", "")
    m = re.search(r"(\d{6})([CP])(\d+\.?\d*)", s)
    if not m: return result
    ds, cp, strike_s = m.group(1), m.group(2), m.group(3)
    try:
        result["expiry"] = pd.Timestamp(
            year=int("20" + ds[:2]), month=int(ds[2:4]), day=int(ds[4:6])
        )
    except Exception:
        pass
    result["opt_type"] = "PUT" if cp == "P" else "CALL"
    try:
        result["strike"] = float(strike_s)
    except Exception:
        pass
    return result


def _dte_category(dte: float) -> str:
    if pd.isna(dte):   return "unknown"
    if dte <= 7:        return "0-7 DTE"
    if dte <= 14:       return "8-14 DTE"
    if dte <= 45:       return "15-45 DTE"
    if dte <= 90:       return "46-90 DTE"
    if dte <= 180:      return "91-180 DTE"
    if dte <= 365:      return "181-365 DTE"
    return "365+ DTE"


def _mode(opt_type: str, dte: float) -> str:
    """
    Classify trade mode based on option type and DTE at entry.
    Mode A  = short-dated put (0-14 DTE) — IV fade / theta bleed
    Mode B  = long-dated put (365+ DTE)  — quality at discount
    Mode C  = structured income (15-364 DTE puts + most calls)
    CC      = covered call written against a stock position
    """
    if pd.isna(opt_type) or pd.isna(dte):
        return "unknown"
    if opt_type == "PUT":
        if dte <= 14:   return "A"
        if dte >= 365:  return "B"
        return "C"
    if opt_type == "CALL":
        return "CC"   # covered call / cap upside
    return "unknown"


def _parse(df: pd.DataFrame) -> pd.DataFrame:
    """Add all structured columns to the raw DataFrame."""

    # Dates
    df["date"] = pd.to_datetime(df["Run Date"], errors="coerce")

    # Numerics
    for col in ["Price ($)", "Quantity", "Commission ($)", "Fees ($)",
                 "Amount ($)", "Cash Balance ($)"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    # Action classification
    df["action_type"] = df["Action"].fillna("").apply(_categorize_action)

    # Option type from Action text (more reliable than symbol for some rows)
    df["opt_type_action"] = df["Action"].fillna("").apply(_get_opt_type)

    # Underlying ticker
    df["ticker"] = df["Symbol"].apply(_extract_ticker)

    # Sector
    df["sector"] = df["ticker"].apply(get_sector)

    # Parse option symbol fields
    sym_parsed = df["Symbol"].apply(_parse_option_symbol)
    df["expiry"]   = sym_parsed.apply(lambda x: x["expiry"])
    df["opt_type"] = sym_parsed.apply(lambda x: x["opt_type"])
    df["strike"]   = sym_parsed.apply(lambda x: x["strike"])

    # Fill opt_type from action text where symbol parse failed
    mask = df["opt_type"].isna() & df["opt_type_action"].notna()
    df.loc[mask, "opt_type"] = df.loc[mask, "opt_type_action"]

    # DTE at entry
    df["dte"] = (df["expiry"] - df["date"]).dt.days
    df["dte_category"] = df["dte"].apply(_dte_category)

    # Mode classification (only meaningful for SELL_OPEN)
    df["mode"] = df.apply(
        lambda r: _mode(r["opt_type"], r["dte"])
        if r["action_type"] == "SELL_OPEN" else None,
        axis=1,
    )

    # Is this a correction/cancellation entry?
    df["is_correction"] = df["action_type"] == "CORRECTION"

    # Year
    df["year"] = df["date"].dt.year

    return df


# ── Quick sanity check ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    df = load_persona("Arjuna", data_dir="tradingData")
    print(f"\nTotal rows:   {len(df)}")
    print(f"Action types:\n{df['action_type'].value_counts().to_string()}")
    print(f"\nSell-open by mode:\n{df[df['action_type']=='SELL_OPEN']['mode'].value_counts().to_string()}")
    print(f"\nYears: {sorted(df['year'].dropna().unique().astype(int).tolist())}")
    print(f"\nTop tickers (sell_open):")
    so = df[df["action_type"] == "SELL_OPEN"]
    print(so["ticker"].value_counts().head(15).to_string())
