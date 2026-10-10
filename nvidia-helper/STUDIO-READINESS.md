Studio readiness changes, October 10, 2026
==========================================

Source review uses stable narration-cut keys and the exact affected sentence.
Findings outside their named cut cannot reach cache or repair. A settled invalid
binding can receive one correction; unknown/timeout requests are never replayed.
Exact quotations with explicit named attribution can verify speakers. Ambiguous
dialogue still uses the ordinary Luna review. No Decisions API is used.
Every review receipt and rejected response is retained privately. An empty model
review is not proof of perfect story fidelity or visual quality.

Automatic rental is opt-in in Advanced settings. Configure an EXISTING installed
Runpod pod, the existing dedicated SSH key file, and a project GPU cap. Studio
starts only that stopped pod at accepted image admission. Ordered directing and
canonical handoffs remain unchanged. A shared rental object spans overlapping
image jobs and the intro. Shutdown is confirmed before local rendering and in
the production finalizer on success, failure and cancellation. No pod creation,
deletion, new key grant, public ComfyUI port or model download occurs here.

The live rate is read before startup. Estimated elapsed rental includes startup
and connection time. An independent helper watchdog reserves a shutdown margin.
KEEP THE HELPER AND LAPTOP AWAKE: this is not a remote Runpod billing guarantee.
Network failure can delay shutdown. Unconfirmed shutdown persists and blocks a
second rental; Recover interrupted rental reconciles only the saved owned pod.
Retained disk/storage charges continue after compute shutdown. Actual account
billing remains the invoice authority. GPU receipts/history are kept separately
from nested stage work, so overlapping durations are not added twice.

Existing external rental mode remains available by leaving the pod ID blank.
Direct single-shot/manual image jobs continue to use their configured connected
worker; automatic rental applies to Generate full video.

Project publication retries the SAME atomic rename only for transient Windows
sharing/access-denied errors. Persistent failures retain originals and staging;
no deletion or permission weakening is used as a recovery mechanism.

Verified offline: ordinary review binding, one settled correction/no cache replay,
wrong speaker versus listener, durable project publication, rental ownership,
lost startup response, paused-startup watchdog, shutdown recovery, saved costs,
pipeline finalization on cancel/error, cloud archives, frontend settings and
private publisher isolation. Real full-video validation is tracked separately in
studio-runtime/readiness-tests; do not present fixture results as cloud runs.

API contract: authenticated Runpod REST v2 OpenAPI snapshot, October 10:
GET /v2/pods/{id}; POST /v2/pods/{id}/action with start or stop only.
https://api.runpod.io/v2/openapi.json

Existing GraphQL-only management credentials may be used through an explicitly
configured installed Runpod CLI (`runpodCLI`, private `runpodActionKeyFile`).
This is an alternate start/stop control path, never an automatic credentials
switch after a 403. Normalized pod reads remain on the configured REST path.
The key is passed privately in the child environment, not command arguments;
CLI response bodies never enter error messages. No Serverless access is added.

API ledger and director cache publication now retry the same flushed snapshot
for brief Windows sharing locks. Private append-only response evidence is kept
before receipt binding and settlement. A lost receipt stays unknown by default.
Explicit operator acknowledgement can account its entire reservation as an
unconfirmed maximum cost; it does not free budget or claim confirmed usage.
There is no automatic timeout replay or unbounded paid recovery loop.
