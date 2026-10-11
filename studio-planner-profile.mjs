// Shared settings projection. No story, references, budgets or keys are copied.
export function plannerSettings(config, profile, evidence) {
  if(profile?.version!==1)throw Error('Update the helper to load saved production settings.');
  const s=profile.settings, v=s.video, d=s.director, i=s.image;
  const warnings=[], next={...config};
  if(!['off','sampled','practical','strict'].includes(s.qcCheckLevel))throw Error('Unsupported saved visual check level.');
  next.qc=s.qcCheckLevel;
  next.sample=100/(s.qcSampleEvery||5);
  const resolution=(w,h)=>w===1280&&h===720?'720p':w===1920&&h===1080?'1080p':null;
  next.videoResolution=resolution(v.width,v.height);
  next.resolution=resolution(i.width,i.height);
  if(!next.videoResolution||!next.resolution||![24,30,60].includes(v.fps))throw Error('This project format has no matched planner timing. Its settings were not replaced with another format.');
  next.videoFps=v.fps;next.videoRenderer=v.renderer||'auto';
  next.intro=profile.intro?.enabled===true;
  if(next.intro){
    if(profile.intro.placement==='every_chapter')throw Error('Per-chapter intros are not calibrated in this planner. Use a manual scenario; no full-story-only intro is silently substituted.');
    next.introSeconds=profile.intro.duration;next.introImages=profile.introImages||5;
  }
  // A retry ceiling is not a measured replacement percentage.
  if(!s.automaticRepair)next.retries=0;
  else warnings.push('Automatic repairs are enabled. Extra attempts remain your scenario allowance; a retry limit is not an error rate.');
  next.directorTier=d.processingTier||'default';
  const profiles=evidence.directorCalibration?.profiles||[];
  let id=d.executionMode==='classic'?'classic-standard':d.executionMode==='staged-lean'?
    (d.compactCuts&&d.factBeatsMode==='storyboard'?'lean-compact-standard':d.factBeatsMode==='storyboard'?'lean-beats-standard':'lean-current-standard'):null;
  if(id&&profiles.some(p=>p.id===id))next.directorProfile=id;
  else warnings.push('This director workflow has no matching measured profile. Existing timing is a comparison reference.');
  next.directorActualSlots=d.parallelism||1;next.apiSlots=next.directorActualSlots;
  next.directorCostMode='measured';
  next.earlyGpu=false;next.gpuStartSeconds=0;
  if(profile.generationStrategy==='align'&&!profile.timedHistoryReady){
    next.readyChapters=next.chapters;
    warnings.push('Timed align has no complete matching production history; like Studio, this setup waits for all chapter plans before image dispatch. The 15-second alignment target is not a guaranteed finish.');
  }else if(profile.generationStrategy==='fastest')next.readyChapters=1;
  if(d.visualPacking&&d.visualPacking!=='small'||d.sourceVisualOverlap||d.reasoning!=='Balanced')
    warnings.push('Director grouping, overlap or reasoning differs from the public calibration; its time and token cost are reference projections, not matching measurements.');
  if(i.model!=='flux2-klein-4b'||i.steps!==4||i.guidance!==1)
    warnings.push('Image model/settings differ from the measured FLUX.2 Klein 4B / 4-step / guidance-1 benchmark. Its speed and rental cost are not calibrated for this setup.');
  const qc=profile.estimates?.visualQC;
  if(next.qc!=='off'&&Number.isFinite(qc?.seconds)&&qc.seconds>0){
    next.qcSeconds=qc.seconds;
    if(Number.isFinite(qc.costUSD)&&qc.costUSD>=0)next.qcCost=qc.costUSD;
  }else{next.qcSeconds=0;warnings.push(next.qc==='off'?'Visual checks are explicitly Off in this saved project; file checks remain.':'No matching visual-check history yet; historical QC time and the displayed per-image cost are estimates.');}
  warnings.push('Voice delivery, motion, overlays, transfers and long exports can differ from short benchmark conditions; total finish time remains a projection.');
  return {config:next,warnings};
}

export function profileSummary(p) {
  const s=p.settings,v=s.video,i=s.image,d=s.director;
  return `${p.name} · saved revision ${p.revision} · ${v.width}×${v.height}/${v.fps} fps · ${v.motionMode||'director'} motion · ${s.voice} / ${s.narrationDelivery||'standard'} · ${i.model}, ${i.steps} steps, guidance ${i.guidance} · ${s.qcCheckLevel} visual checks · intro ${p.intro?.enabled?'On':'Off'} · ${d.parallelism||1} Luna tasks · ${d.processingTier||'default'} processing`;
}
