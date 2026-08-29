"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";

type AISettings = { provider:string; ollama_url:string; ollama_model:string; max_tool_calls:number; timeout_seconds:number; accounting_mode:string; scenario_proposals:string };
type ChatMessage = { role:"user"|"assistant"; content:string; tool_events?:ToolEvent[] };
type ToolEvent = { tool:string; summary:string; error?:string };
type ChatResponse = { reply:string; tool_events:ToolEvent[]; model:string; provider:string; usage:{input_tokens:number;output_tokens:number}; accounting_mode:string; scenario_proposals:string };

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";

async function api(path:string, options?:RequestInit){
  const response=await fetch(`${API}${path}`,options);
  if(!response.ok){const body=await response.json().catch(()=>({detail:"Request failed"}));throw new Error(body.detail||"Request failed")}
  return response.json();
}

const starters = [
  "What is the total outstanding balance today, split between principal and interest?",
  "Which account is currently costing the most interest?",
  "When will the current baseline payment plan clear all debts?",
  "Compare my saved scenarios and tell me which saves the most interest.",
  "Prepare a draft scenario that adds £100 per month to my main payment plan.",
];

export default function AIPage(){
  const [settings,setSettings]=useState<AISettings|null>(null);
  const [messages,setMessages]=useState<ChatMessage[]>([{role:"assistant",content:"Ask me about balances, payments, interest, payoff dates, plans or scenarios. I use the Bank of Mum accounting tools for the numbers; I do not post or alter ledger transactions."}]);
  const [input,setInput]=useState("");
  const [allowProposals,setAllowProposals]=useState(true);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState("");
  const [usage,setUsage]=useState({input_tokens:0,output_tokens:0});

  useEffect(()=>{api("/ai/settings").then(setSettings).catch(reason=>setError(reason instanceof Error?reason.message:"Could not load AI settings"))},[]);
  const conversation=useMemo(()=>messages.filter((item,index)=>!(index===0&&item.role==="assistant")).map(({role,content})=>({role,content})),[messages]);

  async function send(event?:FormEvent, preset?:string){
    event?.preventDefault();
    const text=(preset??input).trim();if(!text||busy)return;
    const nextUser:ChatMessage={role:"user",content:text};
    const outbound=[...conversation,{role:"user" as const,content:text}];
    setMessages(prev=>[...prev,nextUser]);setInput("");setBusy(true);setError("");
    try{
      const result:ChatResponse=await api("/ai/chat",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({messages:outbound,allow_scenario_proposals:allowProposals})});
      setMessages(prev=>[...prev,{role:"assistant",content:result.reply,tool_events:result.tool_events}]);
      setUsage(result.usage);setSettings(prev=>prev?{...prev,ollama_model:result.model}:prev);
    }catch(reason){setError(reason instanceof Error?reason.message:"AI request failed")}
    finally{setBusy(false)}
  }

  return <div className="ai-page"><header className="topbar"><div className="brand"><span className="brand-mark">BM</span><span>Bank of Mum</span></div><div className="workspace-title">Accounting AI <span>Phase 6 · deterministic tools</span></div><div className={`connection ${settings?"online":""}`}><i/>{settings?settings.ollama_model:"Loading"}</div></header><aside className="phase6-rail"><a href="/"><span>⌂</span><small>Home</small></a><a href="/scenarios"><span>↗</span><small>Scenarios</small></a><a className="active" href="/ai"><span>✦</span><small>AI</small></a><a href="/settings"><span>⚙</span><small>Settings</small></a></aside><main className="phase6-workspace"><section className="page-head"><div><p className="eyebrow">BANK OF MUM AI</p><h1>Ask the accounts</h1><p>Natural-language access to the immutable ledger, dated interest, payment plans and what-if engine.</p></div><a className="primary" href="/settings" style={{textDecoration:"none"}}>AI settings</a></section>{error&&<div className="notice error">{error}</div>}<section className="ai-layout"><div className="panel chat-panel"><div className="panel-head"><div><p className="eyebrow">CONVERSATION</p><h2>Accounting assistant</h2></div><span>{busy?"Working…":"Read-only"}</span></div><div className="chat-stream">{messages.map((item,index)=><div key={index} className={`chat-message ${item.role}`}><div className="chat-avatar">{item.role==="assistant"?"AI":"You"}</div><div className="chat-bubble">{item.content}{item.tool_events&&item.tool_events.length>0&&<div className="tool-events">{item.tool_events.map((event,eventIndex)=><span className="tool-event" key={`${event.tool}-${eventIndex}`}>{event.summary}</span>)}</div>}</div></div>)}{busy&&<div className="chat-message assistant"><div className="chat-avatar">AI</div><div className="chat-bubble">Checking the accounting tools…</div></div>}</div><form className="chat-compose" onSubmit={event=>void send(event)}><textarea value={input} onChange={event=>setInput(event.target.value)} placeholder="e.g. If I pay an extra £100 a month, how much sooner will everything be cleared?"/><div className="chat-compose-actions"><label><input type="checkbox" checked={allowProposals} onChange={event=>setAllowProposals(event.target.checked)}/> Allow AI to save draft scenarios</label><button className="primary" disabled={busy||!input.trim()}>{busy?"Running tools…":"Send"}</button></div></form></div><aside className="ai-side"><div className="panel model-card"><strong>{settings?.ollama_model||"Ollama"}</strong><span>{settings?.ollama_url||"Backend settings unavailable"}</span><small>{settings?.accounting_mode==="read_only"?"Accounting read-only":"Check settings"}</small></div><div className="panel quick-prompts"><div className="panel-head" style={{margin:"-14px -14px 10px"}}><div><p className="eyebrow">TRY ASKING</p><h2>Quick questions</h2></div></div>{starters.map(item=><button key={item} disabled={busy} onClick={()=>void send(undefined,item)}>{item}</button>)}</div><div className="panel ai-safety"><h3>Safety boundary</h3><p>The model can read deterministic calculations and prepare draft what-if scenarios. The following accounting operations are intentionally absent from its tool set.</p><div className="safety-grid"><div><small>Ledger writes</small><strong>Blocked</strong></div><div><small>Rate writes</small><strong>Blocked</strong></div><div><small>Corrections</small><strong>Human only</strong></div><div><small>Scenarios</small><strong>Draft only</strong></div></div></div><div className="panel settings-note"><strong>Last request usage</strong><br/>{usage.input_tokens.toLocaleString()} input tokens · {usage.output_tokens.toLocaleString()} output tokens. Tool calculations are performed by Bank of Mum, not by the language model.</div></aside></section></main></div>
}
