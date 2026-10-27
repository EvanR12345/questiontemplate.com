export const VOICE_BATCH = Object.freeze({
  id: 'anchored-coach-20261027',
  startedAt: 1793086776,
  label: 'Coach voice and related takes',
});

// The helper retains the originals. This view shows only the new audition round.
export function currentVoiceBatch(snapshot, batch = VOICE_BATCH) {
  const isCurrent = item => Number.isFinite(item?.createdAt) && item.createdAt >= batch.startedAt;
  const references = (snapshot.references || []).filter(isCurrent);
  const jobs = (snapshot.jobs || []).filter(isCurrent);
  const selected = jobs.some(job => job.id === snapshot.selected?.jobId) ? snapshot.selected : null;
  return {...snapshot, references, jobs, selected};
}
