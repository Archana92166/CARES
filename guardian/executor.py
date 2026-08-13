"""
CARES Guardian Action Executor.

The decision engine decides LOW / MEDIUM / HIGH.
This module executes the corresponding guardian workflow.

It does not make a new physiological decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from engine.models import RiskLevel
from .actions import GuardianAction, GuardianActionPayload
from .location import HardwareLocation, LocationService, ResolvedLocation


@dataclass(frozen=True)
class GuardianActionExecution:
    action_type: str
    status: str
    metadata: Dict[str, object]


@dataclass(frozen=True)
class GuardianExecutionResult:
    risk_level: RiskLevel
    actions_executed: List[str]
    action_statuses: List[GuardianActionExecution]
    location: Optional[ResolvedLocation]
    notification_message: str
    incident_logged: bool


class GuardianActionExecutor:

    @staticmethod
    def execute(
        payload: GuardianActionPayload,
        hardware_location: Optional[HardwareLocation] = None,
    ) -> GuardianExecutionResult:

        executed: List[str] = []
        action_statuses = [
            GuardianActionExecution(
                action_type=action.value,
                status="GENERATED",
                metadata={"integration": "not_configured"},
            )
            for action in payload.actions
        ]
        location: Optional[ResolvedLocation] = None
        incident_logged = False

        # --------------------------------------------------
        # LOW
        # --------------------------------------------------

        if payload.risk_level == RiskLevel.LOW:

            return GuardianExecutionResult(
                risk_level=payload.risk_level,
                actions_executed=executed,
                action_statuses=action_statuses,
                location=None,
                notification_message=(
                    "CARES: Continue monitoring. "
                    "No guardian intervention required. "
                    "Action generated; no external delivery was attempted."
                ),
                incident_logged=False,
            )

        # --------------------------------------------------
        # MEDIUM
        # --------------------------------------------------

        if payload.risk_level == RiskLevel.MEDIUM:

            return GuardianExecutionResult(
                risk_level=payload.risk_level,
                actions_executed=executed,
                action_statuses=action_statuses,
                location=None,
                notification_message=(
                    "CARES WARNING: Physiological deviation "
                    "detected. User warning action generated; "
                    "no external delivery was attempted."
                ),
                incident_logged=False,
            )

        # --------------------------------------------------
        # HIGH
        # --------------------------------------------------

        if payload.risk_level == RiskLevel.HIGH:

            # Hardware GPS is required for location sharing.
            if hardware_location is not None:

                location = LocationService.reverse_geocode(
                    hardware_location
                )

            if location is not None:

                notification = (
                    "CARES EMERGENCY ALERT\n\n"
                    f"{payload.explanation}\n\n"
                    "Location:\n"
                    f"{location.address}\n\n"
                    "Coordinates:\n"
                    f"{location.latitude:.6f}, "
                    f"{location.longitude:.6f}\n\n"
                    f"Map:\n{location.map_url}"
                    "\n\nAction generated; no guardian message was sent."
                )

            else:

                notification = (
                    "CARES EMERGENCY ALERT\n\n"
                    f"{payload.explanation}\n\n"
                    "Hardware GPS location is currently "
                    "unavailable.\n\n"
                    "Action generated; no guardian message was sent."
                )

            return GuardianExecutionResult(
                risk_level=payload.risk_level,
                actions_executed=executed,
                action_statuses=action_statuses,
                location=location,
                notification_message=notification,
                incident_logged=incident_logged,
            )

        raise ValueError(
            f"Unsupported CARES risk level: "
            f"{payload.risk_level!r}"
        )


__all__ = [
    "GuardianActionExecution",
    "GuardianActionExecutor",
    "GuardianExecutionResult",
]
