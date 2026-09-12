"use strict";
const $ = id => document.getElementById(id);
let token = "", activeRun = null, editing = null, polling = false;
let savedLayouts = [];
let submittingRun = false, concurrencyLimit = 1;
let library = null, videoPage = 0, fetchingVideos = false;
let matchingVideo = "", streamLookup = 0;
const videoPageSize = 8, clipPageSize = 10;
let clipPage = 0, displayedRun = null;
let runList = [], detailSignature = "", clipsSignature = "", runListSignature = "", currentView = "library";
const labels = {local:"Local · Gemma 4",gemini:"Gemini 3.8 Flash",openai:"GPT-5.6 Luna",codex:"Codex",zai:"GLM-5.3-Flash",deepseek:"DeepSeek V4 Flash",meta:"Meta Muse Spark 1.3"};
function showView(view){
  const names={library:"Video library",gallery:"Clip gallery",review:"Review queue",process:"New run",results:"Runs & clips",layouts:"Layout presets",editor:"Edit clip"};
  if(!names[view])return;
  if(currentView!==view){
    $("notice").hidden=true;
    $("source-player").pause();
    $("review-player").pause();
    for(const video of $("clips").querySelectorAll("video"))video.pause();
    for(const video of $("gallery-clips").querySelectorAll("video"))video.pause();
    if(view==="layouts"){
      const fromEditor=currentView==="editor"&&editing;
      $("layout-return").href=fromEditor?"#editor":"#process";
      $("layout-return").textContent=fromEditor?"Back to clip editor →":"Back to new run →";
    }
  }
  currentView=view;
  for(const key of ["library","gallery","review","process","results","layouts"]){
    $("view-"+key).hidden=key!==view;
    $("nav-"+key).setAttribute("aria-current",key===(view==="editor"?"results":view)?"page":"false");
  }
  $("editor").hidden=view!=="editor";
  $("view-label").textContent=names[view];
  const heading=$(view==="library"?"channel-heading":view+"-heading");
  heading.focus({preventScroll:true});
  $("workspace").scrollIntoView({behavior:"auto"});
  if(view==="gallery"&&typeof loadGallery==="function")loadGallery().catch(e=>error(e.message));
  if(view==="review"&&typeof loadReviewQueue==="function")loadReviewQueue();
  if(view==="process")matchSelectedStream();
}
function goView(view){
  showView(view);
  if(typeof window!=="undefined"&&window.location.hash!=="#"+view)window.history.pushState(null,"","#"+view);
}
function error(message){$("error").textContent=message;$("error").hidden=!message;if(message){$("notice").hidden=true;$("error").scrollIntoView({behavior:"auto"});}}
async function api(path, options={}){
  const response=await fetch(path,{...options,headers:{"Content-Type":"application/json","X-Local-Token":token,...options.headers}});
  const value=await response.json();
  if(!response.ok) throw new Error(typeof value.detail==="string"?value.detail:"Check the form fields and try again.");
  return value;
}
function action(label, fn, secondary=true){const button=document.createElement("button");button.type="button";button.textContent=label;if(secondary)button.className="secondary";button.onclick=()=>Promise.resolve(fn()).catch(e=>error(e.message));return button;}
function text(tag, value, className){const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node;}
async function layouts(){const list=await api("/api/layouts");savedLayouts=list;for(const id of ["layout","edit-layout","preset-select"]){const selected=$(id).value;$(id).replaceChildren();if(id==="preset-select"){const empty=text("option","New preset");empty.value="";$(id).append(empty);}for(const item of list){const o=document.createElement("option");o.value=item.id;o.textContent=item.body.name;$(id).append(o);}if(list.some(l=>l.id===selected))$(id).value=selected;else if(id==="edit-layout")$(id).value="";}$("layout-required").hidden=Boolean(list.length);}
async function init(){const state=await api("/api/status");token=state.token;concurrencyLimit=state.max_concurrent_jobs||1;$("job-concurrency").value=String(concurrencyLimit);$("cache-path").textContent=`Model cache: ${state.model_cache}`;
  for(const [key,info] of Object.entries(state.providers)){const o=document.createElement("option");o.value=key;o.textContent=(key==="codex"?`Codex · ${info.model}`:labels[key])+(!info.available?" · unavailable":!info.configured?" · key missing":"");o.disabled=!info.available||!info.configured;$("provider").append(o);}
  $("setup-status").textContent=state.models.turbo?"Turbo is prepared.":"Prepare Turbo and your selected LLM before running. See docs/setup.md.";
  $("final-transcription").disabled=!state.models["large-v3"];
  $("final-transcription-status").textContent=state.models["large-v3"]?"Large-v3 is prepared. Refines selected clips before rendering; Turbo still scans the full recording.":"Prepare large-v3 to enable final caption refinement. See docs/setup.md.";
  $("provider").onchange=()=>{const codex=$("provider").value==="codex",local=$("provider").value==="local";$("budget").disabled=codex;$("budget-field").hidden=codex||local;$("local-settings").hidden=!local;$("codex-note").hidden=!codex;$("reasoning-settings").hidden=!codex&&$("provider").value!=="openai";};$("provider").onchange();
  await layouts();restoreLayout();await refresh();
}
$("job-concurrency").onchange=async()=>{const select=$("job-concurrency");select.disabled=true;try{const result=await api("/api/concurrency",{method:"POST",body:JSON.stringify({max_concurrent_jobs:Number(select.value)})});concurrencyLimit=result.max_concurrent_jobs;await refresh();}catch(e){select.value=String(concurrencyLimit);error(e.message);}finally{select.disabled=false;}};
$("run-form").onsubmit=async event=>{event.preventDefault();if(submittingRun)return;submittingRun=true;error("");$("run-button").disabled=true;try{
  const body={video:$("video").value,asr:"turbo",provider:$("provider").value,local_model:$("local-model").value,context_size:Number($("context").value),budget_usd:$("provider").value==="codex"?0:Number($("budget").value),layout_id:$("layout").value,stream_id:$("stream-id").value?Number($("stream-id").value):null,stream_offset_seconds:$("stream-offset").value?Number($("stream-offset").value):null,alignment_confirmed:$("alignment").checked,discovery_reasoning:$("discovery-reasoning").value||"low",verification_reasoning:$("verification-reasoning").value||"low",video_encoder:$("video-encoder").value||"h264_nvenc",trim_silence:$("trim-silence").checked,final_transcription:$("final-transcription").checked};
  const result=await api("/api/runs",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify(body)});activeRun=result.id;await refresh();goView("results");
}catch(e){error(e.message);}finally{submittingRun=false;$("run-button").disabled=false;}};
function paintRuns(){
  const signature=JSON.stringify([activeRun,runList.map(run=>[run.id,run.state,run.config.video]),library?.videos.map(video=>[video.id,video.title])]);
  if(signature===runListSignature)return;runListSignature=signature;
  $("runs").replaceChildren();$("runs-count").textContent=runList.length;
  if(!runList.length)$("runs").append(text("p","Your runs will appear here.","muted"));
  for(const run of runList){
    const video=library?.videos.find(v=>v.id===run.config.video||v.url===run.config.video);
    const row=action("",async()=>{activeRun=run.id;paintRuns();await detail();});
    row.className="run-row"+(run.id===activeRun?" selected":"");row.setAttribute("aria-pressed",String(run.id===activeRun));
    row.append(text("strong",video?.title||run.config.video),text("span",run.state.charAt(0).toUpperCase()+run.state.slice(1)));
    $("runs").append(row);
  }
}
async function refresh(){const runs=await api("/api/runs");const active=runs.find(r=>["queued","running"].includes(r.state));$("run-button").disabled=submittingRun;$("run-availability").hidden=!active;
  if(!activeRun&&runs.length)activeRun=(active||runs[0]).id;
  const changed=JSON.stringify(runList)!==JSON.stringify(runs);runList=runs;
  await loadLibrary();if(changed||!$("runs").children.length)paintRuns();await detail();
  if(currentView==="gallery"&&typeof loadGallery==="function")await loadGallery();
  if(active&&!polling){polling=true;setTimeout(async()=>{polling=false;try{await refresh();}catch(e){error(e.message);}},2000);}
}
async function loadLibrary(){const next=await api("/api/videos");if(JSON.stringify(next)!==JSON.stringify(library)){library=next;paintLibrary();if(currentView==="process")matchSelectedStream();}}
function paintLibrary(){
  if(!library)return;
  $("library-count").textContent=library.videos.length;
  $("fetch-videos").disabled=fetchingVideos||!library.configured;
  $("older-videos").disabled=fetchingVideos||!library.configured;
  $("older-videos").hidden=!library.has_older;
  const heading=library.channel_title||library.channel_id;
  $("channel-status").textContent=fetchingVideos?"Fetching channel videos…":!library.configured?`${heading} · Set YOUTUBE_API_KEY in .env and restart to fetch videos.`:`${heading} · ${library.videos.length} saved videos${library.fetched_at?` · Last fetched ${new Date(library.fetched_at*1000).toLocaleString()}`:" · Fetch the latest videos to begin."}`;
  const query=$("video-search").value.trim().toLocaleLowerCase(), filter=$("video-filter").value;
  const items=library.videos.filter(v=>(filter==="all"||(filter==="processed"?v.processed:!v.processed))&&`${v.title} ${v.id}`.toLocaleLowerCase().includes(query));
  const box=$("channel-videos");box.replaceChildren();
  videoPage=Math.min(videoPage,Math.max(0,Math.ceil(items.length/videoPageSize)-1));
  if(!items.length){const empty=text("div","","empty-state");empty.append(text("h3",library.videos.length?"No saved videos match these filters.":"No channel videos saved yet."),text("p",library.videos.length?"Try another title or choose All videos.":"Fetch your channel videos, or start a new run with a video URL."));box.append(empty);}
  for(const video of items.slice(videoPage*videoPageSize,(videoPage+1)*videoPageSize)){
    const row=text("article","","channel-video");
    const img=document.createElement("img");img.src=`https://i.ytimg.com/vi/${video.id}/mqdefault.jpg`;img.alt="";img.loading="lazy";img.width=160;img.height=90;row.append(img);
    const info=text("div","","channel-video-info");const title=text("h3",video.title);title.title=video.title;info.append(title);
    const seconds=video.duration;const duration=seconds?`${Math.floor(seconds/3600)}:${String(Math.floor(seconds/60)%60).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`:"Duration unavailable";
    info.append(text("p",`${video.published?new Date(video.published).toLocaleDateString():"Date unavailable"} · ${duration}`,"muted"));
    const states={not_started:"Not started",queued:"Queued",running:"Processing",paused:"Paused",failed:"Failed",cancelled:"Cancelled",completed:video.outcome==="no_candidates"?"No suitable clips":video.outcome==="needs_attention"?"Clips need attention":"Completed"};
    const status=text("p",`${video.processed?"Processed · ":""}${states[video.state]||video.state}${video.available?"":" · Recording unavailable"}`,"video-status"+(video.outcome==="needs_attention"||video.state==="failed"?" attention":video.processed?" complete":["running","queued"].includes(video.state)?" active":""));
    const buttons=text("div","","video-actions");const select=action(video.processed?"Select again":"Select video",()=>{$("video").value=video.url;paintSelectedVideo();goView("process");$("video").focus();});select.disabled=!video.available;buttons.append(select);
    if(video.run_id)buttons.append(action("View run",async()=>{activeRun=video.run_id;paintRuns();await detail();goView("results");}));
    if(video.completed_run_id&&video.completed_run_id!==video.run_id)buttons.append(action("Previous results",async()=>{activeRun=video.completed_run_id;paintRuns();await detail();goView("results");}));
    const link=document.createElement("a");link.href=video.url;link.target="_blank";link.rel="noopener";link.textContent="↗";link.title="Open on YouTube";link.setAttribute("aria-label",`Open ${video.title} on YouTube`);buttons.append(link);row.append(info,status,buttons);box.append(row);
  }
  $("show-videos").hidden=(videoPage+1)*videoPageSize>=items.length;
  $("previous-videos").hidden=videoPage===0;
  $("video-page").textContent=items.length?`${videoPage*videoPageSize+1}–${Math.min((videoPage+1)*videoPageSize,items.length)} of ${items.length} videos`:"0 videos";
}
function paintSelectedVideo(){const video=library?.videos.find(v=>v.url===$("video").value||v.id===$("video").value);const box=$("selected-video");box.replaceChildren();box.hidden=!video;if(video){const img=document.createElement("img");img.src=`https://i.ytimg.com/vi/${video.id}/mqdefault.jpg`;img.alt="";box.append(img,text("p",video.title));}
  if(matchingVideo!==$("video").value){matchingVideo=$("video").value;resetStreamMatch();$("stream-query").value=video?.title||"";}
  if(video&&!$("stream-query").value)$("stream-query").value=video.title;
}
function matchSelectedStream(){
  paintSelectedVideo();
  if(library&&$("video").value.trim()&&!$("stream-id").value&&!$("find-stream").disabled)return findStream();
}
$("video").oninput=()=>{paintSelectedVideo();if(library?.videos.some(v=>v.url===$("video").value||v.id===$("video").value))matchSelectedStream();};
$("video").onchange=matchSelectedStream;
function resetStreamMatch(){streamLookup++;$("stream-id").value="";$("stream-offset").value="";$("alignment").checked=false;$("stream-matches").replaceChildren();$("stream-match-status").textContent="";$("find-stream").disabled=false;$("check-stream").disabled=false;}
function chooseStream(match){
  $("stream-id").value=String(match.id);$("stream-offset").value=match.streamOffsetSeconds==null?"":String(match.streamOffsetSeconds);$("alignment").checked=false;
  $("stream-match-status").textContent=`Stream ${match.id} selected. ${match.streamOffsetSeconds==null?"Enter the timing offset and confirm it to enable chat peaks.":"Saved offset filled. Confirm the timing to enable chat peaks."}`;
}
async function findStream(){
  const request=++streamLookup;$("find-stream").disabled=true;$("check-stream").disabled=false;$("stream-match-status").textContent="Looking for a matching stream…";$("stream-matches").replaceChildren();
  try{
    const result=await api(`/api/streams/search?video=${encodeURIComponent($("video").value)}&q=${encodeURIComponent($("stream-query").value)}`);
    if(request!==streamLookup)return;
    if(result.status==="unavailable"){$("stream-match-status").textContent="Stream search is unavailable. You can enter a stream ID or continue without chat peaks.";return;}
    const suggested=result.matches.find(match=>match.id===result.suggestedStreamId);
    $("stream-match-status").textContent=result.matches.length?"Choose a stream below, or enter its ID.":"No matching stream found. Try another title or enter the stream ID.";
    if(suggested&&!$("stream-id").value)chooseStream(suggested);
    if(result.warning)$("stream-matches").append(text("p",result.warning,"muted"));
    for(const match of result.matches){
      const row=text("div","","form-section");row.append(text("p",`Stream ${match.id} · ${new Date(match.startTime).toLocaleString()} · ${match.reason}`),text("p",match.titles.join(" / ")));
      row.append(action("Use this stream",()=>chooseStream(match)));$("stream-matches").append(row);
    }
  }catch(e){if(request===streamLookup)$("stream-match-status").textContent=e.message;}
  finally{if(request===streamLookup)$("find-stream").disabled=false;}
}
$("find-stream").onclick=findStream;
$("stream-query").oninput=resetStreamMatch;
$("stream-id").oninput=()=>{streamLookup++;$("stream-offset").value="";$("alignment").checked=false;$("stream-matches").replaceChildren();$("stream-match-status").textContent="Check this stream ID, then enter and confirm its timing.";$("find-stream").disabled=false;$("check-stream").disabled=false;};
$("stream-offset").oninput=()=>{streamLookup++;$("alignment").checked=false;$("find-stream").disabled=false;$("check-stream").disabled=false;};
$("check-stream").onclick=async()=>{
  const id=Number($("stream-id").value);if(!Number.isInteger(id)||id<=0||id>2147483647){$("stream-match-status").textContent="Enter a valid vaarattu.tv stream ID.";return;}
  const request=++streamLookup;$("check-stream").disabled=true;$("find-stream").disabled=false;$("stream-match-status").textContent="Checking stream…";
  try{
    const stream=await api(`/api/streams/${id}`);if(request!==streamLookup)return;
    const input=$("video").value.trim();
    const recording=stream.youtubeVideos?.find(v=>input===v.id||input===`https://www.youtube.com/watch?v=${v.id}`||input===`https://youtu.be/${v.id}`);
    if(!$("stream-offset").value&&recording)$("stream-offset").value=String(recording.streamOffsetSeconds);
    $("alignment").checked=false;$("stream-match-status").textContent=`Stream ${stream.id} · ${new Date(stream.startTime).toLocaleString()} · ${stream.segments.map(s=>s.title).join(" / ")}. Confirm the timing to enable chat peaks.`;
  }catch(e){if(request===streamLookup)$("stream-match-status").textContent=e.message;}
  finally{if(request===streamLookup)$("check-stream").disabled=false;}
};
async function fetchVideos(action){if(fetchingVideos)return;fetchingVideos=true;error("");paintLibrary();try{library=await api(`/api/videos/${action}`,{method:"POST"});}catch(e){error(e.message);}finally{fetchingVideos=false;paintLibrary();}}
$("fetch-videos").onclick=()=>fetchVideos("refresh");
$("older-videos").onclick=()=>fetchVideos("older");
$("show-videos").onclick=()=>{videoPage++;paintLibrary();$("channel-videos").scrollTop=0;};
$("previous-videos").onclick=()=>{videoPage--;paintLibrary();$("channel-videos").scrollTop=0;};
for(const id of ["video-search","video-filter"])$(id).oninput=()=>{videoPage=0;paintLibrary();};
async function detail(){
  if(!activeRun){$("run-detail").replaceChildren(text("h3","Your first run starts here"),text("p","Choose a recording from the library, then start a new run. Its progress and clips will appear here.","muted"));$("clips").replaceChildren();$("clip-count").textContent="0 clips";return;}
  const run=await api(`/api/runs/${activeRun}`);
  if(run.id!==activeRun)return;
  const signature=JSON.stringify(run);if(signature===detailSignature)return;detailSignature=signature;
  const box=$("run-detail"),usageOpen=$("run-usage")?.open;
  const stages={metadata:"Reading recording details",audio:"Preparing audio",transcript:"Transcribing speech","final-transcript":"Refining clip captions",selection:"Finding moments","review-priority":"Ranking review candidates","context-repair":"Finding missing context",chat:"Reviewing chat peaks",render:"Rendering clips"};
  const states={completed:"Run completed",queued:"Waiting to start",paused:"Run paused",failed:"Run needs attention",cancelled:"Run cancelled"};
  box.replaceChildren(text("h2",states[run.state]||(run.stage.startsWith("recovery-")?"Rechecking moments":stages[run.stage]||"Processing recording")));
  $("run-announcement").textContent=`${run.state} · ${Math.round(run.progress*100)}%. ${run.message||""}`;
  const progress=document.createElement("progress");progress.max=1;progress.value=run.progress;progress.setAttribute("aria-label","Run progress");box.append(progress,text("p",run.message||"Completed stages are saved automatically."));
  const usageDetails=text("details","","usage-details disclosure");usageDetails.id="run-usage";usageDetails.open=Boolean(usageOpen);
  usageDetails.append(text("summary","Processing details & usage"));
  usageDetails.append(text("p",`Turbo · CUDA FP16 · ${run.config.asr_batch_size?`batch size ${run.config.asr_batch_size}`:"unbatched"}${run.config.asr_flash_attention?" · Flash Attention requested":""}`,"muted"));
  if(run.config.final_transcription)usageDetails.append(text("p","Final clip captions: large-v3 · selected sections only.","muted"));
  const usage=run.usage, count=n=>n.toLocaleString();
  if(run.config.provider==="codex")usageDetails.append(text("p",`Codex · ${run.config.codex?.model||"gpt-5.6-luna"} · subscription usage · monetary cost unavailable.`,"muted"));
  else usageDetails.append(text("p",`Estimated API cost: $${usage.estimated_cost_usd.toFixed(4)} · pending / uncertain: $${usage.reserved_usd.toFixed(4)} · limit: $${usage.budget_usd.toFixed(2)}`,"muted"));
  usageDetails.append(text("p",`${count(usage.request_count)} model requests · ${count(usage.input_tokens)} input tokens · ${count(usage.output_tokens)} output tokens (includes reasoning)`));
  const reported=field=>usage.reported_counts[field]?`${count(usage[field])} (${usage.reported_counts[field]}/${usage.request_count} requests reported)` :"not reported";
  usageDetails.append(text("p",`Cached input: ${reported("cached_input_tokens")} · reasoning: ${reported("reasoning_tokens")}. These are included in the input/output totals.`,"muted"));
  if(usage.unreported_requests)usageDetails.append(text("p",`${usage.unreported_requests} requests have incomplete usage. Token totals include only reported counts.`,"muted"));
  usageDetails.append(text("p",run.config.provider==="codex"?"Token counts are reported by the bridge. Codex plan limits and any credit charges apply separately; no dollar spending cap is enforced here.":"Costs use conservative rates; provider discounts and taxes can change the invoice. Local inference has no API charge.","muted"));
  const usageLink=document.createElement("a");usageLink.href=`/api/runs/${run.id}/usage`;usageLink.download=`usage-${run.id}.json`;usageLink.textContent="Download usage report";usageDetails.append(usageLink);
  box.append(usageDetails);
  const controls=text("div","","actions");for(const [label,op,states] of [["Pause","pause",["running","queued"]],["Resume / retry","resume",["paused","failed","cancelled"]],["Cancel","cancel",["running","queued","paused"]]])if(states.includes(run.state))controls.append(action(label,async()=>{await api(`/api/runs/${run.id}/${op}`,{method:"POST"});await refresh();}));box.append(controls);
  if(run.can_recheck){
    const recovery=text("details","","disclosure");recovery.append(text("summary","Recheck excluded moments"));
    recovery.append(text("p","Reuses saved suggestions and transcription, keeps existing clips, and makes new model requests only to recheck exclusions. Your current run's provider and spending limit apply.","muted"));
    recovery.append(action("Recheck excluded moments",async()=>{await api(`/api/runs/${run.id}/recheck`,{method:"POST"});await refresh();}));box.append(recovery);
  }
  const decisions=run.result.verified||[], feedback=run.result.section_feedback||[];
  const priority=run.result.review_summary;
  if(priority){box.append(text("p",`${priority.selected} of ${priority.candidates} candidates in the top picks · Up to ${priority.limit} per run${priority.audit_all?" · All suggestions included for your audit":""}`));if(priority.warning)box.append(text("p",priority.warning,"muted"));}
  if(decisions.length||feedback.length){
    const audit=text("details","","disclosure");audit.append(text("summary","Selection feedback and decisions"));
    const stamp=us=>{const n=Math.floor(us/1e6);return `${Math.floor(n/3600)}:${String(Math.floor(n/60)%60).padStart(2,"0")}:${String(n%60).padStart(2,"0")}`;};
    for(const item of feedback)audit.append(text("p",`${stamp(item.start_us)}–${stamp(item.end_us)} · ${item.source==="chat_peak"?"Chat peak":"Transcript"} · ${item.candidates} suggestions — ${item.feedback}`));
    if(!feedback.length)audit.append(text("p","This older scan did not save section feedback. Rechecking exclusions does not rescan empty sections.","muted"));
    for(const item of decisions){
      const c=item.candidate;audit.append(text("h4",`${stamp(item.start_us)} · ${c.title_fi} · ${(item.review_selected??item.eligible)?"Selected":"Not selected"}${item.review_rank?` · Priority ${item.review_rank}`:""}`));
      audit.append(text("p",c.reason));
      audit.append(text("p",`Model: ${c.outcome} · Standalone ${c.scores.standalone}/4 · Substance ${c.scores.substance}/4 · Fidelity ${c.scores.fidelity}/4 · ${((item.end_us-item.start_us)/1e6).toFixed(1)} seconds`,"muted"));
      for(const reason of item.exclusion_reasons||[])audit.append(text("p",reason));
      for(const reason of item.review_exclusion_reasons||[])audit.append(text("p",reason));
      if(item.priority_reason)audit.append(text("p",item.priority_reason));
      const original=text("a","Watch original context");original.href=`https://www.youtube.com/watch?v=${encodeURIComponent(run.config.video)}&t=${Math.max(0,Math.floor(item.start_us/1e6)-10)}s`;original.target="_blank";original.rel="noopener";audit.append(original);
      for(const note of item.review_notes||[])audit.append(text("p",note));
      for(const flag of c.flags||[])audit.append(text("p",flag,"muted"));
    }
    box.append(audit);
  }
  if(run.state==="completed"&&!run.clips.length)box.append(text("p",run.result.coverage==="partial"?"Some speech sections could not be evaluated. No clips are ready.":"No suitable clips were found in this recording."));
  const peaks=run.result.chat_peak_review;
  if(peaks){const notes={timing_unconfirmed:"Chat peak review skipped: confirm the stream ID and VOD timing offset before starting a new run.",unavailable:"Chat peak review unavailable; the full transcript scan still ran.",stream_unavailable:"The selected stream was unavailable for chat peak review.",no_peaks:"Chat peak review: no distinct spikes found in the available data."};usageDetails.append(text("p",notes[peaks.status]||`Chat peak review: ${peaks.checked} sections checked, ${peaks.skipped} skipped. Peaks do not change the clip quality threshold.`,"muted"));}
  const nextClipsSignature=JSON.stringify([run.id,run.clips,run.clips.length?null:run.state]);
  if(nextClipsSignature===clipsSignature)return;clipsSignature=nextClipsSignature;
  if(displayedRun?.id!==run.id)clipPage=0;displayedRun=run;paintClips();
}
function paintClips(){
  const run=displayedRun;
  clipPage=Math.min(clipPage,Math.max(0,Math.ceil(run.clips.length/clipPageSize)-1));
  const grid=$("clips");grid.replaceChildren();$("clip-count").textContent=`${run.clips.length} ${run.clips.length===1?"clip":"clips"}`;
  if(!run.clips.length)grid.append(text("p",["queued","running","paused"].includes(run.state)?"Clips will appear here as processing finishes.":"No clips are ready for review from this run.","muted"));
  $("clip-page").textContent=run.clips.length?`${clipPage*clipPageSize+1}–${Math.min((clipPage+1)*clipPageSize,run.clips.length)} of ${run.clips.length} clips`:"";
  $("previous-clips").hidden=clipPage===0;$("next-clips").hidden=(clipPage+1)*clipPageSize>=run.clips.length;
  for(const clip of run.clips.slice(clipPage*clipPageSize,(clipPage+1)*clipPageSize)){
    const card=text("article","","clip"),body=text("div","","clip-body");
    if(clip.has_preview){const video=document.createElement("video");video.controls=true;video.preload="metadata";video.src=`/api/artifacts/${clip.id}/video?revision=${clip.revision}`;video.setAttribute("aria-label",clip.title);card.append(video);}
    else card.append(text("div","Preview unavailable","clip-no-preview"));
    body.append(text("h3",clip.title),text("p",`${clip.status==="held"?"Needs review":clip.status} · ${((clip.end_us-clip.start_us)/1e6).toFixed(1)} seconds`),text("p",`Original VOD ${(clip.start_us/1e6).toFixed(1)}–${(clip.end_us/1e6).toFixed(1)}s`));
    if(clip.review_selected!=null)body.append(text("p",clip.review_selected?`Top pick${clip.review_rank?` · Priority ${clip.review_rank}`:""}`:"Additional suggestion for review","muted"));
    if(clip.caption_warning||clip.audit_caption_warning)body.append(text("p",clip.caption_warning||clip.audit_caption_warning,"clip-flag"));
    for(const flag of clip.flags||[])body.append(text("p",flag,"clip-flag"));
    const buttons=text("div","","actions");buttons.append(action("Edit clip",()=>openEditor(clip.id),false));
    if(["held","ready"].includes(clip.status))buttons.append(action(clip.status==="held"?"Retry render":"Render new revision",async()=>{await api(`/api/clips/${clip.id}/retry`,{method:"POST",body:JSON.stringify({expected_revision:clip.revision})});await refresh();}));
    const link=document.createElement("a");link.href=`${clip.source_url}&t=${Math.floor(clip.start_us/1e6)}`;link.target="_blank";link.rel="noopener";link.textContent="Original VOD ↗";buttons.append(link);body.append(buttons);
    if(clip.status==="ready"&&clip.has_preview){const download=document.createElement("a");download.href=`/api/artifacts/${clip.id}/video?revision=${clip.revision}`;download.download="short.mp4";download.textContent="Download vertical video ↓";body.append(download);}
    card.append(body);grid.append(card);
  }
}
$("previous-clips").onclick=()=>{clipPage--;paintClips();$("clip-review-heading").scrollIntoView({behavior:"auto",block:"start"});};
$("next-clips").onclick=()=>{clipPage++;paintClips();$("clip-review-heading").scrollIntoView({behavior:"auto",block:"start"});};

