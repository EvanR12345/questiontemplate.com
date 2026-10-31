import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {completedProductionEvidence} from './pipeline-production-calibration.mjs';
import {qualityBlockedProductionEvidence} from './pipeline-production-components.mjs';

const read=name=>JSON.parse(fs.readFileSync(new URL(name,import.meta.url),'utf8'));
const near=(a,b)=>assert.ok(Math.abs(a-b)<1e-12,`${a} != ${b}`);

test('one launcher rental adds the maximum overlapping receipt once, with existing reserves unchanged',()=>{
 const p=read('./pipeline-cache-launcher-20261031.json'),d=read('./pipeline-production-calibration.json');
 const c=p.cost,prior=d.studyBudget.firstPublicationAccounting,current=d.studyBudget.finalAccounting;
 const increment=Math.max(c.originalPilotResultGPUAndDiskUpperUSD,c.ownedGuardGPUAndDiskUpperUSD);
 near(increment,.030397565642992654);near(c.incrementalGPUAndDiskConservativeUpperUSD,increment);
 near(prior.totalConservativeIncludingReservesUSD,1.7330712851841146);
 near(current.GPUAndDiskConservativeUpperUSD,prior.GPUAndDiskConservativeUpperUSD+increment);
 near(d.studyBudget.accountedFinalUSD,prior.totalConservativeIncludingReservesUSD+increment);
 near(current.remainingAgainstThreeDollarCapUSD,3-d.studyBudget.accountedFinalUSD);
 for(const key of ['APISettledUSD','CPUCachePreparationConservativeUSD','full48HourModelCacheLeaseReserveUSD','GoogleStorageAndTransferReserveUSD'])assert.equal(current[key],prior[key]);
 assert.ok(increment<=.3&&current.GPUAndDiskConservativeUpperUSD<=1.5&&d.studyBudget.accountedFinalUSD<=3);
 assert.equal(c.invoiceVerified,false);assert.equal(c.sameCacheAndGCSReservesIncludedOnce,true);
});

test('successful one-image explicit launcher remains separate from quality-blocked production and forecast',()=>{
 const p=read('./pipeline-cache-launcher-20261031.json'),d=read('./pipeline-production-calibration.json');
 assert.equal(p.status,'PASS_SINGLE_IMAGE_EXPLICIT_JOB_LAUNCHER');
 assert.equal(p.nativeTwoWorkersVerified,true);assert.equal(p.requiredCachedModelFilesFullSHA256Verified,3);
 assert.equal(p.timings.allocationIntentThroughNativeTwoWorkerReadySeconds.toFixed(3),'67.532');
 assert.equal(p.timings.allocationIntentThroughSavedPNGSeconds.toFixed(3),'85.735');
 assert.equal(p.image.generatedPNGs,1);assert.equal(p.image.references,2);
 assert.equal(p.image.RGBSHA256,p.image.baselineRGBSHA256);assert.equal(p.image.pixelsIdenticalAcrossFreshWorkers,true);
 assert.equal(p.image.freshSamplerDecode,true);assert.equal(p.image.positiveEncoderFresh,true);
 assert.equal(p.lifecycle.status,'DELETED_INDEPENDENT_GET404_CONFIRMED');assert.equal(p.lifecycle.unresolvedGPULiability,false);
 assert.equal(p.lifecycle.cacheLeaseExtended,false);
 assert.equal(d.completion,null);assert.equal(completedProductionEvidence({productionCalibration:d}),null);
 assert.ok(qualityBlockedProductionEvidence({productionCalibration:d}));assert.equal(d.partial.blockingImages,26);
 near(d.partial.startup.seconds,135.3613796234131);assert.equal(d.partial.freshRenderVerified,false);
 assert.equal(p.unchanged.forecastUsesThisPilotStartup,false);assert.equal(d.cacheLauncherPilot.forecastUsesPilotStartup,false);
 assert.equal(d.cacheIntegration.nativePoolAutomaticAttachmentWired,false);assert.equal(d.cacheIntegration.nativePoolAutomaticAllocationWired,false);
 assert.deepEqual(p.unchanged.conditionalScheduleMinutes,d.conditionalScenarios.cases.map(c=>c.conditionalScheduleMinutes));
 assert.deepEqual(p.unchanged.conditionalScheduleMinutes.map(n=>n.toFixed(2)),['97.08','112.70','133.34']);
 assert.equal(p.unchanged.narrationEstimateMinutes,12.3);
 for(const key of ['OpenAIRequests','voiceRequests','cloudArchiveMutations'])assert.equal(p.unchanged[key],0);
});

test('public launcher observation is sanitized and report fetches both new accounting and separate proof with a fresh cache tag',()=>{
 const url=new URL('./pipeline-cache-launcher-20261031.json',import.meta.url),text=fs.readFileSync(url,'utf8'),p=JSON.parse(text);
 assert.doesNotMatch(text,/(?:[A-Z]:\\|https?:\/\/|127\.0\.0\.1|podId|jobId|volumeId|remotePromptId|sk-proj-|apiKey)/i);
 assert.equal(Object.keys(p.proofSHA256).length,7);
 for(const sha of Object.values(p.proofSHA256))assert.match(sha,/^[a-f0-9]{64}$/);
 const report=fs.readFileSync(new URL('./pipeline-production-20261031.html',import.meta.url),'utf8');
 assert.match(report,/fetch\('\.\/pipeline-production-calibration\.json\?v=cache-launcher-observation-20261031'\)/);
 assert.match(report,/fetch\('\.\/pipeline-cache-launcher-20261031\.json\?v=cache-launcher-observation-20261031'\)/);
 assert.match(report,/standard website pool still does not automatically allocate/);
 assert.match(report,/135\.361/);assert.match(report,/12\.3 minutes/);assert.doesNotMatch(report,/\$1\.733071|\$1\.183456201|\$1\.266929/);
});
