const {test} = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

class Element {
  constructor(tag="div") { this.tagName=tag; this.children=[]; this.value=""; this.textContent=""; }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children=nodes; }
  scrollIntoView() {}
  focus() {}
  pause() {}
  querySelectorAll() { return []; }
  setAttribute(name, value) { this[name]=value; }
  getContext() { return {}; }
  showModal() { this.open=true; }
  close() { this.open=false; }
}
function find(node, label) {
  if(node.textContent===label)return node;
  for(const child of node.children){const match=find(child,label);if(match)return match;}
}

test("context review queues direction and note, preserves the original, and returns a revision or explanation", async()=>{
  const staticPath=path.join(__dirname,"../src/vaarattu_shorts/static");
  const html=fs.readFileSync(path.join(staticPath,"index.html"),"utf8");
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const player=nodes.get("review-player");
  player.load=()=>{};player.play=async()=>{};player.removeAttribute=name=>{delete player[name];};
  let clip={id:"clip",run_id:"run",revision:1,status:"ready",has_preview:true,title:"Tarina",start_us:20000000,end_us:25000000};
  let runState="running",fail=true,requestId=0;
  const writes=[],listeners={};
  const context=vm.createContext({
    currentView:"review",savedLayouts:[],layouts:async()=>{},setTimeout:()=>0,clearTimeout:()=>{},
    $:id=>nodes.get(id),text:(tag,value)=>Object.assign(new Element(tag),{textContent:value}),clipNotes:()=>[],
    document:{addEventListener:(name,fn)=>{listeners[name]=fn;}},
    api:async(url,options)=>{
      if(url==="/api/clips")return [{...clip},{...clip,id:"other"}];
      if(url==="/api/runs/run")return {state:runState,clips:[clip]};
      assert.equal(url,"/api/clips/clip/context");
      writes.push(JSON.parse(options.body));
      if(fail)throw new Error("Recording is busy");
      requestId++;
      clip={...clip,context_request:{...writes.at(-1),id:String(requestId),status:"pending"}};
      return {run_id:"run",revision:clip.revision,request_id:String(requestId)};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(staticPath,"review.js"),"utf8"),context);
  await vm.runInContext("loadReviewQueue()",context);
  nodes.get("review-context").onclick({detail:1});
  assert.equal(nodes.get("review-context-dialog").open,true);
  assert.equal(nodes.get("review-context-before").checked,false);
  assert.equal(nodes.get("review-context-after").checked,false);
  listeners.keydown({key:"r",target:{closest:()=>null},preventDefault(){assert.fail("Modal must block review shortcuts");}});
  nodes.get("review-context-before").checked=true;
  nodes.get("review-context-note").value="Include the question";
  await nodes.get("review-context-form").onsubmit({preventDefault(){}});
  assert.equal(nodes.get("review-context-dialog").open,true);
  assert.match(nodes.get("review-context-message").textContent,/busy/);
  assert.equal(nodes.get("review-context-note").value,"Include the question");
  fail=false;
  await Promise.all([nodes.get("review-context-form").onsubmit({preventDefault(){}}),nodes.get("review-context-form").onsubmit({preventDefault(){}})]);
  assert.equal(writes.length,2,"Double submit must queue only one request");
  assert.deepEqual(writes[1],{expected_revision:1,before:true,after:false,note:"Include the question"});
  assert.equal(nodes.get("review-context-dialog").open,false);
  assert.equal(player.src,"/api/artifacts/clip/video?revision=1");
  assert.equal(nodes.get("review-approve").disabled,true);
  assert.equal(nodes.get("review-skip").disabled,false);
  clip={...clip,revision:2,previous_revision:1,context_request:{...clip.context_request,status:"expanded"}};
  runState="completed";
  await vm.runInContext("pollReviewRender()",context);
  assert.equal(player.src,"/api/artifacts/clip/video?revision=2");
  assert.equal(nodes.get("review-approve").disabled,false);
  nodes.get("review-context").onclick({detail:1});
  await nodes.get("review-context-form").onsubmit({preventDefault(){}});
  clip={...clip,context_request:{...clip.context_request,status:"unchanged",reason:"Ei lisäkontekstia."}};
  await vm.runInContext("pollReviewRender()",context);
  assert.equal(player.src,"/api/artifacts/clip/video?revision=2");
  assert.match(nodes.get("review-message").textContent,/Original clip kept.*Ei lisäkontekstia/);
  assert.equal(nodes.get("review-approve").disabled,false);
  runState="running";
  nodes.get("review-context").onclick({detail:1});
  await nodes.get("review-context-form").onsubmit({preventDefault(){}});
  await vm.runInContext("skipReview()",context);
  assert.match(nodes.get("review-message").textContent,/Context review continues/);
  assert.equal(vm.runInContext("reviewQueue[0].id",context),"other");
  assert.equal(vm.runInContext("reviewRendering",context),null);
});

test("review queue keeps runs grouped and serves ranked clips best first", async()=>{
  const staticPath=path.join(__dirname,"../src/vaarattu_shorts/static");
  const html=fs.readFileSync(path.join(staticPath,"index.html"),"utf8");
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  nodes.get("review-player").load=()=>{};
  const clips=[
    {id:"third",run_id:"new",review_rank:3},
    {id:"first",run_id:"new",review_rank:1},
    {id:"second",run_id:"new",review_rank:2},
    {id:"older",run_id:"old",review_rank:1},
  ].map(c=>({...c,title:c.id,status:"ready",has_preview:true,revision:1,start_us:0,end_us:5000000}));
  const context=vm.createContext({
    currentView:"gallery",savedLayouts:[],layouts:async()=>{},api:async()=>clips,
    $:id=>nodes.get(id),text:(tag,value)=>Object.assign(new Element(tag),{textContent:value}),clipNotes:()=>[],
    document:{addEventListener(){}},
  });
  vm.runInContext(fs.readFileSync(path.join(staticPath,"review.js"),"utf8"),context);
  await vm.runInContext("loadReviewQueue()",context);
  assert.equal(vm.runInContext("reviewQueue.map(c=>c.id).join(',')",context),"first,second,third,older");
  assert.equal(nodes.get("review-title").textContent,"first");
  assert.match(nodes.get("review-meta").textContent,/Priority 1/);
});

test("queue rerenders the selected layout and waits on the same clip for the new preview", async()=>{
  const staticPath=path.join(__dirname,"../src/vaarattu_shorts/static");
  const html=fs.readFileSync(path.join(staticPath,"index.html"),"utf8");
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const player=nodes.get("review-player");
  player.play=async()=>{};player.load=()=>{};player.removeAttribute=name=>{delete player[name];};
  const clips=Array.from({length:2},(_,i)=>({id:`clip-${i}`,run_id:"render-run",title:`Moment ${i}`,
    status:"ready",has_preview:true,review_status:"unreviewed",revision:1,start_us:0,end_us:15000000}));
  let presets=[{id:"alternate",body:{name:"Alternate camera"}}],failRender=true,failPoll=false,runState="running";
  const writes=[],timers=[];
  const context=vm.createContext({
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout:()=>{},
    document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),addEventListener(){}},
    fetch:async(url,options)=>{
      let result;
      if(url==="/api/status")result={token:"fixture",models:{turbo:true},providers:{}};
      else if(url==="/api/layouts")result=presets;
      else if(url==="/api/runs")result=[];
      else if(url==="/api/videos")result={configured:false,videos:[]};
      else if(url==="/api/clips")result=clips.filter(c=>c.has_preview).map(c=>({...c}));
      else if(url==="/api/clips/clip-0/rerender"){
        const payload=JSON.parse(options.body);writes.push(payload);
        if(failRender)return {ok:false,json:async()=>({detail:"Finish this run before editing it."})};
        assert.deepEqual(payload,{expected_revision:clips[0].revision,layout_id:"alternate",trim_silence:false,video_encoder:"libx264"});
        runState="queued";
        clips[0]={...clips[0],revision:clips[0].revision+1,status:"pending",has_preview:false};
        result={revision:clips[0].revision,run_id:"render-run"};
      }else if(url==="/api/runs/render-run"){
        if(failPoll)throw new Error("Network unavailable");
        result={state:runState,clips:clips.map(c=>({...c}))};
      }else throw new Error(`Unexpected API request: ${url}`);
      return {ok:true,json:async()=>result};
    },
  });
  for(const file of ["app.js","gallery.js","review.js"])vm.runInContext(fs.readFileSync(path.join(staticPath,file),"utf8"),context);
  await new Promise(setImmediate);
  vm.runInContext('goView("review")',context);await new Promise(setImmediate);
  const select=nodes.get("review-layout"),button=nodes.get("review-rerender");
  assert.ok(find(select,"Alternate camera"));assert.equal(button.disabled,false,"Pacing/encoding can change without changing layout");
  select.value="alternate";select.onchange();assert.equal(button.disabled,false);
  await button.onclick({detail:2});assert.equal(writes.length,0);
  await button.onclick({detail:1});
  assert.equal(nodes.get("review-title").textContent,"Moment 0");assert.equal(select.value,"alternate");
  assert.match(nodes.get("review-message").textContent,/Your place in the queue has been kept/);
  failRender=false;
  await button.onclick({detail:1});
  assert.equal(nodes.get("review-title").textContent,"Moment 0","Rerender must not advance the queue");
  assert.equal(nodes.get("review-progress").textContent,"2 left");
  assert.equal(player.hidden,true,"Never show the old preview as the new revision");
  assert.equal(nodes.get("review-empty-title").textContent,"Rendering new revision");
  for(const id of ["review-approve","review-reject","review-skip","review-rerender","review-layout"])assert.equal(nodes.get(id).disabled,true,id);
  await vm.runInContext('decideReview("approved");skipReview();undoReview();rerenderReview();',context);
  assert.equal(writes.length,2,"Do not allow review actions while rendering");
  failPoll=true;await vm.runInContext('pollReviewRender()',context);
  assert.match(nodes.get("review-message").textContent,/Retrying automatically/);
  assert.equal(nodes.get("review-approve").disabled,true);
  failPoll=false;
  clips[0]={...clips[0],status:"ready",has_preview:true};runState="completed";
  await timers.at(-1)();
  assert.equal(nodes.get("review-title").textContent,"Moment 0");
  assert.equal(player.src,"/api/artifacts/clip-0/video?revision=2");
  assert.equal(player.hidden,false);assert.equal(nodes.get("review-approve").disabled,false);
  assert.equal(select.value,"alternate");
  assert.match(nodes.get("review-message").textContent,/New revision ready/);
  assert.equal(clips[0].review_status,"unreviewed");
  await button.onclick({detail:1});
  clips[0]={...clips[0],status:"held",flags:["Camera crop needs attention."]};
  await vm.runInContext('pollReviewRender()',context);
  assert.equal(nodes.get("review-title").textContent,"Moment 0");
  assert.equal(nodes.get("review-empty-title").textContent,"Render needs attention");
  assert.equal(nodes.get("review-approve").disabled,true);
  assert.equal(nodes.get("review-render-run").hidden,false);
  assert.equal(nodes.get("review-notes").disabled,false);
  presets=[];await nodes.get("refresh-review").onclick();
  assert.equal(select.disabled,true);assert.equal(button.disabled,false);
  assert.ok(find(select,"Current layout"));
});

