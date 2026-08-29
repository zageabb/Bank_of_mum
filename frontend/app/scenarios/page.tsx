"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type Account = { id:number; person:string; name:string; current_balance:number; regular_payment:number };
type Bootstrap = { summary:{scenarios:number;payment_plans:number}; accounts:Account[] };
type PlanMember = { account_id:number; person:string; account:string; priority:number; base_payment:number; enabled:boolean };
type PaymentPlan = { id:number; name:string; first_payment_date:string; monthly_budget:number; members:PlanMember[] };
type ScenarioChange = { id?:number; change_type:string; account_id:number|null; account:string|null; effective_from:string; effective_to:string|null; value:number|null; day_count_convention:string; note:string };
type Scenario = { id:number; plan_id:number; plan_name:string; name:string; description:string; status:string; changes:ScenarioChange[] };
type ForecastAccount = { account_id:number; person:string; account:string; payoff_date:string|null; projected_interest:number; forecast_payments:number; remaining_balance:number };
type ForecastPayment = { account_id:number; person:string; account:string; amount:number; base_component:number; rollover_component:number; allocated_to_interest:number; allocated_to_principal:number };
type ForecastPeriod = { period:number; date:string; budget:number; used:number; unused:number; remaining_balance:number; payments:ForecastPayment[]; active_scenario_changes?:string[] };
type Forecast = { payoff_date:string|null; months_generated:number; projected_interest:number; total_forecast_payments:number; remaining_balance:number; accounts:ForecastAccount[]; schedule:ForecastPeriod[]; scenario_events?:Array<{date:string;account:string;amount:number;allocated_to_interest:number;allocated_to_principal:number;note:string}> };
type ScenarioComparison = { scenario:Scenario; plan:PaymentPlan; baseline:Forecast; candidate:Forecast; comparison:{baseline_payoff_date:string|null;scenario_payoff_date:string|null;months_saved:number|null;baseline_interest:number;scenario_interest:number;interest_saved:number;payment_difference:number;remaining_balance_difference:number}; non_destructive:boolean };
type ManyComparison = { plan:PaymentPlan|null; baseline:Forecast|null; scenarios:Array<{scenario:Scenario;payoff_date:string|null;months_saved:number|null;projected_interest:number;interest_saved:number;total_forecast_payments:number;remaining_balance:number}> };
type ChangeDraft = { key:string; change_type:string; account_id:string; effective_from:string; effective_to:string; value:string; day_count_convention:string; note:string };

const API=process.env.NEXT_PUBLIC_API_URL||"http://localhost:8000/api";
const money=new Intl.NumberFormat("en-GB",{style:"currency",currency:"GBP"});
const today=()=>new Date().toISOString().slice(0,10);
const gbDate=(value:string|null|undefined)=>value?new Date(`${value}T00:00:00`).toLocaleDateString("en-GB"):"—";
const accountRequired=new Set(["lump_sum","base_payment_override","priority_override","interest_rate"]);
const valueRequired=new Set(["budget_delta","budget_override","lump_sum","base_payment_override","priority_override","interest_rate"]);
const rangeTypes=new Set(["budget_delta","budget_override","payment_holiday","base_payment_override"]);

async function api<T>(path:string,options?:RequestInit):Promise<T>{
  const response=await fetch(`${API}${path}`,options);
  if(!response.ok){const body=await response.json().catch(()=>({detail:"Request failed"}));throw new Error(body.detail||"Request failed")}
  return response.json() as Promise<T>;
}

function newChange(type:string,start:string,accountId=""):ChangeDraft{
  const defaults:Record<string,string>={budget_delta:"100",budget_override:"350",lump_sum:"500",payment_holiday:"",base_payment_override:"200",priority_override:"1",interest_rate:"5"};
  return {key:`${Date.now()}-${Math.random()}`,change_type:type,account_id:accountId,effective_from:start,effective_to:"",value:defaults[type]||"",day_count_convention:"actual_365",note:""};
}

