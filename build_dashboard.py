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
from src.core_strategies import summarize_core_strategies

DATA_DIR = Path("tradingData")
TRADE_OUTCOMES = ["WIN", "LOSS", "EXPIRED_WIN"]
WIN_OUTCOMES = ["WIN", "EXPIRED_WIN"]
CLOSED_PLUS_ASSIGNED = ["WIN", "LOSS", "EXPIRED_WIN", "ASSIGNED"]


def has_persona_data(persona: str, data_dir: Path = DATA_DIR) -> bool:
    p = data_dir / persona
    return p.exists() and p.is_dir() and any(p.glob("*.csv"))


def _safe_int(v):
    return int(v) if pd.notna(v) else None


def _safe_float(v):
    return round(float(v), 2) if pd.notna(v) else None


def _prepare_closed(trades: pd.DataFrame) -> pd.DataFrame:
    closed = trades[trades["outcome"].isin(TRADE_OUTCOMES)].copy()
    closed["won"] = closed["outcome"].isin(WIN_OUTCOMES)
    close_ref = closed["close_date"].where(closed["close_date"].notna(), closed["expiry_date"])
    fallback_year_date = pd.to_datetime(closed["year"].astype("Int64").astype(str) + "-12-31", errors="coerce")
    closed["pnl_date"] = close_ref.where(close_ref.notna(), closed["entry_date"]).where(
        close_ref.notna() | closed["entry_date"].notna(), fallback_year_date
    )
    closed["month"] = closed["pnl_date"].dt.to_period("M").astype(str)
    return closed


def _get_persona_data(persona: str) -> dict:
    trades, _ = analyze(persona)
    closed = _prepare_closed(trades)
    assigned = trades[trades["outcome"] == "ASSIGNED"].copy()
    open_pos = trades[trades["outcome"] == "OPEN"].copy()

    monthly = (
        closed.groupby("month", as_index=False)
        .agg(
            premium=("premium_collected", "sum"),
            net_pnl=("net_pnl", "sum"),
            trades=("trade_id", "count"),
            wins=("won", "sum"),
        )
        .sort_values("month")
    )
    monthly["win_rate"] = (monthly["wins"] / monthly["trades"] * 100).round(1)
    monthly["premium"] = monthly["premium"].round(0)
    monthly["net_pnl"] = monthly["net_pnl"].round(0)

    by_mode = (
        closed.groupby("mode", as_index=False)
        .agg(
            trades=("trade_id", "count"),
            premium=("premium_collected", "sum"),
            pnl=("net_pnl", "sum"),
            wins=("won", "sum"),
        )
        .sort_values("premium", ascending=False)
    )
    by_mode["win_rate"] = (by_mode["wins"] / by_mode["trades"] * 100).round(1)
    by_mode["premium"] = by_mode["premium"].round(0)
    by_mode["pnl"] = by_mode["pnl"].round(0)

    by_ticker = (
        closed.groupby(["ticker", "sector"], as_index=False)
        .agg(
            trades=("trade_id", "count"),
            premium=("premium_collected", "sum"),
            pnl=("net_pnl", "sum"),
            wins=("won", "sum"),
        )
        .sort_values("premium", ascending=False)
        .head(20)
    )
    by_ticker["win_rate"] = (by_ticker["wins"] / by_ticker["trades"] * 100).round(1)
    by_ticker["premium"] = by_ticker["premium"].round(0)
    by_ticker["pnl"] = by_ticker["pnl"].round(0)

    equity = (
        closed.sort_values(["pnl_date", "entry_date"])
        .groupby("pnl_date", as_index=False)
        .agg(daily_pnl=("net_pnl", "sum"))
    )
    equity["cum_pnl"] = equity["daily_pnl"].cumsum().round(2)
    equity["date"] = equity["pnl_date"].dt.strftime("%Y-%m-%d")

    core = summarize_core_strategies(trades)
    core_data = [] if core.empty else core.to_dict(orient="records")

    trade_log = trades[trades["outcome"].isin(CLOSED_PLUS_ASSIGNED)].copy()
    trade_log = trade_log[
        [
            "trade_id",
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
            "cost_to_close",
            "net_pnl",
            "pnl_pct",
            "return_on_notional_pct",
            "outcome",
        ]
    ]
    trade_log["entry_date"] = trade_log["entry_date"].dt.strftime("%Y-%m-%d")
    trade_log["expiry_date"] = trade_log["expiry_date"].apply(lambda x: x.strftime("%Y-%m-%d") if pd.notna(x) else None)
    trade_log["year"] = trade_log["year"].apply(_safe_int)
    trade_log["dte"] = trade_log["dte"].apply(_safe_int)

    summary = {
        "resolved_trades": int(len(closed)),
        "win_rate": round(float(closed["won"].mean() * 100), 1) if len(closed) else 0,
        "premium": round(float(closed["premium_collected"].sum()), 0),
        "net_pnl": round(float(closed["net_pnl"].sum()), 0),
        "avg_trade_return_pct": round(float(closed["pnl_pct"].mean()), 2) if len(closed) else 0,
        "avg_notional_return_pct": round(float(closed["return_on_notional_pct"].mean()), 3)
        if len(closed) and closed["return_on_notional_pct"].notna().any()
        else None,
        "assigned": int(len(assigned)),
        "open_positions": int(len(open_pos)),
    }

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

    return {
        "persona": persona,
        "summary": summary,
        "monthly": json.loads(monthly.to_json(orient="records")),
        "modes": json.loads(by_mode.to_json(orient="records")),
        "tickers": json.loads(by_ticker.to_json(orient="records")),
        "equity": json.loads(equity[["date", "daily_pnl", "cum_pnl"]].to_json(orient="records")),
        "strategies": core_data,
        "trades": json.loads(trade_log.to_json(orient="records")),
        "open": open_json,
    }


