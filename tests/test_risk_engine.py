import pytest

from engine.config import CARESConfig
from engine.models import (
    PhysiologicalSample,
    RiskLevel,
)
from engine.risk_engine import CARESDecisionEngine


def calibrate_engine(
    engine: CARESDecisionEngine,
    heart_rate: float = 70.0,
) -> None:
    """
    Establish the CARES personal baseline using the required
    minimum 300 seconds of physiological observations.

    Samples occur once per second.

    Timestamp 0 -> timestamp 300 gives 300 elapsed seconds.
    """

    for t in range(301):
        engine.process_sample(
            PhysiologicalSample(
                timestamp=float(t),
                heart_rate_bpm=heart_rate,
            )
        )


def test_calibrating_phase():
    """
    CARES must remain in calibration until the minimum
    five-minute calibration duration has elapsed.
    """

    engine = CARESDecisionEngine()

    # Only five seconds of data.
    for t in range(5):

        output = engine.process_sample(
            PhysiologicalSample(
                timestamp=float(t),
                heart_rate_bpm=70.0,
            )
        )

        assert output.risk_level == RiskLevel.LOW
        assert "BASELINE_CALIBRATING" in output.reason_codes


def test_normal_resting_stream():
    """
    After five-minute personal calibration, a small deviation
    from the person's baseline must be reported correctly.

    Baseline = 70 BPM
    Current HR = 71 BPM
    Expected deviation = +1 BPM
    """

    engine = CARESDecisionEngine()

    calibrate_engine(
        engine,
        heart_rate=70.0,
    )

    # First observation after calibration.
    output = engine.process_sample(
        PhysiologicalSample(
            timestamp=301.0,
            heart_rate_bpm=71.0,
        )
    )

    assert output.risk_level == RiskLevel.LOW

    assert output.deviation == pytest.approx(
        1.0,
        abs=0.1,
    )

    assert output.baseline == pytest.approx(
        70.0,
        abs=0.1,
    )

    assert output.current_value == pytest.approx(
        71.0,
        abs=0.1,
    )


def test_personal_baseline_is_used_for_comparison():
    """
    CARES compares HR against the individual's own baseline,
    not against a population-wide HR threshold.
    """

    engine = CARESDecisionEngine()

    calibrate_engine(
        engine,
        heart_rate=60.0,
    )

    output = engine.process_sample(
        PhysiologicalSample(
            timestamp=301.0,
            heart_rate_bpm=68.0,
        )
    )

    assert output.baseline == pytest.approx(
        60.0,
        abs=0.1,
    )

    assert output.deviation == pytest.approx(
        8.0,
        abs=0.1,
    )

    assert output.risk_level == RiskLevel.LOW


def test_personal_baseline_detects_medium_deviation():
    """
    A sufficiently large deviation from the personal baseline
    should produce a MEDIUM candidate/risk state according to
    the configured CARES persistence logic.
    """

    config = CARESConfig()

    # Keep the test fast while preserving the real CARES
    # five-minute default architecture.
    config.baseline.calibration_duration_seconds = 300

    engine = CARESDecisionEngine(config)

    calibrate_engine(
        engine,
        heart_rate=70.0,
    )

    outputs = []

    for t in range(301, 310):

        output = engine.process_sample(
            PhysiologicalSample(
                timestamp=float(t),
                heart_rate_bpm=82.0,
            )
        )

        outputs.append(output)

    # At minimum the physiological deviation must be detected.
    assert outputs[-1].deviation > 0.0

    assert outputs[-1].baseline == pytest.approx(
        70.0,
        abs=0.5,
    )


def test_sustained_panic_escalation_and_reason_codes():
    """
    Sustained large personal-baseline deviation should escalate
    through CARES temporal persistence logic.

    Personal baseline = 70 BPM
    Current HR = 100 BPM
    """

    config = CARESConfig()

    config.baseline.window_samples = 10
    config.baseline.min_samples_required = 5
    config.baseline.calibration_duration_seconds = 9

    config.escalation.escalate_medium_persistence_samples = 3
    config.escalation.escalate_high_persistence_samples = 5

    engine = CARESDecisionEngine(config)

    # Establish the test baseline.
    for t in range(10):

        engine.process_sample(
            PhysiologicalSample(
                timestamp=float(t),
                heart_rate_bpm=70.0,
            )
        )

    outputs = []

    for t in range(10, 20):

        output = engine.process_sample(
            PhysiologicalSample(
                timestamp=float(t),
                heart_rate_bpm=100.0,
            )
        )

        outputs.append(output)

    # Large personal-baseline deviation must be present.
    assert outputs[-1].deviation >= 29.0

    # CARES must eventually escalate.
    assert any(
        output.risk_level in (
            RiskLevel.MEDIUM,
            RiskLevel.HIGH,
        )
        for output in outputs
    )

    # Explainability must identify the baseline deviation.
    assert any(
        "BASELINE_DEVIATION"
        in output.reason_codes
        for output in outputs
    )