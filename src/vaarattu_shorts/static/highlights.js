"use strict";
let highlightRuns=[],highlightSelected=null,highlightManifest=null,highlightTimer=null,highlightBusy=false,highlightProviders=false,highlightStartKey=null;
function highlightDuration(seconds){return `${Math.floor(seconds/60)}m ${Math.round(seconds%60)}s`;}
function highlightMessage(message){$("highlight-message").textContent=message;}
function highlightLock(value){
  highlightBusy=value;
  for(const id of ["highlight-resolve","highlight-start","highlight-revise","highlight-approve","highlight-reject"])$(id).disabled=value;
  $("highlight-start").disabled=value||!highlightManifest;
}
async function resolveHighlight(){
  if(highlightBusy)return;
  const original=$("highlight-urls").value;
  const urls=original.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
  if(!urls.length){error("Enter a YouTube video or Twitch VOD URL.");return;}
  highlightManifest=null;highlightLock(true);highlightMessage("Checking the recording and matching its parts…");
  try{
    const manifest=await api("/api/highlights/resolve",{method:"POST",body:JSON.stringify({urls})});
    if($("highlight-urls").value!==original){highlightMessage("The source changed. Check the recording parts again.");return;}
    highlightManifest=manifest;highlightStartKey=null;
    const box=$("highlight-parts");box.replaceChildren(text("h3",manifest.title));
    for(const source of manifest.sources)box.append(text("p",`${source.title} · ${highlightDuration(source.duration)}`));
    for(const note of manifest.notes)box.append(text("p",note,"muted"));
    highlightMessage(`${manifest.sources.length} source ${manifest.sources.length===1?"video":"videos"} · ${highlightDuration(manifest.duration)}. Ready to start.`);
  }catch(e){error(e.message);highlightMessage("Recording could not be matched. Check the URLs and try again.");}
  finally{highlightLock(false);}
}
function openHighlights(url){
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
    paintHighlights();
  }catch(e){error(e.message);}
  finally{if(currentView==="highlights"&&highlightRuns.some(r=>["queued","running"].includes(r.state)))highlightTimer=setTimeout(loadHighlights,3000);}
}
function paintHighlights(){
  const box=$("highlight-runs");box.replaceChildren();
  if(!highlightRuns.length)box.append(text("p","Your highlight videos will appear here.","muted"));
  for(const run of highlightRuns){
    const button=action(`${run.title} · ${run.state}`,()=>{highlightSelected=run.id;paintHighlights();});
    button.className="run-row"+(run.id===highlightSelected?" selected":"");button.setAttribute("aria-pressed",String(run.id===highlightSelected));box.append(button);
  }
  const run=highlightRuns.find(r=>r.id===highlightSelected);
  $("highlight-review").hidden=!run;if(!run)return;
  $("highlight-title").textContent=run.title;
  $("highlight-progress").value=run.progress;
  const stage=run.stage.startsWith("audio-")?"Preparing recording audio":run.stage.startsWith("transcript-")?"Transcribing speech":run.stage.startsWith("edit-")?"Selecting and editing highlights":run.stage.startsWith("media-")?"Downloading and checking selected footage":run.stage.startsWith("render-")?(run.stage.endsWith("final")?"Rendering final 1080p video":"Rendering 720p review draft"):"Completed stages are saved automatically.";
  $("highlight-status").textContent=`${run.state} · ${Math.round(run.progress*100)}% · ${run.message||stage}`;
  $("highlight-meta").textContent=`Revision ${run.revision}${run.duration?` · ${highlightDuration(run.duration)}`:""} · ${run.usage.request_count} model requests · ${run.review||"Not reviewed"}`;
  const controls=$("highlight-controls");controls.replaceChildren();
  for(const [label,op,states] of [["Pause","pause",["running","queued"]],["Resume / retry","resume",["paused","failed","cancelled"]],["Cancel","cancel",["running","queued","paused"]]])if(states.includes(run.state))controls.append(action(label,()=>highlightAction(op)));
  const player=$("highlight-player");player.hidden=!run.has_draft;
  const url=run.has_draft?`/api/highlights/${run.id}/video?revision=${run.revision}&quality=${run.has_final?"final":"draft"}`:"";
  if(player.getAttribute("src")!==url){player.pause();if(url)player.src=url;else player.removeAttribute("src");player.load();}
  $("highlight-review-actions").hidden=run.state!=="completed"||!run.has_draft;
  $("highlight-approve").hidden=run.has_final;
  const notes=$("highlight-notes");notes.replaceChildren();
  if(run.shorter_than_target&&run.has_draft)notes.append(text("p","This draft is shorter than five minutes. It was not padded with weaker material."));
  for(const issue of run.issues)notes.append(text("p",issue.instruction));
  if(run.has_draft)notes.append(text("p","Editing used the transcript only. Check gameplay pauses and how the cuts sound before approving.","muted"));
  const downloads=$("highlight-downloads");downloads.replaceChildren();
  if(run.has_draft){
    for(const [label,href] of [[run.has_final?"Download final 1080p video":"Download 720p draft",url],["Download edit plan",`/api/highlights/${run.id}/plan?revision=${run.revision}`]]){
      const link=text("a",label);link.href=href;link.download=label.startsWith("Download edit")?"highlight-edit.json":`highlight-${run.id}-${run.revision}.mp4`;downloads.append(link);
    }
  }
  const history=$("highlight-history");history.replaceChildren();
  for(const old of run.history.filter(r=>r.revision!==run.revision&&r.has_draft)){
    const row=text("div","","actions"),link=text("a",`Watch revision ${old.revision}`);link.href=`/api/highlights/${run.id}/video?revision=${old.revision}&quality=draft`;link.target="_blank";row.append(link);
    if(run.state==="completed")row.append(action("Restore this draft",()=>highlightAction("restore",old.revision)));
    history.append(row);
  }
}
async function highlightAction(op,restore=null){
  if(highlightBusy)return;
  const run=highlightRuns.find(r=>r.id===highlightSelected);if(!run)return;
  const guidance=$("highlight-guidance").value.trim();
  if(op==="revise"&&!guidance){error("Describe the changes you want first.");return;}
  highlightLock(true);
  try{
    await api(`/api/highlights/${run.id}/${op}`,{method:"POST",body:JSON.stringify({revision:run.revision,guidance:op==="revise"?guidance:"",restore})});
    highlightMessage(op==="approve"?"Approved. The final 1080p render will use this exact edit.":op==="revise"?"Revision requested. Your previous draft is saved.":"Highlight video updated.");
    await loadHighlights();
  }catch(e){error(e.message);}finally{highlightLock(false);}
}
$("highlight-urls").oninput=()=>{highlightManifest=null;$("highlight-start").disabled=true;$("highlight-parts").replaceChildren();};
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
    const body={manifest_id:highlightManifest.id,target_minutes:Number($("highlight-target").value),provider,local_model:$("highlight-local").value||"gemma4-31b",context_size:32768,budget_usd:["codex","local"].includes(provider)?0:Number($("highlight-budget").value),video_encoder:$("highlight-encoder").value};
    const signature=JSON.stringify(body);
    if(highlightStartKey?.signature!==signature)highlightStartKey={signature,key:crypto.randomUUID()};
    const run=await api("/api/highlights",{method:"POST",headers:{"Idempotency-Key":highlightStartKey.key},body:signature});
    highlightStartKey=null;highlightSelected=run.id;highlightMessage("Highlight processing started. You can keep reviewing clips while it runs.");await loadHighlights();
  }catch(e){error(e.message);}finally{highlightLock(false);}
};
$("highlight-approve").onclick=()=>highlightAction("approve");
$("highlight-revise").onclick=()=>highlightAction("revise");
$("highlight-reject").onclick=()=>highlightAction("reject");
