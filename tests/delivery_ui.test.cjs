const {test}=require("node:test"),assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm"),path=require("node:path");
class Element{
  constructor(){this.children=[];this.value="";this.open=false;}
  append(...items){this.children.push(...items);}
  replaceChildren(...items){this.children=items;}
  showModal(){this.open=true;}
  close(){this.open=false;}
  querySelectorAll(){return this.children.flatMap(c=>[c,...c.querySelectorAll()]).filter(c=>c.type==="checkbox"&&c.checked);}
}
test("cleanup stays separate from optional publishing plans and deletion still requires explicit confirmation",async()=>{
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const writes=[];
  const context=vm.createContext({
    $:id=>nodes.get(id),document:{createElement:()=>new Element()},text:(tag,value)=>Object.assign(new Element(),{textContent:value}),
    action:(label,onclick)=>Object.assign(new Element(),{textContent:label,onclick}),error:message=>assert.fail(message),
    api:async(url,options)=>{
      if(!options){
        if(url==="/api/delivery")return {recordings:[{id:"run",title:"Recording",resolved:true,ready:2,unresolved:0}],plan:[]};
        return {run_id:"run",title:"Recording",file_count:1,bytes:4096,kept_videos:2,fingerprint:"saved-plan",files:[["runs/run/source.opus",4096,1]]};
      }
      writes.push({url,body:JSON.parse(options.body)});
      return url.endsWith("schedule")?{added:2}:{deleted_bytes:4096,remaining_files:[]};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(directory,"delivery.js"),"utf8"),context);
  await nodes.get("refresh-delivery").onclick();
  assert.equal(nodes.get("delivery-recordings").children.length,0,"Opening cleanup must not load publishing controls");
  assert.equal(nodes.get("posting-plan").children.length,0);
  assert.equal(writes.length,0);
  assert.equal(nodes.get("cleanup-recordings").children.length,1);
  nodes.get("publishing-manual-plan").open=true;
  await nodes.get("publishing-manual-plan").ontoggle();
  nodes.get("delivery-recordings").children[0].children[0].checked=true;
  nodes.get("posting-date").value="2030-02-01";nodes.get("posting-time").value="13:15";nodes.get("posting-zone").value="Europe/Helsinki";
  await nodes.get("create-posting-plan").onclick();
  assert.deepEqual(writes[0].body,{run_ids:["run"],start_date:"2030-02-01",local_time:"13:15",timezone:"Europe/Helsinki"});
  assert.match(nodes.get("posting-message").textContent,/does not upload automatically/);
  await vm.runInContext("openCleanup('run')",context);
  assert.equal(nodes.get("cleanup-dialog").open,true);assert.equal(nodes.get("cleanup-delete").disabled,true);
  assert.match(nodes.get("cleanup-files").textContent,/source.opus/);
  await nodes.get("cleanup-delete").onclick();assert.equal(writes.length,1);
  nodes.get("cleanup-confirm").checked=true;nodes.get("cleanup-confirm").onchange();
  await nodes.get("cleanup-delete").onclick();
  assert.deepEqual(writes[1],{url:"/api/delivery/cleanup/run",body:{fingerprint:"saved-plan"}});
  assert.equal(nodes.get("cleanup-dialog").open,false);assert.match(nodes.get("delivery-message").textContent,/preserved/);
});


test("posting controls belong only to Publishing while Cleanup explains its independent role",()=>{
  const html=fs.readFileSync(path.join(__dirname,"../src/vaarattu_shorts/static/index.html"),"utf8");
  const cleanup=html.slice(html.indexOf('<section id="view-delivery"'),html.indexOf('<section id="view-publishing"'));
  const publishing=html.slice(html.indexOf('<section id="view-publishing"'),html.indexOf('<dialog id="publishing-time-dialog"'));
  assert.match(cleanup,/>Cleanup<\/h1>/);
  assert.match(cleanup,/Cleanup is optional and is not a step in scheduling/);
  for(const id of ['posting-date','create-posting-plan','posting-plan']){
    assert.ok(!cleanup.includes(`id="${id}"`));assert.ok(publishing.includes(`id="${id}"`));
  }
  assert.match(publishing,/it appears here automatically/);
  assert.match(publishing,/Fill scheduled queue does not require this plan/);
  assert.ok(publishing.indexOf('id="publishing-fill"')<publishing.indexOf('id="publishing-connections"'));
  assert.ok(!publishing.includes('href="#delivery"'));
});


test("manual posting records and removed plans refresh the publishing calendar",async()=>{
  const directory=path.join(__dirname,"../src/vaarattu_shorts/static");
  const nodes=new Map([...fs.readFileSync(path.join(directory,"index.html"),"utf8").matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const writes=[];let calendarRefreshes=0;
  const post={id:"plan",clip_id:"clip",title:"Clip",revision:3,ready:true,scheduled_at:"2026-09-29T16:00:00Z",timezone:"Europe/Helsinki",deliveries:{youtube:{status:"pending"}}};
  let plan=[post];
  const context=vm.createContext({
    $:id=>nodes.get(id),document:{createElement:tag=>Object.assign(new Element(),{tagName:tag})},text:(tag,value)=>Object.assign(new Element(),{textContent:value,tagName:tag}),
    action:(label,onclick)=>Object.assign(new Element(),{textContent:label,onclick}),error:message=>assert.fail(message),
    loadPublishing:async()=>{calendarRefreshes++;},
    api:async(url,options)=>{
      if(!options)return {recordings:[],plan};
      writes.push({url,method:options.method});
      if(options.method==="DELETE")plan=[];
      else post.deliveries.youtube={status:"posted",url:JSON.parse(options.body).url};
      return {};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(directory,"delivery.js"),"utf8"),context);
  await nodes.get("refresh-posting-plan").onclick();
  const form=nodes.get("posting-plan").children[0].children.find(c=>c.tagName==="form");
  form.children[0].children[0].value="https://youtube.com/shorts/posted";
  await form.onsubmit({preventDefault(){}});
  assert.equal(calendarRefreshes,1);assert.equal(writes[0].url,"/api/delivery/posts/plan/youtube");
  assert.equal(post.deliveries.youtube.status,"posted");
  post.deliveries.youtube={status:"pending"};await nodes.get("refresh-posting-plan").onclick();
  await nodes.get("posting-plan").children[0].children.find(c=>c.textContent==="Remove from plan").onclick();
  assert.equal(calendarRefreshes,2);assert.equal(plan.length,0);assert.equal(writes[1].method,"DELETE");
});
