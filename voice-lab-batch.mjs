import {VOICE_COMPARISONS,PENDING_COMPARISONS,ARCHIVED_COMPARISON_IDS,validateComparisons} from './voice-lab-comparisons.mjs?v=native-quiet-waiting-20261027';

export const VOICE_BATCH = Object.freeze({
  id: 'anchored-coach-20261027',
  startedAt: 1793086776,
  label: 'Coach voice and related takes',
});

export const SUPERSEDED_REFERENCE_IDS = Object.freeze([
  '769fcd0c075d4512b0506e7b9f5440bc',
  'ab9db48f6a95408bb477d1237cf2e0ed',
  '5083257979734b75a53b44b0687b15d3',
  'aa99a22088684a8591f15540ed1002a7',
]);
const supersededReferences = new Set(SUPERSEDED_REFERENCE_IDS);

const referenceDisplay = Object.freeze({
  '5efd23e2303043869f4f03fb0f625c33': {name:'Coach · Take A · Preferred',priority:0},
  '5d7020c96f6244d4b47ed5b880bd40f0': {name:'Coach · Take B · Backup',priority:1},
  'ad2888d8405c40f4a383404fb50a5f0f': {name:'Original coach · Clean reference',priority:2},
});

// The helper retains the originals. This view shows only the new audition round.
export function currentVoiceBatch(snapshot, batch = VOICE_BATCH, manifest = VOICE_COMPARISONS, pendingManifest = PENDING_COMPARISONS) {
  validateComparisons(manifest);
  const comparisonIds = new Set([...ARCHIVED_COMPARISON_IDS,...manifest.map(item => item.referenceId)]);
  const isCurrent = item => Number.isFinite(item?.createdAt) && item.createdAt >= batch.startedAt;
  const references = (snapshot.references || [])
    .filter(item => isCurrent(item) && !supersededReferences.has(item.id) && !comparisonIds.has(item.id))
    .map(item => referenceDisplay[item.id] ? {...item,name:referenceDisplay[item.id].name} : item)
    .sort((a,b) => (referenceDisplay[a.id]?.priority??3)-(referenceDisplay[b.id]?.priority??3)||(b.createdAt||0)-(a.createdAt||0));
  const sourceIds = new Set(references.map(item => item.id));
  const comparisons = manifest.flatMap(item => {
    const reference = (snapshot.references || []).find(ref => ref.id===item.referenceId && isCurrent(ref));
    return reference && sourceIds.has(item.sourceReferenceId) && !supersededReferences.has(item.referenceId) ? [{...item,reference}] : [];
  });
  const pendingComparisons = pendingManifest.flatMap(item => {
    if (!sourceIds.has(item.sourceReferenceId)) return [];
    const models = item.models.filter(model => !comparisons.some(result => result.sourceReferenceId===item.sourceReferenceId && result.model===model));
    return models.length ? [{...item,models}] : [];
  });
  const jobs = (snapshot.jobs || []).filter(item => isCurrent(item) && !supersededReferences.has(item.referenceId) && !comparisonIds.has(item.referenceId));
  const selected = jobs.some(job => job.id === snapshot.selected?.jobId) ? snapshot.selected : null;
  return {...snapshot, references, comparisons, pendingComparisons, jobs, selected};
}
