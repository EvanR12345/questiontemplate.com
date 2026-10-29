import test from 'node:test';
import assert from 'node:assert/strict';
import {matchedDirectorProfile} from './pipeline-director-matched.mjs';
const fixture=()=>{
 const conditions={model:'gpt-6-luna',tier:'default',parallelism:4,reasoning:'Balanced',
  sourceVisualOverlap:true,acceptedVisualDispatchVersion:2,storyboardReviewCapacityVersion:2,ownershipResolutionVersion:2,layoutSettings:{imagesPerMinute:8}};
 const p={id:'lean-overlap-standard',status:'COMPLETE',unknownRequests:0,completedChapters:2,sourceChapters:2,
  productionReviewsPassed:true,standardReceiptsVerified:true,slots:4,inputCadencePerMinute:5.884425,conditions};
 return {config:{cadence:5.884425},profile:{directorCalibrationProfile:structuredClone(conditions)},
  evidence:{directorCalibration:{profiles:[p]}},p};
};
test('matched receipt requires exact policy, settled Standard receipts and completed reviews',()=>{
 const f=fixture();assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),f.p);
 for(const [key,value] of Object.entries({model:'different',tier:'fast',parallelism:8,reasoning:'Fast',sourceVisualOverlap:false,acceptedVisualDispatchVersion:1,storyboardReviewCapacityVersion:1,ownershipResolutionVersion:1,layoutSettings:{imagesPerMinute:4}})){
  const x=fixture();x.profile.directorCalibrationProfile[key]=value;
  assert.equal(matchedDirectorProfile(x.config,x.profile,x.evidence),null,key);
 }
 for(const [key,value] of Object.entries({status:'FAILED',unknownRequests:1,completedChapters:1,productionReviewsPassed:false,standardReceiptsVerified:false,slots:8})){
  const x=fixture();x.p[key]=value;assert.equal(matchedDirectorProfile(x.config,x.profile,x.evidence),null,key);
 }
});
test('capped or unversioned review measurements never become exact uncapped matches',()=>{
 for(const where of ['actual','expected','both']){
  const f=fixture();
  if(where!=='expected')delete f.profile.directorCalibrationProfile.storyboardReviewCapacityVersion;
  if(where!=='actual')delete f.p.conditions.storyboardReviewCapacityVersion;
  assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),null,where);
 }
});
test('no calibration or changed picture frequency cannot acquire a matching timing claim',()=>{
 const f=fixture();
 assert.equal(matchedDirectorProfile(f.config,f.profile,{}),null);
 assert.equal(matchedDirectorProfile({...f.config,cadence:8},f.profile,f.evidence),null);
 f.p.conditions={};assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),null);
});
test('older ambiguous-owner timings cannot exactly match corrected ownership resolution',()=>{
 for(const where of ['actual','expected','both']){
  const f=fixture();
  if(where!=='expected')delete f.profile.directorCalibrationProfile.ownershipResolutionVersion;
  if(where!=='actual')delete f.p.conditions.ownershipResolutionVersion;
  assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),null,where);
 }
});
const observedCadence=5.884425316359273;
function normalizedFixture(){
 const f=fixture();
 f.config.cadence=f.p.inputCadencePerMinute=f.p.conditions.cadencePerMinute=observedCadence;
 f.profile.directorCalibrationProfile.cadencePerMinute=null;
 f.p.cadenceNormalization={version:1,kind:'observed-production-cadence-before-clearing-scenes',
  savedProfileCadencePerMinute:null,inputCadencePerMinute:observedCadence,
  originalPreflightSHA256:'a'.repeat(64),stateBeforeSHA256:'b'.repeat(64)};
 return f;
}
test('documented observed cadence permits saved null only at exact frozen effective frequency',()=>{
 const f=normalizedFixture();assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),f.p);
 assert.equal(f.profile.directorCalibrationProfile.cadencePerMinute,null,'matching does not rewrite saved profile');
 for(const key of ['model','reasoning','layoutSettings']){
  const x=normalizedFixture();x.profile.directorCalibrationProfile[key]='different';
  assert.equal(matchedDirectorProfile(x.config,x.profile,x.evidence),null,key);
 }
});
test('missing or unproven normalization cannot turn null into exact measured cadence',()=>{
 for(const change of [f=>delete f.p.cadenceNormalization,f=>delete f.p.cadenceNormalization.originalPreflightSHA256,
  f=>f.p.cadenceNormalization.kind='assumed',f=>f.p.cadenceNormalization.version=2,
  f=>f.p.cadenceNormalization.savedProfileCadencePerMinute=8,
  f=>f.p.cadenceNormalization.inputCadencePerMinute=8]){
  const f=normalizedFixture();change(f);assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),null);
 }
});
test('observed-cadence exception rejects numeric alternatives and missing actual cadence',()=>{
 for(const change of [f=>f.profile.directorCalibrationProfile.cadencePerMinute=8,
  f=>delete f.profile.directorCalibrationProfile.cadencePerMinute,
  f=>f.config.cadence=observedCadence+.0001,f=>f.p.inputCadencePerMinute=8,
  f=>f.p.conditions.cadencePerMinute=8,f=>delete f.config.cadence]){
  const f=normalizedFixture();change(f);assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),null);
 }
 const f=normalizedFixture();f.profile.directorCalibrationProfile.cadencePerMinute=observedCadence;
 assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),f.p,'explicit equal numeric cadence still matches normally');
});
test('effective cadence tolerates only tiny real planner floating-point roundtrip',()=>{
 const f=normalizedFixture();f.config.cadence=5.884425316359274;
 assert.equal(matchedDirectorProfile(f.config,f.profile,f.evidence),f.p);
 for(const cadence of [5.88442532,5.8844253164,8,NaN,Infinity]){
  const x=normalizedFixture();x.config.cadence=cadence;
  assert.equal(matchedDirectorProfile(x.config,x.profile,x.evidence),null,String(cadence));
 }
 for(const field of ['inputCadencePerMinute','expected','marker']){
  const x=normalizedFixture();
  if(field==='expected')x.p.conditions.cadencePerMinute=5.884425316359274;
  else if(field==='marker')x.p.cadenceNormalization.inputCadencePerMinute=5.884425316359274;
  else x.p.inputCadencePerMinute=5.884425316359274;
  assert.equal(matchedDirectorProfile(x.config,x.profile,x.evidence),null,field+' remains strict');
 }
});
