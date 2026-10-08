# Private Google render worker

Implemented source, not an active deployment. Existing Studio still renders on
the laptop until the Google archive, private control routes, build and deployed
job have all passed a live smoke test. This worker uses existing narration and
images; cloud narration synthesis is a separate remaining stage.

Build the explicit allowlisted sources using `cloud-render-build.yaml` in Google
Cloud Build, with `_IMAGE` set to the dedicated Artifact Registry image. No
laptop Docker installation, Torch/CUDA download or model replacement is needed.
The container uses official Python and Debian packages, FFmpeg, Pillow and the
Google storage client. Pin the built image digest before deploying a job.

Deployment specification for review: job `studio-render`, us-east1, one task,
parallelism one, four vCPUs, 8 GiB RAM, zero automatic execution retries and a
two-hour task timeout. Attach the existing bucket-only Studio storage identity;
never create a downloaded Google private key. Mount an actual ephemeral disk
at `/work`; do not replace it with an in-memory volume. The initial regional
quota is only 10 GiB per instance. A larger quota or an alternative disk-backed
worker must be validated for long productions before claiming twelve-hour
support. A deployed idle Cloud Run job has no continuously running worker.

Each invocation receives only `STUDIO_RENDER_JOB_ID` (32 hexadecimal characters),
`STUDIO_BUCKET`, and `GOOGLE_CLOUD_PROJECT`. The job record in the private bucket
pins project, saved revision, destination and cancellation state. The entry
point refuses execution without Cloud Run's `CLOUD_RUN_JOB` environment.

Completed clip files upload as immutable checksum-addressed assets. Progress
checkpoints are written about every four seconds; cancellation is checked at
least every two seconds while FFmpeg is running. Network operations can delay
these intervals. A normal final MP4 becomes playable only after its encoding,
upload and manifest commit finish. Wall-clock cloud rendering speed has not
been measured yet.

Only one execution claims a given job. Previously completed checkpoints survive
failure and cancellation. If a worker is killed before writing a terminal
status, reconcile its actual Cloud Run execution before explicitly requeuing;
do not expire a lease and launch duplicate paid work automatically. Project
manifest updates use the original generation precondition, so website edits
made during rendering are retained and the older render remains checkpointed.

Patreon exports omit YouTube's outro/reminders by default; both destinations
reuse narration masters and clip caches. Chapter-based parts can contain one
or several consecutive complete chapters. Every generated part receives only
the selected destination's ending. These controls do not regenerate images,
directing or chapter narration. Native Patreon upload still requires testing
after a cloud-rendered video exists; no Patreon video has been uploaded yet.
