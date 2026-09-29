"use strict";
let manualCaptionClip=null,manualCaptionBusy=false,manualCaptionInputs=[];
function manualCaptionControls(){
  const changed=manualCaptionInputs.some(({word,input})=>input.value.trim()!==word.text.trim());
  $("manual-caption-save").disabled=manualCaptionBusy||!changed||manualCaptionInputs.some(({input})=>!input.value.trim());
  $("manual-caption-close").disabled=manualCaptionBusy;
  $("manual-caption-fields").disabled=manualCaptionBusy;
}
async function openManualCaptions(){
  const queued=reviewQueue[0];
  if(reviewBusy||reviewRendering||!queued||queued.status!=="ready"||!queued.has_preview)return;
  reviewBusy=true;reviewControls();$("review-player").pause();
  manualCaptionBusy=true;
  try{
    const clip=await api(`/api/clips/${queued.id}`);
    if(clip.status!=="ready"||!clip.has_preview)throw new Error("This clip changed. Refresh the queue before editing captions.");
    manualCaptionClip=clip;manualCaptionInputs=[];
    $("manual-caption-words").replaceChildren();
    for(const word of clip.words||[]){
      const removed=(clip.speech_cuts||[]).some(cut=>cut.start_us<=word.start_us&&word.end_us<=cut.end_us);
      const spans=clip.pacing?.retained;
      if(removed||(spans&&!spans.some(span=>span.source_start_us<=word.start_us&&word.end_us<=span.source_end_us)))continue;
      const input=document.createElement("input");input.type="text";input.value=word.text;input.required=true;
      input.setAttribute("aria-label",`Caption word ${manualCaptionInputs.length+1}: ${word.text}`);
      const resize=()=>{input.size=Math.max(3,Math.min(40,input.value.length+1));};
      input.oninput=()=>{resize();manualCaptionControls();};resize();
      manualCaptionInputs.push({word,input});$("manual-caption-words").append(input);
    }
    $("manual-caption-message").textContent=manualCaptionInputs.length?"The video shows the saved captions until you render your changes.":"No caption words are available to edit.";
    const player=$("manual-caption-player");
    player.src=`/api/artifacts/${clip.id}/video?revision=${clip.revision}`;
    const position=clip.revision===queued.revision?$("review-player").currentTime:0;
    player.onloadedmetadata=()=>{player.currentTime=position||0;};player.load();
    $("manual-caption-dialog").showModal();
  }catch(e){reviewMessage(e.message,true);manualCaptionClip=null;reviewBusy=false;reviewControls();}
  finally{manualCaptionBusy=false;manualCaptionControls();}
}
async function saveManualCaptions(event){
  event.preventDefault();
  if(manualCaptionBusy||!manualCaptionClip)return;
  manualCaptionControls();if($("manual-caption-save").disabled)return;
  const clip=manualCaptionClip;
  const changed=new Map(manualCaptionInputs.filter(({word,input})=>input.value.trim()!==word.text.trim()).map(({word,input})=>[word.id,input.value.trim()]));
  manualCaptionBusy=true;manualCaptionControls();$("manual-caption-message").textContent="Saving captions…";
  try{
    const result=await api(`/api/clips/${clip.id}/edit`,{method:"POST",body:JSON.stringify({
      expected_revision:clip.revision,start_us:clip.start_us,end_us:clip.end_us,title:clip.title,
      words:clip.words.map(word=>changed.has(word.id)?{...word,text:changed.get(word.id)}:word),reviewed:true,
    })});
    reviewLast=null;reviewRendering={id:clip.id,...result};
    reviewQueue[0]={...clip,revision:result.revision,status:"pending",has_preview:false};
    $("manual-caption-dialog").close();paintReviewQueue();
    reviewMessage("Captions saved. Rendering the new revision; your place in the queue is kept.");
  }catch(e){$("manual-caption-message").textContent=e.message;}
  finally{manualCaptionBusy=false;manualCaptionControls();}
  if(reviewRendering)await pollReviewRender();
}
$("review-caption-edit").onclick=openManualCaptions;
$("manual-caption-form").onsubmit=saveManualCaptions;
$("manual-caption-close").onclick=()=>{if(!manualCaptionBusy)$("manual-caption-dialog").close();};
$("manual-caption-dialog").oncancel=event=>{if(manualCaptionBusy)event.preventDefault();};
$("manual-caption-dialog").onclose=()=>{
  $("manual-caption-player").pause();$("manual-caption-player").removeAttribute("src");$("manual-caption-player").load();
  manualCaptionClip=null;manualCaptionInputs=[];reviewBusy=false;reviewControls();
};
