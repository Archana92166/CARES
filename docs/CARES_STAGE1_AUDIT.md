# CARES Stage 1 backend audit

Stage 2 Wi-Fi hardware integration is documented in [CARES_STAGE2_HARDWARE_INTEGRATION.md](CARES_STAGE2_HARDWARE_INTEGRATION.md). Stage 3 backend/action integration is documented in [CARES_STAGE3_BACKEND_INTEGRATION.md](CARES_STAGE3_BACKEND_INTEGRATION.md).

## Scope and branch

Independent review of the actual repository on `feature/esp32-hardware-ingestion`. No hardware integration, serial transport, GPS hardware support, frontend change, commit, or push was performed. WESAD/demo paths remain intact.

## Verified architecture

```text
PhysiologicalSample
  -> CARESBackend.process_sample (backend/services.py)
  -> per-(user, session) CARESDecisionEngine
  -> BaselineEstimator (engine/baseline.py; runtime authority)
  -> FeatureExtractor
  -> candidate risk + confidence
  -> EscalationStateMachine
  -> BaselineEstimator.update_adaptive
  -> EngineOutput + GuardianActionMapper
  -> SQLite engine_events / guardian_action_events / incidents
  -> EventBus -> SSE and REST read models
```

`PersonalBaselineManager` is a separate, tested learning/audit implementation. It is not constructed by `CARESBackend`; it does not participate in runtime decisions. Offline `evaluation/` baselines are not runtime implementations.

`CARESBackend` persists the exact serialized `EngineOutput` without recalculation. It creates guardian action rows from `recommended_action`, creates incidents only for persisted HIGH output, and publishes persisted engine/incident events through the existing event bus/SSE path. Demo samples use the same engine seam in isolated demo sessions. Existing backend, guardian, frontend, persistence, and demo tests passed.

## Verified trust and ordering behavior

- `PhysiologicalSample.validate()` rejects non-finite timestamps, HR, and `bvp_quality`, plus existing range/type violations.
- `hr_valid` is contractually binary: omitted/legacy defaults to valid, and explicit `True`, `False`, `1`, and `1.0` are supported. Explicit non-binary values are rejected by `validate()`. `hr_valid=0` and `bvp_quality < 0.10` are not trusted evidence. A missing `bvp_quality` remains legacy-compatible as `1.0`.
- The runtime `CARESDecisionEngine` checks duplicate/out-of-order timestamps before processing and checks trust before updating its accepted timestamp. Invalid, explicitly untrusted, or poor-quality samples return a compatible LOW output with `INVALID_OR_LOW_QUALITY_SAMPLE` and cannot mutate baseline, feature history, persistence counters, candidate counts, escalation state, or adaptation.
- A duplicate/out-of-order sample returns LOW with `STALE_OR_OUT_OF_ORDER_SAMPLE` and does not mutate runtime decision state.
- Invalid/poor-quality outputs map only to `CONTINUE_MONITORING`; the backend therefore cannot create HIGH incidents or HIGH guardian actions from them. If no baseline exists, `_build_untrusted_output()` uses numeric sentinel `baseline=0.0`; if a baseline exists, it reports that actual baseline. The reason code distinguishes this output from genuine LOW-risk evidence while preserving the existing numeric/API/persistence contract.
- Runtime adaptation receives the current candidate risk, not the hysteresis output. A first MEDIUM/HIGH candidate cannot teach the baseline merely because the state machine still reports LOW.
- `reset()` clears baseline, feature history, escalation state, and accepted timestamp. Backend session stop discards the per-session engine, preventing calibration crossing sessions.

## Signal-quality boundary

`WristBVPHREstimator` produces `quality` and an inclusive `accepted = quality >= min_quality` result; its default `min_quality` is `0.10`. There is no current adapter that constructs `PhysiologicalSample` from `HREstimate`. The future hardware adapter must map estimator acceptance to `hr_valid` and the estimator quality to `additional_metrics["bvp_quality"]`; it must not treat a held HR value from an unaccepted estimate as physiological evidence.

