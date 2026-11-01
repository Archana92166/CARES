
import pytest

from backend import CARESBackend
from engine.models import EngineOutput, RiskLevel
import guardian.sms


def make_backend(tmp_path):
    return CARESBackend(db_path=str(tmp_path / "cares_sms.sqlite3"))


def make_output(risk_level, actions, timestamp):
    return EngineOutput(
        timestamp=timestamp,
        risk_level=risk_level,
        risk_score=94.5 if risk_level == RiskLevel.HIGH else 20.0,
        confidence=0.98,
        trend=1.2,
        baseline=70.0,
        current_value=100.0,
        deviation=30.0,
        pct_deviation=42.857,
        persistence=4.0,
        recovery_state="NO_RECOVERY",
        reason_codes=["TEST"],
        human_readable_explanation=f"Test risk level: {risk_level.value}",
        recommended_action=actions,
    )


def test_low_and_medium_do_not_send_sms(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "test@example.com", "password-one")
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")

    calls = []

    def fake_sms(**kwargs):
        calls.append(kwargs)
        return {"success": True, "status": "MOCK_SENT"}

    monkeypatch.setattr(guardian.sms, "send_guardian_sms", fake_sms)

    outputs = [
        make_output(RiskLevel.LOW, ["CONTINUE_MONITORING"], 310.0),
        make_output(
            RiskLevel.MEDIUM,
            ["USER_WARNING", "CONTINUE_MONITORING"],
            320.0,
        ),
    ]

    for output in outputs:
        backend.persist_engine_output(user["id"], output)

    assert calls == []


def test_first_high_simulates_guardian_sms(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "test@example.com", "password-one")
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")

    calls = []

    def fake_sms(**kwargs):
        calls.append(kwargs)
        return {"success": True, "status": "MOCK_SENT"}

    monkeypatch.setattr(guardian.sms, "send_guardian_sms", fake_sms)

    backend.persist_engine_output(
        user["id"],
        make_output(
            RiskLevel.HIGH,
            [
                "EMERGENCY_ALERT",
                "GUARDIAN_NOTIFICATION",
                "LOCATION_SHARE",
                "GUARDIAN_COMMUNICATION",
                "INCIDENT_LOG",
            ],
            310.0,
        ),
    )

    actions = backend.list_actions(user["id"])

    notification = next(
        action for action in actions
        if action["action_type"] == "GUARDIAN_NOTIFICATION"
    )

    assert len(calls) == 1
    assert notification["status"] == "MOCK_SENT"
    assert notification["metadata"]["real_sms_sent"] is False


def test_consecutive_high_does_not_send_duplicate_sms(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "test@example.com", "password-one")
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")

    calls = []

    def fake_sms(**kwargs):
        calls.append(kwargs)
        return {"success": True, "status": "MOCK_SENT"}

    monkeypatch.setattr(guardian.sms, "send_guardian_sms", fake_sms)

    high_actions = [
        "EMERGENCY_ALERT",
        "GUARDIAN_NOTIFICATION",
        "LOCATION_SHARE",
        "GUARDIAN_COMMUNICATION",
        "INCIDENT_LOG",
    ]

    backend.persist_engine_output(
        user["id"],
        make_output(RiskLevel.HIGH, high_actions, 310.0),
    )

    backend.persist_engine_output(
        user["id"],
        make_output(RiskLevel.HIGH, high_actions, 320.0),
    )

    assert len(calls) == 1


def test_recovery_then_high_sends_second_sms(tmp_path, monkeypatch):
    backend = make_backend(tmp_path)
    user = backend.register("Test User", "test@example.com", "password-one")
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")

    calls = []

    def fake_sms(**kwargs):
        calls.append(kwargs)
        return {"success": True, "status": "MOCK_SENT"}

    monkeypatch.setattr(guardian.sms, "send_guardian_sms", fake_sms)

    high_actions = [
        "EMERGENCY_ALERT",
        "GUARDIAN_NOTIFICATION",
        "LOCATION_SHARE",
        "GUARDIAN_COMMUNICATION",
        "INCIDENT_LOG",
    ]

    backend.persist_engine_output(
        user["id"],
        make_output(RiskLevel.HIGH, high_actions, 310.0),
    )

    backend.persist_engine_output(
        user["id"],
        make_output(RiskLevel.MEDIUM, ["USER_WARNING"], 320.0),
    )

    backend.persist_engine_output(
        user["id"],
        make_output(RiskLevel.HIGH, high_actions, 330.0),
    )

    assert len(calls) == 2


def test_actual_mock_sms_prints_notification(tmp_path):
    backend = make_backend(tmp_path)

    user = backend.register(
        "Test User",
        "actual-test@example.com",
        "password-one",
    )

    backend.add_guardian(
        user["id"],
        "Guardian",
        "+91 9876543210",
        "parent",
    )

    backend.persist_engine_output(
        user["id"],
        make_output(
            RiskLevel.HIGH,
            [
                "EMERGENCY_ALERT",
                "GUARDIAN_NOTIFICATION",
                "LOCATION_SHARE",
                "GUARDIAN_COMMUNICATION",
                "INCIDENT_LOG",
            ],
            410.0,
        ),
    )

    actions = backend.list_actions(user["id"])

    notification = next(
        action for action in actions
        if action["action_type"] == "GUARDIAN_NOTIFICATION"
    )

    assert notification["status"] == "MOCK_SENT"
    assert notification["metadata"]["real_sms_sent"] is False



