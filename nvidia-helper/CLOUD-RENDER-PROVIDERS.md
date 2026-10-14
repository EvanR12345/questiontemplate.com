# Cloud GPU rendering and private Google outputs

Studio's existing Files panel now has a marketplace-render adapter and the older
Google Cloud Run adapter. Storage is separate from compute: Google holds the saved
videos; an explicitly capped GPU worker can encode them. Voice remains local.

**Current rollout:** Lium completed a real 82.05-second 720p30 render, direct Google
save, full decoding/checksum verification and confirmed worker release. Its website
adapter is limited to 120-second previews. Vast remains unverified. A 12-hour cloud
export is not verified; no image-generation provider switch has been enabled.
Adding an API key or reading available offers does not enable production or rent
anything. The existing image-generation route is unchanged; alternate-provider
image generation has not been integrated or tested.

## Controls

Settings > Advanced AI settings > GPU marketplaces configures the provider order,
hourly ceiling, working disk and private credentials. Empty password fields retain
saved keys. Keys are held by the authenticated localhost helper, outside projects,
browser storage and the public repository. Internal renderer-verification flags
cannot be set through the public settings endpoint.

When a provider has passed verification, Files exposes the cloud preview control.
Save the project and every input to private Google storage first. Choose the output
destination and an explicit total render cap. The queue runs one owned allocation,
including startup in its compute estimate, with a separate operations reserve.
Reported compute windows are estimates, not final provider invoices. Vast transfer
fees and retained stopped disks must also be considered; retained disk charges can
continue after compute stops. No always-available GPU guarantee is made.

## Recovery and preservation

- Every request has a stable nonce and a durable job. Retrying a lost HTTP reply
  returns the same request; it does not create another rental.
- Refresh discovers saved status. Cancel stops future work and the owned rental.
  Pausing the main queue stops a cloud preview rather than waiting on a billed GPU.
- Cloud jobs interrupted by helper restart are marked INTERRUPTED. They are never
  automatically rerented. Generic Retry excludes cloud jobs.
- Uncertain allocation/shutdown blocks another rental until the exact owned
  resource is reconciled. Lium has a verified provider-side removal deadline;
  a local watchdog also operates while the helper is running.
- Only existing selected images and audio are used. No image, voice or director
  generation is called. No duplicate Torch/CUDA environment is installed.
- Media travels between Google and the worker. Final MP4s are not saved on the
  laptop. Hash verification streams bounded chunks after the GPU stops.
- New videos and parts use an immutable `cloud-renders/<nonce>/` namespace. The
  previous selected video and manual edits are preserved. Cloud manifest
  publication uses a generation condition and rejects concurrent source changes.

## Remaining verification

The completed short cloud pilot used 22.3 seconds for startup (including 6.45 seconds
of setup), 24.74 seconds for sources, and 24.47 seconds for rendering and uploads.
Uploads account for 9.61 seconds inside that last figure; do not add them twice.
Complete controller time was 94.16 seconds. These are one cold short-run sample,
not a 12-hour forecast. The tested template and verification record must be retained.
Vast account access and its private key still need confirmation in the actual
signed-in session. Long exports need duration-scaled deadlines, scratch sizing,
checkpoint/pruning and full-length transfer/render validation before activation.
