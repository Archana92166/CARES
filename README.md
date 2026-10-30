# CARES: Cognitive Assistance and Response Emergency System for Visually Impaired Individuals

## Project Overview

**CARES** is a research-oriented emergency response and cognitive assistance platform designed for visually impaired individuals. It monitors real-time physiological indicators, identifies cognitive-risk distress situations, and adaptively escalates emergency response actions for guardian intervention.

---

## Fixed Project Requirements

### Problem Statement
> *"Existing assistive systems for visually impaired individuals focus mainly on physical safety and basic health monitoring but lack real-time cognitive support during abnormal situations."*

### Objectives
1. **Real-time Cognitive-Risk Monitoring**: To monitor physiological conditions in real time and identify abnormal cognitive-risk situations in visually impaired individuals.
2. **Adaptive Emergency Response**: To develop an adaptive emergency response mechanism that performs intelligent alert escalation based on physiological condition severity.
3. **Guardian Intervention & Support**: To enable immediate guardian intervention by sharing live location and supporting real-time emotional assistance during emergencies.

### Intended Methodology Pipeline
```
Physiological / Wearable Sensing
  │
  ▼
Communication
  │
  ▼
Mobile Application
  │
  ▼
Cognitive Risk Analysis Module
  │
  ▼
LOW / MEDIUM / HIGH Risk State
  │
  ▼
Adaptive Response
  │
  ▼
Guardian Support
  │
  ▼
Live Location
  │
  ▼
Communication / Emotional Assistance
  │
  ▼
Emergency Assistance
  │
  ▼
Incident Logging
```

---

## Architecture & Directory Structure

```
CARES/
├── engine/
│   ├── __init__.py         # Engine package initialization and exports
│   ├── models.py           # Validated physiological sample & result models
│   ├── baseline.py         # Personal baseline estimator
│   ├── features.py         # Deterministic temporal feature extractor
│   ├── risk_engine.py      # CARES Adaptive Cognitive-Risk Decision Engine
│   ├── escalation.py       # Adaptive escalation state machine
│   └── config.py           # Centralized configuration parameters
│
├── simulation/
│   ├── __init__.py         # Simulation package exports
│   ├── generator.py        # Synthetic physiological stream scenario generator
│   └── replay.py           # Stream replay engine for test verification
│
├── evaluation/
│   ├── __init__.py         # Evaluation package exports
│   ├── baselines.py        # Comparative naive static threshold baselines
│   └── metrics.py          # Research evaluation metrics (FPR, latency, churn)
│
├── guardian/
│   ├── __init__.py         # Guardian package exports
│   └── actions.py          # Guardian action software contract & mapping layer
│
├── ui/                     # UI components (Reserved for future milestone)
│
├── data/
│   └── scenarios/          # JSON benchmark scenario data files
│       ├── normal_resting.json
│       ├── transient_spike.json
│       ├── sustained_panic.json
│       └── stress_and_recovery.json
│
├── tests/                  # Pytest verification suite
│   ├── test_baseline.py
│   ├── test_features.py
│   ├── test_risk_engine.py
│   ├── test_escalation.py
│   ├── test_guardian_actions.py
│   ├── test_simulation_eval.py
│   └── test_invalid_input.py
│
├── results/                # Evaluation output artifacts directory
├── docs/                   # Documentation and research specifications
├── requirements.txt        # Python dependency manifest
└── README.md               # Project documentation
```

---

## Decision Engine Pipeline

The core technical contribution of CARES is the **Adaptive Cognitive-Risk Decision Engine**. Rather than relying on static, population-wide thresholds (`if heart_rate > threshold: HIGH`), the CARES engine reasons over temporal physiological behavior using a multi-stage deterministic pipeline:

```
Sample Input ➔ Baseline Estimation ➔ Feature Extraction ➔ Continuous Scoring ➔ State Machine ➔ Explainable Output & Actions
```