test("manual review queue advances only after saving, supports undo and guards shortcuts", async()=>{
  const staticPath=path.join(__dirname,"../src/vaarattu_shorts/static");
  const html=fs.readFileSync(path.join(staticPath,"index.html"),"utf8");
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const player=nodes.get("review-player");
  player.play=async()=>{player.paused=false;};player.pause=()=>{player.paused=true;};
  player.load=()=>{};player.removeAttribute=name=>{delete player[name];};
  const clips=Array.from({length:6},(_,i)=>({id:`clip-${i}`,run_id:"run",title:`Moment ${i}`,
    status:"ready",has_preview:true,review_status:"unreviewed",revision:1,start_us:0,end_us:15000000}));
  clips[3].status="held";clips[4].review_status="approved";clips[5].has_preview=false;
  let keydown, releaseSave, failSave=false, holdSave=false, failLoad=false;
  const writes=[];
  const context=vm.createContext({
    document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag),
      addEventListener:(name,fn)=>{if(name==="keydown")keydown=fn;}},
    fetch:async(url,options)=>{
      let result;
      if(url==="/api/status")result={token:"fixture",models:{turbo:true},providers:{}};
      else if(url==="/api/layouts"||url==="/api/runs")result=[];
      else if(url==="/api/videos")result={configured:false,videos:[]};
      else if(url==="/api/clips"){
        if(failLoad)return {ok:false,json:async()=>({detail:"Unable to load clips."})};
        result=clips.map(c=>({...c}));
      }else if(url.endsWith("/review")){
        const body=JSON.parse(options.body);writes.push(body);
        if(holdSave)await new Promise(resolve=>{releaseSave=resolve;});
        if(failSave)return {ok:false,json:async()=>({detail:"This clip changed. Reload it before reviewing."})};
        const clip=clips.find(c=>url===`/api/clips/${c.id}/review`);
        assert.equal(body.expected_revision,clip.revision);
        clip.review_status=body.status;if(body.note!==undefined)clip.review_note=body.note.trim();result={...clip};
      }else throw new Error(`Unexpected API request: ${url}`);
      return {ok:true,json:async()=>result};
    },
  });
  for(const file of ["app.js","gallery.js","review.js"])vm.runInContext(fs.readFileSync(path.join(staticPath,file),"utf8"),context);
  await new Promise(setImmediate);
  vm.runInContext('goView("review")',context);await new Promise(setImmediate);
  assert.equal(nodes.get("view-review").hidden,false);
  assert.equal(nodes.get("review-progress").textContent,"3 left","Only finished unreviewed previews enter the queue");
  assert.equal(nodes.get("review-title").textContent,"Moment 0");
  assert.equal(player.src,"/api/artifacts/clip-0/video?revision=1");
  const reason=nodes.get("review-rejection-reason");
  assert.equal(reason.disabled,false);
  reason.value="Only save this if rejected";
  const key=(value,extra={})=>keydown({key:value,code:value===" "?"Space":"",target:{closest:()=>null},preventDefault(){},...extra});
  key("a",{repeat:true});key("a",{ctrlKey:true});key("a",{target:{closest:()=>true}});
  nodes.get("clip-notes-dialog").open=true;key("a");nodes.get("clip-notes-dialog").open=false;
  assert.equal(writes.length,0);
  await nodes.get("review-approve").onclick({detail:2});assert.equal(writes.length,0,"Ignore the second click of a double click");
  holdSave=true;
  const saving=nodes.get("review-approve").onclick({detail:1});
  assert.equal(nodes.get("review-title").textContent,"Moment 0","Do not advance before a successful save");
  assert.equal(nodes.get("review-reject").disabled,true);
  assert.equal(reason.disabled,true);
  key("r");key("s");key("u");
  assert.equal(writes.length,1,"Saving blocks additional decisions and navigation");
  releaseSave();holdSave=false;await saving;
  assert.equal(clips[0].review_status,"approved");
  assert.equal(writes[0].note,undefined,"Approval does not save a draft rejection reason");
  assert.equal(reason.value,"","A reason must not leak into the next clip");
  assert.equal(nodes.get("review-title").textContent,"Moment 1");
  assert.equal(nodes.get("review-undo").disabled,false);
  await nodes.get("review-undo").onclick();
  assert.equal(clips[0].review_status,"unreviewed");
  assert.equal(nodes.get("review-title").textContent,"Moment 0");
  assert.equal(nodes.get("review-undo").disabled,true);
  nodes.get("review-notes").onclick();
  nodes.get("review-reason").value="Missing context";
  await nodes.get("save-review-reason").onclick();
  nodes.get("close-clip-notes").onclick();
  assert.equal(reason.value,"Missing context","Notes dialog saves update the queue reason");
  reason.value="Too short; missing the setup";
  const beforeTyping=writes.length;
  key("r",{target:{closest:selector=>selector.includes("textarea")?reason:null}});
  assert.equal(writes.length,beforeTyping,"Typing in the reason must not reject the clip");
  failSave=true;
  await nodes.get("review-reject").onclick({detail:1});
  assert.equal(nodes.get("review-title").textContent,"Moment 0");
  assert.match(nodes.get("review-message").textContent,/Your place in the queue has been kept/);
  assert.equal(nodes.get("review-approve").disabled,false);
  assert.equal(reason.value,"Too short; missing the setup","Failed saves preserve the draft");
  failSave=false;key("r");await new Promise(setImmediate);
  assert.equal(clips[0].review_status,"not_approved");
  assert.equal(clips[0].review_note,"Too short; missing the setup");
  assert.equal(reason.value,"");
  await nodes.get("review-undo").onclick();
  assert.equal(reason.value,"Too short; missing the setup","Undo restores the saved reason for editing");
  reason.value="";
  await nodes.get("review-reject").onclick({detail:1});
  assert.equal(clips[0].review_status,"not_approved","A reason is optional");
  assert.equal(clips[0].review_note,"","An existing reason can be cleared");
  const beforeSkip=writes.length;key("s");
  assert.equal(writes.length,beforeSkip);assert.equal(clips[1].review_status,"unreviewed");
  assert.equal(nodes.get("review-title").textContent,"Moment 2");
  key(" ");assert.equal(player.paused,true);key(" ");assert.equal(player.paused,false);
  key(" ",{target:{closest:selector=>selector==="button"?{}:null}});
  assert.equal(player.paused,true,"Space controls playback even after clicking Skip");
  key("a");await new Promise(setImmediate);
  assert.equal(nodes.get("review-empty").hidden,false);
  assert.equal(nodes.get("review-empty-title").textContent,"Remaining clips skipped");
  assert.equal(nodes.get("review-approve").disabled,true);
  assert.equal(reason.disabled,true);
  assert.equal(player.src,undefined);
  await nodes.get("refresh-review").onclick();
  assert.equal(nodes.get("review-title").textContent,"Moment 1","Refresh returns skipped clips");
  key("a");await new Promise(setImmediate);
  assert.equal(nodes.get("review-empty-title").textContent,"You're all caught up");
  await nodes.get("review-undo").onclick();
  assert.equal(nodes.get("review-title").textContent,"Moment 1","Undo works after finishing the queue");
  failLoad=true;await nodes.get("refresh-review").onclick();
  assert.equal(nodes.get("review-title").textContent,"Moment 1");
  assert.equal(nodes.get("review-message").textContent,"Unable to load clips.");
  vm.runInContext('goView("process")',context);
  assert.equal(player.paused,true,"Leaving review stops playback");
  const beforeLeave=writes.length;key("a");assert.equal(writes.length,beforeLeave);
});

