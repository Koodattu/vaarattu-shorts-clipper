const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Element{
  constructor(){this.children=[];this.value='';this.attributes={};this.hidden=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(k,v){this.attributes[k]=v;}
  getAttribute(k){return this.attributes[k]||'';}
  removeAttribute(k){delete this.attributes[k];}
  set src(v){this.attributes.src=v;}
  get src(){return this.attributes.src;}
  pause(){}
  load(){this.loads=(this.loads||0)+1;}
}
function fixture(){
  const directory=path.join(__dirname,'../src/vaarattu_shorts/static');
  const nodes=new Map([...fs.readFileSync(path.join(directory,'index.html'),'utf8').matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const writes=[],errors=[];
  const run={id:'a'.repeat(32),title:'Recording',state:'completed',stage:'completed',progress:1,message:'Ready',revision:1,has_draft:true,has_final:false,duration:360,issues:[],history:[],usage:{request_count:5}};
  const context=vm.createContext({
    $:id=>nodes.get(id),document:{createElement:()=>new Element()},text:(tag,value)=>Object.assign(new Element(),{textContent:value}),
    action:(label,onclick)=>Object.assign(new Element(),{textContent:label,onclick}),error:message=>errors.push(message),
    currentView:'highlights',setTimeout:()=>1,clearTimeout:()=>{},crypto:{randomUUID:()=> 'request-key'},
    goView:view=>{context.currentView=view;},
    api:async(url,options)=>{
      if(!options){if(url==='/api/status')return {providers:{codex:{available:true,configured:true,model:'configured'}}};return [run];}
      const body=JSON.parse(options.body);writes.push({url,body});
      if(url.endsWith('/resolve'))return {id:'b'.repeat(32),title:'Recording',sources:[{title:'Part 1',duration:100},{title:'Part 2',duration:200}],duration:300,notes:[]};
      return {id:run.id};
    }
  });
  vm.runInContext(fs.readFileSync(path.join(directory,'highlights.js'),'utf8'),context);
  return {nodes,writes,errors,run,context};
}
test('library action resolves parts without starting a job',async()=>{
  const {nodes,writes,context}=fixture();
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  assert.equal(writes.length,1);assert.equal(writes[0].url,'/api/highlights/resolve');
  assert.equal(nodes.get('highlight-start').disabled,false);
  assert.equal(nodes.get('highlight-parts').children.length,3);
  nodes.get('highlight-urls').oninput();assert.equal(nodes.get('highlight-start').disabled,true);
});
test('opening highlights never starts processing and polling does not reload the video',async()=>{
  const {nodes,writes,context}=fixture();
  await vm.runInContext('loadHighlights()',context);
  const player=nodes.get('highlight-player');assert.equal(player.loads,1);
  await vm.runInContext('loadHighlights()',context);
  assert.equal(player.loads,1);assert.equal(writes.length,0);assert.match(player.src,/quality=draft/);
});
test('final render requires an explicit approve action with the current revision',async()=>{
  const {nodes,writes,context}=fixture();await vm.runInContext('loadHighlights()',context);
  await nodes.get('highlight-approve').onclick();
  assert.equal(writes.length,1);assert.match(writes[0].url,/\/approve$/);assert.equal(writes[0].body.revision,1);
});
test('revision requires instructions and does not change the shorts API',async()=>{
  const {nodes,writes,errors,context}=fixture();await vm.runInContext('loadHighlights()',context);
  await nodes.get('highlight-revise').onclick();assert.equal(writes.length,0);assert.equal(errors.length,1);
  nodes.get('highlight-guidance').value='Remove the inventory detour';await nodes.get('highlight-revise').onclick();
  assert.match(writes[0].url,/^\/api\/highlights\/.+\/revise$/);assert.equal(writes[0].body.guidance,'Remove the inventory detour');
});

test('partial model recovery warnings remain visible alongside the draft',async()=>{
  const {nodes,run,context}=fixture();
  run.warnings=['Section 2 could not be fully analyzed; some highlights may be missing.'];
  await vm.runInContext('loadHighlights()',context);
  assert.ok(nodes.get('highlight-notes').children.some(node=>node.textContent===run.warnings[0]));
  assert.match(nodes.get('highlight-player').src,/quality=draft/);
});

test('full recording rebuild is explicit and preserves optional guidance',async()=>{
  const {nodes,writes,context}=fixture();await vm.runInContext('loadHighlights()',context);
  assert.equal(writes.length,0);
  nodes.get('highlight-guidance').value='A condensed episode with entertaining commentary';
  await nodes.get('highlight-rebuild').onclick();
  assert.match(writes[0].url,/\/rebuild$/);
  assert.equal(writes[0].body.revision,1);
  assert.equal(writes[0].body.guidance,'A condensed episode with entertaining commentary');
});


test('paused runs can rebuild and progress identifies the stage and current revision work',async()=>{
  const {nodes,run,context,writes}=fixture();
  run.state='paused';run.stage='edit-2';run.progress=0.75;
  run.activity={request_count:12,thinking:'low',phases:{'Scene editing':{requests:8,retries:1,seconds:120}}};
  await vm.runInContext('loadHighlights()',context);
  assert.match(nodes.get('highlight-status').textContent,/75% of this stage/);
  assert.match(nodes.get('highlight-meta').textContent,/12 model requests in this revision/);
  assert.ok(nodes.get('highlight-notes').children.some(n=>n.textContent.includes('1 retries / repairs')));
  await nodes.get('highlight-controls').children.find(n=>n.textContent==='Rebuild from full recording').onclick();
  assert.match(writes[0].url,/rebuild$/);
});


test('new highlight requests do not send a preferred runtime',async()=>{
  const {nodes,context,writes}=fixture();
  assert.equal(nodes.has('highlight-target'),false);
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  nodes.get('highlight-provider').value='codex';
  nodes.get('highlight-encoder').value='h264_nvenc';
  await nodes.get('highlight-form').onsubmit({preventDefault(){}});
  const submission=writes.find(w=>w.url==='/api/highlights');
  assert.ok(submission);
  assert.equal(Object.hasOwn(submission.body,'target_minutes'),false);
});
