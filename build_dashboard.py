"""
build_dashboard.py — single-file analytics dashboard from closed-lot data.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from src.analyzer import analyze
from src.core_strategies import summarize_core_strategies

DATA_DIR = Path("tradingData")
RESOLVED = ["WIN", "LOSS", "EXPIRED_WIN"]
REALIZED = ["WIN", "LOSS", "EXPIRED_WIN", "ADJUSTMENT"]


def has_persona_data(persona: str, data_dir: Path = DATA_DIR) -> bool:
    p = data_dir / persona
    return p.exists() and p.is_dir() and any(p.glob("*.csv"))


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _bucket(r: pd.Series) -> str:
    if str(r.get("row_kind", "TRADE")).upper() == "ADJUSTMENT":
        return "adjustment"
    if r.get("mode") == "STOCK":
        return "assigned_longed_stock"
    if r.get("opt_type") == "CALL":
        return "covered_calls" if r.get("mode") == "CC" else "naked_calls"
    if r.get("opt_type") == "PUT":
        return "covered_puts" if (r.get("premium_collected") or 0) < (r.get("cost_to_close") or 0) else "naked_puts"
    return "other"


def _persona_data(persona: str) -> dict:
    trades, _ = analyze(persona)
    t = trades.copy()
    t["bucket"] = t.apply(_bucket, axis=1)
    t["close_date_use"] = t["close_date"].where(t["close_date"].notna(), t["entry_date"])

    cols = [
        "trade_id",
        "year",
        "ticker",
        "sector",
        "mode",
        "opt_type",
        "strike",
        "dte",
        "contracts",
        "entry_date",
        "close_date",
        "close_date_use",
        "hold_days",
        "premium_collected",
        "cost_to_close",
        "net_pnl",
        "total_gl",
        "short_term_gl",
        "long_term_gl",
        "pnl_pct",
        "return_on_notional_pct",
        "outcome",
        "bucket",
        "row_kind",
    ]
    out = t[cols].copy()
    out["entry_date"] = out["entry_date"].dt.strftime("%Y-%m-%d")
    out["close_date"] = out["close_date"].dt.strftime("%Y-%m-%d")
    out["close_date_use"] = out["close_date_use"].dt.strftime("%Y-%m-%d")

    years = sorted([int(y) for y in out["year"].dropna().unique().tolist()])
    strategies = summarize_core_strategies(t)

    return {
        "persona": persona,
        "years": years,
        "trades": _records(out),
        "strategies": [] if strategies.empty else strategies.to_dict(orient="records"),
    }


CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500;700&family=Space+Grotesk:wght@300;400;500;700&display=swap');
:root{--bg:#080c10;--card:#111820;--line:#1e2d3d;--muted:#5a7a94;--text:#e0eaf4;--a:#00d4ff;--g:#00ff88;--o:#ffaa00;--r:#ff4466}
*{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--text);font-family:'Space Grotesk',sans-serif}
.app{max-width:1480px;margin:0 auto;padding:18px}
header{display:flex;justify-content:space-between;align-items:end;border-bottom:1px solid var(--line);padding-bottom:12px;margin-bottom:12px}
.logo{font:700 22px 'JetBrains Mono';letter-spacing:3px;color:var(--a)}
.sub{font:11px 'JetBrains Mono';color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.nav{display:flex;gap:0;border-bottom:1px solid var(--line);overflow:auto;white-space:nowrap;margin-bottom:10px}
.nav button{background:none;border:none;color:var(--muted);padding:10px 14px;font:500 12px 'JetBrains Mono';text-transform:uppercase;border-bottom:2px solid transparent;cursor:pointer}
.nav button.active{color:var(--a);border-color:var(--a)}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
select,input{background:#0d1117;border:1px solid var(--line);color:var(--text);padding:6px 10px;border-radius:4px;font:11px 'JetBrains Mono'}
.note{font-size:12px;color:var(--muted);line-height:1.45}
.panel{display:none}.panel.active{display:block}
.cards{display:grid;grid-template-columns:repeat(7,1fr);gap:10px;margin-bottom:12px}
.card{background:var(--card);border:1px solid var(--line);padding:12px;border-radius:4px}
.k{font:10px 'JetBrains Mono';color:var(--muted);text-transform:uppercase;letter-spacing:1px}.v{font:700 20px 'JetBrains Mono';margin-top:5px}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.box{background:var(--card);border:1px solid var(--line);border-radius:4px;padding:14px;margin-bottom:12px}
.title{font:11px 'JetBrains Mono';color:var(--muted);text-transform:uppercase;letter-spacing:1px;margin-bottom:10px}
canvas{width:100%!important;height:255px!important}
.table-wrap{overflow:auto}
table{width:100%;border-collapse:collapse;font:12px 'JetBrains Mono'}th,td{padding:8px 9px;border-bottom:1px solid rgba(30,45,61,.6);text-align:left}th{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px}
.pos{color:var(--g)}.neg{color:var(--r)}
.badge{padding:2px 7px;border-radius:3px;border:1px solid var(--line);font:10px 'JetBrains Mono'}
@media(max-width:1200px){.cards{grid-template-columns:repeat(3,1fr)}.grid2{grid-template-columns:1fr}}
@media(max-width:700px){.cards{grid-template-columns:1fr}.app{padding:10px}}
</style>
"""

