"use strict";
let publishingBusy=false,publishingData=null,publishingPage=0,publishingPreview=null,publishingTimeClip=null,publishingResolution=null;
const publishingSelected=new Set(),publishingEdits=new Map(),publishingFields=new Map();
const publishingNames={youtube:"YouTube",instagram:"Instagram",tiktok:"TikTok"};
const publishingStatuses={pending:"Not submitted",failed:"Submission failed",unknown:"Check uncertain submission",submitting:"Check interrupted submission",scheduled:"Scheduled",sending:"Publishing",posted:"Published",error:"Failed in Buffer",draft:"Draft in Buffer",needs_approval:"Approval needed in Buffer",attention:"Attention needed in Buffer"};
function publishingLink(label,url){const a=text("a",label);a.href=url.startsWith("https://")?url:"https://publish.buffer.com/";a.target="_blank";a.rel="noopener";return a;}
function publishingVideo(id,revision){const v=document.createElement("video");v.controls=true;v.preload="none";v.src=`/api/artifacts/${encodeURIComponent(id)}/video?revision=${revision}`;return v;}
function publishingWhen(item){return item.due_at?`${new Date(item.due_at).toLocaleString(undefined,{timeZone:item.timezone})} · ${item.timezone}`:"Publish now";}
function publishingRemember(){for(const [id,fields] of publishingFields)publishingEdits.set(id,{...publishingEdits.get(id),revision:publishingData.clips.find(c=>c.id===id)?.revision,copy_id:publishingEdits.get(id)?.copy_id??publishingData.clips.find(c=>c.id===id)?.copy?.id??null,...Object.fromEntries(Object.entries(fields).map(([k,e])=>[k,e.type==="checkbox"?e.checked:e.value]))});}
function publishingItem(clip){publishingRemember();return {clip_id:clip.id,revision:clip.revision,title:clip.copy?.title||"",caption:clip.copy?.caption||"",note:clip.copy?.note||"",copy_id:clip.copy?.id||null,category:"20",made_for_kids:false,...publishingEdits.get(clip.id)};}
function publishingOptions(select,options,value){select.replaceChildren();for(const [id,label] of options){const o=text("option",label);o.value=id;select.append(o);}select.value=value||"";}
async function publishingSaveCopy(clip){
  const value=publishingItem(clip);
  const body={revision:clip.revision,title:value.title,caption:value.caption,note:value.note,expected_id:value.copy_id};
  if(!value.title.trim()&&!value.caption.trim()){
    clip.copy=await api(`/api/publishing/copy/${clip.id}/generate`,{method:"POST",body:JSON.stringify({revision:clip.revision,note:value.note,expected_id:value.copy_id})});
  }else if(!clip.copy||["title","caption","note"].some(k=>value[k]!==clip.copy[k])){
    clip.copy=await api(`/api/publishing/copy/${clip.id}`,{method:"POST",body:JSON.stringify(body)});
  }
  if(clip.copy){
    const values={...value,copy_id:clip.copy.id,...Object.fromEntries(["title","caption","note"].map(k=>[k,clip.copy[k]]))};
    publishingEdits.set(clip.id,values);
    for(const [key,field] of Object.entries(publishingFields.get(clip.id)||{}))if(key in values&&field.type!=="checkbox")field.value=values[key];
  }
}
async function publishingWork(fn){
  if(publishingBusy)return;publishingBusy=true;
  try{await fn();}catch(e){$("publishing-message").textContent=e.message;}
  finally{publishingBusy=false;try{await loadPublishing();}catch(e){$("publishing-message").textContent=e.message;}}
}
function publishingSelection(){ $("publishing-selection").textContent=`${publishingSelected.size} selected · up to 10 at a time`;$("publishing-preview-plan").disabled=!publishingSelected.size; }
async function loadPublishing(){
  if(publishingBusy)return;
  publishingRemember();
  const [storage,data]=await Promise.all([api("/api/publishing/storage"),api("/api/publishing/buffer")]);publishingData=data;
  $("publishing-storage-status").textContent=`${storage.connected?"Connected":"Not connected"} · ${storage.bucket} · App storage limit: ${(storage.storage_limit/1e9).toFixed(0)} GB`;
  $("publishing-storage-setup").open=!storage.connected;$("publishing-buffer-setup").open=!data.connected;
  $("publishing-buffer-status").textContent=data.connected?"Connected. Select the channels you want to publish to.":"Not connected";
  publishingOptions($("publishing-organization"),data.organizations.map(o=>[o.id,o.name]),data.organization_id);
  for(const p of Object.keys(publishingNames))publishingOptions($("publishing-"+p),[["","Do not publish here"],...data.channels.filter(c=>c.service===p).map(c=>[c.id,`${c.displayName||c.name}${c.isDisconnected?" · Reconnect in Buffer":""}${c.isLocked?" · Locked":""}${c.isQueuePaused?" · Paused":""}`])],data.mapping[p]);
  $("publishing-save-channels").disabled=!data.connected;$("publishing-refresh-buffer").disabled=!data.connected;
  $("publishing-fill").disabled=!storage.connected||!data.connected||!Object.keys(data.mapping).length;
  $("publishing-capacity").textContent=data.remaining?`${Object.entries(data.remaining).map(([p,n])=>`${publishingNames[p]}: ${n}/10 queue slots available`).join(" · ")}. Last checked ${new Date(data.refreshed_at).toLocaleString()}.`:"Buffer Free: three channels, ten queued posts per channel. Refresh Buffer status to check availability; it is checked again before submission.";
  for(const id of publishingSelected)if(!data.clips.some(c=>c.id===id&&c.ready&&c.plan&&!c.publication))publishingSelected.delete(id);
  for(const [id,edits] of publishingEdits){
    const clip=data.clips.find(c=>c.id===id);
    if(!clip||clip.revision!==edits.revision||(clip.copy?.id||null)!==edits.copy_id)publishingEdits.delete(id);
  }
  publishingPage=Math.min(publishingPage,Math.max(0,Math.ceil(data.clips.length/10)-1));publishingFields.clear();$("publishing-videos").replaceChildren();
  for(const clip of data.clips.slice(publishingPage*10,(publishingPage+1)*10)){
    const row=text("article","","form-section"),job=clip.publication;
    row.append(text("h3",job?job.item.title:clip.title),text("p",`${clip.source_title} · Revision ${job?job.revision:clip.revision}`),publishingVideo(clip.id,job?job.revision:clip.revision));
    if(job){
      row.append(text("p",publishingWhen(job.item)));
      if(job.revision!==clip.revision||!clip.ready)row.append(text("p","This submission uses an earlier approved final version. Editing locally does not replace a post in Buffer.","clip-flag"));
      for(const [p,r] of Object.entries(job.receipts)){
        row.append(text("p",`${publishingNames[p]}: ${publishingStatuses[r.status]||r.status}${r.message?` · ${r.message}`:""}`));
        if(r.url)row.append(publishingLink(`Open ${publishingNames[p]} post`,r.url));
        if(["unknown","submitting"].includes(r.status))row.append(action(`Resolve ${publishingNames[p]} submission`,()=>{
          publishingResolution={clip_id:clip.id,platform:p};$("publishing-resolve-id").value="";$("publishing-resolve-absent").checked=false;$("publishing-resolve-dialog").showModal();
        }));
      }
      row.append(publishingLink("Manage posts in Buffer","https://publish.buffer.com/"));
      const receipts=Object.values(job.receipts);
      if(receipts.some(r=>["pending","failed"].includes(r.status))&&!receipts.some(r=>["unknown","submitting"].includes(r.status)))row.append(action("Preview remaining submissions",()=>openPublishingPreview([publishingItem(clip)],"resume")));
      if(receipts.every(r=>["pending","failed"].includes(r.status)&&!r.post_id))row.append(action("Discard unsubmitted request",()=>publishingWork(async()=>{
        if(!confirm("Discard this unsubmitted request so you can change its caption or time?"))return;
        await api(`/api/publishing/buffer/requests/${clip.id}`,{method:"DELETE"});
      })));
      if(job.media_removed_at)row.append(text("p","Hosted copy removed; local final video retained."));
      else if(receipts.every(r=>r.status==="posted"))row.append(action("Remove hosted copy",()=>publishingWork(async()=>{
        if(!confirm("Remove the public Cloudflare copy? All tracked posts must have been confirmed published for 48 hours. Check that no other scheduled posts use this URL. Your local final video stays saved."))return;
        await api(`/api/publishing/buffer/media/${clip.id}`,{method:"DELETE"});$("publishing-message").textContent="Hosted copy removed. Local final video retained.";
      })));
    }else{
      const values=publishingItem(clip),fields={};
      for(const [name,label,tag] of [["title","YouTube title","input"],["caption","Post caption","textarea"],["note","Writing guidance (optional)","textarea"]]){
        const field=document.createElement(tag);field.value=values[name];field.maxLength=name==="title"?100:name==="note"?2000:2200;if(tag==="textarea")field.rows=3;
        const wrapper=text("label",label);wrapper.append(field);row.append(wrapper);fields[name]=field;
      }
      const category=document.createElement("select");publishingOptions(category,[["1","Film & animation"],["2","Autos & vehicles"],["10","Music"],["15","Pets & animals"],["17","Sports"],["19","Travel & events"],["20","Gaming"],["22","People & blogs"],["23","Comedy"],["24","Entertainment"],["25","News & politics"],["26","How-to & style"],["27","Education"],["28","Science & technology"],["29","Nonprofits & activism"]],values.category);
      const categoryLabel=text("label","YouTube category");categoryLabel.append(category);row.append(categoryLabel);fields.category=category;
      const kids=document.createElement("input");kids.type="checkbox";kids.checked=values.made_for_kids;const kidsLabel=text("label","","check");kidsLabel.append(kids,text("span","Made for kids (YouTube audience setting)"));row.append(kidsLabel);fields.made_for_kids=kids;publishingFields.set(clip.id,fields);
      row.append(text("p",clip.copy?"Saved for this final version. Editing the video requires new posting copy.":"Generate posting copy or enter your own. Blank fields are generated when preparing a post."));
      row.append(action(clip.copy?"Regenerate posting copy":"Generate posting copy",()=>publishingWork(async()=>{
        const value=publishingItem(clip);$("publishing-message").textContent="Writing a title and caption from the final clip…";
        clip.copy=await api(`/api/publishing/copy/${clip.id}/generate`,{method:"POST",body:JSON.stringify({revision:clip.revision,note:value.note,expected_id:value.copy_id})});
        publishingEdits.set(clip.id,{...value,copy_id:clip.copy.id});
        for(const key of ["title","caption","note"])fields[key].value=clip.copy[key];
        publishingRemember();$("publishing-message").textContent="Posting copy generated and saved. You can edit it before scheduling.";
      })),action("Save posting copy",()=>publishingWork(async()=>{
        await publishingSaveCopy(clip);$("publishing-message").textContent="Posting copy saved.";
      })));
      if(clip.plan){
        const select=document.createElement("input");select.type="checkbox";select.checked=publishingSelected.has(clip.id);
        select.onchange=()=>{if(select.checked&&publishingSelected.size>=10){select.checked=false;$("publishing-message").textContent="Select up to ten daily posts at a time.";return;}if(select.checked)publishingSelected.add(clip.id);else publishingSelected.delete(clip.id);publishingSelection();};
        const label=text("label","","check");label.append(select,text("span",`Use daily plan: ${publishingWhen({due_at:clip.plan.scheduled_at,timezone:clip.plan.timezone})}`));row.append(label);
      }
      const controls=text("div","","actions"),enabled=storage.connected&&data.connected&&Object.keys(data.mapping).length>0;
      const instant=action("Preview publish now",()=>openPublishingPreview([publishingItem(clip)],"now"));instant.disabled=!enabled;
      const schedule=action("Choose posting time",()=>{publishingTimeClip=clip;$("publishing-local-time").value="";$("publishing-time-dialog").showModal();});schedule.disabled=!enabled;controls.append(instant,schedule);
      const hosted=Object.values(storage.uploads).find(u=>u.clip_id===clip.id&&u.revision===clip.revision&&u.sha256===clip.video_sha256);
      if(hosted)row.append(publishingLink("Open hosted final video",hosted.url));
      else{const upload=action("Upload final video",()=>publishingWork(async()=>{await api(`/api/publishing/storage/clips/${clip.id}`,{method:"POST",body:JSON.stringify({revision:clip.revision})});$("publishing-message").textContent="Final video uploaded. No social post was created.";}));upload.disabled=!storage.connected;controls.append(upload);}
      row.append(controls);
    }
    $("publishing-videos").append(row);
  }
  if(!data.clips.length)$("publishing-videos").append(text("p","Mark clips Ready for posting to publish them here."));
  $("publishing-page").textContent=data.clips.length?`${publishingPage*10+1}–${Math.min((publishingPage+1)*10,data.clips.length)} of ${data.clips.length}`:"";
  $("publishing-previous").hidden=publishingPage===0;$("publishing-next").hidden=(publishingPage+1)*10>=data.clips.length;publishingSelection();
}
async function openPublishingPreview(items,mode,extra={}){return publishingWork(async()=>{
  if(mode!=="resume"){
    for(const item of items){
      const clip=publishingData?.clips.find(c=>c.id===item.clip_id);
      if(clip){await publishingSaveCopy(clip);Object.assign(item,publishingItem(clip));}
    }
  }
  publishingPreview=await api("/api/publishing/buffer/preview",{method:"POST",body:JSON.stringify({items,mode,...extra})});
  $("publishing-preview-items").replaceChildren();$("publishing-preview-message").textContent="";$("publishing-confirm").checked=false;$("publishing-send").disabled=true;
  $("publishing-send").textContent=publishingPreview.items[0].mode==="now"?"Publish now":"Schedule in Buffer";
  $("publishing-preview-summary").textContent="Confirmation uploads these final files to public Cloudflare URLs and submits them to Buffer for automatic publishing. Refresh Buffer status afterward to check the platform results.";
  for(const item of publishingPreview.items){
    const row=text("article","","form-section");row.append(text("h3",item.title),publishingVideo(item.clip_id,item.revision),text("p",publishingWhen(item)),text("p",item.caption,"publishing-caption"));
    for(const [p,c] of Object.entries(publishingPreview.channels))row.append(text("p",`${publishingNames[p]}: ${c.displayName||c.name}`));
    if(item.mapping.youtube)row.append(text("p",`YouTube: public · ${item.made_for_kids?"Made for kids":"Not made for kids"} · Category ${item.category}`));
    if(item.mapping.instagram)row.append(text("p","Instagram: Reel, also shared to feed."));$("publishing-preview-items").append(row);
  }
  $("publishing-preview-dialog").showModal();
});}
$("publishing-send").onclick=async()=>{
  if(publishingBusy||!publishingPreview||!$("publishing-confirm").checked)return;
  publishingBusy=true;$("publishing-send").disabled=true;$("publishing-preview-cancel").disabled=true;$("publishing-confirm").disabled=true;
  try{
    for(const [index,item] of publishingPreview.items.entries()){
      $("publishing-preview-message").textContent=`Uploading and submitting video ${index+1} of ${publishingPreview.items.length}…`;
      const job=await api(`/api/publishing/buffer/send/${publishingPreview.id}/${item.clip_id}`,{method:"POST"});
      if(Object.values(job.receipts).some(r=>!["posted","scheduled","sending"].includes(r.status)))throw new Error("Submission needs attention. Close this preview and check the per-platform results before continuing.");
      publishingSelected.delete(item.clip_id);
    }
    $("publishing-message").textContent="Buffer accepted the submissions. Refresh Buffer status to check when each platform confirms publication.";$("publishing-preview-dialog").close();
  }catch(e){$("publishing-preview-message").textContent=e.message;}
  finally{publishingBusy=false;$("publishing-preview-cancel").disabled=false;$("publishing-confirm").disabled=false;$("publishing-confirm").checked=false;await loadPublishing().catch(e=>error(e.message));}
};
$("publishing-confirm").onchange=()=>{$("publishing-send").disabled=publishingBusy||!$("publishing-confirm").checked;};
$("publishing-preview-cancel").onclick=()=>{if(!publishingBusy)$("publishing-preview-dialog").close();};
$("publishing-preview-dialog").oncancel=e=>{if(publishingBusy)e.preventDefault();};
$("publishing-time-cancel").onclick=()=>$("publishing-time-dialog").close();
$("publishing-time-form").onsubmit=async e=>{e.preventDefault();if(publishingBusy)return;$("publishing-time-dialog").close();await openPublishingPreview([publishingItem(publishingTimeClip)],"schedule",{local_time:$("publishing-local-time").value,timezone:$("publishing-timezone").value});};
$("publishing-preview-plan").onclick=()=>openPublishingPreview(publishingData.clips.filter(c=>publishingSelected.has(c.id)).map(publishingItem),"plan");
$("publishing-previous").onclick=()=>{if(!publishingBusy){publishingPage--;return loadPublishing().catch(e=>error(e.message));}};
$("publishing-next").onclick=()=>{if(!publishingBusy){publishingPage++;return loadPublishing().catch(e=>error(e.message));}};
$("refresh-publishing").onclick=()=>loadPublishing().catch(e=>error(e.message));
$("publishing-fill").onclick=()=>publishingWork(async()=>{
  $("publishing-fill").disabled=true;
  $("publishing-fill-message").textContent="Preparing posting copy, checking available slots and scheduling daily posts… Keep this page open until submission finishes.";
  try{
    publishingRemember();
    for(const clip of publishingData.clips){
      const edits=publishingEdits.get(clip.id);
      if(!clip.publication&&edits&&(edits.title.trim()||edits.caption.trim()||edits.note.trim()))await publishingSaveCopy(clip);
    }
    const result=await api("/api/publishing/buffer/fill",{method:"POST"});
    $("publishing-fill-message").textContent=`${result.scheduled} clip${result.scheduled===1?"":"s"} scheduled at 09:00 Europe/Helsinki.${result.message?` ${result.message}`:""}${result.skipped_order?` ${result.skipped_order} earlier source moments were left out to preserve posting order.`:""}`;
  }catch(e){$("publishing-fill-message").textContent=e.message;}
});
$("publishing-refresh-buffer").onclick=()=>publishingWork(async()=>{await api("/api/publishing/buffer/refresh",{method:"POST"});$("publishing-message").textContent="Buffer channels and delivery status refreshed.";});
$("publishing-organization").onchange=()=>publishingWork(async()=>{await api("/api/publishing/buffer/channels",{method:"POST",body:JSON.stringify({organization_id:$("publishing-organization").value,mapping:{}})});});
$("publishing-save-channels").onclick=()=>publishingWork(async()=>{const mapping=Object.fromEntries(Object.keys(publishingNames).map(p=>[p,$("publishing-"+p).value]));await api("/api/publishing/buffer/channels",{method:"POST",body:JSON.stringify({organization_id:$("publishing-organization").value,mapping})});$("publishing-message").textContent="Publishing channels saved.";});
$("publishing-buffer-form").onsubmit=e=>{e.preventDefault();return publishingWork(async()=>{const body=JSON.stringify({api_key:$("publishing-buffer-key").value});$("publishing-buffer-key").value="";await api("/api/publishing/buffer/connect",{method:"POST",body});$("publishing-message").textContent="Buffer connected. Check your destination channels below.";});};
$("publishing-storage-form").onsubmit=e=>{e.preventDefault();return publishingWork(async()=>{
  const body=JSON.stringify({access_key:$("publishing-access-key").value,secret_key:$("publishing-secret-key").value});$("publishing-access-key").value="";$("publishing-secret-key").value="";
  const result=await api("/api/publishing/storage/connect",{method:"POST",body});$("publishing-message").textContent=`Storage connected. ${(result.used_bytes/1e9).toFixed(2)} GB currently stored.`;
});};
$("publishing-resolve-cancel").onclick=()=>$("publishing-resolve-dialog").close();
$("publishing-resolve-form").onsubmit=e=>{e.preventDefault();$("publishing-resolve-dialog").close();return publishingWork(async()=>{
  await api(`/api/publishing/buffer/resolve/${publishingResolution.clip_id}/${publishingResolution.platform}`,{method:"POST",body:JSON.stringify({post_id:$("publishing-resolve-id").value.trim(),absent_confirmed:$("publishing-resolve-absent").checked})});$("publishing-message").textContent="Submission resolved. Preview remaining submissions if needed.";
});};