$("open-output").onclick=()=>api("/api/output/open",{method:"POST"}).catch(e=>error(e.message));
function sameLayout(a,b){
  const defaults={camera_height:608,camera_fit:"cover",gameplay_fit:"cover",camera_ratio:"panel",gameplay_ratio:"panel"};
  const normalize=value=>{const v={...defaults,...value};return Object.keys(v).sort().map(key=>[key,key==="camera"||key==="gameplay"?[v[key].x,v[key].y,v[key].width,v[key].height]:v[key]]);};
  return JSON.stringify(normalize(a))===JSON.stringify(normalize(b));
}
async function openEditor(id){$("editor-back").href="#results";$("editor-back").textContent="← Back to clips";editing=await api(`/api/clips/${id}`);$("edit-duration-help").textContent=editing.context_expanded?"This context-expanded clip has no fixed duration cap. Keep at least 3 seconds within the recording.":"Choose a 3–90 second interval. The original recording keeps its full pacing.";const preset=savedLayouts.find(l=>sameLayout(l.body,editing.layout));if(preset)$("edit-layout").value=preset.id;else $("edit-layout").value="";$("use-source-frame").disabled=!editing.has_source;$("editor").hidden=false;$("editor-title").textContent=editing.title;$("edit-caption-coverage").hidden=!editing.caption_coverage;if(editing.caption_coverage)$("edit-caption-coverage").textContent=`Large-v3 captions: edits can use ${(editing.caption_coverage.start_us/1e6).toFixed(2)}–${(editing.caption_coverage.end_us/1e6).toFixed(2)} seconds of the original recording.`;$("edit-start").value=editing.start_us/1e6;$("edit-end").value=editing.end_us/1e6;$("edit-title").value=editing.title;$("edit-trim-silence").checked=Boolean(editing.trim_silence);$("edit-encoder").value=editing.video_encoder||"libx264";$("reviewed").checked=false;$("edit-words").value=editing.words.map(w=>`${w.id} | ${w.text}`).join("\n");$("source-player").hidden=!editing.has_source;if(editing.has_source){$("source-player").src=`/api/artifacts/${id}/source`;$("source-player").onloadedmetadata=()=>{$("source-player").currentTime=Math.max(0,(editing.start_us-editing.section_origin_us)/1e6-3);};}$("source-unavailable").hidden=editing.has_source;goView("editor");}
$("edit-form").onsubmit=async event=>{event.preventDefault();error("");$("save-edit").disabled=true;$("save-edit").textContent="Saving revision…";try{const words=$("edit-words").value.split("\n").filter(Boolean).map(line=>{const split=line.indexOf("|");const id=line.slice(0,split).trim();const original=editing.words.find(w=>w.id===id);if(split<0||!original)throw new Error("Keep each original caption word ID before the |.");return {...original,text:line.slice(split+1).trim()};});const result=await api(`/api/clips/${editing.id}/edit`,{method:"POST",body:JSON.stringify({expected_revision:editing.revision,start_us:Math.round(Number($("edit-start").value)*1e6),end_us:Math.round(Number($("edit-end").value)*1e6),title:$("edit-title").value,words,layout_id:$("edit-layout").value,reviewed:$("reviewed").checked,trim_silence:$("edit-trim-silence").checked,video_encoder:$("edit-encoder").value})});activeRun=result.run_id;$("editor").hidden=true;detailSignature="";await refresh();goView("results");$("notice").textContent="Revision saved. Follow its render in Runs & clips.";$("notice").hidden=false;}catch(e){error(e.message);}finally{$("save-edit").disabled=false;$("save-edit").textContent="Save and render revision";}};
let frame=null,frameSize=null,frameName="",frameVersion=0,frameLoading=false,drawMode="camera",gesture=null,redraw=false,rectangles={};
let composition={camera_height:608,camera_fit:"cover",gameplay_fit:"cover",camera_ratio:"panel",gameplay_ratio:"panel"};
const canvas=$("crop-canvas"),ctx=canvas.getContext("2d");
function settingKey(kind){return `${drawMode==="camera"?"camera":"gameplay"}_${kind}`;}
function syncRectangles(){
  for(const [key,id] of [["camera","camera-rect"],["game","game-rect"]]){
    const r=rectangles[key];$(id).value=r?[r.x,r.y,r.width,r.height].join(", "):"";$(id).setCustomValidity("");
  }
}
function syncComposition(layout={}){
  composition={camera_height:layout.camera_height??608,camera_fit:layout.camera_fit??"cover",gameplay_fit:layout.gameplay_fit??"cover",camera_ratio:layout.camera_ratio??"panel",gameplay_ratio:layout.gameplay_ratio??"panel"};
  $("camera-height").value=String(composition.camera_height);updatePanelLabel();setDrawMode(drawMode);
}
function updatePanelLabel(){
  const percent=Math.round(composition.camera_height/1920*100);
  $("camera-height-label").textContent=`${percent}% camera · ${100-percent}% gameplay`;
}
function paint(){
  ctx.clearRect(0,0,canvas.width,canvas.height);
  const preview=$("layout-preview"),out=preview.getContext("2d"),panels=layoutPanels(composition.camera_height);
  out.fillStyle="#000";out.fillRect(0,0,preview.width,preview.height);
  if(!frame)return;
  ctx.drawImage(frame,0,0,canvas.width,canvas.height);
  for(const [key,r] of Object.entries(rectangles)){
    ctx.strokeStyle=key==="camera"?"#b5ed82":"#8ad7ff";ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=key===drawMode?3:1.5;
    ctx.strokeRect(r.x*canvas.width,r.y*canvas.height,r.width*canvas.width,r.height*canvas.height);
    ctx.font="16px sans-serif";ctx.fillText(key==="camera"?"Camera":"Gameplay",r.x*canvas.width+8,Math.max(20,r.y*canvas.height-8));
    if(key===drawMode)for(const corner of cropCorners(r))ctx.fillRect(corner.x*canvas.width-5,corner.y*canvas.height-5,10,10);
    const fit=composition[key==="camera"?"camera_fit":"gameplay_fit"];
    const panel=panels[key],source=panelSource(r,frameSize.width,frameSize.height,key,composition.camera_height,fit);
    if(source.width<2||source.height<2)continue;
    const scale=preview.width/1080,panelY=key==="camera"?0:composition.camera_height*scale;
    let w=preview.width,h=panel.height*scale;
    if(fit==="contain"){const factor=Math.min(w/source.width,h/source.height);w=source.width*factor;h=source.height*factor;}
    out.drawImage(frame,source.x*frame.width/frameSize.width,source.y*frame.height/frameSize.height,source.width*frame.width/frameSize.width,source.height*frame.height/frameSize.height,(preview.width-w)/2,panelY+(panel.height*scale-h)/2,w,h);
  }
}
function setFrame(image,layout=null,sourceSize=null){
  frameVersion++;frameLoading=false;gesture=null;redraw=false;
  frame=image;frameSize=sourceSize?.width&&sourceSize?.height?sourceSize:{width:image.width,height:image.height};canvas.height=Math.round(canvas.width*image.height/image.width);
  $("frame-empty").hidden=true;$("preview-empty").hidden=true;
  for(const id of ["draw-camera","draw-game","redraw-crop"])$(id).disabled=false;
  rectangles={};if(layout?.camera)rectangles.camera={...layout.camera};if(layout?.gameplay)rectangles.game={...layout.gameplay};
  syncRectangles();$("calibrated").checked=false;paint();
}
function clearFrame(){
  frameVersion++;frameLoading=false;frame=null;frameSize=null;frameName="";gesture=null;
  $("frame-empty").hidden=false;$("preview-empty").hidden=false;
  $("frame-name").textContent="A screenshot copy is saved locally with the preset.";
  $("frame-file").value="";
  for(const id of ["draw-camera","draw-game","redraw-crop"])$(id).disabled=true;
  paint();
}
function loadFrame(url,name,revoke=false,sourceSize=null){
  const version=++frameVersion;frameLoading=true;
  const img=new Image();
  const release=()=>{if(revoke)URL.revokeObjectURL(url);};
  img.onload=()=>{
    release();if(version!==frameVersion)return;
    setFrame(img,{camera:rectangles.camera,gameplay:rectangles.game},sourceSize);frameName=name;$("frame-name").textContent=`${name} · saved with the preset`;
  };
  img.onerror=()=>{release();if(version!==frameVersion)return;frameLoading=false;error("This image could not be opened. Choose the screenshot again.");};
  img.src=url;
}
function chooseFrame(file){
  if(!file)return;
  if(!file.type.startsWith("image/")){error("Choose an image file for the screenshot.");return;}
  error("");
  loadFrame(URL.createObjectURL(file),file.name,true);
}
$("frame-file").onchange=event=>chooseFrame(event.target.files[0]);
$("frame-drop").ondragover=event=>{event.preventDefault();event.dataTransfer.dropEffect="copy";$("frame-drop").classList.add("drag-over");};
$("frame-drop").ondragleave=()=>$("frame-drop").classList.remove("drag-over");
$("frame-drop").ondrop=event=>{event.preventDefault();$("frame-drop").classList.remove("drag-over");chooseFrame(event.dataTransfer.files[0]);};
$("use-source-frame").onclick=()=>{
  const video=$("source-player");if(video.readyState<2){error("Wait for the source picture to load.");return;}
  video.pause();const image=document.createElement("canvas");image.width=video.videoWidth;image.height=video.videoHeight;image.getContext("2d").drawImage(video,0,0);
  syncComposition(editing.layout);setFrame(image,editing.layout);frameName=`Clip frame at ${video.currentTime.toFixed(1)}s`;
  $("frame-name").textContent=`${frameName} · saved with the preset`;
  $("layout-name").value="";$("solo-host").checked=editing.layout.solo_host;
  $("preset-select").value="";$("layout-saved").textContent="";
  updatePresetActions();goView("layouts");
};
function setDrawMode(mode){
  drawMode=mode;redraw=false;
  $("crop-mode").textContent=`${mode==="camera"?"Camera":"Gameplay"} · drag inside to move, drag a corner to resize. Draw outside to replace.`;
  for(const [id,key] of [["draw-camera","camera"],["draw-game","game"]]){$(id).className=key===mode?"":"secondary";$(id).setAttribute("aria-pressed",String(key===mode));}
  $("crop-ratio").value=composition[settingKey("ratio")];$("crop-fit").value=composition[settingKey("fit")];
}
$("draw-camera").onclick=()=>{setDrawMode("camera");paint();};
$("draw-game").onclick=()=>{setDrawMode("game");paint();};
$("redraw-crop").onclick=()=>{redraw=true;$("crop-mode").textContent="Drag to replace the selected crop. Press Escape to cancel.";canvas.focus();};
function updatePresetActions(){
  $("delete-layout").disabled=!$("preset-select").value;
  const exists=savedLayouts.some(l=>l.body.name.trim().toLowerCase()===$("layout-name").value.trim().toLowerCase());
  $("save-layout").textContent=exists?"Replace preset":"Save preset";
}
function rememberLayout(){
  try{localStorage.setItem("layout-preset",$("preset-select").value);}catch{ /* Saving the preset does not require browser storage. */ }
}
function restoreLayout(){
  let id;try{id=localStorage.getItem("layout-preset");}catch{return;}
  if(savedLayouts.some(l=>l.id===id)){$("preset-select").value=id;$("preset-select").onchange();}
}
$("layout-name").oninput=updatePresetActions;
$("preset-select").onchange=()=>{
  rememberLayout();
  const item=savedLayouts.find(l=>l.id===$("preset-select").value),preset=item?.body;
  clearFrame();syncComposition(preset);
  $("layout-name").value=preset?preset.name:"";$("solo-host").checked=Boolean(preset?.solo_host);$("calibrated").checked=false;$("layout-saved").textContent="";
  rectangles=preset?{camera:{...preset.camera},game:{...preset.gameplay}}:{};
  syncRectangles();updatePresetActions();paint();
  if(item?.screenshot_name)loadFrame(`/api/layouts/${item.id}/screenshot`,item.screenshot_name,false,{width:item.screenshot_width,height:item.screenshot_height});
};
$("delete-layout").onclick=async()=>{
  const id=$("preset-select").value,item=savedLayouts.find(l=>l.id===id);if(!item)return;
  if(!window.confirm(`Delete preset “${item.body.name}” and its saved screenshot? Existing runs and clips keep their layouts.`))return;
  $("delete-layout").disabled=true;
  try{await api(`/api/layouts/${id}`,{method:"DELETE"});await layouts();$("preset-select").value="";$("preset-select").onchange();$("layout-saved").textContent="Preset deleted. Existing runs and clips are unchanged.";}
  catch(e){error(e.message);}finally{updatePresetActions();}
};
function changed(){syncRectangles();$("calibrated").checked=false;$("layout-saved").textContent="";paint();}
$("camera-height").oninput=()=>{composition.camera_height=Number($("camera-height").value);updatePanelLabel();$("calibrated").checked=false;$("layout-saved").textContent="";paint();};
$("crop-fit").onchange=()=>{composition[settingKey("fit")]=$("crop-fit").value;changed();};
$("crop-ratio").onchange=()=>{
  const previous=composition[settingKey("ratio")];
  composition[settingKey("ratio")]=$("crop-ratio").value;
  const r=rectangles[drawMode];
  if(frame&&r&&$("crop-ratio").value!=="free"){
    const ratio=cropRatio(drawMode,frameSize.width,frameSize.height,$("crop-ratio").value,composition.camera_height);
    const width=Math.min(r.width,(1-r.y)*ratio),height=width/ratio;
    if(width*frameSize.width<32||height*frameSize.height<32){composition[settingKey("ratio")]=previous;$("crop-ratio").value=previous;error("This shape would make the crop too small. Draw a larger crop.");return;}
    rectangles[drawMode]={...r,width,height};
  }
  changed();
};
function point(event){const b=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(event.clientX-b.left-canvas.clientLeft)/canvas.clientWidth)),y:Math.max(0,Math.min(1,(event.clientY-b.top-canvas.clientTop)/canvas.clientHeight))};}
function hitCrop(p){
  const keys=[drawMode,drawMode==="camera"?"game":"camera"];
  for(const key of keys){
    const r=rectangles[key];if(!r)continue;
    const corner=cropCorners(r).findIndex(c=>Math.abs(c.x-p.x)*canvas.clientWidth<=10&&Math.abs(c.y-p.y)*canvas.clientHeight<=10);
    if(corner>=0)return {key,kind:"resize",anchor:cropCorners(r)[(corner+2)%4]};
    if(p.x>=r.x&&p.x<=r.x+r.width&&p.y>=r.y&&p.y<=r.y+r.height)return {key,kind:"move"};
  }
  return {key:drawMode,kind:"draw",anchor:p};
}
canvas.onpointerdown=event=>{
  if(!frame||frameLoading||event.button>0)return;
  event.preventDefault();canvas.focus();const p=point(event),hit=redraw?{key:drawMode,kind:"draw",anchor:p}:hitCrop(p);
  setDrawMode(hit.key);gesture={...hit,start:p,before:rectangles[hit.key]?{...rectangles[hit.key]}:null};
  canvas.setPointerCapture(event.pointerId);paint();
};
canvas.onpointermove=event=>{
  if(!frame)return;
  const p=point(event);
  if(!gesture){canvas.style.cursor=redraw?"crosshair":({move:"move",resize:"nwse-resize",draw:"crosshair"})[hitCrop(p).kind];return;}
  const g=gesture;
  const r=g.kind==="move"?moveRectangle(g.before,p.x-g.start.x,p.y-g.start.y):panelRectangle(g.anchor,p,g.key,frameSize.width,frameSize.height,composition[settingKey("ratio")],composition.camera_height);
  if(r.width*frameSize.width<32||r.height*frameSize.height<32)return;
  rectangles[g.key]=r;changed();
};
function cancelGesture(){
  if(gesture){if(gesture.before)rectangles[gesture.key]=gesture.before;else delete rectangles[gesture.key];gesture=null;changed();}
  setDrawMode(drawMode);paint();
}
canvas.onpointerup=()=>{gesture=null;};
canvas.onpointercancel=cancelGesture;
canvas.onlostpointercapture=()=>{gesture=null;};
canvas.onkeydown=event=>{
  if(event.key==="Escape"){cancelGesture();return;}
  const direction={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]}[event.key];
  if(!direction||!frame||!rectangles[drawMode])return;
  event.preventDefault();const step=event.shiftKey?10:1;
  rectangles[drawMode]=moveRectangle(rectangles[drawMode],direction[0]*step/frameSize.width,direction[1]*step/frameSize.height);changed();
};
function readRectangle(id){
  const values=$(id).value.split(",").map(v=>v.trim()===""?NaN:Number(v));
  if(values.length!==4||values.some(n=>!Number.isFinite(n)))throw new Error("Enter four crop coordinates.");
  const [x,y,width,height]=values;
  if(x<0||y<0||width<=0||height<=0||x+width>1.000001||y+height>1.000001)throw new Error("Keep both crops inside the source frame.");
  if(frame&&(Math.floor(width*frameSize.width/2)*2<32||Math.floor(height*frameSize.height/2)*2<32))throw new Error("Each crop must be at least 32 pixels wide and tall. Enlarge the crop.");
  return {x,y,width,height};
}
for(const [id,key] of [["camera-rect","camera"],["game-rect","game"]])$(id).oninput=()=>{
  $("calibrated").checked=false;$("layout-saved").textContent="";
  try{rectangles[key]=readRectangle(id);$(id).setCustomValidity("");paint();}catch(e){$(id).setCustomValidity(e.message);}
};
function screenshotCopy(){
  if(!frame)return null;
  const copy=document.createElement("canvas"),scale=Math.min(1,1600/frame.width,1600/frame.height);
  copy.width=Math.round(frame.width*scale);copy.height=Math.round(frame.height*scale);
  copy.getContext("2d").drawImage(frame,0,0,copy.width,copy.height);
  let data=copy.toDataURL("image/jpeg",0.85);
  if(data.length>1800000)data=copy.toDataURL("image/jpeg",0.5);
  if(data.length>1800000)throw new Error("This screenshot is too large to save. Choose a smaller image.");
  return {name:(frameName||"Screenshot").slice(0,255),data,width:frameSize.width,height:frameSize.height};
}
$("layout-form").onsubmit=async event=>{
  event.preventDefault();const forEditor=editing&&$("layout-return").href.endsWith("#editor");error("");$("layout-saved").textContent="";
  if(frameLoading){error("Wait for the screenshot to finish loading before saving.");return;}
  $("save-layout").disabled=true;$("save-layout").textContent="Saving preset…";
  try{
    const result=await api("/api/layouts",{method:"POST",body:JSON.stringify({name:$("layout-name").value.trim(),camera:readRectangle("camera-rect"),gameplay:readRectangle("game-rect"),calibrated:$("calibrated").checked,solo_host:$("solo-host").checked,...composition,screenshot:screenshotCopy()})});
    await layouts();$("layout").value=result.id;if(forEditor)$("edit-layout").value=result.id;$("preset-select").value=result.id;rememberLayout();
    $("layout-saved").textContent="Preset saved and selected for your next run"+(forEditor?" and this clip.":".");error("");
  }catch(e){error(e.message);}finally{$("save-layout").disabled=false;updatePresetActions();}
};
init().catch(e=>error(e.message));
