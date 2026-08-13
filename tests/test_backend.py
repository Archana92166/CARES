import hashlib

import pytest

from backend import CARESBackend, Database
from backend.api import CARESAPI
from backend.location import LocationValidationError
from engine.models import EngineOutput, RiskLevel
from engine.personal_baseline import BaselineAdaptationLog, DailyBaselineRecord


def make_backend(tmp_path):
    return CARESBackend(db_path=str(tmp_path / "cares.sqlite3"))


def high_output() -> EngineOutput:
    return EngineOutput(
        timestamp=310.0,
        risk_level=RiskLevel.HIGH,
        risk_score=94.5,
        confidence=0.98,
        trend=1.2,
        baseline=70.0,
        current_value=100.0,
        deviation=30.0,
        pct_deviation=42.857,
        persistence=4.0,
        recovery_state="NO_RECOVERY",
        reason_codes=["BASELINE_DEVIATION", "ESCALATION_CONFIRMED"],
        human_readable_explanation="Risk Level HIGH: current HR is elevated.",
        recommended_action=[
            "EMERGENCY_ALERT",
            "GUARDIAN_NOTIFICATION",
            "LOCATION_SHARE",
            "GUARDIAN_COMMUNICATION",
            "INCIDENT_LOG",
        ],
    )


def test_account_hashes_password_and_login(tmp_path):
    backend = make_backend(tmp_path)
    user = backend.register("Alice", "alice@example.com", "correct horse")
    row = backend.database.fetch_one("SELECT password_hash FROM users WHERE id = ?", (user["id"],))

    assert row["password_hash"] != "correct horse"
    assert row["password_hash"].startswith("pbkdf2_sha256$")
    assert backend.login("alice@example.com", "correct horse")[0]["id"] == user["id"]
    with pytest.raises(Exception):
        backend.login("alice@example.com", "wrong password")


def test_guardian_and_authenticated_isolation(tmp_path):
    backend = make_backend(tmp_path)
    first = backend.register("First", "first@example.com", "password-one")
    second = backend.register("Second", "second@example.com", "password-two")
    guardian = backend.add_guardian(first["id"], "Guardian", "+91 9876543210", "parent")

    assert backend.list_guardians(first["id"])[0]["id"] == guardian["id"]
    assert backend.list_guardians(second["id"]) == []
    with pytest.raises(Exception):
        backend.update_guardian(second["id"], guardian["id"], name="Leaked")


def test_engine_output_is_persisted_without_recalculation(tmp_path):
    backend = make_backend(tmp_path)
    user = backend.register("Alice", "alice@example.com", "correct horse")
    persisted = backend.persist_engine_output(user["id"], high_output())

    assert persisted["risk_level"] == "HIGH"
    assert persisted["risk_score"] == 94.5
    assert persisted["reason_codes"] == ["BASELINE_DEVIATION", "ESCALATION_CONFIRMED"]
    assert persisted["recommended_actions"][0] == "EMERGENCY_ALERT"
    assert len(backend.list_actions(user["id"])) == 5


def test_high_engine_output_creates_incident_with_latest_hardware_location(tmp_path):
    backend = make_backend(tmp_path)
    user = backend.register("Alice", "alice@example.com", "correct horse")
    location = backend.ingest_location(user["id"], {
        "latitude": 12.3051,
        "longitude": 76.6551,
        "accuracy": 4.5,
        "timestamp": 309.0,
        "source": "hardware-gps",
    })
    backend.persist_engine_output(user["id"], high_output())

    incident = backend.list_incidents(user["id"])[0]
    assert incident["risk_level"] == "HIGH"
    assert incident["location_event_id"] == location["id"]
    assert backend.get_incident(user["id"], incident["id"])["location"]["latitude"] == 12.3051


def test_location_validation_and_coordinate_fallback(tmp_path):
    backend = make_backend(tmp_path)
    user = backend.register("Alice", "alice@example.com", "correct horse")
    with pytest.raises(LocationValidationError):
        backend.ingest_location(user["id"], {
            "latitude": 120,
            "longitude": 76,
            "timestamp": 1,
            "source": "gps",
        })
    location = backend.ingest_location(user["id"], {
        "latitude": 12.3,
        "longitude": 76.6,
        "timestamp": 1,
        "source": "gps",
    })
    assert location["formatted_address"] == "12.300000, 76.600000"
    assert location["provider"] == "coordinates"


def test_baseline_audit_records_are_persisted_and_retrievable(tmp_path):
    backend = make_backend(tmp_path)
    user = backend.register("Alice", "alice@example.com", "correct horse")
    daily = DailyBaselineRecord("2026-08-13", 10, 70, 70, 0, 70, 70, 10, 1, 2)
    adaptation = BaselineAdaptationLog(
        timestamp="2026-08-13T00:00:00+00:00",
        previous_baseline_bpm=70,
        observation_mean_bpm=71,
        observation_std_bpm=0,
        deviation_bpm=1,
        risk_level="LOW",
        valid_samples=3,
        required_samples=3,
        signal_quality_mean=1,
        decision="UPDATED",
        new_baseline_bpm=70.01,
        reason="VALID_LOW_RISK_CONSISTENT_VARIATION",
    )

    assert backend.persist_daily_record(user["id"], daily)["date"] == "2026-08-13"
    assert backend.persist_adaptation_event(user["id"], adaptation)["decision"] == "UPDATED"
    assert len(backend.list_daily_records(user["id"])) == 1
    assert len(backend.list_adaptation_events(user["id"])) == 1


def test_api_auth_guardian_and_dashboard_routes(tmp_path):
    backend = make_backend(tmp_path)
    api = CARESAPI(backend)
    registered = api.handle("POST", "/api/auth/register", {
        "name": "Alice", "email": "alice@example.com", "password": "correct horse"
    })
    assert registered.status == 201
    login = api.handle("POST", "/api/auth/login", {
        "email": "alice@example.com", "password": "correct horse"
    })
    assert login.status == 200
    token = login.headers["Set-Cookie"].split("=", 1)[1].split(";", 1)[0]
    headers = {"Authorization": f"Bearer {token}"}
    added = api.handle("POST", "/api/guardian", {
        "name": "Guardian", "phone_number": "+91 9876543210", "relationship": "parent"
    }, headers)
    assert added.status == 201
    current = api.handle("GET", "/api/dashboard/current", headers=headers)
    assert current.status == 200
    assert current.body["engine_event"] is None


def test_action_status_is_explicitly_updated(tmp_path):
    backend = make_backend(tmp_path)
    user = backend.register("Alice", "alice@example.com", "correct horse")
    backend.persist_engine_output(user["id"], high_output())
    action = backend.list_actions(user["id"])[0]
    assert action["status"] == "GENERATED"
    updated = backend.update_action_status(user["id"], action["id"], "UNAVAILABLE", {"reason": "no integration configured"})
    assert updated["status"] == "UNAVAILABLE"
    assert updated["metadata"]["reason"] == "no integration configured"
