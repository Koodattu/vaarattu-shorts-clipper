"use strict";
let highlightSourceRanges=[];
function highlightSourceTime(seconds){
  const total=Math.round(seconds*1000),whole=Math.floor(total/1000),fraction=total%1000;
  return `${Math.floor(whole/3600)}:${String(Math.floor(whole%3600/60)).padStart(2,"0")}:${String(whole%60).padStart(2,"0")}${fraction?"."+String(fraction).padStart(3,"0"):""}`;
}
function parseHighlightSourceTime(value){
  const parts=value.trim().split(":");
  if(parts.length>3||!parts.length||parts.some(p=>!/^\d+(?:\.\d{1,3})?$/.test(p)))return NaN;
  if(parts.slice(0,-1).some(p=>p.includes("."))||parts.slice(1).some(p=>Number(p)>=60))return NaN;
  return parts.reduce((sum,p)=>sum*60+Number(p),0);
}
function highlightSourceEmbed(source,seconds=0){
  const time=Math.max(0,Math.floor(seconds));
  if(source.provider==="twitch")return `https://player.twitch.tv/?video=v${encodeURIComponent(source.id)}&parent=${encodeURIComponent(window.location.hostname)}&autoplay=false&time=${Math.floor(time/3600)}h${Math.floor(time%3600/60)}m${time%60}s`;
  return `https://www.youtube.com/embed/${encodeURIComponent(source.id)}?autoplay=0&start=${time}&rel=0`;
}
function highlightRangeValid(range){return Number.isFinite(range.start)&&Number.isFinite(range.end)&&range.start>=0&&range.end<=range.source.duration&&range.start<range.end;}
function collectHighlightSources(){
  const included=highlightSourceRanges.filter(r=>r.included);
  if(!included.length)throw Error("Include at least one recording.");
  if(included.some(r=>!highlightRangeValid(r)))throw Error("Check the start and end times of the included recordings.");
  return included.map(r=>({asset:r.source.asset,start_us:Math.round(r.start*1e6),end_us:Math.round(r.end*1e6)}));
}
function updateHighlightSourceSummary(){
  const included=highlightSourceRanges.filter(r=>r.included),valid=included.length&&included.every(highlightRangeValid);
  $("highlight-source-summary").textContent=valid?`${included.length} ${included.length===1?"recording":"recordings"} · ${highlightDuration(included.reduce((sum,r)=>sum+r.end-r.start,0))} selected for this project. Order below is the output order.`:included.length?"Correct the highlighted time ranges before starting.":"Include at least one recording.";
  $("highlight-start").disabled=highlightBusy||!highlightManifest||!valid;
}
function setHighlightSources(manifest){
  const old=new Map(highlightSourceRanges.map(r=>[`${r.source.provider}/${r.source.id}`,r]));
  highlightSourceRanges=manifest.sources.map(source=>{
    const previous=old.get(`${source.provider}/${source.id}`);
    return {source,included:previous?.included??true,start:previous?Math.min(previous.start,source.duration):0,end:previous?Math.min(previous.end,source.duration):source.duration};
  });
  if(!$("highlight-project-title").value.trim())$("highlight-project-title").value=manifest.title.slice(0,150);
  $("highlight-source-setup").hidden=false;paintHighlightSources();
}
function paintHighlightSources(){
  const box=$("highlight-parts");box.replaceChildren();
  box.append(text("p","Choose the range to use from each recording. Preview buttons jump to either boundary; enter times as H:MM:SS or seconds.","muted"));
  highlightSourceRanges.forEach((range,index)=>{
    const source=range.source,card=document.createElement("article");card.className="highlight-source-card";
    const head=document.createElement("div");head.className="highlight-source-head";
    const include=document.createElement("input");include.type="checkbox";include.checked=range.included;include.setAttribute("aria-label",`Include ${source.title}`);
    const label=document.createElement("label");label.className="check";label.append(include,text("span",`${index+1}. ${source.title}`));
    const move=document.createElement("div");move.className="actions";
    for(const [name,delta] of [["Move earlier",-1],["Move later",1]]){
      const button=action(name,()=>{const other=index+delta;[highlightSourceRanges[index],highlightSourceRanges[other]]=[highlightSourceRanges[other],highlightSourceRanges[index]];paintHighlightSources();});
      button.disabled=index+delta<0||index+delta>=highlightSourceRanges.length;button.setAttribute("aria-label",`${name}: ${source.title}`);move.append(button);
    }
    head.append(label,move);card.append(head);
    const content=document.createElement("div");content.className="highlight-source-content";
    const preview=document.createElement("div");preview.className="highlight-source-preview";
    const iframe=document.createElement("iframe");iframe.title=`Preview ${source.title}`;iframe.loading="lazy";iframe.referrerPolicy="strict-origin-when-cross-origin";
    iframe.setAttribute("allow","fullscreen; picture-in-picture");iframe.setAttribute("allowfullscreen","");
    iframe.setAttribute("sandbox","allow-scripts allow-same-origin allow-presentation");
    if(source.provider==="twitch")iframe.className="highlight-source-twitch";
    if(range.included)iframe.src=highlightSourceEmbed(source,range.start);
    preview.append(iframe);
    const fields=document.createElement("fieldset");fields.className="highlight-source-range";fields.disabled=!range.included;
    const legend=text("legend",`Use this range · ${highlightDuration(source.duration)} full recording`);fields.append(legend);
    const status=text("p","","muted");status.setAttribute("role","status");
    const inputs={};
    function update(){
      const valid=highlightRangeValid(range);
      for(const name of ["start","end"]){
        const {input,slider}=inputs[name];
        input.setAttribute("aria-invalid",String(!valid));
        if(Number.isFinite(range[name]))slider.value=String(range[name]);
      }
      status.textContent=valid?`${highlightDuration(range.end-range.start)} selected` : "Start must be before end, within this recording.";
      updateHighlightSourceSummary();
    }
    for(const [name,title] of [["start","Start"],["end","End"]]){
      const row=document.createElement("div");row.className="highlight-source-time-row";
      const wrapper=document.createElement("label");wrapper.append(text("span",title));
      const input=document.createElement("input");input.type="text";input.value=highlightSourceTime(range[name]);input.placeholder="H:MM:SS";input.setAttribute("aria-label",`${title} time: ${source.title}`);
      const slider=document.createElement("input");slider.type="range";slider.min="0";slider.max=String(source.duration);slider.step="0.001";slider.value=String(range[name]);slider.setAttribute("aria-label",`${title} position: ${source.title}`);
      inputs[name]={input,slider};input.oninput=()=>{range[name]=parseHighlightSourceTime(input.value);update();};
      slider.oninput=()=>{range[name]=Number(slider.value);input.value=highlightSourceTime(range[name]);update();};
      const jump=action(`Preview ${name}`,()=>{if(highlightRangeValid(range))iframe.src=highlightSourceEmbed(source,name==="end"?Math.max(range.start,range.end-5):range.start);});
      wrapper.append(input);row.append(wrapper,jump);fields.append(row,slider);
    }
    const reset=action("Use full recording",()=>{range.start=0;range.end=source.duration;inputs.start.input.value=highlightSourceTime(0);inputs.end.input.value=highlightSourceTime(range.end);update();});
    fields.append(status,reset);content.append(preview,fields);card.append(content);
    const fallback=text("a","Open original recording");fallback.href=source.url;fallback.target="_blank";fallback.rel="noopener noreferrer";
    const footer=document.createElement("div");footer.className="highlight-source-footer";footer.append(fallback,text("span","If the embed is unavailable, watch at the source and enter its timestamps here.","muted"));card.append(footer);
    include.onchange=()=>{range.included=include.checked;fields.disabled=!range.included;content.hidden=!range.included;if(range.included)iframe.src=highlightSourceEmbed(source,range.start);else iframe.removeAttribute("src");updateHighlightSourceSummary();};
    content.hidden=!range.included;box.append(card);update();
  });
  updateHighlightSourceSummary();
}

