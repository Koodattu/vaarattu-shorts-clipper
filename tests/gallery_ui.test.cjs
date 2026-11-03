const {test}=require("node:test"),assert=require("node:assert/strict");
const fs=require("node:fs"),path=require("node:path"),vm=require("node:vm");
class Element{
  constructor(tag="div"){this.tagName=tag;this.children=[];this.value="";this.textContent="";}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  setAttribute(key,value){this[key]=value;}
  pause(){this.paused=true;}
  scrollIntoView(){}
}
function fixture(){
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  nodes.get("gallery-filter").value="all";nodes.get("gallery-posting-filter").value="all";
  const clips=Array.from({length:16},(_,i)=>({id:`clip-${i}`,title:`Moment ${i}`,revision:2,status:"ready",review_status:"ready_to_post",start_us:0,end_us:15e6}));
  clips[15].review_status="approved";
  const publishing={clips:[
    {id:"clip-0",publication:{receipts:{youtube:{status:"posted"},tiktok:{status:"posted"}}}},
    {id:"clip-1",publication:{receipts:{youtube:{status:"posted"},tiktok:{status:"error"}}}},
    {id:"clip-2",publication:{receipts:{youtube:{status:"scheduled"}}}},
    {id:"clip-3",plan:{deliveries:{youtube:{status:"posted"},tiktok:{status:"pending"}}}},
    {id:"clip-4",plan:{revision:1,deliveries:{youtube:{status:"posted"}}}},
    {id:"clip-5",publication:{receipts:{youtube:{status:"unknown"}}}},
    {id:"clip-6",plan:{deliveries:{youtube:{status:"pending"}}}},
  ]};
  const calls=[],errors=[];let fail=false;
  const context=vm.createContext({
    $:id=>nodes.get(id),library:null,document:{createElement:tag=>new Element(tag)},
    text:(tag,value,className)=>Object.assign(new Element(tag),{textContent:value,className}),
    action:(label,onclick)=>Object.assign(new Element("button"),{textContent:label,onclick}),error:message=>errors.push(message),
    api:async(url,options)=>{
      assert.equal(options,undefined,"Filtering must not write publishing or review records");calls.push(url);
      if(url==="/api/clips")return clips;
      assert.equal(url,"/api/publishing/buffer");if(fail)throw new Error("Saved publishing records unavailable.");return publishing;
    },
  });
  vm.runInContext(fs.readFileSync(path.join(directory,"gallery.js"),"utf8"),context);
  const titles=()=>nodes.get("gallery-clips").children.filter(c=>c.tagName==="article").map(c=>c.children[1].children.find(n=>n.tagName==="h2").textContent);
  const filter=(id,value)=>{nodes.get(id).value=value;nodes.get(id).oninput();};
  return {nodes,context,clips,publishing,calls,errors,titles,filter,set fail(value){fail=value;}};
}
test("gallery combines readiness, posting status and search across paginated clips",async()=>{
  const f=fixture();await f.nodes.get("refresh-gallery").onclick();
  assert.equal(f.titles().length,12);f.nodes.get("next-gallery").onclick();assert.equal(f.titles().length,4);
  f.filter("gallery-filter","ready_to_post");f.filter("gallery-posting-filter","not_posted");
  assert.deepEqual(f.titles(),Array.from({length:13},(_,i)=>i+2).filter(i=>![3,4].includes(i)).map(i=>`Moment ${i}`));
  assert.equal(f.nodes.get("gallery-page").textContent,"1–11 of 11 clips");
  assert.equal(f.nodes.get("previous-gallery").hidden,true);
  f.filter("gallery-posting-filter","scheduled");assert.deepEqual(f.titles(),["Moment 2"]);
  f.filter("gallery-posting-filter","partly_posted");assert.deepEqual(f.titles(),["Moment 1","Moment 3"]);
  f.filter("gallery-posting-filter","posted");assert.deepEqual(f.titles(),["Moment 0","Moment 4"],"A later render keeps the clip's posting history");
  f.filter("gallery-search","Moment 4");assert.deepEqual(f.titles(),["Moment 4"]);
  f.filter("gallery-search","missing");assert.equal(f.titles().length,0);
  assert.equal(f.nodes.get("gallery-page").textContent,"0 clips");assert.equal(f.nodes.get("next-gallery").hidden,true);
  assert.match(f.nodes.get("gallery-clips").children[0].children[1].textContent,/reset both status filters/);
  assert.equal(f.calls.length,2,"Filter changes use saved data without network requests");
});
test("receipt classification combines manual and Buffer history without treating a schedule as publication",()=>{
  const {context}=fixture();
  for(const [receipts,expected] of [
    [{},"not_posted"],[{youtube:{status:"sending"}},"scheduled"],
    [{youtube:{status:"failed"}},"not_posted"],[{youtube:{status:"posted"}},"posted"],
  ])assert.equal(context.galleryPostingStatus({publication:{receipts}}),expected);
  assert.equal(context.galleryPostingStatus({plan:{deliveries:{youtube:{status:"posted"},tiktok:{status:"pending"}}},publication:{receipts:{tiktok:{status:"posted"}}}}),"posted");
});
test("refresh updates posting badges and active filters, preserves playback, and reports failed loads",async()=>{
  const f=fixture();await f.nodes.get("refresh-gallery").onclick();
  const first=f.nodes.get("gallery-clips").children[0],video=first.children[0];video.currentTime=5;
  f.publishing.clips[0].publication.receipts.tiktok.status="error";
  await f.nodes.get("refresh-gallery").onclick();
  assert.equal(f.nodes.get("gallery-clips").children[0],first);assert.equal(video.currentTime,5);
  assert.equal(first.children[1].children[0].children[1].textContent,"Partly posted");
  f.filter("gallery-posting-filter","not_posted");
  f.publishing.clips.push({id:"clip-7",publication:{receipts:{youtube:{status:"posted"}}}});
  await f.nodes.get("refresh-gallery").onclick();assert.ok(!f.titles().includes("Moment 7"));
  const before=f.titles();f.fail=true;await f.nodes.get("refresh-gallery").onclick();
  assert.deepEqual(f.errors,["Saved publishing records unavailable."]);assert.deepEqual(f.titles(),before);
  assert.equal(f.nodes.get("refresh-gallery").disabled,false);
});
