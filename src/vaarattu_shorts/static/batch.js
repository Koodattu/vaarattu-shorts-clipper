"use strict";
const selectedLibraryVideos=new Map();
let batchVideos=[], batchCurrent=0, batchJobs=null;
const singleRunSubmit=$("run-form").onsubmit;
function batchActive(){return batchVideos.length>0;}
function emptyChat(){return {stream_id:null,stream_offset_seconds:null,alignment_confirmed:false,query:""};}
function toggleLibraryVideo(video,selected){
  if(selected&&video.available)selectedLibraryVideos.set(video.url,video);
  else selectedLibraryVideos.delete(video.url);
  paintLibrarySelection();
}
function paintLibrarySelection(){
  for(const video of library?.videos||[]){
    if(selectedLibraryVideos.has(video.url)){
      if(video.available)selectedLibraryVideos.set(video.url,video);
      else selectedLibraryVideos.delete(video.url);
    }
    const card=videoCards.get(`${video.provider||"youtube"}:${video.id}`)?.node;
    const checkbox=card?.querySelector('input[type="checkbox"]');
    if(checkbox){checkbox.checked=selectedLibraryVideos.has(video.url);checkbox.disabled=!video.available||Boolean(batchJobs);}
    if(card)card.classList.toggle("is-selected",selectedLibraryVideos.has(video.url));
  }
  const count=selectedLibraryVideos.size;
  $("library-selection").hidden=!count&&!batchJobs;
  $("library-selected-count").textContent=batchJobs?`${batchJobs.filter(job=>job.id).length} of ${batchJobs.length} videos queued`:`${count} ${count===1?"video":"videos"} selected`;
  $("clear-video-selection").disabled=Boolean(batchJobs);
  $("batch-create").textContent=batchJobs?"Continue queueing batch":`Create shorts (${count})`;
  $("batch-create").disabled=!count&&!batchJobs;
}
$("clear-video-selection").onclick=()=>{selectedLibraryVideos.clear();paintLibrarySelection();};
$("batch-create").onclick=()=>{
  if(batchJobs){goView("process");return;}
  saveBatchChat();
  const previous=new Map(batchVideos.map(video=>[video.url,video.chat]));
  batchVideos=Array.from(selectedLibraryVideos.values(),video=>({...video,chat:previous.get(video.url)||emptyChat()}));
  if(!batchActive())return;
  $("batch-chat-video").replaceChildren();
  batchVideos.forEach((video,index)=>{const option=text("option",video.title);option.value=String(index);$("batch-chat-video").append(option);});
  setBatchMode(true);loadBatchChat(0);paintBatchSources();goView("process");
};
function setBatchMode(enabled){
  $("batch-sources").hidden=!enabled;$("batch-chat-controls").hidden=!enabled;
  $("single-source").hidden=enabled;$("video").disabled=enabled;
  $("batch-progress").hidden=!enabled;$("batch-progress").textContent="";
  $("run-button").textContent=enabled?`Queue ${batchVideos.length} videos`:"Run video →";
  $("process-heading").textContent=enabled?"Create shorts from selected videos":"New run";
  $("process-description").textContent=enabled?"Choose processing settings once for all selected recordings.":"Turn one recording into a collection of moments.";
  $("run-submit-note").textContent=enabled?"Creates one run per video. API spending limits apply separately to each run.":"Starts processing this recording.";
  $("chat-description").textContent=enabled?"Choose a recording, find its stream, then confirm its timing to include chat peaks. Recordings without a confirmed match still use the full transcript.":"Also review speech around chat spikes. Selecting a library video automatically looks for its stream. Confirm the timing to enable chat peaks. The full transcript is always scanned.";
  $("chat-settings").open=false;
}
function saveBatchChat(){
  if(!batchActive()||batchJobs)return;
  batchVideos[batchCurrent].chat={stream_id:$("stream-id").value===""?null:Number($("stream-id").value),
    stream_offset_seconds:$("stream-offset").value===""?null:Number($("stream-offset").value),
    alignment_confirmed:$("alignment").checked,query:$("stream-query").value};
}
function loadBatchChat(index){
  batchCurrent=index;const video=batchVideos[index], chat=video.chat;
  resetStreamMatch();matchingVideo=video.url;$("video").value=video.url;
  $("batch-chat-video").value=String(index);
  $("stream-query").value=chat.query||video.title;
  $("stream-id").value=chat.stream_id==null?"":String(chat.stream_id);
  $("stream-offset").value=chat.stream_offset_seconds==null?"":String(chat.stream_offset_seconds);
  $("alignment").checked=chat.alignment_confirmed;
  $("stream-match-status").textContent=chat.stream_id?`Stream ${chat.stream_id} · ${chat.alignment_confirmed?"Timing confirmed":"Confirm the timing to enable chat peaks"}.`:"No chat matching. The full transcript will still be scanned.";
}
$("batch-chat-video").onchange=()=>{saveBatchChat();loadBatchChat(Number($("batch-chat-video").value));paintBatchSources();};
$("batch-no-chat").onclick=()=>{batchVideos[batchCurrent].chat=emptyChat();loadBatchChat(batchCurrent);paintBatchSources();};
function paintBatchSources(){
  $("batch-source-list").replaceChildren();
  batchVideos.forEach((video,index)=>{
    const row=text("div","","batch-source");
    const chat=video.chat;
    const status=batchJobs?.[index]?.id?"Queued":chat.alignment_confirmed?`Chat stream ${chat.stream_id} · timing confirmed`:chat.stream_id?`Stream ${chat.stream_id} · chat timing not confirmed`:"No chat matching";
    row.append(text("strong",video.title),text("span",status,"muted"));
    const edit=action("Chat settings",()=>{saveBatchChat();loadBatchChat(index);paintBatchSources();$("chat-settings").open=true;$("batch-chat-video").focus();});
    edit.disabled=Boolean(batchJobs);row.append(edit);$("batch-source-list").append(row);
  });
}
$("run-form").addEventListener("input",()=>{if(batchActive()&&!batchJobs){saveBatchChat();paintBatchSources();}});
function leaveBatchForSingle(){
  if(batchJobs){goView("process");error("Finish queueing this batch with Retry remaining videos before starting another selection.");return false;}
  if(batchActive()){batchVideos=[];setBatchMode(false);resetStreamMatch();matchingVideo="";$("video").value="";paintSelectedVideo();}
  return true;
}
$("cancel-batch").onclick=()=>{leaveBatchForSingle();};
$("run-form").onsubmit=async event=>{
  if(!batchActive())return singleRunSubmit(event);
  event.preventDefault();if(submittingRun)return;
  error("");saveBatchChat();
  if(!batchJobs){
    let settings;
    try{settings=runRequest();}catch(e){error(e.message);return;}
    for(const video of batchVideos){
      const chat=video.chat;
      if(chat.alignment_confirmed&&(!Number.isInteger(chat.stream_id)||chat.stream_id<=0||chat.stream_id>2147483647||chat.stream_offset_seconds===null||!Number.isFinite(chat.stream_offset_seconds))){error(`Check the stream ID and timing for ${video.title}.`);return;}
      if(!library?.videos.some(v=>v.url===video.url&&v.available)){error(`${video.title} is no longer available. Update your selection in the library.`);return;}
    }
    batchJobs=batchVideos.map(video=>({key:crypto.randomUUID(),id:null,body:{...settings,video:video.url,
      stream_id:video.chat.alignment_confirmed?video.chat.stream_id:null,
      stream_offset_seconds:video.chat.alignment_confirmed?video.chat.stream_offset_seconds:null,
      alignment_confirmed:video.chat.alignment_confirmed}}));
  }
  streamLookup++;$("find-stream").disabled=false;$("check-stream").disabled=false;
  submittingRun=true;$("run-button").disabled=true;$("run-settings").disabled=true;paintBatchSources();paintLibrarySelection();
  try{
    for(const job of batchJobs){
      if(job.id)continue;
      const queued=batchJobs.filter(item=>item.id).length;
      $("batch-progress").textContent=`Queueing video ${queued+1} of ${batchJobs.length}…`;
      const result=await api("/api/runs",{method:"POST",headers:{"Idempotency-Key":job.key},body:JSON.stringify(job.body)});
      job.id=result.id;activeRun=result.id;paintBatchSources();
    }
    const total=batchJobs.length;
    batchJobs=null;batchVideos=[];selectedLibraryVideos.clear();setBatchMode(false);resetStreamMatch();$("video").value="";matchingVideo="";
    paintLibrarySelection();goView("results");
    $("notice").textContent=`${total} videos queued with the shared processing settings.`;$("notice").hidden=false;
    await refresh();
  }catch(e){
    if(batchJobs){
      const queued=batchJobs.filter(job=>job.id).length;
      // A rejected first request is safe to edit. Ambiguous responses keep the exact keys and bodies for retry.
      if(!queued&&[400,422].includes(e.status)){batchJobs=null;$("run-settings").disabled=false;$("run-button").textContent=`Queue ${batchVideos.length} videos`;}
      else $("run-button").textContent="Retry remaining videos";
      $("batch-progress").textContent=`${queued} of ${batchVideos.length} videos confirmed queued. ${batchJobs?"Retry uses the same settings and avoids duplicate runs.":"Correct the settings and try again."}`;
    }
    error(e.message);
  }finally{
    submittingRun=false;$("run-button").disabled=false;$("run-settings").disabled=Boolean(batchJobs);if(batchActive())paintBatchSources();paintLibrarySelection();
  }
};
if(library)paintLibrarySelection();
if(typeof window!=="undefined")window.addEventListener("beforeunload",event=>{
  if(batchJobs){event.preventDefault();event.returnValue="";}
});
