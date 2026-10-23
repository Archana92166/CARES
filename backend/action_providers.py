"""External action provider boundaries used by the CARES action worker.

Providers are deliberately small and dependency-free.  They are disabled
unless the deployment explicitly enables live actions and supplies the
required credentials.  Tests can inject a provider implementation without
making network requests.
"""

from __future__ import annotations

import base64
import html
import json
import os
import socket
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from twilio.request_validator import RequestValidator


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, ambiguous: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.ambiguous = ambiguous


@dataclass(frozen=True)
class ProviderResult:
    status: str
    provider_reference: Optional[str] = None
    detail: Optional[str] = None


class SmsProvider(Protocol):
    configured: bool

    def submit(self, recipient: str, body: str, idempotency_key: str) -> ProviderResult:
        ...


class VoiceProvider(Protocol):
    configured: bool

    def submit(self, recipient: str, message: str, idempotency_key: str) -> ProviderResult:
        ...


def _safe_provider_detail(value: object, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] or "provider request failed"


class TwilioSmsProvider:
    """Twilio Messages API adapter.

    Twilio accepts a message submission and returns a message SID.  That is
    represented as SUBMITTED, never DELIVERED.  Delivery callbacks can later
    transition the attempt to DELIVERED or FAILED.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: Optional[str] = None,
        messaging_service_sid: Optional[str] = None,
        status_callback_url: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.messaging_service_sid = messaging_service_sid
        self.status_callback_url = status_callback_url
        self.timeout_seconds = timeout_seconds
        self.configured = bool(
            account_sid
            and auth_token
            and (from_number or messaging_service_sid)
        )

    @classmethod
    def from_environment(cls) -> Optional["TwilioSmsProvider"]:
        if os.getenv("CARES_SMS_PROVIDER", "").strip().lower() != "twilio":
            return None
        provider = cls(
            account_sid=os.getenv("CARES_SMS_ACCOUNT_SID", "").strip(),
            auth_token=os.getenv("CARES_SMS_AUTH_TOKEN", ""),
            from_number=os.getenv("CARES_SMS_FROM", "").strip() or None,
            messaging_service_sid=os.getenv("CARES_SMS_MESSAGING_SERVICE_SID", "").strip() or None,
            status_callback_url=os.getenv("CARES_SMS_STATUS_CALLBACK_URL", "").strip() or None,
            timeout_seconds=float(os.getenv("CARES_PROVIDER_TIMEOUT_SECONDS", "10")),
        )
        return provider if provider.configured else None

    def submit(self, recipient: str, body: str, idempotency_key: str) -> ProviderResult:
        if not self.configured:
            raise ProviderError("SMS provider is not configured.")
        fields: dict[str, str] = {"To": recipient, "Body": body}
        if self.from_number:
            fields["From"] = self.from_number
        if self.messaging_service_sid:
            fields["MessagingServiceSid"] = self.messaging_service_sid
        if self.status_callback_url:
            fields["StatusCallback"] = self.status_callback_url
        return _twilio_submit(
            account_sid=self.account_sid,
            auth_token=self.auth_token,
            endpoint=f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Messages.json",
            fields=fields,
            idempotency_key=idempotency_key,
            timeout_seconds=self.timeout_seconds,
        )

    def verify_callback(self, url: str, fields: Mapping[str, Any], signature: str) -> bool:
        if not self.auth_token or not signature:
            return False
        return bool(RequestValidator(self.auth_token).validate(url, dict(fields), signature))


class TwilioVoiceProvider:
    """Twilio Programmable Voice adapter for an authorized callback contact.

    This places a programmable voice call to the configured recipient; it is
    not a public-safety dispatch integration and does not claim emergency
    services were contacted.
    """

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        status_callback_url: Optional[str] = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.from_number = from_number
        self.status_callback_url = status_callback_url
        self.timeout_seconds = timeout_seconds
        self.configured = bool(account_sid and auth_token and from_number)

    @classmethod
    def from_environment(cls) -> Optional["TwilioVoiceProvider"]:
        if os.getenv("CARES_VOICE_PROVIDER", "").strip().lower() != "twilio":
            return None
        provider = cls(
            account_sid=os.getenv("CARES_VOICE_ACCOUNT_SID", os.getenv("CARES_SMS_ACCOUNT_SID", "")).strip(),
            auth_token=os.getenv("CARES_VOICE_AUTH_TOKEN", os.getenv("CARES_SMS_AUTH_TOKEN", "")),
            from_number=os.getenv("CARES_VOICE_FROM", "").strip(),
            status_callback_url=os.getenv("CARES_VOICE_STATUS_CALLBACK_URL", "").strip() or None,
            timeout_seconds=float(os.getenv("CARES_PROVIDER_TIMEOUT_SECONDS", "10")),
        )
        return provider if provider.configured else None

    def submit(self, recipient: str, message: str, idempotency_key: str) -> ProviderResult:
        if not self.configured:
            raise ProviderError("Voice provider is not configured.")
        twiml = f"<Response><Say>{html.escape(message[:900])}</Say></Response>"
        fields = {"To": recipient, "From": self.from_number, "Twiml": twiml}
        if self.status_callback_url:
            fields["StatusCallback"] = self.status_callback_url
            fields["StatusCallbackEvent"] = "initiated ringing answered completed"
        return _twilio_submit(
            account_sid=self.account_sid,
            auth_token=self.auth_token,
            endpoint=f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Calls.json",
            fields=fields,
            idempotency_key=idempotency_key,
            timeout_seconds=self.timeout_seconds,
        )


    def verify_callback(self, url: str, fields: Mapping[str, Any], signature: str) -> bool:
        if not self.auth_token or not signature:
            return False
        return bool(RequestValidator(self.auth_token).validate(url, dict(fields), signature))


def _twilio_submit(
    *,
    account_sid: str,
    auth_token: str,
    endpoint: str,
    fields: Mapping[str, str],
    idempotency_key: str,
    timeout_seconds: float,
) -> ProviderResult:
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode()).decode()
    request = Request(
        endpoint,
        data=urlencode(fields).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": "CARES-Backend/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(128_000).decode("utf-8")
            payload = json.loads(raw)
            status = int(getattr(response, "status", 200))
    except HTTPError as exc:
        retryable = exc.code == 429 or exc.code >= 500
        raise ProviderError(
            f"provider HTTP {exc.code}", retryable=retryable
        ) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise ProviderError("provider request outcome is ambiguous", ambiguous=True) from exc
    except (ValueError, json.JSONDecodeError) as exc:
        raise ProviderError("provider returned an invalid response", ambiguous=True) from exc

    if status < 200 or status >= 300:
        raise ProviderError(f"provider HTTP {status}", retryable=status >= 500)
    reference = payload.get("sid")
    if not isinstance(reference, str) or not reference:
        raise ProviderError("provider response did not include a reference", ambiguous=True)
    provider_status = str(payload.get("status") or "accepted").lower()
    if provider_status in {"delivered", "completed"}:
        return ProviderResult("DELIVERED", reference, provider_status)
    return ProviderResult("SUBMITTED", reference, provider_status)

def configured_live_providers() -> tuple[Optional[TwilioSmsProvider], Optional[TwilioVoiceProvider]]:
    """Return providers only when the explicit live-action gate is enabled."""
    if os.getenv("CARES_ENABLE_LIVE_ACTIONS", "0") != "1":
        return None, None
    return TwilioSmsProvider.from_environment(), TwilioVoiceProvider.from_environment()


__all__ = [
    "ProviderError",
    "ProviderResult",
    "SmsProvider",
    "VoiceProvider",
    "TwilioSmsProvider",
    "TwilioVoiceProvider",
    "configured_live_providers",
]