def _summary_row(trades: pd.DataFrame) -> dict:
    closed = trades[trades["outcome"].isin(TRADE_OUTCOMES)].copy()
    wins = closed["outcome"].isin(WIN_OUTCOMES).sum()
    n = len(closed)
    return {
        "closed": int(n),
        "win_rate": round(float((wins / n) * 100), 1) if n else 0,
        "premium": round(float(closed["premium_collected"].sum()), 0),
        "pnl": round(float(closed["net_pnl"].sum()), 0),
    }


def _mode_summary(trades: pd.DataFrame, mode: str) -> dict:
    g = trades[(trades["mode"] == mode) & trades["outcome"].isin(TRADE_OUTCOMES)]
    n = len(g)
    wins = g["outcome"].isin(WIN_OUTCOMES).sum()
    return {
        "mode": mode,
        "trades": int(n),
        "win_rate": round(float((wins / n) * 100), 1) if n else 0,
        "pnl": round(float(g["net_pnl"].sum()), 0),
    }


def _head_to_head() -> dict:
    if not has_persona_data("Sundar"):
        return {"status": "pending", "message": "Sundar data pending", "summary": {}, "modes": []}

    try:
        arjuna, _ = analyze("Arjuna")
        sundar, _ = analyze("Sundar")
    except FileNotFoundError:
        return {"status": "pending", "message": "Sundar data pending", "summary": {}, "modes": []}

    modes = []
    mode_values = sorted(set(arjuna["mode"].dropna().unique().tolist() + sundar["mode"].dropna().unique().tolist()))
    for m in mode_values:
        a = _mode_summary(arjuna, m)
        s = _mode_summary(sundar, m)
        modes.append(
            {
                "mode": m,
                "arjuna_trades": a["trades"],
                "sundar_trades": s["trades"],
                "arjuna_win_rate": a["win_rate"],
                "sundar_win_rate": s["win_rate"],
                "arjuna_pnl": a["pnl"],
                "sundar_pnl": s["pnl"],
            }
        )

    return {
        "status": "ready",
        "message": "Resolved option trades only (WIN/LOSS/EXPIRED_WIN). Assignments excluded from win rate.",
        "summary": {"arjuna": _summary_row(arjuna), "sundar": _summary_row(sundar)},
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
header{display:flex;align-items:center;justify-content:space-between;padding:24px 0 20px;border-bottom:1px solid var(--border);margin-bottom:20px;gap:12px;flex-wrap:wrap}
.logo{display:flex;align-items:baseline;gap:10px}
.logo-text{font-family:var(--font-mono);font-size:22px;font-weight:700;letter-spacing:4px;color:var(--accent)}
.logo-sub{font-size:11px;color:var(--muted);letter-spacing:2px;text-transform:uppercase}
.live-label{font-family:var(--font-mono);font-size:11px;color:var(--muted)}
.nav{display:flex;gap:0;margin-bottom:14px;border-bottom:1px solid var(--border);overflow-x:auto;white-space:nowrap}
.nav-tab{padding:10px 16px;font-size:12px;font-family:var(--font-mono);text-transform:uppercase;color:var(--muted);cursor:pointer;border:none;background:none;border-bottom:2px solid transparent;flex:0 0 auto}
.nav-tab.active{color:var(--accent);border-bottom-color:var(--accent)}
.module-toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.p-label{font-size:11px;color:var(--muted);font-family:var(--font-mono);text-transform:uppercase;letter-spacing:1px}
.p-select,.filter-select,.filter-input{background:var(--surface);border:1px solid var(--border);color:var(--text);padding:6px 10px;border-radius:3px;font-family:var(--font-mono);font-size:11px}
.panel{display:none}.panel.active{display:block}
.cards{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px}
.cards.head{grid-template-columns:repeat(4,1fr)}
.card{background:var(--card);border:1px solid var(--border);border-radius:4px;padding:14px;position:relative;overflow:hidden}
.card::before{content:'';position:absolute;top:0;left:0;right:0;height:2px;background:var(--accent);opacity:.6}
.card.green::before{background:var(--accent2)}
.card.warn::before{background:var(--warn)}
.card.danger::before{background:var(--danger)}
.card-label{font-family:var(--font-mono);font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.4px;margin-bottom:8px}
.card-value{font-family:var(--font-mono);font-size:22px}
.card-sub{font-size:11px;color:var(--muted);margin-top:4px}
.chart-grid-2{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}
.chart-box{background:var(--card);border:1px solid var(--border);border-radius:4px;padding:18px;min-width:0}
.chart-title{font-family:var(--font-mono);font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:12px}
.canvas-wrap{height:220px;position:relative}
.canvas-wrap.tall{height:280px}
.table-scroll{overflow-x:auto}
.data-table{width:100%;border-collapse:collapse;font-family:var(--font-mono);font-size:12px}
.data-table th{text-align:left;padding:8px 10px;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:1.2px;border-bottom:1px solid var(--border)}
.data-table td{padding:9px 10px;border-bottom:1px solid rgba(30,45,61,.5);vertical-align:top}
.badge{display:inline-block;padding:2px 8px;border-radius:2px;font-size:10px;border:1px solid transparent}
.badge-A{background:rgba(0,212,255,.15);color:var(--accent);border-color:rgba(0,212,255,.4)}
.badge-B{background:rgba(0,255,136,.15);color:var(--accent2);border-color:rgba(0,255,136,.4)}
.badge-C{background:rgba(255,107,53,.15);color:var(--accent3);border-color:rgba(255,107,53,.4)}
.badge-CC{background:rgba(255,170,0,.15);color:var(--warn);border-color:rgba(255,170,0,.4)}
.badge-core{background:rgba(0,212,255,.12);color:var(--accent);border-color:rgba(0,212,255,.3)}
.pos{color:var(--accent2)} .neg{color:var(--danger)}
.filter-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.pending-box{padding:20px;border:1px dashed var(--border);border-radius:4px;color:var(--muted);font-family:var(--font-mono);text-align:center}
.notes{font-size:12px;color:var(--muted);margin-bottom:12px;line-height:1.5}
.conditions{font-size:11px;color:var(--muted);max-width:380px;white-space:normal}
@media(max-width:1000px){.cards,.cards.head{grid-template-columns:repeat(2,1fr)}.chart-grid-2{grid-template-columns:1fr}}
@media(max-width:640px){.app{padding:0 12px 28px}.cards,.cards.head{grid-template-columns:1fr}.card-value{font-size:20px}}
</style>
"""

JS = r"""
<script>
let activePersona = 'Arjuna';
let charts = {};
const C={accent:'#00d4ff',green:'#00ff88',warn:'#ffaa00',danger:'#ff4466',muted:'#5a7a94'};
const MC={A:'#00d4ff',B:'#00ff88',C:'#ff6b35',CC:'#ffaa00'};

function showTab(id,btn){document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));document.querySelectorAll('.nav-tab').forEach(b=>b.classList.remove('active'));document.getElementById('panel-'+id).classList.add('active');btn.classList.add('active');}
function dc(id){if(charts[id]){charts[id].destroy();delete charts[id];}}
function fmtUsd(v){return v==null?'—':'$'+Number(v).toLocaleString('en-US',{maximumFractionDigits:0});}
function fmtPnl(v){if(v==null)return'—';const s=(v>=0?'+$':'-$')+Math.abs(v).toLocaleString('en-US',{maximumFractionDigits:0});return '<span class="'+(v>=0?'pos':'neg')+'">'+s+'</span>';}

