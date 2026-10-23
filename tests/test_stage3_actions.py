import pytest
import base64
import hashlib
import hmac

from engine.models import EngineOutput, RiskLevel

from backend import CARESBackend, Database
from backend.action_providers import (
    ProviderError, ProviderResult, TwilioSmsProvider, configured_live_providers,
)
from backend.services import AuthenticationError, ConflictError, NotFoundError


class FakeProvider:
    def __init__(self, result=None, error=None):
        self.result = result or ProviderResult("SUBMITTED", "provider-ref")
        self.error = error
        self.calls = []

    def submit(self, recipient, body, idempotency_key):
        self.calls.append((recipient, body, idempotency_key))
        if self.error:
            raise self.error
        return self.result


def high_output():
    return EngineOutput(
        timestamp=100.0, risk_level=RiskLevel.HIGH, risk_score=95.0, confidence=0.9,
        trend=1.0, baseline=70.0, current_value=105.0, deviation=35.0,
        pct_deviation=50.0, persistence=4.0, recovery_state="NO_RECOVERY",
        reason_codes=["ESCALATION_CONFIRMED"],
        human_readable_explanation="High risk.",
        recommended_action=["EMERGENCY_ALERT", "GUARDIAN_NOTIFICATION",
                            "LOCATION_SHARE", "GUARDIAN_COMMUNICATION", "INCIDENT_LOG"],
    )

def make_backend(sms=None, voice=None, **kwargs):
    backend = CARESBackend(
        database=Database(":memory:"), sms_provider=sms, voice_provider=voice,
        enable_live_actions=True, emergency_action_enabled=False, **kwargs
    )
    user = backend.register("Action User", "action@example.com", "password-one")
    return backend, user



def test_live_providers_are_disabled_without_explicit_configuration(monkeypatch):
    for name in (
        "CARES_ENABLE_LIVE_ACTIONS", "CARES_SMS_PROVIDER", "CARES_SMS_ACCOUNT_SID",
        "CARES_SMS_AUTH_TOKEN", "CARES_SMS_FROM", "CARES_SMS_MESSAGING_SERVICE_SID",
        "CARES_VOICE_PROVIDER", "CARES_VOICE_ACCOUNT_SID", "CARES_VOICE_AUTH_TOKEN",
        "CARES_VOICE_FROM",
    ):
        monkeypatch.delenv(name, raising=False)
    sms, voice = configured_live_providers()
    assert sms is None
    assert voice is None

def test_sms_and_voice_execution_is_recipient_specific_and_idempotent():
    sms = FakeProvider()
    voice = FakeProvider(ProviderResult("SUBMITTED", "call-ref"))
    backend, user = make_backend(sms, voice)
    backend.add_guardian(user["id"], "One", "+15550000001", "parent")
    backend.add_guardian(user["id"], "Two", "+15550000002", "sibling")
    backend.persist_engine_output(user["id"], high_output())
    assert {item["status"] for item in backend.list_actions(user["id"])} >= {"QUEUED", "GENERATED"}

    backend.process_pending_actions_once()
    backend.process_pending_actions_once()
    assert len(sms.calls) == 2
    assert len(voice.calls) == 2
    sms_action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_NOTIFICATION")
    assert sms_action["status"] == "SUBMITTED"
    assert len(sms_action["attempts"]) == 2
    assert all("+1555" not in str(item.get("metadata")) for item in sms_action["attempts"])

def test_provider_timeout_is_unknown_and_not_automatically_retried():
    sms = FakeProvider(error=ProviderError("ambiguous", ambiguous=True))
    backend, user = make_backend(sms, None)
    backend.add_guardian(user["id"], "One", "+15550000001", "parent")
    backend.persist_engine_output(user["id"], high_output())
    backend.process_pending_actions_once()
    backend.process_pending_actions_once()
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_NOTIFICATION")
    assert action["status"] == "UNKNOWN"
    assert len(sms.calls) == 1
    assert action["attempts"][0]["status"] == "UNKNOWN"

def test_provider_transient_failure_is_bounded_and_recoverable():
    sms = FakeProvider(error=ProviderError("temporary", retryable=True))
    backend, user = make_backend(sms, None, max_action_retries=1, action_retry_backoff_seconds=1)
    backend.add_guardian(user["id"], "One", "+15550000001", "parent")
    backend.persist_engine_output(user["id"], high_output())
    backend.process_pending_actions_once()
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_NOTIFICATION")
    assert action["status"] == "QUEUED"
    assert action["attempts"][0]["status"] == "RETRY_WAIT"
    action["attempts"][0]["next_attempt_at"] = None
    backend.database.execute("UPDATE action_attempts SET next_attempt_at = NULL WHERE id = ?", (action["attempts"][0]["id"],))
    backend.process_pending_actions_once()
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_NOTIFICATION")
    assert action["status"] == "FAILED"
    assert len(sms.calls) == 2

