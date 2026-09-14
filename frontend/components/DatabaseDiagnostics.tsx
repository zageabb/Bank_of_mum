"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000/api";
type Diagnostics = { database_path:string; accounts:number; people:number; ledger_transactions:number; warning:string|null; version:string; git_commit:string|null };

export default function DatabaseDiagnostics({details=false}:{details?:boolean}) {
  const [data,setData]=useState<Diagnostics|null>(null);
  const [error,setError]=useState("");
  useEffect(()=>{
    fetch(`${API}/diagnostics`).then(async response=>{
      if(!response.ok) throw new Error("Database diagnostics unavailable. Check that the backend is up to date.");
      setData(await response.json());
    }).catch(reason=>setError(reason instanceof Error?reason.message:"Could not load database diagnostics"));
  },[]);
  if(error) return <div className="notice error">{error}</div>;
  if(!data) return null;
  return <>{data.warning&&<div className="notice error" role="alert">{data.warning} <a href="/settings">Database diagnostics</a></div>}{details&&<section className="panel" style={{padding:20,marginBottom:16}}><h2>Database diagnostics</h2><p style={{overflowWrap:"anywhere"}}>{data.database_path}</p><p>{data.accounts} accounts · {data.people} people · {data.ledger_transactions} ledger transactions</p><small>Version {data.version} · Commit {data.git_commit||"unavailable"}</small></section>}</>;
}
