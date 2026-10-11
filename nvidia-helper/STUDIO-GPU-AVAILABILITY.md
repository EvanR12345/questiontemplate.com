# Automatic GPU waiting

The production rental controller supports a prepared worker pool through helper configuration:

- `runpodPreparedPodIds`: one to twenty existing worker IDs, each independently prepared with the required model, references workflow and SSH access. They may be in different regions. A network volume does not automatically move between regions.
- `runpodRequiredGPUType`: exact provider GPU type ID, for example `NVIDIA GeForce RTX 5090`. Verify the catalog ID before configuring it.
- `runpodAvailabilityWaitSeconds`: bounded wait from 0 to 86400 seconds.
- `runpodMaxHourlyUSD`: maximum acceptable hourly rate, in addition to the project's total rental budget.

When opted in, Studio reads only those workers, excludes running workers, wrong GPU types and excessive prices, and selects the cheapest eligible stopped worker. The queue reports waiting and supports cancellation. Waiting does not start a rental. Once started, existing ownership, budget watchdog, private connection, model health checks and end-of-queue shutdown apply. The selected worker is saved for interruption recovery.

A provider advertising a start action is eligibility, not guaranteed physical capacity. If starting fails or its response is uncertain, Studio stops/reconciles that exact worker. It does not purchase another worker while ownership is unresolved. A GPU mismatch after startup stops the rental before image generation.

This feature does not create workers, replicate model volumes or reserve capacity. Existing configuration is unchanged until a verified prepared pool is supplied. It cannot promise a 5090 will always be in stock. Never silently configure another GPU as a fallback.

## Regional stock checks and Studio controls

Studio Settings → Advanced AI settings → Automatic GPU startup / shutdown now exposes required GPU, wait minutes, hourly price ceiling and a prepared worker pool. Regional stock checking is optional and reads the configured CLI's live catalogue. Unknown or absent stock in a worker's region excludes that worker before purchase. Catalogue stock is regional, not proof that a stopped pod's specific physical host is free. Startup and model health are still verified, and failed/uncertain ownership must be reconciled before another purchase.

## Temporary worker model recovery

`runpodBootstrapKleinPodIds` explicitly opts selected prepared workers into restoring
the three pinned public FLUX.2 Klein 4B weights after a restart. These IDs must
belong to the selected worker pool. The Advanced settings checkbox applies this
option to the selected pool, or its primary worker when no pool is specified.

Restoration runs only after budget-gated startup, under the same owned rental
watchdog. Downloads go directly to the remote worker through fixed public model
URLs. Files must match their pinned byte counts and SHA-256 fingerprints before
being renamed into place. Existing valid files are reused; hashes stream in small
chunks. Neither credentials nor story/character data are sent during restoration.
The whole startup remains bounded to 300 seconds and the rental spending window.
Cancellation or failure preserves saved work and closes the rental.

Temporary disks lose these weights when stopped. A new startup therefore may
need approximately 12.45 GB of remote downloads, followed by cold model loading.
Those seconds and their rental cost must be included in estimates. This option
does not create a persistent volume, reserve capacity or make the worker warm
while stopped. Model/node health is checked for the selected FLUX variant rather
than unrelated legacy models in the workflow catalogue.
