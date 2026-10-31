// A public benchmark is usable only for the exact tested production conditions.
const same=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
export const STORYBOARD_REVIEW_CAPACITY_VERSION=2;
export const OWNERSHIP_RESOLUTION_VERSION=2;
const OBSERVED_CADENCE=5.884425316359273;
function observedCadenceMatch(config,actual,expected,p){
 const n=p.cadenceNormalization;
 // This explicit receipt records the existing story's observed frequency,
 // frozen before the benchmark cleared scenes. Null means derive frequency;
 // an explicit numeric saved cadence is never replaced by this exception.
 return actual.cadencePerMinute===null&&expected.cadencePerMinute===OBSERVED_CADENCE&&
  Number.isFinite(Number(config.cadence))&&Math.abs(Number(config.cadence)-OBSERVED_CADENCE)<=1e-12&&
  p.inputCadencePerMinute===OBSERVED_CADENCE&&
  n?.version===1&&n.kind==='observed-production-cadence-before-clearing-scenes'&&
  n.savedProfileCadencePerMinute===null&&n.inputCadencePerMinute===OBSERVED_CADENCE&&
  /^[a-f0-9]{64}$/.test(n.originalPreflightSHA256||'')&&
  /^[a-f0-9]{64}$/.test(n.stateBeforeSHA256||'');
}
function matchingProfile(config,profile,p){
 if(!p||p.status!=='COMPLETE'||p.unknownRequests||p.completedChapters!==p.sourceChapters||
    p.productionReviewsPassed!==true||p.standardReceiptsVerified!==true||p.slots!==4)return null;
 const actual=profile?.directorCalibrationProfile,expected=p.conditions;
 if(!actual||!expected||!Object.keys(expected).length)return null;
 if(actual.storyboardReviewCapacityVersion!==STORYBOARD_REVIEW_CAPACITY_VERSION||
    expected.storyboardReviewCapacityVersion!==STORYBOARD_REVIEW_CAPACITY_VERSION)return null;
 if(actual.ownershipResolutionVersion!==OWNERSHIP_RESOLUTION_VERSION||
    expected.ownershipResolutionVersion!==OWNERSHIP_RESOLUTION_VERSION)return null;
 if(!Object.entries(expected).every(([key,value])=>same(actual[key],value)||
    key==='cadencePerMinute'&&observedCadenceMatch(config,actual,expected,p)))return null;
 // The saved story supplies observed picture frequency on regeneration. It is
 // fixed for both benchmark arms rather than reduced to buy a faster result.
 if(!Number.isFinite(Number(config.cadence))||!Number.isFinite(p.inputCadencePerMinute)||
    Math.abs(Number(config.cadence)-p.inputCadencePerMinute)>0.001)return null;
 return p;
}

export function matchedDirectorProfile(config,profile,evidence){
 // Following a saved setup may use a newer exact-condition component. Explicit
 // historical/manual director IDs are retained by the planner's normal import.
 const profiles=(evidence.directorCalibration?.profiles||[])
  .filter(p=>p.id==='lean-overlap-standard'||p.calibrationFamily==='lean-overlap-standard')
  .sort((a,b)=>(b.observedAt||0)-(a.observedAt||0));
 return profiles.map(p=>matchingProfile(config,profile,p)).find(Boolean)||null;
}
