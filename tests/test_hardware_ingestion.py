import math

import pytest

import backend.services as service_module
from backend import CARESBackend, Database
from backend.api import CARESAPI
from engine.hr_estimator import HREstimate


class FakeEstimator:
    """Synthetic estimator fixture; this does not claim physical validation."""

    accepted = True

    def __init__(self, sampling_rate_hz):
        self.fs = sampling_rate_hz
        self.window_samples = 4
        self.step_samples = 2
        self.calls = 0

    def reset(self):
        pass

    def estimate_window(self, signal, timestamp):
        self.calls += 1
        return HREstimate(
            timestamp=timestamp,
            heart_rate_bpm=70.0,
            quality=1.0 if self.accepted else 0.05,
            accepted=self.accepted,
            method="SYNTHETIC_TEST_FIXTURE",
            reason="SIGNAL_SUPPORTED" if self.accepted else "LOW_QUALITY",
        )


class Clock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value


def make_backend(monkeypatch, accepted=True, clock=None):
    FakeEstimator.accepted = accepted
    monkeypatch.setattr(service_module, "WristBVPHREstimator", FakeEstimator)
    backend = CARESBackend(
        database=Database(":memory:"),
        hardware_clock=clock or Clock(),
        hardware_stale_timeout_seconds=10.0,
        hardware_sampling_rate_hz=64.0,
    )
    user = backend.register("Hardware User", "hardware@example.com", "password-one")
    backend.register_hardware_device(user["id"], "esp32-test", "test-device-token")
    return backend, user


def packet(sequence, samples=None, **extra):
    value = {
        "protocol_version": "1.0",
        "device_id": "esp32-test",
        "connection_id": "wifi-connection-a",
        "sequence": sequence,
        "device_timestamp_start": float(sequence - 1),
        "sampling_rate_hz": 64.0,
        "bvp_samples": samples or [0.1, 0.2],
    }
    value.update(extra)
    return value


def device_headers(token="test-device-token", device_id="esp32-test"):
    return {
        "X-CARES-Device-Id": device_id,
        "X-CARES-Device-Token": token,
    }

def test_authenticated_packets_buffer_raw_bvp_and_persist_engine_output(monkeypatch):
    backend, user = make_backend(monkeypatch)
    api = CARESAPI(backend)
    headers = device_headers()

    first = api.handle("POST", "/api/hardware/ingest", packet(1), headers)
    second = api.handle("POST", "/api/hardware/ingest", packet(2, [0.3, 0.4]), headers)

    assert first.status == 202
    assert first.body["estimate"] is None
    assert second.status == 202
    assert second.body["estimate"]["accepted"] is True
    assert second.body["engine_output"]["source"] == "REAL_HARDWARE"
    assert backend.history(user["id"])[0]["source"] == "REAL_HARDWARE"


def test_hardware_auth_and_session_ownership(monkeypatch):
    backend, user = make_backend(monkeypatch)
    api = CARESAPI(backend)

    assert api.handle("POST", "/api/hardware/ingest", packet(1), {}).status == 401
    assert api.handle(
        "POST", "/api/hardware/ingest", packet(1), device_headers("wrong")
    ).status == 401

    other = backend.register("Other", "other@example.com", "password-two")
    other_session = backend.start_monitoring_session(other["id"], source="REAL_HARDWARE")
    denied = packet(1, session_id=other_session["id"])
    response = api.handle("POST", "/api/hardware/ingest", denied, device_headers())
    assert response.status == 404
    assert backend.history(user["id"]) == []


def test_packet_validation_and_replay_rejection_do_not_mutate_stream(monkeypatch):
    backend, user = make_backend(monkeypatch)
    api = CARESAPI(backend)
    headers = device_headers()

    assert api.handle("POST", "/api/hardware/ingest", packet(1, protocol_version="9.0"), headers).status == 422
    assert api.handle("POST", "/api/hardware/ingest", packet(1, bvp_samples=[math.nan]), headers).status == 422
    assert api.handle("POST", "/api/hardware/ingest", packet(1, bvp_samples=list(range(4097))), headers).status == 422
    assert api.handle("POST", "/api/hardware/ingest", packet(1), headers).status == 202
    duplicate = api.handle("POST", "/api/hardware/ingest", packet(1), headers)
    assert duplicate.status == 409
    stream = next(iter(backend._hardware_streams.values()))
    assert stream.last_sequence == 1
    assert len(stream.buffer) == 2
    assert backend.history(user["id"]) == []


def test_rejected_estimate_does_not_reach_decision_engine(monkeypatch):
    backend, user = make_backend(monkeypatch, accepted=False)
    api = CARESAPI(backend)
    headers = device_headers()
    response = api.handle("POST", "/api/hardware/ingest", packet(1, [1.0, 2.0]), headers)
    assert response.status == 202
    response = api.handle("POST", "/api/hardware/ingest", packet(2, [3.0, 4.0]), headers)
    assert response.status == 202
    assert response.body["estimate"]["accepted"] is False
    assert response.body["engine_output"] is None
    assert backend.history(user["id"]) == []
    assert backend._engines == {}


