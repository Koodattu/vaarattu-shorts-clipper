const {test}=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
class Element{
  constructor(){this.children=[];this.value='';this.attributes={};this.hidden=false;this.style={};this.scrollLeft=0;this.clientWidth=1000;this.currentTime=0;this.listeners={};}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  setAttribute(k,v){this.attributes[k]=v;}
  getAttribute(k){return this.attributes[k]||'';}
  removeAttribute(k){delete this.attributes[k];}
  set src(v){this.attributes.src=v;}
  get src(){return this.attributes.src;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  getBoundingClientRect(){return {left:0,width:100};}
  focus(){this.focused=true;}
  pause(){}
  load(){this.loads=(this.loads||0)+1;}
}
function fixture(){
  const directory=path.join(__dirname,'../src/vaarattu_shorts/static');
  const nodes=new Map([...fs.readFileSync(path.join(directory,'index.html'),'utf8').matchAll(/\bid="([^"]+)"/g)].map(m=>[m[1],new Element()]));
  const writes=[],errors=[],reads=[];
  const plan={retained:[{asset:"s0",start_us:10000000,end_us:20000000,sequence:"b1"},{asset:"s1",start_us:30000000,end_us:35000000,sequence:"b2"}],rankings:[{id:"b1",score:80,reason:"A complete story"}]};
  const run={id:'a'.repeat(32),title:'Recording',state:'completed',stage:'completed',progress:1,message:'Ready',revision:1,has_draft:true,has_final:false,duration:360,issues:[],history:[],usage:{request_count:5},sources:[{asset:'s0',title:'Part 1',duration:100},{asset:'s1',title:'Part 2',duration:200}]};
  const context=vm.createContext({
    $:id=>nodes.get(id),document:{createElement:()=>new Element()},text:(tag,value)=>Object.assign(new Element(),{textContent:value}),
    action:(label,fn)=>Object.assign(new Element(),{textContent:label,onclick:()=>Promise.resolve(fn()).catch(e=>errors.push(e.message))}),error:message=>errors.push(message),
    window:{location:{hostname:"localhost"}},currentView:'highlights',setTimeout:()=>1,clearTimeout:()=>{},crypto:{randomUUID:()=> 'request-key'},
    goView:view=>{context.currentView=view;},
    api:async(url,options)=>{
      if(!options){reads.push(url);if(url.includes('/plan?'))return plan;if(url.includes('/selection?'))return {revision:run.revision,plan_hash:'f'.repeat(64),current_floor:75,previews:Array.from({length:101},(_,score)=>({score_floor:score,duration:score>90?0:score>=80?120:360,scenes:score>90?0:score>=80?2:6,sections:score>90?0:score>=80?4:12,added:0,removed:score>90?6:score>=80?4:0}))};if(url==='/api/status')return {providers:{codex:{available:true,configured:true,model:'configured'}}};return [run];}
      const body=JSON.parse(options.body);writes.push({url,body});
      if(url.endsWith('/resolve'))return {id:'b'.repeat(32),title:'Recording',sources:[{asset:'s0',id:'abc_def-ghI',provider:'youtube',url:'https://youtu.be/abc_def-ghI',title:'Part 1',duration:100},{asset:'s1',id:'123',provider:'twitch',url:'https://www.twitch.tv/videos/123',title:'Part 2',duration:200}],duration:300,notes:[]};
      if(url.endsWith('/copy/generate'))return {id:'generated',title:'Generated title',caption:'Generated description'};
      if(url.endsWith('/thumbnail/frame'))return {id:'c'.repeat(32),kind:'frame',seconds:body.seconds,quality:'draft'};
      if(url.endsWith('/thumbnail/generate'))return {id:body.request_id,kind:'thumbnail',status:'completed',seconds:1,quality:body.quality};
      if(url.endsWith('/copy'))return {id:'saved',title:body.title,caption:body.caption};
      return {id:run.id};
    }
  });
  vm.runInContext(fs.readFileSync(path.join(directory,'highlight-timeline.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(directory,'highlight-copy.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(directory,'highlight-thumbnails.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(directory,'highlight-selection.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(directory,'highlight-sources.js'),'utf8'),context);
  vm.runInContext(fs.readFileSync(path.join(directory,'highlights.js'),'utf8'),context);
  return {nodes,writes,errors,run,context,reads,plan};
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
  assert.ok(nodes.get('highlight-notes').children.some(n=>n.textContent==='12 model requests in this revision'));
  assert.match(nodes.get('highlight-meta').textContent,/Revision 1/);
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


test('minimum final score is submitted and planned length is visible before rendering',async()=>{
  const {nodes,context,writes,run}=fixture();
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  nodes.get('highlight-provider').value='codex';nodes.get('highlight-score-floor').value='80';
  await nodes.get('highlight-form').onsubmit({preventDefault(){}});
  assert.equal(writes.find(w=>w.url==='/api/highlights').body.final_score_floor,80);
  run.state='running';run.stage='media-4-s0-0';run.has_draft=false;
  run.selection_preview={score_floor:75,scenes:35,duration:1082};
  await vm.runInContext('loadHighlights()',context);
  assert.ok(nodes.get('highlight-notes').children.some(n=>n.textContent.includes('35 scenes, 18m 2s, minimum score 75')));
});


test('source timeline spans all parts and seeks into the rendered edit, not source time',async()=>{
  const {nodes,context,reads}=fixture();await vm.runInContext('loadHighlights()',context);
  const sections=nodes.get('highlight-timeline-ranges').children;
  assert.equal(sections.length,2);
  assert.equal(sections[1].style.left,`${130/300*100}%`);
  assert.match(sections[1].getAttribute('aria-label'),/Part 2.*source 0:00:30.*video 0:00:10/);
  sections[1].onclick({detail:1,clientX:50});
  assert.equal(nodes.get('highlight-player').currentTime,12.5);
  assert.equal(nodes.get('highlight-timeline-playhead').style.left,`${132.5/300*100}%`);
  sections[0].onclick({detail:0});assert.equal(nodes.get('highlight-player').currentTime,0);
  nodes.get('highlight-zoom-in').onclick();assert.equal(nodes.get('highlight-timeline-track').style.width,'200%');
  nodes.get('highlight-timeline-viewport').scrollLeft=150;
  await vm.runInContext('loadHighlights()',context);
  assert.equal(reads.filter(url=>url.includes('/plan?')).length,1);
  assert.equal(nodes.get('highlight-timeline-viewport').scrollLeft,150);
  assert.equal(nodes.get('highlight-timeline-track').style.width,'200%');
  nodes.get('highlight-zoom-fit').onclick();assert.equal(nodes.get('highlight-timeline-track').style.width,'100%');
});

test('timeline playhead jumps over removed speech at the exact output cut',async()=>{
  const {nodes,context}=fixture();await vm.runInContext('loadHighlights()',context);
  const player=nodes.get('highlight-player');player.currentTime=10;player.listeners.timeupdate();
  assert.equal(nodes.get('highlight-timeline-playhead').style.left,`${130/300*100}%`);
  assert.equal(nodes.get('highlight-timeline-ranges').children[1].getAttribute('aria-current'),'true');
  player.currentTime=15;player.listeners.timeupdate();assert.equal(nodes.get('highlight-timeline-playhead').hidden,true);
});

test('timeline uses per-section frame rounding from the renderer',()=>{
  const {context}=fixture();
  const model=vm.runInContext(`highlightTimelineModel([{asset:'s0',duration:1}],{retained:[
    {asset:'s0',start_us:0,end_us:150000},
    {asset:'s0',start_us:200000,end_us:250000}
  ]})`,context);
  assert.equal(model.ranges[1].outputStart,4/30);
  assert.equal(model.outputDuration,6/30);
});

test('late timeline response cannot replace a different selected recording',async()=>{
  const {context,nodes,run,plan}=fixture();let resolve;
  context.api=()=>new Promise(done=>{resolve=done;});context.run=run;
  const pending=vm.runInContext('loadHighlightTimeline(run)',context);
  await vm.runInContext('loadHighlightTimeline(null)',context);
  resolve(plan);await pending;
  assert.equal(nodes.get('highlight-timeline').hidden,true);
  assert.equal(nodes.get('highlight-timeline-ranges').children.length,0);
});

test('timeline failures offer explicit retry without repeatedly fetching during polling',async()=>{
  const {context,nodes,run,plan}=fixture();let requests=0;
  context.api=async()=>{requests++;if(requests===1)throw new Error('Unavailable');return plan;};context.run=run;
  await vm.runInContext('loadHighlightTimeline(run)',context);
  assert.equal(nodes.get('highlight-timeline-retry').hidden,false);
  await vm.runInContext('loadHighlightTimeline(run)',context);assert.equal(requests,1);
  await nodes.get('highlight-timeline-retry').onclick();assert.equal(requests,2);
  assert.equal(nodes.get('highlight-timeline-ranges').children.length,2);
});


test('highlight posting text is explicit for existing drafts and preserves edits across polling',async()=>{
  const {context,nodes,writes,run}=fixture();await vm.runInContext('loadHighlights()',context);
  assert.equal(writes.length,0);
  nodes.get('highlight-copy-note').value='Focus on gamer humor';
  await nodes.get('highlight-copy-generate').onclick();
  assert.equal(writes[0].body.note,'Focus on gamer humor');assert.equal(writes[0].body.revision,1);
  assert.equal(nodes.get('highlight-copy-title').value,'Generated title');
  nodes.get('highlight-copy-title').value='My title';nodes.get('highlight-copy-title').oninput();
  await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-copy-title').value,'My title');assert.equal(nodes.get('highlight-copy-generate').disabled,true);
  await nodes.get('highlight-copy-save').onclick();
  assert.equal(writes[1].body.expected_id,'generated');assert.equal(writes[1].body.title,'My title');
  assert.equal(nodes.get('highlight-copy-generate').disabled,false);
  run.revision=2;run.publishing_copy=null;await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-copy-title').value,'');assert.equal(nodes.get('highlight-copy-note').value,'');
});

test('late generated text cannot replace another revision in the form',async()=>{
  const {context,nodes,run}=fixture();await vm.runInContext('loadHighlights()',context);
  let resolve;context.api=()=>new Promise(done=>{resolve=done;});
  const pending=nodes.get('highlight-copy-generate').onclick();
  run.revision=2;context.changedRun=run;vm.runInContext('paintHighlightCopy(changedRun)',context);
  resolve({id:'old',title:'Wrong revision',caption:'Old'});await pending;
  assert.equal(nodes.get('highlight-copy-title').value,'');
});


test('thumbnail capture and generation are explicit and use the paused output time',async()=>{
  const {nodes,run,writes,context}=fixture();run.thumbnails={configured:true,items:[]};
  await vm.runInContext('loadHighlights()',context);
  assert.equal(writes.length,0);assert.equal(nodes.get('highlight-thumbnail-generate').disabled,true);
  const player=nodes.get('highlight-player');player.readyState=2;player.currentTime=12.5;
  await nodes.get('highlight-thumbnail-capture').onclick();
  assert.equal(writes[0].body.seconds,12.5);assert.equal(writes[0].body.revision,1);
  assert.equal(nodes.get('highlight-thumbnail-frames').children.length,1);
  assert.equal(nodes.get('highlight-thumbnail-generate').disabled,false);
  nodes.get('highlight-thumbnail-note').value='Keep my face recognizable';
  await nodes.get('highlight-thumbnail-generate').onclick();
  assert.deepEqual(writes[1].body.frame_ids,['c'.repeat(32)]);
  assert.equal(writes[1].body.note,'Keep my face recognizable');
  assert.equal(writes[1].body.quality,'medium');
  assert.equal(nodes.get('highlight-thumbnail-results').children.length,1);
  assert.match(nodes.get('highlight-thumbnail-results').children[0].children[1].href,/download=true/);
});

test('thumbnail generation blocks unsaved copy and resets references on a new revision',async()=>{
  const {nodes,run,writes,context,errors}=fixture();
  run.thumbnails={configured:true,items:[{id:'c'.repeat(32),kind:'frame',seconds:1,quality:'draft'}]};
  await vm.runInContext('loadHighlights()',context);
  nodes.get('highlight-copy-title').value='Unsaved title';nodes.get('highlight-copy-title').oninput();
  await nodes.get('highlight-thumbnail-generate').onclick();
  assert.equal(writes.length,0);assert.match(errors[0],/Save the title/);
  run.revision=2;run.thumbnails={configured:true,items:[]};await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-thumbnail-frames').children.length,0);
  assert.equal(nodes.get('highlight-thumbnail-generate').disabled,true);
});

test('late thumbnail capture does not replace another revision reference',async()=>{
  const {nodes,run,context}=fixture();run.thumbnails={configured:true,items:[]};
  await vm.runInContext('loadHighlights()',context);
  nodes.get('highlight-player').readyState=2;
  let finish;context.api=()=>new Promise(resolve=>{finish=resolve;});
  const pending=nodes.get('highlight-thumbnail-capture').onclick();
  const replacement={...run,revision:2,thumbnails:{configured:true,items:[]}};
  context.nextRun=replacement;vm.runInContext('highlightRuns=[nextRun];paintHighlightThumbnails(nextRun)',context);
  finish({id:'c'.repeat(32),kind:'frame',seconds:0,quality:'draft'});await pending;
  assert.equal(nodes.get('highlight-thumbnail-frames').children.length,0);
  assert.equal(replacement.thumbnails.items.length,0);
});


test('score slider previews duration immediately without requests or rendering',async()=>{
  const {nodes,writes,reads,context}=fixture();await vm.runInContext('loadHighlights()',context);
  const before=reads.length;
  assert.equal(nodes.get('highlight-selection-render').disabled,true);
  nodes.get('highlight-selection-slider').oninput({target:{value:'80'}});
  assert.match(nodes.get('highlight-selection-summary').textContent,/0:02:00.*2 scenes/);
  assert.equal(nodes.get('highlight-selection-number').value,'80');
  assert.equal(nodes.get('highlight-selection-render').disabled,false);
  assert.equal(writes.length,0);assert.equal(reads.length,before);
  await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-selection-slider').value,'80');
  assert.equal(reads.filter(u=>u.includes('/selection?')).length,1);
  await nodes.get('highlight-selection-render').onclick();
  assert.equal(writes.length,1);assert.match(writes[0].url,/\/selection$/);
  assert.equal(writes[0].body.score_floor,80);assert.equal(writes[0].body.plan_hash,'f'.repeat(64));
});

test('empty selection cannot render and running jobs allow previews only',async()=>{
  const {nodes,run,writes,context}=fixture();await vm.runInContext('loadHighlights()',context);
  nodes.get('highlight-selection-number').onchange({target:{value:'100'}});
  assert.match(nodes.get('highlight-selection-change').textContent,/No scenes/);
  assert.equal(nodes.get('highlight-selection-render').disabled,true);
  await nodes.get('highlight-selection-render').onclick();assert.equal(writes.length,0);
  run.state='running';await vm.runInContext('loadHighlights()',context);
  nodes.get('highlight-selection-slider').oninput({target:{value:'80'}});
  assert.equal(nodes.get('highlight-selection-render').disabled,true);
  assert.equal(nodes.get('highlight-selection-slider').disabled,false);
});

test('late score preview cannot replace another revision and failures support retry',async()=>{
  const {nodes,run,context}=fixture();await vm.runInContext('loadHighlights()',context);
  let finish;context.api=()=>new Promise(resolve=>{finish=resolve;});
  const pending=vm.runInContext('loadHighlightSelection(highlightRuns[0],true)',context);
  vm.runInContext('loadHighlightSelection(null)',context);
  finish({current_floor:0,previews:[]});await pending;
  assert.equal(nodes.get('highlight-selection').hidden,true);
  context.api=async()=>{throw Error('Preview unavailable');};
  await vm.runInContext('loadHighlightSelection(highlightRuns[0],true)',context);
  assert.equal(nodes.get('highlight-selection-retry').hidden,false);
  assert.match(nodes.get('highlight-selection-summary').textContent,/Preview unavailable/);
});


test('tool tabs preserve playback and unsaved posting text without making requests',async()=>{
  const {nodes,writes,reads,context}=fixture();await vm.runInContext('loadHighlights()',context);
  const player=nodes.get('highlight-player'),before=reads.length;player.currentTime=42;
  nodes.get('highlight-tab-publish').onclick();
  assert.equal(nodes.get('highlight-panel-review').hidden,true);
  assert.equal(nodes.get('highlight-panel-publish').hidden,false);
  nodes.get('highlight-copy-title').value='Unsaved title';nodes.get('highlight-copy-title').oninput();
  nodes.get('highlight-tab-details').onclick();nodes.get('highlight-tab-publish').onclick();
  assert.equal(player.loads,1);assert.equal(player.currentTime,42);
  assert.equal(nodes.get('highlight-copy-title').value,'Unsaved title');
  assert.equal(writes.length,0);assert.equal(reads.length,before);
  await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-panel-publish').hidden,false);
});

test('tool tabs support keyboard navigation and skip unavailable finishing controls',async()=>{
  const {nodes,run,context}=fixture();await vm.runInContext('loadHighlights()',context);
  let prevented=0;
  nodes.get('highlight-tab-review').onkeydown({key:'ArrowRight',preventDefault(){prevented++;}});
  assert.equal(nodes.get('highlight-tab-publish').getAttribute('aria-selected'),'true');
  assert.equal(nodes.get('highlight-tab-publish').focused,true);
  assert.equal(nodes.get('highlight-tab-review').tabIndex,-1);
  run.has_draft=false;run.state='running';run.revision=2;await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-tab-publish').disabled,true);
  nodes.get('highlight-tab-details').onkeydown({key:'ArrowLeft',preventDefault(){prevented++;}});
  assert.equal(nodes.get('highlight-tab-review').getAttribute('aria-selected'),'true');
  assert.equal(prevented,2);
});

test('existing recordings start with the creation form closed and library actions open it',async()=>{
  const {nodes,context}=fixture();await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-create').open,false);
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  assert.equal(nodes.get('highlight-create').open,true);
  await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-create').open,true);
});

test('recording selection and editing notes remain accessible without starting work',async()=>{
  const {nodes,run,writes,context}=fixture();run.issues=[{instruction:'Check the final join'}];
  await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-progress-panel').hidden,true);
  assert.match(nodes.get('highlight-notice').textContent,/1 editing note/);
  nodes.get('highlight-notice').onclick();
  assert.equal(nodes.get('highlight-panel-details').hidden,false);
  context.second={...run,id:'b'.repeat(32),title:'Second recording'};
  vm.runInContext('highlightRuns.push(second);paintHighlights()',context);
  nodes.get('highlight-runs').value=context.second.id;nodes.get('highlight-runs').onchange();
  assert.equal(nodes.get('highlight-title').textContent,'Second recording');
  assert.equal(nodes.get('highlight-panel-review').hidden,false);
  assert.equal(writes.length,0);
});
function sourceControls(nodes){
  const all=[];function walk(node){all.push(node);for(const child of node.children||[])walk(child);}
  walk(nodes.get('highlight-parts'));
  return {all,byLabel:label=>all.find(n=>n.getAttribute('aria-label')===label)};
}
test('source times accept precise VOD timestamps and reject malformed input',()=>{
  const {context}=fixture();
  for(const [value,expected] of [['1:02:03.125',3723.125],['12:30',750],['3600',3600],['0',0]]){
    assert.equal(vm.runInContext(`parseHighlightSourceTime(${JSON.stringify(value)})`,context),expected);
  }
  for(const value of ['', '-1','1:60','1.5:20','1:2:3:4','0:00.0001'])assert.ok(Number.isNaN(vm.runInContext(`parseHighlightSourceTime(${JSON.stringify(value)})`,context)));
});
test('selected sources submit original timestamps, title and user-selected order',async()=>{
  const {nodes,writes,context,errors}=fixture();
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  assert.equal(writes[0].body.collection,true);
  let controls=sourceControls(nodes);
  for(const [label,value] of [['Start time: Part 1','0:10.125'],['End time: Part 1','0:40'],['Start time: Part 2','1:00'],['End time: Part 2','2:00']]){
    const input=controls.byLabel(label);input.value=value;input.oninput();
  }
  await controls.byLabel('Move earlier: Part 2').onclick();
  assert.match(nodes.get('highlight-source-summary').textContent,/2 recordings/);
  nodes.get('highlight-project-title').value='Diablo across three evenings';
  nodes.get('highlight-provider').value='codex';
  await nodes.get('highlight-form').onsubmit({preventDefault(){}});
  const body=writes.find(w=>w.url==='/api/highlights').body;
  assert.deepEqual(body.segments,[{asset:'s1',start_us:60000000,end_us:120000000},{asset:'s0',start_us:10125000,end_us:40000000}]);
  assert.equal(body.project_title,'Diablo across three evenings');assert.deepEqual(errors,[]);
  assert.equal(writes.length,2);
});
test('invalid ranges and empty selections cannot start; excluded players stop loading',async()=>{
  const {nodes,writes,context,errors}=fixture();
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  const controls=sourceControls(nodes),start=controls.byLabel('Start time: Part 1');
  start.value='101';start.oninput();assert.equal(nodes.get('highlight-start').disabled,true);
  await nodes.get('highlight-form').onsubmit({preventDefault(){}});
  assert.equal(writes.length,1);assert.match(errors[0],/start and end/);
  const first=controls.byLabel('Include Part 1');first.checked=false;first.onchange();
  assert.equal(nodes.get('highlight-start').disabled,false);
  assert.equal(controls.all.find(n=>n.title==='Preview Part 1').src,undefined);
  const second=controls.byLabel('Include Part 2');second.checked=false;second.onchange();
  assert.equal(nodes.get('highlight-start').disabled,true);
  await nodes.get('highlight-form').onsubmit({preventDefault(){}});
  assert.equal(writes.length,1);assert.match(errors[1],/at least one/);
});
test('embeds preview boundaries without autoplay or processing requests',async()=>{
  const {nodes,writes,context}=fixture();await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  const controls=sourceControls(nodes),frame=controls.all.find(n=>n.title==='Preview Part 2');
  assert.match(frame.src,/player.twitch.tv\/\?video=v123&parent=localhost&autoplay=false/);
  assert.equal(frame.referrerPolicy,'strict-origin-when-cross-origin');
  const start=controls.byLabel('Start position: Part 2');start.value='70';start.oninput();
  assert.equal(controls.byLabel('Start time: Part 2').value,'0:01:10');
  const end=controls.byLabel('End time: Part 2');end.value='90';end.oninput();
  const previews=controls.all.filter(n=>n.textContent==='Preview end');await previews[1].onclick();
  assert.match(frame.src,/time=0h1m25s$/);assert.equal(writes.length,1);
  assert.match(controls.all.find(n=>n.title==='Preview Part 1').src,/www.youtube.com\/embed\/abc_def-ghI\?autoplay=0&start=0/);
});
test('reloading metadata preserves selected ranges for matching sources',async()=>{
  const {nodes,context}=fixture();await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  const start=sourceControls(nodes).byLabel('Start time: Part 1');start.value='25';start.oninput();
  nodes.get('highlight-urls').oninput();await nodes.get('highlight-resolve').onclick();
  assert.equal(sourceControls(nodes).byLabel('Start time: Part 1').value,'0:00:25');
});
test('selected-source timeline omits excluded hours but preserves absolute clock labels',()=>{
  const {context}=fixture();
  const model=vm.runInContext(`highlightTimelineModel([
    {asset:'s0',duration:10000,selection_start_us:3600000000,selection_end_us:3660000000},
    {asset:'s1',duration:20000,selection_start_us:7200000000,selection_end_us:7320000000}
  ],{retained:[{asset:'s0',start_us:3610000000,end_us:3620000000},{asset:'s1',start_us:7230000000,end_us:7240000000}]})`,context);
  assert.equal(model.duration,180);assert.equal(model.ranges[0].start,10);assert.equal(model.ranges[1].start,90);
  assert.equal(model.ranges[1].sourceStart,7230);assert.equal(model.ranges[1].outputStart,10);
  context.selectedRange=model.ranges[1];assert.match(vm.runInContext('highlightRangeLabel(selectedRange)',context),/source 2:00:30.*video 0:00:10/);
});

test('rebuild labels make clear that a partial project keeps its selected ranges',async()=>{
  const {nodes,run,context}=fixture();run.sources[0].selection_start_us=10000000;
  run.sources[0].selection_end_us=80000000;run.state='paused';
  await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-rebuild').textContent,'Rebuild from selected ranges');
  assert.ok(nodes.get('highlight-controls').children.some(n=>n.textContent==='Rebuild from selected ranges'));
});


test('editorial results distinguish unresolved work from technical warnings',async()=>{
  const {nodes,run,context}=fixture();
  run.editorial={verified:25,unresolved:2,not_reviewed:3,corrections:12};
  run.warnings=[];
  await vm.runInContext('loadHighlights()',context);
  assert.ok(nodes.get('highlight-notes').children.some(n=>n.textContent.includes('25 scenes verified, 2 with unresolved notes, 3 not yet checked. 12 corrections applied.')));
});


test('thumbnail references accumulate, can be deselected, and retain selection during polling',async()=>{
  const {nodes,run,context,writes}=fixture();
  run.thumbnails={configured:true,items:[{id:'d'.repeat(32),kind:'frame',seconds:3,quality:'draft'}]};
  await vm.runInContext('loadHighlights()',context);
  nodes.get('highlight-player').readyState=2;nodes.get('highlight-player').currentTime=12.5;
  await nodes.get('highlight-thumbnail-capture').onclick();
  assert.equal(nodes.get('highlight-thumbnail-frames').children.length,2);
  await nodes.get('highlight-thumbnail-generate').onclick();
  assert.deepEqual(writes[1].body.frame_ids,['d'.repeat(32),'c'.repeat(32)]);
  const checkbox=nodes.get('highlight-thumbnail-frames').children[1].children[1];
  checkbox.checked=false;checkbox.onchange();
  await vm.runInContext('loadHighlights()',context);
  await nodes.get('highlight-thumbnail-generate').onclick();
  assert.deepEqual(writes[2].body.frame_ids,['c'.repeat(32)]);
  const remaining=nodes.get('highlight-thumbnail-frames').children[0].children[1];
  remaining.checked=false;remaining.onchange();
  await vm.runInContext('loadHighlights()',context);
  assert.equal(nodes.get('highlight-thumbnail-generate').disabled,true);
  assert.match(nodes.get('highlight-thumbnail-frame-label').textContent,/0 of 16/);
});

test('thumbnail reference limit permits removing a selection without starting generation',async()=>{
  const {nodes,run,context,writes}=fixture();
  run.thumbnails={configured:true,items:Array.from({length:17},(_,i)=>({id:i.toString(16).padStart(32,'0'),kind:'frame',seconds:i,quality:'draft'}))};
  await vm.runInContext('loadHighlights()',context);
  for(let i=1;i<16;i++){
    const checkbox=nodes.get('highlight-thumbnail-frames').children[i].children[1];checkbox.checked=true;checkbox.onchange();
  }
  assert.equal(nodes.get('highlight-thumbnail-capture').disabled,true);
  assert.equal(nodes.get('highlight-thumbnail-frames').children[16].children[1].disabled,true);
  assert.equal(nodes.get('highlight-thumbnail-frames').children[0].children[1].disabled,false);
  assert.equal(writes.length,0);
});


test('additional Codex Luna model is submitted separately from its provider',async()=>{
  const {nodes,context,writes}=fixture();
  await vm.runInContext('loadHighlights()',context);
  assert.ok(nodes.get('highlight-provider').children.some(o=>o.value==='codex'));
  assert.ok(nodes.get('highlight-provider').children.some(o=>o.value==='codex-gpt-6-luna'));
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  nodes.get('highlight-provider').value='codex-gpt-6-luna';nodes.get('highlight-provider').onchange();
  assert.equal(nodes.get('highlight-budget-field').hidden,true);
  assert.match(nodes.get('highlight-provider-note').textContent,/Codex bridge/);
  await nodes.get('highlight-form').onsubmit({preventDefault(){}});
  const body=writes.find(w=>w.url==='/api/highlights').body;
  assert.equal(body.provider,'codex');assert.equal(body.codex_model,'gpt-6-luna');assert.equal(body.budget_usd,0);
});


test('highlight reasoning controls follow provider and submit independent efforts',async()=>{
  const {nodes,context,writes}=fixture();
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  for(const provider of ['local','gemini','openai','codex','codex-gpt-6-luna']){
    nodes.get('highlight-provider').value=provider;nodes.get('highlight-provider').onchange();
    assert.equal(nodes.get('highlight-reasoning-settings').hidden,['local','gemini'].includes(provider));
  }
  nodes.get('highlight-discovery-reasoning').value='none';
  nodes.get('highlight-verification-reasoning').value='xhigh';
  await nodes.get('highlight-form').onsubmit({preventDefault(){}});
  const body=writes.find(w=>w.url==='/api/highlights').body;
  assert.equal(body.discovery_reasoning,'none');assert.equal(body.verification_reasoning,'xhigh');
});

test('all reasoning selectors expose the requested range and keep Low as default',()=>{
  const html=fs.readFileSync(path.join(__dirname,'../src/vaarattu_shorts/static/index.html'),'utf8');
  for(const id of ['discovery-reasoning','verification-reasoning','highlight-discovery-reasoning','highlight-verification-reasoning']){
    const select=html.match(new RegExp(`<select id="${id}">([\\s\\S]*?)</select>`))[1];
    assert.deepEqual([...select.matchAll(/value="([^"]+)"/g)].map(m=>m[1]),['none','low','medium','high','xhigh']);
    assert.match(select,/<option value="low" selected>/);
  }
});


test('a new library video clears the previous project name without starting a run',async()=>{
  const {nodes,writes,context}=fixture();
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  nodes.get('highlight-project-title').value='Previous project';
  await vm.runInContext('openHighlights("https://youtu.be/different01")',context);
  assert.equal(nodes.get('highlight-project-title').value,'Recording');
  assert.ok(writes.every(w=>w.url==='/api/highlights/resolve'));
});

test('new metadata replaces an automatic title but preserves a custom title',async()=>{
  const {nodes,context}=fixture();
  await vm.runInContext('openHighlights("https://youtu.be/abc_def-ghI")',context);
  vm.runInContext('setHighlightSources({...highlightManifest,title:"Next recording"})',context);
  assert.equal(nodes.get('highlight-project-title').value,'Next recording');
  nodes.get('highlight-project-title').value='My combined project';
  vm.runInContext('setHighlightSources({...highlightManifest,title:"Third recording"})',context);
  assert.equal(nodes.get('highlight-project-title').value,'My combined project');
});

test('adding scenes explains that final review may change the preview length',async()=>{
  const {nodes,context,writes}=fixture();await vm.runInContext('loadHighlights()',context);
  vm.runInContext('highlightSelection.data.previews[60].added=2;changeHighlightFloor(60)',context);
  assert.match(nodes.get('highlight-selection-change').textContent,/Final editorial review.*may change this length/);
  assert.equal(writes.length,0);
});
