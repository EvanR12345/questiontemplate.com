// Preserve typing position only when a redraw keeps the same project/chapter/tab.
// Navigation must retain its normal focus behavior and dialogs keep their own focus.
export function captureFormFocus(root,context){
  const control=root.ownerDocument.activeElement;
  if(root.dataset.focusContext!==context||!root.contains(control)||!control?.id)return null;
  return {id:control.id,type:control.type,tagName:control.tagName,
    start:control.selectionStart,end:control.selectionEnd,direction:control.selectionDirection,
    scrollTop:control.scrollTop,scrollLeft:control.scrollLeft};
}
export function restoreFormFocus(root,saved){
  if(!saved)return;
  const control=root.ownerDocument.getElementById(saved.id);
  if(!control||!root.contains(control)||control.type!==saved.type||control.tagName!==saved.tagName)return;
  control.focus({preventScroll:true});
  if(Number.isInteger(saved.start)&&typeof control.setSelectionRange==='function'){
    try{control.setSelectionRange(saved.start,saved.end,saved.direction);}catch{/* Number inputs cannot select ranges. */}
  }
  control.scrollTop=saved.scrollTop;control.scrollLeft=saved.scrollLeft;
}

// Native modal focus containment and Escape behavior; attach outside the workspace
// so background redraws cannot discard an unsaved JSON/prompt or credential form.
export function openFormDialog({document,title,body,escape,onSave,stillCurrent=()=>true}){
  const previous=document.activeElement,modal=document.createElement('dialog');
  modal.className='production studio-form-dialog';modal.setAttribute('aria-label',title);
  modal.innerHTML=`<div class="dialog-content"><div class="toolbar"><h2 style="flex:1">${escape(title)}</h2><button class="dialog-close">Close</button></div>${body}<p class="notice error dialog-error" role="alert" hidden></p><div class="toolbar"><button class="primary dialog-save">Save</button></div></div>`;
  document.body.append(modal);
  const save=modal.querySelector('.dialog-save'),close=modal.querySelector('.dialog-close'),error=modal.querySelector('.dialog-error');
  let busy=false;
  modal.addEventListener('input',()=>{modal.dataset.unsaved='true';});
  modal.addEventListener('close',()=>{modal.remove();if(previous?.isConnected)previous.focus({preventScroll:true});},{once:true});
  modal.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
  close.onclick=()=>{if(!busy)modal.close();};
  save.onclick=async()=>{
    if(busy)return;
    busy=true;save.disabled=true;close.disabled=true;error.hidden=true;
    try{
      if(!stillCurrent())throw Error('The selected project changed. Your form is retained; close it and reopen it in the intended project.');
      await onSave(modal);modal.close();
    }catch(reason){error.textContent=reason.message;error.hidden=false;}
    finally{busy=false;save.disabled=false;close.disabled=false;}
  };
  modal.showModal();
  modal.querySelector('input:not([type="hidden"]),textarea,select,.dialog-save')?.focus({preventScroll:true});
  return modal;
}
