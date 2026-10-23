# CARES Backend and Integration

The backend is a local SQLite application service for authenticated hardware
ingestion, the existing CARES decision engine, persistence, event delivery,
and optional guardian-provider execution. It does not calculate risk. The existing `CARESDecisionEngine`
continues to be the only component that decides `LOW`, `MEDIUM`, or `HIGH`.

## Runtime flow

```text
ESP32/MAX30102 raw BVP → authenticated `/api/hardware/ingest` → bounded buffer
→ WristBVPHREstimator → PhysiologicalSample → CARESDecisionEngine
→ EngineOutput → SQLite persistence → API/SSE/UI
Optional hardware GPS → packet validation → location persistence → incident/alert context
```

For demonstration:

```text
WESAD wrist data → demo adapter → PhysiologicalSample → CARESDecisionEngine
→ EngineOutput → GuardianActionMapper → SQLite session → SSE → CARES web app
```

WESAD is a temporary physiological replay source only. Its labels are never
used to determine risk. A WESAD demo session is marked `DEMO_WESAD`; controlled
scenario streams are marked `DEMO_SYNTHETIC`. Both use the same engine path as
`REAL_HARDWARE` input while retaining distinct source and session labels. Each run has a new monitoring session, a fresh
runtime engine, and is discarded from memory when stopped. Demo baseline state
cannot become the next real-hardware runtime baseline.

`CARESBackend.process_sample(user_id, sample)` is the hardware ingestion seam.
It invokes the existing engine, persists the exact `EngineOutput`, records the
GuardianActionMapper commands as `GENERATED`, and creates an incident for an
actual `HIGH` output. It does not synchronously send SMS or contact emergency services. When explicitly configured, a separate durable action worker executes Twilio SMS/voice adapter requests and persists provider results; the voice integration is an authorized callback call, not public-safety dispatch.

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
POST   /api/provider/twilio/status  POST /api/provider/twilio/voice-status
GET    /api/events/stream       (authenticated SSE stream)
POST   /api/hardware/ingest     GET  /api/hardware/status
GET    /api/system/status       GET  /api/data/export
GET    /api/monitoring/sessions DELETE /api/monitoring/sessions/{id}
POST   /api/demo/start          POST /api/demo/stop
GET    /api/demo/status         (demo routes disabled by production server by default)
```

There is deliberately no risk-calculation endpoint. A frontend reads the
persisted engine output. Action statuses are `GENERATED` when no live provider is configured and `QUEUED` when an explicitly enabled provider worker will execute them. Provider results use `SUBMITTED`, `DELIVERED`, `FAILED`, or `UNKNOWN`; ambiguous submissions are never reported as delivered.

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

The default web application keeps demo controls hidden; the production server
disables demo routes unless `CARES_ENABLE_DEMO=1`. The NORMAL scenario requires
the official local file `data/wesad/S2/S2.pkl` and fails clearly if it is absent.
ELEVATED, SUSTAINED_HIGH, and RECOVERY are explicitly labeled synthetic
controlled input. No WESAD data is committed to Git.

The Settings view exposes an ownership-scoped JSON export and deletion of one
stopped monitoring session after explicit confirmation. Deletion removes local
CARES records for that session only; it cannot recall provider-side messages.

## Responsive app

The same server serves the CARES SPA at `/`. The UI lives in `frontend/` and
contains Dashboard, Live Monitoring, Personal Baseline, History, Incidents,
Guardian, Location, and Settings views. It uses the authenticated JSON API and
`/api/events/stream`; it has no physiological thresholds or independent risk
calculation. Charts are visual transformations of persisted event values.
