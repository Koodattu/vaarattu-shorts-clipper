"use strict";
let reviewQueue=[], reviewBusy=false, reviewLoaded=false, reviewSkipped=0, reviewLast=null, reviewVideoKey="";
let reviewRendering=null, reviewRenderPolling=false, reviewRenderTimer=null;
function reviewMessage(message,isError=false){
  $("review-message").textContent=message;
  $("review-message").title=message;
  $("review-message").className=isError?"review-message review-error":"review-message";
}
function reviewControls(){
  const clip=reviewQueue[0],locked=reviewBusy||Boolean(reviewRendering);
  for(const id of ["review-approve","review-reject"])$(id).disabled=locked||!clip||clip.status!=="ready"||!clip.has_preview;
  $("review-rejection-reason").disabled=$("review-reject").disabled;
  $("review-skip").disabled=locked||!clip;
  $("review-undo").disabled=locked||!reviewLast;
  $("refresh-review").disabled=reviewBusy;
  $("review-notes").disabled=locked||!clip;
  $("review-layout").disabled=locked||!clip||!savedLayouts.length;$("review-encoder").disabled=locked||!clip;$("review-trim-silence").disabled=locked||!clip;
  $("review-rerender").disabled=locked||!clip||!["ready","held"].includes(clip.status);
}
async function loadReviewQueue(){
  if(reviewBusy)return;
  if(reviewRendering){await pollReviewRender();return;}
  reviewBusy=true;reviewControls();reviewMessage("Loading unreviewed renders…");
  try{
    const [clips]=await Promise.all([api("/api/clips"),layouts()]);
    reviewQueue=clips.filter(c=>c.status==="ready"&&c.has_preview&&(c.review_status||"unreviewed")==="unreviewed");
    const groups=new Map();
    for(const clip of reviewQueue){if(!groups.has(clip.run_id))groups.set(clip.run_id,[]);groups.get(clip.run_id).push(clip);}
    reviewQueue=[...groups.values()].flatMap(group=>group.sort((a,b)=>(a.review_rank??Infinity)-(b.review_rank??Infinity)));
    reviewLoaded=true;reviewSkipped=0;reviewLast=null;reviewVideoKey="";
    reviewMessage("");paintReviewQueue();
  }catch(e){reviewMessage(e.message,true);}
  finally{reviewBusy=false;reviewControls();}
}
function paintReviewQueue(){
  const clip=reviewQueue[0],video=$("review-player");
  const playable=clip?.status==="ready"&&clip.has_preview;
  $("review-progress").textContent=`${reviewQueue.length} left${reviewSkipped?` · ${reviewSkipped} skipped`:""}`;
  $("review-empty").hidden=Boolean(playable);
  video.hidden=!playable;
  $("review-render-run").hidden=!clip||Boolean(playable);
  $("review-title").textContent=clip?.title||"No clip selected";
  $("review-title").title=clip?.title||"";
  $("review-meta").textContent=clip?`${clip.review_rank?`Priority ${clip.review_rank} · `:""}${((clip.output_duration_us??(clip.end_us-clip.start_us))/1e6).toFixed(1)}s · Revision ${clip.revision}`:"";
  $("review-previous").hidden=!clip?.previous_revision;if(clip?.previous_revision)$("review-previous").href=`/api/artifacts/${clip.id}/video?revision=${clip.previous_revision}`;
  $("review-notes").textContent=clip&&clipNotes(clip).length?`Review notes (${clipNotes(clip).length})`:"Review reason";
  if(!clip||`${clip.id}:${clip.revision}`!==reviewVideoKey)$("review-rejection-reason").value=clip?.review_note||"";
  if(!playable){
    if(!clip)paintReviewLayouts();
    video.pause();video.removeAttribute("src");video.load();reviewVideoKey="";
    $("review-empty-title").textContent=clip?(reviewRendering?"Rendering new revision":"Render needs attention"):reviewLoaded?(reviewSkipped?"Remaining clips skipped":"You're all caught up"):"Your review queue";
    $("review-empty-copy").textContent=clip?(reviewRendering?"Stay here. The new preview will load automatically when it's ready.":"The new preview is not ready. Check the review notes or open the run to continue."):reviewSkipped?"Skipped clips stay unreviewed. Refresh the queue to revisit them.":"Finished, unreviewed renders appear here. Refresh the queue to check for more.";
  }else{
    const key=`${clip.id}:${clip.revision}`;
    if(key!==reviewVideoKey){
      paintReviewLayouts();
      video.pause();reviewVideoKey=key;
      video.src=`/api/artifacts/${clip.id}/video?revision=${clip.revision}`;
      video.setAttribute("aria-label",clip.title);video.load();
      if(currentView==="review")playReviewVideo();
    }
  }
  reviewControls();
}
function paintReviewLayouts(){
  const clip=reviewQueue[0];$("review-encoder").value=clip?.video_encoder||"libx264";$("review-trim-silence").checked=Boolean(clip?.trim_silence);
  const select=$("review-layout"),placeholder=text("option","Current layout");
  placeholder.value="";select.replaceChildren(placeholder);select.value="";
  for(const preset of savedLayouts){const option=text("option",preset.body.name);option.value=preset.id;select.append(option);}
}
async function rerenderReview(){
  const layoutId=$("review-layout").value;
  if(reviewBusy||reviewRendering||!reviewQueue.length||!["ready","held"].includes(reviewQueue[0].status))return;
  const clip=reviewQueue[0];reviewBusy=true;reviewControls();reviewMessage("Queuing new render…");
  try{
    const result=await api(`/api/clips/${clip.id}/rerender`,{method:"POST",body:JSON.stringify({expected_revision:clip.revision,layout_id:layoutId||null,trim_silence:$("review-trim-silence").checked,video_encoder:$("review-encoder").value||"libx264"})});
    reviewRendering={id:clip.id,run_id:result.run_id,revision:result.revision,layoutId};
    reviewQueue[0]={...clip,revision:result.revision,status:"pending",has_preview:false,flags:[]};
    paintReviewQueue();reviewMessage("Waiting to render the new revision…");
  }catch(e){reviewMessage(e.message+" Your place in the queue has been kept.",true);}
  finally{reviewBusy=false;reviewControls();}
  if(reviewRendering)await pollReviewRender();
}
async function pollReviewRender(){
  if(!reviewRendering||reviewRenderPolling||currentView!=="review")return;
  clearTimeout(reviewRenderTimer);reviewRenderPolling=true;
  const pending=reviewRendering;
  try{
    const run=await api(`/api/runs/${pending.run_id}`);
    const clip=run.clips.find(c=>c.id===pending.id);
    if(clip&&clip.revision>=pending.revision){
      if(clip.status==="ready"&&clip.has_preview){
        reviewQueue[0]=clip;reviewRendering=null;paintReviewQueue();
        if(savedLayouts.some(p=>p.id===pending.layoutId))$("review-layout").value=pending.layoutId;
        reviewMessage("New revision ready. Review it before approving.");
      }else if(clip.status==="held"||["failed","cancelled","paused","completed"].includes(run.state)){
        reviewQueue[0]=clip;reviewRendering=null;paintReviewQueue();
        reviewMessage("The render needs attention. Open the run to continue.",true);
      }else reviewMessage(run.state==="queued"?"Waiting to render the new revision…":"Rendering the new revision. Your place in the queue is saved.");
    }else reviewMessage("Waiting for the new revision…");
  }catch(e){reviewMessage("Could not check the render status. Retrying automatically…",true);}
  finally{
    reviewRenderPolling=false;reviewControls();
    if(reviewRendering&&currentView==="review")reviewRenderTimer=setTimeout(pollReviewRender,2000);
  }
}
async function playReviewVideo(){
  const key=reviewVideoKey;
  try{await $("review-player").play();}
  catch(e){
    if(key!==reviewVideoKey||currentView!=="review"||e.name==="AbortError")return;
    reviewMessage(e.name==="NotAllowedError"?"Press play to watch this clip.":"This preview could not play. Refresh the queue or skip this clip.",e.name!=="NotAllowedError");
  }
}
async function decideReview(status){
  if(reviewBusy||reviewRendering||!reviewQueue.length||reviewQueue[0].status!=="ready"||!reviewQueue[0].has_preview)return;
  const clip=reviewQueue[0];reviewBusy=true;reviewControls();reviewMessage("Saving decision…");
  try{
    await saveClipReview(clip,status,status==="not_approved"?$("review-rejection-reason").value:undefined);
    reviewLast=clip;reviewQueue.shift();
    reviewMessage(`${reviewLabels[status]}. Decision saved.`);paintReviewQueue();
  }catch(e){reviewMessage(e.message+" Your place in the queue has been kept.",true);}
  finally{reviewBusy=false;reviewControls();}
}
function skipReview(){
  if(reviewBusy||reviewRendering||!reviewQueue.length)return;
  reviewQueue.shift();reviewSkipped++;
  reviewMessage("Skipped for now. No decision saved.");paintReviewQueue();
}
async function undoReview(){
  if(reviewBusy||reviewRendering||!reviewLast)return;
  const clip=reviewLast;reviewBusy=true;reviewControls();reviewMessage("Undoing last decision…");
  try{
    await saveClipReview(clip,"unreviewed");
    reviewQueue.unshift(clip);reviewLast=null;
    reviewMessage("Last decision cleared.");paintReviewQueue();
  }catch(e){reviewMessage(e.message,true);}
  finally{reviewBusy=false;reviewControls();}
}
$("review-approve").onclick=event=>{if(event.detail<2)return decideReview("approved");};
$("review-reject").onclick=event=>{if(event.detail<2)return decideReview("not_approved");};
$("review-skip").onclick=event=>{if(event.detail<2)skipReview();};
$("review-undo").onclick=undoReview;
$("refresh-review").onclick=loadReviewQueue;
$("review-layout").onchange=reviewControls;
$("review-rerender").onclick=event=>{if(event.detail<2)return rerenderReview();};
$("review-render-run").onclick=async()=>{if(!reviewQueue.length)return;activeRun=reviewQueue[0].run_id;try{await detail();paintRuns();goView("results");}catch(e){reviewMessage(e.message,true);}};
$("review-notes").onclick=()=>{if(reviewQueue.length)showClipNotes(reviewQueue[0]);};
$("review-player").onerror=()=>{if(reviewQueue.length)reviewMessage("This preview could not load. Refresh the queue or skip this clip.",true);};
document.addEventListener("keydown",event=>{
  if(currentView!=="review"||event.repeat||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey||$("clip-notes-dialog").open)return;
  if(event.target.closest("input,textarea,select,[contenteditable='true']"))return;
  const key=event.key.toLowerCase();
  if(key==="a"||key==="r"||key==="s"||key==="u"){
    event.preventDefault();
    if(key==="a")decideReview("approved");
    else if(key==="r")decideReview("not_approved");
    else if(key==="s")skipReview();
    else undoReview();
  }else if(event.code==="Space"&&!event.target.closest("video")){
    event.preventDefault();if(!reviewQueue.length||reviewRendering||!reviewQueue[0].has_preview)return;
    if($("review-player").paused)playReviewVideo();else $("review-player").pause();
  }
});