JS = r"""
<script>
const RES=['WIN','LOSS','EXPIRED_WIN'];
const REALIZED=['WIN','LOSS','EXPIRED_WIN','ADJUSTMENT'];
const BUCKETS=['naked_calls','covered_calls','naked_puts','covered_puts','assigned_longed_stock'];
const BCOL={naked_calls:'rgba(255,68,102,.6)',covered_calls:'rgba(255,170,0,.6)',naked_puts:'rgba(0,212,255,.6)',covered_puts:'rgba(0,255,136,.6)',assigned_longed_stock:'rgba(160,160,180,.6)'};
let activePersona='Arjuna', activeYear='all', charts={};

function fmtUsd(v){if(v==null||isNaN(v))return '—'; const a=Math.abs(v).toLocaleString('en-US',{maximumFractionDigits:2}); return (v>=0?'$':'-$')+a;}
function fmtP(v){if(v==null||isNaN(v))return '—'; return Number(v).toFixed(2)+'%';}
function pnl(v){if(v==null||isNaN(v))return '—'; return '<span class="'+(v>=0?'pos':'neg')+'">'+fmtUsd(v)+'</span>';}
function dc(k){if(charts[k]){charts[k].destroy(); delete charts[k];}}
function showTab(id,btn){document.querySelectorAll('.panel').forEach(p=>p.classList.remove('active'));document.querySelectorAll('.nav button').forEach(b=>b.classList.remove('active'));document.getElementById('panel-'+id).classList.add('active');btn.classList.add('active');renderAll();}
function data(){return DATA[activePersona]||DATA['Arjuna'];}
function allRows(){return data().trades.filter(r=>REALIZED.includes(r.outcome) && (activeYear==='all'||String(r.year)===activeYear));}
function resolvedRows(){return allRows().filter(r=>RES.includes(r.outcome));}

function setPersona(){activePersona=document.getElementById('persona').value; populateYear(); renderAll();}
function setYear(){activeYear=document.getElementById('year').value; renderAll();}
function populateYear(){const d=data(); const s=document.getElementById('year'); const cur=activeYear; s.innerHTML='<option value="all">All Years</option>'+d.years.map(y=>'<option value="'+y+'">'+y+'</option>').join(''); activeYear=d.years.map(String).includes(cur)?cur:'all'; s.value=activeYear;}

function baseMetrics(){
  const ar=allRows(), rr=resolvedRows();
  const realized=ar.reduce((a,r)=>a+(Number(r.total_gl??r.net_pnl)||0),0);
  const st=ar.reduce((a,r)=>a+(Number(r.short_term_gl)||0),0);
  const lt=ar.reduce((a,r)=>a+(Number(r.long_term_gl)||0),0);
  const proceeds=rr.reduce((a,r)=>a+(Number(r.premium_collected)||0),0);
  const spend=rr.reduce((a,r)=>a+(Number(r.cost_to_close)||0),0);
  const wins=rr.filter(r=>r.outcome==='WIN'||r.outcome==='EXPIRED_WIN').length;
  const avgRet=rr.length?rr.reduce((a,r)=>a+(Number(r.pnl_pct)||0),0)/rr.length:null;
  return {realized,st,lt,proceeds,spend,netPrem:proceeds-spend,count:rr.length,wr:rr.length?wins/rr.length*100:0,avgRet};
}

function bucketLabel(b){return ({naked_calls:'Naked Calls',covered_calls:'Covered Calls',naked_puts:'Naked Puts',covered_puts:'Covered Puts',assigned_longed_stock:'Assigned / Longed Stock'})[b]||b;}

function renderOverview(){
  const m=baseMetrics();
  document.getElementById('m-realized').innerHTML=pnl(m.realized);
  document.getElementById('m-st').innerHTML=pnl(m.st);
  document.getElementById('m-lt').innerHTML=pnl(m.lt);
  document.getElementById('m-net').textContent=fmtUsd(m.netPrem);
  document.getElementById('m-count').textContent=m.count;
  document.getElementById('m-wr').textContent=m.wr.toFixed(1)+'%';
  document.getElementById('m-ret').textContent=fmtP(m.avgRet);

  const rr=resolvedRows();
  document.getElementById('tbody-ex').innerHTML=BUCKETS.map(b=>{
    const g=rr.filter(r=>r.bucket===b); const n=g.length; const wins=g.filter(x=>x.outcome==='WIN'||x.outcome==='EXPIRED_WIN').length;
    const p=g.reduce((a,x)=>a+(Number(x.premium_collected)||0),0); const c=g.reduce((a,x)=>a+(Number(x.cost_to_close)||0),0); const pnlv=g.reduce((a,x)=>a+(Number(x.total_gl??x.net_pnl)||0),0); const avg=n?g.reduce((a,x)=>a+(Number(x.pnl_pct)||0),0)/n:null;
    return '<tr><td>'+bucketLabel(b)+'</td><td>'+n+'</td><td>'+(n?(wins/n*100).toFixed(1):'0.0')+'%</td><td>'+fmtUsd(p)+'</td><td>'+fmtUsd(c)+'</td><td>'+pnl(pnlv)+'</td><td>'+fmtP(avg)+'</td></tr>';
  }).join('');

  // Color-coded stacked monthly by exposure buckets
  const by={}; rr.forEach(r=>{const m=(r.close_date_use||'').slice(0,7); if(!m) return; if(!by[m]) by[m]={}; BUCKETS.forEach(b=>{if(by[m][b]==null)by[m][b]=0}); by[m][r.bucket]=(by[m][r.bucket]||0)+(Number(r.total_gl??r.net_pnl)||0);});
  const months=Object.keys(by).sort();
  dc('month'); charts.month=new Chart(document.getElementById('chart-month').getContext('2d'),{type:'bar',data:{labels:months,datasets:BUCKETS.map(b=>({label:bucketLabel(b),data:months.map(m=>by[m][b]||0),backgroundColor:BCOL[b]}))},options:{responsive:true,maintainAspectRatio:false,plugins:{legend:{position:'bottom'}}}});

  const eq={}; rr.forEach(r=>{const m=(r.close_date_use||'').slice(0,7); if(!m) return; eq[m]=(eq[m]||0)+(Number(r.total_gl??r.net_pnl)||0)}); const ms=Object.keys(eq).sort(); let c=0; const vals=ms.map(k=>(c+=eq[k]));
  dc('eq'); charts.eq=new Chart(document.getElementById('chart-eq').getContext('2d'),{type:'line',data:{labels:ms,datasets:[{data:vals,borderColor:'#00d4ff',backgroundColor:'rgba(0,212,255,.12)',fill:true,pointRadius:0,tension:.2}]},options:{plugins:{legend:{display:false}},responsive:true,maintainAspectRatio:false}});
}

function strategyDefs(){return [
  {name:'NVDA Wheel Engine',desc:'Recurring NVDA short puts and covered calls.',fn:r=>r.ticker==='NVDA'&&['PUT','CALL'].includes(r.opt_type)},
  {name:'Structured Income Puts',desc:'Mode C mid-duration puts (15-364 DTE).',fn:r=>r.mode==='C'&&r.opt_type==='PUT'},
  {name:'Long-Dated Discount Entry Puts',desc:'Mode B 365+ DTE puts.',fn:r=>r.mode==='B'&&r.opt_type==='PUT'},
  {name:'Covered Call Overlay',desc:'Mode CC call-writing on held positions.',fn:r=>r.mode==='CC'&&r.opt_type==='CALL'}
];}

function renderStrategies(){
  const rows=resolvedRows();
  const q=(document.getElementById('q-strat').value||'').toLowerCase();
  const defs=strategyDefs().filter(s=>!q||(s.name+' '+s.desc).toLowerCase().includes(q));
  const strat=defs.map(s=>{const g=rows.filter(s.fn);const n=g.length;const wins=g.filter(x=>x.outcome==='WIN'||x.outcome==='EXPIRED_WIN').length;const pnlv=g.reduce((a,x)=>a+(Number(x.total_gl??x.net_pnl)||0),0);const prem=g.reduce((a,x)=>a+((Number(x.premium_collected)||0)-(Number(x.cost_to_close)||0)),0);const avg=n?g.reduce((a,x)=>a+(Number(x.pnl_pct)||0),0)/n:null;const hold=n?g.reduce((a,x)=>a+(Number(x.hold_days)||0),0)/n:null;const dte=n?g.reduce((a,x)=>a+(Number(x.dte)||0),0)/n:null; return {s,g,n,wins,pnlv,prem,avg,hold,dte};});
  document.getElementById('tbody-strat').innerHTML=strat.map(r=>'<tr><td><div>'+r.s.name+'</div><div class="note">'+r.s.desc+'</div></td><td>'+r.n+'</td><td>'+(r.n?(r.wins/r.n*100).toFixed(1):'0.0')+'%</td><td>'+fmtUsd(r.prem)+'</td><td>'+pnl(r.pnlv)+'</td><td>'+fmtP(r.avg)+'</td><td>'+(r.hold==null?'—':r.hold.toFixed(1))+'</td><td>'+(r.dte==null?'—':r.dte.toFixed(1))+'</td></tr>').join('');

  // Return bands
  const band=[{k:'<=-50%',f:v=>v<=-50},{k:'-50..0%',f:v=>v>-50&&v<=0},{k:'0..25%',f:v=>v>0&&v<=25},{k:'25..100%',f:v=>v>25&&v<=100},{k:'>100%',f:v=>v>100}];
  document.getElementById('tbody-band').innerHTML=band.map(b=>{const g=rows.filter(r=>b.f(Number(r.pnl_pct)||0));const n=g.length;const w=g.filter(x=>x.outcome==='WIN'||x.outcome==='EXPIRED_WIN').length;const p=g.reduce((a,x)=>a+(Number(x.total_gl??x.net_pnl)||0),0);return '<tr><td>'+b.k+'</td><td>'+n+'</td><td>'+(n?(w/n*100).toFixed(1):'0.0')+'%</td><td>'+pnl(p)+'</td></tr>';}).join('');

  // Top transactions (best/worst)
  const sorted=[...rows].sort((a,b)=>(Number(b.total_gl??b.net_pnl)||0)-(Number(a.total_gl??a.net_pnl)||0));
  const best=sorted.slice(0,10), worst=sorted.slice(-10).reverse();
  document.getElementById('tbody-top').innerHTML=[...best,...worst].map(t=>'<tr><td>'+(t.close_date_use||'—')+'</td><td>'+t.ticker+'</td><td>'+t.mode+'</td><td>'+t.opt_type+'</td><td>$'+(t.strike??'—')+'</td><td>'+(t.dte??'—')+'</td><td>'+pnl(Number(t.total_gl??t.net_pnl)||0)+'</td><td>'+fmtP(t.pnl_pct)+'</td></tr>').join('');

  // Strategy weighting chart
  const labels=strat.map(r=>r.s.name), vals=strat.map(r=>r.prem);
  dc('w'); charts.w=new Chart(document.getElementById('chart-weight').getContext('2d'),{type:'doughnut',data:{labels,datasets:[{data:vals,backgroundColor:['rgba(0,212,255,.7)','rgba(0,255,136,.7)','rgba(255,170,0,.7)','rgba(255,68,102,.7)']}]} ,options:{plugins:{legend:{position:'bottom'}},responsive:true,maintainAspectRatio:false}});

  // Large stock shifts context (if augmented columns exist)
  const shifts=rows.filter(r=>r.underlying_move_pct!=null && Math.abs(Number(r.underlying_move_pct))>=4);
  document.getElementById('shift-note').textContent=shifts.length?('Large underlying move trades (|move|>=4%): '+shifts.length+' rows. Use augmented pipeline for full context.'):'No large-move context in current dataset. Run --augment to add underlying_move_pct/IV.';
}

function renderTickers(){
  const rows=resolvedRows(); const m={}; rows.forEach(r=>{const k=r.ticker||'—'; if(!m[k])m[k]={t:k,s:r.sector||'other',n:0,w:0,p:0,pnl:0}; m[k].n++; if(r.outcome==='WIN'||r.outcome==='EXPIRED_WIN')m[k].w++; m[k].p+=(Number(r.premium_collected)||0); m[k].pnl+=(Number(r.total_gl??r.net_pnl)||0);});
  const arr=Object.values(m).sort((a,b)=>b.p-a.p).slice(0,30);
  document.getElementById('tbody-t').innerHTML=arr.map(r=>'<tr><td>'+r.t+'</td><td>'+r.s.replaceAll('_',' ')+'</td><td>'+r.n+'</td><td>'+(r.w/r.n*100).toFixed(1)+'%</td><td>'+fmtUsd(r.p)+'</td><td>'+pnl(r.pnl)+'</td></tr>').join('');
}

function renderTrades(){
  const rows=resolvedRows();
  const md=document.getElementById('f-mode').value, out=document.getElementById('f-out').value;
  const modes=[...new Set(rows.map(r=>r.mode).filter(Boolean))].sort(); const sel=document.getElementById('f-mode'); const cur=sel.value; sel.innerHTML='<option value="">All Modes</option>'+modes.map(m=>'<option value="'+m+'">'+m+'</option>').join(''); if(modes.includes(cur)) sel.value=cur;
  const f=rows.filter(r=>(!md||r.mode===md)&&(!out||r.outcome===out));
  document.getElementById('trade-count').textContent=f.length+' trades';
  document.getElementById('tbody-tr').innerHTML=f.slice(0,600).map(t=>'<tr><td>'+(t.close_date_use||'—')+'</td><td>'+t.ticker+'</td><td>'+t.mode+'</td><td>'+t.opt_type+'</td><td>$'+(t.strike??'—')+'</td><td>'+(t.dte??'—')+'</td><td>'+(t.contracts??'—')+'</td><td>'+fmtUsd(t.premium_collected)+'</td><td>'+pnl(Number(t.total_gl??t.net_pnl)||0)+'</td><td>'+fmtP(t.pnl_pct)+'</td><td>'+(t.hold_days??'—')+'</td><td>'+t.outcome+'</td></tr>').join('');
}

function renderHead(){
  if(!DATA['Sundar']){document.getElementById('h2h').innerHTML='<div class="note">Sundar data pending.</div>';return;}
  const y=activeYear;
  const stat=n=>{const r=(DATA[n].trades||[]).filter(t=>RES.includes(t.outcome)&&(y==='all'||String(t.year)===y));const ntr=r.length,w=r.filter(x=>x.outcome==='WIN'||x.outcome==='EXPIRED_WIN').length,p=r.reduce((a,x)=>a+(Number(x.total_gl??x.net_pnl)||0),0);return {n:ntr,wr:ntr?w/ntr*100:0,p};};
  const a=stat('Arjuna'), s=stat('Sundar');
  document.getElementById('h2h').innerHTML='<table><tr><th>Persona</th><th>Trades</th><th>Win Rate</th><th>Realized G/L</th></tr><tr><td>Arjuna</td><td>'+a.n+'</td><td>'+a.wr.toFixed(1)+'%</td><td>'+pnl(a.p)+'</td></tr><tr><td>Sundar</td><td>'+s.n+'</td><td>'+s.wr.toFixed(1)+'%</td><td>'+pnl(s.p)+'</td></tr></table>';
}

function renderAll(){renderOverview();renderStrategies();renderTickers();renderTrades();renderHead();}

document.addEventListener('DOMContentLoaded',()=>{populateYear();renderAll();});
</script>
"""