def test_missing_emergency_configuration_fails_without_fake_success():
    backend, user = make_backend(None, None)
    backend.persist_engine_output(user["id"], high_output())
    backend.process_pending_actions_once()
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "EMERGENCY_ALERT")
    assert action["status"] == "FAILED"
    assert "disabled" in action["metadata"]["failure_reason"]


def test_twilio_adapter_builds_real_submission_without_network(monkeypatch):
    import backend.action_providers as providers

    class Response:
        status = 201
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, _limit):
            return b'{"sid":"SM-test","status":"accepted"}'

    seen = {}
    def fake_urlopen(request, timeout):
        seen["request"] = request
        seen["timeout"] = timeout
        return Response()

    monkeypatch.setattr(providers, "urlopen", fake_urlopen)
    provider = providers.TwilioSmsProvider("AC-test", "secret", from_number="+15550000000")
    result = provider.submit("+15550000001", "CARES test", "cares-action-test")
    assert result.status == "SUBMITTED"
    assert result.provider_reference == "SM-test"
    assert seen["request"].get_header("Idempotency-key") is None
    assert b"Body=CARES+test" in seen["request"].data


def test_signed_twilio_delivery_callback_updates_persisted_status():
    provider = TwilioSmsProvider("AC-test", "callback-secret", from_number="+15550000000")
    backend, user = make_backend(provider, None)
    backend.add_guardian(user["id"], "One", "+15550000001", "parent")
    backend.persist_engine_output(user["id"], high_output())
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_NOTIFICATION")
    attempt = backend._ensure_action_attempt(action["id"], 1, "sms")
    backend._update_action_attempt(int(attempt["id"]), status="SUBMITTED", attempt_count=1, provider_reference="SM-callback")
    callback_url = "https://backend.example/api/provider/twilio/status"
    data = {"MessageSid": "SM-callback", "MessageStatus": "delivered"}
    canonical = callback_url + "".join(f"{key}{data[key]}" for key in sorted(data))
    signature = base64.b64encode(hmac.new(b"callback-secret", canonical.encode(), hashlib.sha1).digest()).decode()
    backend.process_twilio_status(data, {"X-Twilio-Signature": signature}, callback_url)
    action = backend.get_action(user["id"], action["id"])
    assert action["status"] == "DELIVERED"
    assert action["attempts"][0]["provider_reference"] == "SM-callback"


def test_repeated_high_output_reuses_one_open_incident_and_action_set():
    sms = FakeProvider()
    backend, user = make_backend(sms, None)
    backend.add_guardian(user["id"], "One", "+15550000001", "parent")
    backend.persist_engine_output(user["id"], high_output())
    backend.persist_engine_output(user["id"], high_output())
    assert len(backend.list_incidents(user["id"])) == 1
    assert len(backend.list_actions(user["id"])) == 5
    backend.process_pending_actions_once()
    backend.process_pending_actions_once()
    assert len(sms.calls) == 1

def test_location_freshness_controls_outbound_alert_content():
    clock = [100.0]
    sms = FakeProvider()
    backend, user = make_backend(sms, None, hardware_clock=lambda: clock[0])
    backend.register_hardware_device(user["id"], "esp32-test", "device-secret")
    packet = {
        "protocol_version": "1.0",
        "device_id": "esp32-test",
        "connection_id": "wifi-a",
        "sequence": 1,
        "device_timestamp_start": 100.0,
        "sampling_rate_hz": 64.0,
        "bvp_samples": [0.1],
        "location": {"latitude": 12.3, "longitude": 45.6, "timestamp": 100.0},
    }
    backend.ingest_hardware_packet(
        packet, {"X-CARES-Device-Id": "esp32-test", "X-CARES-Device-Token": "device-secret"}
    )
    backend.persist_engine_output(user["id"], high_output())
    action = next(
        item for item in backend.list_actions(user["id"])
        if item["action_type"] == "GUARDIAN_NOTIFICATION"
    )
    assert "12.300000, 45.600000" in backend._alert_message(action)
    clock[0] = 221.0
    assert "location is unavailable" in backend._alert_message(action)


