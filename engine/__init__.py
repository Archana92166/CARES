"""
CARES Adaptive Cognitive-Risk Decision Engine Package.

This package provides baseline estimation, temporal feature extraction,
adaptive escalation state machine, and risk engine reasoning for
visually impaired cognitive assistance.
"""

from .config import CARESConfig, BaselineConfig, FeatureConfig, RiskConfig, EscalationConfig
from .models import (
    RiskLevel,
    BaselineState,
    RecoveryState,
    PhysiologicalSample,
    TemporalFeatures,
    EngineOutput,
)
from .baseline import BaselineEstimator
from .features import FeatureExtractor
from .escalation import EscalationStateMachine
from .risk_engine import CARESDecisionEngine

__all__ = [
    "CARESConfig",
    "BaselineConfig",
    "FeatureConfig",
    "RiskConfig",
    "EscalationConfig",
    "RiskLevel",
    "BaselineState",
    "RecoveryState",
    "PhysiologicalSample",
    "TemporalFeatures",
    "EngineOutput",
    "BaselineEstimator",
    "FeatureExtractor",
    "EscalationStateMachine",
    "CARESDecisionEngine",
]