function onPersonaChange(){
  const sel=document.getElementById('persona-select');
  activePersona=sel.value;
  renderAll(DATA[activePersona]||DATA['Arjuna']);
}

function renderAll(d){
  document.getElementById('s-trades').textContent=d.summary.resolved_trades;
  document.getElementById('s-wr').textContent=d.summary.win_rate+'%';
  document.getElementById('s-prem').textContent=fmtUsd(d.summary.premium);
  document.getElementById('s-pnl').innerHTML=fmtPnl(d.summary.net_pnl);
  document.getElementById('s-return').textContent=(d.summary.avg_trade_return_pct??0)+'%';
  document.getElementById('metrics-note').textContent='Win rate = WIN or EXPIRED_WIN over resolved option trades. ASSIGNED is tracked separately ('+d.summary.assigned+').';
  renderMonthly(d);renderModes(d);renderEquity(d);renderTickers(d);renderStrategies(d);renderTrades(d);renderPositions(d);populateTradeYears(d);populateModeFilter(d);
}

function renderMonthly(d){
  dc('monthly');
  charts.monthly=new Chart(document.getElementById('chart-monthly').getContext('2d'),{type:'bar',data:{labels:d.monthly.map(r=>r.month),datasets:[{data:d.monthly.map(r=>r.net_pnl),backgroundColor:d.monthly.map(r=>r.net_pnl>=0?'rgba(0,255,136,.45)':'rgba(255,68,102,.45)'),borderColor:d.monthly.map(r=>r.net_pnl>=0?C.green:C.danger),borderWidth:1}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:C.muted,font:{family:'JetBrains Mono',size:9}}},y:{ticks:{color:C.muted,font:{family:'JetBrains Mono',size:10},callback:v=>'$'+Number(v).toLocaleString()}}}}});
}

