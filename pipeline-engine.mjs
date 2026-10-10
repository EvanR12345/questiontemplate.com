// A local scheduling model. This module never calls a paid provider or changes Studio projects.
import {latestProfiles} from './pipeline-measurements.mjs';
import {priceGPU,costParts} from './pipeline-pricing.mjs?v=pricing-20261009';
import {directorForecast} from './pipeline-director-calibration.mjs?v=luna-flex-results-20261010';
export const VERSION = 1;
export const DEFAULTS = { minutes:120, chapters:14, cadence:103/1050.23*60, intro:true,
  introSeconds:30, introImages:5, gpu:'5090', policy:'proposed', qc:'off', sample:20,
  retries:0, warmCache:false, apiSlots:3, cpuOverlap:false, batch:24,
  exhaustive:false, earlyGpu:false, directorCost:.38798, qcCost:.0003, storageDaily:.168,
  directorProfile:'historical',directorTier:'default',directorCostMode:'manual',flexLatencyFactor:1,
  pricingBasis:'published',containerGB:20,storageDays:1,quotedGPUHourly:0,quotedGPU:'',
  resolution:'legacy', videoResolution:'720p', videoFps:30, videoRenderer:'auto', imageWorkers:'best', encodingProfile:'cached', executionMode:'resident', measurementAttempt:'latest', readyChapters:1, gpuStartSeconds:0 };

export const LANES = [
  ['setup','Cloud startup','Boot · models · shutdown'],
  ['audio','Narration','Local NVIDIA helper'],
  ['director','Luna director','Shared API slots'],
  ['prepare','References & requests','Local preparation'],
  ['gpu','Image generation','One cloud GPU'],
  ['transfer','Transfer & validation','Network · decoded pixels'],
  ['qc','Visual checks','Same API pool as Luna'],
  ['save','Durable saves','Images · JSON · state'],
  ['render','Video rendering','Matched local clip timing'],
  ['finish','Final delivery','Join · probe · receipts']
];