test("gallery reviews persist in the UI, filter across runs and preserve playback", async()=>{
  const staticPath=path.join(__dirname,"../src/vaarattu_shorts/static");
  const html=fs.readFileSync(path.join(staticPath,"index.html"),"utf8");
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  nodes.get("gallery-filter").value="all";
  nodes.get("video-filter").value="all";
  const clips=Array.from({length:14},(_,i)=>({id:`clip-${i}`,run_id:`run-${i%2}`,title:`Moment ${i}`,
    status:"ready",review_status:"unreviewed",start_us:0,end_us:15000000,revision:1,has_preview:true,
    source_url:`https://www.youtube.com/watch?v=source-${i%2}`}));
  const calls=[];let failReview=false;
  const context=vm.createContext({
    document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag)},
    fetch:async(url,options)=>{
      calls.push({url,options});let result;
      if(url==="/api/status")result={token:"fixture",models:{turbo:true},providers:{}};
      else if(url==="/api/layouts"||url==="/api/runs")result=[];
      else if(url==="/api/videos")result={configured:false,videos:[{id:"source-1",title:"Second recording",url:clips[1].source_url}]};
      else if(url==="/api/clips")result=clips.map(c=>({...c}));
      else if(url.endsWith("/review")){
        if(failReview)return {ok:false,json:async()=>({detail:"Review could not be saved."})};
        const body=JSON.parse(options.body),clip=clips.find(c=>url===`/api/clips/${c.id}/review`);
        assert.equal(options.headers["X-Local-Token"],"fixture");assert.equal(body.expected_revision,1);
        clip.review_status=body.status;if(body.note!==undefined)clip.review_note=body.note.trim();result={...clip};
      }else throw new Error(`Unexpected API request: ${url}`);
      return {ok:true,json:async()=>result};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(staticPath,"app.js"),"utf8"),context);
  vm.runInContext(fs.readFileSync(path.join(staticPath,"gallery.js"),"utf8"),context);
  await new Promise(setImmediate);
  vm.runInContext('goView("gallery")',context);
  await new Promise(setImmediate);
  assert.equal(nodes.get("view-gallery").hidden,false);
  assert.equal(nodes.get("nav-gallery")["aria-current"],"page");
  const grid=nodes.get("gallery-clips"),first=grid.children[0],video=first.children[0];
  video.currentTime=5;
  assert.equal(grid.children.length,12);
  assert.equal(nodes.get("gallery-page").textContent,"1–12 of 14 clips");
  await find(first,"Approve").onclick();
  assert.equal(clips[0].review_status,"approved");
  assert.ok(find(first,"Approved"));
  assert.equal(grid.children[0],first);
  assert.equal(first.children[0],video);assert.equal(video.currentTime,5);
  await nodes.get("refresh-gallery").onclick();
  assert.equal(grid.children[0],first,"Refreshing unchanged clips preserves preview nodes");
  await find(first,"Not approved").onclick();
  assert.equal(clips[0].review_status,"not_approved");
  find(first,"Review reason").onclick();
  nodes.get("review-reason").value="Needs more context";
  await nodes.get("save-review-reason").onclick();
  assert.equal(clips[0].review_status,"not_approved","Saving a reason does not approve or clear the clip");
  assert.equal(clips[0].review_note,"Needs more context");
  assert.equal(nodes.get("review-reason-message").textContent,"Reason saved.");
  nodes.get("close-clip-notes").onclick();
  await nodes.get("refresh-gallery").onclick();
  find(first,"Review reason").onclick();
  assert.equal(nodes.get("review-reason").value,"Needs more context");
  failReview=true;nodes.get("review-reason").value="Keep this unsaved draft";
  await nodes.get("save-review-reason").onclick();
  assert.equal(nodes.get("review-reason").value,"Keep this unsaved draft");
  assert.equal(nodes.get("review-reason-message").textContent,"Review could not be saved.");
  assert.equal(clips[0].review_note,"Needs more context");
  failReview=false;nodes.get("close-clip-notes").onclick();
  await find(first,"Clear review").onclick();
  assert.equal(clips[0].review_status,"unreviewed");
  assert.equal(clips[0].review_note,"Needs more context");
  failReview=true;
  await find(first,"Approve").onclick();
  assert.ok(find(first,"Unreviewed"));assert.equal(find(first,"Approve").disabled,false);
  assert.equal(nodes.get("error").textContent,"Review could not be saved.");
  failReview=false;
  const before=calls.length;
  nodes.get("next-gallery").onclick();
  assert.equal(grid.children.length,2);assert.equal(nodes.get("gallery-page").textContent,"13–14 of 14 clips");
  nodes.get("previous-gallery").onclick();
  nodes.get("gallery-search").value="Second recording";nodes.get("gallery-search").oninput();
  assert.equal(grid.children.length,7);assert.ok(find(grid,"Moment 13"));
  assert.equal(calls.length,before,"Search and pagination stay local");
  nodes.get("gallery-search").value="";nodes.get("gallery-filter").value="unreviewed";nodes.get("gallery-filter").oninput();
  await find(grid.children[0],"Approve").onclick();
  assert.equal(find(grid,"Moment 0"),undefined,"Reviewed clips leave the Unreviewed filter");
  nodes.get("gallery-filter").value="approved";nodes.get("gallery-filter").oninput();
  assert.equal(grid.children.length,1);assert.ok(find(grid,"Moment 0"));
  clips[0].revision=2;clips[0].review_status="unreviewed";
  await nodes.get("refresh-gallery").onclick();
  assert.ok(find(grid,"No clips match these filters."));
  clips.length=0;
  await nodes.get("refresh-gallery").onclick();
  assert.ok(find(grid,"No rendered clips yet."));
});

