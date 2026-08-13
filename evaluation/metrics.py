"""
Evaluation Metrics for CARES Research Experiments.

Computes quantitative metrics for comparing CARES Decision Engine performance
against baseline decision models.
"""

from typing import Dict, List, Sequence
from engine.models import EngineOutput, RiskLevel


class EvaluationMetrics:
    """
    Computes comparative performance and stability metrics.
    """

    @staticmethod
    def compute_false_positive_rate_on_spikes(
        risk_levels: Sequence[RiskLevel],
    ) -> float:
        """
        Calculates percentage of false positive HIGH/MEDIUM risk decisions
        during transient artifact spikes.
        """
        if not risk_levels:
            return 0.0

        false_positives = sum(1 for r in risk_levels if r in (RiskLevel.HIGH, RiskLevel.MEDIUM))
        return false_positives / len(risk_levels)

    @staticmethod
    def count_state_oscillations(risk_levels: Sequence[RiskLevel]) -> int:
        """
        Counts rapid state changes (churn) in a decision stream.
        High oscillation counts indicate unstable/noisy decision behavior.
        """
        if len(risk_levels) < 2:
            return 0

        oscillations = 0
        for i in range(1, len(risk_levels)):
            if risk_levels[i] != risk_levels[i - 1]:
                oscillations += 1
        return oscillations

    @staticmethod
    def compute_time_to_first_escalation(
        outputs: Sequence[EngineOutput],
        target_level: RiskLevel = RiskLevel.HIGH,
        anomaly_start_time: float = 0.0,
    ) -> float:
        """
        Computes latency (seconds) from anomaly onset until engine escalates to target level.
        Returns -1.0 if target level was never reached.
        """
        for out in outputs:
            if out.timestamp >= anomaly_start_time and out.risk_level == target_level:
                return out.timestamp - anomaly_start_time
        return -1.0
