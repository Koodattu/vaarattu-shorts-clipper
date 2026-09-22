"use strict";
let highlightRuns=[],highlightSelected=null,highlightManifest=null,highlightTimer=null,highlightBusy=false,highlightProviders=false,highlightStartKey=null;
let highlightPanel="review",highlightUIKey=null,highlightRecordingsKey=null,highlightCreateInitialized=false;
const highlightPanels=["review","publish","details"];
function showHighlightPanel(panel,focus=false){
  if(!highlightPanels.includes(panel))return;
  if(panel==="publish"&&$("highlight-tab-publish").disabled)return;
  highlightPanel=panel;
  for(const name of highlightPanels){
    const selected=name===panel,button=$("highlight-tab-"+name);
    button.setAttribute("aria-selected",String(selected));button.tabIndex=selected?0:-1;
    $("highlight-panel-"+name).hidden=!selected;
  }
  if(focus)$("highlight-tab-"+panel).focus();
}
for(const name of highlightPanels){
  const button=$("highlight-tab-"+name);
  button.onclick=()=>showHighlightPanel(name);
  button.onkeydown=event=>{
    const available=highlightPanels.filter(p=>!$("highlight-tab-"+p).disabled),index=available.indexOf(name);
    const next=event.key==="ArrowRight"?available[(index+1)%available.length]:event.key==="ArrowLeft"?available[(index+available.length-1)%available.length]:event.key==="Home"?available[0]:event.key==="End"?available.at(-1):null;
    if(next){event.preventDefault();showHighlightPanel(next,true);}
  };
}
$("highlight-notice").onclick=()=>showHighlightPanel("details",true);
$("highlight-runs").onchange=()=>{highlightSelected=$("highlight-runs").value;paintHighlights();};
function highlightDuration(seconds){const total=Math.round(seconds);return `${Math.floor(total/60)}m ${total%60}s`;}
function highlightState(run){return run.state==="completed"?(run.has_final?"Final ready":run.has_draft?"Draft ready":"No scenes selected"):({running:"Processing",queued:"Queued",paused:"Paused",failed:"Needs attention",cancelled:"Cancelled"}[run.state]||run.state);}
function highlightMessage(message){$("highlight-message").textContent=message;}
function highlightLock(value){
  highlightBusy=value;
  for(const id of ["highlight-resolve","highlight-start","highlight-revise","highlight-rebuild","highlight-approve","highlight-reject"])$(id).disabled=value;
  $("highlight-start").disabled=value||!highlightManifest;
  if(highlightManifest)updateHighlightSourceSummary();
}
async function resolveHighlight(){
  if(highlightBusy)return;
  const original=$("highlight-urls").value,matchParts=$("highlight-match-parts").checked;
  const urls=original.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  if(!urls.length){error("Enter a YouTube video or Twitch VOD URL.");return;}
  highlightManifest=null;highlightLock(true);highlightMessage("Loading recording previews and durations…");
  try{
    const manifest=await api("/api/highlights/resolve",{method:"POST",body:JSON.stringify({urls,collection:true,match_parts:matchParts})});
    if($("highlight-urls").value!==original||$("highlight-match-parts").checked!==matchParts){highlightMessage("The source changed. Check the recording parts again.");return;}
    highlightManifest=manifest;highlightStartKey=null;
    setHighlightSources(manifest);
    highlightMessage("Recordings loaded. Choose the ranges and name this project before starting.");
  }catch(e){error(e.message);highlightMessage("Recordings could not be loaded. Check the URLs and try again.");}
  finally{highlightLock(false);}
}
function openHighlights(url){
  $("highlight-create").open=true;highlightCreateInitialized=true;
  highlightSourceRanges=[];$("highlight-source-setup").hidden=true;
  $("highlight-urls").value=url;highlightManifest=null;$("highlight-parts").replaceChildren();
  goView("highlights");return resolveHighlight();
}
async function loadHighlights(){
  clearTimeout(highlightTimer);highlightTimer=null;
  try{
    if(!highlightProviders){
      const status=await api("/api/status");const select=$("highlight-provider");select.replaceChildren();
      for(const [id,p] of Object.entries(status.providers))if(p.available&&p.configured){const option=document.createElement("option");option.value=id;option.textContent=`${id} · ${p.model}`;select.append(option);}
      if(status.providers.codex?.configured)select.value="codex";
      highlightProviders=true;select.onchange();
    }
    highlightRuns=await api("/api/highlights");
    if(!highlightSelected&&highlightRuns.length)highlightSelected=highlightRuns[0].id;
    await paintHighlights();
  }catch(e){error(e.message);}
  finally{if(currentView==="highlights"&&highlightRuns.some(r=>["queued","running"].includes(r.state)))highlightTimer=setTimeout(loadHighlights,3000);}
}
function paintHighlights(){
  const box=$("highlight-runs");
  const optionsKey=JSON.stringify(highlightRuns.map(r=>[r.id,r.title,r.state,r.revision,r.has_final]));
  if(optionsKey!==highlightRecordingsKey){
    box.replaceChildren();
    for(const run of highlightRuns){const option=text("option",`${run.title} · ${highlightState(run)}`);option.value=run.id;box.append(option);}
    highlightRecordingsKey=optionsKey;
  }
  box.value=highlightSelected||"";
  $("highlight-recordings").hidden=!highlightRuns.length;$("highlight-empty").hidden=Boolean(highlightRuns.length);
  $("highlight-recording-count").textContent=`${highlightRuns.length} ${highlightRuns.length===1?"recording":"recordings"}`;
  if(!highlightCreateInitialized){$("highlight-create").open=!highlightRuns.length||Boolean($("highlight-urls").value.trim());highlightCreateInitialized=true;}
  const run=highlightRuns.find(r=>r.id===highlightSelected);
  $("highlight-review").hidden=!run;const timeline=Promise.all([loadHighlightTimeline(run),loadHighlightSelection(run)]);paintHighlightCopy(run);paintHighlightThumbnails(run);if(!run)return timeline;
  const uiKey=`${run.id}/${run.revision}`;
  $("highlight-tab-publish").disabled=!run.has_draft;
  if(highlightUIKey!==uiKey){highlightUIKey=uiKey;showHighlightPanel(run.has_draft?"review":"details");$("highlight-guidance").value="";$("highlight-edit-actions").open=false;}
  if(!run.has_draft&&highlightPanel==="publish")showHighlightPanel("details");
  $("highlight-title").textContent=run.title;
  $("highlight-state").textContent=highlightState(run);
  $("highlight-state").setAttribute("data-state",run.state);
  $("highlight-progress-panel").hidden=run.state==="completed";
  $("highlight-progress").value=run.progress;
  const stage=run.stage.startsWith("audio-")?"Preparing recording audio":run.stage.startsWith("transcript-")?"Transcribing speech":run.stage.startsWith("edit-")?"Selecting and editing highlights":run.stage.startsWith("media-")?"Downloading and checking selected footage":run.stage.startsWith("render-")?(run.stage.endsWith("final")?"Rendering final 1080p video":"Rendering 720p review draft"):"Completed stages are saved automatically.";
  $("highlight-status").textContent=`${run.state} · ${Math.round(run.progress*100)}% of this stage · ${run.message||stage}`;
  const reviewLabel=({approved:"Approved",rejected:"Rejected",unreviewed:"Not reviewed"})[run.review]||"Not reviewed";
  $("highlight-meta").textContent=`Revision ${run.revision}${run.duration?` · ${highlightDuration(run.duration)}`:""} · ${run.has_final?"1080p final":run.has_draft?"720p draft":"Preview pending"} · ${reviewLabel}`;
  const rebuildLabel=(run.sources||[]).some(s=>s.selection_start_us>0||s.selection_end_us!==undefined&&s.selection_end_us<Math.round(s.duration*1e6))?"Rebuild from selected ranges":"Rebuild from full recording";
  $("highlight-rebuild").textContent=rebuildLabel;
  const controls=$("highlight-controls");controls.replaceChildren();
  for(const [label,op,states] of [["Pause","pause",["running","queued"]],["Resume / retry","resume",["paused","failed","cancelled"]],["Cancel","cancel",["running","queued","paused"]]])if(states.includes(run.state))controls.append(action(label,()=>highlightAction(op)));
  if(["paused","failed","cancelled"].includes(run.state))controls.append(action(rebuildLabel,()=>highlightAction("rebuild")));
  const player=$("highlight-player");player.hidden=!run.has_draft;$("highlight-player-empty").hidden=run.has_draft;
  const url=run.has_draft?`/api/highlights/${run.id}/video?revision=${run.revision}&quality=${run.has_final?"final":"draft"}`:"";
  if(player.getAttribute("src")!==url){player.pause();if(url)player.src=url;else player.removeAttribute("src");player.load();}
  $("highlight-review-actions").hidden=run.state!=="completed"||!run.has_draft;
  $("highlight-edit-actions").hidden=run.state!=="completed"||!run.has_draft;
  $("highlight-approve").hidden=run.has_final;
  const noteCount=(run.warnings||[]).length+(run.issues||[]).length;
  $("highlight-notice").hidden=!noteCount;$("highlight-notice").textContent=`${noteCount} editing ${noteCount===1?"note":"notes"} to review · View details`;
  const notes=$("highlight-notes");notes.replaceChildren();
  notes.append(text("p",`${run.activity?.request_count??run.usage.request_count} model requests in this revision`,"muted"));
  if(run.activity?.thinking)notes.append(text("p",`Editing thinking: ${run.activity.thinking}.`,"muted"));
  for(const [label,phase] of Object.entries(run.activity?.phases||{}))notes.append(text("p",`${label}: ${phase.requests} requests, ${phase.retries} retries / repairs, ${highlightDuration(phase.seconds)} total model response time.`,"muted"));
  if(run.stage.startsWith("edit-")&&run.state==="running")notes.append(text("p","Editing is in progress. Selected footage download and rendering come next.","muted"));
  if(run.selection_preview)notes.append(text("p",`Final selection: ${run.selection_preview.scenes} scenes, ${highlightDuration(run.selection_preview.duration)}, minimum score ${run.selection_preview.score_floor}.`,"muted"));
  if(run.metrics?.eligible_scenes!==undefined)notes.append(text("p",`${run.metrics.mapped_scenes} scenes found; ${run.metrics.eligible_scenes} passed the initial quality check. Length follows the selected content.`,"muted"));
  if(run.metrics?.edited_scenes)notes.append(text("p",`${run.metrics.edited_scenes} scenes edited before selection; ${run.metrics.selected_scenes} scenes and ${run.metrics.retained_ranges} retained ranges in this draft.`,"muted"));
  if(run.editorial)notes.append(text("p",`Editorial checks: ${run.editorial.verified} scenes verified, ${run.editorial.unresolved} with unresolved notes, ${run.editorial.not_reviewed} not yet checked. ${run.editorial.corrections} corrections applied.`,"muted"));
  for(const warning of run.warnings||[])notes.append(text("p",warning));
  for(const issue of run.issues)notes.append(text("p",issue.instruction));
  if(run.has_draft)notes.append(text("p","Editing used the transcript only. Check gameplay pauses and how the cuts sound before approving.","muted"));
  const downloads=$("highlight-downloads");downloads.replaceChildren();
  if(run.has_draft){
    for(const [label,href] of [[run.has_final?"Download final 1080p video":"Download 720p draft",url],["Download edit plan",`/api/highlights/${run.id}/plan?revision=${run.revision}`]]){
      const link=text("a",label);link.href=href;link.download=label.startsWith("Download edit")?"highlight-edit.json":`highlight-${run.id}-${run.revision}.mp4`;downloads.append(link);
    }
  }
  const history=$("highlight-history");history.replaceChildren();
  if(!run.history.some(r=>r.revision!==run.revision&&r.has_draft))history.append(text("p","No earlier drafts yet.","muted"));
  for(const old of run.history.filter(r=>r.revision!==run.revision&&r.has_draft)){
    const row=text("div","","actions"),link=text("a",`Watch revision ${old.revision}`);link.href=`/api/highlights/${run.id}/video?revision=${old.revision}&quality=draft`;link.target="_blank";row.append(link);
    if(run.state==="completed")row.append(action("Restore this draft",()=>highlightAction("restore",old.revision)));
    history.append(row);
  }
  return timeline;
}
async function highlightAction(op,restore=null){
  if(highlightBusy)return;
  const run=highlightRuns.find(r=>r.id===highlightSelected);if(!run)return;
  const guidance=$("highlight-guidance").value.trim();
  if(op==="revise"&&!guidance){error("Describe the changes you want first.");return;}
  highlightLock(true);
  try{
    await api(`/api/highlights/${run.id}/${op}`,{method:"POST",body:JSON.stringify({revision:run.revision,guidance:["revise","rebuild"].includes(op)?guidance:"",restore})});
    highlightMessage(op==="approve"?"Approved. The final 1080p render will use this exact edit.":["revise","rebuild"].includes(op)?"New draft requested. Your previous draft is saved.":"Highlight video updated.");
    await loadHighlights();
  }catch(e){error(e.message);}finally{highlightLock(false);}
}
$("highlight-urls").oninput=()=>{highlightManifest=null;$("highlight-start").disabled=true;$("highlight-parts").replaceChildren();$("highlight-source-setup").hidden=true;};
$("highlight-match-parts").onchange=$("highlight-urls").oninput;
$("highlight-resolve").onclick=resolveHighlight;
$("refresh-highlights").onclick=loadHighlights;
$("highlight-provider").onchange=()=>{
  const p=$("highlight-provider").value;
  $("highlight-budget-field").hidden=p==="codex"||p==="local";
  $("highlight-local-field").hidden=p!=="local";
  $("highlight-provider-note").textContent=p==="codex"?"Uses the configured Codex bridge. Subscription allowance applies.":p==="local"?"Local AI stages share the GPU with transcription.":"Model calls use the API spending limit below.";
};
$("highlight-form").onsubmit=async event=>{
  event.preventDefault();if(highlightBusy||!highlightManifest)return;
  highlightLock(true);
  try{
    const provider=$("highlight-provider").value;
    const body={manifest_id:highlightManifest.id,project_title:$("highlight-project-title").value.trim(),segments:collectHighlightSources(),final_score_floor:Number($("highlight-score-floor").value||75),provider,local_model:$("highlight-local").value||"gemma4-31b",context_size:32768,budget_usd:["codex","local"].includes(provider)?0:Number($("highlight-budget").value),video_encoder:$("highlight-encoder").value};
    const signature=JSON.stringify(body);
    if(highlightStartKey?.signature!==signature)highlightStartKey={signature,key:crypto.randomUUID()};
    const run=await api("/api/highlights",{method:"POST",headers:{"Idempotency-Key":highlightStartKey.key},body:signature});
    $("highlight-create").open=false;highlightStartKey=null;highlightSelected=run.id;highlightMessage("Highlight processing started. You can keep reviewing clips while it runs.");await loadHighlights();
  }catch(e){error(e.message);}finally{highlightLock(false);}
};
$("highlight-approve").onclick=()=>highlightAction("approve");
$("highlight-revise").onclick=()=>highlightAction("revise");
$("highlight-reject").onclick=()=>highlightAction("reject");

$("highlight-rebuild").onclick=()=>highlightAction("rebuild");
