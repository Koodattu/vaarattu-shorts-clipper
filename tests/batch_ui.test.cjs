const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs"), path=require("node:path"), vm=require("node:vm");
class Element {
  constructor(tag="div"){this.tagName=tag;this.children=[];this.value="";this.textContent="";this.checked=false;this.listeners={};this.classList={toggle(){}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(name,value){this[name]=value;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  querySelector(){for(const child of this.children){if(child.type==="checkbox")return child;const nested=child.querySelector();if(nested)return nested;}return null;}
  querySelectorAll(){return [];}
  getContext(){return {};}
  scrollIntoView(){} focus(){} pause(){}
}
async function setup({post,search}={}){
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  for(const [id,value] of Object.entries({"video-filter":"all",provider:"openai",layout:"a".repeat(32),"local-model":"gemma4-31b",context:"16384",budget:"2","discovery-reasoning":"high","verification-reasoning":"medium","video-encoder":"libx264"}))nodes.get(id).value=value;
  const videos=Array.from({length:14},(_,i)=>({id:String(i),provider:i===1?"twitch":"youtube",title:`Recording ${i}`,url:i===1?"https://www.twitch.tv/videos/123":"https://www.youtube.com/watch?v="+String(i).padStart(11,"a"),available:i!==3,processed:false,state:"not_started"}));
  const requests=[],runs=[];let key=0;
  const context=vm.createContext({setTimeout:()=>0,crypto:{randomUUID:()=>`request-key-${++key}`},
    document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag)},
    fetch:async(url,options)=>{
      if(url.startsWith("/api/streams/search"))return search?search(url):{ok:true,json:async()=>({status:"ready",matches:[]})};
      if(url==="/api/runs"&&options.method==="POST"){
        const request={key:options.headers["Idempotency-Key"],body:JSON.parse(options.body)};requests.push(request);
        if(post){const response=await post(request,requests.length);if(response)return response;}
        const id=`run-${requests.length}`;
        runs.push({id,state:"queued",stage:"metadata",progress:0,config:request.body,result:{},clips:[],usage:{estimated_cost_usd:0,reserved_usd:0,budget_usd:2,request_count:0,input_tokens:0,output_tokens:0,reported_counts:{}}});
        return {ok:true,json:async()=>({id})};
      }
      if(url.startsWith("/api/runs/"))return {ok:true,json:async()=>runs.find(run=>url.endsWith(run.id))};
      const data={"/api/status":{token:"fixture",models:{turbo:true},providers:{}},"/api/layouts":[{id:"a".repeat(32),body:{name:"Layout"}}],"/api/runs":runs,"/api/videos":{configured:true,videos}};
      assert.ok(url in data,url);return {ok:true,json:async()=>data[url]};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(directory,"app.js"),"utf8"),context);
  await new Promise(setImmediate);
  vm.runInContext(fs.readFileSync(path.join(directory,"batch.js"),"utf8"),context);
  function select(index){const card=nodes.get("channel-videos").children.find(row=>row.querySelector()?.["aria-label"]===`Select Recording ${index} for batch shorts`);const checkbox=card.querySelector();checkbox.checked=true;checkbox.onchange();}
  function chat(index,id,offset,confirmed){nodes.get("batch-chat-video").value=String(index);nodes.get("batch-chat-video").onchange();nodes.get("stream-id").value=id;nodes.get("stream-offset").value=offset;nodes.get("alignment").checked=confirmed;}
  const submit=()=>nodes.get("run-form").onsubmit({preventDefault(){}});
  return {nodes,context,videos,requests,select,chat,submit};
}
test("selection survives filtering/loading; one settings set queues independent video mappings",async()=>{
  const {nodes,context,videos,requests,select,chat,submit}=await setup();
  assert.equal(nodes.get("channel-videos").children[3].querySelector().disabled,true);
  select(0);select(1);
  nodes.get("video-search").value="Recording 1";nodes.get("video-search").oninput();
  assert.equal(nodes.get("library-selected-count").textContent,"2 videos selected");
  nodes.get("video-search").value="";nodes.get("video-search").oninput();
  await nodes.get("load-more-videos").onclick();select(13);
  assert.equal(nodes.get("library-selected-count").textContent,"3 videos selected");
  nodes.get("stream-id").value="999";nodes.get("stream-offset").value="99";nodes.get("alignment").checked=true;
  nodes.get("batch-create").onclick();assert.equal(requests.length,0);
  assert.equal(nodes.get("stream-id").value,"");assert.equal(nodes.get("alignment").checked,false,"The single-video form's mapping must not leak into a batch");
  assert.equal(nodes.get("video").disabled,true);
  chat(0,"254","0",true);chat(1,"300","12.5",true);
  // Moving to the third video saves the second video's own mapping.
  nodes.get("batch-chat-video").value="2";nodes.get("batch-chat-video").onchange();
  assert.equal(nodes.get("stream-id").value,"");assert.equal(nodes.get("alignment").checked,false);
  vm.runInContext('goView("library")',context);nodes.get("batch-create").onclick();
  assert.equal(nodes.get("stream-id").value,"254","Returning from the library preserves this video's mapping");
  nodes.get("final-transcription").checked=true;nodes.get("trim-silence").checked=true;
  await submit();assert.equal(requests.length,3);
  assert.deepEqual(requests.map(r=>r.body.video),[videos[0].url,videos[1].url,videos[13].url]);
  assert.deepEqual(requests.map(r=>[r.body.stream_id,r.body.stream_offset_seconds,r.body.alignment_confirmed]),[[254,0,true],[300,12.5,true],[null,null,false]]);
  const common=body=>{const {video,stream_id,stream_offset_seconds,alignment_confirmed,...settings}=body;return settings;};
  assert.deepEqual(common(requests[0].body),common(requests[1].body));assert.deepEqual(common(requests[1].body),common(requests[2].body));
  assert.equal(requests[0].body.budget_usd,2);assert.equal(requests[0].body.final_transcription,true);
  assert.equal(new Set(requests.map(r=>r.key)).size,3);
  assert.equal(nodes.get("view-results").hidden,false);assert.equal(nodes.get("library-selection").hidden,true);
  assert.match(nodes.get("notice").textContent,/3 videos queued/);assert.equal(nodes.get("error").textContent,"");
  assert.equal(vm.runInContext("batchActive()",context),false);
});
test("a partial submission retains exact request keys and skips already queued videos on retry",async()=>{
  let finish;
  const state=await setup({post:(_,number)=>number===2?new Promise(resolve=>{finish=resolve;}):null});
  const {nodes,requests,select,submit}=state;
  select(0);select(1);nodes.get("batch-create").onclick();
  const pending=submit();await new Promise(setImmediate);
  await submit();assert.equal(requests.length,2,"Double submit does not start another batch");
  finish({ok:false,status:503,json:async()=>({detail:"Temporary failure"})});await pending;
  assert.equal(nodes.get("run-settings").disabled,true);assert.match(nodes.get("batch-progress").textContent,/1 of 2/);
  assert.equal(nodes.get("run-button").textContent,"Retry remaining videos");
  nodes.get("budget").value="99"; // Even programmatic changes cannot alter an ambiguous retry.
  await submit();assert.equal(requests.length,3);assert.deepEqual(requests[2],requests[1]);
  assert.notEqual(requests[0].key,requests[1].key);assert.equal(nodes.get("error").textContent,"");
});
test("unconfirmed chat is omitted, no-match clears timing, and late searches cannot cross recordings",async()=>{
  let resolveSearch;
  const {nodes,requests,select,chat,submit}=await setup({search:()=>new Promise(resolve=>{resolveSearch=resolve;})});
  select(0);select(1);nodes.get("batch-create").onclick();
  const search=nodes.get("find-stream").onclick();
  nodes.get("batch-chat-video").value="1";nodes.get("batch-chat-video").onchange();
  resolveSearch({ok:true,json:async()=>({status:"ready",suggestedStreamId:254,matches:[{id:254,streamOffsetSeconds:0,titles:["Other recording"]}]})});
  await search;assert.equal(nodes.get("stream-id").value,"");
  chat(0,"254","3",false);chat(1,"300","10",true);
  nodes.get("batch-no-chat").onclick();assert.equal(nodes.get("stream-id").value,"");assert.equal(nodes.get("alignment").checked,false);
  await submit();assert.deepEqual(requests.map(r=>[r.body.stream_id,r.body.stream_offset_seconds,r.body.alignment_confirmed]),[[null,null,false],[null,null,false]]);
});
test("invalid chat prevents all submissions; rejected first request unlocks settings for correction",async()=>{
  const {nodes,requests,select,chat,submit}=await setup({post:(_,number)=>number===1?{ok:false,status:400,json:async()=>({detail:"Prepare the selected model"})}:null});
  select(0);select(1);nodes.get("batch-create").onclick();
  chat(0,"254","",true);await submit();assert.equal(requests.length,0);assert.match(nodes.get("error").textContent,/Check the stream/);
  nodes.get("batch-no-chat").onclick();await submit();assert.equal(requests.length,1);
  assert.equal(nodes.get("run-settings").disabled,false);assert.match(nodes.get("batch-progress").textContent,/Correct the settings/);
  nodes.get("cancel-batch").onclick();assert.equal(nodes.get("video").disabled,false);assert.equal(nodes.get("batch-sources").hidden,true);
});


test("invalid API budgets block the entire batch before requests and allow correction",async()=>{
  const {nodes,requests,select,submit}=await setup();
  select(0);select(1);nodes.get("batch-create").onclick();
  for(const value of ["", "0", "-1", "NaN", "101"]){
    nodes.get("budget").value=value;
    await submit();
    assert.equal(requests.length,0);
    assert.match(nodes.get("error").textContent,/spending limit per recording above/);
    assert.notEqual(nodes.get("run-settings").disabled,true);
  }
  nodes.get("budget").value="3.50";
  await submit();
  assert.equal(requests.length,2);
  assert.deepEqual(requests.map(r=>r.body.budget_usd),[3.5,3.5]);
});

test("switching API, Codex and local preserves the entered budget and only API requires it",async()=>{
  const {nodes,requests,select,submit}=await setup();
  select(0);select(1);nodes.get("batch-create").onclick();
  nodes.get("budget").value="7";
  for(const provider of ["codex","local"]){
    nodes.get("provider").value=provider;nodes.get("provider").onchange();
    assert.equal(nodes.get("budget").disabled,true);
    assert.equal(nodes.get("budget").required,false);
  }
  nodes.get("provider").value="openai";nodes.get("provider").onchange();
  assert.equal(nodes.get("budget").value,"7");
  assert.equal(nodes.get("budget").required,true);
  assert.equal(nodes.get("budget-field").hidden,false);
  nodes.get("provider").value="codex";nodes.get("provider").onchange();nodes.get("budget").value="";
  await submit();
  assert.deepEqual(requests.map(r=>r.body.budget_usd),[0,0]);
});

test("single video also validates API budget before creating a run",async()=>{
  const {nodes,requests,submit}=await setup();
  nodes.get("video").value="https://www.twitch.tv/videos/123";nodes.get("budget").value="0";
  await submit();
  assert.equal(requests.length,0);
  assert.match(nodes.get("error").textContent,/spending limit/);
  assert.equal(nodes.get("run-button").disabled,false);
});
