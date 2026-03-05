"""
build_dashboard.py — Generate dashboard.html from analysis outputs

Run: python build_dashboard.py
Output: output/dashboard.html — open directly in browser, no server needed
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.analyzer import analyze
from src.patterns import build_features, discover_strategies

DATA_DIR = Path("tradingData")
OUTPUT = Path("output")
CLOSED_OUTCOMES = ["WIN", "LOSS", "EXPIRED_WIN", "ASSIGNED"]
WIN_OUTCOMES = ["WIN", "EXPIRED_WIN"]


def has_persona_data(persona: str, data_dir: Path = DATA_DIR) -> bool:
    p = data_dir / persona
    return p.exists() and p.is_dir() and any(p.glob("*.csv"))


def _safe_float(v):
    return round(float(v), 2) if pd.notna(v) else None


def _safe_int(v):
    return int(v) if pd.notna(v) else None


def _trade_base(trades: pd.DataFrame) -> pd.DataFrame:
    closed = trades[trades["outcome"].isin(CLOSED_OUTCOMES)].copy()
    closed["won"] = closed["outcome"].isin(WIN_OUTCOMES)
    closed["month"] = closed["entry_date"].dt.to_period("M").astype(str)
    closed["year"] = closed["entry_date"].dt.year
    close_ref = closed["close_date"].where(closed["close_date"].notna(), closed["expiry_date"])
    closed["pnl_date"] = close_ref.where(close_ref.notna(), closed["entry_date"])
    return closed


def get_data(persona: str = "Arjuna") -> dict:
    trades, _ = analyze(persona)
    closed = _trade_base(trades)

    features = build_features(trades)
    strategies = discover_strategies(features)

    monthly = (
        closed.groupby("month", as_index=False)
        .agg(
            premium=("premium_collected", "sum"),
            trades=("trade_id", "count"),
            wins=("won", "sum"),
        )
        .sort_values("month")
    )
    monthly["win_rate"] = (monthly["wins"] / monthly["trades"] * 100).round(1)
    monthly["premium"] = monthly["premium"].round(0)

    mode_stats = (
        closed.groupby("mode", as_index=False)
        .agg(
            trades=("trade_id", "count"),
            premium=("premium_collected", "sum"),
            pnl=("net_pnl", "sum"),
            wins=("won", "sum"),
        )
        .sort_values("premium", ascending=False)
    )
    mode_stats["win_rate"] = (mode_stats["wins"] / mode_stats["trades"] * 100).round(1)
    mode_stats["premium"] = mode_stats["premium"].round(0)
    mode_stats["pnl"] = mode_stats["pnl"].round(0)

    ticker_stats = (
        closed.groupby(["ticker", "sector"], as_index=False)
        .agg(
            trades=("trade_id", "count"),
            premium=("premium_collected", "sum"),
            pnl=("net_pnl", "sum"),
            wins=("won", "sum"),
        )
        .sort_values("premium", ascending=False)
        .head(15)
    )
    ticker_stats["win_rate"] = (ticker_stats["wins"] / ticker_stats["trades"] * 100).round(1)
    ticker_stats["premium"] = ticker_stats["premium"].round(0)
    ticker_stats["pnl"] = ticker_stats["pnl"].round(0)

    equity = (
        closed.sort_values(["pnl_date", "entry_date"])
        .groupby("pnl_date", as_index=False)
        .agg(daily_pnl=("net_pnl", "sum"))
    )
    equity["cum_pnl"] = equity["daily_pnl"].cumsum().round(2)
    equity["date"] = equity["pnl_date"].dt.strftime("%Y-%m-%d")

    open_pos = trades[trades["outcome"] == "OPEN"].copy()

    trade_log = closed[
        [
            "trade_id",
            "persona",
            "year",
            "ticker",
            "mode",
            "opt_type",
            "strike",
            "dte",
            "contracts",
            "entry_date",
            "expiry_date",
            "hold_days",
            "premium_collected",
            "net_pnl",
            "outcome",
        ]
    ].copy()
    trade_log["entry_date"] = trade_log["entry_date"].dt.strftime("%Y-%m-%d")
    trade_log["expiry_date"] = trade_log["expiry_date"].apply(
        lambda x: x.strftime("%Y-%m-%d") if pd.notna(x) else None
    )
    trade_log["premium_collected"] = trade_log["premium_collected"].round(2)
    trade_log["net_pnl"] = trade_log["net_pnl"].round(2)
    trade_log["dte"] = trade_log["dte"].apply(_safe_int)
    trade_log["year"] = trade_log["year"].apply(_safe_int)

    open_json = []
    for _, r in open_pos.iterrows():
        open_json.append(
            {
                "ticker": r["ticker"],
                "mode": str(r["mode"]),
                "opt_type": str(r["opt_type"]),
                "strike": _safe_float(r["strike"]),
                "expiry_date": r["expiry_date"].strftime("%Y-%m-%d") if pd.notna(r["expiry_date"]) else None,
                "contracts": _safe_int(r["contracts"]),
                "premium_collected": _safe_float(r["premium_collected"]),
            }
        )

    strat_cols = [
        "strategy_name",
        "mode",
        "sector",
        "n_trades",
        "win_rate",
        "avg_premium",
        "avg_pnl",
        "confidence",
        "personas",
        "conditions",
    ]
    strat_data = []
    if len(strategies):
        for row in strategies[strat_cols].to_dict(orient="records"):
            conditions = row.get("conditions")
            if isinstance(conditions, str):
                try:
                    row["conditions"] = json.loads(conditions)
                except json.JSONDecodeError:
                    row["conditions"] = {"raw": conditions}
            strat_data.append(row)

    return {
        "persona": persona,
        "summary": {
            "total_trades": int(len(closed)),
            "win_rate": round(float(closed["won"].mean() * 100), 1) if len(closed) else 0,
            "total_premium": round(float(closed["premium_collected"].sum()), 0),
            "total_pnl": round(float(closed["net_pnl"].sum()), 0),
            "open_positions": int(len(open_pos)),
            "assignments": int((closed["outcome"] == "ASSIGNED").sum()),
        },
        "monthly": json.loads(monthly.to_json(orient="records", date_format="iso")),
        "modes": json.loads(mode_stats.to_json(orient="records")),
        "tickers": json.loads(ticker_stats.to_json(orient="records")),
        "strategies": strat_data,
        "trades": json.loads(trade_log.to_json(orient="records")),
        "open": open_json,
        "equity": json.loads(equity[["date", "daily_pnl", "cum_pnl"]].to_json(orient="records")),
    }


def _mode_summary(trades: pd.DataFrame, mode: str) -> dict:
    subset = trades[(trades["mode"] == mode) & trades["outcome"].isin(CLOSED_OUTCOMES)]
    wins = subset["outcome"].isin(WIN_OUTCOMES).sum()
    n = len(subset)
    return {
        "mode": mode,
        "trades": int(n),
        "win_rate": round(float((wins / n) * 100), 1) if n else 0,
        "premium": round(float(subset["premium_collected"].sum()), 0),
        "pnl": round(float(subset["net_pnl"].sum()), 0),
    }


def _summary_row(trades: pd.DataFrame) -> dict:
    closed = trades[trades["outcome"].isin(CLOSED_OUTCOMES)]
    wins = closed["outcome"].isin(WIN_OUTCOMES).sum()
    n = len(closed)
    return {
        "closed": int(n),
        "win_rate": round(float((wins / n) * 100), 1) if n else 0,
        "premium": round(float(closed["premium_collected"].sum()), 0),
        "pnl": round(float(closed["net_pnl"].sum()), 0),
    }


def build_head_to_head() -> dict:
    if not has_persona_data("Sundar"):
        return {
            "status": "pending",
            "message": "Sundar data pending",
            "summary": {},
            "modes": [],
        }

    try:
        arjuna_trades, _ = analyze("Arjuna")
        sundar_trades, _ = analyze("Sundar")
    except FileNotFoundError:
        return {
            "status": "pending",
            "message": "Sundar data pending",
            "summary": {},
            "modes": [],
        }

    a = _summary_row(arjuna_trades)
    s = _summary_row(sundar_trades)
    modes = []
    for mode in ["A", "B", "C", "CC"]:
        ma = _mode_summary(arjuna_trades, mode)
        ms = _mode_summary(sundar_trades, mode)
        modes.append(
            {
                "mode": mode,
                "arjuna_trades": ma["trades"],
                "sundar_trades": ms["trades"],
                "arjuna_win_rate": ma["win_rate"],
                "sundar_win_rate": ms["win_rate"],
                "arjuna_pnl": ma["pnl"],
                "sundar_pnl": ms["pnl"],
            }
        )

    return {
        "status": "ready",
        "message": "Head-to-head comparison from closed trade history",
        "summary": {
            "arjuna": a,
            "sundar": s,
        },
        "modes": modes,
    }


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;600;700&family=Space+Grotesk:wght@300;400;500;600;700&display=swap');
:root{--bg:#080c10;--surface:#0d1117;--card:#111820;--border:#1e2d3d;--accent:#00d4ff;--accent2:#00ff88;--accent3:#ff6b35;--warn:#ffaa00;--danger:#ff4466;--text:#e0eaf4;--muted:#5a7a94;--font-mono:'JetBrains Mono',monospace;--font-body:'Space Grotesk',sans-serif}
*{margin:0;padding:0;box-sizing:border-box}
body{background:var(--bg);color:var(--text);font-family:var(--font-body);min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(0,212,255,.03) 1px,transparent 1px),linear-gradient(90deg,rgba(0,212,255,.03) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0}
.app{position:relative;z-index:1;max-width:1400px;margin:0 auto;padding:0 24px 40px}
header{display:flex;align-items:center;justify-content:space-between;padding:24px 0 20px;border-bottom:1px solid var(--border);margin-bottom:28px;gap:16px;flex-wrap:wrap}
.logo{display:flex;align-items:baseline;gap:10px}
.logo-text{font-family:var(--font-mono);font-size:22px;font-weight:700;letter-spacing:4px;color:var(--accent);text-shadow:0 0 20px rgba(0,212,255,.4)}
.logo-sub{font-size:11px;color:var(--muted);letter-spacing:2px;text-transform:uppercase}
.header-right{display:flex;align-items:center;gap:12px}
.live-dot{width:8px;height:8px;border-radius:50%;background:var(--accent2);box-shadow:0 0 8px var(--accent2);animation:pulse 2s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.live-label{font-family:var(--font-mono);font-size:11px;color:var(--muted);letter-spacing:1px}
.persona-bar{display:flex;gap:6px;margin-bottom:20px;align-items:center;flex-wrap:wrap}
.persona-label{font-size:11px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-right:8px;font-family:var(--font-mono)}
.persona-btn{padding:6px 18px;border-radius:3px;border:1px solid var(--border);background:transparent;color:var(--muted);font-family:var(--font-mono);font-size:12px;font-weight:500;letter-spacing:1px;cursor:pointer;transition:all .15s}
.persona-btn:hover{border-color:var(--accent);color:var(--accent)}
.persona-btn.active{background:rgba(0,212,255,.1);border-color:var(--accent);color:var(--accent);box-shadow:0 0 12px rgba(0,212,255,.15)}
.persona-btn.pending{opacity:.45;cursor:not-allowed}
.persona-note{font-size:11px;color:var(--muted);font-style:italic;margin-left:8px}
.nav{display:flex;gap:0;margin-bottom:28px;border-bottom:1px solid var(--border);overflow-x:auto;white-space:nowrap;scrollbar-width:thin}
.nav-tab{padding:10px 18px;font-size:12px;font-weight:500;letter-spacing:1px;text-transform:uppercase;font-family:var(--font-mono);color:var(--muted);cursor:pointer;border-bottom:2px solid transparent;transition:all .15s;background:none;border-top:none;border-left:none;border-right:none;flex:0 0 auto}
.nav-tab:hover{color:var(--text)}
.nav-tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.panel{display:none}
.panel.active{display:block}
.cards{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin-bottom:24px}
.cards.head{grid-template-columns:repeat(4,1fr)}
.card{background:var(--card);border:1px solid var(--border);border-radius:4px;padding:16px 18px;position:relative;overflow:hidden;min-width:0}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--accent);opacity:.6}
.card.green::before{background:var(--accent2)}
.card.orange::before{background:var(--accent3)}
.card.warn::before{background:var(--warn)}
.card.danger::before{background:var(--danger)}
.card-label{font-family:var(--font-mono);font-size:10px;color:var(--muted);letter-spacing:1.5px;text-transform:uppercase;margin-bottom:8px}
.card-value{font-family:var(--font-mono);font-size:24px;font-weight:600;color:var(--text);line-height:1}
.card-value.accent{color:var(--accent)}
.card-value.green{color:var(--accent2)}
.card-value.warn{color:var(--warn)}
.card-value.danger{color:var(--danger)}
.card-sub{font-size:11px;color:var(--muted);margin-top:4px}
.chart-grid-2{display:grid;grid-template-columns:2fr 1fr;gap:16px;margin-bottom:16px}
.chart-grid-equal{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
.chart-box{background:var(--card);border:1px solid var(--border);border-radius:4px;padding:20px;min-width:0}
.chart-title{font-family:var(--font-mono);font-size:11px;color:var(--muted);letter-spacing:2px;text-transform:uppercase;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.chart-title::before{content:'';width:3px;height:12px;background:var(--accent);border-radius:2px;flex-shrink:0}
.canvas-wrap{position:relative;height:220px}
.canvas-wrap.tall{height:260px}
.canvas-wrap.med{height:200px}
.section-gap{margin-bottom:16px}
.data-table{width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:12px}
.data-table th{text-align:left;padding:8px 12px;color:var(--muted);font-size:10px;letter-spacing:1.5px;text-transform:uppercase;border-bottom:1px solid var(--border);font-weight:500}
.data-table td{padding:9px 12px;border-bottom:1px solid rgba(30,45,61,.5);color:var(--text);vertical-align:top}
.data-table tr:hover td{background:rgba(0,212,255,.03)}
.badge{display:inline-block;padding:2px 8px;border-radius:2px;font-size:10px;font-weight:600;letter-spacing:1px}
.badge-A{background:rgba(0,212,255,.15);color:var(--accent);border:1px solid rgba(0,212,255,.3)}
.badge-B{background:rgba(0,255,136,.15);color:var(--accent2);border:1px solid rgba(0,255,136,.3)}
.badge-C{background:rgba(255,107,53,.15);color:var(--accent3);border:1px solid rgba(255,107,53,.3)}
.badge-CC{background:rgba(255,170,0,.15);color:var(--warn);border:1px solid rgba(255,170,0,.3)}
.badge-high{background:rgba(0,255,136,.12);color:var(--accent2);border:1px solid rgba(0,255,136,.25)}
.badge-med{background:rgba(255,170,0,.12);color:var(--warn);border:1px solid rgba(255,170,0,.25)}
.badge-low{background:rgba(90,122,148,.15);color:var(--muted);border:1px solid rgba(90,122,148,.3)}
.pos{color:var(--accent2)}
.neg{color:var(--danger)}
.win-bar{display:flex;align-items:center;gap:8px}
.win-bar-track{flex:1;height:4px;background:rgba(255,255,255,.06);border-radius:2px;overflow:hidden;min-width:60px}
.win-bar-fill{height:100%;border-radius:2px;transition:width .4s ease}
.open-badge{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;background:rgba(0,212,255,.08);border:1px solid rgba(0,212,255,.2);border-radius:3px;font-family:var(--font-mono);font-size:11px;color:var(--accent)}
.open-dot{width:5px;height:5px;border-radius:50%;background:var(--accent2);animation:pulse 2s infinite}
.table-scroll{overflow-x:auto;max-width:100%}
.filter-row{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap}
.filter-select,.filter-input{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:3px;font-family:var(--font-mono);font-size:11px}
.filter-input{min-width:220px}
.mode-group{margin-bottom:18px}
.mode-label{font-family:var(--font-mono);font-size:11px;letter-spacing:1.2px;text-transform:uppercase;color:var(--accent);margin-bottom:8px}
.pending-box{padding:24px;border:1px dashed var(--border);border-radius:4px;color:var(--muted);font-family:var(--font-mono);text-align:center}
.conditions{font-size:11px;line-height:1.45;color:var(--muted);max-width:380px;white-space:normal}
@media(max-width:1200px){.cards{grid-template-columns:repeat(3,1fr)}.cards.head{grid-template-columns:repeat(2,1fr)}}
@media(max-width:900px){.cards,.cards.head{grid-template-columns:repeat(2,1fr)}.chart-grid-2,.chart-grid-equal{grid-template-columns:1fr}}
@media(max-width:580px){.app{padding:0 14px 24px}.cards,.cards.head{grid-template-columns:1fr}.card-value{font-size:21px}.filter-input{min-width:160px}}
</style>
"""

