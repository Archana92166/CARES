"""
Unit tests for Escalation State Machine (Transitions, Recovery, Noise Immunity).
"""

import pytest
from engine.config import EscalationConfig
from engine.escalation import EscalationStateMachine
from engine.models import RecoveryState, RiskLevel, TemporalFeatures


def make_features(
    ts: float,
    abnormal_samples: int = 0,
    abnormal_sec: float = 0.0,
    rec_state: RecoveryState = RecoveryState.NO_RECOVERY,
) -> TemporalFeatures:
    return TemporalFeatures(
        timestamp=ts,
        current_hr=100.0,
        baseline_hr=70.0,
        abs_deviation=30.0,
        pct_deviation=42.8,
        rate_of_change=1.0,
        abnormality_persistence_samples=abnormal_samples,
        abnormality_persistence_seconds=abnormal_sec,
        is_abnormal=True,
        recovery_state=rec_state,
    )


def test_escalation_low_to_medium_to_high():
    config = EscalationConfig(
        escalate_medium_persistence_samples=3,
        escalate_high_persistence_samples=5,
        deescalate_persistence_samples=3,
        min_confidence_for_high=0.6,
    )
    sm = EscalationStateMachine(config)
    assert sm.current_state == RiskLevel.LOW

    # Sample 1: candidate MEDIUM (persistence = 1) -> Remains LOW
    state, reason = sm.update(RiskLevel.MEDIUM, make_features(1.0, abnormal_samples=1), confidence=0.8)
    assert state == RiskLevel.LOW

    # Sample 2: candidate MEDIUM (persistence = 2) -> Remains LOW
    state, reason = sm.update(RiskLevel.MEDIUM, make_features(2.0, abnormal_samples=2), confidence=0.8)
    assert state == RiskLevel.LOW

    # Sample 3: candidate MEDIUM (persistence = 3 >= 3) -> Escalates to MEDIUM
    state, reason = sm.update(RiskLevel.MEDIUM, make_features(3.0, abnormal_samples=3), confidence=0.8)
    assert state == RiskLevel.MEDIUM
    assert reason == "ESCALATION_CONFIRMED"

    # Sample 4: candidate HIGH (persistence = 4 < 5) -> Remains MEDIUM
    state, reason = sm.update(RiskLevel.HIGH, make_features(4.0, abnormal_samples=4), confidence=0.8)
    assert state == RiskLevel.MEDIUM

    # Sample 5: candidate HIGH (persistence = 5 >= 5) -> Escalates to HIGH
    state, reason = sm.update(RiskLevel.HIGH, make_features(5.0, abnormal_samples=5), confidence=0.8)
    assert state == RiskLevel.HIGH
    assert reason == "ESCALATION_CONFIRMED"


def test_transient_noise_spike_immunity():
    """
    A single isolated noisy sample must NOT trigger HIGH.
    """
    config = EscalationConfig(
        escalate_medium_persistence_samples=3,
        escalate_high_persistence_samples=5,
        instant_high_sample_count=2,
    )
    sm = EscalationStateMachine(config)
    assert sm.current_state == RiskLevel.LOW

    # Single isolated HIGH spike with extreme flag
    state, reason = sm.update(
        RiskLevel.HIGH,
        make_features(1.0, abnormal_samples=1),
        confidence=0.9,
        is_extreme_spike=True,
    )
    # Must NOT jump to HIGH instantly on 1 sample!
    assert state == RiskLevel.LOW

    # Next sample returns to LOW candidate
    state, reason = sm.update(
        RiskLevel.LOW,
        make_features(2.0, abnormal_samples=0),
        confidence=0.9,
        is_extreme_spike=False,
    )
    assert state == RiskLevel.LOW


def test_deescalation_high_to_medium_to_low():
    config = EscalationConfig(
        escalate_medium_persistence_samples=1,
        escalate_high_persistence_samples=1,
        deescalate_persistence_samples=3,
    )
    sm = EscalationStateMachine(config)

    # Fast-forward to HIGH
    sm.update(RiskLevel.MEDIUM, make_features(1.0, abnormal_samples=1), confidence=0.9)
    sm.update(RiskLevel.HIGH, make_features(2.0, abnormal_samples=1), confidence=0.9)
    assert sm.current_state == RiskLevel.HIGH

    # Candidate LOW sample 1 & 2 -> Still HIGH
    sm.update(RiskLevel.LOW, make_features(3.0, abnormal_samples=0), confidence=0.9)
    sm.update(RiskLevel.LOW, make_features(4.0, abnormal_samples=0), confidence=0.9)
    assert sm.current_state == RiskLevel.HIGH

    # Sample 3 candidate LOW -> De-escalate to MEDIUM
    state, reason = sm.update(RiskLevel.LOW, make_features(5.0, abnormal_samples=0), confidence=0.9)
    assert state == RiskLevel.MEDIUM
    assert reason == "DEESCALATION_CONFIRMED"

    # 3 more LOW candidate samples -> De-escalate to LOW
    sm.update(RiskLevel.LOW, make_features(6.0, abnormal_samples=0), confidence=0.9)
    sm.update(RiskLevel.LOW, make_features(7.0, abnormal_samples=0), confidence=0.9)
    state, reason = sm.update(RiskLevel.LOW, make_features(8.0, abnormal_samples=0), confidence=0.9)
    assert state == RiskLevel.LOW
    assert reason == "DEESCALATION_CONFIRMED"
