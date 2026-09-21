"use strict";
let highlightCopyState={key:null,id:null,dirty:false,busy:false};
function paintHighlightCopy(run){
  const key=run?.has_draft?`${run.id}/${run.revision}`:null;
  $("highlight-copy").hidden=!key;
  const changed=highlightCopyState.key!==key;
  if(changed){highlightCopyState={key,id:null,dirty:false,busy:false};$("highlight-copy-note").value="";}
  if(!key)return;
  const state=highlightCopyState,saved=run.publishing_copy;
  if(changed||(!state.dirty&&!state.busy&&state.id!==(saved?.id||null))){
    $("highlight-copy-title").value=saved?.title||"";
    $("highlight-copy-description").value=saved?.caption||"";
    state.id=saved?.id||null;
  }
  const unavailable=state.busy||run.state!=="completed";
  for(const id of ["highlight-copy-title","highlight-copy-description","highlight-copy-note"])$(id).disabled=unavailable;
  $("highlight-copy-save").disabled=unavailable||!state.dirty;
  $("highlight-copy-generate").disabled=unavailable||state.dirty;
  $("highlight-copy-generate").textContent=saved?.title?"Regenerate title & description":"Generate title & description";
  $("highlight-copy-message").textContent=state.busy?"Preparing posting text...":state.dirty?"Unsaved changes. Save the text before regenerating.":saved?.error|| (saved?.title?"Saved for this video revision.":"Generate posting text or enter your own. Uses this recording's model and remaining allowance.");
}
async function changeHighlightCopy(generate){
  const run=highlightRuns.find(r=>r.id===highlightSelected),state=highlightCopyState;
  if(!run||state.busy||run.state!=="completed")return;
  const body={revision:run.revision,expected_id:state.id};
  if(generate){if(state.dirty)return;body.note=$("highlight-copy-note").value.trim();}
  else{
    body.title=$("highlight-copy-title").value.trim();body.caption=$("highlight-copy-description").value.trim();
    if(!body.title||!body.caption){error("Enter a title and description first.");return;}
  }
  state.busy=true;paintHighlightCopy(run);
  try{
    const saved=await api(`/api/highlights/${run.id}/copy${generate?"/generate":""}`,{method:"POST",body:JSON.stringify(body)});
    if(highlightCopyState!==state)return;
    const current=highlightRuns.find(r=>r.id===run.id);
    if(!current||current.revision!==run.revision)return;
    current.publishing_copy=saved;state.busy=false;state.dirty=false;paintHighlightCopy(current);
  }catch(e){
    if(highlightCopyState!==state)return;
    state.busy=false;paintHighlightCopy(run);$("highlight-copy-message").textContent=e.message;
  }finally{state.busy=false;}
}
for(const id of ["highlight-copy-title","highlight-copy-description"])$(id).oninput=()=>{
  highlightCopyState.dirty=true;paintHighlightCopy(highlightRuns.find(r=>r.id===highlightSelected));
};
$("highlight-copy-generate").onclick=()=>changeHighlightCopy(true);
$("highlight-copy-save").onclick=()=>changeHighlightCopy(false);
