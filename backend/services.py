"""Application services around the frozen CARES decision engine."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from engine.models import EngineOutput, PhysiologicalSample, RiskLevel
from engine.risk_engine import CARESDecisionEngine

from .db import Database, utc_now
from .location import ReverseGeocoder, validate_hardware_location
from .realtime import EventBus
from .security import SessionStore, hash_password, verify_password


class BackendError(Exception):
    status_code = 400


class AuthenticationError(BackendError):
    status_code = 401


class AuthorizationError(BackendError):
    status_code = 403


class NotFoundError(BackendError):
    status_code = 404


class ConflictError(BackendError):
    status_code = 409


class ValidationError(BackendError):
    status_code = 422


EMAIL_OR_USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.+@-]{2,119}$")
PHONE = re.compile(r"^[+()0-9 .-]{7,32}$")
ACTION_STATUSES = {"GENERATED", "PENDING", "SENT", "DELIVERED", "FAILED", "UNAVAILABLE"}


def _row_to_dict(row: Any) -> Optional[dict[str, Any]]:
    return dict(row) if row is not None else None


def _json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def _decode_json(value: str) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return []


def _validate_user_id(database: Database, user_id: int) -> None:
    if database.fetch_one("SELECT id FROM users WHERE id = ?", (user_id,)) is None:
        raise NotFoundError("User not found.")


class CARESBackend:
    """Persistence/API-facing application service.

    ``process_sample`` is the hardware-ingestion seam. It invokes the
    existing ``CARESDecisionEngine`` and persists its exact ``EngineOutput``;
    this class never calculates a risk level.
    """

    def __init__(
        self,
        database: Database | None = None,
        db_path: str = "data/cares.sqlite3",
        geocoder: ReverseGeocoder | None = None,
        engine_factory: Callable[[], CARESDecisionEngine] = CARESDecisionEngine,
    ) -> None:
        self.database = database or Database(db_path)
        self.sessions = SessionStore(self.database)
        self.geocoder = geocoder or ReverseGeocoder()
        self.engine_factory = engine_factory
        self._engines: dict[int, CARESDecisionEngine] = {}
        self._engine_lock = threading.RLock()
        self.events = EventBus()

    # ------------------------------ accounts ------------------------------

    def register(self, name: Any, email: Any, password: Any) -> dict[str, Any]:
        name = str(name or "").strip()
        email = str(email or "").strip().lower()
        if not 1 <= len(name) <= 120:
            raise ValidationError("Name must contain 1-120 characters.")
        if not EMAIL_OR_USERNAME.fullmatch(email):
            raise ValidationError("Email/username is invalid.")
        if not isinstance(password, str) or not 8 <= len(password) <= 256:
            raise ValidationError("Password must contain 8-256 characters.")
        try:
            cursor = self.database.execute(
                "INSERT INTO users(name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (name, email, hash_password(password), utc_now()),
            )
        except Exception as exc:
            if "UNIQUE constraint failed" in str(exc):
                raise ConflictError("An account with that email/username already exists.")
            raise
        return self.user(cursor.lastrowid)

    def user(self, user_id: int) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT id, name, email, created_at FROM users WHERE id = ?", (user_id,)
        )
        if row is None:
            raise NotFoundError("User not found.")
        return dict(row)

    def login(self, email: Any, password: Any) -> tuple[dict[str, Any], str]:
        identifier = str(email or "").strip().lower()
        row = self.database.fetch_one(
            "SELECT id, name, email, password_hash, created_at FROM users WHERE email = ?",
            (identifier,),
        )
        if row is None or not isinstance(password, str) or not verify_password(password, row["password_hash"]):
            raise AuthenticationError("Invalid credentials.")
        return self.user(int(row["id"])), self.sessions.create(int(row["id"]))

    def authenticate(self, token: Optional[str]) -> int:
        user_id = self.sessions.resolve(token)
        if user_id is None:
            raise AuthenticationError("Authentication required.")
        return user_id

    def logout(self, token: Optional[str]) -> None:
        self.sessions.revoke(token)

    # --------------------------- guardian contacts ------------------------

    def list_guardians(self, user_id: int) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        return [dict(row) for row in self.database.fetch_all(
            "SELECT id, name, phone_number, relationship, created_at, updated_at "
            "FROM guardian_contacts WHERE user_id = ? ORDER BY id", (user_id,)
        )]

    def add_guardian(self, user_id: int, name: Any, phone: Any, relationship: Any) -> dict[str, Any]:
        _validate_user_id(self.database, user_id)
        name = str(name or "").strip()
        phone = str(phone or "").strip()
        relationship = str(relationship or "").strip()
        if not 1 <= len(name) <= 120 or not 1 <= len(relationship) <= 80:
            raise ValidationError("Guardian name and relationship are required.")
        if not PHONE.fullmatch(phone):
            raise ValidationError("Guardian phone number is invalid.")
        now = utc_now()
        cursor = self.database.execute(
            "INSERT INTO guardian_contacts(user_id, name, phone_number, relationship, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)", (user_id, name, phone, relationship, now, now)
        )
        return self._guardian(user_id, cursor.lastrowid)

    def update_guardian(self, user_id: int, guardian_id: int, **values: Any) -> dict[str, Any]:
        current = self._guardian(user_id, guardian_id)
        name = str(values.get("name", current["name"])).strip()
        phone = str(values.get("phone_number", current["phone_number"])).strip()
        relationship = str(values.get("relationship", current["relationship"])).strip()
        if not 1 <= len(name) <= 120 or not 1 <= len(relationship) <= 80 or not PHONE.fullmatch(phone):
            raise ValidationError("Guardian contact fields are invalid.")
        self.database.execute(
            "UPDATE guardian_contacts SET name = ?, phone_number = ?, relationship = ?, updated_at = ? "
            "WHERE id = ? AND user_id = ?",
            (name, phone, relationship, utc_now(), guardian_id, user_id),
        )
        return self._guardian(user_id, guardian_id)

    def delete_guardian(self, user_id: int, guardian_id: int) -> None:
        self._guardian(user_id, guardian_id)
        self.database.execute("DELETE FROM guardian_contacts WHERE id = ? AND user_id = ?", (guardian_id, user_id))

    def _guardian(self, user_id: int, guardian_id: int) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT id, name, phone_number, relationship, created_at, updated_at "
            "FROM guardian_contacts WHERE id = ? AND user_id = ?", (guardian_id, user_id)
        )
        if row is None:
            raise NotFoundError("Guardian contact not found.")
        return dict(row)

    # --------------------------- engine persistence ----------------------

    def process_sample(self, user_id: int, sample: PhysiologicalSample) -> dict[str, Any]:
        _validate_user_id(self.database, user_id)
        with self._engine_lock:
            engine = self._engines.setdefault(user_id, self.engine_factory())
            output = engine.process_sample(sample)
        return self.persist_engine_output(user_id, output)

    def persist_engine_output(self, user_id: int, output: EngineOutput) -> dict[str, Any]:
        """Persist an existing EngineOutput without recalculating it."""
        _validate_user_id(self.database, user_id)
        payload = output.to_dict()
        created_at = utc_now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO engine_events(user_id, timestamp, heart_rate, baseline, deviation, "
                "percentage_deviation, risk_level, risk_score, confidence, trend, persistence, "
                "recovery_state, reason_codes, explanation, recommended_actions, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, payload["timestamp"], payload["current_value"], payload["baseline"],
                    payload["deviation"], payload["pct_deviation"], payload["risk_level"],
                    payload["risk_score"], payload["confidence"], payload["trend"],
                    payload["persistence"], payload["recovery_state"], _json(payload["reason_codes"]),
                    payload["human_readable_explanation"], _json(payload["recommended_action"]), created_at,
                ),
            )
            event_id = int(cursor.lastrowid)
            actions = payload["recommended_action"]
            for action in actions:
                connection.execute(
                    "INSERT INTO guardian_action_events(user_id, engine_event_id, action_type, status, timestamp, metadata) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (user_id, event_id, action, "GENERATED", payload["timestamp"], _json({"source": "GuardianActionMapper"})),
                )

            location = self._latest_location(user_id, connection=connection)
            incident_id = None
            if payload["risk_level"] == RiskLevel.HIGH.value:
                incident_cursor = connection.execute(
                    "INSERT INTO incidents(user_id, engine_event_id, risk_level, timestamp, explanation, location_event_id, status) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (user_id, event_id, payload["risk_level"], payload["timestamp"], payload["human_readable_explanation"],
                     location["id"] if location else None, "OPEN"),
                )
                incident_id = int(incident_cursor.lastrowid)

        event = self._engine_event(event_id, user_id)
        self.events.publish(user_id, {"type": "engine_output", "data": event})
        if incident_id is not None:
            self.events.publish(user_id, {"type": "incident", "data": self.get_incident(user_id, incident_id)})
        return event

    def get_current(self, user_id: int) -> dict[str, Any]:
        event = self._engine_event_for_user(user_id)
        return {
            "engine_event": event,
            "location": self.latest_location(user_id),
            "actions": self.list_actions(user_id, limit=20),
        }

    def history(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        limit = max(1, min(int(limit), 500))
        rows = self.database.fetch_all(
            "SELECT * FROM engine_events WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit)
        )
        return [self._decode_engine_event(row) for row in rows]

    def _engine_event_for_user(self, user_id: int) -> Optional[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        row = self.database.fetch_one("SELECT * FROM engine_events WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
        return self._decode_engine_event(row) if row else None

    def _engine_event(self, event_id: int, user_id: int) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM engine_events WHERE id = ? AND user_id = ?", (event_id, user_id))
        if row is None:
            raise NotFoundError("Engine event not found.")
        return self._decode_engine_event(row)

    @staticmethod
    def _decode_engine_event(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["reason_codes"] = _decode_json(item.pop("reason_codes"))
        item["recommended_actions"] = _decode_json(item.pop("recommended_actions"))
        item["current_value"] = item.pop("heart_rate")
        item["percentage_deviation"] = item["percentage_deviation"]
        return item

    # ----------------------------- locations ------------------------------

    def ingest_location(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        _validate_user_id(self.database, user_id)
        location = validate_hardware_location(
            data.get("latitude"), data.get("longitude"), data.get("accuracy"),
            data.get("timestamp"), data.get("source"),
        )
        resolved = self.geocoder.resolve(location)
        cursor = self.database.execute(
            "INSERT INTO location_events(user_id, timestamp, latitude, longitude, accuracy, source, formatted_address, provider) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, location.timestamp, location.latitude, location.longitude, location.accuracy,
             location.source, resolved.formatted_address, resolved.provider),
        )
        event = self._location(int(cursor.lastrowid), user_id)
        self.events.publish(user_id, {"type": "location", "data": event})
        return event

    def latest_location(self, user_id: int) -> Optional[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        row = self.database.fetch_one("SELECT * FROM location_events WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
        return dict(row) if row else None

    def _latest_location(self, user_id: int, connection: Any = None) -> Optional[dict[str, Any]]:
        query = connection.execute("SELECT * FROM location_events WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)) if connection else self.database.fetch_one("SELECT * FROM location_events WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
        row = query.fetchone() if connection else query
        return dict(row) if row else None

    def _location(self, location_id: int, user_id: int) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM location_events WHERE id = ? AND user_id = ?", (location_id, user_id))
        if row is None:
            raise NotFoundError("Location event not found.")
        return dict(row)

    # ------------------------- actions and incidents ---------------------

    def list_actions(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        limit = max(1, min(int(limit), 500))
        rows = self.database.fetch_all("SELECT * FROM guardian_action_events WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        return [self._decode_action(row) for row in rows]

    def get_action(self, user_id: int, action_id: int) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM guardian_action_events WHERE id = ? AND user_id = ?", (action_id, user_id))
        if row is None:
            raise NotFoundError("Guardian action event not found.")
        return self._decode_action(row)

    def update_action_status(self, user_id: int, action_id: int, status: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        status = str(status or "").upper()
        if status not in ACTION_STATUSES:
            raise ValidationError("Invalid guardian action status.")
        self.get_action(user_id, action_id)
        self.database.execute("UPDATE guardian_action_events SET status = ?, metadata = ? WHERE id = ? AND user_id = ?", (status, _json(metadata or {}), action_id, user_id))
        event = self.get_action(user_id, action_id)
        self.events.publish(user_id, {"type": "guardian_action", "data": event})
        return event

    @staticmethod
    def _decode_action(row: Any) -> dict[str, Any]:
        item = dict(row)
        item["metadata"] = _decode_json(item["metadata"])
        return item

    def list_incidents(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        limit = max(1, min(int(limit), 500))
        rows = self.database.fetch_all("SELECT * FROM incidents WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        incidents = []
        for row in rows:
            item = dict(row)
            engine_row = self.database.fetch_one(
                "SELECT * FROM engine_events WHERE id = ? AND user_id = ?",
                (item["engine_event_id"], user_id),
            )
            item["engine_event"] = self._decode_engine_event(engine_row) if engine_row else None
            item["location"] = self._location(item["location_event_id"], user_id) if item["location_event_id"] else None
            incidents.append(item)
        return incidents

    def get_incident(self, user_id: int, incident_id: int) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM incidents WHERE id = ? AND user_id = ?", (incident_id, user_id))
        if row is None:
            raise NotFoundError("Incident not found.")
        item = dict(row)
        if item["location_event_id"] is not None:
            item["location"] = self._location(item["location_event_id"], user_id)
        else:
            item["location"] = None
        return item

    # ----------------------- baseline audit persistence ------------------

    def persist_daily_record(self, user_id: int, record: Any) -> dict[str, Any]:
        _validate_user_id(self.database, user_id)
        data = asdict(record) if is_dataclass(record) else dict(record)
        required = ["date", "trusted_samples", "mean_bpm", "median_bpm", "std_bpm", "minimum_bpm", "maximum_bpm", "eligible_observations", "adaptation_updates", "adaptation_holds"]
        if any(key not in data for key in required):
            raise ValidationError("Incomplete daily baseline record.")
        self.database.execute(
            "INSERT INTO baseline_daily_records(user_id, date, trusted_samples, mean_bpm, median_bpm, std_bpm, minimum_bpm, maximum_bpm, eligible_observations, adaptation_updates, adaptation_holds) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(user_id, date) DO UPDATE SET trusted_samples=excluded.trusted_samples, mean_bpm=excluded.mean_bpm, median_bpm=excluded.median_bpm, std_bpm=excluded.std_bpm, minimum_bpm=excluded.minimum_bpm, maximum_bpm=excluded.maximum_bpm, eligible_observations=excluded.eligible_observations, adaptation_updates=excluded.adaptation_updates, adaptation_holds=excluded.adaptation_holds",
            (user_id, *(data[key] for key in required)),
        )
        row = self.database.fetch_one("SELECT * FROM baseline_daily_records WHERE user_id = ? AND date = ?", (user_id, data["date"]))
        return dict(row)

    def list_daily_records(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        limit = max(1, min(int(limit), 500))
        return [dict(row) for row in self.database.fetch_all("SELECT * FROM baseline_daily_records WHERE user_id = ? ORDER BY date DESC LIMIT ?", (user_id, limit))]

    def persist_adaptation_event(self, user_id: int, record: Any) -> dict[str, Any]:
        _validate_user_id(self.database, user_id)
        data = asdict(record) if is_dataclass(record) else dict(record)
        aliases = {"previous_baseline_bpm": "previous_baseline", "observation_mean_bpm": "observation_mean", "observation_std_bpm": "observation_std", "deviation_bpm": "deviation", "signal_quality_mean": "signal_quality", "new_baseline_bpm": "new_baseline"}
        for old, new in aliases.items():
            if old in data and new not in data:
                data[new] = data[old]
        required = ["timestamp", "risk_level", "valid_samples", "required_samples", "decision", "reason"]
        if any(key not in data for key in required):
            raise ValidationError("Incomplete baseline adaptation event.")
        cursor = self.database.execute(
            "INSERT INTO baseline_adaptation_events(user_id, timestamp, previous_baseline, observation_mean, observation_std, deviation, risk_level, valid_samples, required_samples, signal_quality, decision, new_baseline, reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, data["timestamp"], data.get("previous_baseline"), data.get("observation_mean"), data.get("observation_std"), data.get("deviation"), data["risk_level"], data["valid_samples"], data["required_samples"], data.get("signal_quality"), data["decision"], data.get("new_baseline"), data["reason"]),
        )
        row = self.database.fetch_one("SELECT * FROM baseline_adaptation_events WHERE id = ? AND user_id = ?", (cursor.lastrowid, user_id))
        return dict(row)

    def list_adaptation_events(self, user_id: int, limit: int = 100) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        limit = max(1, min(int(limit), 500))
        return [dict(row) for row in self.database.fetch_all("SELECT * FROM baseline_adaptation_events WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))]

    def baseline_current(self, user_id: int) -> Optional[dict[str, Any]]:
        event = self._engine_event_for_user(user_id)
        if event is None:
            return None
        snapshot: dict[str, Any] = {
            "status": "CALIBRATING" if "BASELINE_CALIBRATING" in event["reason_codes"] else "READY",
            "trusted_samples": None,
            "calibration_elapsed_seconds": None,
            "calibration_progress": None,
        }
        with self._engine_lock:
            engine = self._engines.get(user_id)
            if engine is not None:
                estimator = engine.baseline_estimator
                required_seconds = estimator._required_calibration_seconds()
                snapshot.update({
                    "status": estimator.state.value,
                    "trusted_samples": estimator.samples_count,
                    "calibration_elapsed_seconds": estimator.calibration_elapsed_seconds,
                    "calibration_progress": min(100.0, (estimator.calibration_elapsed_seconds / required_seconds) * 100.0) if required_seconds > 0 else None,
                })
        return {
            "baseline": event["baseline"],
            "timestamp": event["timestamp"],
            "source_engine_event_id": event["id"],
            **snapshot,
        }
