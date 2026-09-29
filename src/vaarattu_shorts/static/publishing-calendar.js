"use strict";
const publishingCalendarZone="Europe/Helsinki";
let publishingCalendarMonth=null,publishingCalendarEntries=[],publishingCalendarDetail=null;
function publishingDateKey(value){
  if(!value)return null;const date=new Date(value);if(!Number.isFinite(date.getTime()))return null;
  const parts=Object.fromEntries(new Intl.DateTimeFormat("en-GB",{timeZone:publishingCalendarZone,year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(date).map(p=>[p.type,p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
function publishingCalendarDateTime(value){return new Date(value).toLocaleString(undefined,{timeZone:publishingCalendarZone,dateStyle:"medium",timeStyle:"short"});}
function publishingReceiptDate(clip,receipt){
  const job=clip.publication;
  const choices=receipt.status==="posted"?[
    [receipt.published_at,"Published",false],[receipt.recorded_at,"Manually recorded",true],
    [receipt.due_at||job?.item.due_at,"Scheduled date; publication time unavailable",true],
    [receipt.checked_at,"Publication confirmed; exact posting time unavailable",true],
  ]:[[receipt.due_at||job?.item.due_at||clip.plan?.scheduled_at,job?"Scheduled":"Local plan only",false],
     [receipt.attempted_at||job?.created_at,"Submission started",true]];
  const valid=choices.find(([at])=>publishingDateKey(at));
  return valid?{at:valid[0],label:valid[1],estimated:valid[2]}:{at:null,label:"Date unavailable",estimated:true};
}
function publishingCalendarModel(clips,filter="all"){
  const groups=new Map();
  for(const clip of clips){
    const job=clip.publication,receipts=job?.receipts||clip.plan?.deliveries||{};
    for(const [platform,receipt] of Object.entries(receipts)){
      const kind=receipt.status==="posted"?"posted":!job?"planned":["scheduled","sending"].includes(receipt.status)?"scheduled":"attention";
      if(filter!=="all"&&filter!==kind)continue;
      const timing=publishingReceiptDate(clip,receipt),day=publishingDateKey(timing.at),key=`${clip.id}:${day||"undated"}`;
      if(!groups.has(key))groups.set(key,{key,clip,day,title:job?.item.title||clip.copy?.title||clip.title,platforms:[]});
      groups.get(key).platforms.push({platform,receipt,kind,...timing});
    }
  }
  return [...groups.values()].map(entry=>{
    const kinds=new Set(entry.platforms.map(p=>p.kind));
    entry.kind=kinds.has("attention")?"attention":kinds.size>1?"mixed":[...kinds][0];
    entry.at=entry.platforms.map(p=>p.at).filter(Boolean).sort((a,b)=>Date.parse(a)-Date.parse(b))[0]||null;
    entry.estimated=entry.platforms.some(p=>p.estimated);return entry;
  }).sort((a,b)=>(Date.parse(a.at)||0)-(Date.parse(b.at)||0)||a.title.localeCompare(b.title));
}
function publishingCalendarLabel(kind){return {scheduled:"Scheduled",posted:"Published",mixed:"Partly published",attention:"Needs attention",planned:"Local plan only"}[kind];}
function publishingCalendarButton(entry){
  const button=text("button","",`publishing-calendar-event ${entry.kind}`);button.type="button";
  const clock=entry.at?new Date(entry.at).toLocaleTimeString("en-GB",{timeZone:publishingCalendarZone,hour:"2-digit",minute:"2-digit"}):"No date";
  const state=`${entry.estimated?"~ ":""}${clock} · ${publishingCalendarLabel(entry.kind)}`;
  button.append(text("span",state,"publishing-calendar-event-state"),text("strong",entry.title),text("span",entry.platforms.map(p=>({youtube:"YT",instagram:"IG",tiktok:"TikTok"}[p.platform]||p.platform)).join(" · "),"publishing-calendar-event-platforms"));
  button.title=`${entry.title} — ${state} — ${entry.platforms.map(p=>`${publishingNames[p.platform]||p.platform}: ${publishingStatuses[p.receipt.status]||p.receipt.status}`).join("; ")}`;
  button.setAttribute("aria-label",button.title);button.onclick=()=>openPublishingCalendar({clipId:entry.clip.id});return button;
}
function paintPublishingCalendar(data){
  const today=publishingDateKey(new Date());if(!publishingCalendarMonth)publishingCalendarMonth=today.slice(0,7);
  const filter=$("publishing-calendar-filter").value||"all";
  publishingCalendarEntries=publishingCalendarModel(data.clips,filter);
  const [year,month]=publishingCalendarMonth.split("-").map(Number),first=new Date(Date.UTC(year,month-1,1));
  const offset=(first.getUTCDay()+6)%7,days=new Date(Date.UTC(year,month,0)).getUTCDate();
  $("publishing-calendar-heading").textContent=first.toLocaleDateString(undefined,{timeZone:"UTC",month:"long",year:"numeric"});
  $("publishing-calendar-month").value=publishingCalendarMonth;
  const visible=publishingCalendarEntries.filter(e=>e.day?.startsWith(publishingCalendarMonth));
  const count=new Set(visible.map(e=>e.clip.id)).size;
  $("publishing-calendar-summary").textContent=count?`${count} ${count===1?"clip":"clips"} this month · Click a post to preview it and see platform details.`:"No posts match this month. Choose another month or filter, or fill the scheduled queue.";
  $("publishing-sync-status").textContent=data.refreshed_at?`Buffer last synced ${publishingCalendarDateTime(data.refreshed_at)}. Dates marked ~ use a saved schedule or confirmation time.`:"Showing saved records. Sync with Buffer to update delivery status and publication dates.";
  const grid=$("publishing-calendar-grid");grid.replaceChildren();
  for(let i=0;i<Math.ceil((offset+days)/7)*7;i++){
    const date=new Date(Date.UTC(year,month-1,i-offset+1)),key=date.toISOString().slice(0,10);
    const cell=text("section","",`publishing-calendar-day${key.slice(0,7)!==publishingCalendarMonth?" outside":""}${key===today?" today":""}`);
    const title=date.toLocaleDateString(undefined,{timeZone:"UTC",weekday:"long",day:"numeric",month:"long",year:"numeric"});cell.setAttribute("aria-label",title);
    const day=text("button",String(date.getUTCDate()),"publishing-calendar-day-number");day.type="button";day.setAttribute("aria-label",`Show posts for ${title}`);if(key===today)day.setAttribute("aria-current","date");day.onclick=()=>openPublishingCalendar({day:key});cell.append(day);
    const entries=publishingCalendarEntries.filter(e=>e.day===key);
    for(const entry of entries.slice(0,3))cell.append(publishingCalendarButton(entry));
    if(entries.length>3){const more=text("button",`+${entries.length-3} more`,"publishing-calendar-more");more.type="button";more.onclick=()=>openPublishingCalendar({day:key});cell.append(more);}
    grid.append(cell);
  }
  const undated=$("publishing-calendar-undated"),items=publishingCalendarEntries.filter(e=>!e.day);undated.replaceChildren();undated.hidden=!items.length;
  if(items.length){undated.append(text("h3","Posts without a saved date"));for(const entry of items)undated.append(publishingCalendarButton(entry));}
  if($("publishing-calendar-dialog").open&&publishingCalendarDetail)paintPublishingCalendarDetail();
}
function paintPublishingCalendarDetail(){
  const panel=$("publishing-calendar-detail");for(const video of panel.querySelectorAll("video"))video.pause();panel.replaceChildren();
  if(publishingCalendarDetail.clipId){
    const clip=publishingData.clips.find(c=>c.id===publishingCalendarDetail.clipId);
    $("publishing-calendar-detail-heading").textContent="Post details";
    if(!clip){panel.append(text("p","This post is no longer in the saved records."));return;}
    if(clip.publication)panel.append(publishingClipCard(clip,publishingStorage));
    else{
      panel.append(text("h3",clip.title),publishingVideo(clip.id,clip.plan.revision));
      for(const [platform,receipt] of Object.entries(clip.plan.deliveries)){
        const timing=publishingReceiptDate(clip,receipt);
        panel.append(text("p",`${publishingNames[platform]||platform}: ${receipt.status==="posted"?"Manually recorded as published":"Local plan only — not scheduled in Buffer"}`));
        if(timing.at)panel.append(text("p",`${timing.label}: ${publishingCalendarDateTime(timing.at)}`,"muted"));
        if(receipt.url)panel.append(publishingLink("Open published post",receipt.url));
      }
      panel.append(action("Open local posting plan",()=>{$("publishing-calendar-dialog").close();$("publishing-manual-plan").open=true;$("publishing-manual-plan").scrollIntoView({block:"start"});}));
    }
  }else{
    const day=publishingCalendarDetail.day;
    $("publishing-calendar-detail-heading").textContent=new Date(day+"T12:00:00Z").toLocaleDateString(undefined,{timeZone:"UTC",weekday:"long",day:"numeric",month:"long",year:"numeric"});
    const entries=publishingCalendarEntries.filter(e=>e.day===day);
    if(!entries.length)panel.append(text("p","No posts match this day and filter."));
    for(const entry of entries)panel.append(publishingCalendarButton(entry));
  }
}
function openPublishingCalendar(selection){publishingCalendarDetail=selection;paintPublishingCalendarDetail();if(!$("publishing-calendar-dialog").open)$("publishing-calendar-dialog").showModal();}
function movePublishingMonth(delta){
  if(!publishingData)return;const [year,month]=publishingCalendarMonth.split("-").map(Number);
  publishingCalendarMonth=new Date(Date.UTC(year,month-1+delta,1)).toISOString().slice(0,7);paintPublishingCalendar(publishingData);
}
$("publishing-calendar-prev").onclick=()=>movePublishingMonth(-1);
$("publishing-calendar-next").onclick=()=>movePublishingMonth(1);
$("publishing-calendar-today").onclick=()=>{publishingCalendarMonth=publishingDateKey(new Date()).slice(0,7);if(publishingData)paintPublishingCalendar(publishingData);};
$("publishing-calendar-month").onchange=()=>{const value=$("publishing-calendar-month").value;if(/^\d{4}-\d{2}$/.test(value)&&Number(value.slice(-2))>=1&&Number(value.slice(-2))<=12){publishingCalendarMonth=value;if(publishingData)paintPublishingCalendar(publishingData);}};
$("publishing-calendar-filter").onchange=()=>{if(publishingData)paintPublishingCalendar(publishingData);};
$("publishing-calendar-close").onclick=()=>$("publishing-calendar-dialog").close();
$("publishing-calendar-dialog").onclose=()=>{for(const video of $("publishing-calendar-detail").querySelectorAll("video"))video.pause();publishingCalendarDetail=null;};