def test_stale_status_and_reconnect_start_new_transport_epoch(monkeypatch):
    clock = Clock()
    backend, user = make_backend(monkeypatch, clock=clock)
    api = CARESAPI(backend)
    headers = device_headers()
    api.handle("POST", "/api/hardware/ingest", packet(1, [1.0, 2.0]), headers)
    api.handle("POST", "/api/hardware/ingest", packet(2, [3.0, 4.0]), headers)

    login_user, token = backend.login("hardware@example.com", "password-one")
    app_headers = {"Authorization": f"Bearer {token}"}
    clock.value += 20.0
    status = api.handle("GET", "/api/hardware/status", headers=app_headers)
    assert status.status == 200
    assert status.body["devices"][0]["transport_status"] == "STALE"

    response = api.handle("POST", "/api/hardware/ingest", packet(3, [5.0, 6.0]), headers)
    assert response.status == 202
    assert response.body["reconnected"] is True
    assert response.body["transport_epoch"] == 1
    assert response.body["transport_status"] == "CONNECTED"


def test_authenticated_hardware_status_does_not_expose_device_token(monkeypatch):
    backend, user = make_backend(monkeypatch)
    api = CARESAPI(backend)
    _, token = backend.login("hardware@example.com", "password-one")
    response = api.handle(
        "GET", "/api/hardware/status", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status == 200
    assert response.body["devices"][0]["device_id"] == "esp32-test"
    assert "token" not in response.body["devices"][0]


def test_retired_connection_packets_are_rejected(monkeypatch):
    backend, user = make_backend(monkeypatch)
    api = CARESAPI(backend)
    headers = device_headers()
    api.handle("POST", "/api/hardware/ingest", packet(1), headers)
    switched = packet(1, connection_id="wifi-connection-b")
    response = api.handle("POST", "/api/hardware/ingest", switched, headers)
    assert response.status == 202
    old = packet(2, connection_id="wifi-connection-a")
    response = api.handle("POST", "/api/hardware/ingest", old, headers)
    assert response.status == 409
    stream = next(iter(backend._hardware_streams.values()))
    assert stream.connection_id == "wifi-connection-b"


def test_production_api_disables_demo_routes_without_affecting_fixtures(monkeypatch):
    backend, user = make_backend(monkeypatch)
    api = CARESAPI(backend, demo_enabled=False)
    _, token = backend.login("hardware@example.com", "password-one")
    headers = {"Authorization": f"Bearer {token}"}
    assert api.handle("POST", "/api/demo/start", {"scenario": "NORMAL"}, headers).status == 404
    assert api.handle("GET", "/api/demo/status", headers=headers).status == 404
    assert api.handle("GET", "/api/dashboard/current", headers=headers).body["demo"]["source"] == "REAL_HARDWARE_ONLY"


def test_optional_hardware_location_is_validated_and_associated(monkeypatch):
    clock = Clock()
    backend, user = make_backend(monkeypatch, clock=clock)
    api = CARESAPI(backend)
    headers = device_headers()
    good = packet(1, device_timestamp_start=100.0, location={"latitude": 0.0, "longitude": 0.0, "timestamp": 100.0})
    response = api.handle("POST", "/api/hardware/ingest", good, headers)
    assert response.status == 202
    session_id = response.body["session_id"]
    location = backend.latest_location(user["id"], session_id=session_id)
    assert location["latitude"] == 0.0
    assert location["longitude"] == 0.0
    assert location["device_id"] == "esp32-test"


def test_invalid_or_stale_hardware_location_does_not_mutate_stream(monkeypatch):
    clock = Clock()
    backend, user = make_backend(monkeypatch, clock=clock)
    api = CARESAPI(backend)
    headers = device_headers()
    invalid = packet(1, device_timestamp_start=100.0, location={"latitude": 91.0, "longitude": 0.0, "timestamp": 100.0})
    assert api.handle("POST", "/api/hardware/ingest", invalid, headers).status == 422
    clock.value = 200.0
    stale = packet(1, device_timestamp_start=200.0, location={"latitude": 12.0, "longitude": 76.0, "timestamp": 1.0})
    assert api.handle("POST", "/api/hardware/ingest", stale, headers).status == 422
    assert backend._hardware_streams == {}
    assert backend.history(user["id"]) == []


def test_old_connection_packet_rejected_without_mutating_current_order(monkeypatch):
    backend, user = make_backend(monkeypatch)
    api = CARESAPI(backend)
    headers = device_headers()
    api.handle("POST", "/api/hardware/ingest", packet(1), headers)
    api.handle("POST", "/api/hardware/ingest", packet(1, connection_id="wifi-connection-b"), headers)
    stream = next(iter(backend._hardware_streams.values()))
    before = (stream.connection_id, stream.last_sequence, stream.last_device_timestamp_start)
    rejected = packet(0, connection_id="wifi-connection-a", device_timestamp_start=0.0)
    assert api.handle("POST", "/api/hardware/ingest", rejected, headers).status == 409
    after = (stream.connection_id, stream.last_sequence, stream.last_device_timestamp_start)
    assert after == before
