"""Application services around the frozen CARES decision engine."""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional

from engine.hr_estimator import WristBVPHREstimator
from engine.models import EngineOutput, PhysiologicalSample, RiskLevel
from engine.risk_engine import CARESDecisionEngine

from .db import Database, utc_now
from .action_providers import (
    ProviderError,
    ProviderResult,
    SmsProvider,
    TwilioSmsProvider,
    VoiceProvider,
    configured_live_providers,
)
from .hardware import (
    DEFAULT_SAMPLING_RATE_HZ,
    DEFAULT_STALE_TIMEOUT_SECONDS,
    HardwareDevice,
    HardwarePacket,
    HardwarePacketError,
    HardwareLocationPacket,
    HardwareStreamState,
    MAX_LOCATION_TIMESTAMP_SKEW_SECONDS,
)
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
ACTION_STATUSES = {
    "GENERATED", "QUEUED", "PROCESSING", "SUBMITTED", "DELIVERED",
    "FAILED", "UNKNOWN", "UNAVAILABLE", "PENDING", "SENT",
}


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
        hardware_devices: Optional[Mapping[str, Mapping[str, Any]]] = None,
        hardware_sampling_rate_hz: float = DEFAULT_SAMPLING_RATE_HZ,
        hardware_stale_timeout_seconds: float = DEFAULT_STALE_TIMEOUT_SECONDS,
        hardware_clock: Callable[[], float] = time.time,
        sms_provider: Optional[SmsProvider] = None,
        voice_provider: Optional[VoiceProvider] = None,
        enable_live_actions: Optional[bool] = None,
        emergency_action_enabled: Optional[bool] = None,
        emergency_recipient: Optional[str] = None,
        location_freshness_seconds: float = 120.0,
        max_action_retries: int = 2,
        action_retry_backoff_seconds: float = 30.0,
        action_claim_timeout_seconds: float = 300.0,
    ) -> None:
        self.database = database or Database(db_path)
        self.sessions = SessionStore(self.database)
        self.geocoder = geocoder or ReverseGeocoder()
        self.engine_factory = engine_factory
        self._engines: dict[tuple[int, str], CARESDecisionEngine] = {}
        self._engine_lock = threading.RLock()
        self.events = EventBus()
        if hardware_sampling_rate_hz <= 0:
            raise ValueError("hardware_sampling_rate_hz must be positive.")
        if hardware_stale_timeout_seconds <= 0:
            raise ValueError("hardware_stale_timeout_seconds must be positive.")
        self.hardware_sampling_rate_hz = float(hardware_sampling_rate_hz)
        self.hardware_stale_timeout_seconds = float(hardware_stale_timeout_seconds)
        self._hardware_clock = hardware_clock
        self._hardware_devices: dict[str, HardwareDevice] = {}
        self._hardware_streams: dict[tuple[str, str], HardwareStreamState] = {}
        self._hardware_lock = threading.RLock()
        self.location_freshness_seconds = float(location_freshness_seconds)
        if self.location_freshness_seconds <= 0:
            raise ValueError("location_freshness_seconds must be positive.")
        self.max_action_retries = max(0, int(max_action_retries))
        self.action_retry_backoff_seconds = max(1.0, float(action_retry_backoff_seconds))
        self.action_claim_timeout_seconds = max(1.0, float(action_claim_timeout_seconds))
        configured_sms, configured_voice = configured_live_providers()
        if sms_provider is None and voice_provider is None:
            self.sms_provider = configured_sms
            self.voice_provider = configured_voice
        else:
            self.sms_provider = sms_provider
            self.voice_provider = voice_provider
        self.external_actions_enabled = (
            bool(enable_live_actions)
            if enable_live_actions is not None
            else bool(self.sms_provider or self.voice_provider)
        )
        self.emergency_action_enabled = (
            os.getenv("CARES_ENABLE_EMERGENCY_ACTION", "0") == "1"
            if emergency_action_enabled is None
            else bool(emergency_action_enabled)
        )
        self.emergency_recipient = str(
            emergency_recipient or os.getenv("CARES_EMERGENCY_CALL_TO", "")
        ).strip() or None
        self._action_lock = threading.RLock()
        self._action_worker_stop = threading.Event()
        self._action_worker: Optional[threading.Thread] = None
        self._recover_processing_actions()
        self._load_hardware_devices(hardware_devices)

    # ---------------------- external action execution --------------------

    def _recover_processing_actions(self, *, startup: bool = True) -> None:
        """Recover claims without retrying an unknown provider submission.

        An attempt marked SUBMITTING may already have reached the provider even
        if CARES crashed before saving its response. It is therefore terminal
        UNKNOWN, not safe to retry. Claims with no provider submission are
        re-queued after their lease expires (or immediately when no marker was
        persisted).
        """
        rows = self.database.fetch_all(
            "SELECT id, metadata FROM guardian_action_events WHERE status = 'PROCESSING'"
        )
        now = datetime.now(timezone.utc)
        with self.database.transaction() as connection:
            for row in rows:
                attempts = connection.execute(
                    "SELECT status FROM action_attempts WHERE action_id = ?",
                    (int(row["id"]),),
                ).fetchall()
                metadata = _decode_json(row["metadata"])
                started = None
                if isinstance(metadata, dict) and metadata.get("processing_started_at"):
                    try:
                        started = datetime.fromisoformat(str(metadata["processing_started_at"]))
                    except ValueError:
                        started = None
                expired = started is None or (now - started).total_seconds() >= self.action_claim_timeout_seconds
                if not (startup or expired):
                    continue
                if any(str(attempt["status"]) == "SUBMITTING" for attempt in attempts):
                    connection.execute(
                        "UPDATE action_attempts SET status = 'UNKNOWN', next_attempt_at = NULL, "
                        "failure_reason = ?, updated_at = ? WHERE action_id = ? AND status = 'SUBMITTING'",
                        ("worker interrupted during provider submission", utc_now(), int(row["id"])),
                    )
                    connection.execute(
                        "UPDATE guardian_action_events SET status = 'UNKNOWN' WHERE id = ?",
                        (int(row["id"]),),
                    )
                    continue
                connection.execute(
                    "UPDATE guardian_action_events SET status = 'QUEUED' WHERE id = ?",
                    (int(row["id"]),),
                )

    def _action_is_externally_queueable(self, action_type: str) -> bool:
        return self.external_actions_enabled and action_type in {
            "GUARDIAN_NOTIFICATION", "GUARDIAN_COMMUNICATION", "EMERGENCY_ALERT",
        }

    def start_action_worker(self, interval_seconds: float = 2.0) -> None:
        """Start the recoverable worker used by the production HTTP server."""
        if not self.external_actions_enabled or self._action_worker is not None:
            return
        interval = max(0.25, float(interval_seconds))
        self._action_worker_stop.clear()

        def run() -> None:
            while not self._action_worker_stop.is_set():
                try:
                    self._recover_processing_actions(startup=False)
                    self.process_pending_actions_once()
                except Exception:
                    # A provider or one malformed row must not kill ingestion.
                    pass
                self._action_worker_stop.wait(interval)

        self._action_worker = threading.Thread(
            target=run, name="cares-action-worker", daemon=True
        )
        self._action_worker.start()

    def stop_action_worker(self) -> None:
        self._action_worker_stop.set()
        worker = self._action_worker
        if worker is not None and worker is not threading.current_thread():
            worker.join(timeout=5.0)
        self._action_worker = None

    def close(self) -> None:
        self.stop_action_worker()
        self.database.close()

    def process_pending_actions_once(self, limit: int = 25) -> int:
        """Execute a bounded batch of persisted action intents.

        This method is also the deterministic recovery hook used by tests and
        deployments that provide their own scheduler.
        """
        if not self.external_actions_enabled:
            return 0
        with self._action_lock:
            rows = self.database.fetch_all(
                "SELECT * FROM guardian_action_events WHERE status = 'QUEUED' "
                "ORDER BY id LIMIT ?", (max(1, min(int(limit), 100)),)
            )
            count = 0
            for row in rows:
                # Claim in SQLite so two local workers cannot submit the same
                # action concurrently. A claimed row is recoverable on restart.
                claim_metadata = _decode_json(row["metadata"])
                if not isinstance(claim_metadata, dict):
                    claim_metadata = {}
                claim_metadata["processing_started_at"] = utc_now()
                with self.database.transaction() as connection:
                    claimed = connection.execute(
                        "UPDATE guardian_action_events SET status = 'PROCESSING', metadata = ? "
                        "WHERE id = ? AND status = 'QUEUED'",
                        (_json(claim_metadata), int(row["id"])),
                    ).rowcount
                if claimed != 1:
                    continue
                fresh = self.database.fetch_one(
                    "SELECT * FROM guardian_action_events WHERE id = ?", (int(row["id"]),)
                )
                if fresh is not None:
                    self._execute_action(dict(fresh))
                    count += 1
            return count

    @staticmethod
    def _retry_time_due(value: Optional[str]) -> bool:
        if not value:
            return True
        try:
            return datetime.fromisoformat(value) <= datetime.now(timezone.utc)
        except ValueError:
            return True

    def _action_attempt(self, action_id: int, recipient_id: Optional[int], channel: str) -> Optional[dict[str, Any]]:
        if recipient_id is None:
            row = self.database.fetch_one(
                "SELECT * FROM action_attempts WHERE action_id = ? AND recipient_id IS NULL AND channel = ?",
                (action_id, channel),
            )
        else:
            row = self.database.fetch_one(
                "SELECT * FROM action_attempts WHERE action_id = ? AND recipient_id = ? AND channel = ?",
                (action_id, recipient_id, channel),
            )
        return dict(row) if row else None

    def _ensure_action_attempt(
        self, action_id: int, recipient_id: Optional[int], channel: str
    ) -> dict[str, Any]:
        current = self._action_attempt(action_id, recipient_id, channel)
        if current is not None:
            return current
        now = utc_now()
        self.database.execute(
            "INSERT INTO action_attempts(action_id, recipient_id, channel, status, updated_at, metadata) "
            "VALUES (?, ?, ?, 'QUEUED', ?, ?)",
            (action_id, recipient_id, channel, now, _json({})),
        )
        return self._action_attempt(action_id, recipient_id, channel)  # type: ignore[return-value]

    def _update_action_attempt(
        self,
        attempt_id: int,
        *,
        status: str,
        attempt_count: Optional[int] = None,
        provider_reference: Optional[str] = None,
        requested_at: Optional[str] = None,
        next_attempt_at: Optional[str] = None,
        failure_reason: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        current = self.database.fetch_one(
            "SELECT * FROM action_attempts WHERE id = ?", (attempt_id,)
        )
        if current is None:
            return
        current_data = dict(current)
        merged = _decode_json(current_data["metadata"])
        if not isinstance(merged, dict):
            merged = {}
        merged.update(metadata or {})
        self.database.execute(
            "UPDATE action_attempts SET status = ?, attempt_count = ?, provider_reference = ?, "
            "requested_at = ?, updated_at = ?, next_attempt_at = ?, failure_reason = ?, metadata = ? "
            "WHERE id = ?",
            (
                status,
                current_data["attempt_count"] if attempt_count is None else attempt_count,
                provider_reference if provider_reference is not None else current_data["provider_reference"],
                requested_at if requested_at is not None else current_data["requested_at"],
                utc_now(),
                next_attempt_at,
                failure_reason,
                _json(merged),
                attempt_id,
            ),
        )

    def _action_metadata(self, action_id: int) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT metadata FROM guardian_action_events WHERE id = ?", (action_id,)
        )
        value = _decode_json(row["metadata"]) if row else {}
        return value if isinstance(value, dict) else {}

    def _set_action_status(
        self, action_id: int, user_id: int, status: str, metadata: Optional[dict[str, Any]] = None
    ) -> None:
        merged = self._action_metadata(action_id)
        merged.update(metadata or {})
        if status == "PROCESSING":
            merged["processing_started_at"] = utc_now()
        self.database.execute(
            "UPDATE guardian_action_events SET status = ?, metadata = ? WHERE id = ? AND user_id = ?",
            (status, _json(merged), action_id, user_id),
        )
        event = self.get_action(user_id, action_id)
        self.events.publish(user_id, {"type": "guardian_action", "data": event})

    def _fresh_location_for_alert(
        self, user_id: int, session_id: str, maximum_timestamp: Optional[float] = None
    ) -> Optional[dict[str, Any]]:
        if maximum_timestamp is None:
            location = self.latest_location(user_id, session_id=session_id)
        else:
            location = self._location_before(user_id, session_id, maximum_timestamp)
        if location is None:
            return None
        now = float(self._hardware_clock())
        if location.get("timestamp") is None:
            return None
        freshness_timestamp = location.get("received_at") or location.get("timestamp")
        age = now - float(freshness_timestamp)
        if age < -MAX_LOCATION_TIMESTAMP_SKEW_SECONDS or age > self.location_freshness_seconds:
            return None
        return location

    def _alert_message(self, action: dict[str, Any]) -> str:
        event = self._engine_event(int(action["engine_event_id"]), int(action["user_id"]))
        message = (
            "CARES HIGH-RISK ALERT: monitoring requires immediate attention. "
            f"Event time {float(event['timestamp']):.0f}."
        )
        location = self._fresh_location_for_alert(
            int(action["user_id"]), str(action["session_id"]), float(event["timestamp"])
        )
        if location is not None:
            message += (
                f" Current authorized location: {float(location['latitude']):.6f},"
                f" {float(location['longitude']):.6f}."
            )
        else:
            message += " Current authorized location is unavailable."
        return message[:480]

    def _recipients_for_action(self, action: dict[str, Any], channel: str) -> list[tuple[Optional[int], str]]:
        action_type = action["action_type"]
        if action_type == "EMERGENCY_ALERT":
            if not self.emergency_action_enabled or not self.emergency_recipient:
                return []
            return [(None, self.emergency_recipient)]
        guardians = self.database.fetch_all(
            "SELECT id, phone_number FROM guardian_contacts WHERE user_id = ? ORDER BY id",
            (action["user_id"],),
        )
        return [(int(row["id"]), str(row["phone_number"])) for row in guardians]

    def _aggregate_action_status(self, action_id: int, user_id: int) -> None:
        rows = self.database.fetch_all(
            "SELECT status FROM action_attempts WHERE action_id = ?", (action_id,)
        )
        statuses = {str(row["status"]) for row in rows}
        if not statuses:
            return
        if "UNKNOWN" in statuses:
            status = "UNKNOWN"
        elif statuses <= {"DELIVERED"}:
            status = "DELIVERED"
        elif statuses & {"SUBMITTED", "DELIVERED"}:
            status = "SUBMITTED"
        elif "RETRY_WAIT" in statuses or "QUEUED" in statuses:
            status = "QUEUED"
        elif statuses <= {"FAILED"}:
            status = "FAILED"
        else:
            status = "PROCESSING"
        self._set_action_status(action_id, user_id, status, {"attempt_statuses": sorted(statuses)})

    def _execute_action(self, raw_action: dict[str, Any]) -> None:
        action = dict(raw_action)
        action_type = str(action["action_type"])
        if action_type not in {"GUARDIAN_NOTIFICATION", "GUARDIAN_COMMUNICATION", "EMERGENCY_ALERT"}:
            return
        if action_type == "GUARDIAN_NOTIFICATION":
            provider = self.sms_provider
            channel = "sms"
        else:
            provider = self.voice_provider
            channel = "voice"
        recipients = self._recipients_for_action(action, channel)
        if not recipients:
            reason = (
                "emergency action is disabled or recipient is not configured"
                if action_type == "EMERGENCY_ALERT"
                else "no authorized guardian recipient is configured"
            )
            self._set_action_status(int(action["id"]), int(action["user_id"]), "FAILED", {"failure_reason": reason})
            return
        if provider is None:
            self._set_action_status(
                int(action["id"]), int(action["user_id"]), "FAILED",
                {"failure_reason": f"{channel} provider is not configured"},
            )
            return

        message = self._alert_message(action)
        action_id = int(action["id"])
        user_id = int(action["user_id"])
        self._set_action_status(action_id, user_id, "PROCESSING")
        for recipient_id, recipient in recipients:
            attempt = self._ensure_action_attempt(action_id, recipient_id, channel)
            if attempt["status"] in {"SUBMITTED", "DELIVERED", "UNKNOWN", "FAILED"}:
                continue
            if not self._retry_time_due(attempt.get("next_attempt_at")):
                continue
            attempt_count = int(attempt["attempt_count"]) + 1
            if attempt_count > self.max_action_retries + 1:
                self._update_action_attempt(
                    int(attempt["id"]), status="FAILED", attempt_count=attempt_count,
                    failure_reason="retry limit reached",
                )
                continue
            self._update_action_attempt(
                int(attempt["id"]), status="SUBMITTING", attempt_count=attempt_count,
                requested_at=utc_now(), next_attempt_at=None, failure_reason=None,
            )
            idempotency_key = f"cares-action-{action_id}-{channel}-{recipient_id or 'emergency'}"
            try:
                result = provider.submit(recipient, message, idempotency_key)
            except ProviderError as exc:
                if exc.ambiguous:
                    attempt_status = "UNKNOWN"
                    next_attempt = None
                elif exc.retryable and attempt_count <= self.max_action_retries:
                    attempt_status = "RETRY_WAIT"
                    next_attempt = datetime.fromtimestamp(
                        time.time() + self.action_retry_backoff_seconds * (2 ** (attempt_count - 1)),
                        timezone.utc,
                    ).isoformat()
                else:
                    attempt_status = "FAILED"
                    next_attempt = None
                self._update_action_attempt(
                    int(attempt["id"]), status=attempt_status, attempt_count=attempt_count,
                    next_attempt_at=next_attempt, failure_reason=str(exc)[:240],
                )
            except Exception:
                self._update_action_attempt(
                    int(attempt["id"]), status="UNKNOWN", attempt_count=attempt_count,
                    failure_reason="provider execution failed",
                )
            else:
                self._update_action_attempt(
                    int(attempt["id"]), status=result.status,
                    attempt_count=attempt_count,
                    provider_reference=result.provider_reference,
                    failure_reason=None,
                    metadata={"provider_detail": result.detail} if result.detail else None,
                )
        self._aggregate_action_status(action_id, user_id)

    def action_attempts(self, user_id: int, action_id: int) -> list[dict[str, Any]]:
        if self.database.fetch_one(
            "SELECT id FROM guardian_action_events WHERE id = ? AND user_id = ?",
            (action_id, user_id),
        ) is None:
            raise NotFoundError("Guardian action event not found.")
        rows = self.database.fetch_all(
            "SELECT id, action_id, recipient_id, channel, status, attempt_count, "
            "provider_reference, requested_at, updated_at, next_attempt_at, "
            "failure_reason, metadata FROM action_attempts WHERE action_id = ? ORDER BY id",
            (action_id,),
        )
        result = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _decode_json(item["metadata"])
            result.append(item)
        return result

    def process_twilio_status(
        self, data: Mapping[str, Any], headers: Mapping[str, str], callback_url: str
    ) -> None:
        provider = self.sms_provider
        if not isinstance(provider, TwilioSmsProvider):
            raise AuthenticationError("Provider callback is not configured.")
        signature = next(
            (value for key, value in headers.items() if key.lower() == "x-twilio-signature"),
            "",
        )
        if not provider.verify_callback(callback_url, data, signature):
            raise AuthenticationError("Invalid provider callback signature.")
        reference = str(data.get("MessageSid") or "")
        status_map = {
            "queued": "SUBMITTED", "accepted": "SUBMITTED", "sending": "SUBMITTED",
            "sent": "SUBMITTED", "delivered": "DELIVERED",
            "failed": "FAILED", "undelivered": "FAILED",
        }
        status = status_map.get(str(data.get("MessageStatus") or "").lower())
        if not reference or status is None:
            raise ValidationError("Provider callback is missing a supported status.")
        row = self.database.fetch_one(
            "SELECT aa.id, aa.action_id, aa.status, ga.user_id FROM action_attempts aa "
            "JOIN guardian_action_events ga ON ga.id = aa.action_id "
            "WHERE aa.provider_reference = ? AND aa.channel = 'sms'",
            (reference,),
        )
        if row is None:
            raise NotFoundError("Provider action reference not found.")
        current_status = str(row["status"])
        if current_status in {"DELIVERED", "FAILED", "UNKNOWN"}:
            if current_status == status:
                return
            raise ConflictError("Invalid provider status transition.")
        self._update_action_attempt(int(row["id"]), status=status, provider_reference=reference)
        self._aggregate_action_status(int(row["action_id"]), int(row["user_id"]))

    def process_twilio_voice_status(
        self, data: Mapping[str, Any], headers: Mapping[str, str], callback_url: str
    ) -> None:
        provider = self.voice_provider
        if provider is None or not hasattr(provider, "verify_callback"):
            raise AuthenticationError("Voice provider callback is not configured.")
        signature = next(
            (value for key, value in headers.items() if key.lower() == "x-twilio-signature"),
            "",
        )
        if not provider.verify_callback(callback_url, data, signature):
            raise AuthenticationError("Invalid provider callback signature.")
        reference = str(data.get("CallSid") or "")
        status_map = {
            "queued": "SUBMITTED", "initiated": "SUBMITTED", "ringing": "SUBMITTED",
            "answered": "SUBMITTED", "in-progress": "SUBMITTED",
            "completed": "DELIVERED", "busy": "FAILED", "no-answer": "FAILED",
            "canceled": "FAILED", "failed": "FAILED",
        }
        status = status_map.get(str(data.get("CallStatus") or "").lower())
        if not reference or status is None:
            raise ValidationError("Provider callback is missing a supported status.")
        row = self.database.fetch_one(
            "SELECT aa.id, aa.action_id, aa.status, ga.user_id FROM action_attempts aa "
            "JOIN guardian_action_events ga ON ga.id = aa.action_id "
            "WHERE aa.provider_reference = ? AND aa.channel = 'voice'",
            (reference,),
        )
        if row is None:
            raise NotFoundError("Provider action reference not found.")
        current_status = str(row["status"])
        if current_status in {"DELIVERED", "FAILED", "UNKNOWN"}:
            if current_status == status:
                return
            raise ConflictError("Invalid provider status transition.")
        self._update_action_attempt(int(row["id"]), status=status, provider_reference=reference)
        self._aggregate_action_status(int(row["action_id"]), int(row["user_id"]))

    # ------------------------- monitoring sessions -----------------------

    def start_monitoring_session(
        self,
        user_id: int,
        source: str = "REAL_HARDWARE",
        scenario: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        _validate_user_id(self.database, user_id)
        source = str(source or "").strip().upper()
        if not source or len(source) > 80:
            raise ValidationError("Monitoring source is required.")
        session_id = session_id or uuid.uuid4().hex
        if self.database.fetch_one("SELECT id FROM monitoring_sessions WHERE id = ?", (session_id,)):
            raise ConflictError("Monitoring session already exists.")
        self.database.execute(
            "INSERT INTO monitoring_sessions(id, user_id, source, scenario, status, started_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, user_id, source, scenario, "ACTIVE", utc_now(), _json(metadata or {})),
        )
        return self.get_monitoring_session(user_id, session_id)

    def stop_monitoring_session(self, user_id: int, session_id: str) -> dict[str, Any]:
        session = self.get_monitoring_session(user_id, session_id)
        self.database.execute(
            "UPDATE monitoring_sessions SET status = ?, stopped_at = ? WHERE id = ? AND user_id = ?",
            ("STOPPED", utc_now(), session_id, user_id),
        )
        self.discard_runtime(user_id, session_id)
        return self.get_monitoring_session(user_id, session_id)

    def get_monitoring_session(self, user_id: int, session_id: str) -> dict[str, Any]:
        row = self.database.fetch_one(
            "SELECT * FROM monitoring_sessions WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
        if row is None:
            raise NotFoundError("Monitoring session not found.")
        item = dict(row)
        item["metadata"] = _decode_json(item["metadata"])
        return item

    def list_monitoring_sessions(self, user_id: int, limit: int = 50) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        limit = max(1, min(int(limit), 200))
        return [
            self.get_monitoring_session(user_id, row["id"])
            for row in self.database.fetch_all(
                "SELECT id FROM monitoring_sessions WHERE user_id = ? ORDER BY started_at DESC LIMIT ?",
                (user_id, limit),
            )
        ]

    def export_user_data(self, user_id: int) -> dict[str, Any]:
        """Return an ownership-scoped export without credentials or session tokens."""
        user = self.user(user_id)
        sessions = [
            self.get_monitoring_session(user_id, row["id"])
            for row in self.database.fetch_all(
                "SELECT id FROM monitoring_sessions WHERE user_id = ? ORDER BY started_at",
                (user_id,),
            )
        ]
        events = [
            self._decode_engine_event(row)
            for row in self.database.fetch_all(
                "SELECT * FROM engine_events WHERE user_id = ? ORDER BY id", (user_id,)
            )
        ]
        locations = [
            dict(row)
            for row in self.database.fetch_all(
                "SELECT * FROM location_events WHERE user_id = ? ORDER BY id", (user_id,)
            )
        ]
        incidents = []
        for row in self.database.fetch_all(
            "SELECT * FROM incidents WHERE user_id = ? ORDER BY id", (user_id,)
        ):
            item = dict(row)
            engine_row = self.database.fetch_one(
                "SELECT * FROM engine_events WHERE id = ? AND user_id = ?",
                (item["engine_event_id"], user_id),
            )
            item["engine_event"] = self._decode_engine_event(engine_row) if engine_row else None
            item["location"] = (
                self._location(item["location_event_id"], user_id)
                if item["location_event_id"]
                else None
            )
            item["actions"] = [
                self._action_with_attempts(user_id, action)
                for action in self.database.fetch_all(
                    "SELECT * FROM guardian_action_events WHERE user_id = ? "
                    "AND engine_event_id = ? ORDER BY id",
                    (user_id, item["engine_event_id"]),
                )
            ]
            incidents.append(item)
        actions = [
            self._action_with_attempts(user_id, row)
            for row in self.database.fetch_all(
                "SELECT * FROM guardian_action_events WHERE user_id = ? ORDER BY id",
                (user_id,),
            )
        ]
        return {
            "format": "cares-user-export-v1",
            "exported_at": utc_now(),
            "user": user,
            "guardians": self.list_guardians(user_id),
            "monitoring_sessions": sessions,
            "engine_events": events,
            "incidents": incidents,
            "actions": actions,
            "locations": locations,
            "baseline_daily_records": [
                dict(row)
                for row in self.database.fetch_all(
                    "SELECT * FROM baseline_daily_records WHERE user_id = ? ORDER BY date",
                    (user_id,),
                )
            ],
            "baseline_adaptation_events": [
                dict(row)
                for row in self.database.fetch_all(
                    "SELECT * FROM baseline_adaptation_events WHERE user_id = ? ORDER BY id",
                    (user_id,),
                )
            ],
        }

    def delete_monitoring_session(
        self, user_id: int, session_id: str, confirm: object = False
    ) -> dict[str, Any]:
        """Delete one stopped, user-owned monitoring session and its records."""
        if confirm is not True:
            raise ValidationError("Explicit confirmation is required to delete a monitoring session.")
        session = self.get_monitoring_session(user_id, session_id)
        if session["status"] == "ACTIVE":
            raise ConflictError("Stop the monitoring session before deleting it.")
        with self.database.transaction() as connection:
            counts = {}
            for table in (
                "incidents", "guardian_action_events", "engine_events",
                "location_events", "baseline_daily_records",
                "baseline_adaptation_events",
            ):
                cursor = connection.execute(
                    f"DELETE FROM {table} WHERE user_id = ? AND session_id = ?",
                    (user_id, session_id),
                )
                counts[table] = cursor.rowcount
            connection.execute(
                "DELETE FROM monitoring_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
        with self._engine_lock:
            self._engines.pop((user_id, session_id), None)
        with self._hardware_lock:
            self._hardware_streams = {
                key: stream
                for key, stream in self._hardware_streams.items()
                if not (stream.session_id == session_id)
            }
        self.events.publish(
            user_id,
            {"type": "monitoring_session_deleted", "data": {"session_id": session_id}},
        )
        return {"session_id": session_id, "deleted": True, "counts": counts}

    def system_status(self, user_id: int) -> dict[str, Any]:
        """Expose operational state without secrets or provider credentials."""
        _validate_user_id(self.database, user_id)
        return {
            "live_actions_enabled": self.external_actions_enabled,
            "sms_provider": "configured" if self.sms_provider is not None else "unconfigured",
            "voice_provider": "configured" if self.voice_provider is not None else "unconfigured",
            "public_safety_dispatch": False,
        }

    def discard_runtime(self, user_id: int, session_id: str) -> None:
        """Drop the in-memory engine so calibration cannot cross sessions."""
        with self._engine_lock:
            self._engines.pop((user_id, session_id), None)
        with self._hardware_lock:
            for key, stream in self._hardware_streams.items():
                if stream.session_id == session_id:
                    stream.status = "STOPPED"

    # ------------------------ hardware ingestion -------------------------

    def _load_hardware_devices(
        self,
        configured: Optional[Mapping[str, Mapping[str, Any]]],
    ) -> None:
        if configured is None:
            raw = os.getenv("CARES_HARDWARE_DEVICES", "")
            if not raw:
                return
            try:
                configured = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError("CARES_HARDWARE_DEVICES must be valid JSON.") from exc
        if not isinstance(configured, Mapping):
            raise ValueError("Hardware devices must be a device_id mapping.")
        for device_id, values in configured.items():
            if not isinstance(values, Mapping):
                raise ValueError("Each hardware device configuration must be an object.")
            self._hardware_devices[str(device_id)] = HardwareDevice.from_config(
                str(device_id), values.get("user_id"), values.get("token")
            )

    def register_hardware_device(self, user_id: int, device_id: str, token: str) -> None:
        """Register a device binding for deployment setup; raw tokens are not stored."""
        _validate_user_id(self.database, user_id)
        device_id = str(device_id or "").strip()
        if not device_id:
            raise ValidationError("Hardware device_id is required.")
        with self._hardware_lock:
            self._hardware_devices[device_id] = HardwareDevice.from_config(device_id, user_id, token)

    @staticmethod
    def _header(headers: Mapping[str, str], name: str) -> Optional[str]:
        return next((value for key, value in headers.items() if key.lower() == name.lower()), None)

    def _authenticate_hardware(
        self,
        headers: Mapping[str, str],
    ) -> HardwareDevice:
        device_id = self._header(headers, "X-CARES-Device-Id")
        token = self._header(headers, "X-CARES-Device-Token")
        if not device_id or not token:
            raise AuthenticationError("Hardware device credentials are required.")
        device = self._hardware_devices.get(device_id.strip())
        if device is None or not device.verify(token):
            raise AuthenticationError("Invalid hardware device credentials.")
        return device

    def _hardware_engine(self, user_id: int, session_id: str) -> Optional[CARESDecisionEngine]:
        with self._engine_lock:
            return self._engines.get((user_id, session_id))

    def _begin_hardware_epoch(self, user_id: int, session_id: str, stream: HardwareStreamState) -> None:
        stream.reset_evidence_epoch()
        engine = self._hardware_engine(user_id, session_id)
        if engine is not None:
            engine.start_new_evidence_epoch()

    def _mark_hardware_stale(self, stream: HardwareStreamState, now: float) -> bool:
        if (
            stream.last_received_at is not None
            and stream.status not in {"STALE", "STOPPED"}
            and now - stream.last_received_at > self.hardware_stale_timeout_seconds
        ):
            stream.status = "STALE"
            return True
        return stream.status == "STALE"

    def ingest_hardware_packet(
        self,
        data: Mapping[str, Any],
        headers: Mapping[str, str],
    ) -> dict[str, Any]:
        """Authenticate, validate, estimate, and feed one raw-BVP packet."""
        device = self._authenticate_hardware(headers)
        try:
            packet = HardwarePacket.from_dict(data, self.hardware_sampling_rate_hz)
        except HardwarePacketError as exc:
            raise ValidationError(str(exc)) from exc
        if packet.device_id != device.device_id:
            raise AuthenticationError("Device credential does not match packet device_id.")
        now = float(self._hardware_clock())
        self._validate_packet_location(packet.location, packet.device_timestamp_start)

        with self._hardware_lock:
            if packet.session_id is None:
                session = self._active_session_for_source(device.user_id, "REAL_HARDWARE")
                if session is None:
                    session = self.start_monitoring_session(device.user_id, source="REAL_HARDWARE")
            else:
                session = self.get_monitoring_session(device.user_id, packet.session_id)
            if session["status"] != "ACTIVE" or session["source"] != "REAL_HARDWARE":
                raise ValidationError("Hardware session is not active.")

            key = (device.device_id, session["id"])
            stream = self._hardware_streams.get(key)
            if stream is None:
                stream = HardwareStreamState(
                    device_id=device.device_id,
                    session_id=session["id"],
                    estimator=WristBVPHREstimator(self.hardware_sampling_rate_hz),
                    buffer=deque(),
                )
                self._hardware_streams[key] = stream

            connection_changed = (
                stream.connection_id is not None
                and packet.connection_id != stream.connection_id
            )
            if connection_changed and packet.connection_id in stream.retired_connection_ids:
                raise ConflictError("Packet belongs to a retired hardware connection.")

            # Ordering checks happen against the current connection before any
            # stream fields are changed. A rejected packet must be state-free.
            if not connection_changed:
                if stream.last_sequence is not None and packet.sequence <= stream.last_sequence:
                    raise ConflictError("Duplicate or out-of-order hardware sequence.")
                if (
                    stream.last_device_timestamp_start is not None
                    and packet.device_timestamp_start <= stream.last_device_timestamp_start
                ):
                    raise ConflictError("Duplicate or out-of-order device timestamp.")

            was_stale = self._mark_hardware_stale(stream, now)
            sequence_gap = stream.last_sequence is not None and packet.sequence > stream.last_sequence + 1
            device_gap = False
            if stream.last_device_timestamp_start is not None:
                expected = (
                    stream.last_device_timestamp_start
                    + stream.last_packet_sample_count / packet.sampling_rate_hz
                )
                device_gap = packet.device_timestamp_start - expected > self.hardware_stale_timeout_seconds
            reconnected = was_stale or sequence_gap or device_gap or connection_changed
            if connection_changed:
                stream.retired_connection_ids.add(stream.connection_id)  # type: ignore[arg-type]
                stream.connection_id = packet.connection_id
                stream.last_sequence = None
                stream.last_device_timestamp_start = None
                stream.last_packet_sample_count = 0
            elif stream.connection_id is None:
                stream.connection_id = packet.connection_id
            if packet.location is not None:
                self._persist_hardware_location(
                    device.user_id, session["id"], packet.device_id, packet.location, now
                )
            if reconnected:
                self._begin_hardware_epoch(device.user_id, session["id"], stream)

            stream.last_sequence = packet.sequence
            stream.last_device_timestamp_start = packet.device_timestamp_start
            stream.last_packet_sample_count = len(packet.bvp_samples)
            stream.last_received_at = now
            stream.status = "CONNECTED"
            stream.last_error = None
            stream.append(packet.bvp_samples)

            window = stream.window()
            if window is None:
                return self._hardware_response(
                    stream, packet, reconnected=reconnected, estimate=None, engine_output=None
                )

            estimate_timestamp = packet.device_timestamp_start + (len(packet.bvp_samples) - 1) / packet.sampling_rate_hz
            estimate = stream.estimator.estimate_window(window, timestamp=estimate_timestamp)
            stream.advance_window()
            if not estimate.accepted:
                stream.status = "RECEIVING_DATA"
                return self._hardware_response(
                    stream, packet, reconnected=reconnected, estimate=estimate.to_dict(), engine_output=None
                )

            sample = PhysiologicalSample(
                timestamp=estimate.timestamp,
                heart_rate_bpm=estimate.heart_rate_bpm,
                additional_metrics={
                    "hr_valid": 1.0 if estimate.accepted else 0.0,
                    "bvp_quality": estimate.quality,
                },
            )
            output = self.process_sample(
                device.user_id, sample, session_id=session["id"], source="REAL_HARDWARE"
            )
            stream.status = "RECEIVING_DATA"
            return self._hardware_response(
                stream, packet, reconnected=reconnected, estimate=estimate.to_dict(), engine_output=output
            )

    def _validate_packet_location(
        self, location: Optional[HardwareLocationPacket], packet_timestamp: float
    ) -> None:
        if location is None:
            return
        if location.timestamp > packet_timestamp + MAX_LOCATION_TIMESTAMP_SKEW_SECONDS:
            raise ValidationError("Hardware location timestamp is in the future.")
        if packet_timestamp - location.timestamp > self.location_freshness_seconds:
            raise ValidationError("Hardware location is stale.")

    def _persist_hardware_location(
        self,
        user_id: int,
        session_id: str,
        device_id: str,
        location: HardwareLocationPacket,
        received_at: float,
    ) -> dict[str, Any]:
        validated = validate_hardware_location(
            location.latitude,
            location.longitude,
            location.accuracy,
            location.timestamp,
            "hardware-gps",
        )
        resolved = self.geocoder.resolve(validated)
        cursor = self.database.execute(
            "INSERT INTO location_events(user_id, timestamp, latitude, longitude, accuracy, source, formatted_address, provider, session_id, device_id, received_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, validated.timestamp, validated.latitude, validated.longitude,
                validated.accuracy, validated.source, resolved.formatted_address,
                resolved.provider, session_id, device_id, received_at,
            ),
        )
        event = self._location(int(cursor.lastrowid), user_id)
        self.events.publish(user_id, {"type": "location", "data": event})
        return event

    @staticmethod
    def _hardware_response(
        stream: HardwareStreamState,
        packet: HardwarePacket,
        reconnected: bool,
        estimate: Optional[dict[str, Any]],
        engine_output: Optional[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "accepted": True,
            "device_id": packet.device_id,
            "session_id": stream.session_id,
            "connection_id": packet.connection_id,
            "sequence": packet.sequence,
            "transport_status": stream.status,
            "reconnected": reconnected,
            "transport_epoch": stream.transport_epoch,
            "samples_buffered": len(stream.buffer),
            "estimate": estimate,
            "engine_output": engine_output,
        }

    def hardware_status(self, user_id: int, device_id: Optional[str] = None) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        now = float(self._hardware_clock())
        with self._hardware_lock:
            devices = [
                device for device in self._hardware_devices.values()
                if device.user_id == user_id and (device_id is None or device.device_id == device_id)
            ]
            result = []
            for device in devices:
                streams = [
                    stream for (stream_device, _), stream in self._hardware_streams.items()
                    if stream_device == device.device_id
                ]
                stream = max(streams, key=lambda item: item.last_received_at or 0.0, default=None)
                if stream is not None:
                    self._mark_hardware_stale(stream, now)
                result.append({
                    "device_id": device.device_id,
                    "transport_status": stream.status if stream else "DISCONNECTED",
                    "session_id": stream.session_id if stream else None,
                    "last_received_at": stream.last_received_at if stream else None,
                    "last_device_timestamp_start": stream.last_device_timestamp_start if stream else None,
                    "connection_id": stream.connection_id if stream else None,
                    "last_sequence": stream.last_sequence if stream else None,
                    "samples_buffered": len(stream.buffer) if stream else 0,
                    "transport_epoch": stream.transport_epoch if stream else 0,
                    "stale_timeout_seconds": self.hardware_stale_timeout_seconds,
                    "last_error": stream.last_error if stream else None,
                })
            return result

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

    def process_sample(
        self,
        user_id: int,
        sample: PhysiologicalSample,
        session_id: Optional[str] = None,
        source: str = "REAL_HARDWARE",
    ) -> dict[str, Any]:
        _validate_user_id(self.database, user_id)
        source = str(source or "REAL_HARDWARE").strip().upper()
        if session_id is None:
            session = self._active_session_for_source(user_id, source)
            if session is None:
                session = self.start_monitoring_session(user_id, source=source)
            session_id = session["id"]
        session = self.get_monitoring_session(user_id, session_id)
        if session["status"] != "ACTIVE":
            raise ValidationError("Monitoring session is not active.")
        if session["source"] != source:
            raise ValidationError("Sample source does not match monitoring session.")
        with self._engine_lock:
            engine = self._engines.setdefault((user_id, session_id), self.engine_factory())
            output = engine.process_sample(sample)
        return self.persist_engine_output(user_id, output, session_id=session_id, source=source)

    def _active_session_for_source(self, user_id: int, source: str) -> Optional[dict[str, Any]]:
        row = self.database.fetch_one(
            "SELECT id FROM monitoring_sessions WHERE user_id = ? AND source = ? AND status = 'ACTIVE' ORDER BY started_at DESC LIMIT 1",
            (user_id, source),
        )
        return self.get_monitoring_session(user_id, row["id"]) if row else None

    def persist_engine_output(
        self,
        user_id: int,
        output: EngineOutput,
        session_id: Optional[str] = None,
        source: str = "REAL_HARDWARE",
    ) -> dict[str, Any]:
        """Persist an existing EngineOutput without recalculating it."""
        _validate_user_id(self.database, user_id)
        source = str(source or "REAL_HARDWARE").strip().upper()
        if session_id is None:
            session = self._active_session_for_source(user_id, source)
            if session is None:
                session = self.start_monitoring_session(user_id, source=source)
            session_id = session["id"]
        session = self.get_monitoring_session(user_id, session_id)
        if session["status"] != "ACTIVE":
            raise ValidationError("Monitoring session is not active.")
        payload = output.to_dict()
        created_at = utc_now()
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO engine_events(user_id, timestamp, heart_rate, baseline, deviation, "
                "percentage_deviation, risk_level, risk_score, confidence, trend, persistence, "
                "recovery_state, reason_codes, explanation, recommended_actions, created_at, session_id, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    user_id, payload["timestamp"], payload["current_value"], payload["baseline"],
                    payload["deviation"], payload["pct_deviation"], payload["risk_level"],
                    payload["risk_score"], payload["confidence"], payload["trend"],
                    payload["persistence"], payload["recovery_state"], _json(payload["reason_codes"]),
                    payload["human_readable_explanation"], _json(payload["recommended_action"]), created_at, session_id, source,
                ),
            )
            event_id = int(cursor.lastrowid)
            is_high = payload["risk_level"] == RiskLevel.HIGH.value
            open_incident = None
            if is_high:
                open_incident = connection.execute(
                    "SELECT id FROM incidents WHERE user_id = ? AND session_id = ? "
                    "AND source = ? AND status = 'OPEN' ORDER BY id DESC LIMIT 1",
                    (user_id, session_id, source),
                ).fetchone()
            invalid_evidence = {
                "INVALID_OR_LOW_QUALITY_SAMPLE", "STALE_OR_OUT_OF_ORDER_SAMPLE",
            }
            legitimate_recovery = not invalid_evidence.intersection(payload["reason_codes"])
            if not is_high and legitimate_recovery:
                connection.execute(
                    "UPDATE incidents SET status = 'RESOLVED' WHERE user_id = ? "
                    "AND session_id = ? AND source = ? AND status = 'OPEN'",
                    (user_id, session_id, source),
                )

            # One OPEN HIGH incident owns one set of external action intents.
            # Repeated HIGH samples still persist engine events but do not
            # create duplicate incident/action rows.
            create_incident = is_high and open_incident is None
            if not is_high or create_incident:
                actions = payload["recommended_action"]
                for action in actions:
                    queued = self._action_is_externally_queueable(action)
                    initial_status = "QUEUED" if queued else "GENERATED"
                    metadata = {
                        "source": "GuardianActionMapper",
                        "execution_status": "QUEUED" if queued else "NOT_CONFIGURED",
                    }
                    connection.execute(
                        "INSERT INTO guardian_action_events(user_id, engine_event_id, action_type, status, timestamp, metadata, session_id, source) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (user_id, event_id, action, initial_status, payload["timestamp"], _json(metadata), session_id, source),
                    )

            location = self._latest_location_for_incident(
                user_id, connection=connection, session_id=session_id, source=source,
                maximum_timestamp=float(payload["timestamp"]),
            )
            incident_id = None
            if create_incident:
                incident_cursor = connection.execute(
                    "INSERT INTO incidents(user_id, engine_event_id, risk_level, timestamp, explanation, location_event_id, status, session_id, source) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (user_id, event_id, payload["risk_level"], payload["timestamp"], payload["human_readable_explanation"],
                     location["id"] if location else None, "OPEN", session_id, source),
                )
                incident_id = int(incident_cursor.lastrowid)

        event = self._engine_event(event_id, user_id)
        self.events.publish(user_id, {"type": "engine_output", "data": event})
        if incident_id is not None:
            self.events.publish(user_id, {"type": "incident", "data": self.get_incident(user_id, incident_id)})
        return event

    def get_current(self, user_id: int) -> dict[str, Any]:
        event = self._engine_event_for_user(user_id)
        session = self.get_monitoring_session(user_id, event["session_id"]) if event else None
        return {
            "engine_event": event,
            "session": session,
            "location": self.latest_location(user_id, session_id=event["session_id"] if event else None),
            "actions": self.list_actions(user_id, limit=20, session_id=event["session_id"] if event else None),
        }

    def history(
        self,
        user_id: int,
        limit: int = 100,
        session_id: Optional[str] = None,
        source: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        limit = max(1, min(int(limit), 500))
        clauses = ["user_id = ?"]
        parameters: list[object] = [user_id]
        if session_id:
            clauses.append("session_id = ?")
            parameters.append(session_id)
        if source:
            clauses.append("source = ?")
            parameters.append(str(source).upper())
        parameters.append(limit)
        rows = self.database.fetch_all(
            f"SELECT * FROM engine_events WHERE {' AND '.join(clauses)} ORDER BY id DESC LIMIT ?", parameters
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

    def _latest_location_for_incident(
        self, user_id: int, connection: Any = None,
        session_id: Optional[str] = None, source: str = "REAL_HARDWARE",
        maximum_timestamp: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        location = self._latest_location(
            user_id, connection=connection, session_id=session_id,
            maximum_timestamp=maximum_timestamp,
        )
        if location is None:
            return None
        # Legacy application-submitted locations retain their existing
        # behavior. Hardware GPS is attached to incidents only while fresh.
        if location.get("source") == "hardware-gps" and location.get("device_id"):
            now = float(self._hardware_clock())
            freshness_timestamp = location.get("received_at") or location.get("timestamp")
            age = now - float(freshness_timestamp)
            if age < -MAX_LOCATION_TIMESTAMP_SKEW_SECONDS or age > self.location_freshness_seconds:
                return None
        return location

    # ----------------------------- locations ------------------------------

    def ingest_location(self, user_id: int, data: dict[str, Any]) -> dict[str, Any]:
        _validate_user_id(self.database, user_id)
        session_id = str(data.get("session_id") or "").strip()
        if session_id:
            session = self.get_monitoring_session(user_id, session_id)
            if str(session["source"]).startswith("DEMO_"):
                raise ValidationError("Demo mode cannot claim hardware location.")
        else:
            session = self._active_session_for_source(user_id, "REAL_HARDWARE")
            if session is None:
                session = self.start_monitoring_session(user_id, source="REAL_HARDWARE")
            session_id = str(session["id"])
        location = validate_hardware_location(
            data.get("latitude"), data.get("longitude"), data.get("accuracy"),
            data.get("timestamp"), data.get("source"),
        )
        resolved = self.geocoder.resolve(location)
        cursor = self.database.execute(
            "INSERT INTO location_events(user_id, timestamp, latitude, longitude, accuracy, source, formatted_address, provider, session_id, device_id, received_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
            (user_id, location.timestamp, location.latitude, location.longitude, location.accuracy,
             location.source, resolved.formatted_address, resolved.provider, session_id),
        )
        event = self._location(int(cursor.lastrowid), user_id)
        self.events.publish(user_id, {"type": "location", "data": event})
        return event

    def _location_before(
        self, user_id: int, session_id: str, maximum_timestamp: float
    ) -> Optional[dict[str, Any]]:
        row = self.database.fetch_one(
            "SELECT * FROM location_events WHERE user_id = ? AND session_id = ? "
            "AND timestamp <= ? ORDER BY timestamp DESC, id DESC LIMIT 1",
            (user_id, session_id, maximum_timestamp),
        )
        return dict(row) if row else None

    def latest_location(self, user_id: int, session_id: Optional[str] = None) -> Optional[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        if session_id:
            row = self.database.fetch_one("SELECT * FROM location_events WHERE user_id = ? AND session_id = ? ORDER BY id DESC LIMIT 1", (user_id, session_id))
        else:
            row = self.database.fetch_one("SELECT * FROM location_events WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,))
        return dict(row) if row else None

    def _latest_location(
        self, user_id: int, connection: Any = None,
        session_id: Optional[str] = None, maximum_timestamp: Optional[float] = None,
    ) -> Optional[dict[str, Any]]:
        sql = "SELECT * FROM location_events WHERE user_id = ?"
        params: tuple[object, ...] = (user_id,)
        if session_id:
            sql += " AND session_id = ?"
            params += (session_id,)
        if maximum_timestamp is not None:
            sql += " AND timestamp <= ?"
            params += (maximum_timestamp,)
        sql += " ORDER BY timestamp DESC, id DESC LIMIT 1"
        query = connection.execute(sql, params) if connection else self.database.fetch_one(sql, params)
        row = query.fetchone() if connection else query
        return dict(row) if row else None

    def _location(self, location_id: int, user_id: int) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM location_events WHERE id = ? AND user_id = ?", (location_id, user_id))
        if row is None:
            raise NotFoundError("Location event not found.")
        return dict(row)

    # ------------------------- actions and incidents ---------------------

    def list_actions(self, user_id: int, limit: int = 100, session_id: Optional[str] = None) -> list[dict[str, Any]]:
        _validate_user_id(self.database, user_id)
        limit = max(1, min(int(limit), 500))
        if session_id:
            rows = self.database.fetch_all("SELECT * FROM guardian_action_events WHERE user_id = ? AND session_id = ? ORDER BY id DESC LIMIT ?", (user_id, session_id, limit))
        else:
            rows = self.database.fetch_all("SELECT * FROM guardian_action_events WHERE user_id = ? ORDER BY id DESC LIMIT ?", (user_id, limit))
        result = []
        for row in rows:
            item = self._decode_action(row)
            item["attempts"] = self.action_attempts(user_id, int(item["id"]))
            result.append(item)
        return result

    def get_action(self, user_id: int, action_id: int) -> dict[str, Any]:
        row = self.database.fetch_one("SELECT * FROM guardian_action_events WHERE id = ? AND user_id = ?", (action_id, user_id))
        if row is None:
            raise NotFoundError("Guardian action event not found.")
        item = self._decode_action(row)
        item["attempts"] = self.action_attempts(user_id, int(item["id"]))
        return item

    def update_action_status(self, user_id: int, action_id: int, status: str, metadata: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        status = str(status or "").upper()
        if status not in ACTION_STATUSES:
            raise ValidationError("Invalid guardian action status.")
        if status in {"QUEUED", "PROCESSING", "SUBMITTED", "DELIVERED", "UNKNOWN"}:
            raise AuthorizationError("Provider action statuses are updated by the action worker or signed callback.")
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

    def _action_with_attempts(self, user_id: int, row: Any) -> dict[str, Any]:
        item = self._decode_action(row)
        item["attempts"] = self.action_attempts(user_id, int(item["id"]))
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
            item["actions"] = [
                self._action_with_attempts(user_id, action)
                for action in self.database.fetch_all(
                    "SELECT * FROM guardian_action_events WHERE user_id = ? AND engine_event_id = ? ORDER BY id",
                    (user_id, item["engine_event_id"]),
                )
            ]
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
        item["actions"] = [
            self._action_with_attempts(user_id, action)
            for action in self.database.fetch_all(
                "SELECT * FROM guardian_action_events WHERE user_id = ? AND engine_event_id = ? ORDER BY id",
                (user_id, item["engine_event_id"]),
            )
        ]
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
            engine = self._engines.get((user_id, str(event["session_id"])))
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
