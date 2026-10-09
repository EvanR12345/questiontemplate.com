# Actual image scheduling and private timing history

October 9, 2026. This update does not change the image model, resolution,
visual cadence, narrator, reasoning setting, checks or saved director concurrency.

Studio shows a saved project-level strategy before Generate full video.
New/unselected choices default to **Timed · align Luna + images · 15s buffer**.
Timed modes require the updated helper, cloud ComfyUI, Luna and a positive API cap.
Older clients and API calls without a strategy keep the existing behavior.

## What actually runs

`studio_schedule.py` admits already accepted chapters from a separate thread.
The main thread keeps narration, story analysis, identity acceptance, factual
state and chapter handoffs ordered. The existing bounded three-shot pool, one
image lane, shared three-call API pool, durable remote receipts, selected checks,
repair policy and rendering prerequisites remain in force. Independent director
passes still use the saved one/two/three-call setting.

* Align waits until compatible observed ready-image work covers the forecast
  planning tail minus 15 seconds. It is a dispatch heuristic, not an optimal
  schedule or guaranteed simultaneous finish. Unknown history plans first.
* Fastest starts an accepted chapter when it is ready and keeps planning
  independent of the image admission window. Chapters needing automatically
  generated character portraits wait until planning ends: changing canonical
  references during paid directing would invalidate the director inputs.
* Existing workflow preserves the previous serial/overlap checkbox behavior.

**These modes schedule image requests, not Runpod rental.** They do not create,
start, stop or delete a pod. Align readiness validates the declared workflow
offline; live model, memory and backend validation still happens before images.
Fastest/existing workflows require the configured live worker at readiness.
An unavailable worker at image dispatch stops with saved plans for a retry; it
does not silently rent a GPU. Keep the worker stopped during offline planning
and connect it before the planned image window.
A manually running GPU bills while planning/waiting. The planner's proposed
latest-boot rental optimization remains a simulation. Do not claim identical
rental costs to starting after the final chapter or a achieved speedup from
this unmeasured production change. Explicit rental orchestration is remaining
work and cannot be inferred from a planner timestamp.

## Persistence and calibration

`studio_performance.py` stores private positive-duration profiled observations
in `performance.sqlite3`, with actual model/settings, hashed backend identity,
units, run identity, status, reuse/pause/unknown flags and estimated receipt
costs where present. No chapter text, portraits, API keys or raw host addresses
are calibration inputs. Project timing history retains profiles and observation
IDs for idempotent recovery if the optional calibration database was unavailable.
Production assets must not fail merely because that database cannot update.

Only exact-profile fresh successful settled/unpaused work calibrates speed.
Partly cached director retries cannot teach a fresh full-chapter estimate.
Changed models, selected concurrency, reasoning, voice effects, resolution or
backend do not inherit an incompatible timing. Recent history uses a 90-day
window and at most 30 samples, balanced by independent run medians. Fewer than
three runs are explicitly limited history. Observed ranges are not confidence
intervals. No fabricated uncertainty multiplier or undocumented free tokens.

Image throughput uses the union of completed fresh image/QC busy intervals,
counting overlapping work once. Per-shot latencies include queue waits and are
retained separately; they cannot be multiplied into a throughput estimate.
Mixed profiles, paused runs and failed reviews cannot train that aggregate.
The live chapter work estimate follows the same rule and only the current
attempt's matching fresh samples. It is not a complete elapsed ETA.

Studio loads matching estimates before readiness/start, on explicit refresh and
after a completed/failed/cancelled job. Old unprofiled history remains visible;
today's settings are never assigned retroactively to old timings. Unknown work
stays unknown. Forecast coverage currently includes remaining narration/directing
and accepted images; intro, missing portraits, rendering, setup, outages, remote
rental stop and platform processing do not form a complete learned ETA. The
global drag-and-drop planner retains labeled historical/simulated calibration;
it does not silently replace pass weights with concurrent parent wall times.

## Verification

247 top-level frontend tests and 102 focused backend tests passed. Synthetic
remote endpoints covered image admission beyond one window, existing portrait
overlap, ordered missing portraits, calibrated/unknown alignment, pause/resume,
cancel before dispatch, changed manual prompt, failed QC, calibration DB failure,
cache reuse exclusion, restart persistence, profile mismatches and interval union.
Existing execution, overlap, director, full-video and queue tests also passed.
No new paid API calls, GPU rentals, image tests or production runs were started.
Live throughput, quality and full-video costs remain unmeasured for this update.