function renderModes(d){
  dc('modes');
  charts.modes=new Chart(document.getElementById('chart-modes').getContext('2d'),{type:'bar',data:{labels:d.modes.map(r=>'Mode '+r.mode),datasets:[{data:d.modes.map(r=>r.win_rate),backgroundColor:d.modes.map(r=>MC[r.mode]||C.muted),borderWidth:0}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:C.muted,font:{family:'JetBrains Mono',size:10}}},y:{min:0,max:100,ticks:{color:C.muted,font:{family:'JetBrains Mono',size:10},callback:v=>v+'%'}}}}});
}

function renderEquity(d){
  dc('equity');
  charts.equity=new Chart(document.getElementById('chart-equity').getContext('2d'),{type:'line',data:{labels:d.equity.map(r=>r.date),datasets:[{data:d.equity.map(r=>r.cum_pnl),borderColor:C.accent,backgroundColor:'rgba(0,212,255,.08)',fill:true,pointRadius:0,tension:.15}]},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{display:false}},scales:{x:{ticks:{color:C.muted,font:{family:'JetBrains Mono',size:9},maxRotation:45}},y:{ticks:{color:C.muted,font:{family:'JetBrains Mono',size:10},callback:v=>'$'+Number(v).toLocaleString()}}}}});
}