def test_invalid_low_output_does_not_resolve_open_high_incident():
    from engine.models import EngineOutput
    sms = FakeProvider()
    backend, user = make_backend(sms, None)
    backend.persist_engine_output(user["id"], high_output())
    invalid = EngineOutput(
        timestamp=101.0, risk_level=RiskLevel.LOW, risk_score=0.0, confidence=0.0,
        trend=0.0, baseline=0.0, current_value=70.0, deviation=0.0, pct_deviation=0.0,
        persistence=0.0, recovery_state="NO_RECOVERY",
        reason_codes=["INVALID_OR_LOW_QUALITY_SAMPLE"],
        human_readable_explanation="Invalid sample.",
        recommended_action=["CONTINUE_MONITORING"],
    )
    backend.persist_engine_output(user["id"], invalid)
    assert backend.list_incidents(user["id"])[0]["status"] == "OPEN"


def _make_file_backend(path, provider):
    backend = CARESBackend(
        db_path=str(path), sms_provider=provider, enable_live_actions=True,
        emergency_action_enabled=False, action_retry_backoff_seconds=1,
    )
    user = backend.register("Recovery User", "recovery@example.com", "password-one")
    backend.add_guardian(user["id"], "One", "+15550000001", "parent")
    backend.persist_engine_output(user["id"], high_output())
    return backend, user

def test_restart_recovers_pending_action_and_executes_once(tmp_path):
    first_provider = FakeProvider()
    first, user = _make_file_backend(tmp_path / "recovery.sqlite3", first_provider)
    first.close()
    second_provider = FakeProvider()
    second = CARESBackend(db_path=str(tmp_path / "recovery.sqlite3"), sms_provider=second_provider, enable_live_actions=True)
    second.process_pending_actions_once()
    assert len(second_provider.calls) == 1
    second.close()

def test_restart_requeues_claim_that_crashed_before_provider_contact(tmp_path):
    provider = FakeProvider()
    backend, user = _make_file_backend(tmp_path / "claim.sqlite3", provider)
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_NOTIFICATION")
    backend._set_action_status(action["id"], user["id"], "PROCESSING")
    backend.database.execute(
        "UPDATE guardian_action_events SET metadata = ? WHERE id = ?",
        ('{"processing_started_at":"2000-01-01T00:00:00+00:00"}', action["id"]),
    )
    backend.close()
    recovered_provider = FakeProvider()
    recovered = CARESBackend(db_path=str(tmp_path / "claim.sqlite3"), sms_provider=recovered_provider, enable_live_actions=True)
    assert next(item for item in recovered.list_actions(user["id"]) if item["id"] == action["id"])["status"] == "QUEUED"
    recovered.process_pending_actions_once()
    assert len(recovered_provider.calls) == 1
    recovered.close()

def test_restart_marks_inflight_provider_submission_unknown_without_retry(tmp_path):
    provider = FakeProvider()
    backend, user = _make_file_backend(tmp_path / "unknown.sqlite3", provider)
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_NOTIFICATION")
    backend._set_action_status(action["id"], user["id"], "PROCESSING")
    attempt = backend._ensure_action_attempt(action["id"], 1, "sms")
    backend._update_action_attempt(int(attempt["id"]), status="SUBMITTING", attempt_count=1, requested_at="now")
    backend.close()
    recovered_provider = FakeProvider()
    recovered = CARESBackend(db_path=str(tmp_path / "unknown.sqlite3"), sms_provider=recovered_provider, enable_live_actions=True)
    action = recovered.get_action(user["id"], action["id"])
    assert action["status"] == "UNKNOWN"
    assert action["attempts"][0]["status"] == "UNKNOWN"
    recovered.process_pending_actions_once()
    assert recovered_provider.calls == []
    recovered.close()

