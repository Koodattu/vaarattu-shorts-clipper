const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
class Element{
  constructor(){this.children=[];this.disabled=false;this.open=false;this.value="";this.currentTime=0;}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  showModal(){this.open=true;}
  close(){this.open=false;this.onclose?.();}
  pause(){this.paused=true;}
  load(){this.loads=(this.loads||0)+1;}
  async play(){this.paused=false;}
  querySelectorAll(){return this.children.flatMap(c=>[c,...c.querySelectorAll()]).filter(c=>c.type==="checkbox"&&c.checked);}
}
for(const mode of ["suggest","instructed"])test(`${mode} edits show the current preview and guidance, then apply only selected cuts or undo`,async()=>{
  const button=mode==="instructed"?"review-instructed-edit":"review-tighten",endpoint=mode==="instructed"?"instructed-edit":"tighten";
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  let clip={id:"clip",revision:1,run_id:"run",words:[],status:"ready",has_preview:true,start_us:1e8,end_us:12e7,
    pacing:{retained:[{source_start_us:1e8,source_end_us:11e7,output_start_us:0},{source_start_us:112e6,source_end_us:12e7,output_start_us:1e7}]}},renders=0;
  const writes=[],errors=[];
  const context=vm.createContext({
    $:id=>nodes.get(id),document:{createElement:()=>new Element()},text:(tag,value)=>Object.assign(new Element(),{textContent:value}),
    action:(label,onclick)=>Object.assign(new Element(),{textContent:label,onclick}),
    setTimeout:()=>1,clearTimeout:()=>{},error:message=>errors.push(message),
    reviewQueue:[clip],reviewRendering:null,paintReviewQueue:()=>{},pollReviewRender:async()=>{renders++;},
    editing:{id:"clip",captionFormState:"saved"},captionEditorState:()=>"unsaved",refresh:async()=>{},goView:()=>{},
    api:async(url,options)=>{
      if(!options)return url.includes("/runs/")?{state:"running"}:{...clip};
      const body=JSON.parse(options.body);writes.push({url,body});
      if(url.endsWith(endpoint)){clip={...clip,tighten_check:{id:"check",status:"pending",note:body.note,mode}};return {request_id:"check"};}
      return {run_id:"run",revision:clip.revision+1};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(directory,"tighten.js"),"utf8"),context);
  await nodes.get(button).onclick();
  assert.equal(writes.length,0,"Opening allows optional guidance before making an LLM request");
  assert.equal(nodes.get("tighten-dialog").open,true);assert.match(nodes.get("tighten-player").src,/revision=1$/);assert.equal(nodes.get("tighten-player").loads,1);
  if(mode==="instructed"){
    assert.equal(nodes.get("tighten-heading").textContent,"Edit with instructions");
    await nodes.get("tighten-request").onclick();
    assert.equal(writes.length,0,"Instructions are required before submitting");
    assert.match(nodes.get("tighten-message").textContent,/Describe how/);
  }
  nodes.get("tighten-note").value="Keep the disagreement; remove the detour";
  await nodes.get("tighten-request").onclick();
  assert.ok(writes[0].url.endsWith(endpoint));
  assert.deepEqual(writes[0].body,{expected_revision:1,note:"Keep the disagreement; remove the detour"});
  assert.equal(renders,0,"A proposal must not render or replace the preview");
  assert.equal(nodes.get("tighten-apply").disabled,true);
  nodes.get("tighten-close").onclick();
  clip={...clip,tighten_check:{...clip.tighten_check,status:"complete",summary:"Two detours",cuts:[
    {start_us:103e6,end_us:106e6,text:"Detour one",reason:"Unrelated",before:"Setup",after:"Point"},
    {start_us:113e6,end_us:117e6,text:"Detour two",reason:"Repeated",before:"Point",after:"Ending"},
  ]}};
  await nodes.get(mode==="instructed"?"review-tighten":"review-instructed-edit").onclick();
  assert.equal(nodes.get("tighten-cuts").children.length,0,"The other action must not show this proposal as its own");
  assert.equal(nodes.get("tighten-note").value,"");
  assert.equal(nodes.get("tighten-apply").disabled,true);
  nodes.get("tighten-close").onclick();
  await nodes.get(button).onclick();assert.equal(writes.length,1);
  assert.equal(nodes.get("tighten-note").value,clip.tighten_check.note);
  const rows=nodes.get("tighten-cuts").children;
  assert.match(rows[1].children[0].children[1].textContent,/0:11.0–0:15.0/);
  await rows[1].children.at(-1).onclick();assert.equal(nodes.get("tighten-player").currentTime,9);
  rows[0].children[0].children[0].checked=false;
  rows[0].children[0].children[0].onchange();
  assert.match(nodes.get("tighten-duration").textContent,/4.0 seconds removed; 14.0 seconds remain/);
  await nodes.get("tighten-apply").onclick();
  assert.deepEqual(writes[1].body,{expected_revision:1,check_id:"check",indices:[1]});
  assert.equal(renders,1);assert.equal(nodes.get("tighten-dialog").open,false);
  clip={...clip,revision:2,tighten_check:null,speech_cuts:[clip.tighten_check.cuts[1]],speech_cut_history:[[]]};
  await nodes.get(button).onclick();assert.equal(nodes.get("tighten-undo").disabled,false);
  await nodes.get("tighten-undo").onclick();
  assert.match(writes[2].url,/speech-cuts-undo$/);assert.deepEqual(writes[2].body,{expected_revision:2});
  assert.equal(renders,2);
  await nodes.get("edit-tighten").onclick();assert.match(errors[0],/Save your current editor changes/);
});
