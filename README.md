# InvisMail

**A self-hosted disposable email backend built with FastAPI, PostgreSQL, Redis Streams, and Python SMTP ingestion.**

InvisMail lets a client create a temporary inbox, receive messages through a local SMTP service, and retrieve them through an authenticated REST API. Email ingestion and message processing are decoupled using Redis Streams. PostgreSQL stores inbox and message metadata, while the local filesystem stores raw email spool files and attachment contents.

> **Project status:** Backend implementation for local development and testing. This repository contains the API, SMTP adapter, optional Mailgun inbound adapter, background workers, database migrations, tests, and Docker Compose configuration for the whole stack. It does not include a frontend application. Direct public-domain SMTP deployment remains operator-managed.

---

## Contents

- [Overview](#overview)
- [Features](#features)
- [Architecture](#architecture)
- [Technology stack](#technology-stack)
- [Repository structure](#repository-structure)
- [Email processing lifecycle](#email-processing-lifecycle)
- [Getting started](#getting-started)
- [Environment variables](#environment-variables)
- [API reference](#api-reference)
- [Sending test emails](#sending-test-emails)
- [Mailgun inbound integration](#mailgun-inbound-integration)
- [Database and storage](#database-and-storage)
- [Testing](#testing)
- [Security considerations](#security-considerations)
- [Current limitations](#current-limitations)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [License](#license)

## Overview

Disposable email services provide short-lived email addresses for situations where a permanent personal address is not required. InvisMail explores the backend engineering involved in building such a service.

The project focuses on:

- Generating unique, expiring inboxes.
- Authenticating inbox access without storing raw access tokens.
- Receiving email through an SMTP adapter.
- Buffering incoming messages for asynchronous processing.
- Parsing MIME messages and extracting attachments.
- Persisting messages and metadata.
- Notifying connected clients when new messages arrive.
- Cleaning up expired inboxes, orphaned attachments, and stale spool files.

The default `invismail.test` domain is intended for local testing. It is not a publicly deliverable email domain.

## Features

### Temporary inboxes

- Generate random email addresses for new inboxes.
- Configure inbox lifetime through a time-to-live (TTL) value between 5 minutes and 24 hours.
- Generate high-entropy bearer access tokens.
- Store the SHA-256 hash of each access token instead of the raw token.
- Reject access to inactive or expired inboxes.

### Email ingestion and processing

- Accept email through an asynchronous SMTP handler built with `aiosmtpd`.
- Validate recipients against active, unexpired inboxes before accepting a message.
- Enforce a configurable maximum raw email size and reject oversized messages with `552`.
- Write raw `.eml` messages to disk before publishing queue events.
- Publish a separate Redis Stream event for each accepted recipient in a single transaction.
- Process messages asynchronously in a background worker.
- Parse plain-text and HTML MIME bodies and extract attachments.
- Enforce limits on MIME part count, nesting depth, attachment count, and total attachment bytes.
- Strip `<script>`, `<style>`, `<iframe>`, `<object>`, `<embed>`, and `<svg>` elements, inline event handlers, and `javascript:` URLs from HTML bodies.

### Message management

- List messages with page-based pagination.
- Retrieve an individual message and mark it as read.
- Delete messages and their related attachment files.
- Download attachments, with paths constrained to the attachment storage directory.
- Scope every protected message and attachment operation to the authenticated inbox.

### Attachments and storage

- Store attachment bytes on the local filesystem.
- Generate storage identifiers instead of using sender-provided filenames as filesystem paths.
- Enforce a configurable per-attachment size limit.
- Retain the original filename, content type, and size as metadata.
- Serve downloads as `application/octet-stream` attachments with `X-Content-Type-Options: nosniff`.

### Real-time notifications

- Publish new-message notifications through Redis Pub/Sub.
- Expose an authenticated Server-Sent Events (SSE) endpoint for inbox updates.

### Background workers

- Consume incoming email events through a Redis Streams consumer group.
- Recover abandoned pending events using `XAUTOCLAIM`.
- Track retry attempts and apply retry delays (60 seconds for the first two failures, then 300 seconds).
- Move permanently failing events to a dead-letter stream with an atomic Lua script.
- Use a unique source event ID to protect against duplicate email records.
- Periodically remove expired inboxes, orphaned attachment files, and stale spool files while protecting files still referenced by the queue.

### Health checks and rate limiting

- Liveness and readiness endpoints, with readiness probing PostgreSQL and Redis.
- Per-IP rate limits on inbox creation, message listing, message retrieval, and attachment download.

## Architecture

```mermaid
flowchart LR
    Sender["Test email sender"] --> SMTP["SMTP adapter<br/>aiosmtpd"]
    SMTP --> Spool["Raw email spool<br/>.eml files"]
    SMTP --> Stream[("Redis Stream<br/>email:incoming")]
    Stream --> Worker["Email worker"]
    Worker --> Parser["MIME parser"]
    Worker --> DB[("PostgreSQL")]
    Worker --> Files["Attachment storage"]
    Worker --> PubSub["Redis Pub/Sub"]
    PubSub --> SSE["SSE endpoint"]
    Client["API client"] <--> API["FastAPI"]
    API <--> DB
    API --> Files
    API --> SSE
    Cleanup["Cleanup worker"] --> DB
    Cleanup --> Files
    Cleanup --> Spool
```

### Component responsibilities

| Component | Responsibility |
|---|---|
| FastAPI | Inbox creation, authentication, message and attachment APIs, health checks, and SSE endpoint |
| SMTP adapter | Validates recipients, applies the raw-message size limit, writes spool files, and publishes queue events |
| Redis Streams | Buffers incoming email events for asynchronous processing |
| Email worker | Recovers and processes queued events, parses MIME content, and persists messages |
| PostgreSQL | Stores inboxes, emails, and attachment metadata |
| Filesystem | Stores raw email spool files and extracted attachment bytes |
| Redis Pub/Sub | Sends transient new-message notifications to connected clients |
| Cleanup worker | Removes expired inbox records, orphaned spool files, and unreferenced attachment files |

PostgreSQL is the persistent source of truth for inbox and message data. Redis Streams supports asynchronous processing; Redis Pub/Sub is used for live notifications and is not a durable message history.

## Technology stack

| Area | Technology |
|---|---|
| Language | Python 3.12 |
| API | FastAPI, Uvicorn |
| SMTP | aiosmtpd |
| Database | PostgreSQL 17 |
| ORM | SQLAlchemy (async), asyncpg |
| Migrations | Alembic |
| Queue and notifications | Redis Streams, Redis Pub/Sub |
| Redis client | redis-py (async) |
| Email parsing | Python standard-library `email` package |
| Validation | Pydantic, pydantic-settings |
| Testing | pytest, pytest-asyncio, httpx |
| Local infrastructure | Docker Compose |

## Repository structure

```text
invismail/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── events.py
│   │   │   ├── health.py
│   │   │   ├── inboxes.py
│   │   │   └── messages.py
│   │   ├── core/
│   │   │   ├── auth.py
│   │   │   ├── database.py
│   │   │   ├── logging.py
│   │   │   ├── rate_limit.py
│   │   │   ├── redis.py
│   │   │   └── security.py
│   │   ├── models/
│   │   │   ├── attachment.py
│   │   │   ├── email.py
│   │   │   └── inbox.py
│   │   ├── schemas/
│   │   │   ├── email.py
│   │   │   └── inbox.py
│   │   ├── services/
│   │   │   ├── email_parser.py
│   │   │   ├── email_processor.py
│   │   │   └── storage_service.py
│   │   ├── smtp/
│   │   │   └── server.py
│   │   ├── workers/
│   │   │   ├── cleanup_worker.py
│   │   │   └── email_worker.py
│   │   └── main.py
│   ├── migrations/
│   │   └── versions/
│   ├── tests/
│   ├── alembic.ini
│   └── requirements.txt
├── storage/
│   ├── attachments/
│   └── spool/
├── tests/
│   ├── k6_smoke.js
│   ├── send_test_email.py
│   ├── send_test_email_with_attachment.py
│   ├── test.pdf
│   └── test.txt
├── docker-compose.yml
├── Dockerfile
└── README.md
```

## Email processing lifecycle

1. **Create an inbox.** The API generates a random address and access token. PostgreSQL stores the token hash and expiry time; the raw token is returned to the client at creation.
2. **Receive an email.** The SMTP adapter verifies that each recipient maps to an active, unexpired inbox and rejects messages that exceed the configured size limit.
3. **Spool the message.** The raw message is written to a uniquely named `.eml` file. The adapter publishes an event for each recipient to the `email:incoming` Redis Stream.
4. **Process asynchronously.** The email worker consumes events, checks for duplicate processing, reads the raw message, parses MIME content, and verifies the destination inbox is still active.
5. **Persist the message.** Email data and attachment metadata are committed to PostgreSQL. Attachment bytes are stored separately on disk, then the spool file is removed.
6. **Notify clients.** After the database commit, the worker publishes the message ID to the inbox-specific Redis Pub/Sub channel. The API exposes this channel through SSE.
7. **Clean up.** The cleanup worker runs every 60 seconds: it deletes expired inboxes (and their cascaded email/attachment records plus attachment files), removes attachment files no longer referenced by the database, and removes orphaned spool files.

The worker follows an at-least-once processing model. Pending-entry recovery, retry tracking, a dead-letter stream, and a database uniqueness constraint on `source_event_id` make processing resilient to failures and duplicate deliveries.

## Getting started

### Prerequisites

- Docker Engine with the Docker Compose plugin (for the containerized setup).
- Python 3.12 and a virtual environment (for the host-local setup).
- Git.

### 1. Clone the repository

```bash
git clone https://github.com/S-Sanjai/invismail.git
cd invismail
```

### 2. Create the environment file

Create a root-level `.env` file. Do not commit it.

```env
# PostgreSQL
DB_PASSWORD=replace-with-a-long-local-password
DATABASE_URL=postgresql+asyncpg://invismail:replace-with-a-long-local-password@127.0.0.1:5432/invismail

# Redis
REDIS_URL=redis://127.0.0.1:6379/0

# Local testing domain only
DEFAULT_DOMAIN=invismail.test

# Size limits
MAX_EMAIL_SIZE_BYTES=10485760
MAX_ATTACHMENT_SIZE_BYTES=5242880
```

Use the same password in `DB_PASSWORD` and `DATABASE_URL`. Compose reads only `DB_PASSWORD` and the optional overrides from `.env`, and builds the internal `DATABASE_URL` itself using the `postgres` service hostname.

### Option A: run the whole stack with Docker Compose

`docker-compose.yml` defines PostgreSQL, Redis, the API, the SMTP adapter, the email worker, and the cleanup worker. PostgreSQL, Redis, the API (`8000`), and SMTP (`2525`) are published on `127.0.0.1` only.

```bash
docker compose up -d --build
docker compose ps
```

The containers do not apply migrations on startup. Alembic reads `sqlalchemy.url` from `backend/alembic.ini` rather than from `DATABASE_URL`, so set it to your database URL first — use host `postgres` when running inside Compose:

```ini
sqlalchemy.url = postgresql+asyncpg://invismail:<your-password>@postgres:5432/invismail
```

Then run the migrations once:

```bash
docker compose run --rm api alembic upgrade head
```

Check the API and its dependencies:

```bash
curl http://127.0.0.1:8000/health/ready
docker compose exec postgres pg_isready -U invismail
docker compose exec redis redis-cli ping
```

The storage directories are bind-mounted from `./storage`, so spool files and attachments are visible from the host.

### Option B: run the Python processes on the host

#### 1. Start PostgreSQL and Redis

```bash
docker compose up -d postgres redis
docker compose exec postgres pg_isready -U invismail
docker compose exec redis redis-cli ping
```

Redis should respond with `PONG`.

#### 2. Create a Python environment

Run these commands from the repository root.

**Windows PowerShell**

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
```

#### 3. Export environment variables and enter the backend directory

The API, SMTP entry point, and Alembic load the root `.env` file themselves. The worker entry points read database and Redis settings from the process environment, so export the variables before starting them.

**Linux / macOS (Bash)**

```bash
set -a
source .env
set +a
cd backend
```

**Windows PowerShell**

```powershell
Get-Content .env | ForEach-Object {
    if ($_ -match '^\s*([^#=]+)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
}
Set-Location backend
```

#### 4. Apply database migrations

From the `backend/` directory, after setting `sqlalchemy.url` in `alembic.ini` to your database URL:

```bash
alembic upgrade head
```

#### 5. Start the backend processes

Run each process in a separate terminal with the virtual environment activated. Each terminal must have the environment variables loaded as described above and use `backend/` as its working directory.

**Terminal 1 — FastAPI**

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

**Terminal 2 — SMTP adapter**

```bash
python -m app.smtp.server
```

**Terminal 3 — email worker**

```bash
python -m app.workers.email_worker
```

**Terminal 4 — cleanup worker**

```bash
python -m app.workers.cleanup_worker
```

The SMTP adapter listens on `0.0.0.0:2525` with no authentication or TLS, which is suitable for host-local testing only. In Compose the port is published to `127.0.0.1:2525`. It is not configured to accept connections from external mail systems.

#### 6. Verify the API

- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Liveness: [http://127.0.0.1:8000/health/live](http://127.0.0.1:8000/health/live)
- Readiness: [http://127.0.0.1:8000/health/ready](http://127.0.0.1:8000/health/ready)

`/health/ready` returns `503` with per-dependency results when PostgreSQL or Redis is unreachable.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `DB_PASSWORD` | PostgreSQL container password | Set in root `.env` |
| `DATABASE_URL` | SQLAlchemy async PostgreSQL connection string | Required (Compose sets it) |
| `REDIS_URL` | Redis connection URL | `redis://127.0.0.1:6379/0` |
| `DEFAULT_DOMAIN` | Domain used to generate inbox addresses | `invismail.test` |
| `MAX_EMAIL_SIZE_BYTES` | Maximum accepted raw email size | `10485760` (10 MiB) |
| `MAX_ATTACHMENT_SIZE_BYTES` | Maximum size of one decoded attachment | `5242880` (5 MiB) |
| `MAX_MIME_PARTS` | Maximum number of MIME parts per message | `100` |
| `MAX_MIME_DEPTH` | Maximum MIME nesting depth | `10` |
| `MAX_ATTACHMENTS_PER_EMAIL` | Maximum attachments per message | `25` |
| `MAX_TOTAL_ATTACHMENT_BYTES` | Maximum total decoded attachment bytes per message | `26214400` (25 MiB) |
| `SPOOL_DLQ_RETENTION_SECONDS` | How long dead-lettered spool files are protected from cleanup | `3600` |
| `SPOOL_ORPHAN_GRACE_SECONDS` | Age before an unreferenced spool file or attachment is removed | `3600` |
| `LOG_LEVEL` | Python logging level | `INFO` |
| `RATE_LIMIT_CREATE_INBOX` | Inbox creations per client IP per minute | `30` |
| `RATE_LIMIT_LIST_MESSAGES` | Message list requests per client IP per minute | `60` |
| `RATE_LIMIT_READ_MESSAGE` | Message reads per client IP per minute | `120` |
| `RATE_LIMIT_DOWNLOAD_ATTACHMENT` | Attachment downloads per client IP per minute | `120` |

The Compose file passes `DATABASE_URL`, `REDIS_URL`, `DEFAULT_DOMAIN`, the size limits, and the spool settings to the containers. The MIME, logging, and rate-limit variables are not passed through; add them to the relevant service if you need to override the defaults in Docker.

Use host addresses (`127.0.0.1`) for Python processes running on your machine. Use Compose service names such as `postgres` and `redis` for processes running inside the Compose network.

## API reference

All application endpoints are served by the FastAPI backend.

| Method | Endpoint | Description | Authentication |
|---|---|---|---|
| `GET` | `/health` | Basic API health response | No |
| `GET` | `/health/live` | Liveness probe | No |
| `GET` | `/health/ready` | Readiness probe for PostgreSQL and Redis | No |
| `POST` | `/api/v1/inboxes` | Create a temporary inbox | No |
| `GET` | `/api/v1/inboxes/me/messages` | List messages in the authenticated inbox | Bearer token |
| `GET` | `/api/v1/messages/{message_id}` | Retrieve a message and attachment metadata | Bearer token |
| `DELETE` | `/api/v1/messages/{message_id}` | Delete a message | Bearer token |
| `GET` | `/api/v1/attachments/{attachment_id}` | Download an attachment | Bearer token |
| `GET` | `/api/v1/attachments/{attachment_id}/download` | Alias of the attachment download route | Bearer token |
| `GET` | `/api/v1/messages/{message_id}/attachments/{attachment_id}` | Download an attachment within a message | Bearer token |
| `GET` | `/api/v1/inboxes/me/events` | Subscribe to inbox events using SSE | Bearer token |

### Create an inbox

`POST /api/v1/inboxes`

Example request:

```json
{
  "ttl_seconds": 3600
}
```

`ttl_seconds` defaults to `3600` and must be between `300` (5 minutes) and `86400` (24 hours).

A successful response (`201`) includes:

- `id`: inbox UUID.
- `email_address`: generated disposable address.
- `access_token`: bearer token returned at creation.
- `expires_at`: expiry timestamp.
- `status`: inbox status.

Store the access token securely on the client. The raw token is not stored in PostgreSQL and is not returned by later API calls.

### Authenticate protected requests

Pass the inbox token in the HTTP Authorization header:

```http
Authorization: Bearer <access_token>
```

The API hashes the supplied token and checks that the corresponding inbox is active and has not expired. Invalid, inactive, or expired credentials return `401`.

### List messages

`GET /api/v1/inboxes/me/messages?page=1&limit=20`

- `page`: 1-based page number; minimum `1`.
- `limit`: number of messages per page; between `1` and `100`; default `20`.

Returns `items` (id, sender, subject, received_at, is_read), plus `page`, `limit`, and `total`, ordered newest first.

### Retrieve a message

`GET /api/v1/messages/{message_id}`

Returns message details including sender, recipient, subject, text and HTML bodies, received time, read state, and attachment metadata. Retrieving a message marks it as read. Messages belonging to another inbox return `404`.

### Delete a message

`DELETE /api/v1/messages/{message_id}`

Deletes the message and related database records, then attempts to remove its attachment files from disk. Returns `204`.

### Download an attachment

`GET /api/v1/attachments/{attachment_id}`

Returns the stored bytes as a file download. HTML and SVG content types are downgraded to `application/octet-stream`, responses include `X-Content-Type-Options: nosniff`, and the stored path must resolve inside the attachment directory.

### Subscribe to new-message events

`GET /api/v1/inboxes/me/events`

Streams Server-Sent Events from an inbox-specific Redis Pub/Sub channel. The stream emits a `connected` event on subscribe, a `new_email` event carrying `message_id` for each delivered message, and a heartbeat comment when the channel is idle. Pub/Sub notifications are transient. Clients should refresh the message list after connecting or reconnecting so a missed notification does not leave the UI stale. The endpoint uses bearer authentication; the browser's native `EventSource` API does not support arbitrary Authorization headers, so a browser client needs a fetch-based streaming approach or a carefully designed short-lived SSE ticket.

### Rate limits

Inbox creation, message listing, message retrieval, and attachment download are rate limited per client IP (using `X-Forwarded-For` when present) and return `429` when exceeded. The limiter fails open when Redis is unavailable.

For exact response schemas, use the interactive Swagger documentation at `/docs`.

## Sending test emails

1. Start PostgreSQL, Redis, the API, the SMTP adapter, and the email worker.
2. Create an inbox through Swagger UI and copy its generated email address.
3. Update the recipient in the relevant test script with that active address.
4. Run the script from the repository root.

Send a plain-text email:

```bash
python tests/send_test_email.py
```

Send an email with an attachment:

```bash
python tests/send_test_email_with_attachment.py
```

Both scripts connect to the SMTP adapter at `127.0.0.1:2525` and contain hard-coded recipients that must be replaced with a live inbox address. After the worker processes the event, use the authenticated message-list endpoint to verify delivery.

## Database and storage

### Data model

| Model | Purpose | Key fields |
|---|---|---|
| `Inbox` | Represents a disposable inbox | UUID, email address, token hash, status, creation time, expiry time, active flag |
| `Email` | Represents a received message | Inbox ID, source event ID, sender, recipient, subject, text/HTML body, message date, received time, read state, raw size |
| `Attachment` | Represents an extracted attachment | Email ID, original filename, content type, filesystem path, size |

Emails reference inboxes and attachments reference emails, both with cascading deletes. A unique constraint on `Email.source_event_id` supports duplicate-processing protection, and an `(inbox_id, received_at)` index supports message listing.

### Filesystem layout

The storage services use a repository-level `storage/` directory at runtime:

```text
storage/
├── spool/        # Raw .eml messages awaiting processing
└── attachments/  # Extracted attachment bytes
```

These are runtime data directories and should not be committed. Persist or back them up appropriately if messages must survive host or deployment changes.

## Testing

### Automated tests

Run the pytest suite from `backend/` with the virtual environment activated and environment variables configured:

```bash
pytest -v
```

The suite covers the MIME parser and its limits, duplicate event handling, atomic dead-letter handoff, expired-inbox cleanup, spool cleanup and reconciliation, attachment storage, safe spool deletion, and the health endpoints.

### Manual SMTP scripts

Run these from the repository root with the SMTP adapter and email worker running:

```bash
python tests/send_test_email.py
python tests/send_test_email_with_attachment.py
```

### Load smoke test

`tests/k6_smoke.js` is a [k6](https://k6.io/) smoke test for the health endpoints, with an optional authenticated message-list check:

```bash
k6 run tests/k6_smoke.js -e BASE_URL=http://127.0.0.1:8000 -e TOKEN=<access_token>
```

### Recommended verification scenarios

- Create an inbox and verify the generated address, token, and expiry.
- Reject invalid, inactive, and expired bearer tokens.
- Send a plain-text message and verify the stored content.
- Send a multipart message with an attachment, then download it.
- Confirm that one inbox cannot retrieve another inbox's messages or attachments.
- Replay a Redis event and verify that no duplicate email row is created.
- Stop the worker while an event is pending, restart it, and verify recovery.
- Trigger a processing failure and inspect retry handling and the dead-letter stream.
- Expire an inbox and verify cleanup of related records and attachment files.
- Stop Redis or PostgreSQL and observe API and worker behavior.

Use a separate test database for tests that create or delete database records.

## Security considerations

InvisMail handles untrusted email content. Before any public deployment:

- Keep `.env` out of version control. If credentials were ever committed, rotate them; removing the file in a later commit does not remove old values from Git history.
- `backend/alembic.ini` contains a checked-in development database URL and password, and Alembic uses it instead of `DATABASE_URL`. Do not rely on it as a real credential and do not commit a live password there.
- Keep inbox bearer tokens private. The API stores token hashes, but clients must protect the raw token returned at inbox creation.
- Keep every message and attachment operation scoped to the authenticated inbox.
- Continue generating attachment storage names instead of using sender-provided filenames as paths.
- Keep message, attachment, and MIME-part limits enabled to reduce resource-exhaustion risks.
- Treat `html_body` as untrusted even though the parser strips the most dangerous markup; regex-based sanitization is not a full sanitizer.
- Keep PostgreSQL and Redis bound to trusted interfaces. The checked-in Compose file publishes their ports on localhost only.
- Never log message bodies, attachment contents, bearer tokens, or other sensitive email content.
- Add abuse prevention, quotas, retention policies, and operational monitoring before exposing the service publicly.

**This repository is a local development project, not a hardened public email service.** A security review and deployment-specific hardening are required before internet exposure.

## Current limitations

- No frontend/client application is included.
- The default `invismail.test` domain is for local testing and cannot receive public email.
- The SMTP adapter has no authentication, TLS, or spam filtering, and no public MX records or production mail ingress are configured.
- Inline (non-text) MIME parts are skipped, so `cid:` images in HTML bodies are not stored or served.
- Inbox creation is not linked to any account; controlling the returned bearer token is the only proof of ownership.
- The SSE endpoint uses Redis Pub/Sub, which is transient rather than durable.
- Message listing uses page/offset pagination.
- Rate limiting is per IP address, counters live in Redis, and the limiter fails open when Redis is unavailable.
- The API returns HTML email content; a consuming client must treat it as untrusted input.
- Containers do not run migrations automatically, and Alembic reads its URL from `alembic.ini` rather than `DATABASE_URL`.

## Roadmap

Potential next steps for evolving the project:

- [ ] Add a migration step to the container startup path and let Alembic read `DATABASE_URL`.
- [ ] Expand API, worker, and cleanup test coverage.
- [ ] Add inbox quotas, blocklists, and stronger abuse prevention.
- [ ] Add a maintained HTML sanitizer and richer attachment handling.
- [ ] Add structured application logging, metrics, and monitoring.
- [ ] Evaluate cursor-based pagination and database query plans as inbox sizes grow.
- [ ] Build a frontend client for inbox creation and message viewing.
- [ ] Configure a real domain, MX records, and secure public SMTP ingress for external email reception.
- [ ] Add backup, retention, alerting, and deployment procedures.

## Contributing

Contributions, bug reports, and suggestions are welcome.

1. Fork the repository.
2. Create a feature branch: `git checkout -b feat/your-change`.
3. Make your changes and add or update tests.
4. Run the relevant tests.
5. Open a pull request with a clear description of the change.

## License

No license file is currently present in the repository. Until a license is added, do not assume that the project grants permission for reuse, redistribution, or modification.


## Mailgun inbound integration

InvisMail includes an optional signed Mailgun Routes adapter for development machines that cannot accept direct inbound SMTP connections (for example, a laptop behind carrier-grade NAT).

The endpoint receives Mailgun's original MIME message, validates the account HTTP webhook signing key, checks the active recipient, and publishes the same Redis Stream event consumed by the existing email worker.

See [MAILGUN_INBOUND.md](MAILGUN_INBOUND.md) for DNS, webhook signing-key, Cloudflare Quick Tunnel, Mailgun Route, and end-to-end test instructions.

The adapter endpoint is:

```text
POST /api/v1/inbound/mailgun/raw-mime
```

Set `MAILGUN_WEBHOOK_SIGNING_KEY` in the root `.env` file. Never commit this secret. Mailgun's Route destination must end in `mime` to receive the `body-mime` field.
