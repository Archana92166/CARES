from backend import CARESBackend, Database
from tests.test_stage3_actions import FakeProvider, high_output


def make(demo_dispatch, location=(12.2958, 76.6394), voice=None):
    sms = FakeProvider()
    backend = CARESBackend(
        database=Database(":memory:"), sms_provider=sms, voice_provider=voice,
        enable_live_actions=True, emergency_action_enabled=False,
        demo_dispatch_enabled=demo_dispatch, demo_location=location,
    )
    user = backend.register("Demo User", "demo@example.com", "password-one")
    backend.add_guardian(user["id"], "Guardian", "+15550000001", "parent")
    return backend, user, sms


def test_demo_high_never_dispatches_by_default():
    backend, user, sms = make(False)
    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")
    assert backend.process_pending_actions_once() == 0
    assert sms.calls == []


def test_demo_dispatch_needs_location():
    backend, user, sms = make(True, location=None)
    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")
    assert backend.process_pending_actions_once() == 0
    assert sms.calls == []


def test_demo_high_sends_labelled_incident_and_location():
    backend, user, sms = make(True)
    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")
    assert backend.process_pending_actions_once() >= 1
    recipient, body, _ = sms.calls[0]
    assert recipient == "+15550000001"
    assert "CARES DEMO" in body and "SIMULATED" in body
    assert "High risk." in body
    assert "12.295800, 76.639400" in body
    assert "google.com/maps" in body
    assert len(sms.calls) == 1


def test_demo_dispatch_skips_emergency_action_and_dedupes():
    backend, user, sms = make(True)
    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")
    backend.persist_engine_output(user["id"], high_output(), source="DEMO_SYNTHETIC")
    backend.process_pending_actions_once()
    backend.process_pending_actions_once()
    assert len(sms.calls) == 1
    types = {a["action_type"]: a["status"] for a in backend.list_actions(user["id"])}
    assert types["EMERGENCY_ALERT"] == "GENERATED"
