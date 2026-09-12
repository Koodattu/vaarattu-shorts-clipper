const {test}=require("node:test");
const assert=require("node:assert/strict");
const fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
class Element{
  constructor(){this.children=[];this.disabled=false;this.open=false;this.value="";}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=nodes;}
  showModal(){this.open=true;}
  close(){this.open=false;this.onclose?.();}
  querySelectorAll(){return this.children.flatMap(c=>c.children||[]).filter(c=>c.type==="checkbox"&&c.checked);}
}
test("caption checks keep the preview, allow individual edits and queue only the accepted words",async()=>{
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  let clip={id:"clip",revision:1,run_id:"run",words:[],status:"ready",has_preview:true},renders=0;
  const writes=[],errors=[];
  const context=vm.createContext({
    $:id=>nodes.get(id),document:{createElement:()=>new Element()},text:(tag,value)=>Object.assign(new Element(),{textContent:value}),
    setTimeout:()=>1,clearTimeout:()=>{},error:message=>errors.push(message),
    reviewQueue:[clip],reviewRendering:null,paintReviewQueue:()=>{},pollReviewRender:async()=>{renders++;},
    editing:{id:"clip",captionFormState:"unsaved"},refresh:async()=>{},goView:()=>{},
    api:async(url,options)=>{
      if(!options)return url.includes("/runs/")?{state:"running"}:{...clip};
      const body=JSON.parse(options.body);writes.push({url,body});
      if(url.endsWith("caption-check")){clip={...clip,caption_check:{id:"check",status:"pending"}};return {request_id:"check"};}
      return {run_id:"run",revision:2};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(directory,"captions.js"),"utf8"),context);
  await nodes.get("review-caption-check").onclick();
  assert.equal(writes.length,1);assert.equal(renders,0);assert.equal(nodes.get("caption-dialog").open,true);
  nodes.get("caption-close").onclick();assert.equal(nodes.get("caption-dialog").open,false);
  clip={...clip,caption_check:{id:"check",status:"complete",changes:[
    {word_id:"w1",original:"old",replacement:"new",reason:"reason"},
    {word_id:"w2",original:"another",replacement:"other",reason:"reason"},
  ]}};
  await nodes.get("review-caption-check").onclick();assert.equal(writes.length,1,"Reopening should load the saved suggestions");
  nodes.get("caption-changes").children[1].children[0].checked=false;
  await nodes.get("caption-apply").onclick();
  assert.deepEqual(writes[1].body,{expected_revision:1,check_id:"check",word_ids:["w1"]});
  assert.equal(renders,1);assert.equal(nodes.get("caption-dialog").open,false);
  await nodes.get("edit-caption-check").onclick();assert.match(errors[0],/Save your current editor changes/);
});
