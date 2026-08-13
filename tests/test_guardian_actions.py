from engine.models import RiskLevel
from guardian.actions import (
    GuardianAction,
    GuardianActionMapper,
)
from guardian.executor import GuardianActionExecutor


def test_low_risk_actions():
    payload = GuardianActionMapper.map_actions(
        RiskLevel.LOW,
        301.0,
        "Stable baseline.",
        ["STABLE_BASELINE"],
    )

    assert payload.actions == [
        GuardianAction.CONTINUE_MONITORING
    ]


def test_medium_risk_actions():
    payload = GuardianActionMapper.map_actions(
        RiskLevel.MEDIUM,
        308.0,
        "Persistent elevation.",
        ["BASELINE_DEVIATION"],
    )

    assert payload.actions == [
        GuardianAction.USER_WARNING,
        GuardianAction.CONTINUE_MONITORING,
    ]

    assert GuardianAction.GUARDIAN_NOTIFICATION not in payload.actions
    assert GuardianAction.LOCATION_SHARE not in payload.actions


def test_high_risk_actions():
    payload = GuardianActionMapper.map_actions(
        RiskLevel.HIGH,
        310.0,
        "Sustained high risk.",
        ["ESCALATION_CONFIRMED"],
    )

    assert payload.actions == [
        GuardianAction.EMERGENCY_ALERT,
        GuardianAction.GUARDIAN_NOTIFICATION,
        GuardianAction.LOCATION_SHARE,
        GuardianAction.GUARDIAN_COMMUNICATION,
        GuardianAction.INCIDENT_LOG,
    ]


def test_payload_preserves_context():
    payload = GuardianActionMapper.map_actions(
        RiskLevel.HIGH,
        310.5,
        "High risk detected.",
        ["ESCALATION_CONFIRMED"],
    )

    assert payload.timestamp == 310.5
    assert payload.explanation == "High risk detected."
    assert payload.reason_codes == ["ESCALATION_CONFIRMED"]


def test_executor_does_not_claim_external_delivery():
    payload = GuardianActionMapper.map_actions(
        RiskLevel.HIGH,
        310.0,
        "Sustained high risk.",
        ["ESCALATION_CONFIRMED"],
    )

    result = GuardianActionExecutor.execute(payload)

    assert result.actions_executed == []
    assert result.incident_logged is False
    assert all(item.status == "GENERATED" for item in result.action_statuses)
