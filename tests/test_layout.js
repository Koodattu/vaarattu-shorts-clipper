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
for(const shape of ["free","panel","16:9","4:3","1:1","9:16"]){
  for(const height of [192,960,1728])for(const mode of ["camera","game"]){
    const r=panelRectangle({x:0.8,y:0.7},{x:0.1,y:0.1},mode,1920,1080,shape,height);
    assert(r.x>=0&&r.y>=0&&r.x+r.width<=1&&r.y+r.height<=1);
    if(shape!=="free")assert(Math.abs(r.width/r.height-cropRatio(mode,1920,1080,shape,height))<1e-10);
    else {assert.equal(r.width,0.8-0.1);assert.equal(r.height,0.7-0.1);}
    const panels=layoutPanels(height);assert.equal(panels.camera.height+panels.game.height,1920);
    assert.deepEqual(panelSource(r,1920,1080,mode,height,"contain"),sourceCrop(r,1920,1080));
  }
}
assert.deepEqual(moveRectangle({x:0.2,y:0.2,width:0.3,height:0.4},2,-2),{x:0.7,y:0,width:0.3,height:0.4});
console.log("Layout geometry checks passed");

// Exercise the editor wiring without starting a server, browser or media processing.
const nodes=new Map(),draws=[];
global.document={getElementById(id){
  if(!nodes.has(id))nodes.set(id,{value:"",width:id==="layout-preview"?270:960,height:id==="layout-preview"?480:540,clientWidth:960,clientHeight:540,clientLeft:1,clientTop:1,
    style:{},classList:{add(){},remove(){}},setPointerCapture(){},setCustomValidity(message){this.validationMessage=message;},
    getContext:()=>({clearRect(){},strokeRect(){},fillRect(){},fillText(){},drawImage(...args){draws.push({id,args});}}),
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
nodes.get("frame-file").onchange({target:{files:[{name:"frame.png",type:"image/png"}]}});
assert.equal(nodes.get("camera-rect").value,"0, 0.6, 0.3, 0.4","Loading a screenshot must preserve the selected crops");
assert.equal(nodes.get("game-rect").value,"0.31, 0, 0.4, 1");
assert.equal(draws.find(d=>d.id==="layout-preview").args[8],152);
assert.equal(draws.filter(d=>d.id==="layout-preview")[1].args[6],152);
assert.deepEqual(vm.runInThisContext('point({clientX:491,clientY:291})'),{x:0.5,y:0.5});
vm.runInThisContext('setFrame({width:1920,height:1080})');
assert.equal(nodes.get("camera-rect").value,"");
assert.equal(nodes.get("game-rect").value,"");
vm.runInThisContext('savedLayouts=[{id:"wrong",body:{name:"Other"}},{id:"correct",body:fixturePreset}]; api=async()=>({layout:fixturePreset,has_source:false,start_us:0,end_us:1000000,words:[],title:"Clip"});');
openEditor("clip").then(async()=>{
  assert.equal(nodes.get("edit-layout").value,"correct");
  assert.equal(nodes.get("use-source-frame").disabled,true);
  assert(vm.runInThisContext('sameLayout(fixturePreset,{...fixturePreset,camera_height:608,camera_fit:"cover",gameplay_fit:"cover",camera_ratio:"panel",gameplay_ratio:"panel"})'));
  vm.runInThisContext('setFrame({width:1920,height:1080},fixturePreset);setDrawMode("camera");');
  const crop=nodes.get("crop-canvas"),event=(x,y)=>({clientX:11+x*960,clientY:21+y*540,pointerId:1,button:0,preventDefault(){}});
  crop.onpointerdown(event(0.1,0.7));crop.onpointermove(event(0.2,0.6));crop.onpointerup();
  let r=vm.runInThisContext('rectangles.camera');
  assert(Math.abs(r.x-0.1)<1e-10&&Math.abs(r.y-0.5)<1e-10);assert.equal(r.width,0.3);assert.equal(r.height,0.4);
  assert.equal(nodes.get("calibrated").checked,false);
  nodes.get("crop-ratio").value="free";nodes.get("crop-ratio").onchange();
  crop.onpointerdown(event(0.4,0.9));crop.onpointermove(event(0.5,0.95));crop.onpointerup();
  r=vm.runInThisContext('rectangles.camera');assert(Math.abs(r.width-0.4)<1e-10&&Math.abs(r.height-0.45)<1e-10);
  const before={...r};crop.onpointerdown(event(0.2,0.6));crop.onpointermove(event(0.3,0.6));crop.onpointercancel();
  assert.deepEqual(vm.runInThisContext('rectangles.camera'),before);
  crop.onkeydown({key:"ArrowRight",shiftKey:true,preventDefault(){}});
  assert(Math.abs(vm.runInThisContext('rectangles.camera.x')-before.x-10/1920)<1e-10);
  nodes.get("draw-game").onclick();
  const gameBefore=vm.runInThisContext('({...rectangles.game})');
  crop.onpointerdown(event(0.6,0.3));crop.onpointermove(event(0.9,0.4));crop.onpointerup();
  const gameAfter=vm.runInThisContext('rectangles.game');
  assert.equal(gameAfter.width,gameBefore.width);assert.equal(gameAfter.height,gameBefore.height);assert.equal(gameAfter.x,0.6);
  draws.length=0;nodes.get("camera-height").value="960";nodes.get("camera-height").oninput();
  assert.equal(draws.find(d=>d.id==="layout-preview").args[8],240);
  assert.equal(draws.filter(d=>d.id==="layout-preview")[1].args[6],240);
  nodes.get("crop-fit").value="contain";nodes.get("crop-fit").onchange();
  const previewDraw=draws.filter(d=>d.id==="layout-preview").at(-1).args;
  assert(previewDraw[7]<270||previewDraw[8]<240,"Contain must leave bars when shapes differ");
  nodes.get("camera-rect").value="0,0,0.2,0.2,0.5";nodes.get("camera-rect").oninput();assert(nodes.get("camera-rect").validationMessage);
  vm.runInThisContext('changed()');assert.equal(nodes.get("camera-rect").validationMessage,"");
  // Loading a saved screenshot is asynchronous; selecting another preset must invalidate it.
  const pending=[];global.Image=class {constructor(){this.width=1920;this.height=1080;pending.push(this);}set src(value){this.url=value;}};
  vm.runInThisContext('savedLayouts=[{id:"saved",body:fixturePreset,screenshot_name:"saved.jpg"}];');
  nodes.get("preset-select").value="saved";nodes.get("preset-select").onchange();
  assert.equal(pending[0].url,"/api/layouts/saved/screenshot");
  nodes.get("preset-select").value="";nodes.get("preset-select").onchange();pending[0].onload();
  assert.equal(vm.runInThisContext('frame'),null);
  nodes.get("preset-select").value="saved";nodes.get("preset-select").onchange();pending[1].onload();
  assert.equal(vm.runInThisContext('frameName'),"saved.jpg");assert.equal(nodes.get("camera-rect").value,"0, 0.6, 0.3, 0.4");
  nodes.get("frame-drop").ondrop({preventDefault(){},dataTransfer:{files:[{name:"dropped.png",type:"image/png"}]}});
  pending[2].onload();assert.equal(vm.runInThisContext('frameName'),"dropped.png");
  document.createElement=tag=>({tagName:tag,getContext:()=>({drawImage(){}}),toDataURL:()=>"data:image/jpeg;base64,/9j/4P/Z"});
  for(const id of ["layout","edit-layout","preset-select"]){const node=document.getElementById(id);node.replaceChildren=function(){this.value="";};node.append=function(){};}
  nodes.get("layout-name").value="Actual clip";nodes.get("calibrated").checked=true;
  document.getElementById("layout-return").href="#process";
  vm.runInThisContext('globalThis.savedRequest=null;api=async(path,options={})=>{if(options.method==="POST"){savedRequest=JSON.parse(options.body);return {id:"saved"};}return [{id:"saved",body:fixturePreset,screenshot_name:"dropped.png"}];};');
  await nodes.get("layout-form").onsubmit({preventDefault(){}});
  assert.equal(global.savedRequest.screenshot.name,"dropped.png");
  assert.equal(global.savedRequest.camera_height,608);assert.equal(nodes.get("preset-select").value,"saved");assert.equal(nodes.get("save-layout").textContent,"Replace preset");
  assert.equal(global.savedRequest.screenshot.width,1920);assert.equal(global.savedRequest.screenshot.height,1080);
  // The local copy is smaller, but validation and pixel snapping still use the original dimensions.
  vm.runInThisContext('setFrame({width:1600,height:900},{...fixturePreset,camera:{x:0,y:0,width:34/1920,height:34/1080}},{width:1920,height:1080})');
  assert.equal(vm.runInThisContext('readRectangle("camera-rect").width'),34/1920);
  assert.equal(vm.runInThisContext('screenshotCopy().width'),1920);
  nodes.get("frame-drop").ondrop({preventDefault(){},dataTransfer:{files:[{name:"delayed.png",type:"image/png"}]}});
  nodes.get("camera-rect").value="0.1, 0.2, 0.3, 0.4";nodes.get("camera-rect").oninput();
  pending[3].onload();assert.equal(nodes.get("camera-rect").value,"0.1, 0.2, 0.3, 0.4","Image loading must preserve intervening coordinate edits");
  console.log("Layout editor wiring checks passed");
}).catch(error=>{console.error(error);process.exitCode=1;});