export default function ScenariosPage(){
  const [bootstrap,setBootstrap]=useState<Bootstrap|null>(null);
  const [plans,setPlans]=useState<PaymentPlan[]>([]);
  const [scenarios,setScenarios]=useState<Scenario[]>([]);
  const [selectedPlanId,setSelectedPlanId]=useState("");
  const [selectedScenarioId,setSelectedScenarioId]=useState("");
  const [name,setName]=useState("Add £100 per month");
  const [description,setDescription]=useState("Compare a higher monthly budget against the saved baseline plan.");
  const [changes,setChanges]=useState<ChangeDraft[]>([]);
  const [comparison,setComparison]=useState<ScenarioComparison|null>(null);
  const [selectedCompareIds,setSelectedCompareIds]=useState<number[]>([]);
  const [many,setMany]=useState<ManyComparison|null>(null);
  const [horizon,setHorizon]=useState("240");
  const [error,setError]=useState("");
  const [notice,setNotice]=useState("");

  const selectedPlan=useMemo(()=>plans.find(item=>String(item.id)===selectedPlanId)||null,[plans,selectedPlanId]);
  const planScenarios=useMemo(()=>scenarios.filter(item=>String(item.plan_id)===selectedPlanId),[scenarios,selectedPlanId]);

  function resetDraft(plan:PaymentPlan|null){
    const start=plan?.first_payment_date||today();
    const first=plan?.members.find(item=>item.enabled);
    setSelectedScenarioId("");setName("Add £100 per month");setDescription("Compare a higher monthly budget against the saved baseline plan.");setChanges([newChange("budget_delta",start,first?String(first.account_id):"")]);setComparison(null);setNotice("");
  }

  function applyScenario(item:Scenario){
    setSelectedScenarioId(String(item.id));setSelectedPlanId(String(item.plan_id));setName(item.name);setDescription(item.description||"");
    setChanges(item.changes.map(change=>({key:String(change.id||Math.random()),change_type:change.change_type,account_id:change.account_id?String(change.account_id):"",effective_from:change.effective_from,effective_to:change.effective_to||"",value:change.value==null?"":String(change.value),day_count_convention:change.day_count_convention||"actual_365",note:change.note||""})));
    void loadComparison(String(item.id));
  }

  async function refresh(){
    try{
      const [boot,planResult,scenarioResult]=await Promise.all([
        api<Bootstrap>("/bootstrap"),
        api<{plans:PaymentPlan[]}>("/payment-plans"),
        api<{scenarios:Scenario[]}>("/scenarios"),
      ]);
      setBootstrap(boot);setPlans(planResult.plans);setScenarios(scenarioResult.scenarios);setError("");
      const planId=selectedPlanId||String(planResult.plans[0]?.id||"");
      if(!selectedPlanId&&planId)setSelectedPlanId(planId);
      if(!changes.length&&planId){const plan=planResult.plans.find(item=>String(item.id)===planId)||null;resetDraft(plan)}
    }catch(reason){setError(reason instanceof Error?reason.message:"Backend unavailable")}
  }

  async function loadComparison(id:string){
    if(!id){setComparison(null);return}
    try{setComparison(await api<ScenarioComparison>(`/scenarios/${id}/comparison?horizon_months=${horizon}`));setError("")}catch(reason){setError(reason instanceof Error?reason.message:"Could not compare scenario")}
  }

  useEffect(()=>{void refresh()},[]);

  function addChange(type:string){
    const first=selectedPlan?.members.find(item=>item.enabled);
    setChanges(prev=>[...prev,newChange(type,selectedPlan?.first_payment_date||today(),first?String(first.account_id):"")]);
  }

  function patchChange(key:string,patch:Partial<ChangeDraft>){setChanges(prev=>prev.map(item=>item.key===key?{...item,...patch}:item))}
  function removeChange(key:string){setChanges(prev=>prev.filter(item=>item.key!==key))}

  async function saveScenario(event:FormEvent){
    event.preventDefault();if(!selectedPlanId){setError("Create or select a baseline payment plan first");return}
    const payload={
      plan_id:Number(selectedPlanId),name,description,status:"active",
      changes:changes.map(item=>({
        change_type:item.change_type,
        account_id:item.account_id?Number(item.account_id):null,
        effective_from:item.effective_from,
        effective_to:item.effective_to||null,
        value:item.value===""?null:item.value,
        day_count_convention:item.day_count_convention,
        note:item.note,
      })),
      ...(selectedScenarioId?{reason:"Scenario assumptions updated in Phase 5 workspace"}:{}),
    };
    try{
      const result=await api<{scenario:Scenario}>(selectedScenarioId?`/scenarios/${selectedScenarioId}`:"/scenarios",{method:selectedScenarioId?"PUT":"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
      setSelectedScenarioId(String(result.scenario.id));setNotice(selectedScenarioId?"Scenario updated and recalculated":"Scenario saved and compared with baseline");
      const list=await api<{scenarios:Scenario[]}>("/scenarios");setScenarios(list.scenarios);applyScenario(result.scenario);await loadComparison(String(result.scenario.id));
    }catch(reason){setError(reason instanceof Error?reason.message:"Could not save scenario")}
  }

  async function compareSelected(){
    if(!selectedCompareIds.length){setMany(null);return}
    const params=new URLSearchParams();selectedCompareIds.forEach(id=>params.append("scenario_ids",String(id)));params.set("horizon_months",horizon);
    try{setMany(await api<ManyComparison>(`/scenarios/compare?${params.toString()}`));setError("")}catch(reason){setError(reason instanceof Error?reason.message:"Could not compare selected scenarios")}
  }

  const baselineAccounts=new Map((comparison?.baseline.accounts||[]).map(item=>[item.account_id,item]));

  return <div className="scenario-page"><div className="scenario-shell">
    <header className="topbar"><div className="brand"><span className="brand-mark">BM</span><span>Bank of Mum</span></div><div className="workspace-title">Family accounting workspace <span>Phase 5 · what-if scenarios</span></div><div className={`connection ${bootstrap?"online":""}`}><i/>{bootstrap?"Connected":"Connecting"}</div></header>
    <aside className="scenario-rail"><a href="/"><span>D</span><small>Dashboard</small></a><a href="/"><span>F</span><small>Forecast</small></a><a href="/scenarios" className="active"><span>S</span><small>Scenarios</small></a><a href="/"><span>A</span><small>Audit</small></a></aside>
    <main className="scenario-workspace">
      <section className="page-head"><div><p className="eyebrow">BANK OF MUM</p><h1>Scenarios</h1><p>Compare temporary changes against the saved baseline without altering ledger history.</p></div><a className="primary" href="/">Back to workspace</a></section>
      {error&&<div className="notice error">{error}</div>}{notice&&<div className="notice success">{notice}</div>}
      <div className="non-destructive-banner">Scenario payments and rates exist only inside the deterministic forecast replay. They never become ledger transactions or contractual rate records.</div>
      <div style={{height:16}}/>
      <section className="scenario-grid">
        <div className="panel scenario-editor">
          <div className="panel-head"><div><p className="eyebrow">WHAT-IF DESIGNER</p><h2>Scenario assumptions</h2></div><button className="quiet" onClick={()=>resetDraft(selectedPlan)}>New</button></div>
          <form className="entry-form" onSubmit={saveScenario}>
            <label>Baseline plan<select value={selectedPlanId} onChange={e=>{setSelectedPlanId(e.target.value);const plan=plans.find(item=>String(item.id)===e.target.value)||null;resetDraft(plan)}}><option value="">Select a plan</option>{plans.map(plan=><option key={plan.id} value={plan.id}>{plan.name}</option>)}</select></label>
            {planScenarios.length>0&&<label>Saved scenario<select value={selectedScenarioId} onChange={e=>{const item=planScenarios.find(row=>String(row.id)===e.target.value);if(item)applyScenario(item);else resetDraft(selectedPlan)}}><option value="">New scenario</option>{planScenarios.map(item=><option key={item.id} value={item.id}>{item.name}</option>)}</select></label>}
            <label>Scenario name<input value={name} onChange={e=>setName(e.target.value)}/></label>
            <label>Description<input value={description} onChange={e=>setDescription(e.target.value)}/></label>
            <div className="scenario-presets"><button type="button" onClick={()=>addChange("budget_delta")}>+ Monthly budget</button><button type="button" onClick={()=>addChange("lump_sum")}>+ Lump sum</button><button type="button" onClick={()=>addChange("payment_holiday")}>+ Holiday</button><button type="button" onClick={()=>addChange("base_payment_override")}>+ Base payment</button><button type="button" onClick={()=>addChange("priority_override")}>+ Priority</button><button type="button" onClick={()=>addChange("interest_rate")}>+ Future rate</button></div>
            <div className="scenario-changes">{changes.map((item,index)=><div className="scenario-change" key={item.key}><div className="scenario-change-head"><strong>Change {index+1}</strong><button type="button" onClick={()=>removeChange(item.key)}>Remove</button></div><div className="scenario-change-grid">
              <label>Type<select value={item.change_type} onChange={e=>patchChange(item.key,{change_type:e.target.value,value:newChange(e.target.value,item.effective_from).value})}><option value="budget_delta">Monthly budget + / -</option><option value="budget_override">Monthly budget override</option><option value="lump_sum">One-off lump sum</option><option value="payment_holiday">Payment holiday</option><option value="base_payment_override">Base payment override</option><option value="priority_override">Priority override</option><option value="interest_rate">Future interest rate</option></select></label>
              {(accountRequired.has(item.change_type)||item.change_type==="payment_holiday")&&<label>Account {item.change_type==="payment_holiday"?"(blank = all)":""}<select value={item.account_id} onChange={e=>patchChange(item.key,{account_id:e.target.value})}><option value="">{item.change_type==="payment_holiday"?"All plan accounts":"Select account"}</option>{selectedPlan?.members.filter(member=>member.enabled).map(member=><option value={member.account_id} key={member.account_id}>{member.person} · {member.account}</option>)}</select></label>}
              <label>Effective from<input type="date" value={item.effective_from} onChange={e=>patchChange(item.key,{effective_from:e.target.value})}/></label>
              {rangeTypes.has(item.change_type)&&<label>Effective to (optional)<input type="date" value={item.effective_to} onChange={e=>patchChange(item.key,{effective_to:e.target.value})}/></label>}
              {valueRequired.has(item.change_type)&&<label>{item.change_type==="interest_rate"?"Annual rate %":item.change_type==="priority_override"?"Priority":"Value"}<input type="number" step={item.change_type==="priority_override"?"1":"0.01"} value={item.value} onChange={e=>patchChange(item.key,{value:e.target.value})}/></label>}
              {item.change_type==="interest_rate"&&<label>Day count<select value={item.day_count_convention} onChange={e=>patchChange(item.key,{day_count_convention:e.target.value})}><option value="actual_365">Actual / 365</option><option value="actual_366">Actual / 366</option><option value="actual_actual">Actual / Actual</option><option value="30_360">30 / 360</option></select></label>}
            </div><label>Note<input value={item.note} onChange={e=>patchChange(item.key,{note:e.target.value})} placeholder="Why are you testing this change?"/></label></div>)}{!changes.length&&<div className="empty">Add one or more changes to build the scenario.</div>}</div>
            <div className="scenario-actions"><button type="submit" className="primary">{selectedScenarioId?"Update scenario":"Save scenario"}</button><button type="button" onClick={()=>selectedScenarioId&&void loadComparison(selectedScenarioId)}>Recalculate</button></div>
          </form>
          <div className="scenario-list">{planScenarios.map(item=><button key={item.id} type="button" className={String(item.id)===selectedScenarioId?"active":""} onClick={()=>applyScenario(item)}><div><strong>{item.name}</strong><small>{item.changes.length} change{item.changes.length===1?"":"s"} · {item.plan_name}</small></div><span className="scenario-status">{item.status}</span></button>)}</div>
        </div>

        <div className="comparison-column">{comparison?<>
          <section className="cards comparison-cards"><article><small>BASELINE PAYOFF</small><strong className="date-value">{gbDate(comparison.comparison.baseline_payoff_date)}</strong><span>{comparison.baseline.months_generated} months generated</span></article><article><small>SCENARIO PAYOFF</small><strong className="date-value">{gbDate(comparison.comparison.scenario_payoff_date)}</strong><span>{comparison.candidate.months_generated} months generated</span></article><article><small>MONTHS SAVED</small><strong className={(comparison.comparison.months_saved||0)>=0?"comparison-positive":"comparison-negative"}>{comparison.comparison.months_saved??"—"}</strong><span>Positive means earlier payoff</span></article><article><small>INTEREST SAVED</small><strong className={comparison.comparison.interest_saved>=0?"comparison-positive":"comparison-negative"}>{money.format(comparison.comparison.interest_saved)}</strong><span>Against current baseline</span></article></section>
          <div className="panel"><div className="panel-head"><div><p className="eyebrow">SIDE BY SIDE</p><h2>Baseline vs {comparison.scenario.name}</h2></div><span>Deterministic</span></div><table className="comparison-table"><thead><tr><th>Measure</th><th>Baseline</th><th>Scenario</th><th>Difference</th></tr></thead><tbody><tr><td>Projected payoff</td><td>{gbDate(comparison.baseline.payoff_date)}</td><td>{gbDate(comparison.candidate.payoff_date)}</td><td>{comparison.comparison.months_saved==null?"—":`${comparison.comparison.months_saved} months saved`}</td></tr><tr><td>Projected interest</td><td>{money.format(comparison.baseline.projected_interest)}</td><td>{money.format(comparison.candidate.projected_interest)}</td><td className="money">{money.format(-comparison.comparison.interest_saved)}</td></tr><tr><td>Forecast payments</td><td>{money.format(comparison.baseline.total_forecast_payments)}</td><td>{money.format(comparison.candidate.total_forecast_payments)}</td><td className="money">{money.format(comparison.comparison.payment_difference)}</td></tr><tr><td>Remaining at horizon</td><td>{money.format(comparison.baseline.remaining_balance)}</td><td>{money.format(comparison.candidate.remaining_balance)}</td><td className="money">{money.format(comparison.comparison.remaining_balance_difference)}</td></tr></tbody></table></div>
          <div className="panel"><div className="panel-head"><div><p className="eyebrow">ASSUMPTIONS</p><h2>Scenario changes</h2></div><span>{comparison.scenario.changes.length}</span></div><div style={{padding:14}}>{comparison.scenario.changes.map(change=><div key={change.id} className="change-pill">{change.change_type.replaceAll("_"," ")} · {gbDate(change.effective_from)}{change.value!=null?` · ${change.change_type==="interest_rate"?`${change.value}%`:change.change_type==="priority_override"?change.value:money.format(change.value)}`:""}</div>)}</div>{(comparison.candidate.scenario_events||[]).map((event,index)=><div className="scenario-event" key={`${event.date}-${index}`}><span>{gbDate(event.date)}</span><div><strong>{event.account}</strong><small>{event.note}</small></div><div className="amount"><small>AMOUNT</small><strong>{money.format(event.amount)}</strong></div><div className="amount"><small>TO PRINCIPAL</small><strong>{money.format(event.allocated_to_principal)}</strong></div></div>)}</div>
          <div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">ACCOUNT OUTCOMES</p><h2>Effect by account</h2></div><select value={horizon} onChange={e=>{setHorizon(e.target.value);if(selectedScenarioId)void loadComparison(selectedScenarioId)}}><option value="60">5 years</option><option value="120">10 years</option><option value="240">20 years</option></select></div><table className="comparison-table"><thead><tr><th>Account</th><th>Baseline payoff</th><th>Scenario payoff</th><th>Baseline interest</th><th>Scenario interest</th></tr></thead><tbody>{comparison.candidate.accounts.map(item=>{const baseline=baselineAccounts.get(item.account_id);return <tr key={item.account_id}><td><strong>{item.person} · {item.account}</strong></td><td>{gbDate(baseline?.payoff_date)}</td><td>{gbDate(item.payoff_date)}</td><td className="money">{money.format(baseline?.projected_interest||0)}</td><td className="money">{money.format(item.projected_interest)}</td></tr>})}</tbody></table></div>
          <div className="panel table-panel"><div className="panel-head"><div><p className="eyebrow">SCENARIO SCHEDULE</p><h2>First 24 periods</h2></div><span>{comparison.candidate.schedule.length} total</span></div><table className="comparison-table"><thead><tr><th>Month</th><th>Date</th><th>Budget</th><th>Payments</th><th>Active changes</th><th>Remaining</th></tr></thead><tbody>{comparison.candidate.schedule.slice(0,24).map(row=><tr key={row.period}><td>{row.period}</td><td>{gbDate(row.date)}</td><td className="money">{money.format(row.budget)}</td><td>{row.payments.map(payment=><div key={payment.account_id}><strong>{payment.account} {money.format(payment.amount)}</strong></div>)}</td><td>{(row.active_scenario_changes||[]).map(change=><span className="change-pill" key={change}>{change.replaceAll("_"," ")}</span>)}</td><td className="money">{money.format(row.remaining_balance)}</td></tr>)}</tbody></table></div>
        </>:<div className="panel empty-plan"><div className="spark">◇</div><h2>Select or save a scenario</h2><p>Bank of Mum will replay the same dated accounting engine twice—once for the saved baseline and once with your hypothetical changes—then show exactly what moved.</p></div>}

        {planScenarios.length>1&&<div className="panel"><div className="panel-head"><div><p className="eyebrow">MULTI-SCENARIO</p><h2>Compare alternatives</h2></div><span>{planScenarios.length} available</span></div><div className="comparison-select"><div className="comparison-select-row">{planScenarios.map(item=><label key={item.id}><input type="checkbox" checked={selectedCompareIds.includes(item.id)} onChange={e=>setSelectedCompareIds(prev=>e.target.checked?[...prev,item.id]:prev.filter(id=>id!==item.id))}/>{item.name}</label>)}<button type="button" onClick={()=>void compareSelected()}>Compare selected</button></div></div>{many&&<div className="side-by-side"><table className="comparison-table"><thead><tr><th>Scenario</th><th>Payoff</th><th>Months saved</th><th>Interest</th><th>Interest saved</th><th>Remaining</th></tr></thead><tbody>{many.scenarios.map(row=><tr key={row.scenario.id}><td><strong>{row.scenario.name}</strong></td><td>{gbDate(row.payoff_date)}</td><td>{row.months_saved??"—"}</td><td className="money">{money.format(row.projected_interest)}</td><td className="money">{money.format(row.interest_saved)}</td><td className="money">{money.format(row.remaining_balance)}</td></tr>)}</tbody></table></div>}</div>}
        </div>
      </section>
    </main>
  </div></div>
}
