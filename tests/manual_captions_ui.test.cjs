const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
class Element{
  constructor(){this.children=[];this.disabled=false;this.open=false;this.value="";this.currentTime=4;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this[key]=value;}
  removeAttribute(key){delete this[key];}
  showModal(){this.open=true;}
  close(){this.open=false;this.onclose?.();}
  pause(){this.paused=true;}
  load(){}
}
function setup(){
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const clip={id:"clip",revision:3,run_id:"run",title:"Title",status:"ready",has_preview:true,start_us:0,end_us:8e6,
    words:[{id:"a",start_us:5e5,end_us:1e6,text:"Vanha",probability:0.9},{id:"b",start_us:3e6,end_us:35e5,text:"Removed"},{id:"c",start_us:6e6,end_us:65e5,text:"loppu."}],
    speech_cuts:[{start_us:2e6,end_us:4e6}],pacing:{retained:[{source_start_us:0,source_end_us:2e6},{source_start_us:4e6,source_end_us:8e6}]}};
  const calls=[],messages=[];let polls=0,failSave=false,failLoad=false,holdSave=null;
  const context=vm.createContext({$:id=>nodes.get(id),document:{createElement:()=>new Element()},reviewQueue:[clip,{id:"next"}],reviewBusy:false,reviewRendering:null,reviewLast:{id:"previous"},
    reviewControls(){},paintReviewQueue(){},reviewMessage:message=>messages.push(message),pollReviewRender:async()=>{polls++;},
    api:async(url,options)=>{
      calls.push({url,body:options?JSON.parse(options.body):null});
      if(!options){if(failLoad)throw new Error("Could not load");return structuredClone(clip);}
      if(holdSave)await holdSave;
      if(failSave)throw new Error("This clip changed. Reload it before saving.");
      return {run_id:"run",revision:4};
    }});
  vm.runInContext(fs.readFileSync(path.join(directory,"manual-captions.js"),"utf8"),context);
  return {nodes,context,clip,calls,messages,get polls(){return polls;},set failSave(v){failSave=v;},set failLoad(v){failLoad=v;},set holdSave(v){holdSave=v;}};
}
const submit=fixture=>fixture.nodes.get("manual-caption-form").onsubmit({preventDefault(){}});
test("quick caption edits retain timings and hidden words, render once and keep queue position",async()=>{
  const f=setup(),n=f.nodes;
  await n.get("review-caption-edit").onclick();
  assert.equal(n.get("manual-caption-dialog").open,true);
  assert.equal(n.get("review-player").paused,true);
  n.get("manual-caption-player").onloadedmetadata();
  assert.equal(n.get("manual-caption-player").currentTime,4);
  assert.match(n.get("manual-caption-player").src,/revision=3$/);
  const inputs=n.get("manual-caption-words").children;
  assert.deepEqual(inputs.map(i=>i.value),["Vanha","loppu."]);
  assert.equal(n.get("manual-caption-save").disabled,true);
  await submit(f);assert.equal(f.calls.length,1,"No-op must not render");
  inputs[0].value="Korjattu";inputs[0].oninput();assert.equal(n.get("manual-caption-save").disabled,false);
  let resolve;f.holdSave=new Promise(done=>{resolve=done;});
  const saving=submit(f);await submit(f);
  assert.equal(n.get("manual-caption-close").disabled,true);
  let prevented=false;n.get("manual-caption-dialog").oncancel({preventDefault(){prevented=true;}});assert.equal(prevented,true);
  resolve();await saving;
  assert.equal(f.calls.length,2);
  assert.equal(f.calls[1].url,"/api/clips/clip/edit");
  assert.deepEqual(f.calls[1].body,{expected_revision:3,start_us:0,end_us:8e6,title:"Title",words:f.clip.words.map(w=>w.id==="a"?{...w,text:"Korjattu"}:w),reviewed:true});
  assert.equal(f.polls,1);assert.equal(n.get("manual-caption-dialog").open,false);
  assert.equal(f.context.reviewQueue[0].id,"clip");assert.equal(f.context.reviewQueue[0].revision,4);assert.equal(f.context.reviewQueue[1].id,"next");
  assert.equal(f.context.reviewLast,null);assert.equal(f.context.reviewBusy,false);
  assert.equal(n.get("manual-caption-player").paused,true);
});
test("cancel and empty words cannot alter the clip",async()=>{
  const f=setup(),n=f.nodes;
  await n.get("review-caption-edit").onclick();
  const input=n.get("manual-caption-words").children[0];input.value=" ";input.oninput();
  assert.equal(n.get("manual-caption-save").disabled,true);await submit(f);
  n.get("manual-caption-close").onclick();
  assert.equal(f.calls.length,1);assert.equal(f.polls,0);assert.equal(f.context.reviewQueue[0].revision,3);assert.equal(f.context.reviewBusy,false);
  await n.get("review-caption-edit").onclick();assert.equal(n.get("manual-caption-words").children[0].value,"Vanha");
});
test("save failure preserves typed corrections and queue position for retry",async()=>{
  const f=setup(),n=f.nodes;f.failSave=true;
  await n.get("review-caption-edit").onclick();const input=n.get("manual-caption-words").children[0];input.value="Korjattu";input.oninput();await submit(f);
  assert.equal(n.get("manual-caption-dialog").open,true);assert.equal(input.value,"Korjattu");assert.match(n.get("manual-caption-message").textContent,/clip changed/);
  assert.equal(f.context.reviewQueue[0].revision,3);assert.equal(f.context.reviewRendering,null);assert.equal(f.polls,0);
  assert.equal(n.get("manual-caption-save").disabled,false);assert.equal(n.get("manual-caption-close").disabled,false);
});
test("load failures release the queue and pending renders cannot open caption edits",async()=>{
  const f=setup();f.failLoad=true;
  await f.nodes.get("review-caption-edit").onclick();assert.equal(f.context.reviewBusy,false);assert.equal(f.nodes.get("manual-caption-dialog").open,false);assert.match(f.messages[0],/Could not load/);
  f.context.reviewRendering={id:"clip"};await f.nodes.get("review-caption-edit").onclick();assert.equal(f.calls.length,1);
});
test("clips without pacing still hide removed speech and expose other words",async()=>{
  const f=setup();delete f.clip.pacing;
  await f.nodes.get("review-caption-edit").onclick();assert.deepEqual(f.nodes.get("manual-caption-words").children.map(i=>i.value),["Vanha","loppu."]);
});
