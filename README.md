# AIHF — AI Hedge Fund Trading Analysis Pipeline

A repeatable options trading analytics pipeline built on real Fidelity brokerage data.
Supports multiple trader personas, multi-year history, and structured strategy classification.

---

## Project Structure

```
AIHF/
├── src/
│   ├── __init__.py
│   ├── parser.py        # Fidelity CSV → structured DataFrame
│   └── analyzer.py      # Trade matching, P&L, win rates
├── tradingData/
│   ├── Arjuna/          # 2024, 2025, 2026 YTD CSVs
│   └── Sundar/          # Drop CSVs here when ready
├── output/              # Generated CSVs (git-ignored)
├── run.py               # CLI entry point
├── README.md
└── ROADMAP.md
```

---

## Setup

```bash
pip install pandas numpy yfinance
```

---

## Usage

```bash
# Analyze one persona (all years)
python run.py --persona Arjuna

# Filter to a specific year
python run.py --persona Arjuna --year 2025

# Compare all personas side-by-side
python run.py --persona all --compare

# Save results to output/ as CSVs
python run.py --persona Arjuna --output csv
```

---

## Strategy Modes

Every `SELL_OPEN` trade is automatically classified:

| Mode | Option Type | DTE at Entry | Thesis |
|------|-------------|--------------|--------|
| A    | PUT         | 0–14 days    | IV fade / theta bleed on short-dated puts after volatility spikes |
| B    | PUT         | 365+ days    | Quality at a discount — get paid to set a limit buy 20-30% OTM |
| C    | PUT         | 15–364 days  | Structured income — mid-range premium collection |
| CC   | CALL        | any          | Covered calls written against stock positions |

---

## Ticker Universe

Defined in `src/parser.py → UNIVERSE`. Edit freely to add tickers.

| Sector | Tickers |
|--------|---------|
| tech_core | NVDA, AMD, GOOGL, META, AMZN, AAPL, TSLA, ORCL, MSFT, CRM |
| tech_growth | CRWV, HOOD, PLTR, RDDT, FIG |
| leveraged | TQQQ, SOXL, UPRO, SPXL |
| long_term_quality | COST, UNH, BRK, V, MA, JPM, WMT |
| crypto_proxy | IBIT, MSTR, COIN |

---

## Current Results (Arjuna, Apr 2024 – Mar 2026)

```
Total closed trades:     469
Overall win rate:        64.6%
Total premium collected: $315,246
Net P&L (closed trades): -$54,268  ← includes unrealized losses on open Mode B positions

BY MODE:
  CC (covered calls)   187 trades   79.4% win   $126,737 premium
  B  (365+ DTE puts)    64 trades   64.1% win    $96,977 premium
  C  (mid-range)        87 trades   56.3% win    $56,309 premium
  A  (0-14 DTE puts)   131 trades   69.0% win    $22,485 premium

TOP TICKERS:
  NVDA    198 trades   65.8% win   $170,017 premium
  META     14 trades   57.1% win    $32,430 premium
  PLTR     34 trades   43.3% win    $21,651 premium   ← watch this
  CRWV     24 trades   88.9% win     $9,991 premium
  TQQQ     38 trades   90.6% win     $8,477 premium

OPEN POSITIONS (as of last run):
  GOOGL B PUT $320  exp 2027-03-19   1 contract   $4,454 premium
  CRWV  B PUT $60   exp 2027-06-17   1 contract   $1,804 premium
  CRWV  B PUT $55   exp 2027-06-17   2 contracts  $2,933 premium
  HOOD  B PUT $50   exp 2027-03-19   1 contract     $839 premium
  ORCL  C PUT $145  exp 2027-01-15   1 contract   $1,854 premium
  NVDA  CC CALL $185 exp 2026-03-06  1 contract     $149 premium
  HOOD  CC CALL $83  exp 2026-03-06  5 contracts    $702 premium
```

---

## Adding a New Persona (Sundar)

1. Create `tradingData/Sundar/`
2. Export CSVs from Fidelity (same format — Activity & Orders → Download)
3. Drop files in the folder (any years, filenames don't matter)
4. Run `python run.py --persona Sundar`
5. Run `python run.py --persona all --compare` for side-by-side

---

## Note on Net P&L

The pipeline matches SELL_OPEN → BUY_CLOSE / EXPIRED / ASSIGNED using FIFO queuing.
Net P&L reflects realized premium minus cost-to-close on closed trades only.
Assigned positions are flagged but P&L is not computed (stock was received at strike).
Open positions show premium collected but no close price yet.