def test_textbee_provider_sends_personalized_message_without_real_network(
    monkeypatch,
):
    import json
    from io import BytesIO

    sent = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "data": {
                    "success": True,
                    "message": "SMS added to queue for processing",
                    "smsBatchId": "batch-test-123",
                    "recipientCount": 1,
                }
            }).encode("utf-8")

    def fake_urlopen(request, timeout):
        sent["url"] = request.full_url
        sent["headers"] = dict(request.header_items())
        sent["body"] = json.loads(request.data.decode("utf-8"))
        sent["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("CARES_SMS_PROVIDER", "textbee")
    monkeypatch.setenv("CARES_ALLOW_SMS_IN_TESTS", "true")
    monkeypatch.setenv("TEXTBEE_API_KEY", "test-key-not-real")
    monkeypatch.setenv("TEXTBEE_DEVICE_ID", "test-device-id")
    monkeypatch.setattr(guardian.sms, "urlopen", fake_urlopen)

    result = guardian.sms.send_guardian_sms(
        phone_number="+91 9876543210",
        heart_rate=112,
        user_name="CARES Demo User",
        latitude=12.2958,
        longitude=76.6394,
        location_address="Mysuru, Karnataka",
        timestamp=1793424000,
    )

    assert sent["url"] == guardian.sms.TEXTBEE_SEND_URL
    assert sent["body"]["recipients"] == ["+919876543210"]
    assert sent["body"]["message"].startswith("CARES EMERGENCY ALERT")
    assert "User: CARES Demo User" in sent["body"]["message"]
    assert "Risk Level: HIGH" in sent["body"]["message"]
    assert "Heart Rate: 112.0 BPM" in sent["body"]["message"]
    assert "Mysuru, Karnataka" in sent["body"]["message"]
    assert "https://www.google.com/maps?q=12.2958,76.6394" in sent["body"]["message"]
    assert sent["body"]["deviceId"] == "test-device-id"
    assert result["success"] is True
    assert result["status"] == "PENDING"
    assert result["sms_batch_id"] == "batch-test-123"
    assert result["real_sms_sent"] is False


def test_textbee_provider_requires_api_key(monkeypatch):
    monkeypatch.setenv("CARES_SMS_PROVIDER", "textbee")
    monkeypatch.setenv("CARES_ALLOW_SMS_IN_TESTS", "true")
    monkeypatch.delenv("TEXTBEE_API_KEY", raising=False)

    result = guardian.sms.send_guardian_sms(
        phone_number="+919876543210",
        heart_rate=100,
        user_name="Test User",
    )

    assert result["success"] is False
    assert result["status"] == "FAILED"
    assert "TEXTBEE_API_KEY" in result["reason"]


def test_default_provider_remains_mock(monkeypatch):
    monkeypatch.delenv("CARES_SMS_PROVIDER", raising=False)
    monkeypatch.delenv("TEXTBEE_API_KEY", raising=False)

    result = guardian.sms.send_guardian_sms(
        phone_number="+919876543210",
        heart_rate=100,
        user_name="Test User",
    )

    assert result["success"] is True
    assert result["status"] == "MOCK_SENT"
    assert result["real_sms_sent"] is False



def test_high_risk_backend_queues_textbee_alert_automatically(
    tmp_path, monkeypatch,
):
    import json

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "data": {
                    "success": True,
                    "smsBatchId": "automatic-high-batch",
                    "recipientCount": 1,
                }
            }).encode("utf-8")

    sent_requests = []

    def fake_urlopen(request, timeout):
        sent_requests.append(json.loads(request.data.decode("utf-8")))
        return FakeResponse()

    monkeypatch.setenv("CARES_SMS_PROVIDER", "textbee")
    monkeypatch.setenv("CARES_ALLOW_SMS_IN_TESTS", "true")
    monkeypatch.setenv("TEXTBEE_API_KEY", "test-key-not-real")
    monkeypatch.delenv("TEXTBEE_DEVICE_ID", raising=False)
    monkeypatch.setattr(guardian.sms, "urlopen", fake_urlopen)

    backend = make_backend(tmp_path)
    user = backend.register("Ramya Test", "ramya-test@example.com", "password-one")
    backend.add_guardian(user["id"], "Guardian", "+91 9876543210", "parent")

    backend.persist_engine_output(
        user["id"],
        make_output(
            RiskLevel.HIGH,
            [
                "EMERGENCY_ALERT",
                "GUARDIAN_NOTIFICATION",
                "LOCATION_SHARE",
                "GUARDIAN_COMMUNICATION",
                "INCIDENT_LOG",
            ],
            1793424000.0,
        ),
    )

    notification = next(
        action for action in backend.list_actions(user["id"])
        if action["action_type"] == "GUARDIAN_NOTIFICATION"
    )

    assert len(sent_requests) == 1
    assert sent_requests[0]["recipients"] == ["+919876543210"]
    assert "User: Ramya Test" in sent_requests[0]["message"]
    assert notification["status"] == "PENDING"
    assert notification["metadata"]["delivery_mode"] == "TEXTBEE"
    assert notification["metadata"]["provider_accepted"] is True
    assert notification["metadata"]["delivery_confirmed"] is False
    assert notification["metadata"]["recipients"][0]["sms_batch_id"] == "automatic-high-batch"
