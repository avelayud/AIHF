# AIHF Roadmap

This file is the source of truth for what's built, what's next, and where the product is going.
Update it as features ship. Claude Code sessions should read this first for context.

---

## What's Built ✅

### Phase 0 — Data Pipeline (COMPLETE)
- `src/parser.py` — Loads any Fidelity CSV export for any persona
  - Strips BOM, blank rows, Fidelity footer disclaimers automatically
  - Parses option symbols: ticker, expiry, type (P/C), strike (including fractional e.g. $177.5)
  - Classifies every action: SELL_OPEN, BUY_CLOSE, EXPIRED, ASSIGNED, STOCK_BUY, CORRECTION, etc.
  - Tags mode (A/B/C/CC), DTE category, sector from ticker universe
  - Handles correction/cancellation pairs (CORR/CXL) — flagged, excluded from analysis
  - Multi-persona: `load_persona("Arjuna")`, `load_persona("all")`
  - Multi-year: auto-discovers all CSVs in persona folder

- `src/analyzer.py` — Trade matching and P&L
  - FIFO queue matching: SELL_OPEN → BUY_CLOSE / EXPIRED / ASSIGNED
  - Handles partial closes, same-day scaling (multiple sells, same strike/expiry)
  - Computes per-trade: premium_collected, cost_to_close, net_pnl, pnl_pct, hold_days, outcome
  - Outcomes: WIN, LOSS, EXPIRED_WIN, ASSIGNED, OPEN, ORPHAN_CLOSE
  - `summarize(trades, by=[...])` — group stats by any combination of columns
  - Win rate, avg P&L, avg hold days, max win/loss per group

- `run.py` — CLI entry point
  - `python run.py --persona Arjuna`
  - `python run.py --persona Arjuna --year 2025`
  - `python run.py --persona all --compare`
  - `python run.py --persona Arjuna --output csv` → saves to `output/`

---

## What's Next 🔨

### Phase 1 — Trade Enrichment (NEXT)
**File:** `src/enricher.py`

For each closed trade, fetch from yfinance:
- `underlying_price_at_entry` — closing price on entry_date
- `underlying_price_at_close` — closing price on close_date (or expiry_date if expired)
- `otm_pct` — how far OTM the strike was at entry: `(underlying - strike) / underlying * 100`
- `underlying_move_pct` — how much the stock moved between entry and close
- `moved_in_our_favor` — bool: for puts, did stock go UP? For calls, did stock go DOWN?
- `iv_context` — placeholder for IVR data (add later)

Key questions this answers:
- What OTM% do we typically enter at per mode?
- Do we actually enter after >4% moves (the core thesis)?
- What was the stock doing in the 5 days before entry?
- Did the stock recover after we sold puts (validating the mean-reversion thesis)?

Implementation notes:
- Use `yfinance.download(ticker, start, end)` — batch tickers to avoid rate limits
- Cache results locally in `output/price_cache.parquet` — don't re-fetch on every run
- Handle weekends/holidays: use nearest prior trading day
- Add `--enrich` flag to run.py: `python run.py --persona Arjuna --enrich`

### Phase 2 — Pattern Analysis
**File:** `src/patterns.py`

Using the enriched dataset, identify:
- Entry conditions that predict wins vs losses (OTM%, DTE, IV context, recent move)
- Best performing (ticker, mode) combinations
- Optimal hold duration per mode (at what % of max profit do we typically close?)
- Correlation between underlying move size at entry and premium captured
- Rolling win rate over time (are we improving?)
- Worst drawdown periods and what triggered them

This is the "training data" for the AI signal system later.

### Phase 3 — Dashboard (React/Vite)
**File:** `dashboard/` (separate frontend)

Local web app that reads from `output/*.csv` and visualizes:
- Equity curve (cumulative P&L over time)
- Win rate by mode, ticker, year, DTE bucket
- Open positions table with live P&L (yfinance current price)
- Trade log with full details — filterable by ticker, mode, outcome, year
- Monthly premium collected bar chart
- Pattern heatmap: (ticker × mode) win rate grid
- Persona comparison view (Arjuna vs Sundar side-by-side)