let highlightSavedSourcesKey=null;
function paintSavedHighlightSources(run,model=null){
  const key=JSON.stringify([run?.id,run?.revision,run?.sources,run?.has_draft,Boolean(model)]);
  if(key===highlightSavedSourcesKey)return;
  highlightSavedSourcesKey=key;
  const box=$("highlight-saved-sources");box.replaceChildren();
  if(!run)return;
  if(!(run.sources||[]).length){box.append(text("p","No source recordings were saved for this project.","muted"));return;}
  for(const [index,source] of run.sources.entries()){
    const start=(source.selection_start_us??0)/1e6,end=(source.selection_end_us??source.duration*1e6)/1e6;
    const card=text("article","","highlight-source-card");
    card.append(text("h4",`${index+1}. ${source.title||source.asset}`));
    card.append(text("p",`Input range: ${highlightSourceTime(start)}–${highlightSourceTime(end)} · ${highlightSourceTime(end-start)} selected`));
    card.append(text("p",`Full recording: ${highlightSourceTime(source.duration)}`,"muted"));
    if(source.url){const link=text("a","Open original recording");link.href=source.url;link.target="_blank";link.rel="noopener noreferrer";card.append(link);}
    if(model){
      const ranges=model.ranges.filter(r=>r.asset===source.asset);
      const kept=ranges.reduce((sum,r)=>sum+r.outputEnd-r.outputStart,0);
      card.append(text("p",`In this revision: ${highlightSourceTime(kept)} · ${ranges.length} retained sections`));
      if(ranges.length){
        const details=text("details","","highlight-disclosure");details.append(text("summary","Show retained sections"));
        const list=text("div","","highlight-retained-sections");
        for(const range of ranges){
          const row=text("div","","actions");
          row.append(text("span",`Source ${highlightSourceTime(range.sourceStart)}–${highlightSourceTime(range.sourceEnd)}`));
          row.append(action(`Watch at ${highlightSourceTime(range.outputStart)}`,()=>{
            $("highlight-player").currentTime=range.outputStart;updateHighlightPlayhead();$("highlight-player").focus();
          }));list.append(row);
        }
        details.append(list);card.append(details);
      }
    }else card.append(text("p",run.has_draft?"Retained sections appear when the saved timeline loads. If it fails, use Retry timeline in Review & edit.":"Retained sections will appear when the draft is ready.","muted"));
    box.append(card);
  }
}
