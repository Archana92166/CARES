# CARES Stage 3 backend integration report

## Verified implementation

The production path is now:

```text
Authenticated ESP32 HTTP packet
  -> HardwarePacket validation and per-device connection/sequence state
  -> bounded raw-BVP buffer
  -> WristBVPHREstimator
  -> PhysiologicalSample (explicit hr_valid and bvp_quality)
  -> existing CARESDecisionEngine
  -> SQLite engine event, action intent, and incident persistence
  -> recoverable action worker / provider adapter
  -> EventBus/SSE and authorized REST read models
```

There is still one risk engine: `CARESDecisionEngine`. Hardware stream state is isolated by `(device_id, monitoring_session_id)`. Rejected packets are checked before stream ordering mutation; accepted estimates use `CARESBackend.process_sample`. Transport status remains separate from LOW/MEDIUM/HIGH risk.

## Optional hardware location

Version `1.0` packets may include:

```json
{"location":{"latitude":12.971600,"longitude":77.594600,"timestamp":1700000000.0,"accuracy":8.0}}
```

The location object is optional, finite, range-checked, and associated with the authenticated device and active owner session. `(0, 0)` is valid. By default, the location timestamp must be no more than 120 seconds older than, and no more than 30 seconds newer than, the packet's `device_timestamp_start`; `CARESBackend(location_freshness_seconds=...)` configures freshness. Server receipt time is stored separately and is used for outbound freshness checks. Invalid, future, or stale location rejects the packet before decision state changes. MAX30102 does not provide GPS; the ESP32 installation must have an authorized GPS/location source or omit the object. Hardware locations include `device_id` in persistence. Stale locations are excluded from new hardware incidents and outbound messages.

## External actions

High-risk action intents are persisted in the existing `guardian_action_events` table before execution. When live action execution is explicitly enabled, external action rows begin as `QUEUED`; otherwise the legacy `GENERATED` state honestly means no external delivery was attempted. `action_attempts` stores channel/recipient-specific attempt count, timestamps, sanitized failure reason, provider reference, retry state, and provider status. SQLite claims prevent concurrent local workers from taking the same queued action. A persisted claim lease is recovered after `action_claim_timeout_seconds`; an attempt already marked `SUBMITTING` is changed to `UNKNOWN` on restart and is never automatically retried, because the provider may already have accepted it. The HTTP server starts the bounded worker and recovers interrupted rows on startup. `CARESBackend.process_pending_actions_once()` is the deterministic scheduler hook.

Actions sourced from `DEMO_*` sessions are persisted as `GENERATED` with
`execution_status=SIMULATED` and are never queued for the external worker.

SMS uses the Twilio Messages API when configured. Provider acceptance is `SUBMITTED`, not `DELIVERED`; signed Twilio status callbacks can transition the attempt to `DELIVERED` or `FAILED`. The Twilio Messages/Voice REST calls used here do not provide a CARES-level create-idempotency guarantee, so a crash after provider acceptance but before saving the provider reference remains `UNKNOWN` and is never automatically retried. Retryable failures receive at most the configured bounded retries. Guardian recipients come only from the authenticated owner's configured guardian contacts. Phone numbers and provider payloads are not included in action-attempt read models.

Voice uses the Twilio Programmable Voice API for an explicitly configured authorized callback contact/guardian. It is not a public-safety dispatch service and does not contact emergency services. The `EMERGENCY_ALERT` policy action is disabled unless `CARES_ENABLE_EMERGENCY_ACTION=1` and `CARES_EMERGENCY_CALL_TO` are configured. A Twilio call submission is `SUBMITTED`; only a signed `completed` callback becomes `DELIVERED`. No real provider is enabled by tests.

Provider failures are isolated from physiological processing. No notification is sent merely because an incident row or SSE event exists.

## Configuration

Safe deployment configuration uses environment variables (never source-controlled secrets):