### 1. Personal Baseline Estimation (`engine/baseline.py`)
- Derives individualized reference parameters (mean, median, standard deviation) from an initial calibration window rather than universal constants.
- Assumes the initial reference window represents a resting/non-stress baseline.
- Supports slow adaptive drift updating during confirmed resting/LOW risk periods.

### 2. Temporal Feature Extraction (`engine/features.py`)
Deterministic extraction of five core temporal metrics:
1. **Absolute Deviation**: $Dev = HR_{current} - HR_{baseline}$ (bpm)
2. **Percentage Deviation**: $PctDev = \frac{HR_{current} - HR_{baseline}}{HR_{baseline}} \times 100\%$
3. **Short-Term Trend / Rate of Change**: Slope $\frac{\Delta HR}{\Delta t}$ over a sliding time window (bpm/sec).
4. **Abnormality Persistence**: Temporal duration (seconds) and sample count of sustained deviation above threshold.
5. **Recovery Behavior**: Detection of negative rate of change ($\frac{\Delta HR}{\Delta t} \le \text{threshold}_{rec}$) while returning toward baseline.

### 3. Continuous Risk Scoring & Evidence Confidence (`engine/risk_engine.py`)
- Computes a continuous risk score ($0.0 - 100.0$) combining deviation magnitude, percentage deviation, and trend acceleration.
- Evaluates evidence confidence ($0.0 - 1.0$) based on sample buffer depth, baseline readiness, and persistence stability.

### 4. Adaptive Escalation State Machine (`engine/escalation.py`)
Prevents false alarms caused by single transient noisy samples by enforcing temporal persistence and confidence hysteresis:
- **LOW $\rightarrow$ MEDIUM**: Requires continuous candidate elevation for $\ge 3$ consecutive samples.
- **MEDIUM $\rightarrow$ HIGH**: Requires continuous severe elevation for $\ge 5$ consecutive samples AND evidence confidence $\ge 0.6$.
- **Transient Immunity**: Single isolated noisy spikes are filtered out without escalating to HIGH.
- **De-escalation (HIGH $\rightarrow$ MEDIUM $\rightarrow$ LOW)**: Requires systematic evidence of returning to normal baseline bounds over $\ge 5$ consecutive samples or active recovery trend.

### 5. Explainable Reason Codes & Narrative
Every decision produces explicit, human-readable explanations accompanied by structured reason codes:
- `BASELINE_DEVIATION`
- `HIGH_PERCENTAGE_DEVIATION`
- `RISING_TREND`
- `PERSISTENT_ABNORMALITY`
- `RAPID_CHANGE`
- `RECOVERY_DETECTED`
- `ESCALATION_CONFIRMED`
- `DEESCALATION_CONFIRMED`
- `STABLE_BASELINE`
- `BASELINE_CALIBRATING`

---

## Guardian Action Contract

The engine output maps directly to software action commands defined in `guardian/actions.py`:

| Risk Level | Mapped Software Action Commands |
| :--- | :--- |
| **LOW** | `CONTINUE_MONITORING` |
| **MEDIUM** | `USER_WARNING`, `CONTINUE_MONITORING` |
| **HIGH** | `EMERGENCY_ALERT`, `GUARDIAN_NOTIFICATION`, `LOCATION_SHARE`, `GUARDIAN_COMMUNICATION`, `INCIDENT_LOG` |

---

## Hardware Status & Validation Disclaimer

> **IMPORTANT NOTICE ON CLINICAL & HARDWARE STATUS:**
> CARES includes a documented, authenticated raw-BVP Wi-Fi receiver for an ESP32/MAX30102 installation. The repository does not contain the physical firmware or wiring, so no physical-device test is claimed.
> **This implementation is not clinically validated and is not a diagnostic or public-safety dispatch system.**
> All thresholds provided in `engine/config.py` are algorithm and evaluation parameters intended for software verification and research prototyping.

