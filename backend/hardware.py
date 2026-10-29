"""Hardware-ingestion protocol and per-device stream state for CARES.

This module contains transport-independent validation/state. The HTTP route
and application persistence remain in :mod:`backend.api` and
:mod:`backend.services`.
"""

from __future__ import annotations

import hashlib
import hmac
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from engine.hr_estimator import WristBVPHREstimator

SUPPORTED_PROTOCOL_VERSION = "1.0"
DEFAULT_SAMPLING_RATE_HZ = 64.0
DEFAULT_STALE_TIMEOUT_SECONDS = 15.0
MAX_BVP_SAMPLES_PER_PACKET = 4096
MIN_SPO2_PERCENT = 50.0
MAX_SPO2_PERCENT = 100.0
MAX_DEVICE_ID_LENGTH = 128
MAX_LOCATION_TIMESTAMP_SKEW_SECONDS = 30.0
# Location timestamps use the same device clock as device_timestamp_start.



class HardwarePacketError(ValueError):
    """A packet failed the hardware protocol contract."""


class HardwareAccessError(PermissionError):
    """A device credential or device ownership check failed."""


@dataclass(frozen=True)
class HardwareLocationPacket:
    latitude: float
    longitude: float
    timestamp: float
    accuracy: Optional[float] = None


@dataclass(frozen=True)
class HardwarePacket:
    """Validated version 1 packet with raw-BVP or precomputed sensor readings."""

    protocol_version: str
    device_id: str
    connection_id: str
    sequence: int
    device_timestamp_start: float
    sampling_rate_hz: float
    bvp_samples: tuple[float, ...]
    heart_rate_bpm: Optional[float] = None
    spo2_percent: Optional[float] = None
    session_id: Optional[str] = None
    location: Optional[HardwareLocationPacket] = None

    @classmethod
    def from_dict(
        cls,
        data: Mapping[str, Any],
        expected_sampling_rate_hz: float,
    ) -> "HardwarePacket":
        if not isinstance(data, Mapping):
            raise HardwarePacketError("JSON packet must be an object.")

        version = data.get("protocol_version")
        if version != SUPPORTED_PROTOCOL_VERSION:
            raise HardwarePacketError(
                f"Unsupported protocol_version; expected {SUPPORTED_PROTOCOL_VERSION}."
            )

        device_id = data.get("device_id")
        if (
            not isinstance(device_id, str)
            or not device_id.strip()
            or len(device_id) > MAX_DEVICE_ID_LENGTH
        ):
            raise HardwarePacketError("device_id is required and invalid.")
        device_id = device_id.strip()

        connection_id = data.get("connection_id")
        if (
            not isinstance(connection_id, str)
            or not connection_id.strip()
            or len(connection_id) > 128
        ):
            raise HardwarePacketError("connection_id is required and invalid.")
        connection_id = connection_id.strip()

        sequence = data.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise HardwarePacketError("sequence must be a non-negative integer.")

        timestamp = data.get("device_timestamp_start")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(float(timestamp))
            or float(timestamp) < 0
        ):
            raise HardwarePacketError("device_timestamp_start must be finite seconds.")

        sampling_rate = data.get("sampling_rate_hz")
        if (
            isinstance(sampling_rate, bool)
            or not isinstance(sampling_rate, (int, float))
            or not math.isfinite(float(sampling_rate))
            or float(sampling_rate) <= 0
        ):
            raise HardwarePacketError("sampling_rate_hz must be a positive finite number.")
        if not math.isclose(float(sampling_rate), expected_sampling_rate_hz, rel_tol=0.0, abs_tol=1e-6):
            raise HardwarePacketError(
                f"sampling_rate_hz must equal configured {expected_sampling_rate_hz:g} Hz."
            )

        raw_samples = data.get("bvp_samples")
        has_precomputed_hr = "heart_rate_bpm" in data
        if raw_samples is None and not has_precomputed_hr:
            raise HardwarePacketError(
                "Provide bvp_samples or a precomputed heart_rate_bpm reading."
            )
        if raw_samples is not None and (
            not isinstance(raw_samples, list)
            or not raw_samples
            or len(raw_samples) > MAX_BVP_SAMPLES_PER_PACKET
        ):
            raise HardwarePacketError(
                f"bvp_samples must contain 1-{MAX_BVP_SAMPLES_PER_PACKET} values."
            )
        samples: list[float] = []
        for value in raw_samples or []:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
            ):
                raise HardwarePacketError("bvp_samples must contain finite numeric values.")
            samples.append(float(value))

        heart_rate = data.get("heart_rate_bpm")
        if heart_rate is not None:
            if (
                isinstance(heart_rate, bool)
                or not isinstance(heart_rate, (int, float))
                or not math.isfinite(float(heart_rate))
                or not 30.0 <= float(heart_rate) <= 220.0
            ):
                raise HardwarePacketError("heart_rate_bpm must be finite and within 30-220 BPM.")
            heart_rate = float(heart_rate)

        spo2 = data.get("spo2_percent")
        if spo2 is not None:
            if (
                isinstance(spo2, bool)
                or not isinstance(spo2, (int, float))
                or not math.isfinite(float(spo2))
                or not MIN_SPO2_PERCENT <= float(spo2) <= MAX_SPO2_PERCENT
            ):
                raise HardwarePacketError("spo2_percent must be finite and within 50-100%.")
            spo2 = float(spo2)
        if heart_rate is None and spo2 is not None:
            raise HardwarePacketError("spo2_percent requires heart_rate_bpm or bvp_samples.")

        session_id = data.get("session_id")
        if session_id is not None and (
            not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 128
        ):
            raise HardwarePacketError("session_id must be a non-empty string when supplied.")

        if "user_id" in data:
            raise HardwarePacketError("user_id is not accepted in hardware packets.")

        location_value = data.get("location")
        location = None
        if location_value is not None:
            if not isinstance(location_value, Mapping):
                raise HardwarePacketError("location must be an object when supplied.")
            try:
                latitude = location_value.get("latitude")
                longitude = location_value.get("longitude")
                location_timestamp = location_value.get("timestamp")
                accuracy = location_value.get("accuracy")
                values = (latitude, longitude, location_timestamp)
                if any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in values
                ):
                    raise ValueError
                if not -90.0 <= float(latitude) <= 90.0:
                    raise ValueError
                if not -180.0 <= float(longitude) <= 180.0:
                    raise ValueError
                if float(location_timestamp) < 0:
                    raise ValueError
                parsed_accuracy = None
                if accuracy is not None:
                    if (
                        isinstance(accuracy, bool)
                        or not isinstance(accuracy, (int, float))
                        or not math.isfinite(float(accuracy))
                        or float(accuracy) < 0
                    ):
                        raise ValueError
                    parsed_accuracy = float(accuracy)
                location = HardwareLocationPacket(
                    latitude=float(latitude),
                    longitude=float(longitude),
                    timestamp=float(location_timestamp),
                    accuracy=parsed_accuracy,
                )
            except (TypeError, ValueError) as exc:
                raise HardwarePacketError(
                    "location must contain finite in-range coordinates and timestamp."
                ) from exc

        return cls(
            protocol_version=version,
            device_id=device_id,
            connection_id=connection_id,
            sequence=sequence,
            device_timestamp_start=float(timestamp),
            sampling_rate_hz=float(sampling_rate),
            bvp_samples=tuple(samples),
            heart_rate_bpm=heart_rate,
            spo2_percent=spo2,
            session_id=session_id.strip() if session_id is not None else None,
            location=location,
        )


