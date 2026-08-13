"""
Adaptive Escalation State Machine for CARES Engine.

Prevents single isolated spikes from causing false alarms by enforcing
temporal evidence, persistence, and confidence thresholds before escalating.
Also manages structured de-escalation when physiological values return toward baseline.
"""

from typing import List, Optional, Tuple
from .config import EscalationConfig
from .models import RiskLevel, TemporalFeatures


class EscalationStateMachine:
    """
    Manages risk state transitions (LOW, MEDIUM, HIGH) with temporal hysteresis.
    """

    def __init__(self, config: Optional[EscalationConfig] = None) -> None:
        self.config: EscalationConfig = config or EscalationConfig()
        self._current_state: RiskLevel = RiskLevel.LOW
        self._candidate_counts: dict = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 0, RiskLevel.HIGH: 0}
        self._consecutive_deescalation_samples: int = 0
        self._consecutive_instant_high_samples: int = 0
        self._transition_history: List[Tuple[float, RiskLevel, RiskLevel, str]] = []

    @property
    def current_state(self) -> RiskLevel:
        return self._current_state

    @property
    def transition_history(self) -> List[Tuple[float, RiskLevel, RiskLevel, str]]:
        return list(self._transition_history)

    def reset(self) -> None:
        self._current_state = RiskLevel.LOW
        self._candidate_counts = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 0, RiskLevel.HIGH: 0}
        self._consecutive_deescalation_samples = 0
        self._consecutive_instant_high_samples = 0
        self._transition_history.clear()

    def update(
        self,
        candidate_level: RiskLevel,
        features: TemporalFeatures,
        confidence: float,
        is_extreme_spike: bool = False,
    ) -> Tuple[RiskLevel, Optional[str]]:
        """
        Updates escalation state machine given candidate level and features.
        
        Returns:
            (new_state, transition_reason_code_if_changed)
        """
        old_state = self._current_state

        # Update candidate counts
        for lvl in RiskLevel:
            if lvl == candidate_level:
                self._candidate_counts[lvl] += 1
            else:
                self._candidate_counts[lvl] = 0

        transition_reason: Optional[str] = None

        if is_extreme_spike:
            self._consecutive_instant_high_samples += 1
        else:
            self._consecutive_instant_high_samples = 0

        # State transition logic
        if self._current_state == RiskLevel.LOW:
            if candidate_level in (RiskLevel.MEDIUM, RiskLevel.HIGH):
                # Check extreme spike rule (requires instant_high_sample_count consecutive extreme samples)
                if (
                    candidate_level == RiskLevel.HIGH
                    and self._consecutive_instant_high_samples >= self.config.instant_high_sample_count
                    and confidence >= self.config.min_confidence_for_high
                ):
                    self._current_state = RiskLevel.HIGH
                    transition_reason = "EXTREME_SPIKE_ESCALATION"
                elif (
                    features.abnormality_persistence_samples >= self.config.escalate_medium_persistence_samples
                ):
                    self._current_state = RiskLevel.MEDIUM
                    transition_reason = "ESCALATION_CONFIRMED"

        elif self._current_state == RiskLevel.MEDIUM:
            # Escalation to HIGH
            if (
                candidate_level == RiskLevel.HIGH
                and features.abnormality_persistence_samples >= self.config.escalate_high_persistence_samples
                and confidence >= self.config.min_confidence_for_high
            ):
                self._current_state = RiskLevel.HIGH
                transition_reason = "ESCALATION_CONFIRMED"
            # De-escalation to LOW
            elif candidate_level == RiskLevel.LOW:
                self._consecutive_deescalation_samples += 1
                if self._consecutive_deescalation_samples >= self.config.deescalate_persistence_samples:
                    self._current_state = RiskLevel.LOW
                    transition_reason = "DEESCALATION_CONFIRMED"
            else:
                self._consecutive_deescalation_samples = 0

        elif self._current_state == RiskLevel.HIGH:
            # De-escalation to MEDIUM
            if candidate_level in (RiskLevel.LOW, RiskLevel.MEDIUM):
                self._consecutive_deescalation_samples += 1
                if self._consecutive_deescalation_samples >= self.config.deescalate_persistence_samples:
                    self._current_state = RiskLevel.MEDIUM
                    transition_reason = "DEESCALATION_CONFIRMED"
                    self._consecutive_deescalation_samples = 0
            else:
                self._consecutive_deescalation_samples = 0

        if self._current_state != old_state and transition_reason:
            self._transition_history.append((features.timestamp, old_state, self._current_state, transition_reason))

        return self._current_state, transition_reason