The supported path is: ESP32 packet → `/api/hardware/ingest` → bounded BVP buffer → `WristBVPHREstimator` → `PhysiologicalSample` → existing `CARESDecisionEngine` → SQLite/API/SSE/UI. Device tokens, session ownership, sequence numbers, connection epochs, and optional location freshness are validated at the backend boundary. See `docs/CARES_STAGE2_HARDWARE_INTEGRATION.md` and `docs/CARES_STAGE3_BACKEND_INTEGRATION.md` for the deployment contract.

---

## Running Verification Tests

Install the Python dependencies in a virtual environment:

```bash
cd /home/archana/Documents/CARES
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

To execute the test suite using the CARES Python environment:

```bash
/home/archana/Documents/CARES/.venv/bin/pytest -v
```

To run a deterministic example through the full decision engine:

```bash
/home/archana/Documents/CARES/.venv/bin/python -c "
from engine import CARESDecisionEngine, PhysiologicalSample

engine = CARESDecisionEngine()
# Calibrate baseline
for t in range(30):
    engine.process_sample(PhysiologicalSample(timestamp=float(t), heart_rate_bpm=70.0))

# Elevated panic sample
sample = PhysiologicalSample(timestamp=30.0, heart_rate_bpm=115.0)
output = engine.process_sample(sample)
print(output.to_dict())
"
```

## Running the website and switching modes

The backend serves the browser frontend from `frontend/`, so CARES uses one
startup command. Enable the demo routes explicitly for a local presentation:

```bash
CARES_ENABLE_DEMO=1 CARES_ENABLE_LIVE_ACTIONS=0 \
  /home/archana/Documents/CARES/.venv/bin/python -m backend.server \
  --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000/` after the server starts. There is no separate
frontend development server in this repository; the command above is the
exact frontend/backend startup command and serves `frontend/index.html`,
`frontend/app.js`, and `frontend/styles.css`. A hardware-only deployment can
omit `CARES_ENABLE_DEMO=1`; the server disables demo routes by default.
`.env.example` contains a non-secret configuration template; environment
variables must be supplied by the operator and are not loaded automatically.

The default SQLite file is the stable project path `data/cares.sqlite3`, resolved relative to the repository rather than the shell's current directory. Set `CARES_DB_PATH` to an absolute path when deploying. The schema uses `CREATE TABLE IF NOT EXISTS` plus additive migrations, so startup does not recreate users or history.

After signing in, the top-bar selector always shows the selected mode:

- **Live Hardware Mode** reads only `REAL_HARDWARE` engine events, the authenticated device status, persisted sensor readings, and hardware GPS. The panel labels readings as sensor readings and reports `FRESH`, `STALE`, or `UNAVAILABLE`.
- **Presentation Demo Mode** starts an explicitly labelled `DEMO_*` monitoring session. The selector supports `NORMAL`, `ELEVATED`, `SUSTAINED_HIGH`, and `RECOVERY`. `NORMAL` uses official WESAD wrist data when `data/wesad/S2/S2.pkl` is installed and otherwise uses the backend's controlled synthetic fallback. `SUSTAINED_HIGH` is the presentation scenario that must satisfy the real CARES engine's configured persistence/confidence rules before showing HIGH. Demo data never uses the hardware ingestion route, `REAL_HARDWARE` events, or hardware readings.

The demo panel displays the source, scenario, session, engine risk state,
engine explanation, and guardian action state returned by the backend. Starting
the same or another scenario is safe: the backend stops the user's prior demo
run before creating a new isolated monitoring session. Switching back to Live
Hardware Mode requests a demo stop when one is active, clears the selected
view, and reloads only `REAL_HARDWARE` data. If stopping fails, the UI keeps
the demo source isolated and reports the failure.

Demo guardian actions are always shown as `SIMULATED GUARDIAN NOTIFICATION`; demo sessions never queue or dispatch real SMS or voice actions, even when live providers are configured. Hardware-triggered actions use the real provider workflow only when explicitly enabled. A real provider is only reported as delivered after the provider callback confirms `DELIVERED`; generated, queued, submitted, failed, and unknown states are not presented as delivery.

Useful environment variables:

```text
CARES_DB_PATH=/absolute/persistent/path/cares.sqlite3
CARES_SESSION_TTL_SECONDS=2592000
CARES_COOKIE_SECURE=1                 # use behind HTTPS
CARES_ENABLE_DEMO=1
CARES_DEMO_CONTINUOUS=1
CARES_HARDWARE_DEVICES={"esp32-max30100":{"user_id":7,"token":"deployment-secret"}}
# Or for one device: CARES_DEVICE_TOKEN=... plus CARES_HARDWARE_USER_ID=7
CARES_ENABLE_LIVE_ACTIONS=0
CARES_SMS_PROVIDER=twilio
CARES_SMS_ACCOUNT_SID=...
CARES_SMS_AUTH_TOKEN=...
CARES_SMS_FROM=...
CARES_SMS_STATUS_CALLBACK_URL=https://your-host.example/api/provider/twilio/status
CARES_VOICE_PROVIDER=twilio
CARES_VOICE_ACCOUNT_SID=...
CARES_VOICE_AUTH_TOKEN=...
CARES_VOICE_FROM=...
CARES_VOICE_STATUS_CALLBACK_URL=https://your-host.example/api/provider/twilio/voice-status
```

The browser receives only an HttpOnly session cookie; passwords and device tokens are not stored in browser storage or frontend JavaScript. The device token is accepted only by `POST /api/hardware/ingest` through `X-CARES-Device-Token` (or the compatible `X-Device-Token` header) and is never returned by status APIs. The single-device environment fallback uses `CARES_DEVICE_TOKEN`, `CARES_HARDWARE_USER_ID`, and optional `CARES_HARDWARE_DEVICE_ID`.

The repository currently contains the backend raw-BVP receiver and related test fixtures, but no `integrate_cares_hardware.py` or ESP32 sketch was present in the inspected checkout. The receiver remains backward-compatible with raw BVP and additionally accepts validated precomputed `heart_rate_bpm` plus optional `spo2_percent` readings from a MAX30100-style device. Both forms still pass HR through the existing risk engine.

Hardware intake is `POST /api/hardware/ingest` with `X-Device-Token` or
`X-CARES-Device-Token` authentication. The server validates ownership, packet
sequence/connection state, finite and bounded sensor values, and optional GPS
coordinates before processing the heart rate through the decision engine.
The live panel shows only actual persisted hardware readings; missing SpO₂,
GPS, disconnected devices, stale readings, insufficient windows, and poor
signal remain explicitly unavailable. Optional `signal_quality` and
`measurement_valid` fields from a precomputed sensor packet are validated; a
poor or invalid packet cannot create an engine event or overwrite the last
valid persisted reading. Raw-BVP estimates expose estimator quality and
require the configured 64 Hz, 10-second (640-sample) window before a reading
is produced. The reported 64 Hz value is the configured protocol rate, not a
claim that the physical device has been measured at exactly 64 Hz.

### Deployment persistence limitation

Local persistence is configured when the server uses `CARES_DB_PATH` or the repository default. Render's ordinary service filesystem is ephemeral across redeploys/restarts unless a persistent disk is attached and mounted. The repository cannot claim deployed cloud persistence from code alone: configure a Render persistent disk and point `CARES_DB_PATH` at its mount path, or migrate the persistence layer to a managed PostgreSQL service. Without that deployment configuration, local records may disappear after a Render restart even though the application schema is correct.

The local database path is resolved relative to the repository (`data/cares.sqlite3`)
and is initialized with non-destructive `CREATE TABLE IF NOT EXISTS` statements
plus additive migrations. Authentication uses an HttpOnly persistent session
cookie; the frontend does not store passwords, session tokens, or device tokens
in browser storage. `NOT_READY`, `CALIBRATING`, and `READY` baseline states are
derived from the backend's trusted engine history and remain source-filtered.

Guardian actions are honest about delivery: `GENERATED` means no provider was
configured, `QUEUED`/`SUBMITTED` indicate workflow progress, `FAILED` and
`UNKNOWN` indicate uncertainty or failure, and only provider-confirmed
`DELIVERED` is treated as delivery. With `CARES_ENABLE_LIVE_ACTIONS=0`, demo
alerts are labelled simulated and no real SMS or voice action is sent.

The Guardian page persists the contact name, country-code-capable phone number,
relationship, and SMS/voice channel preferences. The **Test guardian delivery**
control requires a separate confirmation and never invokes the risk engine. It
records `SIMULATED` when live actions or a provider are unavailable, or reports
the configured provider's actual `SUBMITTED`, `DELIVERED`, `FAILED`, or
`UNKNOWN` result. The test message explicitly says that no emergency was
detected.

The current provider adapter is Twilio. Its SDK/API integration is not itself
free message delivery: Twilio documents usage-based SMS charges, phone-number
fees, and carrier fees, while its trial is limited and requires verified
recipients. Voice is likewise usage-priced after trial allowances. No paid
provider is enabled by default, and CARES does not add an unofficial SMS
gateway or a hidden subscription. See the [Twilio messaging pricing](https://www.twilio.com/en-us/pricing/messaging),
[Twilio voice pricing](https://www.twilio.com/en-us/voice/pricing/us), and
[Twilio trial restrictions](https://www.twilio.com/docs/usage/trials).

### Verification and known limitations

Run the complete suite and the focused frontend checks with:

```bash
/home/archana/Documents/CARES/.venv/bin/pytest -q
/home/archana/Documents/CARES/.venv/bin/pytest -q tests/test_frontend.py
/home/archana/Documents/CARES/.venv/bin/pytest -q tests/test_frontend.py tests/test_demo_integration.py tests/test_persistence_and_live_hardware.py tests/test_personal_baseline.py
node --check frontend/app.js
git diff --check
```

Verified in this checkout: the complete suite passed `108 tests`; the focused
frontend/demo/persistence/baseline selection passed `27 tests`; `node --check
frontend/app.js` passed; and `git diff --check` passed.

The repository's automated tests verify backend validation, source/session
separation, demo scenarios and engine outputs, SQLite reconnection persistence,
session handling, baseline calibration, and the frontend mode contracts. They
do not test a physical ESP32/MAX30100/NEO-6M device, deployed HTTPS transport,
or real guardian-provider delivery. Render persistence requires a configured
persistent disk mounted at `CARES_DB_PATH` (or a managed database); code alone
cannot make Render's default ephemeral filesystem durable.

---

## Limitations & Future Integration Roadmap

### Current Limitations
1. **Physical device validation**: The receiver and estimator path are tested with synthetic packets; wiring, sensor placement, radio behavior, and firmware are not validated here.
2. **Single physiological signal**: The supported live path uses raw BVP to derive heart rate; additional metrics remain extensible through `additional_metrics`.
3. **External action uncertainty**: Twilio submissions can become `UNKNOWN` after an ambiguous timeout or crash; no provider-side exactly-once guarantee is claimed.
4. **WESAD availability**: Official WESAD files are not committed. Offline evaluation and demo use remain clearly separate from `REAL_HARDWARE`.

### Future Roadmap
- **Multimodal sensing**: Integration of EDA and IMU data after a separately reviewed contract.
- **Provider reconciliation**: Add provider-specific reconciliation where supported without blindly retrying ambiguous requests.
- **Deployment hardening**: TLS termination, secret-manager integration, operational retention policy, and physical-device acceptance testing.
