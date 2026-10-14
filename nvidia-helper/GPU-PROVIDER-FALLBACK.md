# RTX 5090 provider switching

Studio can store private Runpod, Vast.ai and Lium connections and an ordered
provider preference. Images and rendering have separate verification lists.
Saving a key or reading stock does not verify a workload or rent a GPU.

The image queue and full-production controller use one durable reservation.
Only an explicit no-capacity response permits another provider. Unknown create
responses, failed startup, authentication errors and unconfirmed shutdown stop
the run. They never silently create a second bill. Completed assets remain saved.
Every provider must supply one RTX 5090; model, references and settings stay fixed.

Runpod uses the existing prepared worker pool. Vast/Lium image adapters share
the existing template Torch environment, pinned Comfy v0.30.0 and fingerprinted
Klein 4B weights between two independent private Comfy processes. No model files
or additional Torch environment are installed on the laptop. SSH host identity
is pinned per owned allocation; Comfy listens only on loopback.

## Verification status on 2026-10-14

All three APIs authenticated. Runpod image production was already tested. Vast
and Lium each completed the same 17-image public-reference protocol: Klein 4B
FP8, 1280×720, four steps, guidance 1, one reference, frozen prompts/seeds and
two independent Comfy processes. Every output decoded, passed the technical
near-black check and was SHA-verified in private Google storage. This does not
certify story fidelity, anatomy or identity quality.

| Observation | Vast | Lium |
| --- | ---: | ---: |
| Warm serial server execution median | 7.791 s | 1.635 s |
| Warm serial image received median | 10.047 s | 5.297 s |
| Warm four-image group including cloud saving | 41.172 s | 15.625 s |
| Observed complete cloud-saved throughput | 5.83/min | 15.36/min |
| Successful attempt setup | 101.781 s | 195 s |
| Torch/CUDA build | 2.7.1+cu128 | 2.11.0+cu128 |

The faster Lium group was 2.64× faster in this small protocol. Host, location,
driver, CPU and Torch differed; this is a practical setup comparison, not proof
that provider identity causes the difference. The Vast successful setup reused
previously prepared files. First parallel groups include warming the second
copy. Admission and cloud I/O also differ between serial and four-slot modes;
this does not isolate the effect of model-copy count. Older Runpod samples use
different inputs and cannot be ranked as a matched third arm of this study.

Startup failures are retained: runtime discovery, missing compiler, canonical
API slash, null startup status, duplicate SSH grant and mismatched support
wheels were fixed. Official CUDA128 torchaudio/torchvision wheels now align
with the existing template Torch; `--no-deps` prevents another Torch/CUDA install.
Native FP8 matrix multiplication remains an unverified opt-in experiment and
is off by default. The low-fee Vast cold attempt timed out; it has no image-speed
measurement. All disposable study resources were removed/released.

An hourly-only estimate missed transfer charges: the first Vast host advertised
$0.052083/GB networking. Its account balance fell approximately $0.650, including
the study's rentals/traffic/storage; this is not an itemized invoice. Lium's
cumulative compute-window estimate is approximately $0.181. Including the $0.10
operations/storage allowance gives approximately $0.931 of the $1 cap. Every
failed attempt and the interruption's retained disk are included. Near-free
Vast networking is now required by default, and a cold-transfer reserve is
deducted before computing the spending deadline. Working disk already included
in marketplace quotes is not added twice by the planner.

Default private preference is Runpod → Lium → Vast. Availability changes and is
not guaranteed. Marketplaces and backend fingerprints are separated in timing
history; a new backend cannot inherit another backend's measured rate. Quotes
remain historical observations, not guaranteed fresh availability or prices.

Tests: 132 backend and 26 focused frontend passed before final installation.
Prior study caps remain closed; this comparison does not authorize production.

Cloud rendering remains limited to verified720p30 previews of up to120seconds.
Two-hour cloud exports are estimates, not enabled production. Local Kokoro voice
and the current full-video renderer can create temporary local media. Verified
historical project media was migrated to private Google storage; runtime,
credentials, recovery databases and small working project metadata stay local.
