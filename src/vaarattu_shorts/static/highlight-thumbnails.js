"use strict";
let highlightThumbnailState={key:null,frame:null,busy:false,message:""};
function highlightThumbnailUrl(run,id,download=false){return `/api/highlights/${run.id}/thumbnail/${id}?revision=${run.revision}${download?"&download=true":""}`;}
function paintHighlightThumbnails(run){
  const key=run?.has_draft?`${run.id}/${run.revision}`:null;
  $("highlight-thumbnails").hidden=!key;
  if(highlightThumbnailState.key!==key){
    highlightThumbnailState={key,frame:null,busy:false,message:""};
    $("highlight-thumbnail-note").value="";$("highlight-thumbnail-quality").value="medium";
  }
  if(!key)return;
  const state=highlightThumbnailState,items=run.thumbnails?.items||[];
  if(!state.frame)state.frame=items.find(item=>item.kind==="frame")||null;
  const unavailable=state.busy||run.state!=="completed";
  $("highlight-thumbnail-capture").disabled=unavailable;
  $("highlight-thumbnail-generate").disabled=unavailable||!state.frame||!run.thumbnails?.configured;
  for(const id of ["highlight-thumbnail-note","highlight-thumbnail-quality"])$(id).disabled=unavailable;
  const frame=$("highlight-thumbnail-frame");frame.hidden=!state.frame;
  if(state.frame){const url=highlightThumbnailUrl(run,state.frame.id);if(frame.getAttribute("src")!==url)frame.src=url;}
  else frame.removeAttribute("src");
  $("highlight-thumbnail-frame-label").textContent=state.frame?`Reference frame at ${highlightDuration(state.frame.seconds)} (${state.frame.quality} video).`:"";
  $("highlight-thumbnail-message").textContent=state.message||(!run.thumbnails?.configured?"Add OPENAI_API_KEY to .env and restart the app to enable image generation.":"Choose a reference frame. Generation can take several minutes; previous images are kept.");
  const results=$("highlight-thumbnail-results");results.replaceChildren();
  for(const item of items.filter(i=>i.kind==="thumbnail")){
    const card=document.createElement("article");
    if(item.status==="completed"){
      const img=document.createElement("img");img.className="highlight-thumbnail-image";img.src=highlightThumbnailUrl(run,item.id);img.alt="Generated YouTube thumbnail";img.loading="lazy";
      const link=document.createElement("a");link.href=highlightThumbnailUrl(run,item.id,true);link.textContent="Download thumbnail";
      card.append(img,link,text("p",`${item.quality} quality · Frame at ${highlightDuration(item.seconds)}`,"muted"));
    }else card.append(text("p",item.message,"muted"));
    if(item.note)card.append(text("p",item.note,"muted"));
    results.append(card);
  }
}
async function changeHighlightThumbnail(generate){
  const run=highlightRuns.find(r=>r.id===highlightSelected),state=highlightThumbnailState;
  if(!run||state.busy||run.state!=="completed")return;
  const body={revision:run.revision};
  if(generate){
    if(!state.frame||!run.thumbnails?.configured)return;
    if(highlightCopyState.dirty){error("Save the title and description before generating a thumbnail.");return;}
    body.frame_id=state.frame.id;body.request_id=crypto.randomUUID().replaceAll("-","");
    body.note=$("highlight-thumbnail-note").value.trim();body.quality=$("highlight-thumbnail-quality").value;
  }else{
    const player=$("highlight-player");
    if(player.readyState<2||!Number.isFinite(player.currentTime)){error("Wait for the video to load, then choose a frame.");return;}
    player.pause();body.seconds=player.currentTime;
  }
  state.busy=true;state.message=generate?"Generating thumbnail. This can take several minutes...":"Capturing the selected frame...";paintHighlightThumbnails(run);
  try{
    const saved=await api(`/api/highlights/${run.id}/thumbnail/${generate?"generate":"frame"}`,{method:"POST",body:JSON.stringify(body)});
    const current=highlightRuns.find(r=>r.id===run.id);
    if(current?.revision===run.revision){
      current.thumbnails=current.thumbnails||{items:[]};
      current.thumbnails.items=[saved,...current.thumbnails.items.filter(item=>item.id!==saved.id)];
    }
    if(highlightThumbnailState!==state)return;
    if(!generate)state.frame=saved;
    state.message=generate?(saved.status==="completed"?"Thumbnail saved. Review it below and download the version you prefer.":saved.message):"Reference frame captured. Add instructions or generate a thumbnail.";
  }catch(e){if(highlightThumbnailState===state)state.message=e.message;}
  finally{state.busy=false;if(highlightThumbnailState===state)paintHighlightThumbnails(highlightRuns.find(r=>r.id===highlightSelected));}
}
$("highlight-thumbnail-capture").onclick=()=>changeHighlightThumbnail(false);
$("highlight-thumbnail-generate").onclick=()=>changeHighlightThumbnail(true);
$("highlight-thumbnail-refresh").onclick=()=>loadHighlights();