// Every sub-process remains inspectable even when a timing receipt only measures its parent.
export const CATALOG = [
  ['preflight','Project preflight','setup','Read chapters and settings; validate available providers, references, budget and existing assets.','Before any generation.','May overlap independent local preparation.','Allowance; not a live availability check.'],
  ['clean','Clean narration','audio','Remove metadata headings; normalize quotes, stretched cries and sound effects; preserve story dialogue.','Source text and manual overrides.','Different chapters can be cleaned independently.','Allowance. No story is transmitted.'],
  ['bible','Load canonical story state','save','Retrieve permanent identities, current appearance, clothing history, locations and previous chapter handoff.','Saved project state.','Read-only loads may overlap audio. Writes stay ordered.','Allowance. Model context is not permanent memory.'],
  ['voice','Continuous narration','audio','Existing helper synthesizes one continuous chapter track; pronunciation handling, PCM buffering and encoding stay included.','Clean narration.','Cloud Luna/images use different GPUs. Local rendering competes for CPU and RAM.','Historical short-excerpt voice test extrapolated.'],
  ['timing','Narration timing','audio','Read true audio duration; create sentence/paragraph alignment. Word timing is not assumed to be exact.','Saved audio.','Timing for one chapter can overlap another chapter’s cloud work.','Allowance; estimated alignment if exact timestamps are unavailable.'],
  ['analyst','Story analyst','director','Identify beats, cast classes, locations, objects and supported appearance-change events.','Audio timing and preceding planned handoff.','Independent intro planning may use another API slot.','Share of aggregate measured director time; not an isolated pass measurement.'],
  ['chapter','Chapter director','director','Choose scene boundaries and pacing from narration and story beats; no fixed scene count.','Story analysis.','Next chapter cannot consume a handoff that does not yet exist.','Share of aggregate director time.'],
  ['scene','Scene and shot planning','director','Plan actions, expressions, people, locations and narration coverage; validate JSON and scene ranges.','Chapter scene boundaries.','Different ready scenes could be batched later; this estimate is conservative.','Share of aggregate director time.'],
  ['layout','Camera and composition','director','Shot size, angle, character placement, lighting and purposeful motion.','Scene/shot facts.','Must follow shot planning for the same scene.','Share of aggregate director time.'],
  ['workflow','Workflow selection','director','Validate selected installed model; choose relevant identity/location references and edit/control settings.','Shot structure and capabilities.','Do not silently swap models.','Share of aggregate director time.'],
  ['continuity','Continuity supervisor','director','Compare planned state with canonical identity and intentional appearance changes; repair structured contradictions.','Current plan and history.','Shares API capacity with visual QC.','Share of aggregate director time; not a visual review.'],
  ['prompt','Model-aware prompts','director','Build FLUX natural-language prompts from structured facts, references, camera and appearance. Preserve held props.','Verified planned facts and workflow.','GPU can work on earlier ready chapters.','Share of aggregate director time.'],
  ['handoff','Commit chapter handoff','save','Persist planned state, scene plan, prompts and next chapter’s story memory in order.','Validated prompt and continuity output.','Next analysis can start before prior chapter images finish.','Share of aggregate director time; actual commit is serialized.'],
  ['introPlan','Optional intro plan','director','Prepare a separately reviewed intro script and five visuals. No title overlay by default.','Project settings and explicit intro content.','May use a spare Luna slot.','Allowance; does not invent knowledge of unread chapters.'],
  ['boot','Provision and boot','setup','Allocate worker, start container and expose the authenticated image service.','Preflight and just-in-time first prompts, unless Early GPU is selected.','May overlap other chapters’ directing. Rental clock starts here.','Measured on four fresh workers; historical allowance for original full 6000.'],
  ['models','Download and hash models','setup','Fetch ~12.45 GB of pinned model components and verify hashes; skip download only with a valid existing cache.','Running cloud worker.','Uses the worker’s network, not the laptop image-transfer lane.','Four-GPU download measurements; assumptions for older workers.'],
  ['health','Backend health and manifest','setup','Validate ComfyUI nodes, workflow, weights, VRAM and model settings once before dispatch.','Downloaded or cached models.','Unchanged availability checks can be cached; invalidation must remain explicit.','Allowance. No public control endpoint is exposed by this planner.'],
  ['cold','First model load','gpu','Load encoder, diffusion model and VAE before first inference. Subsequent images reuse them.','Backend health.','Uses image GPU; cannot infer concurrently on the same measured one-slot configuration.','Cold server duration minus warm server duration for matched tests.'],
  ['prepare','Prepare image request','prepare','Resolve shot state, choose relevant references, preprocess pixels, build workflow and deterministic seed.','Prompt, reference assets and workflow.','Proposed mode can prepare the next group while the GPU works.','Warm client component mean; grouped preparation overlap is unverified.'],
  ['upload','Upload references and submit','transfer','Upload uncached references, resolve existing cache keys and submit a durable remote operation ID.','Prepared request and running model.','Proposed mode may overlap generation; bounded admission is required.','Warm upload + submission means. Reuse of references is already reflected.'],
  ['infer','Encode · denoise · VAE decode','gpu','Qwen text/reference conditioning, four FLUX denoising steps, VAE decoding and backend image output.','Accepted workflow and model load.','One GPU inference lane. Three model copies do not automatically triple measured throughput.','Measured warm server execution; encoder/denoiser/VAE are not separately added.'],
  ['delivered','Measured image worker group','gpu','Independent ComfyUI processes on one GPU. Includes request preparation, reference uploads, encoding, denoising, VAE decode, download, technical validation, PNG encoding, durable saves and receipts.','Chapter handoff, references, running models.','Luna and local audio/render can overlap. Internal worker throughput comes from matched tests; it is not multiplied by process count.','Pooled measured delivered throughput. Internal stages remain included once; moving them independently would require additional instrumentation.'],
  ['poll','Observe completion','transfer','Poll or await the accepted prompt ID; do not resubmit on a delayed response.','Submitted operation.','This block shows only excess polling delay. GPU execution is counted in Infer once.','Client wait minus server time; may include queue/transport noise.'],
  ['download','Retrieve and decode image','transfer','Download output, decode it with Pillow, verify dimensions and inspect essentially black/corrupt frames.','Successful server completion.','Current image lane remains held through retrieval. Proposed mode releases it first.','Measured retrieval + validation combined; disk saving is separate.'],
  ['save','Save image and settings','save','Persist PNG, prompt, model, seed, refs and parameters; update execution journal and checkpoint before completion.','Decoded valid image.','Single durable commit owner; later requests may run if the queue is bounded.','Mixed save/benchmark-receipt allowance; not isolated production save time.'],
  ['vision','Luna visual QC','qc','Review story fidelity, identity, appearance, held objects, anatomy and intentional changes. No endless repair loop.','Saved output and expected shot.','Shares API slots with directing; can check earlier images while the GPU works.','Mean of 12 historical calls. Off skips vision, not decode/black-image validation.'],
  ['repair','Bounded extra image attempts','gpu','An assumed fraction of images gets one more image/edit attempt; same provider settings, bounded retry budget.','First attempt plus configured review or deliberate replacement.','Extra attempts are counted; automatic visual repair cannot be inferred when vision QC is off.','User-entered attempt allowance, not measured failure probability.'],
  ['timeline','Chapter timeline','save','Map images onto real narration boundaries, apply cover framing, motion and approved transitions.','Saved chapter assets and required review decisions.','Can begin while later chapters generate in the proposed scheduler.','Allowance. Existing renderer caches unchanged clips.'],
  ['render','Motion clips and chapter assembly','render','Encode smooth still-image zoom/pan clips, mux narration and cache chapter MP4. Output resolution and frame rate select matched timing evidence.','Chapter timeline, images and audio.','Can overlap cloud work; audio/render CPU overlap requires measurement if enabled.','Matched fresh local motion/encoding clips, one worker; some profiles include chapter assembly. Overlays, cloud transfers and long production remain unmeasured.'],
  ['stop','Stop and reconcile GPU','setup','Stop compute, confirm worker removed/exited and reconcile rental receipts. Saved outputs remain in configured project storage.','All paid image attempts and output transfers completed.','Independent local rendering and Luna work can continue. Storage can still bill.','Eight-second shutdown allowance, distinct from actual last test lease upper bound.'],
  ['join','Join full story','finish','Join ordered cached chapter videos and optional intro once; no chapter-label narration or title overlay.','Rendered chapters and intro.','Must follow every required chapter render.','Part of explicit 2-minute auxiliary allowance, not a measured two-hour mux stage.'],
  ['probe','Routine output checks','finish','Probe duration, codecs, chapter boundaries and a few black/border samples; publish completed file and cost/time report.','Full story MP4.','These technical checks are separate from image vision QC.','Allowance; sampled checks do not certify every story image.'],
  ['decode','Exhaustive video verification','finish','Decode every final video frame. Optional research-level verification, not necessary for every normal render.','Completed final file.','Adds local CPU/disk work.','Actual two-hour decode measurement, ~20.74 minutes.'],
  ['encoder','Text/reference encoding','gpu','Nested inside measured server execution; cached text encodings can avoid work when inputs are unchanged.','Prepared prompt/reference inputs.','Must precede denoising.','Included in Infer; no separate isolated timing.'],
  ['denoise','FLUX sampling','gpu','Four denoising steps, Euler/Flux2, CFG 1.0, no model swap.','Encoded conditioning and seeded latent.','GPU-bound stage; same-GPU parallel copies need a separate benchmark.','Included in Infer; no separate isolated timing.'],
  ['vae','VAE pixel decode','gpu','Turn latent into visible pixels and backend output.','Denoised latent.','Must precede output download.','Included in Infer; not double-counted.'],
  ['audioEncode','Audio encoding and save','audio','Maintain continuous audio, encode chapter output and save to the helper’s persistent project.','Narration PCM.','Existing voice experiment covers inference/encoding overlap.','Included in Voice; not added twice.'],
  ['motion','Zoom, pan and framing','render','Landscape image, cover fit, smooth subpixel zoom/pan and optional transitions.','Validated image and shot duration.','Clip concurrency depends on the selected renderer; native photo motion uses one worker and shares the laptop GPU with narration.','Included in Render. Output format selects its matched calibration; long production is extrapolated.'],
  ['cache','Cache validation and resume','save','Fingerprint saved assets/settings; keep complete outputs and known remote IDs, flag unresolved operations rather than repurchase them.','Saved journal and source revision.','Writes serialize; readonly validation can overlap.','Included in preflight and save allowances.'],
  ['retryJSON','JSON repair / API retry','director','Validate director schemas and story evidence; retry only bounded invalid responses.','Invalid model result, if any.','Costs time and tokens; observed director aggregate includes its test behavior.','Not an additional fixed duration. Outages/manual reviews cannot be predicted.']
].map(([id,name,lane,description,requires,parallel,basis])=>({id,name,lane,description,requires,parallel,basis}));

