"""
CARES Risk Engine Configuration Module.

IMPORTANT ARCHITECTURE NOTE
---------------------------

This configuration supports the existing CARES Decision Engine and its
offline WESAD evaluation pipeline.

The newer PersonalBaselineManager is a separate real-time learning layer
responsible for:

    - 300-second initial personal calibration
    - 3-second adaptation evidence
    - daily baseline learning
    - long-term baseline learning
    - audit logging

WESAD is used for offline evaluation only.

The numerical risk thresholds below are empirical/system parameters used
for the CARES research evaluation. They are NOT clinically validated
medical thresholds.
"""

from dataclasses import dataclass, field


@dataclass
class BaselineConfig:
    """
    Configuration for the existing sample-based CARES baseline estimator.

    These fields are retained for compatibility with the original
    CARES Decision Engine and its tests.

    The production real-time personal baseline protocol is implemented
    separately by PersonalBaselineManager.
    """

    # Existing CARES engine calibration contract
    window_samples: int = 30
    min_samples_required: int = 10

    # Maximum retained calibration history
    max_history_samples: int = 100

    # Statistical protection
    std_outlier_multiplier: float = 3.0

    # Slow adaptation used by the legacy/sample-based estimator
    adaptive_update_alpha: float = 0.01


@dataclass
class FeatureConfig:
    """Configuration for temporal physiological feature extraction."""

    short_term_window_samples: int = 5

    # Reserved for future EMA-based trend processing
    trend_ema_alpha: float = 0.3

    # Persistence threshold relative to personal baseline
    persistence_deviation_threshold_bpm: float = 10.0

    # Recovery trend threshold
    recovery_rate_threshold_bpm_per_sec: float = -0.3

    # Recovery observation window
    recovery_window_samples: int = 5


@dataclass
class RiskConfig:
    """
    Configuration for continuous CARES risk scoring.

    These values are empirical/system decision parameters derived from
    WESAD training distributions in the evaluation pipeline.

    They are NOT clinically validated medical thresholds.
    """

    # Medium deviation
    medium_deviation_bpm: float = 12.5

    # High deviation
    high_deviation_bpm: float = 25.0

    # Medium percentage deviation
    medium_pct_deviation: float = 16.5

    # High percentage deviation
    high_pct_deviation: float = 32.0

    # Rapid HR change
    rapid_change_rate_bpm_per_sec: float = 1.2

    # Extreme instantaneous deviation
    extreme_spike_threshold_bpm: float = 40.0


@dataclass
class EscalationConfig:
    """Configuration for CARES adaptive risk escalation."""

    # LOW → MEDIUM
    escalate_medium_persistence_samples: int = 3

    # MEDIUM → HIGH
    escalate_high_persistence_samples: int = 5

    # HIGH/MEDIUM → lower state
    deescalate_persistence_samples: int = 5

    # Minimum evidence confidence required for HIGH
    min_confidence_for_high: float = 0.6

    # Extreme spikes still require consecutive evidence
    instant_high_sample_count: int = 2


@dataclass
class CARESConfig:
    """Central configuration container for the CARES Decision Engine."""

    baseline: BaselineConfig = field(
        default_factory=BaselineConfig
    )

    feature: FeatureConfig = field(
        default_factory=FeatureConfig
    )

    risk: RiskConfig = field(
        default_factory=RiskConfig
    )

    escalation: EscalationConfig = field(
        default_factory=EscalationConfig
    )

    def to_dict(self) -> dict:
        """Return the complete configuration as a dictionary."""

        return {
            "baseline": self.baseline.__dict__.copy(),
            "feature": self.feature.__dict__.copy(),
            "risk": self.risk.__dict__.copy(),
            "escalation": self.escalation.__dict__.copy(),
        }