```text
CARES_ENABLE_LIVE_ACTIONS=0|1
CARES_SMS_PROVIDER=twilio
CARES_SMS_ACCOUNT_SID=<secret-manager-value>
CARES_SMS_AUTH_TOKEN=<secret-manager-value>
CARES_SMS_FROM=<Twilio-number>
CARES_SMS_MESSAGING_SERVICE_SID=<optional>
CARES_SMS_STATUS_CALLBACK_URL=https://backend.example/api/provider/twilio/status
CARES_VOICE_PROVIDER=twilio
CARES_VOICE_ACCOUNT_SID=<secret-manager-value>
CARES_VOICE_AUTH_TOKEN=<secret-manager-value>
CARES_VOICE_FROM=<Twilio-number>
CARES_VOICE_STATUS_CALLBACK_URL=https://backend.example/api/provider/twilio/voice-status
CARES_ENABLE_EMERGENCY_ACTION=0|1
CARES_EMERGENCY_CALL_TO=<authorized-callback-number>
CARES_PROVIDER_TIMEOUT_SECONDS=10
CARES_ACTION_WORKER_INTERVAL_SECONDS=2
CARES_HARDWARE_DEVICES={"device-id":{"user_id":7,"token":"deployment-secret"}}
```

`CARES_ENABLE_LIVE_ACTIONS` defaults to disabled. Twilio account approval, phone-number capabilities, regional availability, public HTTPS callback URLs, and legal/organizational authorization remain deployment requirements. This implementation is not a public-safety emergency dispatch integration.

## API additions

- `POST /api/hardware/ingest` remains the authenticated-device raw-BVP endpoint.
- `GET /api/hardware/status` requires an application session.
- `GET /api/system/status` reports provider configuration without secrets.
- `GET /api/data/export` returns an ownership-scoped JSON export without password or session-token hashes.
- `DELETE /api/monitoring/sessions/{id}` requires `{"confirm":true}` and only deletes a stopped, user-owned session.
- `GET /api/actions` and `GET /api/actions/{id}` now include sanitized recipient-level `attempts`. Existing action and incident ownership filters remain in force.
- `POST /api/provider/twilio/status` and `/api/provider/twilio/voice-status` accept only provider-signed callbacks when configured.
- `POST /api/demo/*` remains disabled by the production server unless `CARES_ENABLE_DEMO=1`; when explicitly enabled for a presentation, the dashboard labels demo sessions and keeps WESAD/synthetic samples separate from hardware data.

Guardian contacts can opt into SMS and/or voice channels. `GET /api/guardian/test`
lists explicit delivery tests, and `POST /api/guardian/test` requires an
authenticated user, a selected channel, and an exact confirmation value. With
live actions disabled or a provider unavailable, the result is persisted as
`SIMULATED`; a test message never invokes the risk engine or claims an
emergency was detected.

## Verification status

Verified implementation facts are separated from synthetic test evidence: focused tests cover authenticated packet ingestion, location boundaries/freshness/association, connection recovery, no decision-state mutation on rejection, provider request/result semantics through injected mock providers, recipient idempotency, bounded retry, ambiguous timeout handling, missing credentials/emergency configuration, signed callbacks, persistence, and existing demo/risk behavior. The worker persists claim leases atomically and recovers expired claims during its lifecycle; an attempt interrupted during provider submission is terminal `UNKNOWN` because the external outcome cannot be reconciled safely.

Synthetic fixtures do not validate physical ESP32 wiring, radio reliability, sensor placement, provider account behavior, or clinical performance. No physical device or live SMS/voice provider was used.

## Commands run

Using `/tmp/cares-stage1-venv`:

```text
/tmp/cares-stage1-venv/bin/python -m pip install -r requirements.txt
  completed; pytest, numpy, scipy, twilio and transitive dependencies were already satisfied
/tmp/cares-stage1-venv/bin/python -m pytest -q tests/test_stage3_actions.py tests/test_hardware_ingestion.py tests/test_backend.py tests/test_guardian_actions.py tests/test_invalid_input.py tests/test_risk_engine.py
  57 passed in 14.46s
/home/archana/Documents/CARES/.venv/bin/pytest -q
  104 passed in 70.79s
python -m compileall -q backend guardian engine
  passed (exit 0)
git diff --check
  passed (exit 0)
```

Twilio callback verification uses the official SDK `RequestValidator`; tests cover valid, missing, invalid, modified, unknown-reference, repeated, and invalid-transition callbacks. No physical ESP32/MAX30102, live SMS, live voice call, or public-safety dispatch test was performed.