export function validateConfig(input, evidence) {
  const c={...DEFAULTS,...input};
  if(!['published','receipt','quote'].includes(c.pricingBasis))throw Error('Choose published prices, benchmark receipts or your compute quote.');
  for(const [key,min] of [['containerGB',0],['storageDays',0],['quotedGPUHourly',0]]){
    c[key]=Number(c[key]);if(!Number.isFinite(c[key])||c[key]<min)throw Error(key+' must be a finite nonnegative number.');
  }
  if(typeof c.quotedGPU!=='string'||c.quotedGPU.length>100)throw Error('Invalid quoted GPU identity.');
  for(const [key,lo,hi] of [['minutes',1,Infinity],['chapters',1,Infinity],['readyChapters',1,Infinity],['cadence',1,20],['introSeconds',10,60],['introImages',1,20],['sample',1,100],['retries',0,100],['apiSlots',1,c.directorProfile==='historical'?3:8],['batch',1,Infinity],['directorCost',0,100],['qcCost',0,1],['storageDaily',0,10]]) {
    if(!Number.isFinite(Number(c[key]))||Number(c[key])<lo||Number(c[key])>hi) throw Error(`Invalid ${key}: use ${hi===Infinity?'a finite number of at least '+lo:lo+'–'+hi}.`);
    c[key]=Number(c[key]);
  }
  for(const key of ['chapters','readyChapters','introImages','apiSlots','batch']) if(!Number.isSafeInteger(c[key])) throw Error(`${key} must be a whole number.`);
  if(!evidence.gpus.some(g=>g.id===c.gpu)&&!evidence.concurrency?.gpus.some(g=>g.id===c.gpu&&(g.profiles.length||g.productionProfiles?.length))) throw Error('Unknown GPU profile.');
  if(!['cached','fresh'].includes(c.encodingProfile))throw Error('Unknown conditioning profile.');
  if(!['resident','pipeline','hybrid'].includes(c.executionMode))throw Error('Unknown execution method.');
  if(typeof c.measurementAttempt!=='string'||!/^[-a-zA-Z0-9]{1,80}$/.test(c.measurementAttempt))throw Error('Unknown measurement attempt.');
  if(c.executionMode!=='resident'&&c.resolution==='legacy')throw Error('Pipeline timing requires a measured resolution.');
  if(!['legacy','720p','1080p'].includes(c.resolution))throw Error('Unknown image resolution.');
  if(!['720p','1080p'].includes(c.videoResolution))throw Error('Unknown video resolution.');
  if(!['auto','cpu','native'].includes(c.videoRenderer))throw Error('Choose a tested local video renderer.');
  c.videoFps=Number(c.videoFps);
  if(![24,30,60].includes(c.videoFps))throw Error('Choose a calibrated video frame rate: 24, 30 or 60.');
  if(!['best','1','2','3','4','6'].includes(String(c.imageWorkers)))throw Error('Unknown worker count.');
  c.imageWorkers=String(c.imageWorkers);
  if(c.resolution!=='legacy'&&c.policy==='current')throw Error('Measured worker groups are an experiment. Use Proposed or Sequential scheduling.');
  if(!['serial','current','proposed'].includes(c.policy)) throw Error('Unknown schedule mode.');
  if(!['off','sampled','practical','strict'].includes(c.qc)) throw Error('Unknown check level.');
  for(const key of ['intro','warmCache','cpuOverlap','exhaustive','earlyGpu']) if(typeof c[key]!=='boolean') throw Error(`${key} must be true or false.`);
  if(c.readyChapters>c.chapters)throw Error('GPU readiness chapters cannot exceed chapter count.');
  if(!Number.isFinite(Number(c.gpuStartSeconds))||Number(c.gpuStartSeconds)<0)throw Error('GPU boot time must be a finite, nonnegative number of seconds from project start.');
  c.gpuStartSeconds=Number(c.gpuStartSeconds);
  if(c.intro && c.introSeconds>=c.minutes*60) throw Error('The intro must be shorter than the full video.');
  return c;
}