def test_two_workers_atomically_claim_one_action(tmp_path):
    import threading
    provider = FakeProvider()
    first, user = _make_file_backend(tmp_path / "concurrent.sqlite3", provider)
    second = CARESBackend(db_path=str(tmp_path / "concurrent.sqlite3"), sms_provider=provider, enable_live_actions=True)
    threads = [threading.Thread(target=backend.process_pending_actions_once) for backend in (first, second)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert len(provider.calls) == 1
    first.close(); second.close()

def test_callback_signature_and_status_transition_validation():
    provider = TwilioSmsProvider("AC-test", "callback-secret", from_number="+15550000000")
    callback_url = "https://backend.example/api/provider/twilio/status"
    data = {"MessageSid": "SM-callback", "MessageStatus": "delivered"}
    canonical = callback_url + "".join(f"{key}{data[key]}" for key in sorted(data))
    import base64, hashlib, hmac
    signature = base64.b64encode(hmac.new(b"callback-secret", canonical.encode(), hashlib.sha1).digest()).decode()
    assert provider.verify_callback(callback_url, data, signature)
    assert not provider.verify_callback(callback_url, data, "invalid")
    assert not provider.verify_callback(callback_url + "?changed=1", data, signature)
    backend, user = make_backend(provider, None)
    backend.add_guardian(user["id"], "One", "+15550000001", "parent")
    backend.persist_engine_output(user["id"], high_output())
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_NOTIFICATION")
    attempt = backend._ensure_action_attempt(action["id"], 1, "sms")
    backend._update_action_attempt(int(attempt["id"]), status="SUBMITTED", attempt_count=1, provider_reference="SM-callback")
    with pytest.raises(AuthenticationError):
        backend.process_twilio_status(data, {}, callback_url)
    with pytest.raises(AuthenticationError):
        backend.process_twilio_status({**data, "MessageStatus":"failed"}, {"X-Twilio-Signature":signature}, callback_url)
    with pytest.raises(NotFoundError):
        unknown = {"MessageSid":"SM-unknown", "MessageStatus":"delivered"}
        text = callback_url + "".join(f"{key}{unknown[key]}" for key in sorted(unknown))
        sig = base64.b64encode(hmac.new(b"callback-secret", text.encode(), hashlib.sha1).digest()).decode()
        backend.process_twilio_status(unknown, {"X-Twilio-Signature":sig}, callback_url)
    backend.process_twilio_status(data, {"X-Twilio-Signature":signature}, callback_url)
    backend.process_twilio_status(data, {"X-Twilio-Signature":signature}, callback_url)
    failed = {"MessageSid": "SM-callback", "MessageStatus": "failed"}
    failed_text = callback_url + "".join(f"{key}{failed[key]}" for key in sorted(failed))
    failed_signature = base64.b64encode(
        hmac.new(b"callback-secret", failed_text.encode(), hashlib.sha1).digest()
    ).decode()
    with pytest.raises(ConflictError):
        backend.process_twilio_status(
            failed, {"X-Twilio-Signature": failed_signature}, callback_url
        )
    assert backend.get_action(user["id"], action["id"])["status"] == "DELIVERED"


def test_voice_callback_uses_twilio_in_progress_and_completed_states():
    provider = __import__("backend.action_providers", fromlist=["TwilioVoiceProvider"]).TwilioVoiceProvider(
        "AC-test", "voice-secret", from_number="+15550000000"
    )
    backend, user = make_backend(None, provider)
    backend.add_guardian(user["id"], "One", "+15550000001", "parent")
    backend.persist_engine_output(user["id"], high_output())
    action = next(item for item in backend.list_actions(user["id"]) if item["action_type"] == "GUARDIAN_COMMUNICATION")
    attempt = backend._ensure_action_attempt(action["id"], 1, "voice")
    backend._update_action_attempt(int(attempt["id"]), status="SUBMITTED", attempt_count=1, provider_reference="CA-callback")
    import base64, hashlib, hmac
    callback_url = "https://backend.example/api/provider/twilio/voice-status"
    data = {"CallSid":"CA-callback", "CallStatus":"in-progress"}
    text = callback_url + "".join(f"{key}{data[key]}" for key in sorted(data))
    signature = base64.b64encode(hmac.new(b"voice-secret", text.encode(), hashlib.sha1).digest()).decode()
    backend.process_twilio_voice_status(data, {"X-Twilio-Signature":signature}, callback_url)
    assert backend.get_action(user["id"], action["id"])["attempts"][0]["status"] == "SUBMITTED"
    completed = {"CallSid":"CA-callback", "CallStatus":"completed"}
    text = callback_url + "".join(f"{key}{completed[key]}" for key in sorted(completed))
    signature = base64.b64encode(hmac.new(b"voice-secret", text.encode(), hashlib.sha1).digest()).decode()
    backend.process_twilio_voice_status(completed, {"X-Twilio-Signature":signature}, callback_url)
    assert backend.get_action(user["id"], action["id"])["attempts"][0]["status"] == "DELIVERED"