JS = r"""
<script>
let activePersona = 'Arjuna';
let charts = {};
const C = {accent:'#00d4ff',green:'#00ff88',orange:'#ff6b35',warn:'#ffaa00',danger:'#ff4466',muted:'#5a7a94'};
const MC = {A:'#00d4ff',B:'#00ff88',C:'#ff6b35',CC:'#ffaa00'};

function showTab(id, btn) {
  document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));
  document.querySelectorAll('.nav-tab').forEach(b=>b.classList.remove('active'));
  const panel = document.getElementById('panel-'+id);
  if (panel) panel.classList.add('active');
  btn.classList.add('active');
}

function setPersona(name, btn) {
  if (btn.classList.contains('pending')) return;
  activePersona = name;
  document.querySelectorAll('.persona-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  const d = DATA[name] || DATA['Arjuna'];
  renderAll(d);
}

function dc(id){if(charts[id]){charts[id].destroy();delete charts[id];}}
function fmtUsd(v){return v==null?'—':'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:0});}
function fmtPnl(n){
  if(n==null)return'—';
  const s=(n>=0?'+$':'-$')+Math.abs(n).toLocaleString('en-US',{maximumFractionDigits:0});
  return '<span class="'+(n>=0?'pos':'neg')+'">'+s+'</span>';
}
function wrColor(v){return v>=70?C.green:v<50?C.danger:C.warn;}

function renderAll(d) {
  document.getElementById('s-trades').textContent = d.summary.total_trades;
  document.getElementById('s-wr').textContent = d.summary.win_rate+'%';
  document.getElementById('s-prem').textContent = fmtUsd(d.summary.total_premium);
  document.getElementById('s-open').textContent = d.summary.open_positions;

  const pnl = d.summary.total_pnl;
  const pe = document.getElementById('s-pnl');
  pe.textContent = (pnl>=0?'+$':'-$')+Math.abs(pnl).toLocaleString();
  pe.className = 'card-value '+(pnl>=0?'green':'danger');

  renderMonthly(d); renderModes(d);
  renderWRModes(d); renderMonthlyWR(d);
  renderEquity(d);
  renderStrategies(d); renderTickers(d);
  renderTrades(d); renderPositions(d);
  populateTradeYears(d);
}

function renderMonthly(d) {
  dc('monthly');
  const el = document.getElementById('chart-monthly');
  if (!el) return;
  charts['monthly'] = new Chart(el.getContext('2d'), {
    type:'bar',
    data:{labels:d.monthly.map(r=>r.month),datasets:[{
      data:d.monthly.map(r=>r.premium),
      backgroundColor:d.monthly.map(r=>r.premium>10000?'rgba(0,212,255,.7)':'rgba(0,212,255,.35)'),
      borderColor:'rgba(0,212,255,.8)',borderWidth:1,borderRadius:2
    }]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:9},maxRotation:45},grid:{color:'rgba(30,45,61,.4)'}},
        y:{ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:10},callback:v=>'$'+(v/1000).toFixed(0)+'k'},grid:{color:'rgba(30,45,61,.4)'}}
      }}
  });
}

function renderModes(d) {
  dc('modes');
  const el = document.getElementById('chart-modes');
  if (!el) return;
  charts['modes'] = new Chart(el.getContext('2d'), {
    type:'doughnut',
    data:{labels:d.modes.map(r=>'Mode '+r.mode),datasets:[{
      data:d.modes.map(r=>r.premium),
      backgroundColor:d.modes.map(r=>(MC[r.mode]||C.muted)+'99'),
      borderColor:d.modes.map(r=>MC[r.mode]||C.muted),
      borderWidth:2,hoverOffset:6
    }]},
    options:{responsive:true,maintainAspectRatio:false,cutout:'62%',
      plugins:{
        legend:{position:'right',labels:{color:'#e0eaf4',font:{family:'JetBrains Mono',size:11},padding:14}},
        tooltip:{callbacks:{label:c=>' '+fmtUsd(c.raw)+' ('+c.label+')'}}
      }}
  });
}

function renderWRModes(d) {
  dc('wr-mode');
  const el = document.getElementById('chart-wr-mode');
  if (!el) return;
  charts['wr-mode'] = new Chart(el.getContext('2d'), {
    type:'bar',
    data:{labels:d.modes.map(r=>'Mode '+r.mode),datasets:[{
      data:d.modes.map(r=>r.win_rate),
      backgroundColor:d.modes.map(r=>(MC[r.mode]||C.muted)+'aa'),
      borderColor:d.modes.map(r=>MC[r.mode]||C.muted),
      borderWidth:1,borderRadius:3
    }]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:11}},grid:{display:false}},
        y:{min:0,max:100,ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:10},callback:v=>v+'%'},grid:{color:'rgba(30,45,61,.4)'}}
      }}
  });
}

function renderMonthlyWR(d) {
  dc('monthly-wr');
  const el = document.getElementById('chart-monthly-wr');
  if (!el) return;
  charts['monthly-wr'] = new Chart(el.getContext('2d'), {
    type:'line',
    data:{labels:d.monthly.map(r=>r.month),datasets:[{
      data:d.monthly.map(r=>r.win_rate),
      borderColor:C.green,backgroundColor:'rgba(0,255,136,.06)',
      borderWidth:2,pointRadius:3,pointBackgroundColor:C.green,tension:.3,fill:true
    }]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:9},maxRotation:45},grid:{color:'rgba(30,45,61,.4)'}},
        y:{min:0,max:100,ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:10},callback:v=>v+'%'},grid:{color:'rgba(30,45,61,.4)'}}
      }}
  });
}

function renderEquity(d) {
  dc('equity');
  const el = document.getElementById('chart-equity');
  if (!el) return;
  charts['equity'] = new Chart(el.getContext('2d'), {
    type:'line',
    data:{labels:d.equity.map(r=>r.date),datasets:[{
      data:d.equity.map(r=>r.cum_pnl),
      borderColor:C.accent,
      backgroundColor:'rgba(0,212,255,.08)',
      borderWidth:2,
      pointRadius:0,
      tension:.2,
      fill:true
    }]},
    options:{responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{
        x:{ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:9},maxRotation:45},grid:{color:'rgba(30,45,61,.35)'}},
        y:{ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:10},callback:v=>'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:0})},grid:{color:'rgba(30,45,61,.35)'}}
      }}
  });
}

function conditionsText(c) {
  if (!c) return '—';
  const parts = [];
  if (c.mode) parts.push('mode='+c.mode);
  if (c.iv_regime && c.iv_regime !== 'unknown') parts.push('iv='+c.iv_regime);
  if (c.sector && c.sector !== 'other') parts.push('sector='+String(c.sector).replaceAll('_',' '));
  if (c.dte_bucket && c.dte_bucket !== 'unknown') parts.push('dte='+c.dte_bucket);
  if (typeof c.after_big_move === 'boolean') parts.push('after_big_move='+(c.after_big_move?'yes':'no'));
  return parts.length ? parts.join(' · ') : '—';
}

function filteredStrategies(d) {
  const mode = document.getElementById('f-strat-mode').value;
  const conf = document.getElementById('f-strat-conf').value;
  const q = document.getElementById('f-strat-q').value.trim().toLowerCase();
  return d.strategies.filter(s => {
    if (mode && s.mode !== mode) return false;
    if (conf && s.confidence !== conf) return false;
    if (!q) return true;
    const blob = [s.strategy_name, s.sector, conditionsText(s.conditions), s.personas].join(' ').toLowerCase();
    return blob.includes(q);
  });
}

function renderStrategies(d) {
  const groupsEl = document.getElementById('strategies-groups');
  if (!groupsEl) return;

  const filtered = filteredStrategies(d);
  if (!filtered.length) {
    groupsEl.innerHTML = '<div class="pending-box">No strategies match current filters.</div>';
    return;
  }

  const order = ['A','B','C','CC'];
  const grouped = {};
  filtered.forEach(s => {
    const key = s.mode || 'Other';
    if (!grouped[key]) grouped[key] = [];
    grouped[key].push(s);
  });

  groupsEl.innerHTML = order.concat(Object.keys(grouped).filter(k=>!order.includes(k))).filter(k=>grouped[k]).map(mode => {
    const rows = grouped[mode].map(s => {
      const cc = s.win_rate>=70?'pos':s.win_rate<50?'neg':'';
      const cb = s.confidence==='high'?'badge-high':s.confidence==='medium'?'badge-med':'badge-low';
      const wc = wrColor(s.win_rate);
      return '<tr><td>'+s.strategy_name+'</td>'
        +'<td>'+s.n_trades+'</td>'
        +'<td><div class="win-bar"><span class="'+cc+'">'+s.win_rate+'%</span>'
        +'<div class="win-bar-track"><div class="win-bar-fill" style="width:'+s.win_rate+'%;background:'+wc+'"></div></div></div></td>'
        +'<td>'+fmtUsd(s.avg_premium)+'</td>'
        +'<td>'+fmtPnl(s.avg_pnl)+'</td>'
        +'<td><span class="badge '+cb+'">'+String(s.confidence||'—').toUpperCase()+'</span></td>'
        +'<td class="conditions">'+conditionsText(s.conditions)+'</td>'
        +'<td style="color:var(--muted)">'+(s.personas||'—')+'</td></tr>';
    }).join('');

    return '<div class="mode-group">'
      +'<div class="mode-label">Mode '+mode+' <span class="badge badge-'+mode+'">'+mode+'</span></div>'
      +'<div class="table-scroll"><table class="data-table">'
      +'<thead><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Avg Premium</th><th>Avg P&L</th><th>Confidence</th><th>Conditions</th><th>Personas</th></tr></thead>'
      +'<tbody>'+rows+'</tbody></table></div></div>';
  }).join('');

  document.getElementById('strat-count').textContent = filtered.length + ' strategies';
}

function filterStrategies(){ renderStrategies(DATA[activePersona] || DATA['Arjuna']); }

function renderTickers(d) {
  const labels = d.tickers.map(r=>r.ticker);
  const prems = d.tickers.map(r=>r.premium);
  const wrs = d.tickers.map(r=>r.win_rate);

  dc('t-prem');
  const premEl = document.getElementById('chart-t-prem');
  if (premEl) {
    charts['t-prem'] = new Chart(premEl.getContext('2d'),{
      type:'bar',
      data:{labels,datasets:[{data:prems,backgroundColor:'rgba(0,212,255,.5)',borderColor:C.accent,borderWidth:1,borderRadius:2}]},
      options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:false}},
        scales:{
          x:{ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:9},callback:v=>'$'+(v/1000).toFixed(0)+'k'},grid:{color:'rgba(30,45,61,.4)'}},
          y:{ticks:{color:'#e0eaf4',font:{family:'JetBrains Mono',size:10}},grid:{display:false}}
        }}
    });
  }

  dc('t-wr');
  const wrEl = document.getElementById('chart-t-wr');
  if (wrEl) {
    charts['t-wr'] = new Chart(wrEl.getContext('2d'),{
      type:'bar',
      data:{labels,datasets:[{data:wrs,backgroundColor:wrs.map(v=>v>=70?'rgba(0,255,136,.5)':v<50?'rgba(255,68,102,.5)':'rgba(255,170,0,.5)'),borderColor:wrs.map(v=>wrColor(v)),borderWidth:1,borderRadius:2}]},
      options:{indexAxis:'y',responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:false}},
        scales:{
          x:{min:0,max:100,ticks:{color:'#5a7a94',font:{family:'JetBrains Mono',size:9},callback:v=>v+'%'},grid:{color:'rgba(30,45,61,.4)'}},
          y:{ticks:{color:'#e0eaf4',font:{family:'JetBrains Mono',size:10}},grid:{display:false}}
        }}
    });
  }

  document.getElementById('tbody-tickers').innerHTML = d.tickers.map(r=>`
    <tr><td style="font-weight:600">${r.ticker}</td>
    <td style="color:var(--muted);font-size:11px">${(r.sector||'').replace(/_/g,' ')}</td>
    <td>${r.trades}</td>
    <td><span class="${r.win_rate>=70?'pos':r.win_rate<50?'neg':''}">${r.win_rate}%</span></td>
    <td>${fmtUsd(r.premium)}</td>
    <td>${fmtPnl(r.pnl)}</td></tr>`).join('');
}

function populateTradeYears(d) {
  const years = [...new Set(d.trades.map(t=>t.year).filter(Boolean))].sort((a,b)=>a-b);
  const sel = document.getElementById('f-year');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">All Years</option>' + years.map(y=>`<option value="${y}">${y}</option>`).join('');
  if (years.includes(Number(current))) sel.value = current;
}

function renderTrades(d) {
  const mode = document.getElementById('f-mode').value;
  const out = document.getElementById('f-out').value;
  const yr = document.getElementById('f-year').value;
  const filtered = d.trades.filter(t=>(!mode||t.mode===mode)&&(!out||t.outcome===out)&&(!yr||String(t.year)===yr));
  document.getElementById('trade-count').textContent = filtered.length+' trades';
  document.getElementById('tbody-trades').innerHTML = filtered.slice(0,300).map(t=>{
    const oc = t.outcome?.includes('WIN')?'pos':t.outcome==='LOSS'?'neg':'';
    return '<tr>'
      +'<td style="color:var(--muted)">'+(t.entry_date||'—')+'</td>'
      +'<td style="font-weight:600">'+(t.ticker||'—')+'</td>'
      +'<td><span class="badge badge-'+(t.mode||'?')+'">'+(t.mode||'—')+'</span></td>'
      +'<td style="color:var(--muted)">'+(t.opt_type||'—')+'</td>'
      +'<td>$'+(t.strike||'—')+'</td>'
      +'<td style="color:var(--muted)">'+(t.dte!=null?t.dte:'—')+'</td>'
      +'<td>'+(t.contracts||'—')+'</td>'
      +'<td>'+fmtUsd(t.premium_collected)+'</td>'
      +'<td>'+fmtPnl(t.net_pnl)+'</td>'
      +'<td><span class="'+oc+'" style="font-size:11px">'+(t.outcome||'—')+'</span></td>'
      +'<td style="color:var(--muted)">'+(t.hold_days!=null?t.hold_days+'d':'—')+'</td>'
      +'</tr>';
  }).join('');
}

function filterTrades(){renderTrades(DATA[activePersona]||DATA['Arjuna']);}

function renderPositions(d) {
  const today = new Date();
  today.setHours(0,0,0,0);
  document.getElementById('tbody-pos').innerHTML = d.open.map(p=>{
    const dl = p.expiry_date ? Math.round((new Date(p.expiry_date+'T00:00:00') - today)/86400000) : null;
    const uc = dl!=null&&dl<=7?'danger':dl!=null&&dl<=30?'warn':'accent';
    return '<tr>'
      +'<td style="font-weight:600">'+p.ticker+'</td>'
      +'<td><span class="badge badge-'+(p.mode||'?')+'">'+(p.mode||'—')+'</span></td>'
      +'<td style="color:var(--muted)">'+(p.opt_type||'—')+'</td>'
      +'<td style="font-family:var(--font-mono)">$'+(p.strike||'—')+'</td>'
      +'<td style="font-family:var(--font-mono);color:var(--'+uc+')">'+(p.expiry_date||'—')+(dl!=null?' ('+dl+'d)':'')+'</td>'
      +'<td>'+(p.contracts||'—')+'</td>'
      +'<td>'+fmtUsd(p.premium_collected)+'</td>'
      +'<td><span class="open-badge"><span class="open-dot"></span>OPEN</span></td>'
      +'</tr>';
  }).join('');
}

function renderHeadToHead() {
  const h = HEAD_TO_HEAD;
  const msg = document.getElementById('h2h-message');
  const summary = document.getElementById('h2h-summary');
  const table = document.getElementById('h2h-table-wrap');

  if (h.status !== 'ready') {
    msg.textContent = h.message || 'Sundar data pending';
    summary.innerHTML = '<div class="pending-box">Sundar data pending</div>';
    table.innerHTML = '';
    return;
  }

  msg.textContent = h.message;
  summary.innerHTML = '<div class="cards head">'
    +'<div class="card"><div class="card-label">Closed Trades</div><div class="card-value accent">'+h.summary.arjuna.closed+'</div><div class="card-sub">Arjuna</div></div>'
    +'<div class="card"><div class="card-label">Closed Trades</div><div class="card-value accent">'+h.summary.sundar.closed+'</div><div class="card-sub">Sundar</div></div>'
    +'<div class="card green"><div class="card-label">Win Rate</div><div class="card-value green">'+h.summary.arjuna.win_rate+'%</div><div class="card-sub">Arjuna</div></div>'
    +'<div class="card green"><div class="card-label">Win Rate</div><div class="card-value green">'+h.summary.sundar.win_rate+'%</div><div class="card-sub">Sundar</div></div>'
    +'</div>';

  table.innerHTML = '<div class="table-scroll"><table class="data-table">'
    +'<thead><tr><th>Mode</th><th>Arjuna Trades</th><th>Sundar Trades</th><th>Arjuna Win%</th><th>Sundar Win%</th><th>Arjuna Net P&L</th><th>Sundar Net P&L</th></tr></thead>'
    +'<tbody>'
    +h.modes.map(r=>'<tr>'
      +'<td><span class="badge badge-'+r.mode+'">'+r.mode+'</span></td>'
      +'<td>'+r.arjuna_trades+'</td>'
      +'<td>'+r.sundar_trades+'</td>'
      +'<td>'+r.arjuna_win_rate+'%</td>'
      +'<td>'+r.sundar_win_rate+'%</td>'
      +'<td>'+fmtPnl(r.arjuna_pnl)+'</td>'
      +'<td>'+fmtPnl(r.sundar_pnl)+'</td>'
      +'</tr>').join('')
    +'</tbody></table></div>';
}

document.addEventListener('DOMContentLoaded',()=>{
  renderAll(DATA['Arjuna']);
  renderHeadToHead();
});
</script>
"""

