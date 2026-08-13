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
> Hardware components (such as ESP32 microcontrollers or MAX30102 PPG sensors) have **NOT** yet been integrated into this release.  
> **The current implementation is software-in-the-loop and does not constitute clinical validation or real-device validation.**  
> All thresholds provided in `engine/config.py` are algorithm and simulation parameters intended for software verification and research prototyping.

---

## Running Verification Tests

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

---

## Limitations & Future Integration Roadmap

### Current Limitations
1. **Software-in-the-Loop Input**: Input measurements are synthetically generated or replayed from scenario files.
2. **Single Physiological Signal**: Currently models heart rate (bpm) as the primary sensor stream (extensible via `additional_metrics`).
3. **Simulated GPS Location**: Location payload uses a software stub contract.

### Future Roadmap (Post Milestone 1)
- **MAX30102 / ESP32 Hardware Integration**: Direct Bluetooth/Wi-Fi ingestion from physical PPG sensor hardware.
- **Multimodal Sensing**: Integration of Electrodermal Activity (EDA / Skin Conductance) and IMU motion data.
- **Mobile UI & Live Guardian Portal**: Real-time Flutter / React Native user interface consuming the JSON `EngineOutput` contracts.
