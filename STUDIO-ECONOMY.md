# Fantasy production and bounded costs

The existing Studio uses the same local helper and voice environment. Extended shouts are normalized for pronunciation without changing the saved story. Recognized words between single or double asterisks (bang, bam, boom, thud, click, beep, slash, whoosh, crash) become locally synthesized effects in the continuous chapter WAV. Other emphasized text remains narration. The sentence timing includes these effects.

The optional intro supports 10–30 seconds, remains separate from chapter narration and defaults to full-story placement. Only explicit intro voice text is spoken. Existing projects and generated assets remain intact.

## Economy storyboard canvases

Settings contains an explicit opt-in and **Prepare narration and Luna direction** action. This operation updates an already analyzed chapter’s creative shot directions while preserving the original story facts, shot identities, cast, and narration intervals. Luna uses structured JSON in groups of 20 shots. Manual shot fields remain protected. Seven unrelated downloaded character references can remain in the library without being inserted into a chapter.

Image generation combines up to four independently prompted landscape shots in one 1344×768 Qwen canvas. Each quadrant is cropped to 640×360, with a small inset to remove seams. The original canvas, exact prompts, reference ordering, resolved workflow, seed, crop coordinates, parameters and timings are retained beside each result. Completed shots are saved immediately and reused after pause or refresh, including partially completed groups. Missing files are recovered without replacing valid saved images. An individual shot can still be generated normally.

This can reduce model calls by approximately 75% when all groups contain four shots. It reduces per-shot detail and can mix compositions or identities between quadrants. A real sample needs human inspection before a long run. These images receive **UNREVIEWED** status; pixel validation does not imply a passed visual continuity check. Shared-canvas seed replay requires the stored full canvas prompt and settings, rather than an individual-shot prompt.

## Spend protection

Optional project settings `budget.openaiUSD` enable a durable API ledger. Each request reserves a conservative upper bound before sending and settles against returned token usage. An unresolved request blocks further paid requests; uncertain usage is conservatively charged to the ledger. Prices are recorded with the receipt. Free daily OpenAI allowances are not assumed.

For rented GPU generation, supply the actual `cloudStartedAt` timestamp and `gpuHourlyUSD` in the job options (or saved `settings.cloudWindow`). Resumed rental sessions also supply `previousGPUUSD` and `previousGPUSeconds`, so the cap and displayed totals include previous sessions. With `budget.runpodUSD`, the helper refuses another canvas when its estimated completion plus a 25-second stop reserve would exceed the cap. Startup time counts. **This guard stops future image work; it does not stop the rented Runpod pod.** The operator must stop it promptly through the Runpod console. Retained network storage remains separately billed. Do not render videos or prepare narration while the rented GPU is idle.

Runpod and OpenAI amounts shown in Studio are estimates from measured time and token usage, not a substitute for their account billing statements. Keep model loading, generation, image transfer, QC, repair and rendering timings separate. A live RTX PRO 6000 sample took 96 seconds for its first canvas including cold model loading, then about 4–7 seconds of backend generation per warm canvas (5–8 seconds including transfer and saving). These are measured samples, not a promise for every shot. A budget too small for startup and generation requires explicit approval of a revised cap; do not silently switch to a slower local provider.

## Validation

Automated checks cover shout/contraction normalization, effect segmentation and finite audio, API ledger persistence and limits, four-panel crop mapping and grouping coverage, existing generation queue controls, project persistence, Qwen workflow metadata, chapter and full-story rendering. Live visual quality, model startup and total rental time still require an actual bounded production run.