function conditionsText(c){
  if(!c)return '—';
  return Object.entries(c).map(([k,v])=>k+'='+ (Array.isArray(v)?v.join('/') : String(v))).join(' · ');
}

function renderStrategies(d){
  const q=(document.getElementById('f-strat-q').value||'').trim().toLowerCase();
  const group=(document.getElementById('f-strat-group').value||'');
  const rows=d.strategies.filter(s=>{
    if(group && s.group!==group) return false;
    if(!q) return true;
    const blob=[s.strategy_name,s.group,s.definition,conditionsText(s.conditions)].join(' ').toLowerCase();
    return blob.includes(q);
  });
  document.getElementById('strat-count').textContent=rows.length+' strategies';
  if(!rows.length){document.getElementById('tbody-strat').innerHTML='<tr><td colspan="9" class="conditions">No strategy rows match current filters.</td></tr>';return;}
  document.getElementById('tbody-strat').innerHTML=rows.map(s=>'<tr>'
    +'<td><div>'+s.strategy_name+'</div><div class="conditions">'+s.definition+'</div></td>'
    +'<td><span class="badge badge-core">'+s.group+'</span></td>'
    +'<td>'+s.trades+'</td>'
    +'<td>'+s.win_rate+'%</td>'
    +'<td>'+fmtUsd(s.premium)+'</td>'
    +'<td>'+fmtPnl(s.pnl)+'</td>'
    +'<td>'+s.avg_pnl_pct+'%</td>'
    +'<td>'+(s.avg_return_on_notional_pct==null?'—':s.avg_return_on_notional_pct+'%')+'</td>'
    +'<td class="conditions">'+conditionsText(s.conditions)+'</td>'
    +'</tr>').join('');
}

function filterStrategies(){renderStrategies(DATA[activePersona]||DATA['Arjuna']);}

function renderTickers(d){
  document.getElementById('tbody-tickers').innerHTML=d.tickers.map(r=>'<tr><td style="font-weight:600">'+r.ticker+'</td><td class="conditions">'+String(r.sector||'').replaceAll('_',' ')+'</td><td>'+r.trades+'</td><td>'+r.win_rate+'%</td><td>'+fmtUsd(r.premium)+'</td><td>'+fmtPnl(r.pnl)+'</td></tr>').join('');
}

function populateTradeYears(d){
  const years=[...new Set(d.trades.map(t=>t.year).filter(Boolean))].sort((a,b)=>a-b);
  const sel=document.getElementById('f-year');
  const cur=sel.value;
  sel.innerHTML='<option value="">All Years</option>'+years.map(y=>'<option value="'+y+'">'+y+'</option>').join('');
  if(years.includes(Number(cur))) sel.value=cur;
}

function populateModeFilter(d){
  const modes=[...new Set(d.trades.map(t=>t.mode).filter(Boolean))].sort();
  const sel=document.getElementById('f-mode');
  const cur=sel.value;
  sel.innerHTML='<option value=\"\">All Modes</option>'+modes.map(m=>'<option value=\"'+m+'\">'+m+'</option>').join('');
  if(modes.includes(cur)) sel.value=cur;
}

function renderTrades(d){
  const mode=document.getElementById('f-mode').value;
  const out=document.getElementById('f-out').value;
  const yr=document.getElementById('f-year').value;
  const rows=d.trades.filter(t=>(!mode||t.mode===mode)&&(!out||t.outcome===out)&&(!yr||String(t.year)===yr));
  document.getElementById('trade-count').textContent=rows.length+' trades';
  document.getElementById('tbody-trades').innerHTML=rows.slice(0,400).map(t=>'<tr>'
    +'<td class="conditions">'+(t.entry_date||'—')+'</td><td style="font-weight:600">'+(t.ticker||'—')+'</td><td><span class="badge badge-'+(t.mode||'A')+'">'+(t.mode||'—')+'</span></td><td>'+(t.opt_type||'—')+'</td><td>$'+(t.strike||'—')+'</td><td>'+(t.dte??'—')+'</td><td>'+(t.contracts??'—')+'</td><td>'+fmtUsd(t.premium_collected)+'</td><td>'+fmtPnl(t.net_pnl)+'</td><td>'+(t.pnl_pct==null?'—':Number(t.pnl_pct).toFixed(1)+'%')+'</td><td>'+(t.return_on_notional_pct==null?'—':Number(t.return_on_notional_pct).toFixed(3)+'%')+'</td><td>'+(t.outcome||'—')+'</td></tr>').join('');
}

