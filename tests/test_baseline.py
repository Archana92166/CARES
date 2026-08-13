"""
Unit tests for Baseline Estimator.
"""

import pytest
from engine.baseline import BaselineEstimator
from engine.config import BaselineConfig
from engine.models import BaselineState, PhysiologicalSample


def test_baseline_calibration_readiness():
    config = BaselineConfig(window_samples=10, min_samples_required=5)
    estimator = BaselineEstimator(config)

    assert estimator.state == BaselineState.NOT_READY
    assert estimator.baseline_hr is None

    # Add 4 samples (under min required)
    for t in range(4):
        state = estimator.add_sample(PhysiologicalSample(timestamp=float(t), heart_rate_bpm=70.0))
        assert state == BaselineState.NOT_READY

    # 5th sample triggers CALIBRATING
    state = estimator.add_sample(PhysiologicalSample(timestamp=4.0, heart_rate_bpm=70.0))
    assert state == BaselineState.CALIBRATING
    assert estimator.baseline_hr == pytest.approx(70.0)

    # Fill up to 10 samples triggers READY
    for t in range(5, 10):
        state = estimator.add_sample(PhysiologicalSample(timestamp=float(t), heart_rate_bpm=70.0))

    assert state == BaselineState.READY
    assert estimator.baseline_hr == pytest.approx(70.0)
    assert estimator.std_hr == pytest.approx(0.0)


def test_baseline_statistics():
    config = BaselineConfig(window_samples=5, min_samples_required=3)
    estimator = BaselineEstimator(config)

    hrs = [60.0, 70.0, 80.0, 90.0, 100.0]
    for t, hr in enumerate(hrs):
        estimator.add_sample(PhysiologicalSample(timestamp=float(t), heart_rate_bpm=hr))

    assert estimator.baseline_hr == pytest.approx(80.0)
    assert estimator.median_hr == pytest.approx(80.0)
    # std of [60, 70, 80, 90, 100] mean 80 is sqrt((400+100+0+100+400)/5) = sqrt(200) = ~14.142
    assert estimator.std_hr == pytest.approx(14.1421, rel=1e-3)


def test_baseline_adaptive_update():
    config = BaselineConfig(window_samples=5, min_samples_required=3, adaptive_update_alpha=0.1)
    estimator = BaselineEstimator(config)

    for t in range(5):
        estimator.add_sample(PhysiologicalSample(timestamp=float(t), heart_rate_bpm=70.0))

    assert estimator.baseline_hr == pytest.approx(70.0)

    # Adaptive update during LOW risk
    estimator.update_adaptive(PhysiologicalSample(timestamp=5.0, heart_rate_bpm=75.0), current_risk_level="LOW")
    # 0.9 * 70 + 0.1 * 75 = 70.5
    assert estimator.baseline_hr == pytest.approx(70.5)

    # Adaptive update skipped during HIGH risk
    estimator.update_adaptive(PhysiologicalSample(timestamp=6.0, heart_rate_bpm=120.0), current_risk_level="HIGH")
    assert estimator.baseline_hr == pytest.approx(70.5)