export function describePlan(input,evidence){return buildPlan(input,evidence,{metadataOnly:true});}

export function buildPlan(input, evidence, {metadataOnly=false}={}) {
  const c=validateConfig(input,evidence),cal=evidence.calibration;
  const director=directorForecast(c,evidence);
  const nativeProfile=cal.productionRenderProfiles?.find(p=>p.resolution===c.videoResolution&&p.fps===c.videoFps&&p.backend==='native');
  const cpuProfile=cal.productionRenderProfiles?.find(p=>p.resolution===c.videoResolution&&p.fps===c.videoFps&&(p.backend||'cpu')==='cpu')||cal.renderProfiles?.find(p=>p.resolution===c.videoResolution&&p.fps===c.videoFps);
  const renderProfile=c.videoRenderer==='native'?nativeProfile:c.videoRenderer==='cpu'?cpuProfile:nativeProfile||cpuProfile;
  if(!renderProfile)throw Error(`No matched ${c.videoResolution}/${c.videoFps}fps render calibration. Reload the updated evidence; no old 24fps timing is substituted.`);
  let g=evidence.gpus.find(g=>g.id===c.gpu),measurement=null;
  if(c.resolution!=='legacy'){
    const tested=evidence.concurrency?.gpus.find(g=>g.id===c.gpu);
    const profiles=latestProfiles(tested,c.resolution,c.encodingProfile,c.executionMode,c.measurementAttempt);
    measurement=c.imageWorkers==='best'?profiles.reduce((best,p)=>!best||p.imagesPerSecond>best.imagesPerSecond?p:best,null):profiles.find(p=>(c.executionMode!=='resident'?p.clientSlots:p.workers)===Number(c.imageWorkers));
    if(!measurement)throw Error(`No completed two-round ${c.resolution} / ${c.imageWorkers} worker / ${c.encodingProfile} conditioning profile for this GPU. No estimated throughput is substituted.`);
    const h=tested.hardware.find(h=>h.attempt===measurement.attempt);
    if(!h)throw Error('This measured host has no verified hardware/startup receipt. No other rental host is substituted.');
    const serialBaseline=[...(tested.profiles||[]),...(tested.productionProfiles||[])].find(p=>p.attempt===measurement.attempt&&p.resolution===c.resolution&&p.workers===1&&!p.postControl&&(p.encodingProfile||'cached')===c.encodingProfile);
    const baselineLatency=serialBaseline?.meanClientLatencySeconds??measurement.meanClientLatencySeconds;
    g={...(g||{}),id:tested.id,name:tested.name,vram:tested.vramGB,hourly:h.computeContainerHourlyUSD??h.hourlyUSD,
      boot:h.startupSeconds,download:h.downloadHashSeconds,
      cold:Math.max(.05,(h.firstWarmupClientSeconds||baselineLatency)-baselineLatency),
      meanClient:measurement.meanClientLatencySeconds,server:measurement.meanServerLatencySeconds,
      region:h.region,basis:'concurrency',date:h.runStartedAt?new Date(h.runStartedAt*1000).toISOString().slice(0,10):'Date unavailable',
      scope:`${c.resolution}, ${measurement.workers} workers${c.executionMode!=='resident'?`, ${measurement.clientSlots} request slots`:''}, ${measurement.rounds} rounds. ${c.encodingProfile==='fresh'?'Fresh text and reference encoding':'Cached conditioning'}; best tested count is not a hardware maximum.`};
  }
  if(!g)throw Error('This GPU has no legacy timing profile. Select a measured resolution.');
  g=priceGPU(g,c);
  const capacities={api:director.aggregated?director.slots:c.policy==='serial'?1:c.apiSlots, cloud:1, remote:1, network:1, disk:1, cpu:c.cpuOverlap?2:1, audioGPU:1, imagePipe:1};
  const story=c.minutes*60-(c.intro?c.introSeconds:0);
  const count=Math.ceil(story/60*c.cadence),chapterSec=story/c.chapters;
  if(!Number.isSafeInteger(count))throw Error('Image count exceeds numeric precision; use a smaller planning scenario.');
  // Coarsen image display groups only, preserving chapters, attempts and duration.
  // This detailed browser chart has a task budget, not a project-length cap.
  const available=Math.max(100,3000-c.chapters*20-40);
  if(c.chapters*14>30000)throw Error('The detailed browser chart exceeds its 30,000-operation memory budget. These project settings are valid, but require a larger-project view.');
  const groupSize=Math.max(c.batch,Math.ceil((count*(1+c.retries/100)+(c.intro?c.introImages:0))*8/available));
  if(metadataOnly)return {config:c,gpu:g,measurement,renderProfile,groupSize,director};
  const tasks=[];let seq=0;
  const add=(id,kind,chapter,duration,deps,resources={},extra={})=>{
    if(tasks.length>=30000)throw Error('The detailed browser chart exceeds its 30,000-operation memory budget. These project settings are valid, but require a larger-project view.');
    const meta=CATALOG.find(x=>x.id===kind);if(!meta)throw Error('Unknown process '+kind);
    const t={id,kind,chapter,name:meta.name,lane:meta.lane,duration:Math.max(.05,duration),deps:[...new Set(deps)],resources,priority:seq++,basis:meta.basis,...extra};tasks.push(t);return id;
  };
  add('preflight','preflight',0,8,[],{cpu:.1});
  const handoffs=[],prompts=[],audio=[],timings=[],renders=[],saves=[],reviews=[];
  for(let i=1;i<=c.chapters;i++) {
    const prefix=`c${i}-`,images=Math.floor(count*i/c.chapters)-Math.floor(count*(i-1)/c.chapters);
    const clean=add(prefix+'clean','clean',i,1,['preflight'],{cpu:.1});
    const bible=add(prefix+'bible','bible',i,.3,['preflight'],{disk:1});
    audio[i]=add(prefix+'voice','voice',i,chapterSec/cal.voiceSampleSeconds*cal.voiceWallSeconds,[clean],{cpu:.8,audioGPU:1});
    timings[i]=add(prefix+'timing','timing',i,.5,[audio[i]],{cpu:.1});
    const total=director.secondsPerStorySecond*chapterSec;
    let prev=null;
    if(director.aggregated){
      const deps=[timings[i],bible,...(i>1?[handoffs[i-1]]:[])];
      prev=add(prefix+'chapter','chapter',i,total,deps,{api:director.slots},{images,
        name:'Chapter directing · all measured passes',directorProfile:director.id,
        includedStages:director.stages,basis:`${director.label}: ${director.measuredSeconds.toFixed(3)}s for ${director.sourceSeconds}s of narration; ${director.calls} API requests, ${director.slots} tested slots. Includes source analysis, storyboards, fidelity checks and observed repairs once. ${director.scope}`});
      prev=add(prefix+'handoff','handoff',i,.3,[prev],{disk:1},{images,basis:'Separate 0.3-second durable commit allowance. Full measured chapter directing is counted once above.'});
      prompts[i]=prefix+'chapter';
    }else{
    for(const [kind,weight] of [['analyst',.17],['chapter',.14],['scene',.18],['layout',.12],['workflow',.06],['continuity',.12],['prompt',.18],['handoff',.03]]) {
      const deps=prev?[prev]:[timings[i],bible,...(i>1?[handoffs[i-1]]:[])];
      prev=add(prefix+kind,kind,i,total*weight,deps,kind==='handoff'?{disk:1}:{api:1},{images});
    }
    prompts[i]=prefix+'prompt';
    }
    handoffs[i]=prev;
  }
  if(c.intro){
    add('intro-plan','introPlan',0,8,['preflight'],{api:1});
    add('intro-voice','voice',0,c.introSeconds/cal.voiceSampleSeconds*cal.voiceWallSeconds,['intro-plan'],{cpu:.8,audioGPU:1});
  }
  const startDeps=c.earlyGpu?['preflight']:c.policy==='serial'?[...handoffs.slice(1)]:[handoffs[c.readyChapters]];
  add('boot','boot',0,g.boot,startDeps,{cloud:1},{notBefore:c.gpuStartSeconds});
  add('models','models',0,c.warmCache?1:g.download,['boot'],{cloud:1});
  add('health','health',0,3,['models'],{cloud:1});
  add('cold','cold',0,g.cold,['health'],{remote:1});
  const attemptCount=count+(c.intro?c.introImages:0)+Math.ceil(count*c.retries/100);
  let lastPack=null,allPackSaves=[],packNumber=0;
  const imageSources=c.intro?[0,...Array.from({length:c.chapters},(_,i)=>i+1)]:Array.from({length:c.chapters},(_,i)=>i+1);
  for(const i of imageSources){
    const n=i===0?c.introImages:Math.floor(count*i/c.chapters)-Math.floor(count*(i-1)/c.chapters);
    const extras=i===0?0:Math.floor(Math.ceil(count*c.retries/100)*i/c.chapters)-Math.floor(Math.ceil(count*c.retries/100)*(i-1)/c.chapters);
    const imageStart=i===0?'intro-plan':handoffs[i],finalSaves=[],finalReviews=[];
    let checked=0;
    const wanted=c.qc==='off'?0:c.qc==='sampled'?Math.ceil(n*c.sample/100):n;
    for(let a=0;a<n+extras;a+=groupSize){
      const size=Math.min(groupSize,n+extras-a),p=`c${i}-b${Math.floor(a/groupSize)+1}-`;
      let save;
      if(measurement){
        const deps=[imageStart,'cold',...(lastPack?[lastPack]:[])];
        save=add(p+'infer','delivered',i,size/measurement.imagesPerSecond,deps,{remote:1},
          {images:size,attempts:size,workers:measurement.workers,resolution:c.resolution,hasAllowance:a+size>n,
           basis:`${measurement.images} fresh images in ${measurement.rounds} rounds; complete delivery ${measurement.imagesPerSecond.toFixed(3)} images/s. ${c.encodingProfile==='fresh'?'Fresh prompt/reference encoding':'Cached conditioning'}. ${c.executionMode!=='resident'?measurement.clientSlots+' request slots, '+measurement.workers+' resident model copies':measurement.workers+' resident workers'}. Includes network/disk stages once. Production contention with concurrent Luna/audio/render remains unmeasured.`});
      }else{
      const imageLock=c.policy==='proposed'?{}:{imagePipe:1};
      const capDeps=c.policy==='proposed'?(packNumber>=3?[allPackSaves[packNumber-3]]:[]):lastPack?[lastPack]:[];
      const prepare=add(p+'prepare','prepare',i,size*g.prepare,[imageStart,...capDeps],{cpu:.1,...imageLock},{images:size,batch:Math.floor(a/groupSize)+1});
      const upload=add(p+'upload','upload',i,size*(g.upload+g.submit),[prepare,'cold'],{network:1,...imageLock},{images:size});
      const infer=add(p+'infer','infer',i,size*g.server,[upload],{remote:1,...imageLock},{images:size,attempts:size,hasAllowance:a+size>n});
      const poll=add(p+'poll','poll',i,size*g.poll,[infer],{...imageLock},{images:size});
      const download=add(p+'download','download',i,size*g.retrieve,[poll],{network:1,cpu:.1,...imageLock},{images:size});
      save=add(p+'save','save',i,size*g.save,[download],{disk:1,...imageLock},{images:size});
      }
      finalSaves.push(save);allPackSaves.push(save);lastPack=save;packNumber++;
      const q=Math.min(size,Math.max(0,wanted-checked));checked+=q;
      if(q)finalReviews.push(add(p+'vision','vision',i,q*cal.qcSeconds,[save],{api:1},{images:q}));
      if(c.policy==='serial'&&q)lastPack=finalReviews.at(-1);
    }
    saves.push(...finalSaves);reviews.push(...finalReviews);
    const timeline=add(`c${i}-timeline`,'timeline',i,.8,[...finalSaves,...finalReviews,...(i===0?['intro-voice']:[audio[i]])],{disk:1});
    const videoSeconds=i===0?c.introSeconds:chapterSec;
    const stageProfile=i===0&&renderProfile.backend==='native'?cpuProfile:renderProfile;
    renders.push(add(`c${i}-render`,'render',i,stageProfile.wallSeconds/stageProfile.videoSeconds*videoSeconds,[timeline],renderProfile.backend==='native'?{cpu:1,audioGPU:1}:{cpu:1},{videoSeconds,
      timingRange:{min:stageProfile.minWallSeconds/stageProfile.videoSeconds*videoSeconds,max:stageProfile.maxWallSeconds/stageProfile.videoSeconds*videoSeconds},
      basis:`${i===0&&renderProfile.backend==='native'?'Intro remains on the CPU compatibility path; its fades are not separately calibrated. ':''}${c.videoResolution}/${c.videoFps}fps, ${stageProfile.clips} fresh clips in ${stageProfile.rounds} rounds, one clip worker; reference ${stageProfile.wallSeconds.toFixed(2)}s per ${stageProfile.videoSeconds}s of ${stageProfile.mode==='chapter-export'?'motion and chapter assembly':'motion'}. ${stageProfile.scope} ${stageProfile.hardwareCondition||''} Two-worker speedup is not assumed.`}));
  }
  if(c.policy!=='proposed') {
    for(const r of renders){const task=tasks.find(t=>t.id===r);task.deps.push(...saves,...reviews);}
  }
  add('stop','stop',0,8,saves,{cloud:1});
  tasks.find(t=>t.id==='stop').priority=-1; // Stop compute before purely local rendering, including in the serial baseline.
  if(c.policy==='current') {
    for(let i=2;i<=c.chapters;i++) {
      tasks.find(t=>t.id===`c${i}-voice`).deps.push(handoffs[i-1]);
      if(i>2)tasks.find(t=>t.id===`c${i}-voice`).deps.push(...tasks.filter(t=>t.chapter===i-2&&['save','vision'].includes(t.kind)).map(t=>t.id));
    }
  }
  // Keep long-video delivery allowances proportional rather than treating a
  // 24-hour output as the same 45-second join/probe as a two-hour output.
  const lengthScale=c.minutes/120;
  const joinDuration=renderProfile.joinWallSeconds===undefined?45*lengthScale:renderProfile.joinWallSeconds/renderProfile.videoSeconds*c.minutes*60;
  add('join','join',0,joinDuration,renders,{cpu:.3,disk:1},{basis:renderProfile.joinWallSeconds===undefined?'Allowance: 45 seconds per two output hours, scaled by length. Not a measured long-video join; filesystem and chapter count can change it.':`Instrumented full-story AAC128 mux: ${renderProfile.joinWallSeconds.toFixed(3)}s per ${renderProfile.videoSeconds}s output in the paired fresh export. Excluded from chapter Render, counted once here; long-video rate is extrapolated.`});
  add('probe','probe',0,45*lengthScale,['join','stop'],{cpu:.2,disk:1},{basis:'Allowance: 45 seconds per two output hours, scaled by length. Cloud upload and platform processing are separate and not timed.'});
  if(c.exhaustive)add('decode','decode',0,cal.exhaustiveDecodeSeconds*lengthScale*(c.videoFps/24)*(c.videoResolution==='1080p'?2.25:1),['probe'],{cpu:1,disk:1},{basis:'Estimate from a historical 720p24 full-decode sample, scaled by output frames and pixels. The selected-format full-decode rate is not measured.'});
  if(c.policy==='serial') {
    // Topological order first: renderer nodes also wait for later chapters' saves.
    const left=[...tasks],done=new Set();let previous=null;
    while(left.length){
      const ready=left.filter(t=>t.deps.every(d=>done.has(d))).sort((a,b)=>a.priority-b.priority);
      if(!ready.length)throw Error('Cycle in serial plan.');
      const task=ready[0];if(previous&&!task.deps.includes(previous))task.deps.push(previous);
      done.add(task.id);previous=task.id;left.splice(left.indexOf(task),1);
    }
  }
  return {version:VERSION,config:c,groupSize,gpu:g,measurement,tasks,capacities,imageCount:count+(c.intro?c.introImages:0),attemptCount,storySeconds:story,
    director,renderProfile,renderScope:`${c.videoResolution}/${c.videoFps}fps video · ${renderProfile.backend==='native'?'local GTX 1650 GPU motion, one worker; shared narration GPU; full-story mux timed separately':renderProfile.mode==='chapter-export'?'fresh real-media CPU chapter exports, RAM-limited one worker; full-story mux timed separately':'matched fresh local CPU clip timing, one worker'}. Long-video scaling, overlays, cloud transfer and production contention remain unmeasured; these forecasts are projections, not guaranteed completion times.`};
}

