import {pairingKey,helperJson} from './helper-connection.mjs?v=connection-20261009';
const link=document.getElementById('voiceLabLink');
async function reveal(){
  const key=pairingKey();if(!key||!link||!link.hidden)return;
  try{const info=await helperJson('/studio/voice-lab',key);if(Array.isArray(info.models)&&info.models.length===3)link.hidden=false;}catch{}
}
reveal();setInterval(()=>{if(document.visibilityState==='visible')reveal();},15000);
