"""
Unit tests for Deterministic Feature Extraction.
"""

import pytest
from engine.config import FeatureConfig
from engine.features import FeatureExtractor
from engine.models import PhysiologicalSample, RecoveryState


def test_feature_extraction_deviations_and_trend():
    config = FeatureConfig(short_term_window_samples=3)
    extractor = FeatureExtractor(config)

    baseline_hr = 70.0

    s1 = PhysiologicalSample(timestamp=0.0, heart_rate_bpm=70.0)
    f1 = extractor.extract_features(s1, baseline_hr)
    assert f1.abs_deviation == pytest.approx(0.0)
    assert f1.pct_deviation == pytest.approx(0.0)
    assert f1.rate_of_change == pytest.approx(0.0)

    s2 = PhysiologicalSample(timestamp=2.0, heart_rate_bpm=80.0)
    f2 = extractor.extract_features(s2, baseline_hr)
    assert f2.abs_deviation == pytest.approx(10.0)
    assert f2.pct_deviation == pytest.approx((10.0 / 70.0) * 100.0)
    # Rate of change: (80 - 70) / (2 - 0) = 5.0 bpm/s
    assert f2.rate_of_change == pytest.approx(5.0)


def test_abnormality_persistence_tracking():
    config = FeatureConfig(persistence_deviation_threshold_bpm=10.0)
    extractor = FeatureExtractor(config)
    baseline_hr = 70.0

    # Normal sample
    f0 = extractor.extract_features(PhysiologicalSample(timestamp=0.0, heart_rate_bpm=70.0), baseline_hr)
    assert f0.abnormality_persistence_samples == 0
    assert f0.abnormality_persistence_seconds == 0.0

    # Abnormal samples
    f1 = extractor.extract_features(PhysiologicalSample(timestamp=2.0, heart_rate_bpm=85.0), baseline_hr)
    assert f1.abnormality_persistence_samples == 1
    assert f1.abnormality_persistence_seconds == 0.0  # First abnormal sample

    f2 = extractor.extract_features(PhysiologicalSample(timestamp=5.0, heart_rate_bpm=90.0), baseline_hr)
    assert f2.abnormality_persistence_samples == 2
    assert f2.abnormality_persistence_seconds == pytest.approx(3.0)

    f3 = extractor.extract_features(PhysiologicalSample(timestamp=8.0, heart_rate_bpm=92.0), baseline_hr)
    assert f3.abnormality_persistence_samples == 3
    assert f3.abnormality_persistence_seconds == pytest.approx(6.0)

    # Return to normal resets persistence
    f4 = extractor.extract_features(PhysiologicalSample(timestamp=10.0, heart_rate_bpm=71.0), baseline_hr)
    assert f4.abnormality_persistence_samples == 0
    assert f4.abnormality_persistence_seconds == 0.0


def test_recovery_behavior_classification():
    config = FeatureConfig(
        persistence_deviation_threshold_bpm=10.0,
        recovery_rate_threshold_bpm_per_sec=-0.3,
        short_term_window_samples=3,
    )
    extractor = FeatureExtractor(config)
    baseline_hr = 70.0

    # Build abnormal state
    extractor.extract_features(PhysiologicalSample(timestamp=0.0, heart_rate_bpm=110.0), baseline_hr)
    extractor.extract_features(PhysiologicalSample(timestamp=2.0, heart_rate_bpm=115.0), baseline_hr)

    # Dropping rapidly -> RECOVERING
    f_rec = extractor.extract_features(PhysiologicalSample(timestamp=5.0, heart_rate_bpm=100.0), baseline_hr)
    # Rate of change over window (timestamp 0 to 5): (100 - 110) / 5 = -2.0 bpm/s <= -0.3
    assert f_rec.recovery_state == RecoveryState.RECOVERING

    # Return near baseline -> RECOVERED
    f_end = extractor.extract_features(PhysiologicalSample(timestamp=10.0, heart_rate_bpm=72.0), baseline_hr)
    assert f_end.recovery_state == RecoveryState.RECOVERED
