"use client";

import { useEffect, useState } from "react";

type Check={ok:boolean;[key:string]:unknown};
type Ready={ok:boolean;phase:number;version:string;environment:string;checks:Record<string,Check>};
type Diagnostics={ok:boolean;runtime:{version:string;phase:number;environment:string;started_at:string;uptime_seconds:number;python:string;platform:string;data_root:string;log_level:string};readiness:Ready;accounting_integrity:{ok:boolean;database_integrity:string;warnings:string[];counts:Record<string,number>;ledger_integrity:Array<{ok:boolean}>};ollama:{ok:boolean;base_url:string;configured_model:string;models_available?:number;elapsed_ms:number;error?:string;required_for_accounting_readiness:boolean};backup:{directory:string;count:number;latest:null|{filename:string;size:number;modified_at:string}}};

const API=process.env.NEXT_PUBLIC_API_URL||"http://localhost:8000/api";

async function api<T>(path:string,allowServiceUnavailable=false):Promise<T>{
  const response=await fetch(`${API}${path}`,{cache:"no-store"});
  const body=await response.json().catch(()=>({detail:"Request failed"}));
  if(!response.ok&&!(allowServiceUnavailable&&response.status===503))throw new Error(body.detail||`HTTP ${response.status}`);
  return body as T;
}

export default function SystemPage(){
  const [ready,setReady]=useState<Ready|null>(null);
  const [details,setDetails]=useState<Diagnostics|null>(null);
  const [error,setError]=useState("");
  const [loading,setLoading]=useState(true);

  async function refresh(){
    setLoading(true);
    try{
      const [r,d]=await Promise.all([api<Ready>("/health/ready",true),api<Diagnostics>("/system/diagnostics")]);
      setReady(r);setDetails(d);setError("");
    }catch(reason){setError(reason instanceof Error?reason.message:"Could not load system status")}
    finally{setLoading(false)}
  }
  useEffect(()=>{void refresh();const timer=setInterval(()=>void refresh(),30000);return()=>clearInterval(timer)},[]);

  const status=Boolean(details?.ok&&ready?.ok);
  return <main className="phase8-page">
    <section className="phase8-head"><div><p className="eyebrow">BANK OF MUM · PHASE 8</p><h1>System status</h1><p>Production readiness, accounting integrity and runtime diagnostics.</p></div><div className={`phase8-overall ${status?"ok":"review"}`}>{loading?"Checking…":status?"READY":"REVIEW"}</div></section>
    {error&&<div className="notice error">{error}</div>}
    <section className="phase8-grid">
      <article className="panel"><p className="eyebrow">RUNTIME</p><h2>{details?.runtime.version||"—"}</h2><dl><dt>Environment</dt><dd>{details?.runtime.environment||"—"}</dd><dt>Uptime</dt><dd>{details?`${Math.round(details.runtime.uptime_seconds)} sec`:"—"}</dd><dt>Python</dt><dd>{details?.runtime.python||"—"}</dd><dt>Log level</dt><dd>{details?.runtime.log_level||"—"}</dd></dl></article>
      <article className="panel"><p className="eyebrow">DATABASE</p><h2>{ready?.checks.database?.ok?"Ready":"Review"}</h2><dl><dt>Journal</dt><dd>{String(ready?.checks.database?.journal_mode||"—")}</dd><dt>Foreign keys</dt><dd>{ready?.checks.database?.foreign_keys?"On":"Off"}</dd><dt>Busy timeout</dt><dd>{String(ready?.checks.database?.busy_timeout_ms||"—")} ms</dd><dt>Schema</dt><dd>{ready?.checks.schema?.ok?"Complete":"Missing tables"}</dd></dl></article>
      <article className="panel"><p className="eyebrow">STORAGE</p><h2>{ready?.checks.data_storage?.ok&&ready?.checks.backup_storage?.ok?"Ready":"Review"}</h2><dl><dt>Data free</dt><dd>{String(ready?.checks.data_storage?.free_mb||"—")} MB</dd><dt>Backup free</dt><dd>{String(ready?.checks.backup_storage?.free_mb||"—")} MB</dd><dt>Backups</dt><dd>{details?.backup.count??"—"}</dd><dt>Latest</dt><dd>{details?.backup.latest?.filename||"None yet"}</dd></dl></article>
      <article className="panel"><p className="eyebrow">OLLAMA</p><h2>{details?.ollama.ok?"Online":"Optional / offline"}</h2><dl><dt>Server</dt><dd>{details?.ollama.base_url||"—"}</dd><dt>Model</dt><dd>{details?.ollama.configured_model||"—"}</dd><dt>Models found</dt><dd>{details?.ollama.models_available??"—"}</dd><dt>Accounting readiness</dt><dd>Not required</dd></dl></article>
    </section>
    <section className="panel phase8-integrity"><div className="panel-head"><div><p className="eyebrow">DEEP VERIFICATION</p><h2>Accounting integrity</h2></div><button onClick={()=>void refresh()} disabled={loading}>Run checks</button></div>
      <div className={`phase8-integrity-status ${details?.accounting_integrity.ok?"ok":"review"}`}>{details?.accounting_integrity.ok?"PASS":"REVIEW"}</div>
      <div className="phase8-counts">{Object.entries(details?.accounting_integrity.counts||{}).map(([key,value])=><div key={key}><strong>{value}</strong><span>{key.replaceAll("_"," ")}</span></div>)}</div>
      {(details?.accounting_integrity.warnings||[]).length?<ul>{details?.accounting_integrity.warnings.map(item=><li key={item}>{item}</li>)}</ul>:<p>No integrity warnings reported.</p>}
    </section>
    <section className="phase8-actions"><a href="/maintenance">Backups & restore</a><a href="/settings">AI settings</a><a href="/">Main workspace</a></section>
  </main>
}
