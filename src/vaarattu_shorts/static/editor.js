"use strict";
let editorBusy=false,editorRendering=null,editorRenderPolling=false,editorRenderTimer=null,editorLoad=0;
let editorAnalysisClip=null;
function initEditorModels(state){
  const select=$("editor-provider");
  select.replaceChildren(Object.assign(text("option","Original run"),{value:""}));
  for(const [key,info] of Object.entries(state.providers)){
    const option=text("option",labels[key]+(!info.available?" · unavailable":!info.configured?" · key missing":""));
    option.value=key;option.disabled=!info.available||!info.configured;select.append(option);
  }
  for(const option of $("editor-local-model").options)option.disabled=!state.models[option.value];
}
function editorModelFields(){
  const selected=$("editor-provider").value,defaults=editing?.analysis_defaults;
  $("editor-analysis-budget-field").hidden=!selected||["local","codex"].includes(selected);
  $("editor-local-model-field").hidden=selected!=="local";
  $("editor-provider-note").textContent=!defaults?"Restart the app to enable model selection for editing actions.":
    !selected?`Uses ${labels[defaults.provider]} from the original run.`:
    selected==="codex"?"Uses Codex subscription usage for AI actions. Plan limits apply.":
    selected==="local"?"Runs AI actions locally using the selected model.":
    "Applies to AI actions here. Existing API usage for this run counts toward the limit.";
}
function editorAnalysisRequest(){
  const provider=$("editor-provider").value;
  if(!provider)return {};
  if(!editing?.analysis_defaults)throw new Error("Restart the app before choosing a model for editing actions.");
  const analysis={provider};
  if(provider==="local")analysis.local_model=$("editor-local-model").value;
  else if(provider!=="codex"){
    const limit=Number($("editor-analysis-budget").value);
    if(!Number.isFinite(limit)||limit<=0||limit>100)throw new Error("Set an API run spending limit above zero and at most $100.");
    analysis.budget_usd=limit;
  }
  return {analysis};
}
$("editor-provider").onchange=editorModelFields;
function editorMessage(message,isError=false){
  $("editor-message").textContent=message;$("editor-message").className=isError?"review-error":"muted";
}
function pauseEditorPlayers(){$("editor-player").pause();$("source-player").pause();}
function editorActionAllowed(){
  if(!editing||editorBusy||editorRendering||editing.context_request?.status==="pending")return false;
  if(editing.status!=="ready"||!editing.has_preview)return false;
  if(editing.captionFormState!==captionEditorState()){error("Save your current editor changes before using the saved clip tools.");return false;}
  return true;
}
function editorContextStopped(){
  return editing?.context_request?.status==="pending"&&["failed","cancelled","paused","completed"].includes(editing.context_job?.state);
}
function editorContextFailure(){
  if(!editorContextStopped())return "";
  return `Context search ${editing.context_job.state}. ${editing.context_job.message||"The search did not finish."} Retry context or keep the current clip.`;
}
function editorControls(){
  const locked=editorBusy||Boolean(editorRendering)||editing?.context_request?.status==="pending";
  const ready=editing?.status==="ready"&&editing.has_preview;
  for(const id of ["edit-trim","edit-context","edit-tighten","edit-instructed-edit","edit-caption-edit","edit-caption-check","edit-approve","edit-reject","edit-ready"])$(id).disabled=locked||!ready;
  $("edit-trim").disabled||=!editing?.has_source;
  $("editor-fields").disabled=locked||!editing||!["ready","held"].includes(editing.status);
  $("refresh-editor").disabled=editorBusy;$("edit-notes").disabled=locked||!editing;
  $("use-source-frame").disabled=locked||!editing?.has_source;
  const stopped=editorContextStopped(),contextPending=editing?.context_request?.status==="pending";
  for(const id of ["editor-provider","editor-analysis-budget","editor-local-model"])$(id).disabled=editorBusy||(locked&&!stopped)||!editing?.analysis_defaults;
  $("editor-context-recovery").hidden=!contextPending;
  const supported=editing?.context_recovery_available,job=editing?.context_job;
  $("editor-context-stop").hidden=!supported||!["queued","running"].includes(job?.state);
  $("editor-context-stop").disabled=editorBusy||job?.intent==="cancel";
  for(const id of ["editor-context-retry","editor-context-discard"]){$(id).hidden=!supported||!stopped;$(id).disabled=editorBusy;}
  $("editor-context-recovery-note").textContent=!supported?"Restart the app to enable context recovery controls.":
    stopped?"Retry with the model selected below, or keep this clip and unlock editing.":
    job?.intent==="cancel"?"Waiting for the worker to stop safely. Your current clip is preserved.":
    "Context searches time out after 15 minutes of processing. You can stop sooner; your current clip is preserved.";
}
function paintEditorReview(){
  if(!editing)return;
  $("editor-meta").textContent=`${reviewLabels[editing.review_status||"unreviewed"]} · Revision ${editing.revision} · ${((editing.output_duration_us??(editing.end_us-editing.start_us))/1e6).toFixed(1)}s`;
  $("edit-notes").textContent=clipNotes(editing).length?`Review notes (${clipNotes(editing).length})`:"Review reason";
  $("editor-previous").hidden=!editing.previous_revision;
  if(editing.previous_revision)$("editor-previous").href=`/api/artifacts/${editing.id}/video?revision=${editing.previous_revision}`;
  for(const [id,status] of [["edit-approve","approved"],["edit-reject","not_approved"],["edit-ready","ready_to_post"]])$(id).setAttribute("aria-pressed",String(editing.review_status===status));
  editorControls();
}
function fillEditor(clip){
  editing=clip;pauseEditorPlayers();
  if(editorAnalysisClip!==clip.id){
    if(typeof providerStatus!=="undefined"&&providerStatus)initEditorModels(providerStatus);
    editorAnalysisClip=clip.id;$("editor-provider").value="";
    $("editor-analysis-budget").value=clip.analysis_defaults?.budget_usd||5;
    $("editor-local-model").value=clip.analysis_defaults?.local_model||"gemma4-31b";
  }
  const inherited=$("editor-provider").children[0];
  if(inherited)inherited.textContent=clip.analysis_defaults?`Original run · ${labels[clip.analysis_defaults.provider]}`:"Original run";
  editorModelFields();
  $("editor-title").textContent=clip.title;
  $("edit-duration-help").textContent=clip.context_expanded?"This context-expanded clip has no fixed duration cap. Keep at least 3 seconds within the recording.":"Choose a 3–90 second interval. The original recording keeps its full pacing.";
  $("edit-layout").value=savedLayouts.find(l=>sameLayout(l.body,clip.layout))?.id||"";
  $("edit-caption-coverage").hidden=!clip.caption_coverage;
  if(clip.caption_coverage)$("edit-caption-coverage").textContent=`Saved captions (${clip.caption_coverage.profile}): edits can use ${(clip.caption_coverage.start_us/1e6).toFixed(2)}–${(clip.caption_coverage.end_us/1e6).toFixed(2)} seconds of the original recording. Use Trim clip to include more saved footage.`;
  $("edit-start").value=clip.start_us/1e6;$("edit-end").value=clip.end_us/1e6;$("edit-title").value=clip.title;
  $("edit-trim-silence").checked=Boolean(clip.trim_silence);$("edit-encoder").value=clip.video_encoder||"libx264";$("reviewed").checked=false;
  $("edit-speech-cuts").hidden=!clip.speech_cuts?.length;
  $("edit-speech-cuts").textContent=`This revision omits ${clip.speech_cuts?.length||0} speech passages. Caption and layout edits retain them. Undo speech cuts before changing the excerpt boundaries.`;
  $("edit-words").value=(clip.words||[]).map(w=>`${w.id} | ${w.text}`).join("\n");
  const source=$("source-player"),preview=$("editor-player");
  source.hidden=!clip.has_source;$("source-unavailable").hidden=clip.has_source;source.removeAttribute("src");
  if(clip.has_source){
    source.src=`/api/artifacts/${clip.id}/source?revision=${clip.revision}`;
    source.onloadedmetadata=()=>{source.currentTime=Math.max(0,(clip.start_us-clip.section_origin_us)/1e6-3);};
  }
  preview.hidden=!clip.has_preview;$("editor-preview-unavailable").hidden=clip.has_preview;preview.removeAttribute("src");
  if(clip.has_preview){preview.src=`/api/artifacts/${clip.id}/video?revision=${clip.revision}`;preview.setAttribute("aria-label",clip.title);}
  source.load();preview.load();editing.captionFormState=captionEditorState();paintEditorReview();
}
function editorPending(clip){
  if(clip.context_request?.status==="pending"&&["failed","cancelled","paused","completed"].includes(clip.context_job?.state))return null;
  return clip.status==="pending"||clip.context_request?.status==="pending"?{id:clip.id,run_id:clip.run_id,revision:clip.revision,context:clip.context_request?.status==="pending",request_id:clip.context_request?.id}:null;
}
async function openEditor(id){
  if(editorBusy)return;
  const load=++editorLoad,clip=await api(`/api/clips/${id}`);
  if(load!==editorLoad)return;
  clearTimeout(editorRenderTimer);editorRendering=editorPending(clip);
  $("editor-back").href="#results";$("editor-back").textContent="← Back to clips";
  fillEditor(clip);editorMessage(editorContextFailure(),editorContextStopped());goView("editor");
}
async function followEditorRevision(clip,result,context=false){
  if(editing?.id!==clip.id)return;
  editorRendering={id:clip.id,...result,context};
  pauseEditorPlayers();editorMessage(context?"Looking for missing context…":"Rendering the new revision. The preview will update here when ready.");editorControls();
  await pollEditorRender();
}
async function pollEditorRender(){
  if(!editorRendering||editorRenderPolling||currentView!=="editor")return;
  clearTimeout(editorRenderTimer);editorRenderPolling=true;
  const pending=editorRendering,load=editorLoad;
  try{
    const run=await api(`/api/runs/${pending.run_id}`);
    if(editorRendering!==pending||editorLoad!==load)return;
    const clip=run.clips.find(c=>c.id===pending.id),request=clip?.context_request;
    if(pending.context&&editing?.id===pending.id){
      editing.context_request=request;editing.context_job={state:run.state,intent:run.intent,message:run.message};
    }
    const contextPending=pending.context&&request?.id===pending.request_id&&request.status==="pending";
    const unchanged=pending.context&&request?.id===pending.request_id&&request.status==="unchanged";
    const ready=!contextPending&&clip?.revision>=pending.revision&&clip.status==="ready"&&clip.has_preview;
    const stopped=["failed","cancelled","paused","completed"].includes(run.state)||clip?.status==="held";
    if(unchanged||ready||stopped){
      const full=await api(`/api/clips/${pending.id}`);
      if(editorRendering!==pending||editorLoad!==load)return;
      if(pending.context&&full.context_request?.id!==pending.request_id){
        editorRendering=editorPending(full);fillEditor(full);editorMessage(editorContextFailure()||"Context request changed. Refreshed the clip.",editorContextStopped());return;
      }
      if(contextPending)full.context_job={state:run.state,intent:run.intent,message:run.message};
      editorRendering=null;fillEditor(full);
      if(contextPending&&stopped){editorMessage(editorContextFailure(),true);return;}
      editorMessage(request?.status==="cancelled"?"Current clip kept. You can edit it or start another context search.":unchanged?`Original clip kept. ${request.reason||"No useful additional context found."}`:ready?"New revision ready. Review it before approving.":"This edit needs attention. Open the run to resume or retry.",!unchanged&&!ready);
    }else editorMessage(run.intent==="cancel"?"Stopping context search…":run.state==="queued"?"Waiting for a worker…":contextPending?`Looking for missing context… ${run.message||""}`:"Rendering the new revision…");
  }catch(e){if(editorRendering===pending)editorMessage("Could not check the edit status. Retrying automatically…",true);}
  finally{
    editorRenderPolling=false;editorControls();
    if(editorRendering&&currentView==="editor")editorRenderTimer=setTimeout(pollEditorRender,2000);
  }
}
async function recoverEditorContext(action){
  if(editorBusy||!editing?.context_recovery_available||editing.context_request?.status!=="pending")return;
  const clip=editing,load=editorLoad;editorBusy=true;editorControls();
  try{
    const body={expected_revision:clip.revision,request_id:clip.context_request.id,action,
      ...(action==="retry"?editorAnalysisRequest():{})};
    const fresh=await api(`/api/clips/${clip.id}/context-recovery`,{method:"POST",body:JSON.stringify(body)});
    if(load!==editorLoad)return;
    clearTimeout(editorRenderTimer);editorRendering=editorPending(fresh);fillEditor(fresh);
    editorMessage(action==="discard"?"Current clip kept. Editing is unlocked.":action==="stop"?(editorContextStopped()?"Context search stopped. Retry or keep the current clip.":"Stopping context search…"):"Context search queued again.");
  }catch(e){if(load===editorLoad)editorMessage(e.message,true);}
  finally{editorBusy=false;editorControls();}
  await pollEditorRender();
}
$("editor-context-stop").onclick=()=>recoverEditorContext("stop");
$("editor-context-retry").onclick=()=>recoverEditorContext("retry");
$("editor-context-discard").onclick=()=>recoverEditorContext("discard");
async function decideEditorReview(status){
  if(!editorActionAllowed())return;
  const clip=editing;editorBusy=true;editorControls();
  try{await saveClipReview(clip,status);if(editing===clip){paintEditorReview();editorMessage(`${reviewLabels[status]}. Decision saved.`);}}
  catch(e){editorMessage(e.message,true);}
  finally{editorBusy=false;editorControls();}
}
$("refresh-editor").onclick=async()=>{
  if(editorBusy||!editing)return;
  if(editing.captionFormState!==captionEditorState()){error("Save your current editor changes before refreshing this clip.");return;}
  const clip=editing,load=editorLoad;editorBusy=true;editorControls();
  try{const fresh=await api(`/api/clips/${clip.id}`);if(load!==editorLoad)return;editorRendering=editorPending(fresh);fillEditor(fresh);editorMessage(editorContextFailure()||"Clip refreshed.",editorContextStopped());}
  catch(e){editorMessage(e.message,true);}
  finally{editorBusy=false;editorControls();}
  await pollEditorRender();
};
$("edit-notes").onclick=()=>{if(editing){pauseEditorPlayers();showClipNotes(editing);}};
$("edit-approve").onclick=()=>decideEditorReview("approved");
$("edit-reject").onclick=()=>decideEditorReview("not_approved");
$("edit-ready").onclick=()=>decideEditorReview("ready_to_post");
$("editor-view-run").onclick=async()=>{if(editing){activeRun=editing.run_id;await detail();paintRuns();goView("results");}};
$("editor-player").onplay=()=>$("source-player").pause();
$("source-player").onplay=()=>$("editor-player").pause();
$("edit-form").onsubmit=async event=>{
  event.preventDefault();if(editorBusy||editorRendering||!editing||editing.context_request?.status==="pending")return;
  const clip=editing;editorBusy=true;editorControls();error("");$("save-edit").textContent="Saving revision…";
  try{
    const words=$("edit-words").value.split("\n").filter(Boolean).map(line=>{
      const split=line.indexOf("|"),id=line.slice(0,split).trim(),original=clip.words.find(w=>w.id===id);
      if(split<0||!original)throw new Error("Keep each original caption word ID before the |.");
      return {...original,text:line.slice(split+1).trim()};
    });
    const result=await api(`/api/clips/${clip.id}/edit`,{method:"POST",body:JSON.stringify({expected_revision:clip.revision,start_us:Math.round(Number($("edit-start").value)*1e6),end_us:Math.round(Number($("edit-end").value)*1e6),title:$("edit-title").value,words,layout_id:$("edit-layout").value,reviewed:$("reviewed").checked,trim_silence:$("edit-trim-silence").checked,video_encoder:$("edit-encoder").value})});
    await followEditorRevision(clip,result);
  }catch(e){error(e.message);}
  finally{editorBusy=false;editorControls();$("save-edit").textContent="Save and render revision";}
};
