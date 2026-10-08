# Private Studio cloud publisher

## Google storage compatibility

The publisher can use the same private Google bucket for both videos and its saved upload/OAuth state. Configure `STUDIO_STORAGE_PROVIDER=gcs`, `STUDIO_GCS_BUCKET` and the encrypted Worker secret `STUDIO_GCS_SERVICE_ACCOUNT` (the approved bucket-only JSON identity). Retain existing YouTube OAuth client secrets and the private publisher credential; no new YouTube scope is requested. The R2 binding is ignored in GCS mode, and invalid Google configuration never falls back to R2. Until live migration and provider verification finish, keep production on the existing R2 configuration.

`google-bucket.mjs` uses scoped service-account OAuth, generation-pinned reads, exact range checks and generation-conditional upload leases. Video chunks stream directly from cloud storage to YouTube. `/status` reports the real configured storage provider; the helper and Files UI block an archive/publisher mismatch. No credential is returned in status. All outbound requests within one control operation share a100second deadline, below the helper timeout and saved120second lease.

Migration preserves source objects and existing Google records. `cloud_migration.py` streams immutable assets without creating laptop download files, validates canonical checksums and project identities, preserves history/upload records, and publishes manifests only after their assets are verified. Existing target objects are hashed rather than trusted from custom metadata. Restored-project and live publishing checks are required before switching the active archive. This is storage/publisher compatibility, not a completed all-cloud narration/rendering deployment.

This Worker streams 16 MiB ranges directly from the private R2 bucket into a YouTube resumable-upload session. The helper sends only JSON control messages; video bytes do not pass through the laptop. Progress is saved in R2 and every resume queries YouTube for the actual committed byte offset. Concurrent requests are guarded by a conditional R2 lease. The original file is never deleted. Uploads default to private.

## Deployment prerequisites

1. Bind `STUDIO` to the existing private `questiontemplate-studio` bucket. Do not enable public R2 access.
2. Configure a Google OAuth **Web application** client with the deployed Worker's exact `/oauth/callback` URL. Enable YouTube Data API v3. The only requested user scope is `youtube.upload`.
3. Store `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and a random private `PUBLISHER_TOKEN` of at least 32 characters using Cloudflare Worker secrets. Never use frontend variables, Git, plaintext report output or command-line arguments for secret values.
4. Deploy `wrangler.jsonc` using the authorized Cloudflare account. The Worker uses no scheduled rental or GPU. Check the account's Workers plan and quota before a large transfer; do not silently subscribe to a paid plan.
5. Create the helper's private `.publisher-secrets.json` containing only `url` (the verified HTTPS workers.dev address) and `token`. Apply the same owner-only Windows ACL as `.studio-secrets.json` before writing it. This file is excluded from Git and project/cloud exports.
6. In Studio → Files, connect YouTube and approve the Google consent screen. Then choose an uploaded cloud video, title, description, privacy and audience setting. Clicking upload is an explicit publishing decision.

Account setup and Google channel consent are deployment prerequisites. The transfer implementation is tested with a simulated R2/YouTube service; deployment verification does not upload a real video. Browser refresh or a laptop outage stops control requests, but restarting and pressing Resume safely continues the saved session. An unattended cloud coordinator is needed to continue transfers when Studio/helper is fully offline. This Worker does not run Kokoro, FFmpeg or image generation; moving all production off the laptop is separate compute integration.

While an external OAuth app remains in Google's Testing mode, upload authorization expires after seven days and the channel must reconnect ([Google token expiration](https://developers.google.com/identity/protocols/oauth2#expiration)). Do not claim permanent connectivity or silently publish the OAuth app to production. R2 credentials also have their own configured expiration and must be renewed before that date.

## Tests

YouTube may keep uploads from new unaudited API projects private even when public visibility is requested ([API documentation](https://developers.google.com/youtube/v3/docs/videos/insert)). Completion reports YouTube's returned visibility, rather than assuming the request succeeded. Uploads exceeding [12 hours or 256 GB](https://support.google.com/youtube/answer/71673) are rejected before starting; choose the generated two-hour parts instead. Channel verification and any required API audit remain account prerequisites.

Run `node --test cloud-publisher/worker.test.mjs` from the app repository. Tests verify authentication, secret-free status, direct range transfer, acknowledged-byte recovery, invalid cloud sources/session hosts and OAuth state/PKCE. Keep real API keys and production uploads out of these tests.
