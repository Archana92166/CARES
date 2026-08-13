"""
Data Models for CARES Adaptive Cognitive-Risk Decision Engine.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np

class RiskLevel(str, Enum):
    """Risk severity levels."""
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class BaselineState(str, Enum):
    """Personal physiological baseline calibration states."""
    NOT_READY = "NOT_READY"
    CALIBRATING = "CALIBRATING"
    READY = "READY"


class RecoveryState(str, Enum):
    """Recovery behavior states."""
    NO_RECOVERY = "NO_RECOVERY"
    RECOVERING = "RECOVERING"
    RECOVERED = "RECOVERED"


@dataclass
class PhysiologicalSample:
    """
    Validated physiological measurement sample.

    Signal validity semantics
    -------------------------
    hr_valid:
        1.0 = HR was directly estimated from sufficiently good signal.
        0.0 = HR is unavailable/untrusted/held from a previous estimate.

    bvp_quality:
        Signal-quality score in the range 0.0-1.0 when available.

    A held HR value may be carried through the pipeline for continuity,
    but hr_valid=0 means it is NOT physiological evidence and must not
    update baseline, temporal evidence, or risk escalation.
    """

    timestamp: float
    heart_rate_bpm: float
    additional_metrics: Dict[str, float] = field(default_factory=dict)

    @property
    def hr_valid(self) -> bool:
        """
        Whether this HR value is trusted physiological evidence.

        For backwards compatibility, samples without an explicit
        hr_valid field are considered valid.
        """
        return bool(
            self.additional_metrics.get("hr_valid", 1.0) >= 1.0
        )

    @property
    def bvp_quality(self) -> float:
        """
        BVP signal quality in [0, 1].

        Missing quality defaults to 1.0 for legacy/synthetic samples.
        Real wrist-BVP samples should provide the estimator quality.
        """
        return float(
            np.clip(
                self.additional_metrics.get("bvp_quality", 1.0),
                0.0,
                1.0,
            )
        )

    def validate(self) -> None:
        """Validates sample field bounds."""

        if not isinstance(self.timestamp, (int, float)) or self.timestamp < 0:
            raise ValueError(
                f"Invalid timestamp: {self.timestamp}. "
                "Must be non-negative number."
            )

        if not isinstance(self.heart_rate_bpm, (int, float)):
            raise ValueError(
                f"Invalid heart rate type: "
                f"{type(self.heart_rate_bpm)}. Must be numeric."
            )

        if self.heart_rate_bpm < 30.0 or self.heart_rate_bpm > 220.0:
            raise ValueError(
                "Heart rate out of physiological plausible bounds "
                f"(30-220 bpm): {self.heart_rate_bpm}"
            )

        hr_valid = self.additional_metrics.get("hr_valid", 1.0)

        if not isinstance(hr_valid, (int, float)):
            raise ValueError("hr_valid must be numeric.")

        if hr_valid not in (0, 1, 0.0, 1.0):
            raise ValueError(
                f"hr_valid must be 0 or 1, got {hr_valid}"
            )

        quality = self.additional_metrics.get("bvp_quality", 1.0)

        if not isinstance(quality, (int, float)):
            raise ValueError("bvp_quality must be numeric.")

        if not 0.0 <= float(quality) <= 1.0:
            raise ValueError(
                f"bvp_quality must be between 0 and 1, got {quality}"
            )

    def __post_init__(self) -> None:
        self.validate()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": float(self.timestamp),
            "heart_rate_bpm": float(self.heart_rate_bpm),
            "additional_metrics": {
                k: float(v)
                for k, v in self.additional_metrics.items()
            },
        }

    @classmethod
    def from_dict(
        cls,
        data: Dict[str, Any],
    ) -> "PhysiologicalSample":
        return cls(
            timestamp=float(data["timestamp"]),
            heart_rate_bpm=float(data["heart_rate_bpm"]),
            additional_metrics=data.get(
                "additional_metrics",
                {},
            ),
        )


@dataclass
class TemporalFeatures:
    """Mathematically derived temporal physiological features."""
    timestamp: float
    current_hr: float
    baseline_hr: float
    abs_deviation: float
    pct_deviation: float
    rate_of_change: float  # bpm/sec
    abnormality_persistence_samples: int
    abnormality_persistence_seconds: float
    is_abnormal: bool
    recovery_state: RecoveryState

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": float(self.timestamp),
            "current_hr": float(self.current_hr),
            "baseline_hr": float(self.baseline_hr),
            "abs_deviation": float(self.abs_deviation),
            "pct_deviation": float(self.pct_deviation),
            "rate_of_change": float(self.rate_of_change),
            "abnormality_persistence_samples": int(self.abnormality_persistence_samples),
            "abnormality_persistence_seconds": float(self.abnormality_persistence_seconds),
            "is_abnormal": bool(self.is_abnormal),
            "recovery_state": str(self.recovery_state.value if isinstance(self.recovery_state, Enum) else self.recovery_state),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TemporalFeatures":
        return cls(
            timestamp=float(data["timestamp"]),
            current_hr=float(data["current_hr"]),
            baseline_hr=float(data["baseline_hr"]),
            abs_deviation=float(data["abs_deviation"]),
            pct_deviation=float(data["pct_deviation"]),
            rate_of_change=float(data["rate_of_change"]),
            abnormality_persistence_samples=int(data["abnormality_persistence_samples"]),
            abnormality_persistence_seconds=float(data["abnormality_persistence_seconds"]),
            is_abnormal=bool(data["is_abnormal"]),
            recovery_state=RecoveryState(data["recovery_state"]),
        )


@dataclass
class EngineOutput:
    """
    Serializable output of the CARES Cognitive-Risk Decision Engine.
    
    Contains explicit risk assessment, evidence confidence, trend, baseline,
    temporal deviation metrics, explainable reason codes, and software action recommendations.
    """
    timestamp: float
    risk_level: RiskLevel
    risk_score: float  # Continuous 0.0 - 100.0
    confidence: float  # 0.0 - 1.0
    trend: float  # Rate of change bpm/sec
    baseline: float
    current_value: float
    deviation: float  # Absolute deviation
    pct_deviation: float
    persistence: float  # Seconds of persistent abnormality
    recovery_state: str
    reason_codes: List[str]
    human_readable_explanation: str
    recommended_action: List[str]  # List of software action commands

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": float(self.timestamp),
            "risk_level": str(self.risk_level.value if isinstance(self.risk_level, Enum) else self.risk_level),
            "risk_score": round(float(self.risk_score), 2),
            "confidence": round(float(self.confidence), 3),
            "trend": round(float(self.trend), 3),
            "baseline": round(float(self.baseline), 2),
            "current_value": round(float(self.current_value), 2),
            "deviation": round(float(self.deviation), 2),
            "pct_deviation": round(float(self.pct_deviation), 2),
            "persistence": round(float(self.persistence), 2),
            "recovery_state": str(self.recovery_state),
            "reason_codes": list(self.reason_codes),
            "human_readable_explanation": str(self.human_readable_explanation),
            "recommended_action": list(self.recommended_action),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EngineOutput":
        return cls(
            timestamp=float(data["timestamp"]),
            risk_level=RiskLevel(data["risk_level"]),
            risk_score=float(data["risk_score"]),
            confidence=float(data["confidence"]),
            trend=float(data["trend"]),
            baseline=float(data["baseline"]),
            current_value=float(data["current_value"]),
            deviation=float(data["deviation"]),
            pct_deviation=float(data["pct_deviation"]),
            persistence=float(data["persistence"]),
            recovery_state=str(data["recovery_state"]),
            reason_codes=list(data["reason_codes"]),
            human_readable_explanation=str(data["human_readable_explanation"]),
            recommended_action=list(data["recommended_action"]),
        )
