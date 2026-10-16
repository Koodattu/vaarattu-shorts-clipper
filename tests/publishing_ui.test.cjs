const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
class Element{
  constructor(){this.children=[];this.value="";this.checked=false;this.open=false;this.dataset={};}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  showModal(){this.open=true;}
  close(){this.open=false;this.onclose?.();}
  setAttribute(key,value){this[key]=value;}
  pause(){this.paused=true;}
  querySelectorAll(tag){return this.children.flatMap(c=>[...(c.tagName===tag?[c]:[]),...c.querySelectorAll(tag)]);}
  scrollIntoView(){}
  focus(){this.focused=true;}
}
function fixture(){
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const writes=[],state={storage:false,buffer:false,fail:false};
  const clips=[{id:"clip",revision:3,title:"Clip",ready:true,source_title:"Recording",plan:null,publication:null,copy:{id:"copy-1",title:"Posting title",caption:"Posting caption.",note:""}}];
  const data={connected:false,organizations:[{id:"org",name:"My account"}],organization_id:"org",channels:[{id:"yt",service:"youtube",name:"Channel"}],mapping:{youtube:"yt"},clips};
  const find=(node,label)=>{if(node.textContent===label)return node;for(const c of node.children||[]){const match=find(c,label);if(match)return match;}};
  const context=vm.createContext({
    $:id=>nodes.get(id),document:{createElement:tag=>Object.assign(new Element(),{tagName:tag})},text:(tag,value,className)=>Object.assign(new Element(),{textContent:value,tagName:tag,className}),
    action:(label,onclick)=>Object.assign(new Element(),{textContent:label,onclick}),error:message=>assert.fail(message),
    api:async(url,options)=>{
      if(!options)return url.endsWith("storage")?{connected:state.storage,bucket:"bucket",storage_limit:8e9,uploads:{}}:{...data,connected:state.buffer};
      const body=options.body?JSON.parse(options.body):null;writes.push({url,body});
      if(url.endsWith("storage/connect")){
        assert.equal(nodes.get("publishing-secret-key").value,"");
        assert.equal(nodes.get("publishing-access-key").value,"");
        state.storage=true;return {used_bytes:0};
      }
      if(url.endsWith("buffer/connect")){assert.equal(nodes.get("publishing-buffer-key").value,"");state.buffer=true;return {};}
      if(url.includes("/publishing/copy/")){
        const copy={id:"copy-2",title:body.title||"Generated title",caption:body.caption||"Generated caption.",note:body.note||""};
        clips[0].copy=copy;return copy;
      }
      if(url.endsWith("buffer/media-cleanup")){
        if(state.fail)throw new Error("Buffer unavailable; no files removed.");
        return {removed:[{clip_id:"done",title:"Published clip"}],removed_bytes:25000000,
          kept:[{clip_id:"wait",title:"Scheduled clip",reason:"Still scheduled"}],failed:[]};
      }
      if(url.includes("/buffer/confirm-published/")){
        if(state.fail)throw new Error("Paste the HTTPS link to the published post on the selected platform.");
        const receipt=data.clips[0].publication.receipts.tiktok;
        data.clips[0].publication.receipts.tiktok={...receipt,status:"posted",url:body.url,published_at:null,message:"",manual_confirmation:{previous_receipt:{...receipt}}};
        return data.clips[0].publication;
      }
      if(url.endsWith("buffer/fill"))return {scheduled:2,message:"",skipped_order:0,attention:state.attention||[]};
      if(url.endsWith("preview"))return {id:"ticket",channels:{youtube:{name:"Channel"}},items:body.items.map(i=>({...i,mapping:{youtube:"yt"},mode:body.mode==="now"?"now":"schedule",due_at:body.mode==="now"?null:"2030-01-01T16:00:00Z",timezone:body.timezone||"Europe/Helsinki"}))};
      if(url.includes("/send/"))return {receipts:{youtube:{status:state.fail?"unknown":"scheduled"}}};
      return {};
    },
  });
  for(const file of ["publishing-calendar.js","publishing.js"])vm.runInContext(fs.readFileSync(path.join(directory,file),"utf8"),context);
  return {nodes,writes,state,data,context,find:(label)=>find(nodes.get("publishing-videos"),label),findIn:find};
}
test("publishing requires an explicit upload and clears credential inputs before sending",async()=>{
  const {nodes,writes,find}=fixture();
  await nodes.get("refresh-publishing").onclick();
  assert.equal(writes.length,0);
  assert.equal(find("Upload final video").disabled,true);
  nodes.get("publishing-access-key").value="access";nodes.get("publishing-secret-key").value="secret";
  await nodes.get("publishing-storage-form").onsubmit({preventDefault(){}});
  assert.deepEqual(writes[0].body,{access_key:"access",secret_key:"secret"});
  await find("Upload final video").onclick();
  assert.deepEqual(writes[1],{url:"/api/publishing/storage/clips/clip",body:{revision:3}});
  assert.match(nodes.get("publishing-message").textContent,/No social post/);
});
test("Buffer preview embeds exact video and needs explicit confirmation before publish-now",async()=>{
  const {nodes,writes,state,find}=fixture();state.storage=true;
  nodes.get("publishing-buffer-key").value="personal-key";
  await nodes.get("publishing-buffer-form").onsubmit({preventDefault(){}});
  assert.deepEqual(writes[0].body,{api_key:"personal-key"});
  await find("Preview publish now").onclick();
  assert.equal(nodes.get("publishing-preview-dialog").open,true);
  assert.equal(nodes.get("publishing-preview-items").children[0].children[1].src,"/api/artifacts/clip/video?revision=3");
  assert.equal(nodes.get("publishing-send").disabled,true);
  await nodes.get("publishing-send").onclick();
  assert.equal(writes.filter(w=>w.url.includes("/send/")).length,0);
  nodes.get("publishing-confirm").checked=true;nodes.get("publishing-confirm").onchange();
  await nodes.get("publishing-send").onclick();
  assert.equal(writes.filter(w=>w.url.includes("/send/")).length,1);
  assert.equal(nodes.get("publishing-preview-dialog").open,false);
  assert.match(nodes.get("publishing-message").textContent,/accepted/);
});
test("custom schedule passes local time and configurable zone into preview",async()=>{
  const {nodes,writes,state,find}=fixture();state.storage=true;state.buffer=true;
  await nodes.get("refresh-publishing").onclick();
  await find("Choose posting time").onclick();
  nodes.get("publishing-local-time").value="2030-01-01T18:00";nodes.get("publishing-timezone").value="Europe/Helsinki";
  await nodes.get("publishing-time-form").onsubmit({preventDefault(){}});
  assert.equal(writes[0].body.mode,"schedule");
  assert.equal(writes[0].body.local_time,"2030-01-01T18:00");
  assert.equal(writes[0].body.timezone,"Europe/Helsinki");
  assert.equal(nodes.get("publishing-send").textContent,"Schedule in Buffer");
  assert.equal(writes.length,1);
});
test("uncertain submissions stop a batch and require fresh confirmation",async()=>{
  const {nodes,writes,state,context}=fixture();state.storage=true;state.buffer=true;state.fail=true;
  await vm.runInContext('openPublishingPreview([{clip_id:"clip",revision:3,title:"One"},{clip_id:"second",revision:1,title:"Two"}],"plan")',context);
  nodes.get("publishing-confirm").checked=true;
  await nodes.get("publishing-send").onclick();
  assert.equal(writes.filter(w=>w.url.includes("/send/")).length,1);
  assert.equal(nodes.get("publishing-confirm").checked,false);
  assert.equal(nodes.get("publishing-preview-dialog").open,true);
  assert.match(nodes.get("publishing-preview-message").textContent,/attention/);
});
test("fill button schedules directly only when clicked and blocks repeat clicks while busy",async()=>{
  const {nodes,writes,state}=fixture();state.storage=true;state.buffer=true;
  await nodes.get("refresh-publishing").onclick();
  assert.equal(writes.length,0);
  assert.equal(nodes.get("publishing-fill").disabled,false);
  const first=nodes.get("publishing-fill").onclick();
  const duplicate=nodes.get("publishing-fill").onclick();
  await Promise.all([first,duplicate]);
  assert.equal(writes.length,1);
  assert.equal(writes[0].url,"/api/publishing/buffer/fill");
  assert.match(nodes.get("publishing-fill-message").textContent,/2 clips scheduled at random times between 19:00 and 21:00 Finnish time \(Europe\/Helsinki\), one per day/);
  assert.equal(nodes.get("publishing-fill").disabled,false);
});

