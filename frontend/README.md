# InvisMail frontend

The InvisMail frontend is a React + TypeScript + Vite application. It provides a temporary-mail experience with a generated inbox, address copying, expiry display, inbox search, automatic message refresh, message reading, deletion, and attachment downloads.

## Requirements

- Node.js 20 or newer
- npm
- The InvisMail backend stack running locally through Docker Compose

## Run locally

From the repository root, start the backend:

```powershell
docker compose up -d --build
```

Install frontend dependencies and start Vite:

```powershell
cd frontend
npm install
npm run dev
```

Open the local URL printed by Vite (normally `http://localhost:5173`).

The Vite development server proxies `/api` and `/health` to `http://127.0.0.1:8000`, so the browser uses a same-origin API path and does not need a separate CORS configuration.

If the backend is running somewhere else, set `API_PROXY_TARGET` before starting Vite:

```powershell
$env:API_PROXY_TARGET = "http://192.168.1.20:8000"
npm run dev
```

For a separately hosted API, set `VITE_API_BASE_URL` to its origin at build time. Configure CORS on the backend for the frontend origin in that deployment.

## Production build

```powershell
npm run build
npm run preview
```

The generated static site is written to `frontend/dist`.

## API integration

The UI uses the existing API contract:

- `POST /api/v1/inboxes`
- `GET /api/v1/inboxes/me/messages?page=1&limit=100`
- `GET /api/v1/messages/{message_id}`
- `DELETE /api/v1/messages/{message_id}`
- `GET /api/v1/attachments/{attachment_id}/download`

Protected requests use the inbox's bearer access token. The token is persisted in browser local storage to keep the current inbox available after a page reload. Use a dedicated browser profile for sensitive testing, and clear site data to discard the saved inbox session.

New messages are discovered by polling the message-list API every five seconds. This keeps compatibility with the current backend without requiring browser EventSource authentication support.

## Design

The interface uses a restrained dark workspace, warm neutral text, a lime accent, and a two-pane inbox/reader layout. The InvisMail mark is an abstract, interrupted eye: the broken contour and disappearing inner stroke represent invisibility without using a generic envelope illustration.
