// Populated only with verified, imported recordings before publication.
export const VOICE_COMPARISONS = Object.freeze([
  {
    "referenceId": "2325634ff6284116a74139d693be563d",
    "sourceReferenceId": "ad2888d8405c40f4a383404fb50a5f0f",
    "model": "qwen3",
    "delivery": "quote-volume-adjusted",
    "gainDB": -11.30838495554223,
    "nativeContrastDB": -1.3083853013670377,
    "finalContrastDB": 10.000000122700056,
    "canonicalAudioSHA256": "2ce1c0045f2efdcb10a38be602a3c3192729f771896102dabcb5c635c7ae95f4",
    "expectedDurationSeconds": 27.167583333333333,
    "processingReceiptSHA256": "4142ded22651acb903960d42cf07bafb84153bea1de9ac928251113e3935b3e1"
  },
  {
    "referenceId": "2b68e64f687c40cdb5c3188b26e7e440",
    "sourceReferenceId": "ad2888d8405c40f4a383404fb50a5f0f",
    "model": "drama3",
    "delivery": "quote-volume-adjusted",
    "gainDB": -9.137964650184225,
    "nativeContrastDB": 0.8620356545887055,
    "finalContrastDB": 10.000000677693212,
    "canonicalAudioSHA256": "8f56065cf1a2fac74131b7d35b30acaf608cdd9c1fda1d0aeb8ada361e9896eb",
    "expectedDurationSeconds": 28.560541666666666,
    "processingReceiptSHA256": "ed1b5ab728215f276a11793e339d545414f26ac9c60839dc9ec374ff7bb4fd67"
  }
]);
export const PENDING_COMPARISONS = Object.freeze([
  {
    "sourceReferenceId": "5efd23e2303043869f4f03fb0f625c33",
    "models": [
      "qwen3",
      "drama3"
    ],
    "note": "RunPod tests in progress under approved budget."
  },
  {
    "sourceReferenceId": "5d7020c96f6244d4b47ed5b880bd40f0",
    "models": [
      "qwen3",
      "drama3"
    ],
    "note": "RunPod tests in progress under approved budget."
  }
]);

export function validateComparisons(manifest) {
  const ids = new Set();
  for (const item of manifest) {
    if (!/^[a-f0-9]{32}$/.test(item.referenceId||'') || !/^[a-f0-9]{32}$/.test(item.sourceReferenceId||'') || item.referenceId===item.sourceReferenceId || ids.has(item.referenceId)) throw Error('Invalid clone comparison reference.');
    if (!['qwen3','drama3'].includes(item.model) || !['native','native-volume-delivery','quote-volume-adjusted'].includes(item.delivery)) throw Error('Invalid clone comparison delivery.');
    if (item.delivery==='quote-volume-adjusted' && !(Number.isFinite(item.gainDB) && item.gainDB<0 && item.gainDB>=-60)) throw Error('A quote-volume comparison needs its actual gain.');
    if (item.delivery!=='quote-volume-adjusted' && item.gainDB!==undefined && item.gainDB!==0) throw Error('Native delivery cannot include quote gain.');
    ids.add(item.referenceId);
  }
  return manifest;
}

export function comparisonDeliveryLabel(item) {
  return item.delivery==='quote-volume-adjusted'
    ? `Quoted dialogue lowered by ${Number(Math.abs(item.gainDB).toFixed(2))} dB after generation`
    : item.delivery==='native-volume-delivery' ? 'Native quieter quote volume · no quote gain applied' : 'Native model delivery · no quote-volume adjustment';
}
