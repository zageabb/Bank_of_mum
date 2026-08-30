"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Summary = { people:number; accounts:number; opening_principal:number; recorded_payments:number; ledger_balance:number; outstanding_principal:number; accrued_interest:number; total_interest_accrued:number; total_interest_paid:number; audit_events:number; payment_plans:number };
type Account = { id:number; person:string; name:string; opening_principal:number; annual_interest_rate:number; regular_payment:number; start_date?:string|null; status:string; current_balance:number; principal_balance:number; accrued_interest:number; fees:number; nominal_ledger_balance:number; calculated_as_of:string; interest_method:string; day_count_convention:string; payment_allocation:string };
type Bootstrap = { summary:Summary; accounts:Account[]; settings:{ollama_url:string;ollama_model:string}; balance_note:string; phase:number };
type LedgerRow = { id:number; account_id:number; person:string; account:string; effective_date:string; transaction_type:string; direction:"debit"|"credit"; amount:number; delta:number; running_balance:number; note:string; reference:string; source:string; created_by:string; reverses_transaction_id:number|null; correction_group:string; entry_hash:string; is_reversed:boolean; is_reversal:boolean };
type AuditEvent = { id:number; entity_type:string; entity_id:number; action:string; summary:string; reason:string; actor:string; created_at:string };
type RatePeriod = { id:number; effective_from:string; annual_rate:number; day_count_convention:string; reason:string };
type CalcRow = { transaction_id:number; date:string; type:string; direction:string; amount:number; note:string; interest_accrual_before_transaction:number; allocated_to_fees:number; allocated_to_interest:number; allocated_to_principal:number; principal_after:number; interest_after:number; fees_after:number; balance_after:number; reverses_transaction_id:number|null };
type Calculation = { account_id:number; as_of:string; principal:number; accrued_interest:number; fees:number; unapplied_credit:number; total_balance:number; total_interest_accrued:number; total_interest_paid:number; interest_since_last_transaction:number; timeline:CalcRow[]; rate_periods:RatePeriod[]; interest_method:string; day_count_convention:string; payment_allocation:string };
type PlanMember = { id:number; account_id:number; person:string; account:string; priority:number; base_payment:number; enabled:boolean; current_regular_payment:number };
type PaymentPlan = { id:number; name:string; first_payment_date:string; frequency:string; monthly_budget:number; strategy:string; status:string; notes:string; members:PlanMember[] };
type PlanMemberDraft = { enabled:boolean; priority:number; base_payment:string };
type ForecastAccount = { account_id:number; person:string; account:string; priority:number; base_payment:number; starting_balance:number; forecast_payments:number; projected_interest:number; payoff_date:string|null; remaining_balance:number };
type ForecastPayment = { account_id:number; person:string; account:string; priority:number; base_component:number; rollover_component:number; amount:number; allocated_to_fees:number; allocated_to_interest:number; allocated_to_principal:number; balance_before:number; balance_after:number };
type ForecastPeriod = { period:number; date:string; budget:number; used:number; unused:number; remaining_balance:number; payments:ForecastPayment[] };
type PlanForecast = { plan:PaymentPlan; forecast:{ first_payment_date:string; monthly_budget:number; base_payment_total:number; rollover_available_initially:number; strategy:string; horizon_months:number; months_generated:number; payoff_date:string|null; total_forecast_payments:number; projected_interest:number; remaining_balance:number; accounts:ForecastAccount[]; schedule:ForecastPeriod[] }; non_destructive:boolean; calculation_note:string };

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
const money = new Intl.NumberFormat("en-GB", { style:"currency", currency:"GBP" });
const nav = ["Dashboard","People","Accounts","Payments","Ledger","Forecast","Reports","AI","Audit","Settings"];
const today = () => new Date().toISOString().slice(0,10);
const gbDate = (value:string|null|undefined) => value ? new Date(`${value}T00:00:00`).toLocaleDateString("en-GB") : "—";

async function api(path:string, options?:RequestInit){
  const response=await fetch(`${API}${path}`,options);
  if(!response.ok){const body=await response.json().catch(()=>({detail:"Request failed"}));throw new Error(body.detail||"Request failed")}
  return response.json();
}

