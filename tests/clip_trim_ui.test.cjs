const {test}=require("node:test"),assert=require("node:assert/strict");
const fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
class Element{
  constructor(){this.children=[];this.style={};this.value="";this.disabled=false;this.open=false;this.currentTime=0;this.tagName="DIV";}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  setAttribute(key,value){this[key]=value;}
  removeAttribute(key){delete this[key];}
  showModal(){this.open=true;}
  close(){this.open=false;this.onclose?.();}
  pause(){this.paused=true;}
  async play(){this.paused=false;}
  load(){}
  getBoundingClientRect(){return {left:0,width:100};}
}
function setup(){
  const dir=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(dir,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const clip={id:"clip",revision:2,status:"ready",has_preview:true,start_us:10e6,end_us:30e6,speech_cuts:[{start_us:18e6,end_us:21e6}]};
  const words=Array.from({length:40},(_,i)=>({id:`w${i}`,start_us:i*1e6+1e5,end_us:i*1e6+8e5,text:`word${i}`}));
  const calls=[],messages=[];let polls=0,fail=false;
  const context=vm.createContext({$:id=>nodes.get(id),document:{createElement:()=>new Element()},text:(tag,value)=>Object.assign(new Element(),{textContent:value}),
    reviewQueue:[clip,{id:"next"}],reviewBusy:false,reviewRendering:null,reviewLast:{id:"previous"},reviewControls(){},paintReviewQueue(){},reviewMessage:m=>messages.push(m),
    pollReviewRender:async()=>{polls++;},api:async(url,options)=>{
      calls.push({url,body:options?JSON.parse(options.body):null});
      if(!options)return {clip,words,min_us:5e6,max_us:35e6,origin_us:4e6};
      if(fail)throw new Error("This edge crosses an existing speech cut.");
      return {run_id:"run",revision:3};
    }});
  vm.runInContext(fs.readFileSync(path.join(dir,"clip-trim.js"),"utf8"),context);
  return {nodes,context,calls,messages,get polls(){return polls;},set fail(v){fail=v;}};
}
async function open(f){await f.nodes.get("review-trim").onclick();f.nodes.get("trim-player").onloadedmetadata();}
const submit=f=>f.nodes.get("trim-form").onsubmit({preventDefault(){}});
test("timeline extends either edge with word snapping and saves the same queue item",async()=>{
  const f=setup(),n=f.nodes;await open(f);
  assert.equal(f.calls.length,1);assert.equal(n.get("trim-dialog").open,true);assert.equal(n.get("trim-save").disabled,true);
  assert.equal(n.get("trim-player").currentTime,6,"Source time uses the verified origin");
  assert.match(n.get("trim-player").src,/source\?revision=2$/);
  const start=n.get("trim-start-handle");start.value=7.4e6;start.oninput();start.onchange();
  assert.equal(n.get("trim-start").value,"7.100");
  n.get("trim-end").value="32.4";n.get("trim-end").onchange();assert.equal(n.get("trim-end").value,"32.800");
  assert.match(n.get("trim-summary").textContent,/25.70 seconds/);
  await submit(f);
  assert.deepEqual(f.calls[1],{url:"/api/clips/clip/trim",body:{expected_revision:2,start_us:7.1e6,end_us:32.8e6}});
  assert.equal(f.polls,1);assert.equal(f.context.reviewQueue[0].id,"clip");assert.equal(f.context.reviewQueue[0].revision,3);assert.equal(f.context.reviewQueue[1].id,"next");
  assert.equal(f.context.reviewBusy,false);assert.equal(n.get("trim-dialog").open,false);
});
test("source seeking, playhead edges, selection playback and reset work without saving",async()=>{
  const f=setup(),n=f.nodes;await open(f);
  n.get("trim-track").onclick({target:n.get("trim-track"),clientX:50});assert.equal(n.get("trim-player").currentTime,16);
  n.get("trim-set-start").onclick();assert.equal(n.get("trim-start").value,"20.000");
  await n.get("trim-preview").onclick();assert.equal(n.get("trim-player").paused,false);assert.equal(n.get("trim-player").currentTime,16);
  n.get("trim-player").currentTime=26;n.get("trim-player").ontimeupdate();assert.equal(n.get("trim-player").paused,true);
  n.get("trim-reset").onclick();assert.equal(n.get("trim-start").value,"10.000");assert.equal(n.get("trim-save").disabled,true);
  await submit(f);assert.equal(f.calls.length,1);n.get("trim-close").onclick();assert.equal(f.context.reviewQueue[0].revision,2);assert.equal(f.context.reviewBusy,false);
});
test("keyboard handles can cross a word without getting stuck and enforce minimum length",async()=>{
  const f=setup(),n=f.nodes;await open(f);
  const handle=n.get("trim-start-handle"),event={key:"ArrowRight",preventDefault(){}};
  handle.onkeydown(event);assert.equal(n.get("trim-start").value,"10.100");
  handle.onkeydown(event);assert.equal(n.get("trim-start").value,"10.800");
  n.get("trim-start").value="29.9";n.get("trim-start").onchange();assert.equal(n.get("trim-start").value,"27.000");
  n.get("trim-end").value="28";n.get("trim-end").onchange();assert.equal(n.get("trim-end").value,"30.000");
});
test("failed save keeps selected edges, and failed media disables saving",async()=>{
  const f=setup(),n=f.nodes;await open(f);f.fail=true;
  n.get("trim-start").value="19";n.get("trim-start").onchange();await submit(f);
  assert.match(n.get("trim-message").textContent,/speech cut/);assert.equal(n.get("trim-dialog").open,true);assert.equal(n.get("trim-start").value,"19.000");
  assert.equal(f.context.reviewQueue[0].revision,2);assert.equal(f.polls,0);
  n.get("trim-player").onerror();assert.equal(n.get("trim-save").disabled,true);assert.match(n.get("trim-message").textContent,/could not load/);
});
test("render in progress prevents opening another trim",async()=>{
  const f=setup();f.context.reviewRendering={id:"clip"};await f.nodes.get("review-trim").onclick();assert.equal(f.calls.length,0);
});
