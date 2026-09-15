# Cloudflare publishing storage

The dedicated R2 bucket is `vaarattu-shorts-publishing`, in Eastern Europe with
Standard storage. Public files are served through `https://publishing.vaarattu.tv`.
The bucket is for final clips intended for publication, not source recordings.

The **Publishing** tab accepts the S3 access key ID and secret access key from
Cloudflare's R2 token screen. The token must have **Object Read & Write** on this
bucket only. Credentials are saved as `R2_ACCESS_KEY_ID` and `R2_SECRET_ACCESS_KEY`
in the project's ignored `.env` file. Other settings in that file are preserved.
Do not paste credentials into chat, documentation, or tracked configuration.

Publishing offers an explicit upload for each revision marked Ready for posting.
Confirming a Buffer submission also uploads the file if needed. Uploads verify the
local SHA-256, preserve the MP4 bytes, and use a stable HTTPS URL. The public URL
must respond with the correct video content type and size before upload succeeds.
Upload records remain in `workdir/r2-uploads.json`, including when public URL
verification fails. Retrying an uploaded revision checks its existing URL.

This app refuses uploads exceeding 8 GB total bucket storage or 2 GB for a single
file. This is an app guard, not an account-wide billing cap: Cloudflare bills usage
beyond its account's free storage/request allowances, including other apps and
buckets. Public requests count towards that allowance. No age-based deletion rule
is enabled because it could delete videos before delayed or retried publication.

The standalone storage upload button does not create Buffer posts. Use the
[Buffer publishing workflow](buffer-publishing.md) to publish now or submit scheduled
posts. Keep cloud copies while posts are scheduled or need attention. Publishing
offers explicit removal once every tracked destination has confirmed publication
for at least 48 hours. It checks other tracked clips using the same file, preserves
local final videos, and never removes cloud copies automatically.

Backend routes activate on the next normal app restart. No worker or processing
run needs to be started to configure storage.

References: [R2 authentication](https://developers.cloudflare.com/r2/api/tokens/),
[R2 pricing](https://developers.cloudflare.com/r2/pricing/),
[Buffer media hosting](https://developers.buffer.com/guides/hosting-media.html).