@dataclass(frozen=True)
class HardwareDevice:
    """Device credential binding; the device token is never retained raw."""

    device_id: str
    user_id: int
    token_digest: bytes

    @classmethod
    def from_config(cls, device_id: str, user_id: int, token: str) -> "HardwareDevice":
        if not isinstance(token, str) or not token:
            raise ValueError("Hardware device token is required.")
        return cls(
            device_id=device_id,
            user_id=int(user_id),
            token_digest=hashlib.sha256(token.encode("utf-8")).digest(),
        )

    def verify(self, token: Any) -> bool:
        if not isinstance(token, str) or not token:
            return False
        presented = hashlib.sha256(token.encode("utf-8")).digest()
        return hmac.compare_digest(self.token_digest, presented)


@dataclass
class HardwareStreamState:
    """Mutable stream state isolated by device and monitoring session."""

    device_id: str
    session_id: str
    estimator: WristBVPHREstimator
    connection_id: Optional[str] = None
    retired_connection_ids: set[str] = field(default_factory=set)
    buffer: deque[float] = field(default_factory=deque)
    last_sequence: Optional[int] = None
    last_device_timestamp_start: Optional[float] = None
    last_packet_sample_count: int = 0
    last_received_at: Optional[float] = None
    status: str = "DISCONNECTED"
    transport_epoch: int = 0
    last_error: Optional[str] = None
    latest_heart_rate_bpm: Optional[float] = None
    latest_spo2_percent: Optional[float] = None
    latest_reading_at: Optional[float] = None
    latest_reading_received_at: Optional[float] = None
    latest_reading_source: Optional[str] = None

    def reset_evidence_epoch(self) -> None:
        """Discard incomplete BVP/estimator continuity for a new epoch."""
        self.buffer.clear()
        self.estimator.reset()
        self.transport_epoch += 1

    def append(self, samples: tuple[float, ...]) -> None:
        self.buffer.extend(samples)
        max_buffer = self.estimator.window_samples + self.estimator.step_samples
        while len(self.buffer) > max_buffer:
            self.buffer.popleft()

    def window(self) -> Optional[list[float]]:
        if len(self.buffer) < self.estimator.window_samples:
            return None
        return list(self.buffer)[-self.estimator.window_samples:]

    def advance_window(self) -> None:
        for _ in range(min(self.estimator.step_samples, len(self.buffer))):
            self.buffer.popleft()
