"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Summary = { people:number; accounts:number; opening_principal:number; recorded_payments:number; ledger_balance:number; audit_events:number };
type Account = { id:number; person:string; name:string; opening_principal:number; annual_interest_rate:number; regular_payment:number; start_date?:string|null; status:string; current_balance:number };
type Bootstrap = { summary:Summary; accounts:Account[]; settings:{ollama_url:string;ollama_model:string}; balance_note:string; phase:number };
type LedgerRow = { id:number; account_id:number; person:string; account:string; effective_date:string; transaction_type:string; direction:"debit"|"credit"; amount:number; delta:number; running_balance:number; note:string; reference:string; source:string; created_by:string; reverses_transaction_id:number|null; correction_group:string; entry_hash:string; is_reversed:boolean; is_reversal:boolean };
type AuditEvent = { id:number; entity_type:string; entity_id:number; action:string; summary:string; reason:string; actor:string; created_at:string };

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const money = new Intl.NumberFormat("en-GB", { style:"currency", currency:"GBP" });
const nav = ["Dashboard","People","Accounts","Payments","Ledger","Forecast","Reports","AI","Audit","Settings"];
const today = () => new Date().toISOString().slice(0,10);

async function api(path:string, options?:RequestInit){
  const response=await fetch(`${API}${path}`,options);
  if(!response.ok){const body=await response.json().catch(()=>({detail:"Request failed"}));throw new Error(body.detail||"Request failed")}
  return response.json();
}

