import queue
import time

import pytest

from backend import CARESBackend, Database
from backend.api import CARESAPI
from backend.demo import DemoController, DemoInputUnavailable, DemoSampleAdapter
from engine.models import PhysiologicalSample


def make_backend():
    return CARESBackend(database=Database(":memory:"))


def wait_for_finished(controller, user_id, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = controller.status(user_id)
        if not status.get("active"):
            return status
        time.sleep(0.01)
    raise AssertionError("demo did not finish in time")


def test_controlled_adapter_only_creates_samples_and_no_risk_decision():
    stream = DemoSampleAdapter(sample_limit=301).create("SUSTAINED_HIGH")
    assert stream.source == "DEMO_SYNTHETIC"
    assert len(stream.samples) >= 301
    assert all(isinstance(sample, PhysiologicalSample) for sample in stream.samples)
    assert not hasattr(stream, "risk_level")


def test_normal_requires_official_wesad_file_when_unavailable(tmp_path):
    adapter = DemoSampleAdapter(wesad_dir=str(tmp_path), sample_limit=301)
    with pytest.raises(DemoInputUnavailable, match="official WESAD"):
        adapter.create("NORMAL")


def test_demo_engine_persistence_session_isolation_and_fresh_runtime():
    backend = make_backend()
    user = backend.register("Demo", "demo@example.com", "password-one")
    controller = DemoController(
        backend,
        adapter=DemoSampleAdapter(sample_limit=301),
        interval_seconds=0.0005,
    )

    first = controller.start(user["id"], "ELEVATED")
    first_status = wait_for_finished(controller, user["id"])
    first_session = first["session"]["id"]
    assert first_status["session"]["id"] == first_session
    assert first_status["session"]["status"] == "STOPPED"
    assert first_status["sample_count"] > 0

    events = backend.history(user["id"], limit=500, session_id=first_session)
    assert events
    assert all(event["source"] == "DEMO_SYNTHETIC" for event in events)
    assert all(event["session_id"] == first_session for event in events)

    second = controller.start(user["id"], "ELEVATED")
    second_status = wait_for_finished(controller, user["id"])
    second_session = second["session"]["id"]
    assert second_session != first_session
    assert second_status["sample_count"] > 0
    second_events = backend.history(user["id"], limit=500, session_id=second_session)
    assert second_events
    assert any("BASELINE_CALIBRATING" in event["reason_codes"] for event in reversed(second_events[-3:]))


def test_sustained_high_uses_engine_actions_and_incident_without_demo_location():
    backend = make_backend()
    user = backend.register("High Demo", "high@example.com", "password-one")
    controller = DemoController(
        backend,
        adapter=DemoSampleAdapter(sample_limit=301),
        interval_seconds=0.0005,
    )
    controller.start(user["id"], "SUSTAINED_HIGH")
    status = wait_for_finished(controller, user["id"])
    events = backend.history(user["id"], limit=500, session_id=status["session"]["id"])
    assert any(event["risk_level"] == "HIGH" for event in events)
    incidents = backend.list_incidents(user["id"])
    assert incidents
    assert incidents[0]["source"] == "DEMO_SYNTHETIC"
    assert incidents[0]["location"] is None
    actions = backend.list_actions(user["id"], limit=500, session_id=status["session"]["id"])
    assert {action["action_type"] for action in actions} >= {
        "EMERGENCY_ALERT",
        "GUARDIAN_NOTIFICATION",
        "LOCATION_SHARE",
        "GUARDIAN_COMMUNICATION",
        "INCIDENT_LOG",
    }
    assert all(action["status"] == "GENERATED" for action in actions)


def test_demo_sse_receives_actual_persisted_engine_output():
    backend = make_backend()
    user = backend.register("Stream", "stream@example.com", "password-one")
    subscriber = backend.events.subscribe(user["id"])
    controller = DemoController(
        backend,
        adapter=DemoSampleAdapter(sample_limit=301),
        interval_seconds=0.0005,
    )
    controller.start(user["id"], "ELEVATED")
    received = None
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            event = subscriber.get(timeout=0.2)
        except queue.Empty:
            continue
        if event.get("type") == "engine_output":
            received = event["data"]
            break
    status = wait_for_finished(controller, user["id"])
    backend.events.unsubscribe(user["id"], subscriber)
    assert received is not None
    assert received["source"] == "DEMO_SYNTHETIC"
    assert received["session_id"] == status["session"]["id"]
    assert received["reason_codes"]
    assert received["explanation"]


def test_demo_api_is_authenticated_and_duplicate_start_is_safe():
    backend = make_backend()
    api = CARESAPI(backend)
    registered = api.handle("POST", "/api/auth/register", {
        "name": "API Demo", "email": "api-demo@example.com", "password": "password-one"
    })
    assert registered.status == 201
    login = api.handle("POST", "/api/auth/login", {
        "email": "api-demo@example.com", "password": "password-one"
    })
    token = login.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    headers = {"Authorization": f"Bearer {token}"}
    assert api.handle("GET", "/api/demo/status").status == 401
    first = api.handle("POST", "/api/demo/start", {"scenario": "ELEVATED"}, headers)
    second = api.handle("POST", "/api/demo/start", {"scenario": "RECOVERY"}, headers)
    assert first.status == 202
    assert second.status == 202
    assert first.body["demo"]["session"]["id"] != second.body["demo"]["session"]["id"]
    stopped = api.handle("POST", "/api/demo/stop", headers=headers)
    assert stopped.status == 200
    assert stopped.body["demo"]["active"] is False
