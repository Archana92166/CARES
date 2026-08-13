# CARES Milestone 1 Backend

The backend is a local, dependency-light foundation for the future CARES
application. It does not calculate risk. The existing `CARESDecisionEngine`
continues to be the only component that decides `LOW`, `MEDIUM`, or `HIGH`.

## Runtime flow

```text
Hardware HR → CARESDecisionEngine → EngineOutput → SQLite persistence → API/UI
Hardware GPS → POST /api/location → optional reverse geocoding → dashboard/map
```

For demonstration:

```text
WESAD wrist data → demo adapter → PhysiologicalSample → CARESDecisionEngine
→ EngineOutput → GuardianActionMapper → SQLite session → SSE → CARES web app
```

WESAD is a temporary physiological replay source only. Its labels are never
used to determine risk. A WESAD demo session is marked `DEMO_WESAD`; controlled
scenario streams are marked `DEMO_SYNTHETIC`. Both use the same engine path as
future `REAL_HARDWARE` input. Each run has a new monitoring session, a fresh
runtime engine, and is discarded from memory when stopped. Demo baseline state
cannot become the next real-hardware runtime baseline.

`CARESBackend.process_sample(user_id, sample)` is the hardware ingestion seam.
It invokes the existing engine, persists the exact `EngineOutput`, records the
GuardianActionMapper commands as `GENERATED`, and creates an incident for an
actual `HIGH` output. It does not send SMS or contact emergency services.

## Storage

The default database is `data/cares.sqlite3`. It contains users, sessions,
guardian contacts, engine events, guardian action events, location events,
incidents, daily baseline records, and baseline adaptation audit events.
Reason codes, recommended actions, and action metadata are serialized as JSON
inside SQLite. User-scoped queries enforce data isolation.

## API

The standard-library server is started with:

```bash
.venv/bin/python -m backend.server
```

Implemented routes:

```text
POST   /api/auth/register       POST   /api/auth/login
POST   /api/auth/logout         GET    /api/auth/me
GET/POST /api/guardian          PUT/DELETE /api/guardian/{id}
GET    /api/dashboard/current   GET    /api/dashboard/history
GET    /api/baseline/current    GET    /api/baseline/daily
GET    /api/baseline/adaptation
POST   /api/location            GET    /api/location/latest
GET    /api/actions             GET/PATCH /api/actions/{id}
GET    /api/incidents           GET    /api/incidents/{id}
GET    /api/events/stream       (authenticated SSE stream)
POST   /api/demo/start          POST /api/demo/stop
GET    /api/demo/status         GET  /api/monitoring/sessions
```

There is deliberately no risk-calculation endpoint. A frontend reads the
persisted engine output. Action statuses begin as `GENERATED`; integrations
must explicitly transition them to `PENDING`, `SENT`, `DELIVERED`, `FAILED`,
or `UNAVAILABLE`.

## Security and configuration

- Passwords use PBKDF2-HMAC-SHA256 with per-password random salts.
- Sessions use random bearer tokens stored only as SHA-256 hashes.
- Passwords and secrets are never returned by the API or logged.
- `CARES_DB_PATH` controls the SQLite path.
- `CARES_HOST` and `CARES_PORT` control the local server bind.
- Set `CARES_COOKIE_SECURE=1` when serving over HTTPS.
- Set `GOOGLE_GEOCODING_API_KEY` to enable Google reverse geocoding.

Without a geocoding key, the backend stores exact hardware coordinates and
uses a coordinate string as the address. It never fabricates a location.

The default web application includes the demo controls on the Dashboard. The
NORMAL scenario requires the official local file `data/wesad/S2/S2.pkl` and
fails clearly if it is absent. ELEVATED, SUSTAINED_HIGH, and RECOVERY are
explicitly labeled synthetic controlled input. No WESAD data is committed to
Git.

## Responsive app

The same server serves the CARES SPA at `/`. The UI lives in `frontend/` and
contains Dashboard, Live Monitoring, Personal Baseline, History, Incidents,
Guardian, Location, and Settings views. It uses the authenticated JSON API and
`/api/events/stream`; it has no physiological thresholds or independent risk
calculation. Charts are visual transformations of persisted event values.
