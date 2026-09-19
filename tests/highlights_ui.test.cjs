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
