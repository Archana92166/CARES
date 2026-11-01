"""
CARES guardian SMS service.

The default provider is MOCK so automated tests and local development never
send real messages accidentally. Set CARES_SMS_PROVIDER=textbee and provide
TEXTBEE_API_KEY to enable real SMS through the registered Android gateway.
"""

from __future__ import annotations

import json
import math
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


TEXTBEE_SEND_URL = "https://api.textbee.dev/api/v1/gateway/send-sms"
HTTP_TIMEOUT_SECONDS = 15


def _load_local_env() -> None:
    """Load project .env settings during normal runs, never during pytest.

    Tests must rely only on their explicitly configured environment so a
    developer's local TextBee key cannot leak into a test that intentionally
    removes the key or accidentally trigger a real provider request.
    """
    if os.getenv("PYTEST_CURRENT_TEST"):
        return

    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.is_file():
        return

    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        # Environment variables can still be supplied by the host process.
        return


def _build_message(
    user_name: str,
    heart_rate: float,
    latitude: Optional[float],
    longitude: Optional[float],
    location_address: Optional[str],
    timestamp: Optional[float | str],
) -> tuple[str, Optional[str]]:
    location_url = None
    if latitude is not None and longitude is not None:
        try:
            latitude = float(latitude)
            longitude = float(longitude)
            if (
                not math.isfinite(latitude)
                or not math.isfinite(longitude)
                or not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
            ):
                raise ValueError
            location_url = f"https://www.google.com/maps?q={latitude},{longitude}"
        except (TypeError, ValueError):
            location_url = None

    if timestamp is None:
        event_time = datetime.now().astimezone().strftime("%d-%m-%Y %I:%M:%S %p %Z")
    else:
        try:
            event_time = datetime.fromtimestamp(float(timestamp)).astimezone().strftime(
                "%d-%m-%Y %I:%M:%S %p %Z"
            )
        except (TypeError, ValueError, OverflowError, OSError):
            event_time = str(timestamp)

    lines = [
        "CARES EMERGENCY ALERT",
        "",
        "A HIGH-risk event has been detected.",
        f"User: {user_name}",
        "Risk Level: HIGH",
        f"Heart Rate: {heart_rate:.1f} BPM",
        f"Time: {event_time}",
    ]
    if location_address:
        lines.append(f"Location: {location_address}")
    if location_url:
        lines.append(f"Google Maps: {location_url}")
    else:
        lines.append("GPS location: Unavailable")

    lines.extend([
        "",
        "Please contact the user and provide immediate assistance.",
        "- CARES System",
    ])
    return "\n".join(lines), location_url


def _normalize_phone(phone_number: str) -> str:
    """Return an E.164 phone number; reject ambiguous local-format numbers."""
    phone = re.sub(r"[\s().-]", "", str(phone_number or ""))
    if not re.fullmatch(r"\+[1-9]\d{7,14}", phone):
        raise ValueError(
            "Guardian phone number must use international format, e.g. +919876543210."
        )
    return phone


