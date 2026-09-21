"use strict";
let highlightTimeline={key:null,model:null,zoom:1,active:null};
function highlightClock(seconds){
  const n=Math.max(0,Math.floor(seconds));
  return `${Math.floor(n/3600)}:${String(Math.floor(n%3600/60)).padStart(2,"0")}:${String(n%60).padStart(2,"0")}`;
}
function highlightTimelineModel(sources,plan){
  let offset=0,output=0;
  const parts=sources.map(source=>{
    const duration=Math.max(Number(source.duration)||0,...(plan.retained||[]).filter(r=>r.asset===source.asset).map(r=>r.end_us/1e6));
    const part={...source,offset,duration};offset+=duration;return part;
  });
  const scores=new Map((plan.rankings||[]).map(r=>[r.id,r]));
  const ranges=(plan.retained||[]).map(span=>{
    const part=parts.find(p=>p.asset===span.asset);
    if(!part||span.end_us<=span.start_us)return null;
    // Match the renderer's per-section 30 fps rounding, including ties to even.
    const rawFrames=(span.end_us-span.start_us)/1e6*30,base=Math.floor(rawFrames);
    const frames=Math.max(1,rawFrames-base===0.5?base+(base%2):Math.round(rawFrames));
    const duration=frames/30;
    const range={...span,sourceStart:span.start_us/1e6,sourceEnd:span.end_us/1e6,
      start:part.offset+span.start_us/1e6,end:part.offset+span.end_us/1e6,
      outputStart:output,outputEnd:output+duration,part,ranking:scores.get(span.sequence)};
    output+=duration;return range;
  }).filter(Boolean);
  return {parts,ranges,duration:offset,outputDuration:output};
}
async function loadHighlightTimeline(run,force=false){
  const panel=$("highlight-timeline"),key=run?.has_draft?`${run.id}/${run.revision}`:null;
  panel.hidden=!key;
  if(!key){highlightTimeline={key:null,model:null,zoom:1,active:null};return;}
  if(!force&&highlightTimeline.key===key)return;
  const state={key,model:null,zoom:1,active:null};highlightTimeline=state;
  $("highlight-timeline-summary").textContent="Loading the saved edit...";
  $("highlight-timeline-retry").hidden=true;
  $("highlight-timeline-viewport").hidden=true;
  $("highlight-timeline-detail").textContent="";
  try{
    const plan=await api(`/api/highlights/${run.id}/plan?revision=${run.revision}`);
    if(highlightTimeline!==state)return;
    state.model=highlightTimelineModel(run.sources||[],plan);
    const model=state.model;
    if(!model.duration||!model.ranges.length){$("highlight-timeline-summary").textContent="This draft has no saved timeline sections.";return;}
    $("highlight-timeline-summary").textContent=`${highlightClock(model.duration)} recording \u2192 ${highlightClock(model.outputDuration)} edited video \u00b7 ${model.ranges.length} retained sections`;
    $("highlight-timeline-viewport").hidden=false;
    $("highlight-timeline-viewport").scrollLeft=0;
    paintHighlightTimeline();
  }catch(e){
    if(highlightTimeline!==state)return;
    $("highlight-timeline-summary").textContent="The saved timeline could not be loaded. You can retry without changing the video.";
    $("highlight-timeline-retry").hidden=false;
    $("highlight-timeline-retry").onclick=()=>loadHighlightTimeline(run,true);
  }
}
function highlightRangeLabel(range){
  return `${range.part.title||range.asset} \u00b7 source ${highlightClock(range.sourceStart)}\u2013${highlightClock(range.sourceEnd)} \u00b7 video ${highlightClock(range.outputStart)}\u2013${highlightClock(range.outputEnd)}${range.ranking?` \u00b7 score ${range.ranking.score}`:""}`;
}
function paintHighlightTimeline(){
  const {model,zoom}=highlightTimeline;if(!model)return;
  $("highlight-timeline-track").style.width=`${zoom*100}%`;
  $("highlight-zoom").value=String(zoom);$("highlight-zoom-value").textContent=`${zoom}\u00d7`;
  $("highlight-zoom-out").disabled=zoom===1;$("highlight-zoom-in").disabled=zoom===64;
  const ruler=$("highlight-timeline-ruler"),parts=$("highlight-timeline-parts"),ranges=$("highlight-timeline-ranges");
  ruler.replaceChildren();parts.replaceChildren();ranges.replaceChildren();
  const target=model.duration/(zoom*8);
  const step=[1,2,5,10,15,30,60,120,300,600,900,1800,3600,7200,14400,86400].find(n=>n>=target)||86400;
  for(let t=0;t<model.duration;t+=step){const tick=text("span",highlightClock(t));tick.style.left=`${t/model.duration*100}%`;ruler.append(tick);}
  for(const part of model.parts){
    const label=text("span",part.title||part.asset);label.title=part.title||part.asset;
    label.style.left=`${part.offset/model.duration*100}%`;label.style.width=`${part.duration/model.duration*100}%`;parts.append(label);
  }
  model.ranges.forEach(range=>{
    const label=highlightRangeLabel(range),button=text("button","");
    button.onclick=event=>{
      const bounds=button.getBoundingClientRect();
      const fraction=event?.detail?Math.max(0,Math.min(0.999,(event.clientX-bounds.left)/bounds.width)):0;
      $("highlight-player").currentTime=range.outputStart+fraction*(range.outputEnd-range.outputStart);
      updateHighlightPlayhead();
    };
    button.type="button";button.title=label;button.setAttribute("aria-label",label);
    button.style.left=`${range.start/model.duration*100}%`;button.style.width=`${(range.end-range.start)/model.duration*100}%`;
    button.onfocus=button.onmouseenter=()=>{$("highlight-timeline-detail").textContent=label+(range.ranking?.reason?` \u00b7 ${range.ranking.reason}`:"");};
    range.button=button;ranges.append(button);
  });
  highlightTimeline.active=null;updateHighlightPlayhead();
}
function updateHighlightPlayhead(){
  const {model}=highlightTimeline,head=$("highlight-timeline-playhead");
  if(!model){head.hidden=true;return;}
  const time=$("highlight-player").currentTime||0;
  const range=model.ranges.find(r=>time>=r.outputStart&&time<r.outputEnd);
  head.hidden=!range;
  if(highlightTimeline.active!==range){
    highlightTimeline.active?.button?.removeAttribute("aria-current");
    range?.button?.setAttribute("aria-current","true");highlightTimeline.active=range;
  }
  if(!range)return;
  const source=range.start+(range.end-range.start)*(time-range.outputStart)/(range.outputEnd-range.outputStart);
  head.style.left=`${source/model.duration*100}%`;
}
function zoomHighlightTimeline(value){
  if(!highlightTimeline.model)return;
  const viewport=$("highlight-timeline-viewport"),old=highlightTimeline.zoom;
  const center=(viewport.scrollLeft+viewport.clientWidth/2)/(viewport.clientWidth*old);
  highlightTimeline.zoom=Math.max(1,Math.min(64,Number(value)||1));paintHighlightTimeline();
  viewport.scrollLeft=center*viewport.clientWidth*highlightTimeline.zoom-viewport.clientWidth/2;
}
$("highlight-zoom").oninput=event=>zoomHighlightTimeline(event.target.value);
$("highlight-zoom-in").onclick=()=>zoomHighlightTimeline(highlightTimeline.zoom*2);
$("highlight-zoom-out").onclick=()=>zoomHighlightTimeline(Math.floor(highlightTimeline.zoom/2));
$("highlight-zoom-fit").onclick=()=>zoomHighlightTimeline(1);
$("highlight-timeline-locate").onclick=()=>{
  const head=$("highlight-timeline-playhead"),viewport=$("highlight-timeline-viewport");
  if(!head.hidden)viewport.scrollLeft=parseFloat(head.style.left)/100*viewport.clientWidth*highlightTimeline.zoom-viewport.clientWidth/2;
};
$("highlight-player").addEventListener("timeupdate",updateHighlightPlayhead);
$("highlight-player").addEventListener("seeked",updateHighlightPlayhead);