BODY = """
<div class="app">
  <header><div><div class="logo">AIHF</div><div class="sub">Trading Analytics</div></div><div class="sub">Closed Lots + Fidelity G/L</div></header>
  <div class="nav">
    <button class="active" onclick="showTab('overview',this)">Overview</button>
    <button onclick="showTab('strategies',this)">Strategy Analysis</button>
    <button onclick="showTab('head',this)">Head-to-Head</button>
    <button onclick="showTab('tickers',this)">By Ticker</button>
    <button onclick="showTab('trades',this)">Trade Log</button>
  </div>
  <div class="toolbar">
    <span class="sub">Persona</span><select id="persona" onchange="setPersona()">{PERSONA_OPTIONS}</select>
    <span class="sub">Year</span><select id="year" onchange="setYear()"></select>
    <span class="note">Legend: A=Short-dated puts, B=Long-dated puts, C=Structured-income puts, CC=Covered calls, STOCK=long equity lots.</span>
  </div>

  <div id="panel-overview" class="panel active">
    <div class="cards">
      <div class="card"><div class="k">Realized G/L</div><div id="m-realized" class="v">—</div></div>
      <div class="card"><div class="k">Short-term G/L</div><div id="m-st" class="v">—</div></div>
      <div class="card"><div class="k">Long-term G/L</div><div id="m-lt" class="v">—</div></div>
      <div class="card"><div class="k">Premium Net (Proceeds-Spend)</div><div id="m-net" class="v">—</div></div>
      <div class="card"><div class="k">Trade Count</div><div id="m-count" class="v">—</div></div>
      <div class="card"><div class="k">Win Rate</div><div id="m-wr" class="v">—</div></div>
      <div class="card"><div class="k">Avg Return / Transaction</div><div id="m-ret" class="v">—</div></div>
    </div>
    <div class="box"><div class="title">Exposure Split</div><div class="table-wrap"><table><thead><tr><th>Bucket</th><th>Transactions</th><th>Win Rate</th><th>Proceeds</th><th>Spend</th><th>Realized G/L</th><th>Avg Return</th></tr></thead><tbody id="tbody-ex"></tbody></table></div></div>
    <div class="grid2">
      <div class="box"><div class="title">Monthly Realized G/L by Exposure Bucket</div><canvas id="chart-month"></canvas></div>
      <div class="box"><div class="title">Cumulative Equity Curve</div><canvas id="chart-eq"></canvas></div>
    </div>
  </div>

  <div id="panel-strategies" class="panel">
    <div class="box"><div class="title">Distinct Strategies (where worked vs failed)</div><div class="toolbar"><input id="q-strat" placeholder="Filter strategies" oninput="renderStrategies()"/></div><div class="table-wrap"><table><thead><tr><th>Strategy</th><th>Transactions</th><th>Win Rate</th><th>Premium Net</th><th>Realized G/L</th><th>Avg Return</th><th>Avg Hold Days</th><th>Avg DTE</th></tr></thead><tbody id="tbody-strat"></tbody></table></div></div>
    <div class="grid2">
      <div class="box"><div class="title">Return Bands</div><div class="table-wrap"><table><thead><tr><th>Band</th><th>Transactions</th><th>Win Rate</th><th>Realized G/L</th></tr></thead><tbody id="tbody-band"></tbody></table></div></div>
      <div class="box"><div class="title">Strategy Weighting (Premium Net)</div><canvas id="chart-weight"></canvas></div>
    </div>
    <div class="box"><div class="title">Top / Worst Transactions</div><div class="table-wrap"><table><thead><tr><th>Date</th><th>Ticker</th><th>Mode</th><th>Type</th><th>Strike</th><th>DTE</th><th>Realized G/L</th><th>Return</th></tr></thead><tbody id="tbody-top"></tbody></table></div><div id="shift-note" class="note"></div></div>
  </div>

  <div id="panel-head" class="panel"><div class="box"><div class="title">Arjuna vs Sundar</div><div id="h2h"></div></div></div>
  <div id="panel-tickers" class="panel"><div class="box"><div class="title">Ticker Summary</div><div class="table-wrap"><table><thead><tr><th>Ticker</th><th>Sector</th><th>Transactions</th><th>Win Rate</th><th>Proceeds</th><th>Realized G/L</th></tr></thead><tbody id="tbody-t"></tbody></table></div></div></div>
  <div id="panel-trades" class="panel"><div class="box"><div class="title">Trade Log — <span id="trade-count"></span></div><div class="toolbar"><select id="f-mode" onchange="renderTrades()"><option value="">All Modes</option></select><select id="f-out" onchange="renderTrades()"><option value="">All Outcomes</option><option>WIN</option><option>LOSS</option><option>EXPIRED_WIN</option></select></div><div class="table-wrap"><table><thead><tr><th>Date</th><th>Ticker</th><th>Mode</th><th>Type</th><th>Strike</th><th>DTE</th><th>Qty</th><th>Proceeds</th><th>Realized G/L</th><th>Return %</th><th>Hold Days</th><th>Outcome</th></tr></thead><tbody id="tbody-tr"></tbody></table></div></div></div>
</div>
"""


def build(output_path: str = "output/dashboard.html"):
    print("Loading data...")
    data = {"Arjuna": _persona_data("Arjuna")}
    opts = ["<option value='Arjuna'>Arjuna</option>"]

    if has_persona_data("Sundar"):
        try:
            data["Sundar"] = _persona_data("Sundar")
            opts.append("<option value='Sundar'>Sundar</option>")
            data["Combined"] = _persona_data("all")
            opts.append("<option value='Combined'>Combined</option>")
        except FileNotFoundError:
            pass

    html = (
        "<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<title>AIHF Dashboard</title><script src='https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js'></script>"
        + CSS
        + "</head><body><script>const DATA="
        + json.dumps(data, default=str)
        + ";</script>"
        + BODY.replace("{PERSONA_OPTIONS}", "".join(opts))
        + JS
        + "</body></html>"
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"  Written: {output_path} ({Path(output_path).stat().st_size // 1024} KB)")


if __name__ == "__main__":
    build()