export default function Home(){
  const [data,setData]=useState<Bootstrap|null>(null);
  const [ledger,setLedger]=useState<LedgerRow[]>([]);
  const [audit,setAudit]=useState<AuditEvent[]>([]);
  const [plans,setPlans]=useState<PaymentPlan[]>([]);
  const [active,setActive]=useState("Dashboard");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");
  const [accountId,setAccountId]=useState("");
  const [paymentDate,setPaymentDate]=useState(today());
  const [paymentAmount,setPaymentAmount]=useState("");
  const [paymentNote,setPaymentNote]=useState("");
  const [asOf,setAsOf]=useState(today());
  const [calculation,setCalculation]=useState<Calculation|null>(null);
  const [rateDate,setRateDate]=useState(today());
  const [rateValue,setRateValue]=useState("");
  const [rateConvention,setRateConvention]=useState("actual_365");
  const [rateReason,setRateReason]=useState("Interest rate change");
  const [selectedPlanId,setSelectedPlanId]=useState("");
  const [forecast,setForecast]=useState<PlanForecast|null>(null);
  const [planName,setPlanName]=useState("Family debt snowball");
  const [planDate,setPlanDate]=useState(today());
  const [planBudget,setPlanBudget]=useState("");
  const [planNotes,setPlanNotes]=useState("Roll released payments to the next account by priority.");
  const [planMembers,setPlanMembers]=useState<Record<string,PlanMemberDraft>>({});
  const [horizon,setHorizon]=useState("240");

  function defaultMembers(accounts:Account[]){
    const next:Record<string,PlanMemberDraft>={};
    accounts.forEach((item,index)=>{next[String(item.id)]={enabled:true,priority:index+1,base_payment:String(item.regular_payment||0)}});
    setPlanMembers(next);
    setPlanBudget(String(accounts.reduce((sum,item)=>sum+(item.regular_payment||0),0)));
  }

  function applyPlan(plan:PaymentPlan,accounts:Account[]){
    setPlanName(plan.name);setPlanDate(plan.first_payment_date);setPlanBudget(String(plan.monthly_budget));setPlanNotes(plan.notes||"");
    const next:Record<string,PlanMemberDraft>={};
    accounts.forEach((item,index)=>{next[String(item.id)]={enabled:false,priority:index+1,base_payment:String(item.regular_payment||0)}});
    plan.members.forEach(member=>{next[String(member.account_id)]={enabled:member.enabled,priority:member.priority,base_payment:String(member.base_payment)}});
    setPlanMembers(next);
  }

  async function loadForecast(id:string){
    if(!id){setForecast(null);return}
    try{setForecast(await api(`/payment-plans/${id}/forecast?horizon_months=${horizon}`));setError("")}catch(reason){setError(reason instanceof Error?reason.message:"Could not forecast payment plan")}
  }

  async function refresh(){
    try{
      const [boot,ledgerResult,auditResult,planResult]=await Promise.all([api("/bootstrap"),api("/ledger"),api("/audit"),api("/payment-plans")]);
      setData(boot);setLedger(ledgerResult.transactions);setAudit(auditResult.events);setPlans(planResult.plans);setError("");
      const chosen=accountId||String(boot.accounts[0]?.id||"");
      if(!accountId&&chosen)setAccountId(chosen);
      if(chosen)setCalculation(await api(`/accounts/${chosen}/calculation?as_of=${asOf}`));
      if(!Object.keys(planMembers).length){
        if(planResult.plans.length){
          const plan=planResult.plans[0] as PaymentPlan;setSelectedPlanId(String(plan.id));applyPlan(plan,boot.accounts);setForecast(await api(`/payment-plans/${plan.id}/forecast?horizon_months=${horizon}`));
        }else defaultMembers(boot.accounts);
      }
    }catch(reason){setError(reason instanceof Error?reason.message:"Backend unavailable")}
  }

  async function loadCalculation(id:string,dateValue=asOf){
    if(!id)return;
    try{setCalculation(await api(`/accounts/${id}/calculation?as_of=${dateValue}`));setError("")}catch(reason){setError(reason instanceof Error?reason.message:"Could not calculate account")}
  }

  useEffect(()=>{void refresh()},[]);
  const payments=useMemo(()=>ledger.filter(item=>item.transaction_type==="payment"),[ledger]);
  const selectedAccount=data?.accounts.find(item=>String(item.id)===accountId);

  async function addPayment(event:FormEvent){
    event.preventDefault();if(!accountId||!paymentAmount)return;
    const normalizedAmount=paymentAmount.trim().replace(",",".");
    if(!/^-?\d+(\.\d{1,2})?$/.test(normalizedAmount)||Number(normalizedAmount)===0){setError("Enter a non-zero amount with up to two decimal places, for example 125.50");return}
    try{const result=await api(`/accounts/${accountId}/transactions`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({effective_date:paymentDate,transaction_type:"payment",amount:normalizedAmount,note:paymentNote,source:"manual",reason:"Payment recorded in Bank of Mum"})});setPaymentAmount("");setPaymentNote("");setCalculation(result.calculation);setNotice(Number(normalizedAmount)<0?"Debit posted and interest recalculated from the dated ledger":"Payment posted and interest recalculated from the dated ledger");await refresh();if(selectedPlanId)await loadForecast(selectedPlanId)}catch(reason){setError(reason instanceof Error?reason.message:"Could not add payment")}
  }

  async function addRate(event:FormEvent){
    event.preventDefault();if(!accountId||!rateValue)return;
    try{const result=await api(`/accounts/${accountId}/rates`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({effective_from:rateDate,annual_rate:rateValue,day_count_convention:rateConvention,reason:rateReason})});setCalculation(result.calculation);setNotice(`New interest rate applied from ${gbDate(rateDate)}`);await refresh();if(selectedPlanId)await loadForecast(selectedPlanId)}catch(reason){setError(reason instanceof Error?reason.message:"Could not add interest rate")}
  }

  async function reverse(row:LedgerRow){
    const reason=window.prompt(`Reason for reversing transaction #${row.id}`)?.trim();if(!reason)return;
    try{await api(`/transactions/${row.id}/reverse`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({reason})});setNotice(`Transaction #${row.id} reversed and account recalculated`);await refresh();if(selectedPlanId)await loadForecast(selectedPlanId)}catch(reason){setError(reason instanceof Error?reason.message:"Could not reverse transaction")}
  }

  async function correct(row:LedgerRow){
    const amount=window.prompt("Correct amount",String(row.amount));if(!amount)return;
    const effectiveDate=window.prompt("Correct effective date",row.effective_date);if(!effectiveDate)return;
    const note=window.prompt("Correct note",row.note||"")??row.note;
    const reason=window.prompt("Reason for correction")?.trim();if(!reason)return;
    const type=row.transaction_type==="reversal"?"adjustment":row.transaction_type;
    try{await api(`/transactions/${row.id}/correct`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({effective_date:effectiveDate,transaction_type:type,amount,direction:row.direction,note,reason})});setNotice(`Transaction #${row.id} corrected; all later interest recalculated`);await refresh();if(selectedPlanId)await loadForecast(selectedPlanId)}catch(reason){setError(reason instanceof Error?reason.message:"Could not correct transaction")}
  }

  function startNewPlan(){
    setSelectedPlanId("");setForecast(null);setPlanName("Family debt snowball");setPlanDate(today());setPlanNotes("Roll released payments to the next account by priority.");if(data)defaultMembers(data.accounts);
  }

  async function selectPlan(id:string){
    setSelectedPlanId(id);
    const plan=plans.find(item=>String(item.id)===id);
    if(plan&&data)applyPlan(plan,data.accounts);
    await loadForecast(id);
  }

  async function savePlan(event:FormEvent){
    event.preventDefault();if(!data)return;
    const members=data.accounts.filter(item=>planMembers[String(item.id)]?.enabled).map(item=>({account_id:item.id,priority:Number(planMembers[String(item.id)].priority),base_payment:planMembers[String(item.id)].base_payment||"0",enabled:true}));
    if(!members.length){setError("Enable at least one account in the payment plan");return}
    const body:any={name:planName,first_payment_date:planDate,monthly_budget:planBudget||"0",strategy:"priority_rollover",status:"active",notes:planNotes,members};
    if(selectedPlanId)body.reason="Payment plan updated in Phase 4 workspace";
    try{
      const result=await api(selectedPlanId?`/payment-plans/${selectedPlanId}`:"/payment-plans",{method:selectedPlanId?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const plan=result.plan as PaymentPlan;setSelectedPlanId(String(plan.id));applyPlan(plan,data.accounts);setNotice(selectedPlanId?"Payment plan updated and forecast recalculated":"Payment plan created and forecast calculated");
      const list=await api("/payment-plans");setPlans(list.plans);setForecast(await api(`/payment-plans/${plan.id}/forecast?horizon_months=${horizon}`));await refresh();
    }catch(reason){setError(reason instanceof Error?reason.message:"Could not save payment plan")}
  }

  function Dashboard(){return <>
    <section className="cards">
      <article><small>CALCULATED BALANCE</small><strong>{money.format(data?.summary.ledger_balance||0)}</strong><span>Principal + accrued interest + fees</span></article>
      <article><small>PRINCIPAL OUTSTANDING</small><strong>{money.format(data?.summary.outstanding_principal||0)}</strong><span>{data?.summary.accounts||0} accounts</span></article>
      <article><small>ACCRUED INTEREST</small><strong>{money.format(data?.summary.accrued_interest||0)}</strong><span>{money.format(data?.summary.total_interest_paid||0)} interest paid</span></article>
      <article><small>PAYMENT PLANS</small><strong>{data?.summary.payment_plans||0}</strong><span>Dynamic rollover strategies</span></article>
    </section>
    <section className="grid">
      <div className="panel accounts-panel"><div className="panel-head"><div><p className="eyebrow">AS OF TODAY</p><h2>Accounts</h2></div><span>{data?.accounts.length||0}</span></div><div className="account-list">{data?.accounts.length?data.accounts.map(a=><article key={a.id} onClick={()=>{setAccountId(String(a.id));setActive("Accounts");void loadCalculation(String(a.id))}}><div className="account-icon">£</div><div><strong>{a.person} · {a.name}</strong><p>{a.annual_interest_rate}% original APR · {money.format(a.regular_payment)} regular payment · interest {money.format(a.accrued_interest)}</p></div><div className="amount"><strong>{money.format(a.current_balance)}</strong><small>{a.status}</small></div></article>):<div className="empty">No v2 accounts yet. Run the legacy importer once to migrate existing JSON records.</div>}</div></div>
      <div className="panel ai-panel"><div className="panel-head"><div><p className="eyebrow">AI FOUNDATION</p><h2>Assistant</h2></div><span>Phase 6</span></div><div className="ai-body"><div className="spark">✦</div><h3>Accounting-aware AI foundation</h3><p>The ledger, dated-interest engine and payment-plan forecasts will become safe AI tools later; the LLM will interpret results, not invent the accounting maths.</p><dl><div><dt>Ollama server</dt><dd>{data?.settings.ollama_url||"—"}</dd></div><div><dt>Model</dt><dd>{data?.settings.ollama_model||"—"}</dd></div></dl><button disabled>Ask Bank of Mum</button></div></div>
    </section><div className="notice">{data?.balance_note}</div>
  </>}

  function Accounts(){return <section className="account-workspace"><div className="panel account-selector"><div className="panel-head"><div><p className="eyebrow">ACCOUNT</p><h2>Interest calculation</h2></div><span>Phase 3</span></div><div className="entry-form"><label>Account<select value={accountId} onChange={e=>{setAccountId(e.target.value);void loadCalculation(e.target.value)}}>{data?.accounts.map(a=><option key={a.id} value={a.id}>{a.person} · {a.name}</option>)}</select></label><label>Calculate balance as at<input type="date" value={asOf} onChange={e=>{setAsOf(e.target.value);void loadCalculation(accountId,e.target.value)}}/></label><div className="calculation-cards"><div><small>PRINCIPAL</small><strong>{money.format(calculation?.principal||0)}</strong></div><div><small>ACCRUED INTEREST</small><strong>{money.format(calculation?.accrued_interest||0)}</strong></div><div><small>TOTAL BALANCE</small><strong>{money.format(calculation?.total_balance||0)}</strong></div></div><p className="form-hint">{selectedAccount?.interest_method.replaceAll("_"," ")} · {selectedAccount?.payment_allocation.replaceAll("_"," → ")}. Backdated entries cause the full history to be replayed.</p></div><form className="entry-form rate-form" onSubmit={addRate}><h3>New rate period</h3><div className="two"><label>Effective from<input type="date" value={rateDate} onChange={e=>setRateDate(e.target.value)}/></label><label>Annual rate %<input type="number" min="0" max="100" step="0.001" value={rateValue} onChange={e=>setRateValue(e.target.value)} placeholder="5.000"/></label></div><label>Day count<select value={rateConvention} onChange={e=>setRateConvention(e.target.value)}><option value="actual_365">Actual / 365</option><option value="actual_366">Actual / 366</option><option value="actual_actual">Actual / Actual</option><option value="30_360">30 / 360</option></select></label><label>Reason<input value={rateReason} onChange={e=>setRateReason(e.target.value)}/></label><button className="primary">Add rate period</button></form></div><div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">RECALCULATION</p><h2>Payment allocation</h2></div><span>{calculation?.as_of||asOf}</span></div><div className="rate-strip">{calculation?.rate_periods.map(rate=><div key={rate.id}><strong>{rate.annual_rate}%</strong><span>from {gbDate(rate.effective_from)}</span><small>{rate.day_count_convention.replaceAll("_"," /")}</small></div>)}</div><CalculationTable rows={calculation?.timeline||[]}/></div></section>}

  function Payments(){return <section className="split-view"><div className="panel"><div className="panel-head"><div><p className="eyebrow">POST ENTRY</p><h2>Record payment</h2></div><span>Credit / debit</span></div><form className="entry-form" onSubmit={addPayment}><label>Account<select value={accountId} onChange={e=>setAccountId(e.target.value)}>{data?.accounts.map(a=><option key={a.id} value={a.id}>{a.person} · {a.name}</option>)}</select></label><div className="two"><label>Effective date<input type="date" value={paymentDate} onChange={e=>setPaymentDate(e.target.value)}/></label><label>Amount<input type="text" inputMode="decimal" value={paymentAmount} onChange={e=>setPaymentAmount(e.target.value)} placeholder="125.50 or -70.25" autoComplete="off"/></label></div><label>Note<input value={paymentNote} onChange={e=>setPaymentNote(e.target.value)} placeholder="Bank transfer, cash, regular payment…"/></label><button className="primary">Post entry</button><p className="form-hint">Decimals may use a point or comma. Positive amounts are repayments (credits); negative amounts are additional borrowing or charges (debits). The date controls interest and recalculates all later allocations.</p></form></div><div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">HISTORY</p><h2>Recent payments</h2></div><span>{payments.length}</span></div><LedgerTable rows={payments.slice(0,15)} onReverse={reverse} onCorrect={correct}/></div></section>}

  function Ledger(){return <div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">SOURCE OF TRUTH</p><h2>Immutable ledger</h2></div><span>{ledger.length} entries</span></div><div className="integrity-note">The ledger remains immutable. Interest and payment plans are deterministic views over the dated source history.</div><LedgerTable rows={ledger} onReverse={reverse} onCorrect={correct}/></div>}

  function Forecast(){return <section className="plan-workspace"><div className="panel plan-editor"><div className="panel-head"><div><p className="eyebrow">PAYMENT STRATEGY</p><h2>Priority rollover plan</h2></div><button className="quiet" onClick={startNewPlan}>New</button></div><form className="entry-form" onSubmit={savePlan}>{plans.length>0&&<label>Saved plan<select value={selectedPlanId} onChange={e=>void selectPlan(e.target.value)}><option value="">New plan</option>{plans.map(plan=><option key={plan.id} value={plan.id}>{plan.name}</option>)}</select></label>}<label>Plan name<input value={planName} onChange={e=>setPlanName(e.target.value)}/></label><div className="two"><label>First payment date<input type="date" value={planDate} onChange={e=>setPlanDate(e.target.value)}/></label><label>Monthly budget<input type="number" min="0.01" step="0.01" value={planBudget} onChange={e=>setPlanBudget(e.target.value)}/></label></div><label>Notes<input value={planNotes} onChange={e=>setPlanNotes(e.target.value)}/></label><div className="plan-members"><div className="member-head"><span>Use</span><span>Account</span><span>Priority</span><span>Base payment</span></div>{data?.accounts.map(account=>{const draft=planMembers[String(account.id)]||{enabled:false,priority:1,base_payment:String(account.regular_payment||0)};return <div className="member-row" key={account.id}><input type="checkbox" checked={draft.enabled} onChange={e=>setPlanMembers(prev=>({...prev,[String(account.id)]:{...draft,enabled:e.target.checked}}))}/><div><strong>{account.person} · {account.name}</strong><small>Balance {money.format(account.current_balance)}</small></div><input type="number" min="1" step="1" value={draft.priority} onChange={e=>setPlanMembers(prev=>({...prev,[String(account.id)]:{...draft,priority:Number(e.target.value)}}))}/><input type="number" min="0" step="0.01" value={draft.base_payment} onChange={e=>setPlanMembers(prev=>({...prev,[String(account.id)]:{...draft,base_payment:e.target.value}}))}/></div>})}</div><button className="primary">{selectedPlanId?"Update plan":"Create plan"}</button><p className="form-hint">Every enabled account gets its base payment first. Any remaining budget rolls to the highest-priority open account, including unused money from a final payment in the same month.</p></form></div><div className="forecast-column">{forecast?<><section className="cards forecast-cards"><article><small>MONTHLY BUDGET</small><strong>{money.format(forecast.forecast.monthly_budget)}</strong><span>Base total {money.format(forecast.forecast.base_payment_total)}</span></article><article><small>PROJECTED PAYOFF</small><strong className="date-value">{gbDate(forecast.forecast.payoff_date)}</strong><span>{forecast.forecast.months_generated} generated months</span></article><article><small>PROJECTED INTEREST</small><strong>{money.format(forecast.forecast.projected_interest)}</strong><span>Future accrual from plan start</span></article><article><small>REMAINING</small><strong>{money.format(forecast.forecast.remaining_balance)}</strong><span>At forecast horizon</span></article></section><div className="panel"><div className="panel-head"><div><p className="eyebrow">ACCOUNT OUTCOMES</p><h2>Payoff sequence</h2></div><span>Non-destructive</span></div><div className="payoff-grid">{forecast.forecast.accounts.map(item=><article key={item.account_id}><div className="priority-badge">{item.priority}</div><div><strong>{item.person} · {item.account}</strong><p>Base {money.format(item.base_payment)} · forecast interest {money.format(item.projected_interest)}</p></div><div className="payoff-date"><small>PAYOFF</small><strong>{gbDate(item.payoff_date)}</strong></div></article>)}</div></div><div className="panel table-panel forecast-table"><div className="panel-head"><div><p className="eyebrow">ROLLOVER SCHEDULE</p><h2>Monthly plan</h2></div><div className="horizon-control"><select value={horizon} onChange={async e=>{setHorizon(e.target.value);if(selectedPlanId){const value=e.target.value;try{setForecast(await api(`/payment-plans/${selectedPlanId}/forecast?horizon_months=${value}`))}catch(reason){setError(reason instanceof Error?reason.message:"Could not update forecast")}}}}><option value="60">5 years</option><option value="120">10 years</option><option value="240">20 years</option></select></div></div><ForecastTable rows={forecast.forecast.schedule}/></div><div className="notice">{forecast.calculation_note}</div></>:<div className="panel empty-plan"><div className="spark">↗</div><h2>Create or select a plan</h2><p>The forecast will use the current ledger, effective-dated interest rates and exact payment dates without writing future payments to the accounts.</p></div>}</div></section>}

  function Audit(){return <div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">CONTROL HISTORY</p><h2>Audit trail</h2></div><span>{audit.length} events</span></div><div className="audit-list">{audit.map(item=><article key={item.id}><div className="audit-mark">⌁</div><div><strong>{item.summary}</strong><p>{item.action.replaceAll("_"," ")} · {item.entity_type} #{item.entity_id}{item.reason?` · ${item.reason}`:""}</p></div><div className="audit-meta"><span>{item.actor}</span><small>{new Date(item.created_at).toLocaleString("en-GB")}</small></div></article>)}{!audit.length&&<div className="empty">No audit events yet.</div>}</div></div>}

  const content=active==="Dashboard"?<Dashboard/>:active==="Accounts"?<Accounts/>:active==="Payments"?<Payments/>:active==="Ledger"?<Ledger/>:active==="Forecast"?<Forecast/>:active==="Audit"?<Audit/>:<div className="coming"><h2>{active}</h2><p>This workspace is reserved for a later development phase.</p></div>;
  return <div className="app-shell"><header className="topbar"><div className="brand"><span className="brand-mark">BM</span><span>Bank of Mum</span></div><div className="workspace-title">Family accounting workspace <span>Phase 4 · dynamic payment planning</span></div><div className={`connection ${data?"online":""}`}><i/>{data?"Connected":"Connecting"}</div></header><aside className="rail">{nav.map(item=><button key={item} onClick={()=>setActive(item)} className={active===item?"active":""}><span>{item.slice(0,1)}</span><small>{item}</small></button>)}</aside><main className="workspace"><section className="page-head"><div><p className="eyebrow">BANK OF MUM</p><h1>{active}</h1><p>Transaction-led lending, interest, audit and dynamic repayment planning.</p></div><button className="primary" onClick={()=>setActive(active==="Forecast"?"Payments":"Forecast")}>{active==="Forecast"?"+ Payment":"Payment plans"}</button></section>{error&&<div className="notice error">{error}</div>}{notice&&<div className="notice success">{notice}</div>}{content}</main></div>
}

function LedgerTable({rows,onReverse,onCorrect}:{rows:LedgerRow[];onReverse:(row:LedgerRow)=>void;onCorrect:(row:LedgerRow)=>void}){
  return <div className="table-scroll"><table className="ledger-table"><thead><tr><th>Date</th><th>Account</th><th>Type</th><th>Direction</th><th>Amount</th><th>Nominal balance</th><th>Reference</th><th/></tr></thead><tbody>{rows.map(row=><tr key={row.id} className={row.is_reversed?"reversed":""}><td>{gbDate(row.effective_date)}</td><td><strong>{row.person} · {row.account}</strong><small>{row.note||`Transaction #${row.id}`}</small></td><td>{row.transaction_type.replaceAll("_"," ")}{row.is_reversal&&<small>reverses #{row.reverses_transaction_id}</small>}</td><td><span className={`direction ${row.direction}`}>{row.direction}</span></td><td className="money">{money.format(row.amount)}</td><td className="money">{money.format(row.running_balance)}</td><td><code>{row.reference||`#${row.id}`}</code></td><td><div className="row-actions"><button onClick={()=>onCorrect(row)}>Correct</button><button onClick={()=>onReverse(row)}>Reverse</button></div></td></tr>)}{!rows.length&&<tr><td colSpan={8}><div className="empty">No ledger entries.</div></td></tr>}</tbody></table></div>
}

function CalculationTable({rows}:{rows:CalcRow[]}){
  return <div className="table-scroll"><table className="ledger-table calculation-table"><thead><tr><th>Date</th><th>Event</th><th>Amount</th><th>Interest before</th><th>To interest</th><th>To principal</th><th>Principal after</th><th>Interest after</th><th>Balance</th></tr></thead><tbody>{rows.map(row=><tr key={row.transaction_id}><td>{gbDate(row.date)}</td><td><strong>{row.type.replaceAll("_"," ")}</strong><small>{row.note}</small></td><td>{money.format(row.amount)}</td><td>{money.format(row.interest_accrual_before_transaction)}</td><td>{money.format(row.allocated_to_interest)}</td><td>{money.format(row.allocated_to_principal)}</td><td>{money.format(row.principal_after)}</td><td>{money.format(row.interest_after)}</td><td>{money.format(row.balance_after)}</td></tr>)}{!rows.length&&<tr><td colSpan={9}><div className="empty">No transactions before this date.</div></td></tr>}</tbody></table></div>
}

function ForecastTable({rows}:{rows:ForecastPeriod[]}){
  return <div className="table-scroll"><table className="ledger-table plan-table"><thead><tr><th>Month</th><th>Date</th><th>Payments</th><th>Budget used</th><th>Unused</th><th>Remaining debt</th></tr></thead><tbody>{rows.map(row=><tr key={row.period}><td>{row.period}</td><td>{gbDate(row.date)}</td><td>{row.payments.map(item=><div className="forecast-payment" key={`${row.period}-${item.account_id}`}><strong>{item.person} · {item.account}: {money.format(item.amount)}</strong><small>base {money.format(item.base_component)}{item.rollover_component>0?` + rollover ${money.format(item.rollover_component)}`:""} · interest {money.format(item.allocated_to_interest)} · principal {money.format(item.allocated_to_principal)}</small></div>)}</td><td className="money">{money.format(row.used)}</td><td className="money">{money.format(row.unused)}</td><td className="money"><strong>{money.format(row.remaining_balance)}</strong></td></tr>)}</tbody></table></div>
}
