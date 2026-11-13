# InvisMail

**A self-hosted disposable email backend built with FastAPI, PostgreSQL, Redis Streams, and Python SMTP ingestion.**

InvisMail lets a client create a temporary inbox, receive messages through a local SMTP service, and retrieve them through an authenticated REST API. Email ingestion and message processing are decoupled using Redis Streams. PostgreSQL stores inbox and message metadata, while the local filesystem stores raw email spool files and attachment contents.

> **Project status:** Backend implementation for local development and testing. This repository contains the API, SMTP adapter, background workers, database migrations, and local infrastructure configuration. It does not currently include a frontend application or public-domain email delivery configuration.

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
- Cleaning up expired inboxes and orphaned attachment files.

The default `invismail.test` domain is intended for local testing. It is not a publicly deliverable email domain.

## Features

### Temporary inboxes

- Generate random email addresses for new inboxes.
- Configure inbox lifetime through a time-to-live (TTL) value.
- Generate high-entropy bearer access tokens.
- Store the SHA-256 hash of each access token instead of the raw token.
- Reject access to inactive or expired inboxes.

### Email ingestion and processing

- Accept email through an asynchronous SMTP handler built with `aiosmtpd`.
- Validate recipients against active, unexpired inboxes.
- Enforce a configurable maximum raw email size.
- Write raw `.eml` messages to disk before publishing queue events.
- Publish a separate Redis Stream event for each accepted recipient.
- Process messages asynchronously in a background worker.
- Parse plain-text and HTML MIME bodies and extract attachments.

### Message management

- List messages with page-based pagination.
- Retrieve an individual message.
- Track read state.
- Delete messages and their related attachment metadata.
- Scope protected message operations to the authenticated inbox.

### Attachments and storage

- Store attachment bytes on the local filesystem.
- Generate storage identifiers instead of using sender-provided filenames as filesystem paths.
- Enforce a configurable per-attachment size limit.
- Retain the original filename, content type, and size as metadata.

### Real-time notifications

- Publish new-message notifications through Redis Pub/Sub.
- Expose an authenticated Server-Sent Events (SSE) endpoint for inbox updates.

### Background workers

- Consume incoming email events through a Redis Streams consumer group.
- Recover abandoned pending events using `XAUTOCLAIM`.
- Track retry attempts and apply retry delays.
- Move permanently failing events to a dead-letter stream.
- Use a unique source event ID to protect against duplicate email records.
- Periodically remove expired inboxes and orphaned attachment files.

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
    API --> SSE
    Cleanup["Cleanup worker"] --> DB
    Cleanup --> Files
