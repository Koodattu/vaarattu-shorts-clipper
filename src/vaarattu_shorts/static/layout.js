"use strict";
const cropPanels = {camera: {width:1080,height:608}, game: {width:1080,height:1312}};

function panelRectangle(origin, point, mode, width, height){
  const ratio=cropPanels[mode].width/cropPanels[mode].height*height/width;
  const w=Math.min(Math.abs(point.x-origin.x),Math.abs(point.y-origin.y)*ratio);
  const h=w/ratio;
  return {x:point.x<origin.x?origin.x-w:origin.x,y:point.y<origin.y?origin.y-h:origin.y,width:w,height:h};
}

function sourceCrop(rect,width,height){
  return {x:Math.floor(rect.x*width/2)*2,y:Math.floor(rect.y*height/2)*2,
    width:Math.floor(rect.width*width/2)*2,height:Math.floor(rect.height*height/2)*2};
}

function panelSource(rect,width,height,mode){
  const source=sourceCrop(rect,width,height), panel=cropPanels[mode];
  const scale=Math.max(panel.width/source.width,panel.height/source.height);
  const w=panel.width/scale,h=panel.height/scale;
  return {x:source.x+(source.width-w)/2,y:source.y+(source.height-h)/2,width:w,height:h};
}
