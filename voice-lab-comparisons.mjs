// Current native-delivery round: real imported recordings with explicit listening-review status.
export const VOICE_COMPARISONS = Object.freeze([
  {
    "referenceId": "4b30476debba4c45adc02a59868373ab",
    "sourceReferenceId": "5efd23e2303043869f4f03fb0f625c33",
    "model": "voxcpm2",
    "executionKind": "private-native-worker",
    "executionLabel": "RunPod private native worker - paid test",
    "billingKind": "paid-runpod-test",
    "checkpointRevision": "32279effe8c19989596f05d353d1447f51d9e915",
    "backend": "VoxCPM optimize=False",
    "delivery": "native-expression-assembled",
    "assembly": "joined-native-sections",
    "gainDB": 0,
    "qcStatus": "CONTENT_CHECKED_LISTENING_PENDING",
    "qualityNote": "The full separately generated quiet quote and surrounding narration are preserved. Automatic recognition of the joined recording has a low-confidence Do/to discrepancy at the first quote word; the standalone quote matches the script. Listen to judge wording, expression, voice identity, breaths, noise and joins.",
    "canonicalAudioSHA256": "543254ae876d16e2e96b8e29f6cac580e3be0cad1d2f4bb92b95c044a6dd6357",
    "expectedDurationSeconds": 28.397333333333332,
    "processingReceiptSHA256": "2ce6a68e605246f371ca5f4104ce16587e2c3f4bd713b785a092f5b53673c27d"
  }
]);
export const PENDING_COMPARISONS = Object.freeze([{sourceReferenceId:'5efd23e2303043869f4f03fb0f625c33',models:['drama3'],status:'unavailable',note:'Unavailable this round: compatible GPU capacity was unavailable after setup; no Drama audio generated.'}]);

// Prior round recordings remain stored privately, outside the current comparison.
export const ARCHIVED_COMPARISON_IDS = Object.freeze([
  "2325634ff6284116a74139d693be563d",
  "2b68e64f687c40cdb5c3188b26e7e440",
  "85df764347c04cb7952d0faef132e7cf",
  "7426b12af810428f9f9d4700fb1cdc79",
  "2b8866279ad7448992abb7bbc5fc6a52",
  "8eefc34cfb7544cdba57b7fc1e1510ed"
]);

export function validateComparisons(manifest) {
  const ids = new Set();
  for (const item of manifest) {
    if (!/^[a-f0-9]{32}$/.test(item.referenceId||'') || !/^[a-f0-9]{32}$/.test(item.sourceReferenceId||'') || item.referenceId===item.sourceReferenceId || ids.has(item.referenceId)) throw Error('Invalid clone comparison reference.');
    if (!['qwen3','drama3','voxcpm2'].includes(item.model) || !['native','native-volume-delivery','native-expression','native-expression-assembled','quote-volume-adjusted'].includes(item.delivery)) throw Error('Invalid clone comparison delivery.');
    if (item.delivery==='quote-volume-adjusted' && !(Number.isFinite(item.gainDB) && item.gainDB<0 && item.gainDB>=-60)) throw Error('A quote-volume comparison needs its actual gain.');
    if (item.delivery!=='quote-volume-adjusted' && item.gainDB!==undefined && item.gainDB!==0) throw Error('Native delivery cannot include quote gain.');
    if (['native-expression','native-expression-assembled'].includes(item.delivery) && item.gainDB!==0) throw Error('Native expressive speech requires explicit zero quote gain.');
    if (item.delivery==='native-expression-assembled' && item.assembly!=='joined-native-sections') throw Error('Joined native speech needs an assembly label.');
    ids.add(item.referenceId);
  }
  return manifest;
}

export function comparisonDeliveryLabel(item) {
  if (item.delivery==='native-expression') return 'Native expressive delivery · no quote gain applied';
  if (item.delivery==='native-expression-assembled') return 'Native speech sections joined · no quote gain applied';
  return item.delivery==='quote-volume-adjusted'
    ? `Quoted dialogue lowered by ${Number(Math.abs(item.gainDB).toFixed(2))} dB after generation`
    : item.delivery==='native-volume-delivery' ? 'Native quieter quote volume · no quote gain applied' : 'Native model delivery · no quote-volume adjustment';
}
