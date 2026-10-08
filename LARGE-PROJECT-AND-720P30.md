# Selected output and large-project performance

The selected new-project video default is **1280 × 720 at 30 fps**. Existing project settings and already rendered videos are preserved; an explicitly inherited project profile can still use its saved settings. The planner exposes output resolution and frame rate separately from image generation resolution.

Render estimates use the matched October 8 local test: nine fresh clips per format, three five-second motions per randomized round, three rounds, libx264 veryfast CRF21, one clip worker, two filter/encoder threads. Six output profiles cover 720p/1080p and 24/30/60 fps. The old repeated-asset 720p24 fixture remains historical evidence and is not substituted for 720p30.

These are **projections**, including duration-scaled delivery allowances. They are not full-production guarantees for 90–1440 minutes. Chapter mux, overlays, cloud transfer, platform processing, production contention and a two-worker speedup are not established by the fresh clip measurements. Optional full-decode timing is explicitly labeled as an estimate at other frame rates/resolutions. Image throughput is unchanged by video frame rate.

For large projects:

- Scheduler candidates retain their earliest feasible slot until a new shared-resource allocation affects it. Recalculation starts from that bound rather than replaying every previous conflict. Six differential scenarios retain exactly the original task times and costs, including 80 chapters at 90 and 1440 minutes.
- Measured 80-chapter scheduling dropped from roughly 2.7–3.0 seconds to approximately 0.11 seconds after warm-up on the test laptop. This is scheduler computation, not video generation time or a guaranteed whole-page load time.
- Plan calculation and startup comparisons run in cancellable browser workers. Startup candidates retain aggregate results instead of duplicate full timelines. Optional GPU panels and source inventories load on demand.
- Shared reference bytes are hashed once per project-view request, not once per shot. Each new request rechecks references so changed images still invalidate manual acceptances.
- Project selection uses revision-bound compact summaries, with file fingerprints detecting external changes. Existing records without summaries remain compatible.
- Queue polling reuses per-revision progress counts rather than flattening every chapter's shots repeatedly. Image previews load lazily and obtain media links through at most four concurrent requests; stale view requests cannot attach to a different project.

Verification includes an 80-chapter/1600-shot reference fixture, 80-chapter/8000-shot progress fixture, 90–1440-minute plan checks, unchanged timeline/cost comparisons, media/reference change invalidation, schema persistence and authentication tests. No image/API/GPU production was started by these changes.
