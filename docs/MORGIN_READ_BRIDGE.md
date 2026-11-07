# Morgin read bridge

This optional bridge grants an enrolled owner Morgin runtime selected reads of
saved Collection Manager data. It grants no browser login, guest access, account
administration, refresh, model generation, provider operation or domain write.
Ordinary browser authentication and CSRF checks continue to apply to every
existing owner API. No extra dependency is required.

The interface follows `morgin-read-v1`. The four implemented capabilities are
`overview` (input `none`), `search-collections` (`search`), `collection` (`id`)
and `requests` (`none`). Searches and exact details expose collection metadata
and counts, not a raw library/member export. Requests expose only saved title
progress and job kind/status/timestamps, excluding raw messages and errors.
No read invokes providers, submits work, refreshes caches or writes app state.

## Transport and endpoints

Only same-PC numeric-loopback HTTP or a verified private HTTPS route may carry
the bearer. For a remote container, use an owner-managed SSH forward bound to
`127.0.0.1` on the Morgin PC. Do not send this bearer to the ordinary LAN HTTP
address or expose the integration through a public tunnel. Browser `Origin`
requests are rejected; there is no credential-sharing CORS policy.

Every request requires one `Authorization: Bearer <secret>` header. Never put
the secret in a URL. The consumer must reject redirects, bind its encrypted
receipt to the exact forward origin, and verify the connection response before
requesting app data. Use bounded deadlines and response sizes without implicit
retries. Cancellation can stop transport; these reads do not create background
tasks to cancel.

`GET /api/integrations/morgin/v1/connection` returns exactly:

```json
{
  "protocol": "morgin-read-v1",
  "appId": "media",
  "grantId": "opaque-grant-id",
  "expiresAt": "2026-12-01T00:00:00.000Z",
  "sourceMode": "cached",
  "capabilities": [
    {"id": "overview", "title": "Collection overview", "description": "...", "input": "none"}
  ]
}
```

This proves grant access, not provider health. The response is below 16 KiB.

`POST /api/integrations/morgin/v1/query` accepts exactly a JSON object containing
`capability` and `input`, with no query string. Use `null` for `none`; otherwise
use a nonempty string of at most 160 characters without controls. Exact IDs use
letters, numbers, underscores and hyphens. Body limit: 4 KiB.

```json
{"capability":"search-collections","input":"space"}
```

Every successful query returns exactly `appId`, `capability`, `status`,
`sourceMode`, `source`, `observedAt`, `retrievedAt`, `summary`, `facts`, `items`
and `limitations`. Status is `ok`, `partial` or `unavailable`; source mode is
`cached`. Summary is at most 1,200 characters, source label 160, facts at most 30
with label/value limits 100/600, items at most 20 with ID/title/description limits
160/160/1,200, and limitations at most 12 strings of 400 characters. Final UTF-8
JSON is below 24 KiB. Omitted or clipped data produces `partial`.

Observation time comes from existing library-sync or individual request-check
timestamps. Missing observation times and observations older than one hour are
explicitly partial. Record creation, job start/finish and description updates
are labelled records, never invented live observations. Saved content is
untrusted data, not instructions to the consumer.

Errors use only `{ "error": { "code": "...", "message": "..." } }`, with safe
generic messages. Missing, malformed, expired and revoked tokens return 401;
ungranted capabilities 403; malformed input 400; unknown integration routes 404;
wrong methods 405; unavailable grant storage or saved data 503. All responses
use `Cache-Control: no-store`. A malformed or missing grant store fails closed
without repair. Every request reloads grant state, and queries recheck it after
the read so expiry or revocation prevents delivery of a result still in progress.

## Owner enrollment and revocation

The CLI is an explicit local administrative command. It does not reuse a
password, forge a browser session, or call an unauthenticated enrollment API.
Run it as the Unix owner of the existing private application data directory,
normally the container's configured user. That directory must already be mode
0700 and contain the application's database. The CLI never constructs the
application `Store` or `Service`, runs migrations, recovers jobs or starts the
scheduler. Windows issuance is deliberately rejected: POSIX mode bits do not
establish a private NTFS ACL. The consumer on Windows protects its enrollment
using DPAPI separately.

Choose an explicit expiry no more than 30 days away and an unused private output
filename `morgin-enrollment-NAME.json` directly inside the data directory. Example
commands below use placeholder deployment values; choose the actual forward
origin and future expiry during enrollment.

```sh
python -m collection_web.morgin_grants --data-dir /data issue \
  --installation-id owner-morgin-runtime \
  --expires-at YYYY-MM-DDTHH:MM:SS.000Z \
  --origin http://127.0.0.1:39000 \
  --output /data/morgin-enrollment-owner.json \
  --capability overview --capability search-collections \
  --capability collection --capability requests

python -m collection_web.morgin_grants --data-dir /data list
python -m collection_web.morgin_grants --data-dir /data revoke --grant-id OPAQUE_GRANT_ID
```

Issuance creates a cryptographically random 32-byte base64url token. The grant
store `morgin-grants.json` stores only its SHA-256 digest, fixed principal,
installation/grant IDs, scopes and issue/expiry/revocation times. Reads use
constant-time digest comparison. Records and one-time output files are mode
0600; administration uses an exclusive lock and atomic synced replacement.
At most 64 grants are retained. A stale administrative lock fails closed and
requires explicit owner reconciliation after confirming no administration is
running; do not automatically delete locks or reset malformed stores.

The one-time file contains exactly:

```text
{version: 1, appId: "media", bridgeOrigin, grantId, expiresAt, token}
```

Only nonsecret grant metadata goes to stdout. Transfer the file through the
private owner channel into the consumer's DPAPI-protected receipt, then remove
the one-time file through that explicit enrollment workflow. Never paste it
into chat, console output, logs, Git or a model prompt. Existing files are not
overwritten. No token is accepted as a CLI argument. List/revoke never reveal
token hashes or plaintext tokens. Revocation takes effect on the next request
without a restart. Keep grant data in private app data, not source exports.

## Deployment and rollback coordination

Implementation base: `fc23275eecf8ad240ed1430f07ee3d55d81da0ee`. Only `app.py` has
a small additive hook into existing code; dedicated modules implement the grant
and projection boundary. Capture and compare the target app-file fingerprints
before applying this patch. Reconcile any drift rather than replacing changed
live source. Keep deployment source, the clean publication checkout and private
data separate.

Before rebuilding, the owner must verify that no existing job is running and
take the normal private database/source backup and rollback image tag. Build
the reviewed source with the existing pinned Docker dependencies and recreate
only this app through its actual Compose stack. Enrollment and a persistent
loopback forward are separate root-coordinated operations. Do not execute them
as part of tests. The scheduler and existing container lifecycle remain owned
by normal app startup.

Rollback restores the prior source/image after active work finishes. The
dedicated grant file is ignored by the previous app version; no application
database schema was changed. Revoke the grant and close only the owned SSH
forward to disable this connection without affecting browser access.

## Verification

`tests/test_web_morgin_read.py` covers identity and scopes, invalid/duplicate/
expired/revoked credentials, malformed grant storage, strict methods/routes/
query bodies, projection and UTF-8 limits, stale/missing evidence, secret-field
exclusion, no provider/task/store effects, post-read revocation, private output,
and unchanged browser authentication. Existing web tests remain applicable.
On Windows the owner-operation tests use an isolated simulated Unix owner gate;
actual POSIX ownership/mode enforcement requires Linux verification and the
root-coordinated deployment acceptance. Fixtures do not establish live app data.