function filterTrades(){renderTrades(DATA[activePersona]||DATA['Arjuna']);}

function renderPositions(d){
  document.getElementById('tbody-pos').innerHTML=d.open.map(p=>'<tr><td style="font-weight:600">'+p.ticker+'</td><td>'+(p.mode||'—')+'</td><td>'+(p.opt_type||'—')+'</td><td>$'+(p.strike||'—')+'</td><td>'+(p.expiry_date||'—')+'</td><td>'+(p.contracts||'—')+'</td><td>'+fmtUsd(p.premium_collected)+'</td></tr>').join('');
}

function renderHeadToHead(){
  const h=HEAD_TO_HEAD;
  if(h.status!=='ready'){
    document.getElementById('h2h-message').textContent='Sundar data pending';
    document.getElementById('h2h-table').innerHTML='';
    return;
  }
  document.getElementById('h2h-message').textContent=h.message;
  document.getElementById('h2h-table').innerHTML='<table class="data-table"><thead><tr><th>Mode</th><th>Arjuna Trades</th><th>Sundar Trades</th><th>Arjuna Win%</th><th>Sundar Win%</th><th>Arjuna Net P&L</th><th>Sundar Net P&L</th></tr></thead><tbody>'
  +h.modes.map(r=>'<tr><td><span class="badge badge-'+r.mode+'">'+r.mode+'</span></td><td>'+r.arjuna_trades+'</td><td>'+r.sundar_trades+'</td><td>'+r.arjuna_win_rate+'%</td><td>'+r.sundar_win_rate+'%</td><td>'+fmtPnl(r.arjuna_pnl)+'</td><td>'+fmtPnl(r.sundar_pnl)+'</td></tr>').join('')
  +'</tbody></table>';
}

