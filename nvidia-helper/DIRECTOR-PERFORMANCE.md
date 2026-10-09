# Luna directing: measured optimization, October 9, 2026

The director is GPT-6 Luna. Images remain FLUX.2 Klein 4B; local voice, references, image parameters, selected cadence and video defaults are unchanged. This study generated plans and text only. It did not generate images or render a production video.

## Observed results

Two saved chapters had 1,050.23 seconds of existing narration. Each complete-plan arm started with the same source text, accepted character library, settings and empty planning cache. Model responses were not deterministic: subsequent call inputs and shot counts differ. One complete-plan repetition per arm does not establish statistical significance or equal visual quality.

| Arm | Chapter planning elapsed | API work | Estimated API cost | Planned shots |
|---|---:|---:|---:|---:|
| Serial | 420.328 s | 409.911 s | $0.042708 | 106 |
| Two concurrent tasks | 308.485 s | 385.362 s | $0.042934 | 107 |
| Three concurrent tasks | 320.782 s | 415.422 s | $0.047657 | 115 |
| Two + short supplements | 319.469 s | See published receipt summary | $0.043270 | 121 |

Two tasks reduced observed wall time by 26.6% against the serial arm. Three did not win this small comparison; the additional calls/repairs and different plans confound a simple slot ranking. API work is the sum of call durations and is not elapsed time or GPU utilization.

The isolated prompt study used three identical, frozen 12-shot batches in each of four rounds, rotating arm order. A fresh exact-result cache was used for each arm/round. All 36 requests used the same model, reasoning and processing tier; only concurrency and the short-supplement instruction differed.

| Prompt arm | Median elapsed / three batches | Mean estimated cost / round | Median supplement words |
|---|---:|---:|---:|
| Serial | 37.899 s | $0.003751 | 80.5 |
| Three concurrent | 12.415 s | $0.003618 | 71.5 |
| Three + short supplements | 7.961 s | $0.002370 | 27 |

The short text supplements composition, expression and lighting; it does **not** replace the application's full grounded draft. The repeated prompt result is not a whole-video speedup or image-quality result. Four rounds do not establish a general latency distribution.

A separate exploratory one-chapter arm doubled the remote group limit from 48 sentences / 9,000 characters to 96 / 18,000. It took 102.922 seconds, versus 141.281 for the ordinary two-task chapter, but planned 40 shots rather than 45 and 14 scenes rather than 21. Production limits remain unchanged. This needs a larger paired semantic/cadence evaluation before adoption.

All text-only requests together cost an estimated **$0.231084 of the approved $0.25 cumulative cap**. Confirmed usage receipts are settled; no pending reservation remains. Prices are receipt-based estimates, not an itemized invoice or a claim about free shared-traffic credits. Original saved project bytes were unchanged. All variants covered narration from zero to its end without missing/overlapping shot intervals. The production reference selector found no missing required main-character references in the saved plans. This is wiring/structural validation, not visual identity proof.

Sanitized numbers are in `pipeline-director-study.json`; the planner's Evidence & limits panel shows comparisons and labels linear projections. Private story data, portraits, source project IDs, keys and private test paths are excluded. The main schedule still uses its earlier director calibration and assumed stage allocation; the study is not silently substituted into GPU comparisons.

## Installed behavior

Studio Settings → Advanced AI settings provides **Independent Luna tasks** (one, two or three). Existing projects default to one. It applies only when the existing **Overlap cloud tasks** option is enabled. Two is a conservative starting point, leaving one shared API slot for existing visual QC.

`director_tasks.py` admits a bounded window and collects results in input order. Independent camera, workflow and continuity review read the same finished shot facts. Prompt batches have frozen inputs and separate adapters, budget reservations and durable execution owners. Chronological fact extraction, appearance changes, group state and chapter handoffs remain ordered.

