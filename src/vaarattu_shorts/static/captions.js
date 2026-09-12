"use strict";
let captionClip=null,captionBusy=false,captionTimer=null,captionFromEditor=false;
function captionEditorState(){return JSON.stringify(["edit-start","edit-end","edit-title","edit-layout","edit-words","edit-encoder"].map(id=>$(id).value).concat([$("edit-trim-silence").checked]));}
function paintCaptionCheck(){
  const check=captionClip?.caption_check,pending=check?.status==="pending";
  $("caption-changes").replaceChildren();
  for(const change of check?.status==="complete"?check.changes:[]){
    const label=text("label","","check"),input=document.createElement("input");input.type="checkbox";input.value=change.word_id;input.checked=true;
    label.append(input,text("span",`${change.original} → ${change.replacement} — ${change.reason}`));$("caption-changes").append(label);
  }
  const last=captionClip?.caption_corrections?.at(-1);
  if(last&&!last.undone&&last.changes.length){
    $("caption-changes").append(text("p","Previously applied corrections:"));
    for(const c of last.changes)$("caption-changes").append(text("p",`${c.original} → ${c.replacement} — ${c.reason}`));
  }
  $("caption-check-again").disabled=captionBusy||pending;
  $("caption-apply").disabled=captionBusy||pending||check?.status!=="complete"||!check.changes.length;
  $("caption-undo").disabled=captionBusy||pending||!last||last.undone||!last.changes.length;
  $("caption-message").textContent=pending?"Checking captions. The current preview is unchanged.":check?.status==="complete"?(check.changes.length?"Select the corrections you want to apply.":"No clear word errors found."):captionClip?.caption_correction_warning||"Check the saved captions for individual word errors.";
}
async function openCaptionCheck(id,fromEditor=false){
  if(!id||captionBusy)return;
  if(fromEditor&&editing.captionFormState!==captionEditorState()){error("Save your current editor changes before checking the saved captions.");return;}
  captionFromEditor=fromEditor;captionBusy=true;
  try{captionClip=await api(`/api/clips/${id}`);$("caption-dialog").showModal();}
  catch(e){error(e.message);return;}
  finally{captionBusy=false;}
  paintCaptionCheck();
  if(captionClip.caption_check?.status==="pending")pollCaptionCheck();
  else if(!captionClip.caption_check&&!captionClip.caption_corrections?.length)await requestCaptionCheck();
}
async function requestCaptionCheck(){
  if(captionBusy||!captionClip)return;captionBusy=true;paintCaptionCheck();
  try{
    const request=await api(`/api/clips/${captionClip.id}/caption-check`,{method:"POST",body:JSON.stringify({expected_revision:captionClip.revision})});
    captionClip.caption_check={id:request.request_id,status:"pending"};
  }catch(e){captionBusy=false;paintCaptionCheck();$("caption-message").textContent=e.message;return;}
  captionBusy=false;paintCaptionCheck();await pollCaptionCheck();
}
async function pollCaptionCheck(){
  clearTimeout(captionTimer);if(!$("caption-dialog").open||!captionClip)return;
  const id=captionClip.id;
  try{
    const clip=await api(`/api/clips/${id}`);if(!$("caption-dialog").open||captionClip?.id!==id)return;
    captionClip=clip;paintCaptionCheck();
    if(clip.caption_check?.status!=="pending")return;
    const run=await api(`/api/runs/${clip.run_id}`);
    if(!["queued","running"].includes(run.state)){
      $("caption-message").textContent=`Caption check ${run.state}. Open Runs & clips to resume or retry it. ${run.message||""}`;return;
    }
  }catch(e){$("caption-message").textContent=`Could not refresh the caption check. Retrying. ${e.message}`;}
  captionTimer=setTimeout(pollCaptionCheck,2000);
}
async function saveCaptionCorrections(undo=false){
  if(captionBusy||!captionClip)return;
  const clip=captionClip;
  const ids=[...$("caption-changes").querySelectorAll("input:checked")].map(input=>input.value);
  if(!undo&&!ids.length){$("caption-message").textContent="Select at least one correction.";return;}
  captionBusy=true;$("caption-apply").disabled=true;$("caption-undo").disabled=true;$("caption-check-again").disabled=true;
  try{
    const result=await api(`/api/clips/${clip.id}/${undo?"caption-undo":"caption-corrections"}`,{method:"POST",body:JSON.stringify({expected_revision:clip.revision,...(undo?{}:{check_id:clip.caption_check.id,word_ids:ids})})});
    $("caption-dialog").close();clearTimeout(captionTimer);
    if(!captionFromEditor&&reviewQueue[0]?.id===clip.id){
      reviewRendering={id:clip.id,...result};reviewQueue[0]={...clip,revision:result.revision,status:"pending",has_preview:false};paintReviewQueue();await pollReviewRender();
    }else{activeRun=result.run_id;detailSignature="";await refresh();goView("results");}
  }catch(e){$("caption-message").textContent=e.message;}
  finally{captionBusy=false;if($("caption-dialog").open){$("caption-apply").disabled=false;$("caption-undo").disabled=false;$("caption-check-again").disabled=false;}}
}
$("review-caption-check").onclick=()=>openCaptionCheck(reviewQueue[0]?.id);
$("edit-caption-check").onclick=()=>openCaptionCheck(editing?.id,true);
$("caption-check-again").onclick=requestCaptionCheck;
$("caption-apply").onclick=()=>saveCaptionCorrections();
$("caption-undo").onclick=()=>saveCaptionCorrections(true);
$("caption-close").onclick=()=>{if(!captionBusy)$("caption-dialog").close();};
$("caption-dialog").oncancel=event=>{if(captionBusy)event.preventDefault();};
$("caption-dialog").onclose=()=>clearTimeout(captionTimer);
