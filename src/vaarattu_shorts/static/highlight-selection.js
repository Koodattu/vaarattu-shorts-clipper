"use strict";
let highlightSelection={key:null,data:null,floor:75,busy:false,run:null};
async function loadHighlightSelection(run,force=false){
  const key=run&&(run.has_draft||run.selection_preview)?`${run.id}/${run.revision}`:null;
  $("highlight-selection").hidden=!key;
  if(!key){highlightSelection={key:null,data:null,floor:75,busy:false,run:null};return;}
  if(!force&&highlightSelection.key===key){highlightSelection.run=run;paintHighlightSelection();return;}
  const state={key,data:null,floor:75,busy:false,run};highlightSelection=state;
  $("highlight-selection-retry").hidden=true;
  $("highlight-selection-summary").textContent="Calculating lengths from the saved scene scores...";
  $("highlight-selection-render").disabled=true;
  $("highlight-selection-slider").disabled=true;$("highlight-selection-number").disabled=true;
  try{
    const data=await api(`/api/highlights/${run.id}/selection?revision=${run.revision}`);
    if(highlightSelection!==state)return;
    state.data=data;state.floor=data.current_floor;paintHighlightSelection();
  }catch(e){
    if(highlightSelection!==state)return;
    $("highlight-selection-summary").textContent=e.message;
    $("highlight-selection-retry").hidden=false;
  }
}
function paintHighlightSelection(){
  const state=highlightSelection;if(!state.data)return;
  const preview=state.data.previews[state.floor];
  $("highlight-selection-slider").value=String(state.floor);$("highlight-selection-number").value=String(state.floor);
  $("highlight-selection-slider").disabled=state.busy;$("highlight-selection-number").disabled=state.busy;
  $("highlight-selection-summary").textContent=`${highlightClock(Math.round(preview.duration))} predicted video length · ${preview.scenes} scenes · ${preview.sections} retained sections`;
  $("highlight-selection-change").textContent=preview.scenes?`${preview.added} scenes added and ${preview.removed} removed compared with this revision. ${state.run.state!=="completed"?"Wait for current processing to finish before rendering another draft.":""}`:"No scenes meet this floor. Lower it to render a video.";
  $("highlight-selection-render").disabled=state.busy||state.run.state!=="completed"||!preview.scenes||(!preview.added&&!preview.removed);
  $("highlight-selection-render").textContent=state.busy?"Requesting new draft...":"Render new draft with this floor";
}
function changeHighlightFloor(value){
  if(!highlightSelection.data||highlightSelection.busy)return;
  highlightSelection.floor=Math.max(0,Math.min(100,Math.round(Number(value)||0)));
  paintHighlightSelection();
}
async function renderHighlightSelection(){
  const state=highlightSelection,run=state.run;
  if(!state.data||state.busy||$("highlight-selection-render").disabled)return;
  state.busy=true;paintHighlightSelection();
  try{
    await api(`/api/highlights/${run.id}/selection`,{method:"POST",body:JSON.stringify({revision:run.revision,score_floor:state.floor,plan_hash:state.data.plan_hash})});
    highlightMessage("New draft requested with the selected floor. The previous revision is saved.");
    await loadHighlights();
  }catch(e){error(e.message);}
  finally{state.busy=false;if(highlightSelection===state)paintHighlightSelection();}
}
$("highlight-selection-slider").oninput=event=>changeHighlightFloor(event.target.value);
$("highlight-selection-number").oninput=$("highlight-selection-number").onchange=event=>changeHighlightFloor(event.target.value);
$("highlight-selection-retry").onclick=()=>loadHighlightSelection(highlightSelection.run,true);
$("highlight-selection-render").onclick=renderHighlightSelection;
