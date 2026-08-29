"use client";

import { useEffect } from "react";

export default function Phase6Navigation(){
  useEffect(()=>{
    function route(event:MouseEvent){
      const target=event.target as HTMLElement|null;
      const button=target?.closest(".rail button") as HTMLButtonElement|null;
      if(!button)return;
      const label=button.querySelector("small")?.textContent?.trim();
      const destinations:Record<string,string>={People:"/manage",Accounts:"/manage",Reports:"/reports",AI:"/ai",Settings:"/settings"};
      const destination=label?destinations[label]||"":"";
      if(!destination)return;
      event.preventDefault();event.stopPropagation();window.location.href=destination;
    }
    document.addEventListener("click",route,true);
    return()=>document.removeEventListener("click",route,true);
  },[]);
  return null;
}