Tech: React + Vite + Recharts + TailwindCSS
Deploy: Vercel (later) — run locally for now with `npm run dev`

### Phase 4 — Signal Engine (AI Layer)
**Files:** `src/signals/` (multiple modules)

Six independent signal modules, each outputting score ∈ [-1, +1]:
1. `technical.py` — RSI, MACD, Bollinger, ATR, VWAP, trend regime
2. `options_flow.py` — Put/call ratio, IVR, unusual activity, GEX, skew  ← YOUR MOAT
3. `news_sentiment.py` — NewsAPI + Claude API analysis, earnings NLP
4. `social_sentiment.py` — Reddit PRAW + FinBERT/Claude
5. `macro_regime.py` — VIX, sector ETFs, Fed rates, yield curve
6. `sec_filings.py` — EDGAR 10-K/8-K/13F + Claude extraction

Combiner: `final_score = Σ(wᵢ × signalᵢ) × macro_gate`
Risk manager: Kelly sizing, max drawdown circuit breaker, VIX-gated scaling

**Key classifier:** News permanence scorer (Claude API)
- Input: news headline + ticker
- Output: permanence_score 1-10, catalyst_type, confidence, reasoning
- Routes trade: low permanence → Mode A, high permanence → Mode B

### Phase 5 — Live Paper Trading
**Files:** `src/execution/`

- Universe scanner: daily scan, flag volume >2x OR price >3% OR news event
- Trade proposal generator: signal scores + AI reasoning per proposal
- Alpaca Markets API (paper first, live later)
- Position sync + real-time P&L

### Phase 6 — Deploy (Vercel + API)
- Dashboard on Vercel
- FastAPI backend for signals + proposals
- Scheduled jobs: market hours scanning every 30 min

---

## Architecture Principles

- **Persona-first:** every function takes `persona` as first arg. Sundar's data plugs in with zero code changes.
- **Incremental enrichment:** price cache means yfinance only called for new trades.
- **No lookahead bias:** enricher only uses prices available at close_date, never future data.
- **Mode classification is immutable:** assigned at parse time from DTE + option type, never overridden.
- **CSV as source of truth:** raw Fidelity exports are never modified. All derived data lives in `output/`.

---

## Data Flow

```
Fidelity CSV exports
        ↓
  src/parser.py          → structured DataFrame (1010 rows across 3 years)
        ↓
  src/analyzer.py        → matched trades DataFrame (469 closed + 8 open)
        ↓
  src/enricher.py        → + price context, OTM%, move data        [NEXT]
        ↓
  src/patterns.py        → win condition analysis, entry signals    [PHASE 2]
        ↓
  output/*.csv           → feeds dashboard + signal training
        ↓
  dashboard/             → React visualization                      [PHASE 3]
        ↓
  src/signals/           → live AI signal engine                    [PHASE 4]
```

---

## Claude Code Session Starter

When starting a new Claude Code session, use this prompt:

> "Read README.md and ROADMAP.md first. Then run `python run.py --persona Arjuna` to see current output.
> We are on Phase 1 — building `src/enricher.py`. It should fetch yfinance historical prices for each
> closed trade, calculate OTM% at entry, underlying move by close, and whether the move was in our favor.
> Add an `--enrich` flag to run.py. Cache prices in `output/price_cache.parquet`. Write real code directly
> to the filesystem."

---

## Key Numbers to Know

- 469 closed trades across 2024–2026 (Arjuna)
- 64.6% overall win rate
- $315K total premium collected
- Covered calls (CC): best mode at 79.4% win rate
- PLTR: biggest concern — 43.3% win rate on $21K premium
- NVDA: 58% of all premium, core vehicle for all modes
- Net P&L negative due to Mode B positions marked against current prices
  (these are long-dated — expected to recover / expire profitable)

---

## Repo
https://github.com/avelayud/AIHF

## Local Path
/Users/arjunavelayudam/Desktop/Coding/AIHF