function fits(task,start,allocations,capacities) {
  let next=start;
  for(const [pool,units] of Object.entries(task.resources)) {
    if(units>capacities[pool]+1e-8)throw Error(`Task ${task.id} exceeds ${pool} capacity.`);
    const relevant=(allocations[pool]||[]).filter(a=>a.end>start+1e-7&&a.start<start+task.duration-1e-7);
    const points=[start,...relevant.map(a=>Math.max(start,a.start))];
    for(const point of points) {
      const active=relevant.filter(a=>a.start<=point+1e-7&&a.end>point+1e-7);
      if(active.reduce((n,a)=>n+a.units,0)+units>capacities[pool]+1e-7)next=Math.max(next,Math.min(...active.map(a=>a.end)));
    }
  }
  return next;
}

export function schedule(plan, preferences={}) {
  const pending=new Set(plan.tasks),completed=new Map(),allocations={},out=[];
  // A candidate's earliest slot remains valid until a newly allocated shared
  // resource overlaps it. Avoid rescheduling every ready chapter after unrelated work.
  const earliest=new Map();
  const dirtySlots=new Set();
  const ids=new Set(plan.tasks.map(t=>t.id));if(ids.size!==pending.size)throw Error('Duplicate task IDs.');
  const ready=new Set(),remaining=new Map(),followers=new Map();
  for(const task of plan.tasks){
    remaining.set(task.id,task.deps.length);if(!task.deps.length)ready.add(task);
    for(const dep of task.deps){if(!ids.has(dep))throw Error(`Missing dependency ${dep}.`);(followers.get(dep)||followers.set(dep,[]).get(dep)).push(task);}
  }
  while(pending.size) {
    let choices=[];
    for(const task of ready) {
      let start=earliest.get(task);
      if(start===undefined||dirtySlots.has(task)){
        if(start===undefined){
          const requested=Math.max(task.notBefore||0,preferences[task.id]?.notBefore||0);
          start=Math.max(requested,0,...task.deps.map(d=>completed.get(d).end));
        }
        // Allocations are only added: an invalidated earliest slot cannot move
        // earlier. Resume from that bound instead of replaying all past conflicts.
        for(let guard=0;;guard++){
          if(guard>plan.tasks.length*3+20)throw Error('Resource allocation failed.');
          const next=fits(task,start,allocations,plan.capacities);if(next<=start+1e-7)break;start=next;
        }
        earliest.set(task,start);
        dirtySlots.delete(task);
      }
      choices.push({task,start,priority:preferences[task.id]?.priority??task.priority});
    }
    if(!choices.length)throw Error('Cycle in task dependencies.');
    choices.sort((a,b)=>a.start-b.start||a.priority-b.priority||a.task.priority-b.task.priority);
    const {task,start}=choices[0],item={...task,start,end:start+task.duration,manual:Boolean(preferences[task.id])};
    out.push(item);completed.set(item.id,item);pending.delete(task);ready.delete(task);
    earliest.delete(task);
    dirtySlots.delete(task);
    for(const candidate of ready){
      const at=earliest.get(candidate);
      if(at!==undefined&&item.end>at+1e-7&&item.start<at+candidate.duration-1e-7&&
          Object.keys(task.resources).some(pool=>task.resources[pool]&&candidate.resources[pool]))dirtySlots.add(candidate);
    }
    for(const follower of followers.get(item.id)||[]){const left=remaining.get(follower.id)-1;remaining.set(follower.id,left);if(left===0)ready.add(follower);}
    for(const [pool,units] of Object.entries(task.resources))(allocations[pool]||=[]).push({id:item.id,start:item.start,end:item.end,units});
  }
  out.sort((a,b)=>a.start-b.start||a.priority-b.priority);
  const end=Math.max(...out.map(t=>t.end)),boot=completed.get('boot'),stop=completed.get('stop');
  const rentalSeconds=stop.end-boot.start,gpuWork=out.filter(t=>t.resources.remote).reduce((n,t)=>n+t.duration,0);
  const directorUSD=plan.director.costPerTwoHours*plan.storySeconds/7200;
  const qcCount=out.filter(t=>t.kind==='vision').reduce((n,t)=>n+(t.images||0),0);
  const qcUSD=qcCount*plan.config.qcCost;
  return {tasks:out,end,rentalSeconds,gpuWork,gpuIdleSeconds:Math.max(0,rentalSeconds-gpuWork),
    ...costParts(rentalSeconds,plan.gpu.hourly,directorUSD,qcUSD,plan.config.storageDaily,plan.config.storageDays),
    directorUSD,qcUSD,qcCount,diagnostics:validateSchedule(plan,out)};
}