```

### Component responsibilities

| Component | Responsibility |
|---|---|
| FastAPI | Inbox creation, authentication, message APIs, and SSE endpoint |
| SMTP adapter | Validates recipients, applies the raw-message size limit, writes spool files, and publishes queue events |
| Redis Streams | Buffers incoming email events for asynchronous processing |
| Email worker | Recovers and processes queued events, parses MIME content, and persists messages |
| PostgreSQL | Stores inboxes, emails, and attachment metadata |
| Filesystem | Stores raw email spool files and extracted attachment bytes |
| Redis Pub/Sub | Sends transient new-message notifications to connected clients |
| Cleanup worker | Removes expired inbox records and orphaned attachment files |

PostgreSQL is the persistent source of truth for inbox and message data. Redis Streams supports asynchronous processing; Redis Pub/Sub is used for live notifications and is not a durable message history.

## Technology stack

| Area | Technology |
|---|---|
| Language | Python |
| API | FastAPI, Uvicorn |
| SMTP | aiosmtpd |
| Database | PostgreSQL |
| ORM | SQLAlchemy (async) |
| Migrations | Alembic |
| Queue and notifications | Redis Streams, Redis Pub/Sub |
| Redis client | redis-py (async) |
| Email parsing | Python standard-library `email` package |
| Validation | Pydantic |
| Testing | pytest, pytest-asyncio, httpx |
| Local infrastructure | Docker Compose |

## Repository structure

```text
invismail/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── events.py
│   │   │   ├── inboxes.py
│   │   │   └── messages.py
│   │   ├── core/
│   │   │   ├── auth.py
│   │   │   ├── database.py
│   │   │   ├── redis.py
│   │   │   └── security.py
│   │   ├── models/
│   │   │   ├── attachment.py
│   │   │   ├── email.py
│   │   │   └── inbox.py
│   │   ├── schemas/
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
├── tests/
│   ├── send_test_email.py
│   ├── send_test_email_with_attachment.py
│   ├── test.pdf
│   └── test.txt
├── docker-compose.yml
├── .gitignore
└── README.md
```

## Email processing lifecycle

1. **Create an inbox.** The API generates a random address and access token. PostgreSQL stores the token hash and expiry time; the raw token is returned to the client at creation.
2. **Receive an email.** The SMTP adapter verifies that each recipient maps to an active, unexpired inbox and rejects messages that exceed the configured size limit.
3. **Spool the message.** The raw message is written to a uniquely named `.eml` file. The adapter publishes an event for each recipient to the `email:incoming` Redis Stream.
4. **Process asynchronously.** The email worker consumes events, reads the raw message, parses MIME content, and checks that the destination inbox is still active.
5. **Persist the message.** Email data and attachment metadata are committed to PostgreSQL. Attachment bytes are stored separately on disk.
6. **Notify clients.** After the database commit, the worker publishes a notification to the inbox-specific Redis Pub/Sub channel. The API exposes this channel through SSE.
7. **Clean up.** The cleanup worker periodically removes expired inbox records and attachment files that are no longer referenced by the database.

The worker follows an at-least-once processing model. Pending-entry recovery, retry tracking, a dead-letter stream, and a database uniqueness constraint help make processing resilient to failures and duplicate deliveries.

## Getting started

### Prerequisites

- Python 3.11 or later.
- Docker Engine and the Docker Compose plugin.
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

Use the same password in `DB_PASSWORD` and `DATABASE_URL`. The URL above is for Python processes running directly on your host. Processes running inside Docker should connect to PostgreSQL using the Compose service hostname `postgres`, not `127.0.0.1`.

### 3. Start PostgreSQL and Redis

The current `docker-compose.yml` starts the two infrastructure dependencies only. It does **not** build or start the API, SMTP server, or Python workers.

```bash
docker compose up -d
docker compose ps
```

Check both dependencies:

```bash
docker compose exec postgres pg_isready -U invismail
docker compose exec redis redis-cli ping
```

Redis should respond with `PONG`.

### 4. Create a Python environment

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

### 5. Export environment variables and enter the backend directory

The API and SMTP entry points load the root `.env` file. The worker entry points read database and Redis settings from the process environment, so export the variables before starting them.

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

### 6. Apply database migrations

From the `backend/` directory:

```bash
alembic upgrade head
```

### 7. Start the backend processes

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

The SMTP server currently binds to `127.0.0.1:2525`, which is suitable for host-local testing. It is not configured to accept connections from external mail systems.

### 8. Verify the API

- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health endpoint: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

The current `/health` endpoint reports API process health. It does not independently verify PostgreSQL and Redis readiness.

## Environment variables

| Variable | Purpose | Example / default |
|---|---|---|
| `DB_PASSWORD` | PostgreSQL container password | Set in root `.env` |
| `DATABASE_URL` | SQLAlchemy async PostgreSQL connection string | `postgresql+asyncpg://...` |
| `REDIS_URL` | Redis connection URL | `redis://127.0.0.1:6379/0` |
| `DEFAULT_DOMAIN` | Domain used to generate inbox addresses | `invismail.test` |
| `MAX_EMAIL_SIZE_BYTES` | Maximum accepted raw email size | `10485760` (10 MiB) |
| `MAX_ATTACHMENT_SIZE_BYTES` | Maximum size of one decoded attachment | `5242880` (5 MiB) |

Use host addresses (`127.0.0.1`) for Python processes running on your machine. Use Compose service names such as `postgres` and `redis` for processes running inside the Compose network.

## API reference

All application endpoints are served by the FastAPI backend.

| Method | Endpoint | Description | Authentication |
|---|---|---|---|
| `GET` | `/health` | Basic API health check | No |
| `POST` | `/api/v1/inboxes` | Create a temporary inbox | No |
| `GET` | `/api/v1/inboxes/me/messages` | List messages in the authenticated inbox | Bearer token |
| `GET` | `/api/v1/messages/{message_id}` | Retrieve a message and attachment metadata | Bearer token |
| `DELETE` | `/api/v1/messages/{message_id}` | Delete a message | Bearer token |
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

A successful response includes:

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

The API hashes the supplied token and checks that the corresponding inbox is active and has not expired.

### List messages

`GET /api/v1/inboxes/me/messages?page=1&limit=20`

- `page`: 1-based page number; minimum `1`.
- `limit`: number of messages per page; between `1` and `100`; default `20`.

### Retrieve a message

`GET /api/v1/messages/{message_id}`

Returns message details including sender, recipient, subject, text and HTML bodies, received time, read state, and attachment metadata.

### Delete a message

`DELETE /api/v1/messages/{message_id}`

Deletes the message and related database records, then attempts to remove its attachment files from disk.

### Subscribe to new-message events

`GET /api/v1/inboxes/me/events`

Streams Server-Sent Events from an inbox-specific Redis Pub/Sub channel. Pub/Sub notifications are transient. Clients should refresh the message list after connecting or reconnecting so a missed notification does not leave the UI stale. The endpoint uses bearer authentication; the browser's native `EventSource` API does not support arbitrary Authorization headers, so a browser client will need a fetch-based streaming approach or a carefully designed short-lived SSE ticket.

