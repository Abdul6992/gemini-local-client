# Gemini Creator Core local / Wi-Fi client

Use an existing, signed-in Gemini Creator Core desktop to generate images from
your own tool. No hosted relay or cloud storage is used. This package contains
only client code; it does not contain profiles, account credentials or API keys.

## Desktop setup

1. Open the desktop software and sign in.
2. Configure at least one signed-in Chrome profile in **Accounts**.
3. Open **API & Jobs**, enable the API and click **Apply Settings**.
4. Click **Create API Key** and copy the key. It is shown once and expires in
   90 days. Revoke it from the desktop if it is lost.
5. Default maximum open profiles is two. Change it in **API & Jobs**.
   Warm-up opens the next profiles after sign-in; it does not consume their turn.

For the same PC the default API address is `http://127.0.0.1:8765`.
With LAN enabled, the API uses HTTPS (including localhost). Copy an HTTPS address
shown by the desktop, export its public LAN certificate and give that certificate
to your client. Never copy `lan-private-key.pem`.

For another device on the same network, enable LAN and use **LAN Firewall Setup**
on the generation PC. Windows requests administrator approval for a rule limited
to **Private** networks and **LocalSubnet**. The client may also need to be on
the same non-isolated Wi-Fi network. No router port forwarding is required.
Do not expose the listener to the public internet.

## Install

After cloning this client repository, run:

```powershell
python -m pip install .
$env:GEMINI_LOCAL_API_KEY = "YOUR_KEY"
```

This client requires Python 3.10+ and has no runtime package dependencies.

## Python

```python
from gemini_local_client import Client

client = Client()  # reads GEMINI_LOCAL_API_KEY
job = client.submit(
    [
        {"id": "scene-001", "text": "A mountain at sunrise"},
        {"id": "scene-002", "text": "A city at night"},
    ],
    ratio="16:9",
    idempotency_key="my-project-render-001",
)
print("Save this job ID:", job["job_id"])

for image in client.wait_and_download(job["job_id"]):
    print(image["index"], image["prompt_id"], image["path"])

print(client.status(job["job_id"]))
```

LAN client:

```python
client = Client(
    base_url="https://192.168.1.20:8765",
    certificate="gemini-lan-certificate.pem",
)
```

Do not disable TLS verification. A changed LAN IP, certificate renewal or reinstall
may require exporting the public certificate again. Use the desktop's current URL.

## CLI

Create `prompts.txt` with one prompt per line:

```powershell
gemini-local --prompts prompts.txt --ratio 16:9
gemini-local --resume JOB_ID --output "D:\My Results"
gemini-local --url https://192.168.1.20:8765 --certificate gemini-lan-certificate.pem --prompts prompts.txt
```

The CLI prints the job ID immediately, then one JSON record per downloaded image.
Exit code 2 means the final job was partial, failed, cancelled or needs review.

## Files and progress

- Prompt indices are **1-based**. Results may finish out of order.
- Results are saved on the generation PC in a unique directory per job.
- Open profile contexts are retained between jobs. A profile is refreshed after
  the requesting client confirms every completed image from its batch. The SDK
  sends this acknowledgement after SHA-256 verification.
- The API reports `completed_in_cycle` and `cooldown_until` for open profiles.
  At 400 completed images, a profile rests for three hours before its count resets.
- A same-PC client can reuse the existing local file without downloading a duplicate.
- A LAN client defaults to `~/Downloads/GeminiCreatorCore/JOB_ID/`.
- Each completed prompt includes an authenticated `download_url` served directly
  by the desktop API over the local network. The generator blob is captured once
  into the desktop's saved file; the API streams that file without uploading it
  to GitHub, a cloud service, or another host. A browser `blob:` URL itself only
  works inside its owning browser session and cannot be used on another device.
- Pass `directory=` to `wait_and_download` to select another destination.
- Re-run with the same job ID after a disconnect. Files already matching their
  SHA-256 hash are skipped; partial transfers use HTTP Range.