export function validateSchedule(plan,tasks) {
  const errors=[],map=new Map(tasks.map(t=>[t.id,t]));
  for(const task of tasks){
    if(!Number.isFinite(task.start)||!Number.isFinite(task.end)||task.start<0||task.end<=task.start)errors.push(`Invalid time: ${task.id}`);
    for(const d of task.deps)if(!map.has(d)||map.get(d).end>task.start+1e-6)errors.push(`Dependency violated: ${d} → ${task.id}`);
  }
  for(const [pool,capacity] of Object.entries(plan.capacities)){
    const events=[];
    for(const t of tasks)if(t.resources[pool]){events.push([t.start,t.resources[pool]]);events.push([t.end,-t.resources[pool]]);}
    events.sort((a,b)=>a[0]-b[0]);let count=0;
    for(let i=0;i<events.length;){
      const at=events[i][0];let delta=0;
      // Scheduling accepts touching boundaries within 1e-7 seconds. The validator
      // must use the same tolerance instead of reporting nanosecond false collisions.
      while(i<events.length&&events[i][0]-at<=1e-7)delta+=events[i++][1];
      count+=delta;if(count>capacity+1e-6){errors.push(`Resource collision: ${pool} at ${at.toFixed(1)}s`);break;}
    }
  }
  return errors;
}