export default function Home(){
  const [data,setData]=useState<Bootstrap|null>(null);
  const [ledger,setLedger]=useState<LedgerRow[]>([]);
  const [audit,setAudit]=useState<AuditEvent[]>([]);
  const [active,setActive]=useState("Dashboard");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [accountId,setAccountId]=useState("");
  const [paymentDate,setPaymentDate]=useState(today());
  const [paymentAmount,setPaymentAmount]=useState("");
  const [paymentNote,setPaymentNote]=useState("");

  async function refresh(){
    try{
      const [boot,ledgerResult,auditResult]=await Promise.all([api("/bootstrap"),api("/ledger"),api("/audit")]);
      setData(boot);setLedger(ledgerResult.transactions);setAudit(auditResult.events);setError("");
      if(!accountId&&boot.accounts.length)setAccountId(String(boot.accounts[0].id));
    }catch(reason){setError(reason instanceof Error?reason.message:"Backend unavailable")}
  }

  useEffect(()=>{void refresh()},[]);

  const payments=useMemo(()=>ledger.filter(item=>item.transaction_type==="payment"),[ledger]);

  async function addPayment(event:FormEvent){
    event.preventDefault();
    if(!accountId||!paymentAmount)return;
    try{
      await api(`/accounts/${accountId}/transactions`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({effective_date:paymentDate,transaction_type:"payment",amount:paymentAmount,direction:"credit",note:paymentNote,source:"manual",reason:"Payment recorded in Bank of Mum"})});
      setPaymentAmount("");setPaymentNote("");setNotice("Payment posted to the immutable ledger");await refresh();
    }catch(reason){setError(reason instanceof Error?reason.message:"Could not add payment")}
  }

  async function reverse(row:LedgerRow){
    const reason=window.prompt(`Reason for reversing transaction #${row.id}`)?.trim();
    if(!reason)return;
    try{await api(`/transactions/${row.id}/reverse`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reason})});setNotice(`Transaction #${row.id} reversed without deleting the original`);await refresh()}catch(reason){setError(reason instanceof Error?reason.message:"Could not reverse transaction")}
  }

  async function correct(row:LedgerRow){
    const amount=window.prompt("Correct amount",String(row.amount));if(!amount)return;
    const effectiveDate=window.prompt("Correct effective date",row.effective_date);if(!effectiveDate)return;
    const note=window.prompt("Correct note",row.note||"")??row.note;
    const reason=window.prompt("Reason for correction")?.trim();if(!reason)return;
    const type=row.transaction_type==="reversal"?"adjustment":row.transaction_type;
    try{await api(`/transactions/${row.id}/correct`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({effective_date:effectiveDate,transaction_type:type,amount,direction:row.direction,note,reason})});setNotice(`Transaction #${row.id} corrected by reversal and replacement`);await refresh()}catch(reason){setError(reason instanceof Error?reason.message:"Could not correct transaction")}
  }

  function Dashboard(){return <>
    <section className="cards">
      <article><small>LEDGER BALANCE</small><strong>{money.format(data?.summary.ledger_balance||0)}</strong><span>Calculated from immutable entries</span></article>
      <article><small>OPENING PRINCIPAL</small><strong>{money.format(data?.summary.opening_principal||0)}</strong><span>{data?.summary.accounts||0} accounts</span></article>
      <article><small>RECORDED PAYMENTS</small><strong>{money.format(data?.summary.recorded_payments||0)}</strong><span>{payments.length} payment entries</span></article>
      <article><small>AUDIT EVENTS</small><strong>{data?.summary.audit_events||0}</strong><span>Append-only history</span></article>
    </section>
    <section className="grid">
      <div className="panel accounts-panel"><div className="panel-head"><div><p className="eyebrow">CURRENT DATA</p><h2>Accounts</h2></div><span>{data?.accounts.length||0}</span></div>
        <div className="account-list">{data?.accounts.length?data.accounts.map(a=><article key={a.id}><div className="account-icon">£</div><div><strong>{a.person} · {a.name}</strong><p>{a.annual_interest_rate}% APR · {money.format(a.regular_payment)} regular payment</p></div><div className="amount"><strong>{money.format(a.current_balance)}</strong><small>{a.status}</small></div></article>):<div className="empty">No v2 accounts yet. Run the legacy importer once to migrate existing JSON records.</div>}</div>
      </div>
      <div className="panel ai-panel"><div className="panel-head"><div><p className="eyebrow">AI FOUNDATION</p><h2>Assistant</h2></div><span>Phase 6</span></div>
        <div className="ai-body"><div className="spark">✦</div><h3>Accounting-aware AI foundation</h3><p>AI remains read-only until its tool layer is activated. The model and server defaults are already available to the new app.</p><dl><div><dt>Ollama server</dt><dd>{data?.settings.ollama_url||"—"}</dd></div><div><dt>Model</dt><dd>{data?.settings.ollama_model||"—"}</dd></div></dl><button disabled>Ask Bank of Mum</button></div>
      </div>
    </section>
    <div className="notice">{data?.balance_note}</div>
  </>}

  function Payments(){return <section className="split-view">
    <div className="panel"><div className="panel-head"><div><p className="eyebrow">POST ENTRY</p><h2>Record payment</h2></div><span>Credit</span></div>
      <form className="entry-form" onSubmit={addPayment}>
        <label>Account<select value={accountId} onChange={e=>setAccountId(e.target.value)}>{data?.accounts.map(a=><option key={a.id} value={a.id}>{a.person} · {a.name}</option>)}</select></label>
        <div className="two"><label>Effective date<input type="date" value={paymentDate} onChange={e=>setPaymentDate(e.target.value)}/></label><label>Amount<input type="number" min="0.01" step="0.01" value={paymentAmount} onChange={e=>setPaymentAmount(e.target.value)} placeholder="0.00"/></label></div>
        <label>Note<input value={paymentNote} onChange={e=>setPaymentNote(e.target.value)} placeholder="Bank transfer, cash, regular payment…"/></label>
        <button className="primary" type="submit">Post payment</button><p className="form-hint">Payments are never edited or deleted after posting. Corrections create reversal and replacement entries.</p>
      </form>
    </div>
    <div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">HISTORY</p><h2>Recent payments</h2></div><span>{payments.length}</span></div><LedgerTable rows={payments.slice(0,15)} onReverse={reverse} onCorrect={correct}/></div>
  </section>}

  function Ledger(){return <div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">SOURCE OF TRUTH</p><h2>Immutable ledger</h2></div><span>{ledger.length} entries</span></div><div className="integrity-note">Every entry is append-only and hash-chained. Reversals remain visible beside the original transaction.</div><LedgerTable rows={ledger} onReverse={reverse} onCorrect={correct}/></div>}

  function Audit(){return <div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">CONTROL HISTORY</p><h2>Audit trail</h2></div><span>{audit.length} events</span></div><div className="audit-list">{audit.map(item=><article key={item.id}><div className="audit-mark">⌁</div><div><strong>{item.summary}</strong><p>{item.action.replaceAll("_"," ")} · {item.entity_type} #{item.entity_id}{item.reason?` · ${item.reason}`:""}</p></div><div className="audit-meta"><span>{item.actor}</span><small>{new Date(item.created_at).toLocaleString("en-GB")}</small></div></article>)}{!audit.length&&<div className="empty">No audit events yet.</div>}</div></div>}

  const content=active==="Dashboard"?<Dashboard/>:active==="Payments"?<Payments/>:active==="Ledger"?<Ledger/>:active==="Audit"?<Audit/>:<div className="coming"><h2>{active}</h2><p>This workspace is reserved for a later development phase.</p></div>;

  return <div className="app-shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">BM</span><span>Bank of Mum</span></div><div className="workspace-title">Family accounting workspace <span>Phase 2 · immutable ledger & audit</span></div><div className={`connection ${data?"online":""}`}><i/>{data?"Connected":"Connecting"}</div></header>
    <aside className="rail">{nav.map(item=><button key={item} onClick={()=>setActive(item)} className={active===item?"active":""}><span>{item.slice(0,1)}</span><small>{item}</small></button>)}</aside>
    <main className="workspace"><section className="page-head"><div><p className="eyebrow">BANK OF MUM</p><h1>{active}</h1><p>Transaction-led lending, accounting and forecasting.</p></div><button className="primary" onClick={()=>setActive("Payments")}>+ Payment</button></section>{error&&<div className="notice error">{error}</div>}{notice&&<div className="notice success">{notice}</div>}{content}</main>
  </div>
}

function LedgerTable({rows,onReverse,onCorrect}:{rows:LedgerRow[];onReverse:(row:LedgerRow)=>void;onCorrect:(row:LedgerRow)=>void}){
  return <div className="table-scroll"><table className="ledger-table"><thead><tr><th>Date</th><th>Account</th><th>Type</th><th>Direction</th><th>Amount</th><th>Balance</th><th>Reference</th><th/></tr></thead><tbody>{rows.map(row=><tr key={row.id} className={row.is_reversed?"reversed":""}><td>{new Date(`${row.effective_date}T00:00:00`).toLocaleDateString("en-GB")}</td><td><strong>{row.person} · {row.account}</strong><small>{row.note||`Transaction #${row.id}`}</small></td><td>{row.transaction_type.replaceAll("_"," ")}{row.is_reversal&&<small>reverses #{row.reverses_transaction_id}</small>}</td><td><span className={`direction ${row.direction}`}>{row.direction}</span></td><td className="money">{money.format(row.amount)}</td><td className="money">{money.format(row.running_balance)}</td><td><code>{row.reference||`#${row.id}`}</code></td><td><div className="row-actions"><button onClick={()=>onCorrect(row)}>Correct</button><button onClick={()=>onReverse(row)}>Reverse</button></div></td></tr>)}{!rows.length&&<tr><td colSpan={8}><div className="empty">No ledger entries.</div></td></tr>}</tbody></table></div>
}
