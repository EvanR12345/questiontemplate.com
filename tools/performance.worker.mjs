import { splitText, prepareBatches, SAMPLE_RATE, PART_SECONDS } from '../audio-core.mjs?v=english-2';
import { phonemize, normalizeText } from '../phonemize.mjs?v=profile-1';
import { loadLexicon } from '../english-phonemes.mjs?v=english-2';
import { speechText, pronunciationRules } from '../pronunciation.mjs?v=english-2';
import { nativeHealth, nativeRequest, nativeTokenizer } from '../native-client.mjs';
import { createEncoder } from '../encode-audio.mjs?v=long-fast-2';
import { streamSynthesis } from '../synthesis-pipeline.mjs';
const paragraph='The lights went out at exactly midnight. Maya heard three slow knocks at the door. She had been warned never to open it after dark, but the visitor knew her name. Across the square, a clock rang once and fell silent. Her brother had left a letter beside the window. It described a narrow path through the forest, a stone bridge, and a house with blue shutters. Maya packed a coat, a bottle of water, and the old map. She wanted answers, yet she knew that rushing would only make the journey harder. When morning came, she stepped outside and watched the sunlight spread across the hills. The village was waking up. A baker carried fresh bread, children hurried to school, and a traveler asked where the next road would lead. Maya checked her map and continued toward the bridge.\n\n';
const text=paragraph.repeat(6);
let ack, busy=false;
self.onmessage=({data})=>{if(data.type==='ack')ack?.();if(data.type==='run'&&!busy)void run(data);};
async function run({key,mode}){
  busy=true;
  const metrics={mode,textCharacters:text.length,setupMs:0,normalizationMs:0,phonemizeMs:0,requestMs:0,downloadValidationMs:0,encodeMs:0,encodeSetupMs:0,gateMs:0,checkpointWaitMs:0,loopbackHealthMs:[],sections:[],audioSeconds:0};
  try{
    const setup=performance.now();const info=await nativeHealth(key);await nativeRequest('/prepare',key,{voice:'am_michael'});await loadLexicon('a');
    const tokenizer=nativeTokenizer(info.vocab),rules=pronunciationRules('');metrics.gpu=info.gpu;metrics.setupMs=performance.now()-setup;
    for(let i=0;i<10;i++){const start=performance.now();await nativeHealth(key);metrics.loopbackHealthMs.push(performance.now()-start);}
    const pronounce=async(source,language,final)=>{const normalizeStart=performance.now();const normalized=normalizeText(speechText(source,rules,final));metrics.normalizationMs+=performance.now()-normalizeStart;const start=performance.now();const result=await phonemize(normalized,language,false);metrics.phonemizeMs+=performance.now()-start;return result;};
    async function* batches(){let position=0;for(const chunk of splitText(text,420)){position+=chunk.length;yield* prepareBatches(chunk,tokenizer,pronounce,'a',280,position===text.length);}}
    let encoder,processed=0,partFrames=0,totalFrames=0;
    const emit=(bytes,frames=0)=>{const buffer=bytes.buffer.slice(bytes.byteOffset,bytes.byteOffset+bytes.byteLength);postMessage({type:'chunk',bytes:buffer,frames},[buffer]);};
    const closePart=async()=>{if(!partFrames)return;emit(encoder.flush());const saved=new Promise(resolve=>{ack=resolve;});const start=performance.now();postMessage({type:'partEnd',processed});await saved;metrics.checkpointWaitMs+=performance.now()-start;ack=null;partFrames=0;encoder=null;};
    const render=async batch=>{
      const requested=performance.now();const response=await nativeRequest('/synthesize',key,{phonemes:batch.ids.phonemes,voice:'am_michael',speed:1});const requestMs=performance.now()-requested;
      const start=performance.now();const buffer=await response.arrayBuffer();if(response.headers.get('X-Sample-Rate')!=='24000'||!buffer.byteLength||buffer.byteLength%4)throw Error('Invalid audio');const audio=new Float32Array(buffer);if(audio.some(n=>!Number.isFinite(n)))throw Error('Nonfinite audio');
      const downloadValidationMs=performance.now()-start;metrics.requestMs+=requestMs;metrics.downloadValidationMs+=downloadValidationMs;
      metrics.sections.push({sourceCharacters:batch.text.length,tokens:batch.ids.dims[1],phonemes:batch.ids.phonemes,audioSeconds:audio.length/SAMPLE_RATE,requestMs,downloadValidationMs,serverTiming:response.headers.get('Server-Timing')});return audio;
    };
    const gate=async()=>{const start=performance.now();await new Promise(resolve=>setTimeout(resolve,0));metrics.gateMs+=performance.now()-start;return true;};
    const started=performance.now();
    for await(const {batch,audio} of streamSynthesis(batches(),render,{gate,prefetch:mode==='overlap'})){
      if(!batch.ids){processed+=batch.text.length;continue;}
      if(!encoder){const start=performance.now();encoder=await createEncoder('mp3',48);metrics.encodeSetupMs+=performance.now()-start;}
      const start=performance.now();const bytes=encoder.encode(audio,1);metrics.encodeMs+=performance.now()-start;processed+=batch.text.length;partFrames+=audio.length;totalFrames+=audio.length;emit(bytes,audio.length);
      postMessage({type:'progress',processed,seconds:totalFrames/SAMPLE_RATE});if(partFrames>=PART_SECONDS*SAMPLE_RATE)await closePart();
    }
    await closePart();metrics.wallMs=performance.now()-started;metrics.audioSeconds=totalFrames/SAMPLE_RATE;metrics.realtime=metrics.audioSeconds/(metrics.wallMs/1000);metrics.sourcePreserved=processed===text.length;
    postMessage({type:'done',metrics});
  }catch(error){postMessage({type:'error',message:error.stack||String(error)});}finally{busy=false;}
}
