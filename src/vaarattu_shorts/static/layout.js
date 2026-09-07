"use strict";
const cropPanels = {camera: {width:1080,height:608}, game: {width:1080,height:1312}};
function layoutPanels(cameraHeight=608){
  return {camera:{width:1080,height:cameraHeight},game:{width:1080,height:1920-cameraHeight}};
}

function cropRatio(mode,width,height,shape="panel",cameraHeight=608){
  if(shape==="free")return null;
  const panel=layoutPanels(cameraHeight)[mode];
  const ratio=shape==="panel"?panel.width/panel.height:shape.split(":").map(Number).reduce((a,b)=>a/b);
  return ratio*height/width;
}

function panelRectangle(origin, point, mode, width, height,shape="panel",cameraHeight=608){
  const ratio=cropRatio(mode,width,height,shape,cameraHeight);
  const w=ratio?Math.min(Math.abs(point.x-origin.x),Math.abs(point.y-origin.y)*ratio):Math.abs(point.x-origin.x);
  const h=ratio?w/ratio:Math.abs(point.y-origin.y);
  return {x:point.x<origin.x?origin.x-w:origin.x,y:point.y<origin.y?origin.y-h:origin.y,width:w,height:h};
}

function moveRectangle(rect,dx,dy){
  return {...rect,x:Math.max(0,Math.min(1-rect.width,rect.x+dx)),y:Math.max(0,Math.min(1-rect.height,rect.y+dy))};
}

function cropCorners(rect){
  return [{x:rect.x,y:rect.y},{x:rect.x+rect.width,y:rect.y},
    {x:rect.x+rect.width,y:rect.y+rect.height},{x:rect.x,y:rect.y+rect.height}];
}

function sourceCrop(rect,width,height){
  return {x:Math.floor(rect.x*width/2)*2,y:Math.floor(rect.y*height/2)*2,
    width:Math.floor(rect.width*width/2)*2,height:Math.floor(rect.height*height/2)*2};
}

function panelSource(rect,width,height,mode,cameraHeight=608,fit="cover"){
  const source=sourceCrop(rect,width,height), panel=layoutPanels(cameraHeight)[mode];
  if(fit==="contain"||source.width===0||source.height===0)return source;
  const scale=Math.max(panel.width/source.width,panel.height/source.height);
  const w=panel.width/scale,h=panel.height/scale;
  return {x:source.x+(source.width-w)/2,y:source.y+(source.height-h)/2,width:w,height:h};
}