On failure, new admission stops and already submitted sibling responses drain for billing receipts. Pause holds dispatch and lets paid responses settle; cancellation preserves unresolved liability when an accepted response cannot be collected. UNKNOWN operations are not silently replayed. Input signatures include canonical descriptions, aliases and default appearance; edits during requests cannot overwrite current work.

**Short visual-direction supplements** is a separate optional flag. It reuses existing prompt guidance without enabling the separate scene-fact guidance. Existing/manual prompts stay saved; no automatic rewrite of old shots occurs.

Prompt mappings must contain exactly one nonempty entry per supplied shot. Invalid responses are charged correctly and are never published as reusable successful cache entries. An older damaged/incomplete cache is retained for diagnosis and requires an explicit retry; discovering it does not buy a replacement implicitly.

Telemetry separates API-lane wait, connection/headers, first text, stream duration, output limits, attempts, reasoning tokens and visible output tokens. Reasoning tokens already belong to billed output; they are not counted twice. Missing reasoning usage stays unknown. Failed requests report elapsed time, settled cost and unresolved reservations separately without recording payloads or credentials.

## Why more threads cannot remove the remaining bottleneck

The old 313.4-minute directing chart is an extrapolation from the October 3 isolated 471.463-second / 48-call experiment. Its per-pass allocations were assumptions. The new study measures individual calls, but did not run 80 chapters or a 12-hour production.

Casting → analyst → scene boundaries → shot facts are still dependent. Parallel final passes and prompts cannot run the next chapter before its preceding factual handoff exists. Bigger groups may reduce call overhead, but the single exploratory arm changed creative granularity. Keeping the image cadence and semantic quality matters more than choosing the fastest isolated number.

A generated 80-chapter / 1,600-shot project with one history snapshot per chapter occupied 13.63 MB. Five-round medians were 0.084 s to load, 0.114 s to deep-copy, 0.0013 s for an analysis signature and 0.506 s to append a timing receipt through a full project save. This is a bounded bookkeeping fixture, excluding trace writes, network and inference. Whole-project writes merit a durable delta design; deleting historical state or weakening persistence is not justified by this result.

## Next architecture: factual checkpoint before visual finishing

1. Extract explicit source events using accepted IDs, sentence evidence and uncertainty. Keep pronoun/alias resolution ordered when prior context is required.
2. Reduce accepted events chronologically into versioned canonical state. Commit a factual handoff independently of visual planning.
3. Freeze each chapter's incoming state and source/character signatures. Use real narration timing for scene planning; keep voice work serialized on the local GPU.
4. Let an independent visual worker finish camera and prompt groups while the next chapter consumes the accepted factual checkpoint.
5. Publish images only after complete validated prompt groups exist. Do not dispatch images from partial streamed JSON or an unaccepted proposed plan.

This is **not implemented or timed**. Required acceptance tests include manual-plan preservation; uncertain identity resolution; appearance/object carry-forward; source edits after admission; fact-vs-visual invalidation; shutdown recovery with settled/UNKNOWN requests; no replay of purchased operations; shared API budget/limits; and bounded project memory. It should be evaluated before claiming a larger whole-story speedup.

Other candidates remain optional research: merging scene/camera wire output, role-specific reasoning, stable-prefix caching, and tomorrow's asynchronous preplanning. Do not reduce semantic passes or output limits merely to improve a timing number.

## Official documentation

- [Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization): reducing generated output and parallel independent steps target the main sources of latency. Input shortening alone is often a smaller latency gain.
- [Prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching): explicit mode without breakpoints performs no caching/writes. Reusable prefixes must meet the model's minimum; caching does not speed output decoding. The existing explicit mode is retained.
- [Batch](https://developers.openai.com/api/docs/guides/batch): 50% price discount with a 24-hour completion window. Useful for nonurgent preplanning, not a guaranteed immediate speedup; dependent waves still need orchestration.
- [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna) and [Fast processing](https://developers.openai.com/api/docs/guides/fast-mode): model and Standard processing remain unchanged. Faster processing has different pricing and would need corresponding conservative reservations and a separate bounded test.
