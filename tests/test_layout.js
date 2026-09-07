"use strict";
const assert=require("node:assert/strict"),fs=require("node:fs"),vm=require("node:vm");
vm.runInThisContext(fs.readFileSync("src/vaarattu_shorts/static/layout.js","utf8"));
for(const mode of ["camera","game"]){
  for(const point of [{x:0,y:0},{x:1,y:1},{x:0,y:1},{x:1,y:0}]){
    const r=panelRectangle({x:0.5,y:0.5},point,mode,1920,1080);
    assert(r.x>=0&&r.y>=0&&r.x+r.width<=1&&r.y+r.height<=1);
    assert(Math.abs(r.width*1920/(r.height*1080)-cropPanels[mode].width/cropPanels[mode].height)<1e-10);
  }
  const rect={x:0.31,y:0.01,width:0.35,height:0.98};
  const source=sourceCrop(rect,1920,1080), filled=panelSource(rect,1920,1080,mode);
  assert(filled.x>=source.x&&filled.y>=source.y);
  assert(filled.x+filled.width<=source.x+source.width+1e-8);
  assert(filled.y+filled.height<=source.y+source.height+1e-8);
  assert(Math.abs(filled.width/filled.height-cropPanels[mode].width/cropPanels[mode].height)<1e-10);
}
assert.equal(cropPanels.camera.height+cropPanels.game.height,1920);
console.log("Layout geometry checks passed");

// Exercise the editor wiring without starting a server, browser or media processing.
const nodes=new Map(),draws=[];
global.document={getElementById(id){
  if(!nodes.has(id))nodes.set(id,{value:"",width:960,height:540,clientWidth:960,clientHeight:540,clientLeft:1,clientTop:1,
    getContext:()=>({clearRect(){},strokeRect(){},drawImage(...args){draws.push({id,args});}}),
    getBoundingClientRect:()=>({left:10,top:20}),scrollIntoView(){},focus(){},pause(){},querySelectorAll(){return [];},setAttribute(name,value){this[name]=value;}});
  return nodes.get(id);
}};
global.fetch=async()=>{throw new Error("Offline fixture");};
vm.runInThisContext(fs.readFileSync("src/vaarattu_shorts/static/app.js","utf8"));
const preset={name:"Actual clip",camera:{x:0,y:0.6,width:0.3,height:0.4},gameplay:{x:0.31,y:0,width:0.4,height:1},calibrated:true,solo_host:true};
global.fixturePreset=preset;
vm.runInThisContext('setFrame({width:1920,height:1080},fixturePreset)');
assert.equal(nodes.get("camera-rect").value,"0, 0.6, 0.3, 0.4");
assert.equal(nodes.get("calibrated").checked,false);
global.Image=class {constructor(){this.width=1920;this.height=1080;}set src(value){this.onload();}};
global.URL={createObjectURL:()=>"blob:fixture",revokeObjectURL(){}};
nodes.get("frame-file").onchange({target:{files:[{}]}});
assert.equal(nodes.get("camera-rect").value,"0, 0.6, 0.3, 0.4","Loading a screenshot must preserve the selected crops");
assert.equal(nodes.get("game-rect").value,"0.31, 0, 0.4, 1");
assert.equal(draws.find(d=>d.id==="layout-preview").args[8],152);
assert.equal(draws.filter(d=>d.id==="layout-preview")[1].args[6],152);
assert.deepEqual(vm.runInThisContext('point({clientX:491,clientY:291})'),{x:0.5,y:0.5});
vm.runInThisContext('setFrame({width:1920,height:1080})');
assert.equal(nodes.get("camera-rect").value,"");
assert.equal(nodes.get("game-rect").value,"");
vm.runInThisContext('savedLayouts=[{id:"wrong",body:{name:"Other"}},{id:"correct",body:fixturePreset}]; api=async()=>({layout:fixturePreset,has_source:false,start_us:0,end_us:1000000,words:[],title:"Clip"});');
openEditor("clip").then(()=>{
  assert.equal(nodes.get("edit-layout").value,"correct");
  assert.equal(nodes.get("use-source-frame").disabled,true);
  console.log("Layout editor wiring checks passed");
}).catch(error=>{console.error(error);process.exitCode=1;});
