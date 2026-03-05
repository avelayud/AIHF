# Strategy Identified

Data source: `trade_analyzer/data/Arjuna`

## Portfolio Truth Metrics
- Resolved option trades: **116**
- Win rate (WIN + EXPIRED_WIN only): **87.1%**
- Premium collected (resolved): **$228,088**
- Realized net P&L (resolved): **-$4,303**
- Average return per trade (net_pnl / premium): **65.79%**
- Average return on notional (net_pnl / strike notional): **-0.577%**

## Core Strategy Breakdown

| Strategy | Transactions | Win Rate | Premium Net | Realized P&L | Avg Return % | Avg Notional % | Avg Hold Days |
|---|---:|---:|---:|---:|---:|---:|---:|
| NVDA Wheel Engine | 64 | 87.5% | $4,212 | $4,212 | 97.34% | 0.093% | — |
| Covered Call Overlay | 42 | 83.3% | $4,296 | $4,296 | 105.45% | 0.202% | — |

## Mode Summary

| Mode | Trades | Win Rate | Premium | Net P&L | Avg P&L % | Avg Notional % |
|---|---:|---:|---:|---:|---:|---:|
| STOCK | 17 | 70.6% | $115,058 | -$13,147 | -5.76% | -5.757% |
| CC | 42 | 83.3% | $56,876 | $4,296 | 105.45% | 0.202% |
| PUT | 57 | 94.7% | $56,155 | $4,547 | 61.14% | 0.394% |

## Key Learnings
- Top premium ticker is **NVDA** with $114,135 (50.0% of premium).
- Top ticker win rate: **87.9%** across 66 trades.
- Win rate excludes ASSIGNED events to avoid distorting trade quality metrics.

## Suggestions
- NVDA drives 50.0% of premium; treat it as a dedicated monitoring stream rather than a generic scanner target.
- Covered calls generated $56,876 across 42 trades; keep call-writing as its own signal path tied to stock ownership.