BODY = """
<div class="app">
  <header>
    <div class="logo">
      <span class="logo-text">AIHF</span>
      <span class="logo-sub">Trading Analytics</span>
    </div>
    <div class="header-right">
      <div class="live-dot"></div>
      <span class="live-label" id="date-range">APR 2024 — MAR 2026</span>
    </div>
  </header>

  <div class="persona-bar">
    <span class="persona-label">Persona</span>
    <button class="persona-btn active" onclick="setPersona('Arjuna',this)">ARJUNA</button>
    {SUNDAR_BTN}
    {COMBINED_BTN}
    <span class="persona-note" id="persona-note">{PERSONA_NOTE}</span>
  </div>

  <div class="nav">
    <button class="nav-tab active" onclick="showTab('overview',this)">Overview</button>
    <button class="nav-tab" onclick="showTab('strategies',this)">Strategy Registry</button>
    <button class="nav-tab" onclick="showTab('headtohead',this)">Head-to-Head</button>
    <button class="nav-tab" onclick="showTab('tickers',this)">By Ticker</button>
    <button class="nav-tab" onclick="showTab('trades',this)">Trade Log</button>
    <button class="nav-tab" onclick="showTab('positions',this)">Open Positions</button>
  </div>

  <div id="panel-overview" class="panel active">
    <div class="cards">
      <div class="card"><div class="card-label">Total Trades</div><div class="card-value accent" id="s-trades">—</div><div class="card-sub">closed positions</div></div>
      <div class="card green"><div class="card-label">Win Rate</div><div class="card-value green" id="s-wr">—</div><div class="card-sub">across all modes</div></div>
      <div class="card"><div class="card-label">Premium Collected</div><div class="card-value" id="s-prem">—</div><div class="card-sub">gross premium</div></div>
      <div class="card danger"><div class="card-label">Net P&L</div><div class="card-value danger" id="s-pnl">—</div><div class="card-sub">realized closed trades</div></div>
      <div class="card warn"><div class="card-label">Open Positions</div><div class="card-value warn" id="s-open">—</div><div class="card-sub">active legs</div></div>
    </div>

    <div class="chart-grid-equal">
      <div class="chart-box"><div class="chart-title">Monthly Premium Collected</div><div class="canvas-wrap"><canvas id="chart-monthly"></canvas></div></div>
      <div class="chart-box"><div class="chart-title">Strategy Mode Split</div><div class="canvas-wrap"><canvas id="chart-modes"></canvas></div></div>
    </div>

    <div class="chart-grid-equal">
      <div class="chart-box"><div class="chart-title">Win Rate by Mode</div><div class="canvas-wrap med"><canvas id="chart-wr-mode"></canvas></div></div>
      <div class="chart-box"><div class="chart-title">Monthly Win Rate %</div><div class="canvas-wrap med"><canvas id="chart-monthly-wr"></canvas></div></div>
    </div>

    <div class="chart-box"><div class="chart-title">Cumulative P&L Equity Curve</div><div class="canvas-wrap tall"><canvas id="chart-equity"></canvas></div></div>
  </div>

  <div id="panel-strategies" class="panel">
    <div class="chart-box section-gap">
      <div class="chart-title">Strategy Registry — <span id="strat-count"></span></div>
      <div class="filter-row">
        <select id="f-strat-mode" class="filter-select" onchange="filterStrategies()"><option value="">All Modes</option><option>A</option><option>B</option><option>C</option><option>CC</option></select>
        <select id="f-strat-conf" class="filter-select" onchange="filterStrategies()"><option value="">All Confidence</option><option value="high">High</option><option value="medium">Medium</option><option value="low">Low</option></select>
        <input id="f-strat-q" class="filter-input" oninput="filterStrategies()" placeholder="Filter by name, sector, condition..." />
      </div>
      <div id="strategies-groups"></div>
    </div>
  </div>

  <div id="panel-headtohead" class="panel">
    <div class="chart-box section-gap">
      <div class="chart-title">Persona Comparison</div>
      <div id="h2h-message" class="persona-note" style="margin:0 0 12px 0">Sundar data pending</div>
      <div id="h2h-summary"></div>
      <div id="h2h-table-wrap" style="margin-top:12px"></div>
    </div>
  </div>

  <div id="panel-tickers" class="panel">
    <div class="chart-grid-equal section-gap">
      <div class="chart-box"><div class="chart-title">Premium by Ticker</div><div class="canvas-wrap tall"><canvas id="chart-t-prem"></canvas></div></div>
      <div class="chart-box"><div class="chart-title">Win Rate by Ticker</div><div class="canvas-wrap tall"><canvas id="chart-t-wr"></canvas></div></div>
    </div>
    <div class="chart-box">
      <div class="chart-title">Ticker Detail</div>
      <div class="table-scroll">
        <table class="data-table">
          <thead><tr><th>Ticker</th><th>Sector</th><th>Trades</th><th>Win Rate</th><th>Premium</th><th>Net P&L</th></tr></thead>
          <tbody id="tbody-tickers"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div id="panel-trades" class="panel">
    <div class="chart-box">
      <div class="chart-title">Trade Log — <span id="trade-count"></span></div>
      <div class="filter-row">
        <select id="f-mode" class="filter-select" onchange="filterTrades()"><option value="">All Modes</option><option>A</option><option>B</option><option>C</option><option>CC</option></select>
        <select id="f-out" class="filter-select" onchange="filterTrades()"><option value="">All Outcomes</option><option>WIN</option><option>LOSS</option><option>EXPIRED_WIN</option><option>ASSIGNED</option></select>
        <select id="f-year" class="filter-select" onchange="filterTrades()"><option value="">All Years</option></select>
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead><tr><th>Date</th><th>Ticker</th><th>Mode</th><th>Type</th><th>Strike</th><th>DTE</th><th>Cts</th><th>Premium</th><th>P&L</th><th>Outcome</th><th>Hold</th></tr></thead>
          <tbody id="tbody-trades"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div id="panel-positions" class="panel">
    <div class="chart-box">
      <div class="chart-title">Open Positions</div>
      <div class="table-scroll">
        <table class="data-table">
          <thead><tr><th>Ticker</th><th>Mode</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Contracts</th><th>Premium</th><th>Status</th></tr></thead>
          <tbody id="tbody-pos"></tbody>
        </table>
      </div>
    </div>
  </div>
</div>
"""


