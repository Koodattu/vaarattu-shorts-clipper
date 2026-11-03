const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
class Element{
  constructor(tag="div"){this.tagName=tag;this.children=[];this.value="";this.checked=false;this.open=false;this.style={};this.currentTime=4;}
  get options(){return this.children;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(key,value){this[key]=value;}
  removeAttribute(key){delete this[key];}
  showModal(){this.open=true;}
  close(){this.open=false;this.onclose?.();}
  pause(){this.paused=true;}
  load(){}
  focus(){}
  querySelectorAll(selector){return this.children.flatMap(c=>[...(c.type==="checkbox"&&c.checked?[c]:[]),...c.querySelectorAll(selector)]);}
}
function fixture(){
  const dir=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(dir,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const clip={analysis_defaults:{provider:"codex",budget_usd:0,local_model:"gemma4-31b"},id:"edited",run_id:"run",revision:2,title:"Selected clip",status:"ready",has_preview:true,has_source:true,section_origin_us:0,
    start_us:5e6,end_us:15e6,review_status:"approved",layout:{},words:[{id:"a",text:"Hei",start_us:6e6,end_us:7e6},{id:"b",text:"maailma",start_us:12e6,end_us:13e6}],flags:[]};
  const state={clip,runState:"running",fail:null,hold:null,contextUnchanged:false},calls=[],errors=[],timers=[],views=[];
  const context=vm.createContext({
    labels:{codex:"Codex gpt-6-luna",openai:"OpenAI API - gpt-6-luna",local:"Local · Gemma 4"},
    $:id=>nodes.get(id),editing:null,currentView:"editor",savedLayouts:[{id:"layout",body:{name:"Layout"}}],sameLayout:()=>true,
    document:{createElement:tag=>new Element(tag),addEventListener(){}},text:(tag,value)=>Object.assign(new Element(tag),{textContent:value}),
    action:(label,onclick)=>Object.assign(new Element("button"),{textContent:label,onclick}),error:message=>errors.push(message),library:null,
    setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},detail:async()=>{},paintRuns(){},refresh:async()=>{},
    goView:view=>{context.currentView=view;views.push(view);},
    api:async(url,options)=>{
      const body=options?JSON.parse(options.body):null;calls.push({url,body});
      if(state.hold&&url===state.hold.url)await state.hold.promise;
      if(state.fail===url)throw new Error("Request failed. Try again.");
      if(!options){
        if(url==="/api/clips/other")return {...clip,id:"other",title:"Other clip"};
        if(url==="/api/runs/run")return {state:state.runState,message:state.runMessage,intent:state.runIntent,clips:[structuredClone(state.clip)]};
        if(url.endsWith("/trim"))return {clip:structuredClone(state.clip),words:clip.words,min_us:0,max_us:20e6,origin_us:0};
        assert.equal(url,"/api/clips/edited");return structuredClone(state.clip);
      }
      assert.ok(url.startsWith("/api/clips/edited/"),"Actions must use the editor clip, not the review queue");
      if(url.endsWith("/review")){state.clip={...state.clip,review_status:body.status,review_note:body.note||""};return structuredClone(state.clip);}
      if(url.endsWith("/context-recovery")){
        if(body.action==="discard")state.clip.context_request.status="cancelled";
        if(body.action==="retry"){state.runState="queued";state.clip.context_request.id="retry-context";}
        if(body.action==="stop")state.runIntent="cancel";
        state.clip.context_job={state:state.runState,intent:state.runIntent,message:state.runMessage};
        return structuredClone(state.clip);
      }
      if(url.endsWith("/context")){
        state.clip={...state.clip,context_request:{id:"context",status:state.contextUnchanged?"unchanged":"pending",reason:state.contextUnchanged?"The question is already included.":""}};
        return {run_id:"run",revision:2,request_id:"context"};
      }
      if(url.endsWith("/tighten")||url.endsWith("/instructed-edit")){
        state.clip={...state.clip,tighten_check:{id:"cuts",status:"complete",mode:url.endsWith("/instructed-edit")?"instructed":"suggest",summary:"One detour",cuts:[{start_us:8e6,end_us:10e6,text:"Detour",reason:"Unrelated",before:"Hei",after:"maailma"}]}};return {request_id:"cuts"};
      }
      if(url.endsWith("/caption-check")){state.clip={...state.clip,caption_check:{id:"captions",status:"complete",changes:[{word_id:"a",original:"Hei",replacement:"Moi",reason:"Heard clearly"}]}};return {request_id:"captions"};}
      state.clip={...state.clip,status:"pending",has_preview:false,revision:3};return {run_id:"run",revision:3};
    },
  });
  for(const name of ["gallery","review","captions","manual-captions","clip-trim","tighten","editor"])vm.runInContext(fs.readFileSync(path.join(dir,name+".js"),"utf8"),context);
  vm.runInContext('reviewQueue=[{id:"queue-clip",revision:7,status:"ready",has_preview:true}];reviewLast={id:"previous"};',context);
  const queue=()=>JSON.parse(vm.runInContext('JSON.stringify([reviewQueue,reviewLast,reviewRendering,reviewBusy])',context));
  const open=()=>context.openEditor("edited");
  const finish=async()=>{state.clip={...state.clip,status:"ready",has_preview:true,previous_revision:2,words:clip.words};await context.pollEditorRender();};
  return {nodes,state,context,calls,errors,timers,views,queue,open,finish};
}
const submit=(f,id)=>f.nodes.get(id).onsubmit({preventDefault(){}});
test("editor offers the shared tools and decisions on its own clip without changing the review queue",async()=>{
  const f=fixture();await f.open();const before=f.queue();
  assert.match(f.nodes.get("editor-player").src,/edited\/video\?revision=2/);
  for(const id of ["edit-trim","edit-context","edit-instructed-edit","edit-caption-edit","edit-caption-check","edit-ready"])assert.equal(f.nodes.get(id).disabled,false);
  f.nodes.get("edit-notes").onclick();assert.equal(f.nodes.get("clip-notes-dialog").open,true);f.nodes.get("close-clip-notes").onclick();
  for(const [id,status] of [["edit-approve","approved"],["edit-reject","not_approved"],["edit-ready","ready_to_post"]]){
    await f.nodes.get(id).onclick();assert.equal(f.state.clip.review_status,status);assert.equal(f.nodes.get(id)["aria-pressed"],"true");
  }
  assert.deepEqual(f.queue(),before);assert.equal(f.context.currentView,"editor");
});
test("context lookup preserves direction and notes, then loads the new revision in the same editor",async()=>{
  const f=fixture();await f.open();const before=f.queue();f.nodes.get("editor-back").href="#gallery";
  f.nodes.get("edit-context").onclick();f.nodes.get("review-context-before").checked=true;f.nodes.get("review-context-note").value="Include the question";
  await submit(f,"review-context-form");
  assert.deepEqual(f.calls.find(c=>c.body).body,{expected_revision:2,before:true,after:false,note:"Include the question"});
  assert.equal(f.nodes.get("editor-fields").disabled,true);assert.equal(f.nodes.get("edit-ready").disabled,true);
  f.state.clip={...f.state.clip,revision:3,context_request:{id:"context",status:"revised"},title:"With context"};await f.finish();
  assert.equal(f.context.editing.revision,3);assert.equal(f.nodes.get("editor-title").textContent,"With context");
  assert.match(f.nodes.get("editor-player").src,/revision=3/);assert.equal(f.nodes.get("editor-back").href,"#gallery");
  assert.equal(f.nodes.get("editor-fields").disabled,false);assert.deepEqual(f.queue(),before);
});
test("unchanged context keeps the original clip and explains why; failed submit keeps the note",async()=>{
  const f=fixture();await f.open();f.nodes.get("edit-context").onclick();f.nodes.get("review-context-note").value="Keep this note";
  f.state.fail="/api/clips/edited/context";await submit(f,"review-context-form");
  assert.equal(f.nodes.get("review-context-dialog").open,true);assert.equal(f.nodes.get("review-context-note").value,"Keep this note");
  f.state.fail=null;f.state.contextUnchanged=true;await submit(f,"review-context-form");
  assert.match(f.nodes.get("editor-message").textContent,/Original clip kept.*question/);assert.equal(f.context.editing.revision,2);
});
test("timeline trim and quick captions keep the editor open and leave queue state untouched",async()=>{
  for(const tool of ["trim","caption-edit"]){
    const f=fixture();await f.open();const before=f.queue();await f.nodes.get("edit-"+tool).onclick();
    if(tool==="trim"){
      f.nodes.get("trim-player").onloadedmetadata();f.nodes.get("trim-start").value=4;f.nodes.get("trim-start").onchange();await submit(f,"trim-form");
      assert.equal(f.calls.find(c=>c.body).body.start_us,4e6);
    }else{
      const input=f.nodes.get("manual-caption-words").children[0];input.value="Moi";input.oninput();await submit(f,"manual-caption-form");
      assert.equal(f.calls.find(c=>c.body).body.words[0].text,"Moi");
    }
    assert.deepEqual(f.queue(),before);assert.equal(f.context.currentView,"editor");await f.finish();assert.equal(f.context.editing.revision,3);
  }
});
test("suggested cuts, instructed edits and caption corrections return their render to the editor",async()=>{
  for(const tool of ["tighten","instructed-edit","caption-check"]){
    const f=fixture();await f.open();const before=f.queue();await f.nodes.get("edit-"+tool).onclick();
    if(tool==="caption-check"){await f.nodes.get("caption-check-again").onclick();await f.nodes.get("caption-apply").onclick();}
    else{f.nodes.get("tighten-note").value="Remove the detour";await f.nodes.get("tighten-request").onclick();await f.nodes.get("tighten-apply").onclick();}
    assert.ok(f.calls.some(c=>c.url===`/api/clips/edited/${tool}`&&c.body));
    assert.deepEqual(f.queue(),before);await f.finish();assert.equal(f.context.editing.revision,3);assert.equal(f.context.currentView,"editor");
  }
});
test("unsaved editor changes block tools, review decisions and refresh without losing the draft",async()=>{
  const f=fixture();await f.open();f.nodes.get("edit-title").value="Unsaved title";const count=f.calls.length;
  for(const id of ["edit-trim","edit-context","edit-tighten","edit-instructed-edit","edit-caption-edit","edit-caption-check","edit-ready","refresh-editor"])await f.nodes.get(id).onclick();
  assert.equal(f.calls.length,count);assert.equal(f.nodes.get("edit-title").value,"Unsaved title");assert.equal(f.errors.length,8);
  f.state.fail="/api/clips/edited/edit";await submit(f,"edit-form");assert.equal(f.nodes.get("edit-title").value,"Unsaved title");assert.equal(f.nodes.get("editor-fields").disabled,false);
});
test("loading saved-clip checks locks the form and releases it on failure",async()=>{
  for(const tool of ["tighten","caption-check"]){
    const f=fixture();await f.open();let resolve;
    f.state.hold={url:"/api/clips/edited",promise:new Promise(done=>resolve=done)};f.state.fail="/api/clips/edited";
    const opening=f.nodes.get("edit-"+tool).onclick();assert.equal(f.nodes.get("editor-fields").disabled,true);
    resolve();await opening;assert.equal(f.nodes.get("editor-fields").disabled,false);assert.match(f.errors[0],/Request failed/);
  }
});
test("form saves prevent duplicate renders and update the preview without leaving the editor",async()=>{
  const f=fixture();await f.open();f.nodes.get("edit-title").value="New title";f.nodes.get("reviewed").checked=true;
  let resolve;f.state.hold={url:"/api/clips/edited/edit",promise:new Promise(done=>resolve=done)};
  const saving=submit(f,"edit-form");await submit(f,"edit-form");assert.equal(f.calls.filter(c=>c.body).length,1);resolve();await saving;
  assert.equal(f.calls.find(c=>c.body).body.title,"New title");assert.equal(f.nodes.get("editor-fields").disabled,true);
  await f.finish();assert.equal(f.context.currentView,"editor");assert.equal(f.nodes.get("editor-fields").disabled,false);
});
test("polling retries errors, reports stopped work and ignores a late response after switching clips",async()=>{
  const f=fixture();await f.open();f.state.clip={...f.state.clip,revision:3,status:"pending",has_preview:false};f.state.fail="/api/runs/run";
  await f.context.followEditorRevision(f.context.editing,{run_id:"run",revision:3});assert.match(f.nodes.get("editor-message").textContent,/Retrying/);
  f.state.fail=null;f.state.runState="paused";await f.context.pollEditorRender();assert.match(f.nodes.get("editor-message").textContent,/needs attention/);
  f.state.runState="running";let resolve;f.state.hold={url:"/api/runs/run",promise:new Promise(done=>resolve=done)};
  const polling=f.context.followEditorRevision(f.context.editing,{run_id:"run",revision:3});await f.context.openEditor("other");resolve();await polling;
  assert.equal(f.context.editing.id,"other");assert.equal(f.nodes.get("editor-title").textContent,"Other clip");
});

for(const [button,submitButton,path] of [
  ["edit-context",null,"context"],
  ["edit-tighten","tighten-request","tighten"],
  ["edit-instructed-edit","tighten-request","instructed-edit"],
  ["edit-caption-check","caption-check-again","caption-check"],
])test(`editor model selection reaches ${path} without changing the original run`,async()=>{
  const f=fixture();await f.open();
  f.nodes.get("editor-provider").value="openai";f.nodes.get("editor-analysis-budget").value="2.5";
  f.nodes.get("editor-provider").onchange();
  assert.equal(f.nodes.get("editor-analysis-budget-field").hidden,false);
  await f.nodes.get(button).onclick();f.nodes.get("tighten-note").value="Keep the point";
  if(submitButton)await f.nodes.get(submitButton).onclick();else await submit(f,"review-context-form");
  const request=f.calls.find(c=>c.url===`/api/clips/edited/${path}`&&c.body);
  assert.deepEqual(request.body.analysis,{provider:"openai",budget_usd:2.5});
  assert.equal(f.state.clip.analysis_defaults.provider,"codex");
});
test("model selection survives refresh and renders but resets for another clip",async()=>{
  const f=fixture();await f.open();
  assert.deepEqual(JSON.parse(JSON.stringify(f.context.editorAnalysisRequest())),{});
  f.nodes.get("editor-provider").value="openai";await f.nodes.get("refresh-editor").onclick();
  assert.equal(f.nodes.get("editor-provider").value,"openai");
  await f.finish();assert.equal(f.nodes.get("editor-provider").value,"openai");
  await f.context.openEditor("other");assert.equal(f.nodes.get("editor-provider").value,"");
});
test("invalid API limits block requests, and old servers cannot silently ignore overrides",async()=>{
  const f=fixture();await f.open();f.nodes.get("editor-provider").value="openai";
  f.nodes.get("editor-analysis-budget").value="0";
  assert.throws(()=>f.context.editorAnalysisRequest(),/spending limit/);
  delete f.state.clip.analysis_defaults;f.context.fillEditor(f.state.clip);
  assert.equal(f.nodes.get("editor-provider").disabled,true);
  assert.throws(()=>f.context.editorAnalysisRequest(),/Restart/);
});
test("model options distinguish API and Codex and disable unavailable providers",()=>{
  const f=fixture();f.context.initEditorModels({providers:{
    openai:{available:true,configured:true},codex:{available:true,configured:true},local:{available:true,configured:false},
  },models:{}});
  const options=f.nodes.get("editor-provider").children;
  assert.equal(options[1].textContent,"OpenAI API - gpt-6-luna");
  assert.equal(options[2].textContent,"Codex gpt-6-luna");assert.equal(options[3].disabled,true);
});

function pendingContext(f,state="failed"){
  f.state.runState=state;f.state.runMessage="Model service retries exhausted.";
  f.state.clip.context_request={id:"stuck",status:"pending",note:"Include the question"};
  f.state.clip.context_job={state,message:f.state.runMessage};f.state.clip.context_recovery_available=true;
}
test("a failed context search shows its error and recovery controls; keep current clip unlocks editing",async()=>{
  const f=fixture();pendingContext(f);await f.open();
  assert.match(f.nodes.get("editor-message").textContent,/retries exhausted/);
  assert.equal(f.nodes.get("editor-context-retry").hidden,false);
  assert.equal(f.nodes.get("editor-context-discard").hidden,false);
  assert.equal(f.nodes.get("editor-fields").disabled,true);
  assert.equal(f.nodes.get("editor-provider").disabled,false);
  await f.nodes.get("editor-context-discard").onclick();
  assert.equal(f.context.editing.revision,2);assert.equal(f.nodes.get("editor-fields").disabled,false);
  assert.equal(f.nodes.get("editor-context-recovery").hidden,true);
  assert.match(f.nodes.get("editor-message").textContent,/Editing is unlocked/);
});
test("failed context polling stops the spinner and retry uses the selected model",async()=>{
  const f=fixture();pendingContext(f,"running");await f.open();f.state.runState="failed";
  await f.context.pollEditorRender();assert.match(f.nodes.get("editor-message").textContent,/retries exhausted/);
  f.nodes.get("editor-provider").value="openai";f.nodes.get("editor-analysis-budget").value="3";
  await f.nodes.get("editor-context-retry").onclick();
  const request=f.calls.find(c=>c.url.endsWith("context-recovery")&&c.body);
  assert.equal(request.body.request_id,"stuck");assert.deepEqual(request.body.analysis,{provider:"openai",budget_usd:3});
  assert.match(f.nodes.get("editor-message").textContent,/Waiting for a worker/);
  assert.equal(f.nodes.get("editor-provider").disabled,true);
});
test("stop search waits for worker acknowledgement before offering keep current clip",async()=>{
  const f=fixture();pendingContext(f,"running");await f.open();
  await f.nodes.get("editor-context-stop").onclick();
  assert.equal(f.nodes.get("editor-context-stop").disabled,true);
  assert.equal(f.nodes.get("editor-context-discard").hidden,true);
  assert.match(f.nodes.get("editor-message").textContent,/Stopping/);
  f.state.runState="cancelled";await f.context.pollEditorRender();
  assert.equal(f.nodes.get("editor-context-discard").hidden,false);
});
test("recovery failures keep the original request and offer retry",async()=>{
  const f=fixture();pendingContext(f);await f.open();f.state.fail="/api/clips/edited/context-recovery";
  await f.nodes.get("editor-context-discard").onclick();
  assert.equal(f.context.editing.context_request.id,"stuck");
  assert.equal(f.nodes.get("editor-context-discard").disabled,false);
  assert.match(f.nodes.get("editor-message").textContent,/Request failed/);
});