For exact response schemas, use the interactive Swagger documentation at `/docs`.

## Sending test emails

1. Start PostgreSQL, Redis, the API, and the SMTP adapter.
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

Both scripts connect to the local SMTP adapter at `127.0.0.1:2525`. After the worker processes the event, use the authenticated message-list endpoint to verify delivery.

## Database and storage

### Data model

| Model | Purpose | Key fields |
|---|---|---|
| `Inbox` | Represents a disposable inbox | UUID, email address, token hash, status, creation time, expiry time, active flag |
| `Email` | Represents a received message | Inbox ID, source event ID, sender, recipient, subject, text/HTML body, message date, received time, read state, raw size |
| `Attachment` | Represents an extracted attachment | Email ID, original filename, content type, filesystem path, size |

Emails reference inboxes, and attachments reference emails. Foreign-key cascade behavior is configured for related message data. A unique constraint on `Email.source_event_id` supports duplicate-processing protection.

### Filesystem layout

The storage services use a repository-level `storage/` directory at runtime:

```text
storage/
├── spool/        # Raw .eml messages awaiting processing
└── attachments/  # Extracted attachment bytes
```

These are runtime data directories and should not be committed. Persist or back them up appropriately if messages must survive host or deployment changes.

## Testing

The repository currently includes a parser unit test and manual SMTP test scripts.

Run the pytest suite from `backend/` with the virtual environment activated and environment variables configured:

```bash
pytest -v
```

Run the manual SMTP scripts from the repository root:

```bash
python tests/send_test_email.py
python tests/send_test_email_with_attachment.py
```

### Recommended verification scenarios

- Create an inbox and verify the generated address, token, and expiry.
- Reject invalid and expired bearer tokens.
- Send a plain-text message and verify the stored content.
- Send a multipart message with an attachment and verify its metadata and stored file.
- Confirm that one inbox cannot retrieve another inbox's messages.
- Replay a Redis event and verify that no duplicate email row is created.
- Stop the worker while an event is pending, restart it, and verify recovery.
- Trigger a processing failure and inspect retry handling and the dead-letter stream.
- Expire an inbox and verify cleanup of related records and attachment files.
- Stop Redis or PostgreSQL and observe API and worker behavior.

Use a separate test database for tests that create or delete database records.

## Security considerations

InvisMail handles untrusted email content. Before any public deployment:

- Keep `.env` out of version control. If credentials were ever committed, rotate them; removing the file in a later commit does not remove old values from Git history.
- Keep inbox bearer tokens private. The API stores token hashes, but clients must protect the raw token returned at inbox creation.
- Keep every message and attachment operation scoped to the authenticated inbox.
- Continue generating attachment storage names instead of using sender-provided filenames as paths.
- Enforce message, attachment, and MIME-part limits to reduce resource-exhaustion risks.
- Do not render untrusted `html_body` directly in a browser without robust sanitization and isolation.
- Serve downloaded attachments with safe content-disposition and content-sniffing protections.
- Keep PostgreSQL and Redis bound to trusted interfaces. The checked-in Compose file currently publishes their ports on localhost only.
- Never log message bodies, attachment contents, bearer tokens, or other sensitive email content.
- Add abuse prevention, rate limiting, quotas, retention policies, and operational monitoring before exposing the service publicly.

**This repository is a local development project, not a hardened public email service.** A security review and deployment-specific hardening are required before internet exposure.

## Current limitations

- No frontend/client application is included.
- The current Compose file starts PostgreSQL and Redis only; API, SMTP, and worker processes are started separately with Python commands.
- The default `invismail.test` domain is for local testing and cannot receive public email.
- The SMTP adapter currently binds to localhost.
- No public MX records, public SMTP ingress, TLS/mail-server configuration, or production mail delivery setup is included.
- The SSE endpoint uses Redis Pub/Sub, which is transient rather than durable.
- Message listing currently uses page/offset pagination.
- `/health` is a basic API health response; separate dependency-aware readiness checks are not currently exposed.
- The API returns HTML email content; a consuming client must treat it as untrusted input.
- There is no dedicated attachment download endpoint in the current API routes; message retrieval returns attachment metadata.

## Roadmap

Potential next steps for evolving the project:

- [ ] Add a complete Docker Compose deployment for the API, SMTP adapter, email worker, and cleanup worker.
- [ ] Add dependency-aware readiness checks for PostgreSQL and Redis.
- [ ] Add spool reconciliation for files written before a failed Redis publish.
- [ ] Expand API, worker, authentication, and cleanup test coverage.
- [ ] Add rate limiting, inbox quotas, and abuse prevention.
- [ ] Add robust HTML email sanitization and safe attachment download handling.
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
