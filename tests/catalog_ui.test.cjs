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
  getContext() { return {}; }
}
function find(node, label) {
  if(node.textContent===label)return node;
  for(const child of node.children){const match=find(child,label);if(match)return match;}
}

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
});
