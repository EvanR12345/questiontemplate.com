# Luna visual-check admission pilot — October 11, 2026

28 fresh Standard Luna visual checks on seven curated, previously generated
short-preview images. Four API slots; independent adapters and isolated local
response caches per arm. Arms ran3,5,5,3 admission tasks. No GPU/image generation.
The image lane used a fixed10ms placeholder, so this does not measure a complete
image/QC pipeline or production speed with a rented GPU.

| Admission tasks | Seven-check batch times | Mean elapsed | Estimated API usage, 14 checks |
|---|---|---|---|
| 3 | 24.375s;20.375s | 22.375s | US$0.003258403 |
| 5 | 23.328s;22.360s | 22.844s | US$0.003276404 |

Five-task batches were2.1%slower in this small QC-only comparison. There is no
demonstrated real speedup, and two runs per configuration are insufficient to
establish a reliable winner. A prior synthetic scheduler test improved29.2%;
that is a scheduling-capacity result, not measured Luna/GPU production speed.

All28requests settled; zero retries and unresolved reservations. Total estimated
API usage US$0.006534807 (about0.65cents); mean per check US$0.000233386.
36,748inputtokens and5,720outputtokens; zero reported cache-read/write tokens.
Actual billed debit can differ under account free-token eligibility. No GPU or
storage resource was created. Original projects and production caches unchanged.

Individual API review mean9.902s, median9.813s, range5.359–13.531s. These sum to
work, not batch elapsed time. This small curated sample does not replace the
planner's broader historical estimate or calibrate fresh image generation.

Verdicts varied on identical images. Three-task arms returned5/7 and6/7 issue-free
results; five-task arms returned6/7 and6/7. One scene detail changed severity across
uncertain,major andadvisory findings. These are reviewer verdicts, not independently
graded accuracy or usable-image percentages. Practical advisory decisions, manual
acceptances, and original findings remain intact; no automatic repairs purchased.

Initial benchmark iterations exposed local cache hits: only seven calls were
fresh. Those cache timings were excluded. The remaining21approved fresh calls used
separate benchmark cache roots to produce the final28-call comparison. Production
caching remains enabled. Older three-task image throughput is not used to certify
the five-task production path. No long-video completion guarantee follows.