export function explainMove(plan,result,id,requested,index=new Map(result.tasks.map(task=>[task.id,task]))) {
  const task=index.get(id);if(!task)throw Error('Unknown task.');
  const dependencies=task.deps.map(d=>index.get(d));
  const ready=dependencies.reduce((end,t)=>Math.max(end,t.end),0);
  if(requested<ready-1e-6){
    const names=dependencies.slice(0,6).map(t=>t.name).join(', ');
    return `Needs ${names}${dependencies.length>6?` and ${dependencies.length-6} more`:''} first. Earliest dependency time: ${formatTime(ready)}.`;
  }
  return 'Resources and dependent tasks will be re-scheduled safely.';
}

export function formatTime(seconds) {
  const s=Math.round(seconds),h=Math.floor(s/3600),m=Math.floor(s%3600/60),r=s%60;
  return h?`${h}h ${String(m).padStart(2,'0')}m`:`${m}m ${String(r).padStart(2,'0')}s`;
}

export function importSnapshot(input,evidence){
  if(!input||input.version!==VERSION||input.type!=='studio-pipeline-plan')throw Error('This is not a supported planner export.');
  const config=validateConfig(input.config,evidence),preferences={};
  const plan=buildPlan(config,evidence),known=new Set(plan.tasks.map(t=>t.id));
  if(input.preferences&&typeof input.preferences!=='object')throw Error('Invalid task preferences.');
  for(const [id,p] of Object.entries(input.preferences||{})){
    if(!known.has(id))continue;
    if(!p||!Number.isFinite(p.notBefore)||p.notBefore<0)throw Error('Invalid start preference.');
    if(p.priority!==undefined&&(!Number.isFinite(p.priority)||Math.abs(p.priority)>1000000))throw Error('Invalid task priority.');
    preferences[id]={notBefore:p.notBefore,...(p.priority===undefined?{}:{priority:p.priority})};
  }
  return {config,preferences};
}