test("channel selection, local filters and explicit page fetching", async()=>{
  const staticPath=path.join(__dirname,"../src/vaarattu_shorts/static");
  const html=fs.readFileSync(path.join(staticPath,"index.html"),"utf8");
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  nodes.get("video-filter").value="all";
  const videos=[
    {id:"abc_def-ghI",title:"Uusi keskustelu",published:"2026-09-01T12:00:00Z",duration:3600,
     available:true,processed:false,state:"not_started",url:"https://www.youtube.com/watch?v=abc_def-ghI"},
    {id:"0123456789_",title:"Vanha keskustelu",published:"2026-08-01T12:00:00Z",duration:3600,
     available:true,processed:true,state:"completed",outcome:"no_candidates",url:"https://www.youtube.com/watch?v=0123456789_"},
  ];
  const library={configured:true,channel_id:"UCfixture",channel_title:"VaarattuVODs",has_older:true,fetched_at:1,videos};
  const calls=[];
  const context=vm.createContext({
    document:{getElementById(id){assert.ok(nodes.has(id),`Missing HTML element: ${id}`);return nodes.get(id);},
              createElement:tag=>new Element(tag)},
    fetch:async(url,options)=>{
      calls.push({url,method:options.method||"GET"});
      const responses={"/api/status":{token:"fixture",models:{turbo:true},providers:{}},
                       "/api/layouts":[],"/api/runs":[],"/api/videos":library,
                       "/api/videos/older":{...library,has_older:false}};
      assert.ok(url in responses,`Unexpected API request: ${url}`);
      return {ok:true,json:async()=>responses[url]};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(staticPath,"app.js"),"utf8"),context);
  await new Promise(setImmediate);
  const list=nodes.get("channel-videos");
  assert.equal(list.children.length,2);
  assert.ok(calls.every(c=>c.method==="GET"));
  await find(list,"Select video").onclick();
  assert.equal(nodes.get("video").value,videos[0].url);
  assert.equal(nodes.get("view-process").hidden,false);
  assert.equal(nodes.get("view-library").hidden,true);
  assert.equal(nodes.get("nav-process")["aria-current"],"page");
  assert.ok(find(nodes.get("selected-video"),videos[0].title));
  assert.ok(calls.every(c=>c.method==="GET"),"Selection must not queue a run");
  const before=calls.length;
  nodes.get("video-filter").value="processed";
  nodes.get("video-filter").oninput();
  assert.equal(list.children.length,1);
  assert.ok(find(list,"Vanha keskustelu"));
  assert.ok(find(list,"Processed · No suitable clips"));
  await find(list,"Select again").onclick();
  assert.equal(nodes.get("video").value,videos[1].url);
  nodes.get("video-search").value="no match";
  nodes.get("video-search").oninput();
  assert.ok(find(list,"No saved videos match these filters."));
  assert.equal(calls.length,before,"Filtering must not use API quota");
  await nodes.get("older-videos").onclick();
  assert.deepEqual(calls.at(-1),{url:"/api/videos/older",method:"POST"});
  assert.equal(nodes.get("older-videos").hidden,true);
  nodes.get("video-search").value="";
  nodes.get("video-filter").value="all";
  for(let i=0;i<9;i++)videos.push({...videos[0],id:`fixture-${i}`,title:`Recording ${i}`});
  nodes.get("video-filter").oninput();
  assert.equal(list.children.length,8);
  assert.equal(nodes.get("video-page").textContent,"1–8 of 11 videos");
  const pageCalls=calls.length;
  nodes.get("show-videos").onclick();
  assert.equal(list.children.length,3);
  assert.equal(nodes.get("video-page").textContent,"9–11 of 11 videos");
  assert.equal(nodes.get("show-videos").hidden,true);
  nodes.get("previous-videos").onclick();
  assert.equal(list.children.length,8);
  assert.equal(calls.length,pageCalls,"Paging saved recordings must not call the API");
  nodes.get("video-search").value="Recording 8";
  nodes.get("video-search").oninput();
  assert.equal(list.children.length,1);
  assert.equal(nodes.get("previous-videos").hidden,true);
  assert.equal(nodes.get("video-page").textContent,"1–1 of 1 videos");
  vm.runInContext('editing={};goView("editor");goView("layouts");',context);
  assert.equal(nodes.get("layout-return").href,"#editor");
  vm.runInContext('goView("process");goView("layouts");',context);
  assert.equal(nodes.get("layout-return").href,"#process");
  assert.equal(nodes.get("video").value,videos[1].url,"Navigation must preserve the selected source");
});

test("Codex selection disables dollar cap and renders unknown costs", async()=>{
  const staticPath=path.join(__dirname,"../src/vaarattu_shorts/static");
  const html=fs.readFileSync(path.join(staticPath,"index.html"),"utf8");
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const run={id:"fixture-run",state:"completed",stage:"complete",message:"",clips:[],
    config:{video:"abc_def-ghI",provider:"codex",final_transcription:true,codex:{model:"gpt-5.6-luna"}},result:{coverage:"complete"},
    usage:{estimated_cost_usd:null,reserved_usd:null,budget_usd:null,request_count:1,input_tokens:800,
      output_tokens:120,cached_input_tokens:100,reasoning_tokens:20,unreported_requests:0,
      reported_counts:{cached_input_tokens:1,reasoning_tokens:1}}};
  let queued=false, submitted;
  const context=vm.createContext({
    crypto:{randomUUID:()=>"fixture-request"},
    setTimeout:()=>0,
    document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag)},
    fetch:async(url,options)=>{
      let result;
      if(url==="/api/status")result={token:"fixture",max_concurrent_jobs:2,models:{turbo:true,"large-v3":true},providers:{
        codex:{model:"gpt-5.6-luna",configured:true,available:true}}};
      else if(url==="/api/concurrency"){
        assert.equal(options.method,"POST");
        assert.deepEqual(JSON.parse(options.body),{max_concurrent_jobs:3});
        result={max_concurrent_jobs:3};
      }
      else if(url==="/api/layouts")result=[];
      else if(url==="/api/videos")result={configured:false,videos:[]};
      else if(url==="/api/runs"&&options.method==="POST"){
        queued=true;submitted=JSON.parse(options.body);result={id:run.id};
      }else if(url==="/api/runs")result=queued?[run]:[];
      else if(url==="/api/runs/fixture-run")result=run;
      else throw new Error(`Unexpected API request: ${url}`);
      return {ok:true,json:async()=>result};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(staticPath,"app.js"),"utf8"),context);
  await new Promise(setImmediate);
  assert.equal(nodes.get("job-concurrency").value,"2");
  assert.equal(nodes.get("final-transcription").disabled,false);
  nodes.get("final-transcription").checked=true;
  nodes.get("job-concurrency").value="3";
  await nodes.get("job-concurrency").onchange();
  assert.equal(nodes.get("job-concurrency").disabled,false);
  assert.ok(find(nodes.get("provider"),"Codex · gpt-5.6-luna"));
  nodes.get("provider").value="codex";
  nodes.get("provider").onchange();
  assert.equal(nodes.get("budget").disabled,true);
  assert.equal(nodes.get("codex-note").hidden,false);
  assert.equal(nodes.get("local-settings").hidden,true);
  assert.equal(nodes.get("budget-field").hidden,true);
  nodes.get("budget").value="9";
  nodes.get("video").value="abc_def-ghI";
  await nodes.get("run-form").onsubmit({preventDefault(){}});
  assert.equal(submitted.provider,"codex");
  assert.equal(submitted.final_transcription,true);
  assert.ok(find(nodes.get("run-detail"),"Final clip captions: large-v3 · selected sections only."));
  assert.equal(submitted.budget_usd,0);
  assert.equal(nodes.get("error").textContent,"");
  assert.equal(nodes.get("view-results").hidden,false);
  assert.equal(nodes.get("view-process").hidden,true);
  assert.ok(find(nodes.get("run-detail"),"Codex · gpt-5.6-luna · subscription usage · monetary cost unavailable."));
  nodes.get("provider").value="openai";
  nodes.get("provider").onchange();
  assert.equal(nodes.get("budget").disabled,false);
  assert.equal(nodes.get("budget-field").hidden,false);
  run.clips=Array.from({length:12},(_,i)=>({id:`clip-${i}`,title:`Moment ${i}`,status:"pending",
    start_us:0,end_us:15000000,revision:1,has_preview:false,source_url:"https://www.youtube.com/watch?v=abc_def-ghI"}));
  await vm.runInContext('detail()',context);
  assert.equal(nodes.get("clips").children.length,10);
  assert.equal(nodes.get("clip-page").textContent,"1–10 of 12 clips");
  const firstClip=nodes.get("clips").children[0];
  run.message="Updated run status";
  await vm.runInContext('detail()',context);
  assert.equal(nodes.get("clips").children[0],firstClip,"Progress updates must preserve existing previews and focus");
  nodes.get("next-clips").onclick();
  assert.equal(nodes.get("clips").children.length,2);
  assert.ok(find(nodes.get("clips"),"Moment 10"));
  assert.equal(nodes.get("next-clips").hidden,true);
  nodes.get("previous-clips").onclick();
  assert.ok(find(nodes.get("clips"),"Moment 0"));
  run.state="running";
  await vm.runInContext('refresh()',context);
  assert.equal(nodes.get("run-button").disabled,false,"Active jobs must not block submitting another video");
  assert.equal(nodes.get("run-availability").hidden,false);
  run.state="paused";
  await vm.runInContext('refresh()',context);
  assert.equal(nodes.get("run-button").disabled,false);
  assert.equal(nodes.get("run-availability").hidden,true,"Paused jobs do not occupy a processing slot");
});
