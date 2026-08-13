"""
Guardian Action Mapping & Data Contract Layer for CARES.

Maps engine risk decisions into software action commands consumed by
the future UI / mobile application layer.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional
from engine.models import RiskLevel


class GuardianActionCommand(str, Enum):
    """Explicit software action commands mapped from risk decisions."""
    CONTINUE_MONITORING = "CONTINUE_MONITORING"
    USER_WARNING = "USER_WARNING"
    EMERGENCY_ALERT = "EMERGENCY_ALERT"
    GUARDIAN_NOTIFICATION = "GUARDIAN_NOTIFICATION"
    LOCATION_SHARE = "LOCATION_SHARE"
    GUARDIAN_COMMUNICATION = "GUARDIAN_COMMUNICATION"
    INCIDENT_LOG = "INCIDENT_LOG"


@dataclass
class GuardianActionPayload:
    """
    Structured action payload generated for UI consumption.
    """
    timestamp: float
    risk_level: RiskLevel
    actions: List[GuardianActionCommand]
    explanation: str
    reason_codes: List[str]
    location_payload: Dict[str, Any] = field(default_factory=lambda: {
        "status": "LOCATION_SHARE_REQUESTED_SOFTWARE_STUB",
        "latitude": None,
        "longitude": None,
    })

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": float(self.timestamp),
            "risk_level": str(self.risk_level.value if isinstance(self.risk_level, Enum) else self.risk_level),
            "actions": [str(a.value if isinstance(a, Enum) else a) for a in self.actions],
            "explanation": str(self.explanation),
            "reason_codes": list(self.reason_codes),
            "location_payload": dict(self.location_payload),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GuardianActionPayload":
        return cls(
            timestamp=float(data["timestamp"]),
            risk_level=RiskLevel(data["risk_level"]),
            actions=[GuardianActionCommand(a) for a in data["actions"]],
            explanation=str(data["explanation"]),
            reason_codes=list(data["reason_codes"]),
            location_payload=dict(data.get("location_payload", {})),
        )


class GuardianActionMapper:
    """
    Maps CARES risk levels and decision outputs into software action contracts.
    """

    @staticmethod
    def map_actions(
        risk_level: RiskLevel,
        timestamp: float,
        explanation: str,
        reason_codes: List[str],
    ) -> GuardianActionPayload:
        """
        Derives action contract list based on fixed project requirements:
        
        LOW:
        - CONTINUE_MONITORING
        
        MEDIUM:
        - USER_WARNING
        - CONTINUE_MONITORING
        
        HIGH:
        - EMERGENCY_ALERT
        - GUARDIAN_NOTIFICATION
        - LOCATION_SHARE
        - GUARDIAN_COMMUNICATION
        - INCIDENT_LOG
        """
        if risk_level == RiskLevel.LOW:
            actions = [GuardianActionCommand.CONTINUE_MONITORING]
        elif risk_level == RiskLevel.MEDIUM:
            actions = [
                GuardianActionCommand.USER_WARNING,
                GuardianActionCommand.CONTINUE_MONITORING,
            ]
        elif risk_level == RiskLevel.HIGH:
            actions = [
                GuardianActionCommand.EMERGENCY_ALERT,
                GuardianActionCommand.GUARDIAN_NOTIFICATION,
                GuardianActionCommand.LOCATION_SHARE,
                GuardianActionCommand.GUARDIAN_COMMUNICATION,
                GuardianActionCommand.INCIDENT_LOG,
            ]
        else:
            actions = [GuardianActionCommand.CONTINUE_MONITORING]

        return GuardianActionPayload(
            timestamp=timestamp,
            risk_level=risk_level,
            actions=actions,
            explanation=explanation,
            reason_codes=reason_codes,
        )
