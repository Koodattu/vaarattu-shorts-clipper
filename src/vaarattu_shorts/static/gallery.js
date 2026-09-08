"use strict";
let galleryClips=[], galleryPage=0, galleryLoading=false;
let reviewNotesClip=null, reviewNoteSaving=false;
const galleryPageSize=12, galleryCards=new Map();
const reviewLabels={unreviewed:"Unreviewed",approved:"Approved",not_approved:"Not approved"};
async function loadGallery(){
  if(galleryLoading)return;
  galleryLoading=true;$("refresh-gallery").disabled=true;
  try{galleryClips=await api("/api/clips");paintGallery();}
  finally{galleryLoading=false;$("refresh-gallery").disabled=false;}
}
function gallerySource(clip){
  const recording=library?.videos.find(v=>v.url===clip.source_url);
  return recording?.title||clip.source_url||"Original recording";
}
function clipNotes(clip){return [...(clip.status==="held"?["Render needs attention"]:[]),...(clip.context_request?.note?[`Your context note: ${clip.context_request.note}`]:[]),...(clip.context_request?.reason?[`Context review: ${clip.context_request.reason}`]:[]),...(clip.review_notes||[]),...(clip.flags||[])];}
function showClipNotes(clip){
  if(reviewNoteSaving)return;
  reviewNotesClip=clip;
  if(currentView==="review")$("review-player").pause();
  $("clip-notes-title").textContent=clip.title;
  $("clip-notes-body").replaceChildren(...clipNotes(clip).map(note=>text("p",note)));
  $("review-reason").value=clip.review_note||"";
  $("save-review-reason").disabled=!["ready","held"].includes(clip.status)||!clip.has_preview;
  $("review-reason-message").textContent="";
  $("clip-notes-dialog").showModal();
}
$("close-clip-notes").onclick=()=>$("clip-notes-dialog").close();
$("clip-notes-dialog").oncancel=event=>{if(reviewNoteSaving)event.preventDefault();};
$("save-review-reason").onclick=async()=>{
  if(reviewNoteSaving||!reviewNotesClip)return;
  const clip=reviewNotesClip;reviewNoteSaving=true;
  $("save-review-reason").disabled=true;$("review-reason").disabled=true;
  $("close-clip-notes").disabled=true;
  $("review-reason-message").textContent="Saving reason…";
  try{
    await saveClipReview(clip,clip.review_status||"unreviewed",$("review-reason").value);
    $("review-reason").value=clip.review_note||"";
    $("review-reason-message").textContent="Reason saved.";
  }catch(e){$("review-reason-message").textContent=e.message;}
  finally{reviewNoteSaving=false;$("save-review-reason").disabled=false;$("review-reason").disabled=false;$("close-clip-notes").disabled=false;}
};
async function saveClipReview(clip,status,note){
  const previousNote=clip.review_note||"";
  const updated=await api(`/api/clips/${clip.id}/review`,{method:"POST",body:JSON.stringify({expected_revision:clip.revision,status,...(note===undefined?{}:{note})})});
  clip.review_status=updated.review_status;clip.review_note=updated.review_note||"";
  if(typeof reviewQueue!=="undefined"&&reviewQueue[0]?.id===clip.id&&reviewQueue[0].revision===clip.revision&&$("review-rejection-reason").value===previousNote)$("review-rejection-reason").value=clip.review_note;
  const item=galleryClips.find(c=>c.id===updated.id&&c.revision===updated.revision);
  if(item){item.review_status=updated.review_status;item.review_note=updated.review_note||"";}
  return updated;
}
function galleryCard(clip){
  const card=text("article","","gallery-card"),body=text("div","","gallery-card-body");
  const video=document.createElement("video");video.controls=true;video.preload="metadata";video.playsInline=true;
  video.src=`/api/artifacts/${clip.id}/video?revision=${clip.revision}`;video.setAttribute("aria-label",clip.title);
  video.onplay=()=>{for(const other of $("gallery-clips").querySelectorAll("video"))if(other!==video)other.pause();};
  const badge=text("span","","review-badge");
  const statusRow=text("div","","gallery-card-meta");
  statusRow.append(badge,text("span",`${((clip.output_duration_us??(clip.end_us-clip.start_us))/1e6).toFixed(1)}s · Revision ${clip.revision}`,"muted"));
  const title=text("h2",clip.title),source=text("p",gallerySource(clip),"gallery-source muted");
  title.title=clip.title;source.title=source.textContent;
  const notes=clipNotes(clip),notesButton=action(notes.length?`${notes.length} review ${notes.length===1?"note":"notes"}`:"Review reason",()=>showClipNotes(entry.clip));
  notesButton.className="gallery-notes";
  body.append(statusRow,title,source,notesButton);
  const reviews=text("div","","gallery-review"),buttons=[];
  const entry={card,video,badge,buttons,clip,saving:false};
  for(const [status,label] of [["approved","Approve"],["not_approved","Not approved"],["unreviewed","Clear review"]]){
    const button=action(label,async()=>{
      if(entry.saving)return;
      entry.saving=true;updateGalleryReview(entry);error("");
      try{
        const updated=await saveClipReview(clip,status);
        paintGallery();
        $("notice").textContent=`${clip.title}: ${reviewLabels[updated.review_status]}.`;$("notice").hidden=false;
      }finally{entry.saving=false;updateGalleryReview(entry);}
    });
    button.setAttribute("aria-label",`${label}: ${clip.title}`);buttons.push({button,status});reviews.append(button);
  }
  body.append(reviews);
  const links=text("div","","actions gallery-links");
  links.append(action("Edit clip",async()=>{
    await openEditor(clip.id);
    $("editor-back").href="#gallery";$("editor-back").textContent="← Back to gallery";
  }),action("View run",async()=>{activeRun=clip.run_id;paintRuns();await detail();goView("results");}));
  if(clip.status==="ready"){
    const download=document.createElement("a");download.href=video.src;download.download=`${clip.id}.mp4`;download.textContent="Download ↓";links.append(download);
  }
  body.append(links);card.append(video,body);return entry;
}
function updateGalleryReview(entry){
  const status=entry.clip.review_status||"unreviewed";
  entry.badge.textContent=reviewLabels[status];entry.badge.className="review-badge review-"+status;
  for(const item of entry.buttons){
    item.button.disabled=entry.saving||item.status===status;
    item.button.setAttribute("aria-pressed",String(item.status===status));
  }
}
function paintGallery(){
  $("gallery-count").textContent=galleryClips.length;
  const counts={unreviewed:0,approved:0,not_approved:0};
  for(const clip of galleryClips)counts[clip.review_status||"unreviewed"]++;
  $("gallery-summary").textContent=`${galleryClips.length} rendered clips · ${counts.unreviewed} unreviewed · ${counts.approved} approved · ${counts.not_approved} not approved`;
  const query=$("gallery-search").value.trim().toLocaleLowerCase(),filter=$("gallery-filter").value;
  const items=galleryClips.filter(c=>(filter==="all"||(c.review_status||"unreviewed")===filter)&&`${c.title} ${gallerySource(c)}`.toLocaleLowerCase().includes(query));
  galleryPage=Math.min(galleryPage,Math.max(0,Math.ceil(items.length/galleryPageSize)-1));
  const visible=items.slice(galleryPage*galleryPageSize,(galleryPage+1)*galleryPageSize),cards=[];
  const grid=$("gallery-clips"),keep=new Set();
  for(const clip of visible){
    const signature=JSON.stringify([clip.id,clip.revision,clip.title,clip.status,clip.flags,clip.review_notes,gallerySource(clip)]);
    let entry=galleryCards.get(signature);
    if(!entry){entry=galleryCard(clip);galleryCards.set(signature,entry);}
    entry.clip=clip;updateGalleryReview(entry);keep.add(signature);cards.push(entry.card);
  }
  for(const [key,entry] of galleryCards)if(!keep.has(key)){entry.video.pause();galleryCards.delete(key);}
  if(!cards.length){
    const empty=text("div","","empty-state");
    empty.append(text("h2",galleryClips.length?"No clips match these filters.":"No rendered clips yet."),text("p",galleryClips.length?"Try another search or choose All clips.":"Clips from every run appear here once a rendered preview is available."));
    grid.replaceChildren(empty);
  }else if(cards.length!==grid.children.length||cards.some((card,i)=>card!==grid.children[i]))grid.replaceChildren(...cards);
  $("gallery-page").textContent=items.length?`${galleryPage*galleryPageSize+1}–${Math.min((galleryPage+1)*galleryPageSize,items.length)} of ${items.length} clips`:"0 clips";
  $("previous-gallery").hidden=galleryPage===0;$("next-gallery").hidden=(galleryPage+1)*galleryPageSize>=items.length;
}
$("refresh-gallery").onclick=()=>loadGallery().catch(e=>error(e.message));
for(const id of ["gallery-search","gallery-filter"])$(id).oninput=()=>{galleryPage=0;paintGallery();};
$("previous-gallery").onclick=()=>{galleryPage--;paintGallery();$("gallery-heading").scrollIntoView({behavior:"auto"});};
$("next-gallery").onclick=()=>{galleryPage++;paintGallery();$("gallery-heading").scrollIntoView({behavior:"auto"});};
