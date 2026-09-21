"use strict";
let deliveryBusy=false,postingBusy=false,cleanupPreview=null,cleanupBusy=false,postingPage=0;
function deliveryDate(){const d=new Date();d.setDate(d.getDate()+1);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;}
function diskSize(bytes){return `${(bytes/1024**3).toFixed(2)} GB`;}
async function loadDelivery(){
  if(deliveryBusy)return;deliveryBusy=true;$("refresh-delivery").disabled=true;
  try{
    const data=await api("/api/delivery");
    $("cleanup-recordings").replaceChildren();$("cleanup-pending-recordings").replaceChildren();
    const available=data.recordings.filter(r=>r.resolved);
    $("cleanup-status").textContent=`${available.length} recordings ready for cleanup. Final videos, captions and publishing records are kept.`;
    for(const recording of data.recordings){
      const row=text("div","","form-section");
      row.append(text("strong",recording.title),text("p",`${recording.ready} ready for posting · ${recording.unresolved} still need a final decision${recording.state!=="completed"?" · Processing is not complete":""}${recording.media_cleaned?" · Source media cleaned":""}`));
      if(recording.resolved)row.append(action("Preview cleanup",()=>openCleanup(recording.id)));
      row.append(action("Open recording",async()=>{activeRun=recording.id;await detail();goView("results");}));
      $(recording.resolved?"cleanup-recordings":"cleanup-pending-recordings").append(row);
    }
    if(!available.length)$("cleanup-recordings").append(text("p","No recordings are ready for cleanup. Finish processing and mark each clip Not approved or Ready for posting in review."));
    $("cleanup-pending").hidden=available.length===data.recordings.length;
  }finally{deliveryBusy=false;$("refresh-delivery").disabled=false;}
}
async function loadPostingPlan(){
  if(postingBusy)return;postingBusy=true;$("refresh-posting-plan").disabled=true;$("create-posting-plan").disabled=true;
  try{
    const data=await api("/api/delivery");
    const selected=new Set([...$("delivery-recordings").querySelectorAll("input:checked")].map(i=>i.value));
    if(!$("posting-date").value)$("posting-date").value=deliveryDate();
    $("delivery-recordings").replaceChildren();$("posting-plan").replaceChildren();
    for(const recording of data.recordings){
      if(recording.resolved&&recording.ready){
        const label=text("label","","check"),input=document.createElement("input");input.type="checkbox";input.value=recording.id;input.checked=selected.has(recording.id);
        label.append(input,text("span",`${recording.title} · ${recording.ready} final clips`));$("delivery-recordings").append(label);
      }
    }
    if(!$("delivery-recordings").children.length)$("delivery-recordings").append(text("p","No fully reviewed recordings with final clips yet. Individual ready clips can still be scheduled above."));
    postingPage=Math.min(postingPage,Math.max(0,Math.ceil(data.plan.length/10)-1));
    $("posting-page").textContent=data.plan.length?`${postingPage*10+1}–${Math.min((postingPage+1)*10,data.plan.length)} of ${data.plan.length} planned clips`:"";
    $("posting-previous").hidden=postingPage===0;$("posting-next").hidden=(postingPage+1)*10>=data.plan.length;
    for(const post of data.plan.slice(postingPage*10,(postingPage+1)*10)){
      const row=text("article","","form-surface");
      row.append(text("h3",post.title),text("p",`${new Date(post.scheduled_at).toLocaleString(undefined,{timeZone:post.timezone})} · ${post.timezone} · Revision ${post.revision}`));
      if(!post.ready)row.append(text("p","This revision is no longer ready for posting. Check the clip before uploading.","clip-flag"));
      const video=document.createElement("video");video.controls=true;video.preload="none";video.src=`/api/artifacts/${post.clip_id}/video?revision=${post.revision}`;row.append(video);
      const download=text("a","Download final video");download.href=video.src;download.download=`${post.clip_id}-r${post.revision}.mp4`;row.append(download);
      for(const [platform,delivery] of Object.entries(post.deliveries)){
        const name={youtube:"YouTube",instagram:"Instagram",tiktok:"TikTok"}[platform];
        if(post.buffer_managed){
          row.append(text("p",`${name}: ${delivery.status} · Managed in Publishing`));
          if(delivery.url){const link=text("a",`Open ${name} post`);link.href=delivery.url;link.target="_blank";link.rel="noopener";row.append(link);}
          continue;
        }
        const form=document.createElement("form"),label=text("label",`${name}: ${delivery.status==="posted"?"Posted":"Not posted"}`),input=document.createElement("input");
        input.type="url";input.required=true;input.placeholder="Paste the published post link";input.value=delivery.url||"";label.append(input);form.append(label);
        const button=text("button",delivery.status==="posted"?"Update post link":"Record posted link");button.type="submit";button.disabled=!post.ready&&delivery.status!=="posted";form.append(button);
        form.onsubmit=async event=>{event.preventDefault();button.disabled=true;try{
          await api(`/api/delivery/posts/${post.id}/${platform}`,{method:"POST",body:JSON.stringify({url:input.value})});
          await loadPostingPlan();
        }catch(e){error(e.message);button.disabled=false;}};
        row.append(form);
        if(delivery.url){const link=text("a",`Open ${name} post`);link.href=delivery.url;link.target="_blank";link.rel="noopener";row.append(link);}
      }
      if(post.buffer_managed){const link=text("a","Manage publishing");link.href="#publishing";row.append(link);}
      if(!post.buffer_managed&&Object.values(post.deliveries).every(d=>d.status!=="posted"))row.append(action("Remove from plan",async()=>{
        await api(`/api/delivery/posts/${post.id}`,{method:"DELETE"});await loadPostingPlan();
      }));
      $("posting-plan").append(row);
    }
    if(!data.plan.length)$("posting-plan").append(text("p","No local plan entries. Use Fill scheduled queue above to schedule directly in Buffer."));
  }finally{postingBusy=false;$("refresh-posting-plan").disabled=false;$("create-posting-plan").disabled=false;}
}
async function createPostingPlan(){
  if(postingBusy)return;postingBusy=true;$("create-posting-plan").disabled=true;
  try{
    const run_ids=[...$("delivery-recordings").querySelectorAll("input:checked")].map(i=>i.value);
    const result=await api("/api/delivery/schedule",{method:"POST",body:JSON.stringify({run_ids,start_date:$("posting-date").value,local_time:$("posting-time").value,timezone:$("posting-zone").value})});
    $("posting-message").textContent=`Added ${result.added} daily entries. This plan does not upload automatically.`;
  }catch(e){$("posting-message").textContent=e.message;}
  finally{postingBusy=false;$("create-posting-plan").disabled=false;}
  await loadPostingPlan();
  if(typeof loadPublishing==="function")await loadPublishing();
}
async function openCleanup(runId){
  if(cleanupBusy)return;cleanupBusy=true;
  try{
    cleanupPreview=await api(`/api/delivery/cleanup/${runId}`);
    $("cleanup-summary").textContent=`${cleanupPreview.title}: delete ${cleanupPreview.file_count} files (${diskSize(cleanupPreview.bytes)}); keep ${cleanupPreview.kept_videos} final/scheduled videos.`;
    $("cleanup-files").textContent=cleanupPreview.files.map(f=>`${diskSize(f[1])} · ${f[0]}`).join("\n");
    $("cleanup-confirm").checked=false;$("cleanup-delete").disabled=true;$("cleanup-message").textContent="";$("cleanup-dialog").showModal();
  }catch(e){error(e.message);}finally{cleanupBusy=false;}
}
async function deleteCleanup(){
  if(cleanupBusy||!cleanupPreview||!$("cleanup-confirm").checked)return;
  cleanupBusy=true;$("cleanup-delete").disabled=true;$("cleanup-close").disabled=true;$("cleanup-confirm").disabled=true;
  try{
    const result=await api(`/api/delivery/cleanup/${cleanupPreview.run_id}`,{method:"POST",body:JSON.stringify({fingerprint:cleanupPreview.fingerprint})});
    $("delivery-message").textContent=`Freed ${diskSize(result.deleted_bytes)}.${result.remaining_files.length?` ${result.remaining_files.length} files could not be removed; preview cleanup again to retry.`:" Final videos and review records are preserved."}`;
    $("cleanup-dialog").close();await loadDelivery();
  }catch(e){$("cleanup-message").textContent=e.message;}
  finally{cleanupBusy=false;$("cleanup-close").disabled=false;$("cleanup-confirm").disabled=false;$("cleanup-delete").disabled=!$("cleanup-confirm").checked;}
}
$("refresh-delivery").onclick=()=>loadDelivery().catch(e=>error(e.message));
$("create-posting-plan").onclick=createPostingPlan;
$("refresh-posting-plan").onclick=()=>loadPostingPlan().catch(e=>error(e.message));
$("publishing-manual-plan").ontoggle=()=>{if($("publishing-manual-plan").open)return loadPostingPlan().catch(e=>error(e.message));};
$("posting-previous").onclick=()=>{if(!postingBusy){postingPage--;return loadPostingPlan().catch(e=>error(e.message));}};
$("posting-next").onclick=()=>{if(!postingBusy){postingPage++;return loadPostingPlan().catch(e=>error(e.message));}};
$("cleanup-confirm").onchange=()=>{$("cleanup-delete").disabled=cleanupBusy||!$("cleanup-confirm").checked;};
$("cleanup-delete").onclick=deleteCleanup;
$("cleanup-close").onclick=()=>{if(!cleanupBusy)$("cleanup-dialog").close();};
$("cleanup-dialog").oncancel=event=>{if(cleanupBusy)event.preventDefault();};
