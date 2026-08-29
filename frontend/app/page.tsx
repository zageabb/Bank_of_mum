"use client";

import { useEffect, useState } from "react";

type Summary = { people:number; accounts:number; opening_principal:number; recorded_payments:number; provisional_balance:number };
type Account = { id:number; person:string; name:string; opening_principal:number; annual_interest_rate:number; regular_payment:number; start_date?:string|null; status:string };
type Bootstrap = { summary:Summary; accounts:Account[]; settings:{ollama_url:string;ollama_model:string}; balance_note:string };

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const money = new Intl.NumberFormat("en-GB", { style:"currency", currency:"GBP" });
const nav = ["Dashboard","People","Accounts","Payments","Ledger","Forecast","Reports","AI","Audit","Settings"];

export default function Home(){
  const [data,setData]=useState<Bootstrap|null>(null);
  const [active,setActive]=useState("Dashboard");
  const [error,setError]=useState("");

  useEffect(()=>{fetch(`${API}/bootstrap`).then(r=>{if(!r.ok)throw new Error("Backend unavailable");return r.json()}).then(setData).catch(e=>setError(e.message))},[]);

  return <div className="app-shell">
    <header className="topbar">
      <div className="brand"><span className="brand-mark">BM</span><span>Bank of Mum</span></div>
      <div className="workspace-title">Family accounting workspace <span>Phase 1 foundation</span></div>
      <div className={`connection ${data?"online":""}`}><i/>{data?"Connected":"Connecting"}</div>
    </header>
    <aside className="rail">{nav.map(item=><button key={item} onClick={()=>setActive(item)} className={active===item?"active":""}><span>{item.slice(0,1)}</span><small>{item}</small></button>)}</aside>
    <main className="workspace">
      <section className="page-head"><div><p className="eyebrow">BANK OF MUM</p><h1>{active}</h1><p>Transaction-led lending, accounting and forecasting.</p></div><button className="primary" disabled>+ New account</button></section>
      {error&&<div className="notice error">{error}. Start the FastAPI backend on port 8000.</div>}
      {active!=="Dashboard"?<div className="coming"><h2>{active}</h2><p>This workspace is scaffolded in Phase 1 and will be activated in the relevant development phase.</p></div>:
      <>
        <section className="cards">
          <article><small>PROVISIONAL BALANCE</small><strong>{money.format(data?.summary.provisional_balance||0)}</strong><span>Interest engine follows in Phase 3</span></article>
          <article><small>OPENING PRINCIPAL</small><strong>{money.format(data?.summary.opening_principal||0)}</strong><span>{data?.summary.accounts||0} accounts</span></article>
          <article><small>RECORDED PAYMENTS</small><strong>{money.format(data?.summary.recorded_payments||0)}</strong><span>Imported and future ledger payments</span></article>
          <article><small>PEOPLE</small><strong>{data?.summary.people||0}</strong><span>One person can hold multiple accounts</span></article>
        </section>
        <section className="grid">
          <div className="panel accounts-panel"><div className="panel-head"><div><p className="eyebrow">CURRENT DATA</p><h2>Accounts</h2></div><span>{data?.accounts.length||0}</span></div>
            <div className="account-list">{data?.accounts.length?data.accounts.map(a=><article key={a.id}><div className="account-icon">£</div><div><strong>{a.person} · {a.name}</strong><p>{a.annual_interest_rate}% APR · {money.format(a.regular_payment)} regular payment</p></div><div className="amount"><strong>{money.format(a.opening_principal)}</strong><small>{a.status}</small></div></article>):<div className="empty">No v2 accounts yet. Run the legacy importer once to migrate the existing JSON records.</div>}</div>
          </div>
          <div className="panel ai-panel"><div className="panel-head"><div><p className="eyebrow">AI FOUNDATION</p><h2>Assistant</h2></div><span>Phase 6</span></div>
            <div className="ai-body"><div className="spark">✦</div><h3>Accounting-aware AI is coming</h3><p>The app is already carrying the editable AI defaults into the new architecture.</p><dl><div><dt>Ollama server</dt><dd>{data?.settings.ollama_url||"—"}</dd></div><div><dt>Model</dt><dd>{data?.settings.ollama_model||"—"}</dd></div></dl><button disabled>Ask Bank of Mum</button></div>
          </div>
        </section>
        <div className="notice">{data?.balance_note||"The Phase 1 dashboard is deliberately non-authoritative until the date-sensitive accounting engine is implemented."}</div>
      </>}
    </main>
  </div>
}
