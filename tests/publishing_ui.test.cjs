const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
class Element{
  constructor(){this.children=[];this.value="";this.checked=false;this.open=false;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  showModal(){this.open=true;}
  close(){this.open=false;}
}
function fixture(){
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const writes=[],state={storage:false,buffer:false,fail:false};
  const clips=[{id:"clip",revision:3,title:"Clip",ready:true,source_title:"Recording",plan:null,publication:null}];
  const data={connected:false,organizations:[{id:"org",name:"My account"}],organization_id:"org",channels:[{id:"yt",service:"youtube",name:"Channel"}],mapping:{youtube:"yt"},clips};
  const find=(node,label)=>{if(node.textContent===label)return node;for(const c of node.children||[]){const match=find(c,label);if(match)return match;}};
  const context=vm.createContext({
    $:id=>nodes.get(id),document:{createElement:()=>new Element()},text:(tag,value)=>Object.assign(new Element(),{textContent:value}),
    action:(label,onclick)=>Object.assign(new Element(),{textContent:label,onclick}),error:message=>assert.fail(message),
    api:async(url,options)=>{
      if(!options)return url.endsWith("storage")?{connected:state.storage,bucket:"bucket",storage_limit:8e9,uploads:{}}:{...data,connected:state.buffer};
      const body=options.body?JSON.parse(options.body):null;writes.push({url,body});
      if(url.endsWith("storage/connect")){
        assert.equal(nodes.get("publishing-secret-key").value,"");
        assert.equal(nodes.get("publishing-access-key").value,"");
        state.storage=true;return {used_bytes:0};
      }
      if(url.endsWith("buffer/connect")){assert.equal(nodes.get("publishing-buffer-key").value,"");state.buffer=true;return {};}
      if(url.endsWith("preview"))return {id:"ticket",channels:{youtube:{name:"Channel"}},items:body.items.map(i=>({...i,mapping:{youtube:"yt"},mode:body.mode==="now"?"now":"schedule",due_at:body.mode==="now"?null:"2030-01-01T16:00:00Z",timezone:body.timezone||"Europe/Helsinki"}))};
      if(url.includes("/send/"))return {receipts:{youtube:{status:state.fail?"unknown":"scheduled"}}};
      return {};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(directory,"publishing.js"),"utf8"),context);
  return {nodes,writes,state,data,context,find:(label)=>find(nodes.get("publishing-videos"),label)};
}
test("publishing requires an explicit upload and clears credential inputs before sending",async()=>{
  const {nodes,writes,find}=fixture();
  await nodes.get("refresh-publishing").onclick();
  assert.equal(writes.length,0);
  assert.equal(find("Upload final video").disabled,true);
  nodes.get("publishing-access-key").value="access";nodes.get("publishing-secret-key").value="secret";
  await nodes.get("publishing-storage-form").onsubmit({preventDefault(){}});
  assert.deepEqual(writes[0].body,{access_key:"access",secret_key:"secret"});
  await find("Upload final video").onclick();
  assert.deepEqual(writes[1],{url:"/api/publishing/storage/clips/clip",body:{revision:3}});
  assert.match(nodes.get("publishing-message").textContent,/No social post/);
});
test("Buffer preview embeds exact video and needs explicit confirmation before publish-now",async()=>{
  const {nodes,writes,state,find}=fixture();state.storage=true;
  nodes.get("publishing-buffer-key").value="personal-key";
  await nodes.get("publishing-buffer-form").onsubmit({preventDefault(){}});
  assert.deepEqual(writes[0].body,{api_key:"personal-key"});
  await find("Preview publish now").onclick();
  assert.equal(nodes.get("publishing-preview-dialog").open,true);
  assert.equal(nodes.get("publishing-preview-items").children[0].children[1].src,"/api/artifacts/clip/video?revision=3");
  assert.equal(nodes.get("publishing-send").disabled,true);
  await nodes.get("publishing-send").onclick();
  assert.equal(writes.filter(w=>w.url.includes("/send/")).length,0);
  nodes.get("publishing-confirm").checked=true;nodes.get("publishing-confirm").onchange();
  await nodes.get("publishing-send").onclick();
  assert.equal(writes.filter(w=>w.url.includes("/send/")).length,1);
  assert.equal(nodes.get("publishing-preview-dialog").open,false);
  assert.match(nodes.get("publishing-message").textContent,/accepted/);
});
test("custom schedule passes local time and configurable zone into preview",async()=>{
  const {nodes,writes,state,find}=fixture();state.storage=true;state.buffer=true;
  await nodes.get("refresh-publishing").onclick();
  await find("Choose posting time").onclick();
  nodes.get("publishing-local-time").value="2030-01-01T18:00";nodes.get("publishing-timezone").value="Europe/Helsinki";
  await nodes.get("publishing-time-form").onsubmit({preventDefault(){}});
  assert.equal(writes[0].body.mode,"schedule");
  assert.equal(writes[0].body.local_time,"2030-01-01T18:00");
  assert.equal(writes[0].body.timezone,"Europe/Helsinki");
  assert.equal(nodes.get("publishing-send").textContent,"Schedule in Buffer");
  assert.equal(writes.length,1);
});
test("uncertain submissions stop a batch and require fresh confirmation",async()=>{
  const {nodes,writes,state,context}=fixture();state.storage=true;state.buffer=true;state.fail=true;
  await vm.runInContext('openPublishingPreview([{clip_id:"clip",revision:3,title:"One"},{clip_id:"second",revision:1,title:"Two"}],"plan")',context);
  nodes.get("publishing-confirm").checked=true;
  await nodes.get("publishing-send").onclick();
  assert.equal(writes.filter(w=>w.url.includes("/send/")).length,1);
  assert.equal(nodes.get("publishing-confirm").checked,false);
  assert.equal(nodes.get("publishing-preview-dialog").open,true);
  assert.match(nodes.get("publishing-preview-message").textContent,/attention/);
});