def _send_textbee(phone_number: str, message: str) -> dict:
    _load_local_env()
    api_key = os.getenv("TEXTBEE_API_KEY", "").strip()
    if not api_key:
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "reason": "TEXTBEE_API_KEY is not configured in the project .env or environment.",
        }

    body = {
        "recipients": [phone_number],
        "message": message,
    }
    device_id = os.getenv("TEXTBEE_DEVICE_ID", "").strip()
    if device_id:
        body["deviceId"] = device_id

    request = Request(
        TEXTBEE_SEND_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "x-api-key": api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "CARES/1.0",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            response_body = response.read().decode("utf-8")
            status_code = response.status
    except HTTPError as exc:
        try:
            error_body = exc.read().decode("utf-8")
        except Exception:
            error_body = ""
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "http_status": exc.code,
            "reason": error_body[:500] or str(exc),
        }
    except (URLError, TimeoutError, OSError) as exc:
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "reason": f"Could not contact TextBee: {exc}",
        }

    try:
        parsed = json.loads(response_body) if response_body else {}
    except json.JSONDecodeError:
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "http_status": status_code,
            "reason": "TextBee returned an invalid JSON response.",
        }

    data = parsed.get("data", parsed) if isinstance(parsed, dict) else {}
    accepted = status_code == 200 and (
        data.get("success") is True
        or bool(data.get("smsBatchId"))
        or int(data.get("successCount") or 0) > 0
    )
    if not accepted:
        return {
            "success": False,
            "status": "FAILED",
            "provider": "TEXTBEE",
            "http_status": status_code,
            "reason": str(data.get("message") or parsed.get("message") or "TextBee did not accept the SMS."),
        }

    # A 200 response confirms API acceptance/queueing, not handset delivery.
    return {
        "success": True,
        "status": "PENDING",
        "provider": "TEXTBEE",
        "provider_status": "ACCEPTED",
        "http_status": status_code,
        "sms_batch_id": data.get("smsBatchId"),
        "recipient_count": data.get("recipientCount", 1),
        "warning": data.get("warning"),
        "delivery_confirmed": False,
    }


def send_guardian_sms(
    phone_number: str,
    heart_rate: float,
    user_name: str,
    latitude: Optional[float] = None,
    longitude: Optional[float] = None,
    location_address: Optional[str] = None,
    timestamp: Optional[float | str] = None,
) -> dict:
    """Send a personalized alert using MOCK or TextBee, based on configuration."""
    try:
        recipient = _normalize_phone(phone_number)
    except ValueError as exc:
        return {
            "success": False,
            "status": "FAILED",
            "reason": str(exc),
        }

    user_name = str(user_name or "").strip() or "CARES User"
    try:
        heart_rate = float(heart_rate)
        if not math.isfinite(heart_rate):
            raise ValueError
    except (TypeError, ValueError):
        return {
            "success": False,
            "status": "FAILED",
            "reason": "Invalid heart-rate value.",
        }

    message, location_url = _build_message(
        user_name=user_name,
        heart_rate=heart_rate,
        latitude=latitude,
        longitude=longitude,
        location_address=location_address,
        timestamp=timestamp,
    )

    _load_local_env()
    provider = os.getenv("CARES_SMS_PROVIDER", "mock").strip().lower()

    # Prevent an accidental real SMS during the test suite, even if a developer
    # has configured TextBee in their local .env. Provider unit tests may opt in
    # while replacing urlopen with a fake HTTP response.
    if os.getenv("PYTEST_CURRENT_TEST") and os.getenv("CARES_ALLOW_SMS_IN_TESTS") != "true":
        provider = "mock"

    if provider == "mock":
        mock_message = (
            message
            + "\n\nThis is a simulated CARES guardian notification. "
            "No real SMS was sent."
        )
        print("\n" + "=" * 55)
        print("CARES MOCK SMS SERVICE")
        print("=" * 55)
        print(f"Recipient: {recipient}")
        print("\n" + mock_message)
        print("=" * 55)
        print("SIMULATION STATUS: MOCK_SENT")
        print("REAL SMS DELIVERY: NOT ATTEMPTED")
        print("=" * 55 + "\n")
        return {
            "success": True,
            "status": "MOCK_SENT",
            "provider": "MOCK",
            "recipient": recipient,
            "message": mock_message,
            "location_url": location_url,
            "real_sms_sent": False,
        }

    if provider == "textbee":
        result = _send_textbee(recipient, message)
        result.update({
            "recipient": recipient,
            "message": message,
            "location_url": location_url,
            "real_sms_sent": False,
        })
        return result

    return {
        "success": False,
        "status": "FAILED",
        "provider": provider.upper(),
        "reason": "Unsupported CARES_SMS_PROVIDER. Use 'mock' or 'textbee'.",
    }