`bvp_quality=1.0` when absent is strictly a backward-compatibility default for legacy/synthetic callers. It is not evidence that a future hardware signal was high quality. Hardware ingestion must provide explicit validity and quality fields. The current model cannot distinguish a legacy caller omitting quality from a hardware caller omitting quality, so enforcing mandatory hardware metadata now would be a product/API contract change; that decision remains open for Stage 2.

## Timestamp-gap findings

A long gap is currently accepted if timestamps remain increasing. `FeatureExtractor` retains pre-gap samples, keeps abnormal sample-count persistence, and computes persistence seconds as the full timestamp difference. In a direct experiment after three abnormal samples, resuming at timestamp `10000` produced `MEDIUM` with persistence `9699.0` seconds; the state did not reset. With only one pre-gap abnormal sample, the next abnormal sample did not immediately escalate because escalation still counts samples, but its persistence duration was still gap-sized.

This is not an immediate escalation bypass, but it can produce misleading persistence and allow pre-gap evidence to continue across a monitoring pause. No arbitrary timeout was implemented. Recommended policy: treat a transport/session pause as a new temporal evidence epoch—reset feature persistence, trend, and escalation hysteresis while preserving the personal baseline unless the product explicitly defines a full session reset. Alternatives are (a) require the hardware adapter to close/reopen monitoring sessions on pause, or (b) add a configured maximum-gap policy in the engine. Hardware clock synchronization and pause detection must be decided before Stage 2.

## Physiological semantics

The feature field historically named `abs_deviation` is actually signed `current_hr - baseline_hr`, and risk scoring uses that signed value. Current behavior intentionally detects elevated HR; negative deviations score LOW. This review did not broaden detection scope or change thresholds.

The runtime baseline requires the existing trusted-sample minimum and 300-second default calibration duration. The three-observation/three-second protocol belongs only to the unused-at-runtime `PersonalBaselineManager`; runtime `BaselineEstimator` uses its existing per-sample slow update.

## Confirmed fixes in this review

- `engine/risk_engine.py`: invalid samples no longer advance `_last_sample_timestamp`; a future-timestamp invalid packet cannot block the next valid packet.
- `engine/models.py`: trust boundary now matches `WristBVPHREstimator`’s inclusive `quality >= 0.10` acceptance contract and defensively rejects invalid metadata mutations after construction.
- `tests/test_risk_engine.py`: regression coverage now verifies invalid samples leave feature history and candidate counts unchanged, that an invalid future timestamp does not block a valid sample, and that unavailable baseline output is distinguishable and schema-compatible.
- `tests/test_invalid_input.py`: regression coverage now verifies binary/legacy `hr_valid` values and rejects invalid post-construction trust metadata.

Stage 1’s earlier fixes remain: finite-value validation; trust gating in baseline and personal-baseline learning; candidate-risk adaptation gating; and runtime duplicate/out-of-order rejection.

## Dependencies and test results

`requirements.txt` now declares the imported runtime/test dependencies: `pytest`, `numpy`, and `scipy`. In an isolated temporary environment: `/tmp/cares-stage1-venv/bin/python -m pip install -r requirements.txt` completed successfully.

Executed commands and results:

- `/tmp/cares-stage1-venv/bin/python -m pytest -q tests/test_invalid_input.py tests/test_baseline.py tests/test_risk_engine.py` — **20 passed**.
- `/tmp/cares-stage1-venv/bin/python -m pytest -q` — **63 passed in 55.42s**.
- `python -m compileall -q engine backend guardian tests` — passed.

No tests were weakened or deleted.

## Remaining blockers before Stage 2

1. Decide the pause/long-gap temporal policy and how the hardware transport signals a new evidence epoch.
2. Define the hardware adapter contract mapping `HREstimate.accepted`, `quality`, and held values into `PhysiologicalSample` without relying on missing-field defaults.
3. Decide whether hardware sessions may omit signal-quality metadata; the current backward-compatible model cannot enforce provenance-sensitive requirements.
4. Decide whether CARES remains elevated-HR-only or later supports two-sided deviation semantics.
5. Keep `PersonalBaselineManager` versus `BaselineEstimator` integration as an explicit architecture decision; do not merge them implicitly during hardware work.