test("posting copy can be regenerated with guidance and manual edits are saved before preview",async()=>{
  const {nodes,writes,state,data,find}=fixture();state.storage=true;state.buffer=true;
  await nodes.get("refresh-publishing").onclick();
  find("Writing guidance (optional)").children[0].value="Kuiva huumori";
  await find("Regenerate posting copy").onclick();
  assert.equal(writes[0].url,"/api/publishing/copy/clip/generate");
  assert.equal(writes[0].body.note,"Kuiva huumori");
  assert.equal(find("Post caption").children[0].value,"Generated caption.");
  find("Post caption").children[0].value="My own caption.";
  await find("Preview publish now").onclick();
  assert.equal(writes[1].url,"/api/publishing/copy/clip");
  assert.equal(writes[1].body.expected_id,"copy-2");
  assert.equal(writes[2].body.items[0].caption,"My own caption.");
  assert.equal(data.clips[0].copy.caption,"My own caption.");
  assert.equal(nodes.get("publishing-send").disabled,true);
});

test("a new video revision does not reuse unsaved text from the old revision",async()=>{
  const {nodes,data,find}=fixture();
  await nodes.get("refresh-publishing").onclick();
  find("Post caption").children[0].value="Old edit";
  // Replace the server object, leaving the displayed revision's object intact.
  data.clips=[{...data.clips[0],revision:4,copy:null}];
  await nodes.get("refresh-publishing").onclick();
  assert.equal(find("Post caption").children[0].value,"");
});


