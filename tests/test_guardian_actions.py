"""
Unit tests for Guardian Action Contract & Mapping.
"""

import pytest
from engine.models import RiskLevel
from guardian.actions import (
    GuardianActionCommand,
    GuardianActionMapper,
    GuardianActionPayload,
)


def test_action_mapping_low_risk():
    payload = GuardianActionMapper.map_actions(
        risk_level=RiskLevel.LOW,
        timestamp=10.0,
        explanation="Normal state",
        reason_codes=["STABLE_BASELINE"],
    )

    assert payload.risk_level == RiskLevel.LOW
    assert payload.actions == [GuardianActionCommand.CONTINUE_MONITORING]

    d = payload.to_dict()
    assert d["actions"] == ["CONTINUE_MONITORING"]

    reconstituted = GuardianActionPayload.from_dict(d)
    assert reconstituted.actions == [GuardianActionCommand.CONTINUE_MONITORING]


def test_action_mapping_medium_risk():
    payload = GuardianActionMapper.map_actions(
        risk_level=RiskLevel.MEDIUM,
        timestamp=12.0,
        explanation="Elevated HR",
        reason_codes=["BASELINE_DEVIATION"],
    )

    assert payload.risk_level == RiskLevel.MEDIUM
    assert payload.actions == [
        GuardianActionCommand.USER_WARNING,
        GuardianActionCommand.CONTINUE_MONITORING,
    ]


def test_action_mapping_high_risk():
    payload = GuardianActionMapper.map_actions(
        risk_level=RiskLevel.HIGH,
        timestamp=20.0,
        explanation="Panic state detected",
        reason_codes=["BASELINE_DEVIATION", "PERSISTENT_ABNORMALITY"],
    )

    assert payload.risk_level == RiskLevel.HIGH
    expected = [
        GuardianActionCommand.EMERGENCY_ALERT,
        GuardianActionCommand.GUARDIAN_NOTIFICATION,
        GuardianActionCommand.LOCATION_SHARE,
        GuardianActionCommand.GUARDIAN_COMMUNICATION,
        GuardianActionCommand.INCIDENT_LOG,
    ]
    assert payload.actions == expected

    d = payload.to_dict()
    assert len(d["actions"]) == 5
    assert "LOCATION_SHARE" in d["actions"]
    assert "EMERGENCY_ALERT" in d["actions"]
