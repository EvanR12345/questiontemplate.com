export const VOICE_BATCH = Object.freeze({
  id: 'anchored-coach-20261027',
  startedAt: 1793086776,
  label: 'Coach voice and related takes',
});

export const SUPERSEDED_REFERENCE_IDS = Object.freeze([
  '769fcd0c075d4512b0506e7b9f5440bc',
  'ab9db48f6a95408bb477d1237cf2e0ed',
  '5083257979734b75a53b44b0687b15d3',
]);
const supersededReferences = new Set(SUPERSEDED_REFERENCE_IDS);

// The helper retains the originals. This view shows only the new audition round.
export function currentVoiceBatch(snapshot, batch = VOICE_BATCH) {
  const isCurrent = item => Number.isFinite(item?.createdAt) && item.createdAt >= batch.startedAt;
  const references = (snapshot.references || []).filter(item => isCurrent(item) && !supersededReferences.has(item.id));
  const jobs = (snapshot.jobs || []).filter(item => isCurrent(item) && !supersededReferences.has(item.referenceId));
  const selected = jobs.some(job => job.id === snapshot.selected?.jobId) ? snapshot.selected : null;
  return {...snapshot, references, jobs, selected};
}