test("ready clips publish without a local plan and connected accounts stay out of the main workflow",async()=>{
  const {nodes,state,writes,find,data}=fixture();state.storage=true;state.buffer=true;
  await nodes.get("refresh-publishing").onclick();
  assert.equal(nodes.get("publishing-fill").disabled,false);
  assert.equal(find("Choose posting time").disabled,false);
  assert.equal(nodes.get("publishing-plan-actions").hidden,true);
  assert.equal(nodes.get("publishing-connections").open,false);
  assert.match(nodes.get("publishing-connection-status").textContent,/Ready to publish/);
  assert.equal(writes.length,0);
  data.clips[0].plan={scheduled_at:"2030-01-01T16:00:00Z",timezone:"Europe/Helsinki"};
  await nodes.get("refresh-publishing").onclick();
  assert.equal(nodes.get("publishing-plan-actions").hidden,false);
  assert.equal(writes.length,0);
});


test("cloud cleanup is one explicit action, blocks duplicates and shows removed and kept copies",async()=>{
  const {nodes,writes,state}=fixture();
  await nodes.get("refresh-publishing").onclick();assert.equal(nodes.get("publishing-cleanup").disabled,true);
  state.buffer=true;state.storage=true;await nodes.get("refresh-publishing").onclick();
  assert.equal(writes.length,0);assert.equal(nodes.get("publishing-cleanup").disabled,false);
  await Promise.all([nodes.get("publishing-cleanup").onclick(),nodes.get("publishing-cleanup").onclick()]);
  assert.equal(writes.length,1);assert.equal(writes[0].url,"/api/publishing/buffer/media-cleanup");
  assert.match(nodes.get("publishing-cleanup-message").textContent,/1 cloud copies removed \(25.0 MB\). 1 kept/);
  assert.equal(nodes.get("publishing-cleanup-details").hidden,false);
  assert.match(nodes.get("publishing-cleanup-items").children[1].textContent,/Still scheduled/);
  assert.equal(nodes.get("publishing-cleanup").disabled,false);
  state.fail=true;await nodes.get("publishing-cleanup").onclick();
  assert.match(nodes.get("publishing-cleanup-message").textContent,/Buffer unavailable/);
  assert.equal(nodes.get("publishing-cleanup").disabled,false);
  assert.equal(nodes.get("publishing-cleanup-details").hidden,true);
});


