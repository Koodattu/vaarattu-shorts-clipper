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

test("Codex selection disables dollar cap and renders unknown costs", async()=>{
  const staticPath=path.join(__dirname,"../src/vaarattu_shorts/static");
  const html=fs.readFileSync(path.join(staticPath,"index.html"),"utf8");
  const nodes=new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const run={id:"fixture-run",state:"completed",stage:"complete",message:"",clips:[],
    config:{video:"abc_def-ghI",provider:"codex",codex:{model:"gpt-5.6-luna"}},result:{coverage:"complete"},
    usage:{estimated_cost_usd:null,reserved_usd:null,budget_usd:null,request_count:1,input_tokens:800,
      output_tokens:120,cached_input_tokens:100,reasoning_tokens:20,unreported_requests:0,
      reported_counts:{cached_input_tokens:1,reasoning_tokens:1}}};
  let queued=false, submitted;
  const context=vm.createContext({
    crypto:{randomUUID:()=>"fixture-request"},
    document:{getElementById:id=>nodes.get(id),createElement:tag=>new Element(tag)},
    fetch:async(url,options)=>{
      let result;
      if(url==="/api/status")result={token:"fixture",models:{turbo:true},providers:{
        codex:{model:"gpt-5.6-luna",configured:true,available:true}}};
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
  assert.ok(find(nodes.get("provider"),"Codex · gpt-5.6-luna"));
  nodes.get("provider").value="codex";
  nodes.get("provider").onchange();
  assert.equal(nodes.get("budget").disabled,true);
  assert.equal(nodes.get("codex-note").hidden,false);
  nodes.get("budget").value="9";
  nodes.get("video").value="abc_def-ghI";
  await nodes.get("run-form").onsubmit({preventDefault(){}});
  assert.equal(submitted.provider,"codex");
  assert.equal(submitted.budget_usd,0);
  assert.equal(nodes.get("error").textContent,"");
  assert.ok(find(nodes.get("run-detail"),"Codex · gpt-5.6-luna · subscription usage · monetary cost unavailable."));
  nodes.get("provider").value="openai";
  nodes.get("provider").onchange();
  assert.equal(nodes.get("budget").disabled,false);
});
