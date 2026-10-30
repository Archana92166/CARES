from backend import CARESBackend
from backend.api import CARESAPI
from engine.models import EngineOutput, RiskLevel


def _packet(**overrides):
    packet = {
        "protocol_version": "1.0",
        "device_id": "esp32-max30100",
        "connection_id": "wifi-a",
        "sequence": 1,
        "device_timestamp_start": 1700000000.0,
        "sampling_rate_hz": 64.0,
        "heart_rate_bpm": 72.5,
        "spo2_percent": 97.2,
        "location": {
            "latitude": 12.9716,
            "longitude": 77.5946,
            "timestamp": 1700000000.0,
            "accuracy": 6.0,
        },
    }
    packet.update(overrides)
    return packet


def test_single_device_environment_binding_supports_compatible_headers(monkeypatch, tmp_path):
    monkeypatch.delenv("CARES_HARDWARE_DEVICES", raising=False)
    monkeypatch.setenv("CARES_DEVICE_TOKEN", "env-device-secret")
    monkeypatch.setenv("CARES_HARDWARE_USER_ID", "1")
    monkeypatch.setenv("CARES_HARDWARE_DEVICE_ID", "esp32-env")
    backend = CARESBackend(db_path=str(tmp_path / "env.sqlite3"))
    backend.register("Env Hardware", "env@example.com", "password-one")
    packet = _packet(device_id="esp32-env")
    response = CARESAPI(backend).handle("POST", "/api/hardware/ingest", packet, {
        "X-Device-Id": "esp32-env", "X-Device-Token": "env-device-secret",
    })
    assert response.status == 202


def test_precomputed_sensor_readings_are_validated_and_persisted(tmp_path):
    backend = CARESBackend(db_path=str(tmp_path / "live.sqlite3"), hardware_devices={
        "esp32-max30100": {"user_id": 1, "token": "device-secret"},
    })
    user = backend.register("Hardware", "live@example.com", "password-one")
    backend.register_hardware_device(user["id"], "esp32-max30100", "device-secret")
    api = CARESAPI(backend)
    headers = {"X-CARES-Device-Id": "esp32-max30100", "X-CARES-Device-Token": "device-secret"}

    response = api.handle("POST", "/api/hardware/ingest", _packet(), headers)
    assert response.status == 202
    assert response.body["reading"]["heart_rate_bpm"] == 72.5
    status = backend.hardware_status(user["id"])[0]
    assert status["latest_reading"]["spo2_percent"] == 97.2
    assert status["reading_freshness"] == "FRESH"
    assert backend.latest_location(user["id"])["device_id"] == "esp32-max30100"

    invalid = api.handle("POST", "/api/hardware/ingest", _packet(sequence=2, spo2_percent=101), headers)
    assert invalid.status == 422
    assert len(backend.hardware_readings(user["id"])) == 1


def test_poor_precomputed_signal_is_visible_and_never_replaces_valid_engine_data(tmp_path):
    backend = CARESBackend(db_path=str(tmp_path / "quality.sqlite3"), hardware_devices={
        "esp32-max30100": {"user_id": 1, "token": "device-secret"},
    })
    user = backend.register("Quality", "quality@example.com", "password-one")
    backend.register_hardware_device(user["id"], "esp32-max30100", "device-secret")
    api = CARESAPI(backend)
    headers = {"X-CARES-Device-Id": "esp32-max30100", "X-CARES-Device-Token": "device-secret"}

    valid = api.handle("POST", "/api/hardware/ingest", _packet(signal_quality=0.8), headers)
    assert valid.status == 202
    assert len(backend.history(user["id"], source="REAL_HARDWARE")) == 1

    poor = api.handle(
        "POST", "/api/hardware/ingest",
        _packet(sequence=2, device_timestamp_start=1700000001.0, signal_quality=0.05), headers,
    )
    assert poor.status == 202
    assert poor.body["estimate"]["accepted"] is False
    status = backend.hardware_status(user["id"])[0]
    assert status["measurement_status"] == "POOR_SIGNAL"
    assert status["reading_freshness"] == "UNAVAILABLE"
    assert len(backend.hardware_readings(user["id"])) == 1


def test_records_and_sessions_survive_backend_reconnection(tmp_path):
    path = tmp_path / "reconnect.sqlite3"
    first = CARESBackend(db_path=str(path))
    user = first.register("Persistent", "persistent@example.com", "password-one")
    first.persist_engine_output(user_id=user["id"], output=EngineOutput(
        timestamp=1.0, risk_level=RiskLevel.LOW, risk_score=1.0, confidence=0.8,
        trend=0.0, baseline=70.0, current_value=70.0, deviation=0.0,
        pct_deviation=0.0, persistence=0.0, recovery_state="NO_RECOVERY",
        reason_codes=["STABLE_BASELINE"], human_readable_explanation="Stable.",
        recommended_action=["CONTINUE_MONITORING"],
    ))
    _, token = first.login("persistent@example.com", "password-one")
    first.close()

    second = CARESBackend(db_path=str(path))
    assert second.user(user["id"])["email"] == "persistent@example.com"
    assert second.history(user["id"])
    assert second.sessions.resolve(token) == user["id"]
    second.close()


def test_login_cookie_is_persistent_but_does_not_contain_password_or_device_token(tmp_path):
    backend = CARESBackend(db_path=str(tmp_path / "cookie.sqlite3"))
    backend.register("Cookie", "cookie@example.com", "password-one")
    response = CARESAPI(backend).handle("POST", "/api/auth/login", {
        "email": "cookie@example.com", "password": "password-one",
    })
    cookie = response.headers["Set-Cookie"]
    assert "Max-Age=" in cookie
    assert "password-one" not in cookie
    assert "device-secret" not in cookie