function scheduledClip(id="scheduled",receipts={youtube:{status:"scheduled",due_at:"2026-09-29T16:00:00Z"},instagram:{status:"scheduled",due_at:"2026-09-29T16:00:00Z"}}){
  return {id,revision:4,title:"Local title",ready:true,source_title:"Recording",publication:{revision:3,created_at:"2026-09-20T12:00:00Z",item:{title:"Saved publishing title",due_at:"2026-09-29T16:00:00Z",timezone:"Europe/Helsinki"},receipts}};
}
test("calendar groups platforms per clip/day, uses Finnish time and handles DST",()=>{
  const {context}=fixture();context.calendarClips=[scheduledClip()];
  let entries=vm.runInContext("publishingCalendarModel(calendarClips)",context);
  assert.equal(entries.length,1);assert.equal(entries[0].platforms.length,2);assert.equal(entries[0].day,"2026-09-29");assert.equal(entries[0].kind,"scheduled");
  assert.equal(vm.runInContext('publishingDateKey("2026-09-29T22:30:00Z")',context),"2026-09-30");
  assert.equal(vm.runInContext('publishingDateKey("2026-10-25T22:30:00Z")',context),"2026-10-26");
  context.calendarClips[0].publication.receipts.youtube={status:"posted",published_at:"2026-09-30T00:01:00Z",due_at:"2026-09-29T16:00:00Z"};
  entries=vm.runInContext("publishingCalendarModel(calendarClips)",context);
  assert.equal(entries.length,2);assert.equal(entries[1].day,"2026-09-30");assert.equal(entries[1].kind,"posted");assert.equal(entries[1].estimated,false);
  assert.equal(vm.runInContext('publishingCalendarModel(calendarClips,"posted").length',context),1);
});
test("past due dates never imply publication and legacy published dates remain labelled",()=>{
  const {context}=fixture();context.calendarClips=[scheduledClip("legacy",{youtube:{status:"posted",checked_at:"2026-10-01T08:00:00Z"},instagram:{status:"scheduled"}})];
  let entries=vm.runInContext("publishingCalendarModel(calendarClips)",context);
  assert.equal(entries[0].kind,"mixed");assert.equal(entries[0].estimated,true);
  assert.match(entries[0].platforms[0].label,/publication time unavailable/);
  context.calendarClips[0].publication.receipts.youtube.status="error";
  entries=vm.runInContext("publishingCalendarModel(calendarClips)",context);assert.equal(entries[0].kind,"attention");
  context.calendarClips[0].publication.item.due_at=null;context.calendarClips[0].publication.created_at=null;
  entries=vm.runInContext('publishingCalendarModel(calendarClips,"attention")',context);assert.equal(entries[0].day,null);
});
test("local plans remain distinct from Buffer schedules and manual post times are labelled",()=>{
  const {context}=fixture();context.calendarClips=[{id:"manual",title:"Manual clip",plan:{scheduled_at:"2026-09-29T16:00:00Z",deliveries:{youtube:{status:"pending"},tiktok:{status:"posted",recorded_at:"2026-09-30T09:00:00Z"}}}}];
  const entries=vm.runInContext("publishingCalendarModel(calendarClips)",context);
  assert.equal(entries.length,2);assert.equal(entries[0].kind,"planned");assert.equal(entries[1].kind,"posted");
  assert.equal(entries[1].platforms[0].label,"Manually recorded");assert.equal(entries[1].estimated,true);
});
test("month calendar navigates, filters and opens saved revision details without posting",async()=>{
  const {nodes,data,writes,context}=fixture();data.clips.push(scheduledClip());
  await nodes.get("refresh-publishing").onclick();
  nodes.get("publishing-calendar-month").value="2026-09";nodes.get("publishing-calendar-month").onchange();
  const grid=nodes.get("publishing-calendar-grid");assert.equal(grid.children.length,35);
  assert.equal(grid.children[0].children[0].textContent,"31","Monday-first grid includes the previous month");
  const cells=grid.children.filter(c=>c.children.some(n=>n.className?.includes("publishing-calendar-event")));
  assert.equal(cells.length,1);const event=cells[0].children[1];assert.match(event.children[0].textContent,/19:00/);
  event.onclick();assert.equal(nodes.get("publishing-calendar-dialog").open,true);
  const video=nodes.get("publishing-calendar-detail").querySelectorAll("video")[0];assert.match(video.src,/scheduled\/video\?revision=3$/);
  nodes.get("publishing-calendar-close").onclick();assert.equal(video.paused,true);
  assert.equal(nodes.get("publishing-videos").children.length,1,"Published/scheduled clips stay out of the ready pool");
  assert.equal(nodes.get("publishing-ready-summary").textContent,"Ready to schedule · 1 clip");
  nodes.get("publishing-videos").children[0].open=true;await nodes.get("refresh-publishing").onclick();assert.equal(nodes.get("publishing-videos").children[0].open,true);
  nodes.get("publishing-calendar-next").onclick();assert.equal(nodes.get("publishing-calendar-month").value,"2026-10");
  nodes.get("publishing-calendar-prev").onclick();assert.equal(nodes.get("publishing-calendar-month").value,"2026-09");
  nodes.get("publishing-calendar-filter").value="posted";nodes.get("publishing-calendar-filter").onchange();
  assert.equal(vm.runInContext("publishingCalendarEntries.length",context),0);assert.equal(writes.length,0);
});
test("calendar includes every month entry beyond the ten-item ready pool page",async()=>{
  const {nodes,data,context}=fixture();data.clips=Array.from({length:16},(_,i)=>scheduledClip(`clip-${i}`));
  await nodes.get("refresh-publishing").onclick();nodes.get("publishing-calendar-month").value="2026-09";nodes.get("publishing-calendar-month").onchange();
  assert.equal(vm.runInContext("publishingCalendarEntries.length",context),16);
  const day=nodes.get("publishing-calendar-grid").children.find(c=>c.children.length>1);
  assert.equal(day.children.length,5);assert.equal(day.children[4].textContent,"+13 more");day.children[4].onclick();
  assert.equal(nodes.get("publishing-calendar-detail").children.length,16);
});