- Custom clients should `POST /v1/jobs/{job_id}/ack` with JSON
  `{"indices":[1,2]}` after fully verifying downloads. This releases and
  refreshes the profile page. A 15-minute safety timeout refreshes the page if
  a client disconnects without acknowledging.
- Keep the original job ID. Submitting a new job ID intentionally generates again.
- Use a stable `Idempotency-Key` when retrying a submission whose response was lost.
  Reusing that key with changed prompts/ratio returns HTTP 409.
- Downloading finished images remains available while the signed-in desktop API
  is running. The client disconnecting does not cancel generation.
- API requests cannot select arbitrary host filesystem paths.

## HTTP API

Send `Authorization: Bearer YOUR_KEY` on every request.
POST bodies use `Content-Type: application/json`.

| Method | Path | Purpose |
|---|---|---|
| GET | /v1/devices | Device identity and worker states |
| POST | /v1/jobs | Submit prompts and ratio; returns 202 |
| GET | /v1/jobs | Latest 50 jobs belonging to this account |
| GET | /v1/jobs/{job_id} | Per-prompt status and completed indices |
| GET | /v1/jobs/{job_id}/events | SSE; reconnect with Last-Event-ID |
| GET | /v1/jobs/{job_id}/images/{index} | Authenticated image, supports Range |
| POST | /v1/jobs/{job_id}/ack | Confirm checksum-verified image indices |
| POST | /v1/jobs/{job_id}/cancel | Stop unsubmitted work; keep completed files |
| POST | /v1/jobs/{job_id}/retry | New job for failed/cancelled selected prompts |
| GET | /v1/openapi.json | Authenticated API schema |

Submit body:

```json
{
  "ratio": "16:9",
  "prompts": [
    {"id": "scene-001", "text": "A mountain at sunrise"},
    {"id": "scene-002", "text": "A city at night"}
  ]
}
```

Ratios are `1:1`, `16:9`, `9:16`. One prompt produces one image.
Prompts must be single lines, at most 10,000 characters each.
Request body limit: 2 MiB. Maximum 10,000 prompts per job, further limited by
configured profiles (400 per profile per job) and the account's daily quota.
At most 20 jobs can be queued/running. API burst limit is 300 requests/minute per
source address; on 429 wait before retrying.

Retry body:

```json
{"indices": [2, 5], "confirm_uncertain": false}
```

Retry returns a **new** job ID. Prompt IDs are preserved; child indices start at
one. The response includes `original_indices` to map them to the original job.
Successful results cannot be retried through this endpoint.

An app/browser crash after submission may leave a prompt in `needs_review`.
The generator may already have processed it. These prompts are never automatically
resubmitted. Inspect before explicitly retrying with `confirm_uncertain: true`.
Their daily quota reservations are conservative because their outcome is unknown.

## Browser / JavaScript integrations

Backend tools can call the HTTP API directly. Browser applications must have their
exact origin (e.g. `http://localhost:3000`) added to **Allowed browser origins**
in the desktop. Wildcards are not accepted. Every request still requires the API key.
Do not embed your key in publicly hosted frontend source. Browsers control their
own download-folder permissions.

For Node.js, use the included `examples/node-client.mjs`. For LAN HTTPS start Node
with the exported certificate in `NODE_EXTRA_CA_CERTS`.

## Errors and limits

401: invalid/expired/revoked key. 403: browser origin not allowed.
409: quota/capacity/idempotency conflict or confirmation needed.
413: request too large. 422: validation error. 429: rate/queue limit.
503: desktop signed out/unavailable. 507: output disk low.

The software, browser accounts and generator page must be available. There is no
internet relay, no automatic generation when the PC is off, and no cloud copy of
the images. A changed Gemini page/login may need an adapter update or re-login.
Mocked integration tests do not certify compatibility with a future live page.

## Publishing this repository

Publish the **client directory only** (choose your repository name and license).
Never publish the parent desktop directory with browser profiles, auth sessions,
api_data, private certificates or generated images. GitHub publication is separate
from running this client locally.
