# Studio whole-process fixes - October 12, 2026

Following saved settings now uses the installed single ComfyUI queue and the configured GPU, keeping experimental multi-copy/render-overlap comparisons separate. A two-hour forecast is about 97.9 minutes with Practical checks versus 94.8 with checks Off under the saved example assumptions. These are projections: the actual four Luna slots differ from the eight-slot calibration, and retries, transfers, rate limits and archive work can change results.

The helper now verifies narration cache signatures without restoring a cloud WAV, versions intro audio without overwriting previous masters, retains full scene context for fresh repairs, preserves mandatory identity references when selecting editing, and invalidates uploaded-reference caches after worker restart. QC learns the actual reviewer profile and captures the settings used by each review; changing review settings rechecks saved art without starting an image GPU, while explicit user acceptance remains respected. Intros keep project-owned references after chapter changes.

An exact-byte 32 MiB reference-preparation cache reduced 100 preparations of a 1 MiB synthetic fixture from 0.719 to 0.060 seconds in eight alternated paired trials. It changes no image pixels. This is a preparation-stage result, not an end-to-end image or video benchmark.

Validation:694 broad backend tests,309 frontend tests and29 private publisher tests passed; additional focused tests cover the final review-only dispatch/forecast guards. No paid API calls, rentals, production run or image generation was performed for this review. Existing projects, media, manual edits and the working helper environment are preserved. Earlier visual findings remain unresolved until an explicitly budgeted production run checks or repairs them.

Nested whole-review measurements and Luna API-call measurements now remain separate, preventing incompatible QC timing samples from being mixed. Twenty repeated synthetic director-drain tests passed after correcting a reusable mock barrier; real unresolved reservations remain protected.
