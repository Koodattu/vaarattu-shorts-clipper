"use strict";
let tightenClip=null,tightenBusy=false,tightenTimer=null,tightenFromEditor=false,tightenSession=0,tightenMode="suggest";
function activeTightenCheck(){
  const check=tightenClip?.tighten_check;
  return (check?.mode||"suggest")===tightenMode?check:null;
}
function tightenTime(source){
  const spans=tightenClip.pacing?.retained;
  if(!spans)return (source-tightenClip.start_us)/1e6;
  for(const span of spans)if(source<span.source_end_us)return (span.output_start_us+Math.max(0,source-span.source_start_us))/1e6;
  const last=spans.at(-1);return (last.output_start_us+last.source_end_us-last.source_start_us)/1e6;
}
function tightenClock(seconds){return `${Math.floor(seconds/60)}:${(seconds%60).toFixed(1).padStart(4,"0")}`;}
function selectedTightenCuts(){return [...$("tighten-cuts").querySelectorAll("input:checked")].map(input=>Number(input.value));}
function updateTightenDuration(){
  const indices=selectedTightenCuts(),cuts=activeTightenCheck()?.cuts||[];
  const removed=indices.reduce((n,i)=>n+tightenTime(cuts[i].end_us)-tightenTime(cuts[i].start_us),0);
  $("tighten-duration").textContent=indices.length?`About ${removed.toFixed(1)} seconds removed; ${(tightenTime(tightenClip.end_us)-removed).toFixed(1)} seconds remain. Final pacing may vary slightly.`:"";
  $("tighten-apply").disabled=tightenBusy||!indices.length;
}
function paintTighten(){
  const check=activeTightenCheck(),pending=tightenClip.tighten_check?.status==="pending",instructed=tightenMode==="instructed";
  $("tighten-heading").textContent=instructed?"Edit with instructions":"Suggest tighter edit";
  $("tighten-description").textContent=instructed?"Describe which discussion to remove or keep. Review the proposed cuts before rendering. This action removes passages inside the clip; it cannot add speech or change the opening and ending.":"Remove spoken detours while keeping the point and original order. Listen to the joins in the new revision before approving it.";
  $("tighten-note-label").textContent=instructed?"Editing instructions":"Editing guidance (optional)";
  $("tighten-note").required=instructed;
  $("tighten-note").placeholder=instructed?"What should be removed, and what should stay? You can refer to times in this preview.":"What should this edit focus on?";
  $("tighten-request").textContent=instructed?"Plan my edit":"Find removable passages";
  $("tighten-cuts").replaceChildren();
  for(const [i,cut] of (check?.status==="complete"?check.cuts:[]).entries()){
    const row=text("div","","form-section"),label=text("label","","check"),input=document.createElement("input");
    input.type="checkbox";input.value=String(i);input.checked=true;input.onchange=updateTightenDuration;
    label.append(input,text("strong",`Remove ${tightenClock(tightenTime(cut.start_us))}–${tightenClock(tightenTime(cut.end_us))}`));
    row.append(label,text("p",cut.text),text("p",cut.reason,"muted"),text("p",`Join: “…${cut.before}” → “${cut.after}…”`));
    row.append(action("Play this passage",()=>{$("tighten-player").currentTime=Math.max(0,tightenTime(cut.start_us)-2);return $("tighten-player").play();}));
    $("tighten-cuts").append(row);
  }
  for(const cut of tightenClip.speech_cuts||[])$("tighten-cuts").append(text("p",`Already removed: ${cut.text}`));
  $("tighten-request").disabled=tightenBusy||pending;$("tighten-note").disabled=tightenBusy||pending;
  $("tighten-undo").disabled=tightenBusy||pending||!tightenClip.speech_cut_history?.length;
  $("tighten-message").textContent=pending?(!check?"Another editing check is running for this clip.":instructed?"Planning cuts from your instructions. The current preview is unchanged.":"Looking for removable passages. The current preview is unchanged."):check?.status==="complete"?check.summary:instructed?"Your instructions determine what to cut. Nothing changes until you apply the proposed cuts.":"Describe your focus, or let the model look for dispensable detours. Suggestions are optional.";
  updateTightenDuration();
}
async function openTighten(id,fromEditor=false,mode="suggest"){
  if(!id||tightenBusy)return;
  if(fromEditor&&editing.captionFormState!==captionEditorState()){error("Save your current editor changes before checking the saved clip.");return;}
  tightenBusy=true;tightenFromEditor=fromEditor;tightenMode=mode;tightenSession++;
  try{
    tightenClip=await api(`/api/clips/${id}`);$("tighten-note").value=activeTightenCheck()?.note||"";
    $("review-player").pause();$("source-player").pause();
    $("tighten-player").src=`/api/artifacts/${id}/video?revision=${tightenClip.revision}`;
    $("tighten-player").load();
    $("tighten-dialog").showModal();
  }catch(e){error(e.message);return;}finally{tightenBusy=false;}
  paintTighten();if(tightenClip.tighten_check?.status==="pending")await pollTighten();
}
async function requestTighten(){
  if(tightenBusy||!tightenClip)return;
  if(tightenMode==="instructed"&&!$("tighten-note").value.trim()){$("tighten-message").textContent="Describe how you want this clip edited.";return;}
  tightenBusy=true;paintTighten();
  try{
    const result=await api(`/api/clips/${tightenClip.id}/${tightenMode==="instructed"?"instructed-edit":"tighten"}`,{method:"POST",body:JSON.stringify({expected_revision:tightenClip.revision,note:$("tighten-note").value})});
    tightenClip.tighten_check={id:result.request_id,status:"pending",mode:tightenMode};
  }catch(e){tightenBusy=false;paintTighten();$("tighten-message").textContent=e.message;return;}
  tightenBusy=false;paintTighten();await pollTighten();
}
async function pollTighten(){
  clearTimeout(tightenTimer);if(!$("tighten-dialog").open||!tightenClip)return;
  const session=tightenSession,id=tightenClip.id;
  try{
    const clip=await api(`/api/clips/${id}`);if(session!==tightenSession||!$("tighten-dialog").open)return;
    tightenClip=clip;paintTighten();if(clip.tighten_check?.status!=="pending")return;
    const run=await api(`/api/runs/${clip.run_id}`);if(session!==tightenSession||!$("tighten-dialog").open)return;
    if(!["queued","running"].includes(run.state)){$("tighten-message").textContent=`Editing check ${run.state}. Open Runs & clips to resume or retry. ${run.message||""}`;return;}
  }catch(e){if(session!==tightenSession)return;$("tighten-message").textContent=`Could not refresh the edit. Retrying. ${e.message}`;}
  tightenTimer=setTimeout(pollTighten,2000);
}
async function saveTighten(undo=false){
  if(tightenBusy||!tightenClip)return;
  const clip=tightenClip,indices=selectedTightenCuts();
  if(!undo&&!indices.length)return;
  tightenBusy=true;$("tighten-apply").disabled=true;$("tighten-undo").disabled=true;$("tighten-request").disabled=true;
  try{
    const result=await api(`/api/clips/${clip.id}/${undo?"speech-cuts-undo":"speech-cuts"}`,{method:"POST",body:JSON.stringify({expected_revision:clip.revision,...(undo?{}:{check_id:clip.tighten_check.id,indices})})});
    $("tighten-dialog").close();
    if(!tightenFromEditor&&reviewQueue[0]?.id===clip.id){reviewRendering={id:clip.id,...result};reviewQueue[0]={...clip,revision:result.revision,status:"pending",has_preview:false};paintReviewQueue();await pollReviewRender();}
    else{activeRun=result.run_id;detailSignature="";await refresh();goView("results");}
  }catch(e){$("tighten-message").textContent=e.message;}
  finally{tightenBusy=false;$("tighten-request").disabled=false;$("tighten-undo").disabled=!clip.speech_cut_history?.length;updateTightenDuration();}
}
$("review-tighten").onclick=()=>openTighten(reviewQueue[0]?.id);
$("review-instructed-edit").onclick=()=>openTighten(reviewQueue[0]?.id,false,"instructed");
$("edit-tighten").onclick=()=>openTighten(editing?.id,true);
$("tighten-request").onclick=requestTighten;
$("tighten-apply").onclick=()=>saveTighten();
$("tighten-undo").onclick=()=>saveTighten(true);
$("tighten-close").onclick=()=>{if(!tightenBusy)$("tighten-dialog").close();};
$("tighten-dialog").oncancel=event=>{if(tightenBusy)event.preventDefault();};
$("tighten-dialog").onclose=()=>{tightenSession++;clearTimeout(tightenTimer);$("tighten-player").pause();};
