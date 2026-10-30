# CARES Stage 2 Wi-Fi hardware integration

## Scope

CARES now accepts raw MAX30102/ESP32 BVP packets over HTTP and feeds completed windows through the existing `WristBVPHREstimator` and `CARESDecisionEngine`. No physical device was connected during implementation; automated inputs are synthetic fixtures only.

Production runtime uses `REAL_HARDWARE` sessions. The actual HTTP server disables `/api/demo/*` by default; set `CARES_ENABLE_DEMO=1` only for an explicitly non-production presentation or test environment. WESAD and synthetic demo adapters remain source-separated and are never part of the documented hardware data path.

## Transport

Endpoint:

```text
POST /api/hardware/ingest
Content-Type: application/json
X-CARES-Device-Id: <configured device id>
X-CARES-Device-Token: <configured secret>
```

The server must be reachable by the ESP32 on the same network or through the deployment’s configured network route. The backend does not discover devices or expose tokens. `GET /api/hardware/status` requires the existing application session and never returns device credentials.

Device credentials are configured by `CARES_HARDWARE_DEVICES` as JSON, for example:

```json
{
  "esp32-lab-01": {
    "user_id": 7,
    "token": "set-this-outside-source-control"
  }
}
```

For tests or controlled setup, `CARESBackend.register_hardware_device(user_id, device_id, token)` provides the same in-memory binding. Tokens are SHA-256 digested and compared with `hmac.compare_digest`; raw tokens are not persisted, returned, or logged. A device credential establishes the owning user. Packet `user_id` is rejected, and a supplied `session_id` must belong to that owner and be an active `REAL_HARDWARE` session.

## Version 1 packet contract

The implementation supports raw BVP samples and validated precomputed heart-rate/SpO₂ packets. An optional `location` object may be supplied by an authorized GPS/location source attached to the ESP32 installation; the MAX30102 itself has no GPS. Precomputed packets may include `signal_quality` in the range 0–1 and boolean `measurement_valid`; poor or invalid measurements are reported as unavailable and do not enter the decision engine. The current raw-BVP estimator defaults are 64 Hz, a 10-second/640-sample window, and a 1-second step. The backend setting `hardware_sampling_rate_hz` is configurable and defaults to 64 Hz; packets must match it. This is the configured protocol rate, not a measured physical-device rate.

```json
{
  "protocol_version": "1.0",
  "device_id": "esp32-lab-01",
  "connection_id": "boot-or-wifi-connection-uuid",
  "sequence": 42,
  "device_timestamp_start": 1234.0,
  "sampling_rate_hz": 64.0,
  "bvp_samples": [0.12, 0.14, 0.11],
  "session_id": "optional-active-cares-session-id",
  "location": {
    "latitude": 12.9716,
    "longitude": 77.5946,
    "timestamp": 1234.0,
    "accuracy": 8.0
  }
}
```

Required fields are `protocol_version`, `device_id`, `connection_id`, `sequence`, `device_timestamp_start`, `sampling_rate_hz`, and either a non-empty `bvp_samples` array or validated `heart_rate_bpm`. Optional `spo2_percent`, `signal_quality`, and `measurement_valid` fields are range/type checked. Device timestamps are finite seconds in the device clock; backend receipt time is tracked separately and is never substituted for device time in physiological features. BVP values must be finite numbers. Packet arrays are limited to 4,096 values and HTTP bodies remain subject to the server’s existing 1 MB limit. `user_id` is not accepted.

`connection_id` must change when the firmware starts a new Wi-Fi connection/boot epoch. Sequence numbers must increase within a connection. Duplicate, stale, or out-of-order sequence/timestamp packets return HTTP `409`; packets from retired connection IDs also return `409`. Unsupported versions, malformed fields, non-finite values, wrong sampling rate, or oversized arrays return `422`. Missing/invalid credentials return `401`; unauthorized session ownership is not accepted.

## Estimator and engine path

Packets are buffered independently per `(device_id, monitoring_session_id)` and bounded to one estimator window plus one step. No decision is made until a complete window exists. The estimator result maps as follows:

```text
HREstimate.accepted  -> PhysiologicalSample.additional_metrics["hr_valid"]
HREstimate.quality   -> PhysiologicalSample.additional_metrics["bvp_quality"]
HREstimate.timestamp -> PhysiologicalSample.timestamp
HREstimate.heart_rate_bpm -> PhysiologicalSample.heart_rate_bpm
```

Rejected/held estimates are reported in the ingest response but are not converted into a decision-engine sample and cannot change baseline, temporal features, escalation, persistence, or guardian actions. Accepted estimates use `CARESBackend.process_sample`, then the existing SQLite, guardian-action, incident, and SSE pathways. No GPS or external notification side effect is added.

The live dashboard exposes transport status separately from measurement status:
`INSUFFICIENT_DATA`, `POOR_SIGNAL`, `QUALITY_UNSPECIFIED`, `VALID`, `STALE`, and
`UNAVAILABLE` are not risk classifications. A stale or poor-quality reading is
never substituted into the current live display as a fresh value.

## Connection lifecycle

`GET /api/hardware/status` is an application-authenticated status endpoint. It reports `DISCONNECTED`, `CONNECTED`, `RECEIVING_DATA`, `STALE`, or `STOPPED`, plus last receipt time, last sequence, device timestamp, buffer depth, and transport epoch. The default stale timeout is 15 seconds and is configurable through the `hardware_stale_timeout_seconds` backend setting; it is an operational transport value, not a clinical threshold.

A stale status, sequence gap, device-time gap, or new connection ID starts a new transport/evidence epoch. The stream buffer and estimator continuity are cleared, and the existing engine’s temporal feature history, escalation state, and accepted timestamp are reset while its personal baseline is preserved. A packet from a retired connection or stopped session cannot enter the new epoch. Transport status is separate from LOW/MEDIUM/HIGH physiological risk.

## Local non-production integration test

```bash
python -m venv /tmp/cares-stage2-venv
/tmp/cares-stage2-venv/bin/python -m pip install -r requirements.txt
/tmp/cares-stage2-venv/bin/python -m pytest -q tests/test_hardware_ingestion.py
```

The test uses a clearly labelled fake estimator and synthetic BVP values. It verifies the receiver contract and state isolation; it does not prove MAX30102 wiring, radio reliability, estimator quality on a physical wrist, or clinical validity.

## Hardware teammate configuration

The firmware teammate must provide:

- a stable device ID matching the configured credential;
- a newly generated connection ID per boot/Wi-Fi connection;
- monotonically increasing packet sequence numbers within that connection;
- device timestamps in seconds and raw BVP samples at the configured sampling rate;
- the device token through a deployment secret channel; and
- the backend URL, network route, and active CARES session policy.

TLS, network isolation, secret rotation, physical sensor placement, MAX30102 electrical validation, and physical-device acceptance testing remain deployment responsibilities. The current HTTP token boundary is access control, not a claim of end-to-end transport security if deployed without TLS or network protection. The dashboard exposes Presentation Demo Mode when `CARES_ENABLE_DEMO=1`; demo routes remain disabled by default for production operation, and all demo fixtures remain source-separated from hardware data.

## Stage 3 action execution

High-risk action intents are persisted before a separate bounded worker attempts configured external actions. Twilio SMS and Programmable Voice adapters are disabled unless `CARES_ENABLE_LIVE_ACTIONS=1` and provider credentials are supplied. See `CARES_STAGE3_BACKEND_INTEGRATION.md` for statuses, callback security, retries, and the explicit limitation that voice is an authorized callback call rather than emergency-services dispatch.