def build(output_path: str = "output/dashboard.html"):
    print("Loading data...")
    data = {"Arjuna": get_data("Arjuna")}

    sundar_ready = has_persona_data("Sundar")
    if sundar_ready:
        try:
            data["Sundar"] = get_data("Sundar")
            data["Combined"] = get_data("all")
        except FileNotFoundError:
            sundar_ready = False

    head_to_head = build_head_to_head()

    sundar_btn = (
        "<button class=\"persona-btn\" onclick=\"setPersona('Sundar',this)\">SUNDAR</button>"
        if sundar_ready
        else "<button class=\"persona-btn pending\" onclick=\"setPersona('Sundar',this)\" title=\"Add tradingData/Sundar/*.csv to enable\">SUNDAR</button>"
    )
    combined_btn = (
        "<button class=\"persona-btn\" onclick=\"setPersona('Combined',this)\">COMBINED</button>"
        if sundar_ready
        else "<button class=\"persona-btn pending\" onclick=\"setPersona('Combined',this)\">COMBINED</button>"
    )
    persona_note = (
        "Head-to-Head tab is live with Arjuna vs Sundar comparison"
        if sundar_ready
        else "Sundar data pending — add tradingData/Sundar/*.csv then re-run build_dashboard.py"
    )

    body = (
        BODY.replace("{SUNDAR_BTN}", sundar_btn)
        .replace("{COMBINED_BTN}", combined_btn)
        .replace("{PERSONA_NOTE}", persona_note)
    )

    print("Building HTML...")
    Path(output_path).parent.mkdir(exist_ok=True)

    data_js = (
        f"<script>\nconst DATA = {json.dumps(data, default=str)};\n"
        f"const HEAD_TO_HEAD = {json.dumps(head_to_head, default=str)};\n</script>\n"
    )

    html = (
        "<!DOCTYPE html>\n<html lang='en'>\n<head>\n"
        "<meta charset='UTF-8'>\n"
        "<meta name='viewport' content='width=device-width, initial-scale=1.0'>\n"
        "<title>AIHF — Trading Analytics</title>\n"
        "<script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js'></script>\n"
        + CSS
        + "\n</head>\n<body>\n"
        + data_js
        + body
        + JS
        + "\n</body>\n</html>"
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    size_kb = Path(output_path).stat().st_size // 1024
    print(f"  Written: {output_path} ({size_kb} KB)")
    print(f"  Open in browser: open {output_path}")
    return output_path


if __name__ == "__main__":
    build()