document.addEventListener('DOMContentLoaded',()=>{renderAll(DATA['Arjuna']);renderHeadToHead();});
</script>
"""

BODY = """
<div class="app">
  <header>
    <div class="logo"><span class="logo-text">AIHF</span><span class="logo-sub">Trading Analytics Module</span></div>
    <span class="live-label">APR 2024 — MAR 2026</span>
  </header>

  <div class="nav">
    <button class="nav-tab active" onclick="showTab('overview',this)">Overview</button>
    <button class="nav-tab" onclick="showTab('strategies',this)">Core Strategies</button>
    <button class="nav-tab" onclick="showTab('headtohead',this)">Head-to-Head</button>
    <button class="nav-tab" onclick="showTab('tickers',this)">By Ticker</button>
    <button class="nav-tab" onclick="showTab('trades',this)">Trade Log</button>
    <button class="nav-tab" onclick="showTab('positions',this)">Open Positions</button>
  </div>

  <div class="module-toolbar">
    <span class="p-label">Persona</span>
    <select id="persona-select" class="p-select" onchange="onPersonaChange()">{PERSONA_OPTIONS}</select>
    <span class="notes">This module treats personas as training datasets for strategy analytics.</span>
  </div>

  <div id="panel-overview" class="panel active">
    <div class="notes" id="metrics-note"></div>
    <div class="cards">
      <div class="card"><div class="card-label">Resolved Trades</div><div class="card-value" id="s-trades">—</div><div class="card-sub">WIN/LOSS/EXPIRED_WIN</div></div>
      <div class="card green"><div class="card-label">Win Rate</div><div class="card-value" id="s-wr">—</div><div class="card-sub">resolved only</div></div>
      <div class="card"><div class="card-label">Premium Collected</div><div class="card-value" id="s-prem">—</div><div class="card-sub">resolved only</div></div>
      <div class="card danger"><div class="card-label">Net P&L</div><div class="card-value" id="s-pnl">—</div><div class="card-sub">realized options only</div></div>
    </div>
    <div class="cards" style="grid-template-columns:repeat(2,1fr)">
      <div class="card warn"><div class="card-label">Avg Return / Trade</div><div class="card-value" id="s-return">—</div><div class="card-sub">net_pnl / premium</div></div>
      <div class="card"><div class="card-label">Truth Note</div><div class="card-sub">Assignments are excluded from P&L/win rate and shown separately in trade log.</div></div>
    </div>

    <div class="chart-grid-2">
      <div class="chart-box"><div class="chart-title">Monthly Net P&L</div><div class="canvas-wrap"><canvas id="chart-monthly"></canvas></div></div>
      <div class="chart-box"><div class="chart-title">Win Rate by Mode</div><div class="canvas-wrap"><canvas id="chart-modes"></canvas></div></div>
    </div>

    <div class="chart-box"><div class="chart-title">Cumulative Net P&L Equity Curve</div><div class="canvas-wrap tall"><canvas id="chart-equity"></canvas></div></div>
  </div>

  <div id="panel-strategies" class="panel">
    <div class="chart-box">
      <div class="chart-title">Core Strategy Registry — <span id="strat-count"></span></div>
      <div class="notes">Modes translated: A = short-dated puts, C = structured income puts (15-364 DTE), B = long-dated puts, CC = covered calls.</div>
      <div class="filter-row">
        <select id="f-strat-group" class="filter-select" onchange="filterStrategies()"><option value="">All Groups</option><option>Core Mode</option><option>Overlay</option><option>Ticker Core</option><option>Ticker Pattern</option></select>
        <input id="f-strat-q" class="filter-input" oninput="filterStrategies()" placeholder="Filter by strategy/condition" />
      </div>
      <div class="table-scroll">
        <table class="data-table">
          <thead><tr><th>Strategy</th><th>Group</th><th>Trades</th><th>Win Rate</th><th>Premium</th><th>Net P&L</th><th>Avg P&L %</th><th>Avg Notional %</th><th>Conditions</th></tr></thead>
          <tbody id="tbody-strat"></tbody>
        </table>
      </div>
    </div>
  </div>

  <div id="panel-headtohead" class="panel">
    <div class="chart-box">
      <div class="chart-title">Arjuna vs Sundar</div>
      <div class="notes" id="h2h-message">Sundar data pending</div>
      <div class="table-scroll" id="h2h-table"></div>
    </div>
  </div>

  <div id="panel-tickers" class="panel">
    <div class="chart-box">
      <div class="chart-title">Ticker Summary</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Ticker</th><th>Sector</th><th>Trades</th><th>Win Rate</th><th>Premium</th><th>Net P&L</th></tr></thead><tbody id="tbody-tickers"></tbody></table></div>
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
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Date</th><th>Ticker</th><th>Mode</th><th>Type</th><th>Strike</th><th>DTE</th><th>Cts</th><th>Premium</th><th>Net P&L</th><th>P&L %</th><th>Notional %</th><th>Outcome</th></tr></thead><tbody id="tbody-trades"></tbody></table></div>
    </div>
  </div>

  <div id="panel-positions" class="panel">
    <div class="chart-box">
      <div class="chart-title">Open Positions</div>
      <div class="table-scroll"><table class="data-table"><thead><tr><th>Ticker</th><th>Mode</th><th>Type</th><th>Strike</th><th>Expiry</th><th>Contracts</th><th>Premium</th></tr></thead><tbody id="tbody-pos"></tbody></table></div>
    </div>
  </div>
</div>
"""


def build(output_path: str = "output/dashboard.html"):
    print("Loading data...")
    data = {"Arjuna": _get_persona_data("Arjuna")}

    persona_options = ["<option value='Arjuna'>Arjuna</option>"]
    if has_persona_data("Sundar"):
        try:
            data["Sundar"] = _get_persona_data("Sundar")
            data["Combined"] = _get_persona_data("all")
            persona_options.append("<option value='Sundar'>Sundar</option>")
            persona_options.append("<option value='Combined'>Combined</option>")
        except FileNotFoundError:
            pass

    head_to_head = _head_to_head()

    body = BODY.replace("{PERSONA_OPTIONS}", "".join(persona_options))

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


if __name__ == "__main__":
    build()