test("fill reports skipped existing failures without resubmitting them",async()=>{
  const {nodes,state,writes}=fixture();state.storage=true;state.buffer=true;
  state.attention=[{clip_id:"old",title:"Uber, pizzaversiona",platforms:["tiktok"]}];
  await nodes.get("refresh-publishing").onclick();
  await nodes.get("publishing-fill").onclick();
  assert.deepEqual(writes.map(w=>w.url),["/api/publishing/buffer/fill"]);
  assert.match(nodes.get("publishing-fill-message").textContent,/2 clips scheduled/);
  assert.match(nodes.get("publishing-fill-message").textContent,/Skipped 1 existing request needing attention: Uber, pizzaversiona \(TikTok\)/);
});

test("already published action keeps input on failure and saves only explicit local confirmation",async()=>{
  const {nodes,state,writes,data,context,findIn}=fixture();state.buffer=true;
  data.clips[0].publication={revision:3,item:{title:"Uber, pizzaversiona",due_at:"2026-10-06T17:19:00Z",timezone:"Europe/Helsinki"},receipts:{
    tiktok:{status:"error",post_id:"tt-1",message:"socket hang up",due_at:"2026-10-06T17:19:00Z"},
    youtube:{status:"scheduled",post_id:"yt-1"},
  }};
  await nodes.get("refresh-publishing").onclick();
  vm.runInContext('openPublishingCalendar({clipId:"clip"})',context);
  const panel=nodes.get("publishing-calendar-detail");
  assert.equal(findIn(panel,"Already published on YouTube"),undefined);
  findIn(panel,"Already published on TikTok").onclick();
  assert.equal(nodes.get("publishing-published-dialog").open,true);
  assert.match(nodes.get("publishing-published-summary").textContent,/Uber, pizzaversiona.*TikTok/);
  assert.equal(nodes.get("publishing-published-url").focused,true);
  assert.equal(writes.length,0);
  nodes.get("publishing-published-url").value="https://instagram.com/reel/wrong";
  state.fail=true;
  await nodes.get("publishing-published-form").onsubmit({preventDefault(){}});
  assert.equal(nodes.get("publishing-published-dialog").open,true);
  assert.equal(nodes.get("publishing-published-url").value,"https://instagram.com/reel/wrong");
  assert.match(nodes.get("publishing-published-message").textContent,/HTTPS link/);
  assert.equal(nodes.get("publishing-published-save").disabled,false);
  state.fail=false;
  nodes.get("publishing-published-url").value="https://www.tiktok.com/@vaarattu/video/123";
  const submit=()=>nodes.get("publishing-published-form").onsubmit({preventDefault(){}});
  await Promise.all([submit(),submit()]);
  assert.equal(writes.length,2);
  assert.deepEqual(writes[1],{url:"/api/publishing/buffer/confirm-published/clip/tiktok",body:{url:"https://www.tiktok.com/@vaarattu/video/123",post_id:"tt-1"}});
  assert.equal(nodes.get("publishing-published-dialog").open,false);
  assert.ok(findIn(panel,"TikTok: Published · confirmed by you"));
  assert.ok(findIn(panel,"Failed in Buffer · socket hang up"));
  assert.equal(findIn(panel,"Already published on TikTok"),undefined);
  assert.equal(nodes.get("publishing-calendar-close").focused,true);
  assert.match(nodes.get("publishing-message").textContent,/No post was submitted/);
  const calendar=vm.runInContext('publishingCalendarModel(publishingData.clips,"posted")',context);
  assert.equal(calendar[0].day,"2026-10-06");
  assert.equal(calendar[0].estimated,true);
});
