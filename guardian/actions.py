from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

from engine.models import RiskLevel


class GuardianAction(str, Enum):
    CONTINUE_MONITORING = "CONTINUE_MONITORING"
    USER_WARNING = "USER_WARNING"
    EMERGENCY_ALERT = "EMERGENCY_ALERT"
    GUARDIAN_NOTIFICATION = "GUARDIAN_NOTIFICATION"
    LOCATION_SHARE = "LOCATION_SHARE"
    GUARDIAN_COMMUNICATION = "GUARDIAN_COMMUNICATION"
    INCIDENT_LOG = "INCIDENT_LOG"


@dataclass(frozen=True)
class GuardianActionPayload:
    risk_level: RiskLevel
    timestamp: float
    explanation: str
    reason_codes: List[str]
    actions: List[GuardianAction]


class GuardianActionMapper:

    @staticmethod
    def map_actions(
        risk_level: RiskLevel,
        timestamp: float,
        explanation: str = "",
        reason_codes: Optional[List[str]] = None,
    ) -> GuardianActionPayload:

        reasons = list(reason_codes or [])

        if risk_level == RiskLevel.LOW:

            actions = [
                GuardianAction.CONTINUE_MONITORING,
            ]

        elif risk_level == RiskLevel.MEDIUM:

            actions = [
                GuardianAction.USER_WARNING,
                GuardianAction.CONTINUE_MONITORING,
            ]

        elif risk_level == RiskLevel.HIGH:

            actions = [
                GuardianAction.EMERGENCY_ALERT,
                GuardianAction.GUARDIAN_NOTIFICATION,
                GuardianAction.LOCATION_SHARE,
                GuardianAction.GUARDIAN_COMMUNICATION,
                GuardianAction.INCIDENT_LOG,
            ]

        else:
            raise ValueError(
                f"Unsupported CARES risk level: {risk_level!r}"
            )

        return GuardianActionPayload(
            risk_level=risk_level,
            timestamp=float(timestamp),
            explanation=explanation,
            reason_codes=reasons,
            actions=actions,
        )


__all__ = [
    "GuardianAction",
    "GuardianActionPayload",
    "GuardianActionMapper",
]
