# Staged Luna director: measurements and operating limits

Measured October 9 Toronto / October 10 UTC, 2026. Same GPT-6 Luna model,
selected low reasoning, saved two-chapter story, cast, canonical references,
style and existing narration timings. Fresh director caches for every run.
Source project and assets remained unchanged. No image generation, GPU rental,
audio regeneration or rendering was purchased in this study.

| Complete two-chapter directing | Elapsed | Shots | Estimated API cost | Outcome |
| --- | ---: | ---: | ---: | --- |
| Existing classic, Standard, two independent tasks | 309.187 s | 118 | $0.04282 | Completed |
| Final staged, Fast, eight tasks, run 1 | 67.266 s | 107 | $0.07072 | Completed, 4.60× faster |
| Final staged, Fast, eight tasks, run 2 | 81.906 s | 107 | $0.07532 | Completed, 3.78× faster |
| Final staged, Standard, eight tasks | 112.078 s before stopping | Chapter 1: 45 | $0.04107 | Chapter 2 rejected by fidelity review after bounded repair |

The original saved video used 103 images. The final two successes each planned
107, approximately 4% more, rather than gaining speed by cutting the picture
rate or raising it to 171 images. Both narration timelines ended at their real
435.06 / 615.17 second durations. These are two repeats of one story against
one fresh baseline, not a statistically established universal speedup, an
80-chapter benchmark, generated-image quality proof, or a full-video ETA.

Total estimated API usage across **all 21 experiments, including failures and
rejected prototypes: $0.944791171 of the approved $1 cap**. No outstanding
reservations remained. Estimates use returned token usage and processing tier;
they do not prove account credit deductions or eligibility for free traffic.

## What changed

1. Accept source facts chronologically in bounded groups. Exact sentence
   evidence, incoming state and canonical identities remain application data.
2. Independently verify proposed facts; allow one targeted correction and
   recheck. Unsupported facts stop the plan, preserving previous work.
3. Split visual tasks at appearance/possession changes and before they exceed
   24 sentences, 6,000 characters or roughly eight selected-cadence shots.
   This avoids the output-limit retry that delayed earlier candidates.
4. Run up to eight independent visual tasks through the existing shared API
   lane, ledger, execution journal and pause/cancel/edit guards. Story facts
   and cross-chapter handoffs remain ordered. Image generation stays one lane.
5. Luna returns one compact structured storyboard containing scene starts,
   shot actions, cameras, expression, pose, lighting, motion and transitions.
   Lossless short wire keys/IDs are decoded to ordinary stable project data.
   The app restores redundant end times and compiles the existing full,
   model-aware prompt; it does not ask for a second paraphrase of each shot.
6. Run separate source/storyboard fidelity checks. Targeted repair changes
   flagged visuals only, then checks them once more. This is text-plan review;
   image vision QC remains controlled by the existing independent settings.
7. Preserve observed image cadence, grounded character traits, references,
   appearance evidence, cumulative injury deltas, manual edits and history.
   No draft facts commit before the complete plan passes and inputs still match.
8. Profile actual whole-chapter work with workflow, tier, task limit and group
   version. Static workflow-selection cache reuse alone does not disqualify an
   otherwise fresh chapter; reused creative outputs, failures, pauses and
   unresolved charges stay excluded from fresh-speed estimates.

## Using it

Studio → Settings → Advanced AI settings:

- Director: **GPT-6 Luna**.
- Workflow: **Staged Luna · reviewed parallel visuals**.
- Independent tasks: **8** (lower this if API rate limits constrain throughput).
- API processing: **Fast** for the tested speed configuration, or Standard/Flex
  for an explicit cost/latency tradeoff. This differs from reasoning “Fast.”
- Keep an explicit project API cap. Cloud ComfyUI and individual image shots
  are required. Models and image settings remain unchanged.

Save settings, then Analyze a chapter or Generate full video. The production
bar displays the actual selected director workflow/tier. The timed image
strategy remains separately configurable; rental start/stop remains external.
Previously completed shots are reused until new analysis is requested. Manual
plans use the existing proposal/review flow instead of being silently replaced.

Existing projects keep their saved workflow and tier. This update does not
silently enroll them in premium processing or start production. Fast pricing
is **2× Standard token rates**, although these compact plans' measured complete
API cost was roughly 1.65–1.76× this baseline. It can fall back to Standard;
the ledger uses the returned tier, or conservatively retains the requested
tier when missing. Wider concurrency does not guarantee eightfold speed.

## Rejected approaches and remaining limits

Wide unbounded visual output caused truncation/retries. Smaller-output plans
that produced only 25 shots, or 171 shots, were rejected as cadence mismatches.
Dropping source reasoning or reviews was not used to claim the target. Standard
processing remains available but its single complete-story trial failed review,
so a 3× Standard-tier result has not been demonstrated. Review findings and
failed paid requests were retained, never counted as successful speed samples.

The independent reviewer is another model call, not a correctness oracle.
Character identity and final picture quality still require generated-image
evaluation; this study spent nothing on images. No unverified global 3×
multiplier has been injected into the planner. Real compatible production
observations continue to calibrate timing history.

Sources: [OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization),
[Fast processing](https://developers.openai.com/api/docs/guides/fast-mode),
[API pricing](https://developers.openai.com/api/docs/pricing).
