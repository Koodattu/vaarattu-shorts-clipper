"use strict";
let highlightThumbnailState={key:null,frames:null,busy:false,message:""};
function highlightThumbnailUrl(run,id,download=false){return `/api/highlights/${run.id}/thumbnail/${id}?revision=${run.revision}${download?"&download=true":""}`;}
function paintHighlightThumbnails(run){
  const key=run?.has_draft?`${run.id}/${run.revision}`:null;
  $("highlight-thumbnails").hidden=!key;
  if(highlightThumbnailState.key!==key){
    highlightThumbnailState={key,frames:null,busy:false,message:""};
    $("highlight-thumbnail-note").value="";$("highlight-thumbnail-quality").value="medium";
  }
  if(!key)return;
  const state=highlightThumbnailState,items=run.thumbnails?.items||[];
  if(state.frames===null)state.frames=items.filter(item=>item.kind==="frame").slice(0,1);
  const unavailable=state.busy||run.state!=="completed";
  $("highlight-thumbnail-capture").disabled=unavailable||state.frames.length>=16;
  $("highlight-thumbnail-generate").disabled=unavailable||!state.frames.length||!run.thumbnails?.configured;
  for(const id of ["highlight-thumbnail-note","highlight-thumbnail-quality"])$(id).disabled=unavailable;
  const gallery=$("highlight-thumbnail-frames");gallery.replaceChildren();
  for(const frame of items.filter(item=>item.kind==="frame")){
    const card=text("label","","highlight-thumbnail-reference"),input=document.createElement("input");
    const index=state.frames.findIndex(f=>f.id===frame.id);
    input.type="checkbox";input.checked=index>=0;input.disabled=unavailable||(index<0&&state.frames.length>=16);
    input.setAttribute("aria-label",`Use frame at ${highlightSourceTime(frame.seconds)}`);
    input.onchange=()=>{
      if(input.checked)state.frames.push(frame);else state.frames=state.frames.filter(f=>f.id!==frame.id);
      state.message="";paintHighlightThumbnails(run);
    };
    const img=document.createElement("img");img.src=highlightThumbnailUrl(run,frame.id);img.alt=`Video frame at ${highlightSourceTime(frame.seconds)}`;img.loading="lazy";
    card.append(img,input,text("span",`${index>=0?`Reference ${index+1} · `:""}${highlightSourceTime(frame.seconds)} (${frame.quality})`));gallery.append(card);
  }
  $("highlight-thumbnail-frame-label").textContent=`${state.frames.length} of 16 reference frames selected. Uncheck a frame to leave it out.`;
  $("highlight-thumbnail-message").textContent=state.message||(!run.thumbnails?.configured?"Add OPENAI_API_KEY to .env and restart the app to enable image generation.":"Add frames from the player or select saved frames below. Generation can take several minutes; previous images are kept.");
  const results=$("highlight-thumbnail-results");results.replaceChildren();
  for(const item of items.filter(i=>i.kind==="thumbnail")){
    const card=document.createElement("article");
    if(item.status==="completed"){
      const img=document.createElement("img");img.className="highlight-thumbnail-image";img.src=highlightThumbnailUrl(run,item.id);img.alt="Generated YouTube thumbnail";img.loading="lazy";
      const link=document.createElement("a");link.href=highlightThumbnailUrl(run,item.id,true);link.textContent="Download thumbnail";
      card.append(img,link,text("p",`${item.quality} quality · Frames at ${(item.frame_seconds||[item.seconds]).map(highlightSourceTime).join(", ")}`,"muted"));
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
    if(!state.frames?.length||!run.thumbnails?.configured)return;
    if(highlightCopyState.dirty){error("Save the title and description before generating a thumbnail.");return;}
    body.frame_ids=state.frames.map(f=>f.id);body.request_id=crypto.randomUUID().replaceAll("-","");
    body.note=$("highlight-thumbnail-note").value.trim();body.quality=$("highlight-thumbnail-quality").value;
  }else{
    if(state.frames?.length>=16)return;
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
    if(!generate)state.frames=[...state.frames.filter(f=>f.id!==saved.id),saved];
    state.message=generate?(saved.status==="completed"?"Thumbnail saved. Review it below and download the version you prefer.":saved.message):"Frame added. Choose another moment to add more, or generate a thumbnail.";
  }catch(e){if(highlightThumbnailState===state)state.message=e.message;}
  finally{state.busy=false;if(highlightThumbnailState===state)paintHighlightThumbnails(highlightRuns.find(r=>r.id===highlightSelected));}
}
$("highlight-thumbnail-capture").onclick=()=>changeHighlightThumbnail(false);
$("highlight-thumbnail-generate").onclick=()=>changeHighlightThumbnail(true);
$("highlight-thumbnail-refresh").onclick=()=>loadHighlights();
