"""Dependency-free JSON API facade for the CARES backend services."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional
from urllib.parse import parse_qs, urlsplit

from .services import (
    AuthenticationError,
    BackendError,
    CARESBackend,
    NotFoundError,
)
from .demo import DemoController


@dataclass(frozen=True)
class APIResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str]


def _cookie(headers: Mapping[str, str], name: str) -> Optional[str]:
    raw = next((value for key, value in headers.items() if key.lower() == "cookie"), "")
    for part in raw.split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == name:
            return value
    return None


class CARESAPI:
    """Routes authenticated HTTP requests to ``CARESBackend``.

    There is intentionally no endpoint that calculates risk. Hardware or a
    trusted application integration calls ``CARESBackend.process_sample``;
    the API only reads persisted EngineOutput data.
    """

    def __init__(self, backend: CARESBackend, secure_cookie: bool = False, demo_controller: Optional[DemoController] = None) -> None:
        self.backend = backend
        self.secure_cookie = secure_cookie
        self.demo_controller = demo_controller or DemoController(backend)

    def authenticate_request(self, headers: Mapping[str, str]) -> int:
        authorization = next(
            (value for key, value in headers.items() if key.lower() == "authorization"), ""
        )
        token = authorization[7:].strip() if authorization.lower().startswith("bearer ") else _cookie(headers, "cares_session")
        return self.backend.authenticate(token)

    def handle(
        self,
        method: str,
        target: str,
        body: Optional[dict[str, Any]] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> APIResponse:
        method = method.upper()
        headers = headers or {}
        body = body or {}
        parsed = urlsplit(target)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)
        try:
            response = self._route(method, path, body, headers, query)
            return APIResponse(response[0], response[1], response[2] if len(response) > 2 else {})
        except BackendError as exc:
            return APIResponse(exc.status_code, {"error": str(exc)}, {})
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            return APIResponse(422, {"error": str(exc) or "Invalid request."}, {})

    def _route(self, method: str, path: str, body: dict[str, Any], headers: Mapping[str, str], query: Mapping[str, list[str]]) -> tuple[int, dict[str, Any], dict[str, str]]:
        # Authentication
        if path == "/api/auth/register" and method == "POST":
            return 201, {"user": self.backend.register(body.get("name"), body.get("email"), body.get("password"))}, {}
        if path == "/api/auth/login" and method == "POST":
            user, token = self.backend.login(body.get("email"), body.get("password"))
            cookie = f"cares_session={token}; HttpOnly; SameSite=Lax; Path=/"
            if self.secure_cookie:
                cookie += "; Secure"
            return 200, {"user": user}, {"Set-Cookie": cookie}
        if path == "/api/auth/logout" and method == "POST":
            self.backend.logout(self._token(headers))
            return 200, {"ok": True}, {"Set-Cookie": "cares_session=; Max-Age=0; HttpOnly; SameSite=Lax; Path=/"}
        if path == "/api/auth/me" and method == "GET":
            return 200, {"user": self.backend.user(self._user(headers))}, {}

        # Isolated demonstration input. It only feeds PhysiologicalSample
        # values into the normal backend engine path.
        if path == "/api/demo/start" and method == "POST":
            return 202, {"demo": self.demo_controller.start(self._user(headers), body.get("scenario"))}, {}
        if path == "/api/demo/stop" and method == "POST":
            return 200, {"demo": self.demo_controller.stop(self._user(headers))}, {}
        if path == "/api/demo/status" and method == "GET":
            return 200, {"demo": self.demo_controller.status(self._user(headers))}, {}
        if path == "/api/monitoring/sessions" and method == "GET":
            return 200, {"sessions": self.backend.list_monitoring_sessions(self._user(headers), self._limit(query))}, {}

        user_id = self._user(headers)

        # Guardian contacts
        if path == "/api/guardian" and method == "GET":
            return 200, {"guardians": self.backend.list_guardians(user_id)}, {}
        if path == "/api/guardian" and method == "POST":
            return 201, {"guardian": self.backend.add_guardian(user_id, body.get("name"), body.get("phone_number"), body.get("relationship"))}, {}
        guardian_id = self._id_after(path, "/api/guardian/")
        if guardian_id is not None and method == "PUT":
            return 200, {"guardian": self.backend.update_guardian(user_id, guardian_id, **body)}, {}
        if guardian_id is not None and method == "DELETE":
            self.backend.delete_guardian(user_id, guardian_id)
            return 200, {"ok": True}, {}

        # Dashboard and baseline read models
        if path == "/api/dashboard/current" and method == "GET":
            current = self.backend.get_current(user_id)
            current["demo"] = self.demo_controller.status(user_id)
            if current["demo"].get("active"):
                current["location"] = None
            return 200, current, {}
        if path == "/api/dashboard/history" and method == "GET":
            return 200, {"events": self.backend.history(user_id, self._limit(query), query.get("session_id", [None])[0], query.get("source", [None])[0])}, {}
        if path == "/api/baseline/current" and method == "GET":
            return 200, {"baseline": self.backend.baseline_current(user_id)}, {}
        if path == "/api/baseline/daily" and method == "GET":
            return 200, {"records": self.backend.list_daily_records(user_id, self._limit(query))}, {}
        if path == "/api/baseline/adaptation" and method == "GET":
            return 200, {"events": self.backend.list_adaptation_events(user_id, self._limit(query))}, {}

        # Location
        if path == "/api/location" and method == "POST":
            return 201, {"location": self.backend.ingest_location(user_id, body)}, {}
        if path == "/api/location/latest" and method == "GET":
            demo = self.demo_controller.status(user_id)
            return 200, {"location": None if demo.get("active") else self.backend.latest_location(user_id)}, {}

        # Actions
        if path == "/api/actions" and method == "GET":
            return 200, {"actions": self.backend.list_actions(user_id, self._limit(query))}, {}
        action_id = self._id_after(path, "/api/actions/")
        if action_id is not None and method == "GET":
            return 200, {"action": self.backend.get_action(user_id, action_id)}, {}
        if action_id is not None and method == "PATCH":
            return 200, {"action": self.backend.update_action_status(user_id, action_id, body.get("status"), body.get("metadata"))}, {}

        # Incidents
        if path == "/api/incidents" and method == "GET":
            return 200, {"incidents": self.backend.list_incidents(user_id, self._limit(query))}, {}
        incident_id = self._id_after(path, "/api/incidents/")
        if incident_id is not None and method == "GET":
            return 200, {"incident": self.backend.get_incident(user_id, incident_id)}, {}

        raise NotFoundError("Route not found.")

    @staticmethod
    def _id_after(path: str, prefix: str) -> Optional[int]:
        if not path.startswith(prefix):
            return None
        tail = path[len(prefix):]
        if not tail.isdigit():
            return None
        return int(tail)

    @staticmethod
    def _limit(query: Mapping[str, list[str]]) -> int:
        try:
            return int(query.get("limit", ["100"])[0])
        except (ValueError, TypeError):
            return 100

    def _token(self, headers: Mapping[str, str]) -> Optional[str]:
        authorization = next((value for key, value in headers.items() if key.lower() == "authorization"), "")
        if authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return _cookie(headers, "cares_session")

    def _user(self, headers: Mapping[str, str]) -> int:
        return self.backend.authenticate(self._token(headers))
