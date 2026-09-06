"use strict";
const $ = id => document.getElementById(id);
let token = "", activeRun = null, editing = null, polling = false;
let library = null, videoLimit = 20, fetchingVideos = false;
const labels = {local:"Local · Gemma 4",gemini:"Gemini 3.8 Flash",openai:"GPT-5.6 Luna",codex:"Codex",zai:"GLM-5.3-Flash",deepseek:"DeepSeek V4 Flash",meta:"Meta Muse Spark 1.3"};
function error(message){$("error").textContent=message;$("error").hidden=!message;}
async function api(path, options={}){
  const response=await fetch(path,{...options,headers:{"Content-Type":"application/json","X-Local-Token":token,...options.headers}});
  const value=await response.json();
  if(!response.ok) throw new Error(typeof value.detail==="string"?value.detail:"Check the form fields and try again.");
  return value;
}
function action(label, fn, secondary=true){const button=document.createElement("button");button.type="button";button.textContent=label;if(secondary)button.className="secondary";button.onclick=()=>Promise.resolve(fn()).catch(e=>error(e.message));return button;}
function text(tag, value, className){const node=document.createElement(tag);node.textContent=value;if(className)node.className=className;return node;}
async function layouts(){const list=await api("/api/layouts");for(const id of ["layout","edit-layout"]){const selected=$(id).value;$(id).replaceChildren();for(const item of list){const o=document.createElement("option");o.value=item.id;o.textContent=item.body.name;$(id).append(o);}if(list.some(l=>l.id===selected))$(id).value=selected;}}
async function init(){const state=await api("/api/status");token=state.token;$("cache-path").textContent=`Model cache: ${state.model_cache}`;
  for(const [key,info] of Object.entries(state.providers)){const o=document.createElement("option");o.value=key;o.textContent=(key==="codex"?`Codex · ${info.model}`:labels[key])+(!info.available?" · unavailable":!info.configured?" · key missing":"");o.disabled=!info.available||!info.configured;$("provider").append(o);}
  $("setup-status").textContent=state.models.turbo?"Turbo is prepared.":"Prepare Turbo and your selected LLM before running. See docs/setup.md.";
  $("provider").onchange=()=>{const codex=$("provider").value==="codex";$("budget").disabled=codex;$("codex-note").hidden=!codex;};$("provider").onchange();
  await layouts();await refresh();
}
$("run-form").onsubmit=async event=>{event.preventDefault();error("");$("run-button").disabled=true;try{
  const body={video:$("video").value,asr:"turbo",provider:$("provider").value,local_model:$("local-model").value,context_size:Number($("context").value),budget_usd:$("provider").value==="codex"?0:Number($("budget").value),layout_id:$("layout").value,stream_id:$("stream-id").value?Number($("stream-id").value):null,stream_offset_seconds:$("stream-offset").value?Number($("stream-offset").value):null,alignment_confirmed:$("alignment").checked};
  const result=await api("/api/runs",{method:"POST",headers:{"Idempotency-Key":crypto.randomUUID()},body:JSON.stringify(body)});activeRun=result.id;await refresh();
}catch(e){error(e.message);$("run-button").disabled=false;}};
async function refresh(){const runs=await api("/api/runs");const active=runs.find(r=>["queued","running","paused"].includes(r.state));$("run-button").disabled=Boolean(active);$("runs").replaceChildren();
  for(const run of runs.slice(0,8)){const row=text("div","","run-row");row.append(text("span",`${run.config.video} · ${run.state}`),action("View",async()=>{activeRun=run.id;await detail();}));$("runs").append(row);}
  if(!activeRun&&runs.length)activeRun=(active||runs[0]).id;await detail();await loadLibrary();
  if(active&&!polling){polling=true;setTimeout(async()=>{polling=false;try{await refresh();}catch(e){error(e.message);}},2000);}
}
async function loadLibrary(){library=await api("/api/videos");paintLibrary();}
function paintLibrary(){
  if(!library)return;
  $("fetch-videos").disabled=fetchingVideos||!library.configured;
  $("older-videos").disabled=fetchingVideos||!library.configured;
  $("older-videos").hidden=!library.has_older;
  const heading=library.channel_title||library.channel_id;
  $("channel-status").textContent=fetchingVideos?"Fetching channel videos…":!library.configured?`${heading} · Set YOUTUBE_API_KEY in .env and restart to fetch videos.`:`${heading} · ${library.videos.length} saved videos${library.fetched_at?` · Last fetched ${new Date(library.fetched_at*1000).toLocaleString()}`:" · Fetch the latest videos to begin."}`;
  const query=$("video-search").value.trim().toLocaleLowerCase(), filter=$("video-filter").value;
  const items=library.videos.filter(v=>(filter==="all"||(filter==="processed"?v.processed:!v.processed))&&`${v.title} ${v.id}`.toLocaleLowerCase().includes(query));
  const box=$("channel-videos");box.replaceChildren();
  if(!items.length)box.append(text("p",library.videos.length?"No saved videos match these filters.":"No channel videos saved yet.","muted"));
  for(const video of items.slice(0,videoLimit)){
    const row=text("article","","channel-video");
    const img=document.createElement("img");img.src=`https://i.ytimg.com/vi/${video.id}/mqdefault.jpg`;img.alt="";img.loading="lazy";img.width=160;img.height=90;row.append(img);
    const info=text("div","","channel-video-info");info.append(text("h3",video.title));
    const seconds=video.duration;const duration=seconds?`${Math.floor(seconds/3600)}:${String(Math.floor(seconds/60)%60).padStart(2,"0")}:${String(seconds%60).padStart(2,"0")}`:"Duration unavailable";
    info.append(text("p",`${video.published?new Date(video.published).toLocaleDateString():"Date unavailable"} · ${duration}`,"muted"));
    const states={not_started:"Not started",queued:"Queued",running:"Processing",paused:"Paused",failed:"Failed",cancelled:"Cancelled",completed:video.outcome==="no_candidates"?"No suitable clips":video.outcome==="needs_attention"?"Clips need attention":"Completed"};
    info.append(text("p",`${video.processed?"Processed · ":""}${states[video.state]||video.state}${video.available?"":" · Recording unavailable"}`,"muted"));
    const buttons=text("div","","actions");const select=action(video.processed?"Select again":"Select video",()=>{$("video").value=video.url;$("run-form").scrollIntoView({behavior:"smooth"});$("video").focus();});select.disabled=!video.available;buttons.append(select);
    if(video.run_id)buttons.append(action("View run",async()=>{activeRun=video.run_id;await detail();$("run-detail").scrollIntoView({behavior:"smooth"});}));
    if(video.completed_run_id&&video.completed_run_id!==video.run_id)buttons.append(action("Previous results",async()=>{activeRun=video.completed_run_id;await detail();$("run-detail").scrollIntoView({behavior:"smooth"});}));
    const link=document.createElement("a");link.href=video.url;link.target="_blank";link.rel="noopener";link.textContent="YouTube";buttons.append(link);info.append(buttons);row.append(info);box.append(row);
  }
  $("show-videos").hidden=items.length<=videoLimit;
}
async function fetchVideos(action){if(fetchingVideos)return;fetchingVideos=true;error("");paintLibrary();try{library=await api(`/api/videos/${action}`,{method:"POST"});}catch(e){error(e.message);}finally{fetchingVideos=false;paintLibrary();}}
$("fetch-videos").onclick=()=>fetchVideos("refresh");
$("older-videos").onclick=()=>fetchVideos("older");
$("show-videos").onclick=()=>{videoLimit+=20;paintLibrary();};
for(const id of ["video-search","video-filter"])$(id).oninput=()=>{videoLimit=20;paintLibrary();};
async function detail(){if(!activeRun){$("run-detail").textContent="No videos processed yet.";return;}const run=await api(`/api/runs/${activeRun}`);const box=$("run-detail");box.replaceChildren(text("h3",`${run.stage} · ${run.state}`));
  const progress=document.createElement("progress");progress.max=1;progress.value=run.progress;box.append(progress,text("p",run.message||"Completed stages are saved automatically."));
  box.append(text("p",`Turbo · CUDA FP16 · ${run.config.asr_batch_size?`batch size ${run.config.asr_batch_size}`:"unbatched"}${run.config.asr_flash_attention?" · Flash Attention requested":""}`,"muted"));
  const usage=run.usage, count=n=>n.toLocaleString();
  if(run.config.provider==="codex")box.append(text("p",`Codex · ${run.config.codex?.model||"gpt-5.6-luna"} · subscription usage · monetary cost unavailable.`,"muted"));
  else box.append(text("p",`Estimated API cost: $${usage.estimated_cost_usd.toFixed(4)} · pending / uncertain: $${usage.reserved_usd.toFixed(4)} · limit: $${usage.budget_usd.toFixed(2)}`,"muted"));
  box.append(text("p",`${count(usage.request_count)} model requests · ${count(usage.input_tokens)} input tokens · ${count(usage.output_tokens)} output tokens (includes reasoning)`));
  const reported=field=>usage.reported_counts[field]?`${count(usage[field])} (${usage.reported_counts[field]}/${usage.request_count} requests reported)` :"not reported";
  box.append(text("p",`Cached input: ${reported("cached_input_tokens")} · reasoning: ${reported("reasoning_tokens")}. These are included in the input/output totals.`,"muted"));
  if(usage.unreported_requests)box.append(text("p",`${usage.unreported_requests} requests have incomplete usage. Token totals include only reported counts.`,"muted"));
  box.append(text("p",run.config.provider==="codex"?"Token counts are reported by the bridge. Codex plan limits and any credit charges apply separately; no dollar spending cap is enforced here.":"Costs use conservative rates; provider discounts and taxes can change the invoice. Local inference has no API charge.","muted"));
  const usageLink=document.createElement("a");usageLink.href=`/api/runs/${run.id}/usage`;usageLink.download=`usage-${run.id}.json`;usageLink.textContent="Download usage report";box.append(usageLink);
  const controls=text("div","","actions");for(const [label,op,states] of [["Pause","pause",["running","queued"]],["Resume / retry","resume",["paused","failed","cancelled"]],["Cancel","cancel",["running","queued","paused"]]])if(states.includes(run.state))controls.append(action(label,async()=>{await api(`/api/runs/${run.id}/${op}`,{method:"POST"});await refresh();}));box.append(controls);
  if(run.state==="completed"&&!run.clips.length)box.append(text("p",run.result.coverage==="partial"?"Some speech sections could not be evaluated. No clips are ready.":"No suitable clips were found in this recording."));
  const peaks=run.result.chat_peak_review;
  if(peaks){const notes={timing_unconfirmed:"Chat peak review skipped: confirm the stream ID and VOD timing offset before starting a new run.",unavailable:"Chat peak review unavailable; the full transcript scan still ran.",stream_unavailable:"The selected stream was unavailable for chat peak review.",no_peaks:"Chat peak review: no distinct spikes found in the available data."};box.append(text("p",notes[peaks.status]||`Chat peak review: ${peaks.checked} sections checked, ${peaks.skipped} skipped. Peaks do not change the clip quality threshold.`,"muted"));}
  const grid=$("clips");grid.replaceChildren();for(const clip of run.clips){const card=text("article","","clip");if(clip.has_preview){const video=document.createElement("video");video.controls=true;video.preload="metadata";video.src=`/api/artifacts/${clip.id}/video`;card.append(video);}card.append(text("h3",clip.title),text("p",`${clip.status} · ${((clip.end_us-clip.start_us)/1e6).toFixed(1)} seconds · VOD ${(clip.start_us/1e6).toFixed(1)}–${(clip.end_us/1e6).toFixed(1)}s`));for(const flag of clip.flags||[])card.append(text("p",flag));const link=document.createElement("a");link.href=`${clip.source_url}&t=${Math.floor(clip.start_us/1e6)}`;link.target="_blank";link.rel="noopener";link.textContent="Original VOD";card.append(link,action("Edit",()=>openEditor(clip.id)));if(clip.status==="held")card.append(action("Retry render",async()=>{await api(`/api/clips/${clip.id}/retry`,{method:"POST",body:JSON.stringify({expected_revision:clip.revision})});await refresh();}));if(clip.status==="ready"&&clip.has_preview){const download=document.createElement("a");download.href=`/api/artifacts/${clip.id}/video`;download.download="short.mp4";download.textContent="Download vertical video";card.append(download);}grid.append(card);}
}
$("open-output").onclick=()=>api("/api/output/open",{method:"POST"}).catch(e=>error(e.message));
async function openEditor(id){editing=await api(`/api/clips/${id}`);$("editor").hidden=false;$("editor-title").textContent=editing.title;$("edit-start").value=editing.start_us/1e6;$("edit-end").value=editing.end_us/1e6;$("edit-title").value=editing.title;$("reviewed").checked=false;$("edit-words").value=editing.words.map(w=>`${w.id} | ${w.text}`).join("\n");$("source-player").hidden=!editing.has_source;if(editing.has_source){$("source-player").src=`/api/artifacts/${id}/source`;$("source-player").onloadedmetadata=()=>{$("source-player").currentTime=Math.max(0,(editing.start_us-editing.section_origin_us)/1e6-3);};}$("editor").scrollIntoView({behavior:"smooth"});}
$("edit-form").onsubmit=async event=>{event.preventDefault();try{const words=$("edit-words").value.split("\n").filter(Boolean).map(line=>{const split=line.indexOf("|");const id=line.slice(0,split).trim();const original=editing.words.find(w=>w.id===id);if(split<0||!original)throw new Error("Keep each original caption word ID before the |.");return {...original,text:line.slice(split+1).trim()};});const result=await api(`/api/clips/${editing.id}/edit`,{method:"POST",body:JSON.stringify({expected_revision:editing.revision,start_us:Math.round(Number($("edit-start").value)*1e6),end_us:Math.round(Number($("edit-end").value)*1e6),title:$("edit-title").value,words,layout_id:$("edit-layout").value,reviewed:$("reviewed").checked})});activeRun=result.run_id;$("editor").hidden=true;await refresh();}catch(e){error(e.message);}};
let frame=null,drawMode="camera",origin=null,rectangles={};const canvas=$("crop-canvas"),ctx=canvas.getContext("2d");
function paint(){ctx.clearRect(0,0,canvas.width,canvas.height);if(frame)ctx.drawImage(frame,0,0,canvas.width,canvas.height);for(const [key,r] of Object.entries(rectangles)){ctx.strokeStyle=key==="camera"?"#b5ed82":"#8ad7ff";ctx.lineWidth=3;ctx.strokeRect(r.x*canvas.width,r.y*canvas.height,r.width*canvas.width,r.height*canvas.height);}}
$("frame-file").onchange=event=>{const file=event.target.files[0];if(!file)return;const url=URL.createObjectURL(file);const img=new Image();img.onload=()=>{frame=img;canvas.height=Math.round(canvas.width*img.height/img.width);paint();URL.revokeObjectURL(url);};img.src=url;};
$("draw-camera").onclick=()=>{drawMode="camera";};$("draw-game").onclick=()=>{drawMode="game";};
function point(event){const b=canvas.getBoundingClientRect();return{x:Math.max(0,Math.min(1,(event.clientX-b.left)/b.width)),y:Math.max(0,Math.min(1,(event.clientY-b.top)/b.height))};}
canvas.onpointerdown=event=>{origin=point(event);canvas.setPointerCapture(event.pointerId);};canvas.onpointermove=event=>{if(!origin)return;const p=point(event);const r={x:Math.min(origin.x,p.x),y:Math.min(origin.y,p.y),width:Math.abs(origin.x-p.x),height:Math.abs(origin.y-p.y)};rectangles[drawMode]=r;$(drawMode==="camera"?"camera-rect":"game-rect").value=[r.x,r.y,r.width,r.height].map(n=>n.toFixed(5)).join(", ");paint();};canvas.onpointerup=()=>{origin=null;};
$("layout-form").onsubmit=async event=>{event.preventDefault();try{const rect=id=>{const values=$(id).value.split(",").map(Number);if(values.length!==4||values.some(n=>!Number.isFinite(n)))throw new Error("Enter four crop coordinates.");const[x,y,width,height]=values;return{x,y,width,height};};const result=await api("/api/layouts",{method:"POST",body:JSON.stringify({name:$("layout-name").value,camera:rect("camera-rect"),gameplay:rect("game-rect"),calibrated:$("calibrated").checked,solo_host:$("solo-host").checked})});await layouts();$("layout").value=result.id;error("");}catch(e){error(e.message);}};
init().catch(e=>error(e.message));
