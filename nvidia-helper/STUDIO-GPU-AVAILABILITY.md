# Automatic GPU waiting

The production rental controller supports a prepared worker pool through helper configuration:

- `runpodPreparedPodIds`: one to twenty existing worker IDs, each independently prepared with the required model, references workflow and SSH access. They may be in different regions. A network volume does not automatically move between regions.
- `runpodRequiredGPUType`: exact provider GPU type ID, for example `NVIDIA GeForce RTX 5090`. Verify the catalog ID before configuring it.
- `runpodAvailabilityWaitSeconds`: bounded wait from 0 to 86400 seconds.
- `runpodMaxHourlyUSD`: maximum acceptable hourly rate, in addition to the project's total rental budget.

When opted in, Studio reads only those workers, excludes running workers, wrong GPU types and excessive prices, and selects the cheapest eligible stopped worker. The queue reports waiting and supports cancellation. Waiting does not start a rental. Once started, existing ownership, budget watchdog, private connection, model health checks and end-of-queue shutdown apply. The selected worker is saved for interruption recovery.

A provider advertising a start action is eligibility, not guaranteed physical capacity. If starting fails or its response is uncertain, Studio stops/reconciles that exact worker. It does not purchase another worker while ownership is unresolved. A GPU mismatch after startup stops the rental before image generation.

This feature does not create workers, replicate model volumes or reserve capacity. Existing configuration is unchanged until a verified prepared pool is supplied. It cannot promise a 5090 will always be in stock. Never silently configure another GPU as a fallback.
