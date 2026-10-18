# Studio recovery and long-project performance

This update preserves generation models, references, cadence, visual review policy,
production strategy and saved assets. It changes status handling and repeated
read-only work. No paid API calls, GPU rentals or image generation were used.

## Fixed behavior

- A helper restart during cloud rendering retains an interrupted attempt for
  reconciliation. Queue polling no longer crashes on its interrupted status.
- The queue and Problems inbox show project-level cloud failures. Open cloud
  render goes to Files; these attempts do not offer a blind paid retry.
- History totals include retained attempts, with the visible 30-row limit stated.
- Completion reports include images whose older successful visual review became
  stale after review settings changed. Existing evidence remains saved.
- The Cloud Run container source list includes the voice-settings module needed
  by render input validation. An isolated import/signature test verifies this.
  This is a source/package fix; no new Cloud Run image was deployed in this study.
- Unreadable media folders raise a scan error rather than silently allowing a
  partial scan to appear successful.

## Matched synthetic measurements

Baseline: commit `110c38f`. Alternating old/new trials on the same laptop and
fixtures; outputs were checked for equivalence. Medians below are observed work,
not network timings or a full-production benchmark.

| Operation | Before | After | Trials |
| --- | ---: | ---: | ---: |
| Queue snapshot, 20,000 retained jobs, one timing profile | 72.62 ms | 22.69 ms | 11 |
| Queue snapshot, 20,000 jobs, 160 profiles | 83.49 ms | 21.47 ms | 11 |
| Queue snapshot, 6,000 jobs, large non-image requests | 124.17 ms | 19.35 ms | 11 |
| Project view, 80 chapters / 4,800 shots, Practical reviews | 957.95 ms | 861.45 ms | 9 |
| Same view, stale Sampled reviews | 1116.65 ms | 863.03 ms | 9 |
| Local media scan, 100 saved assets / 2,000 excluded scratch files | 305.25 ms | 19.36 ms | 9 |
| File locations, 4,800 shots sharing references | 822.45 ms | 19.51 ms | 9 |

Queue improvements come from SQLite indexes, avoiding large irrelevant payloads,
and collecting timing samples in one pass. Project scans reuse a request-local
review-settings fingerprint and first-shot sample index. File listings check
repeated reference paths once and prune excluded directories before traversal.
Every new request recalculates state; nothing relies on a persistent stale cache.

The checks-Off project-view control had no improvement (800.28 versus 835.40 ms).
Do not apply these percentages to Luna, image generation, encoding, rental cost or
end-to-end ETA. Existing production estimates remain based on their own evidence.
