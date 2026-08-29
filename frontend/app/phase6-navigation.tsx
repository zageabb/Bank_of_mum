"use client";

import { useEffect } from "react";

export default function Phase6Navigation(){
  useEffect(()=>{
    function route(event:MouseEvent){
      const target=event.target as HTMLElement|null;
      const button=target?.closest(".rail button") as HTMLButtonElement|null;
      if(!button)return;
      const label=button.querySelector("small")?.textContent?.trim();
      const destination=label==="AI"?"/ai":label==="Settings"?"/settings":"";
      if(!destination)return;
      event.preventDefault();event.stopPropagation();window.location.href=destination;
    }
    document.addEventListener("click",route,true);
    return()=>document.removeEventListener("click",route,true);
  },[]);
  return null;
}
