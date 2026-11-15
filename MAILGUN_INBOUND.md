# Mailgun inbound email (development deployment)

InvisMail can receive mail through either its built-in SMTP adapter or a Mailgun inbound Route. This guide describes the Mailgun path for a development machine behind carrier-grade NAT.

## Architecture

1. DNS MX records deliver mail for the domain to Mailgun.
2. A Mailgun Route forwards the original MIME message to the FastAPI webhook.
3. A Cloudflare Quick Tunnel exposes the laptop's local API over HTTPS.
4. The webhook verifies Mailgun's HTTP webhook signature, validates the active recipient, writes the original MIME message to the spool directory, and publishes the existing `email:incoming` Redis Stream event.
5. The existing email worker parses and stores the message.

The webhook is `POST /api/v1/inbound/mailgun/raw-mime`. The path ends in `mime` intentionally: Mailgun sends an `application/x-www-form-urlencoded` request with the original message in `body-mime`.

## DNS

For a Mailgun EU domain, Mailgun currently displays its inbound MX records as:

- `mxa.eu.mailgun.org`, priority 10
- `mxb.eu.mailgun.org`, priority 10

Copy the exact SPF and DKIM records from your Mailgun DNS Records page. DNSExit should have only the intended Mailgun MX targets for this domain. Do not replace unrelated A or CNAME records used for web hosting.

## Configure the signing key

In Mailgun, open **Settings → API Security → HTTP webhook signing key**. This is the account-level webhook signing key, not the Mailgun API key or a domain sending key.

Add the key to the root `.env` file. Do not commit or share it:

```env
DEFAULT_DOMAIN=testingtemp.publicvm.com
MAILGUN_WEBHOOK_SIGNING_KEY=replace-with-your-http-webhook-signing-key
MAILGUN_WEBHOOK_MAX_AGE_SECONDS=86400
MAILGUN_WEBHOOK_DEDUP_SECONDS=604800
```

The Docker Compose API service passes these values into FastAPI. Keep the signing key private.

## Start the application and tunnel

Start the existing stack:

```powershell
docker compose up -d --build
curl.exe http://localhost:8000/health/ready
```

Start a Quick Tunnel in a separate PowerShell window:

```powershell
cloudflared tunnel --url http://localhost:8000
```

Keep that terminal open. Copy the generated `https://...trycloudflare.com` hostname. Quick Tunnel hostnames are temporary and change when the tunnel is restarted.

Rebuild/restart the API after setting the signing key:

```powershell
docker compose up -d --build api
```

## Create the Mailgun Route

In Mailgun, open **Receiving → Routes** and add a route with:

**Expression**

```text
match_recipient(".*@testingtemp\\.publicvm\\.com")
```

**Action**

```text
forward("https://YOUR-QUICK-TUNNEL.trycloudflare.com/api/v1/inbound/mailgun/raw-mime")
```

Replace the hostname with the current Quick Tunnel URL. The destination must end in `mime` so Mailgun sends the raw MIME payload. Do not add a trailing path segment after `raw-mime`.

Do not configure the route until the webhook signing key is in `.env`, the API has been rebuilt, and the tunnel is running.

## End-to-end test

1. Open the tunnel URL with `/health/ready` or check the API locally at `http://localhost:8000/health/ready`.
2. Use `POST /api/v1/inboxes` to create an active address under `testingtemp.publicvm.com`.
3. Send a message from an independent email account to that generated address.
4. Watch logs:

```powershell
docker compose logs -f api worker
```

5. Confirm the message appears through the existing authenticated message-list API.

The webhook returns HTTP 200 after the Redis event is accepted. Invalid signatures, invalid MIME payloads, and unknown/expired recipients return HTTP 406 (permanent rejection). Temporary database, Redis, or processing failures return HTTP 503 so Mailgun can retry.

## Operational limitations

- Cloudflare Quick Tunnels are for development and testing, not production availability. Keep the tunnel process running; its hostname changes when restarted.
- The laptop, Docker stack, and internet connection must remain online to process inbound messages. Mailgun retries non-200/non-406 Route POST failures for a limited period.
- The standard API port remains bound to localhost; do not expose PostgreSQL or Redis publicly.
- Mailgun is the public SMTP receiving system in this mode. The laptop does not receive direct internet SMTP connections.
- The existing direct SMTP adapter remains available for local testing or a future host with public TCP/25 access.
