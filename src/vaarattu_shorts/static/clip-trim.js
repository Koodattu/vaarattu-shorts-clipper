"use strict";
let clipTrim=null,trimBusy=false,trimPlaying=false,trimMediaReady=false;
function trimClock(us){
  const seconds=Math.max(0,us/1e6),whole=Math.floor(seconds);
  return `${Math.floor(whole/3600)}:${String(Math.floor(whole%3600/60)).padStart(2,"0")}:${(seconds%60).toFixed(2).padStart(5,"0")}`;
}
function trimSnap(value,edge,words){
  for(;;){
    const crossing=words.filter(w=>w.start_us<value&&value<w.end_us);if(!crossing.length)return value;
    value=edge==="start"?Math.min(...crossing.map(w=>w.start_us)):Math.max(...crossing.map(w=>w.end_us));
  }
}
function trimPosition(node,start,end){
  const span=clipTrim.max_us-clipTrim.min_us;
  node.style.left=`${(start-clipTrim.min_us)/span*100}%`;
  if(end!==undefined)node.style.width=`${(end-start)/span*100}%`;
}
function trimControls(){
  const changed=clipTrim&&(clipTrim.start!==clipTrim.clip.start_us||clipTrim.end!==clipTrim.clip.end_us);
  $("trim-save").disabled=trimBusy||!trimMediaReady||!changed||clipTrim.end-clipTrim.start<3e6;
  $("trim-close").disabled=trimBusy;$("trim-fields").disabled=trimBusy;
  $("trim-preview").disabled=$("trim-set-start").disabled=$("trim-set-end").disabled=trimBusy||!trimMediaReady;
}
function paintTrim(){
  if(!clipTrim)return;
  const {start,end,words}=clipTrim;
  for(const edge of ["start","end"]){
    $("trim-"+edge).value=(clipTrim[edge]/1e6).toFixed(3);
    const handle=$("trim-"+edge+"-handle");handle.value=clipTrim[edge];handle.setAttribute("aria-valuetext",trimClock(clipTrim[edge]));
  }
  trimPosition($("trim-selected"),start,end);
  $("trim-summary").textContent=`${trimClock(start)} – ${trimClock(end)} · ${((end-start)/1e6).toFixed(2)} seconds before internal cuts`;
  const spoken=words.filter(w=>start<=w.start_us&&w.end_us<=end&&!(clipTrim.clip.speech_cuts||[]).some(c=>c.start_us<=w.start_us&&w.end_us<=c.end_us));
  $("trim-speech-summary").textContent=spoken.length?`Opening: ${spoken.slice(0,8).map(w=>w.text).join(" ")} … Ending: ${spoken.slice(-8).map(w=>w.text).join(" ")}`:"No speech in this range.";
  trimControls();
}
function setTrimEdge(edge,value,snap=true){
  if(!clipTrim||trimBusy||!Number.isFinite(value))return;
  value=Math.round(Math.max(clipTrim.min_us,Math.min(clipTrim.max_us,value)));
  value=edge==="start"?Math.min(value,clipTrim.end-3e6):Math.max(value,clipTrim.start+3e6);
  if(snap)value=trimSnap(value,edge,clipTrim.words);
  if(value<clipTrim.min_us||value>clipTrim.max_us){$("trim-message").textContent="Move this edge farther inside the available range to keep the whole word.";return;}
  clipTrim[edge]=value;trimPlaying=false;$("trim-player").pause();
  if(trimMediaReady)$("trim-player").currentTime=Math.max(0,(value-clipTrim.origin_us)/1e6-(edge==="end"?0.8:0));
  $("trim-message").textContent="";paintTrim();
}
async function openClipTrim(){
  const queued=reviewQueue[0];
  if(reviewBusy||reviewRendering||!queued||queued.status!=="ready"||!queued.has_preview)return;
  reviewBusy=true;reviewControls();$("review-player").pause();trimBusy=true;
  try{
    const data=await api(`/api/clips/${queued.id}/trim`);
    clipTrim={...data,start:data.clip.start_us,end:data.clip.end_us};trimPlaying=false;trimMediaReady=false;
    $("trim-message").textContent="Loading the original recording…";
    $("trim-availability").textContent=`Available: ${trimClock(data.min_us)} – ${trimClock(data.max_us)} in the original recording.`;
    for(const edge of ["start","end"]){
      const handle=$("trim-"+edge+"-handle");handle.min=data.min_us;handle.max=data.max_us;
      $("trim-"+edge).min=data.min_us/1e6;$("trim-"+edge).max=data.max_us/1e6;
    }
    $("trim-ruler").replaceChildren();$("trim-speech").replaceChildren();$("trim-cuts").replaceChildren();
    for(let i=0;i<=4;i++)$("trim-ruler").append(text("span",trimClock(data.min_us+(data.max_us-data.min_us)*i/4)));
    trimPosition($("trim-original"),data.clip.start_us,data.clip.end_us);
    for(const word of data.words){const bar=document.createElement("span");trimPosition(bar,Math.max(data.min_us,word.start_us),Math.min(data.max_us,word.end_us));$("trim-speech").append(bar);}
    for(const cut of data.clip.speech_cuts||[]){const bar=document.createElement("span");trimPosition(bar,cut.start_us,cut.end_us);$("trim-cuts").append(bar);}
    const player=$("trim-player"),session=clipTrim;
    player.onloadedmetadata=()=>{
      if(clipTrim!==session)return;
      trimMediaReady=true;player.currentTime=Math.max(0,(session.start-session.origin_us)/1e6);
      $("trim-message").textContent="";trimControls();
    };
    player.src=`/api/artifacts/${data.clip.id}/source?revision=${data.clip.revision}`;player.load();
    $("trim-dialog").showModal();paintTrim();
  }catch(e){reviewMessage(e.message,true);clipTrim=null;reviewBusy=false;reviewControls();}
  finally{trimBusy=false;trimControls();}
}
async function previewTrim(){
  if(!clipTrim||!trimMediaReady||trimBusy)return;
  trimPlaying=true;const player=$("trim-player");player.currentTime=(clipTrim.start-clipTrim.origin_us)/1e6;
  try{await player.play();}catch(e){trimPlaying=false;$("trim-message").textContent="Could not play the source preview. Use the player's controls to retry.";}
}
async function saveClipTrim(event){
  event.preventDefault();if(trimBusy||!clipTrim)return;
  setTrimEdge("start",clipTrim.start);setTrimEdge("end",clipTrim.end);
  trimControls();if($("trim-save").disabled)return;
  trimBusy=true;trimControls();$("trim-message").textContent="Saving trim…";
  try{
    const clip=clipTrim.clip;
    const result=await api(`/api/clips/${clip.id}/trim`,{method:"POST",body:JSON.stringify({expected_revision:clip.revision,start_us:clipTrim.start,end_us:clipTrim.end})});
    reviewLast=null;reviewRendering={id:clip.id,...result};
    reviewQueue[0]={...clip,revision:result.revision,status:"pending",has_preview:false};
    $("trim-dialog").close();paintReviewQueue();reviewMessage("Trim saved. Rendering the new revision; your place in the queue is kept.");
  }catch(e){$("trim-message").textContent=e.message;}
  finally{trimBusy=false;trimControls();}
  if(reviewRendering)await pollReviewRender();
}
$("review-trim").onclick=openClipTrim;
$("trim-form").onsubmit=saveClipTrim;
for(const edge of ["start","end"]){
  const handle=$("trim-"+edge+"-handle");
  handle.oninput=()=>setTrimEdge(edge,Number(handle.value),false);
  handle.onchange=()=>setTrimEdge(edge,Number(handle.value));
  handle.onkeydown=event=>{
    if(!clipTrim||trimBusy)return;
    const direction={ArrowLeft:-1,ArrowDown:-1,ArrowRight:1,ArrowUp:1}[event.key];if(!direction)return;
    event.preventDefault();
    const value=trimSnap(clipTrim[edge]+direction*(event.shiftKey?1e6:1e5),direction<0?"start":"end",clipTrim.words);
    setTrimEdge(edge,value);
  };
  $("trim-"+edge).onchange=()=>setTrimEdge(edge,Number($("trim-"+edge).value)*1e6);
  $("trim-set-"+edge).onclick=()=>{if(clipTrim&&trimMediaReady)setTrimEdge(edge,clipTrim.origin_us+$("trim-player").currentTime*1e6);};
}
$("trim-preview").onclick=previewTrim;
$("trim-reset").onclick=()=>{if(clipTrim&&!trimBusy){clipTrim.start=clipTrim.clip.start_us;clipTrim.end=clipTrim.clip.end_us;trimPlaying=false;$("trim-player").pause();$("trim-message").textContent="";paintTrim();}};
$("trim-track").onclick=event=>{
  if(!clipTrim||!trimMediaReady||trimBusy||event.target.tagName==="INPUT")return;
  const rect=$("trim-track").getBoundingClientRect();
  const time=clipTrim.min_us+Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width))*(clipTrim.max_us-clipTrim.min_us);
  trimPlaying=false;$("trim-player").currentTime=(time-clipTrim.origin_us)/1e6;
};
$("trim-player").ontimeupdate=()=>{
  if(!clipTrim)return;
  const time=clipTrim.origin_us+$("trim-player").currentTime*1e6;
  trimPosition($("trim-playhead"),Math.max(clipTrim.min_us,Math.min(clipTrim.max_us,time)));
  if(trimPlaying&&time>=clipTrim.end){trimPlaying=false;$("trim-player").pause();}
};
$("trim-player").onerror=()=>{if(clipTrim){trimMediaReady=false;trimControls();$("trim-message").textContent="The original source preview could not load. Close and reopen Trim clip to retry.";}};
$("trim-close").onclick=()=>{if(!trimBusy)$("trim-dialog").close();};
$("trim-dialog").oncancel=event=>{if(trimBusy)event.preventDefault();};
$("trim-dialog").onclose=()=>{
  clipTrim=null;trimPlaying=false;trimMediaReady=false;
  $("trim-player").pause();$("trim-player").removeAttribute("src");$("trim-player").load();
  reviewBusy=false;reviewControls();
};
