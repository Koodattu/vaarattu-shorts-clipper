"use strict";
function navigateWorkspace(){
  const requested=window.location.hash.slice(1);
  if(requested==="workspace")return;
  const view=["library","gallery","review","process","results","layouts","editor"].includes(requested)?requested:"library";
  const destination=view==="editor"&&!editing?"results":view;
  if(destination!==view)window.history.replaceState(null,"","#"+destination);
  if(destination!==currentView)showView(destination);
}
window.addEventListener("hashchange",navigateWorkspace);
window.addEventListener("popstate",navigateWorkspace);
document.addEventListener("click",event=>{
  if(event.defaultPrevented||event.button!==0||event.metaKey||event.ctrlKey||event.shiftKey||event.altKey)return;
  const link=event.target.closest('a[href^="#"]');
  if(!link)return;
  const view=link.getAttribute("href").slice(1);
  if(!["library","gallery","review","process","results","layouts","editor"].includes(view))return;
  event.preventDefault();goView(view==="editor"&&!editing?"results":view);
});
navigateWorkspace();
