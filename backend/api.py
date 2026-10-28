"""Dependency-free JSON API facade for the CARES backend services."""

from __future__ import annotations

import json

import math
import time
import os
import hmac

from dataclasses import dataclass

from typing import Any, Mapping, Optional

from urllib.parse import parse_qs, urlsplit

from engine.models import PhysiologicalSample

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

    raw = next(

        (value for key, value in headers.items() if key.lower() == "cookie"),

        "",

    )

    for part in raw.split(";"):

        key, separator, value = part.strip().partition("=")

        if separator and key == name:

            return value

    return None

class CARESAPI:

    """Routes authenticated HTTP requests to CARESBackend."""

    def __init__(

        self,

        backend: CARESBackend,

        secure_cookie: bool = False,

        demo_controller: Optional[DemoController] = None,

    ) -> None:

        self.backend = backend

        self.secure_cookie = secure_cookie

        self.demo_controller = demo_controller or DemoController(backend)

    def authenticate_request(self, headers: Mapping[str, str]) -> int:

        authorization = next(

            (

                value

                for key, value in headers.items()

                if key.lower() == "authorization"

            ),

            "",

        )

        token = (

            authorization[7:].strip()

            if authorization.lower().startswith("bearer ")

            else _cookie(headers, "cares_session")

        )

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

            response = self._route(

                method,

                path,

                body,

                headers,

                query,

            )

            return APIResponse(

                response[0],

                response[1],

                response[2] if len(response) > 2 else {},

            )

        except BackendError as exc:

            return APIResponse(

                exc.status_code,

                {"error": str(exc)},

                {},

            )

        except (

            ValueError,

            TypeError,

            KeyError,

            json.JSONDecodeError,

        ) as exc:

            return APIResponse(

                422,

                {"error": str(exc) or "Invalid request."},

                {},

            )

    def _route(

        self,

        method: str,

        path: str,

        body: dict[str, Any],

        headers: Mapping[str, str],

        query: Mapping[str, list[str]],

    ) -> tuple[int, dict[str, Any], dict[str, str]]:

        #* Authentication: registration*

        if path == "/api/auth/register" and method == "POST":

            return (

                201,

                {

                    "user": self.backend.register(

                        body.get("name"),

                        body.get("email"),

                        body.get("password"),

                    )

                },

                {},

            )

        #* Authentication: login*

        if path == "/api/auth/login" and method == "POST":

            user, token = self.backend.login(

                body.get("email"),

                body.get("password"),

            )

            cookie = (

                f"cares_session={token}; "

                "HttpOnly; SameSite=Lax; Path=/"

            )

            if self.secure_cookie:

                cookie += "; Secure"

            return 200, {"user": user}, {"Set-Cookie": cookie}

        #* Authentication: logout*

        if path == "/api/auth/logout" and method == "POST":

            self.backend.logout(self._token(headers))

            return (

                200,

                {"ok": True},

                {

                    "Set-Cookie": (

                        "cares_session=; Max-Age=0; "

                        "HttpOnly; SameSite=Lax; Path=/"

                    )

                },

            )

        #* Authentication: current user*

        if path == "/api/auth/me" and method == "GET":

            return (

                200,

                {"user": self.backend.user(self._user(headers))},

                {},

            )

        #* Demonstration input*

        if path == "/api/demo/start" and method == "POST":

            return (

                202,

                {

                    "demo": self.demo_controller.start(

                        self._user(headers),

                        body.get("scenario"),

                    )

                },

                {},

            )

        if path == "/api/demo/stop" and method == "POST":

            return (

                200,

                {"demo": self.demo_controller.stop(self._user(headers))},

                {},

            )

        if path == "/api/demo/status" and method == "GET":

            return (

                200,

                {"demo": self.demo_controller.status(self._user(headers))},

                {},

            )

        if path == "/api/monitoring/sessions" and method == "GET":

            return (

                200,

                {

                    "sessions": self.backend.list_monitoring_sessions(

                        self._user(headers),

                        self._limit(query),

                    )

                },

                {},

            )
        # ESP32 + MAX30100 device-authenticated ingestion. Keep this route
        # before the user-session authentication barrier below.
        if path == "/api/hardware/ingest" and method == "POST":
            expected_token = os.environ.get("CARES_DEVICE_TOKEN", "")
            supplied_token = next(
                (value for key, value in headers.items() if key.lower() == "x-device-token"),
                "",
            )

            if not expected_token:
                return 503, {"ok": False, "error": "Hardware device token is not configured."}, {}

            if not supplied_token or not hmac.compare_digest(supplied_token, expected_token):
                return 401, {"ok": False, "error": "Invalid device token."}, {}

            try:
                hardware_user_id = int(os.environ.get("CARES_HARDWARE_USER_ID", ""))
                if hardware_user_id <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                return 503, {
                    "ok": False,
                    "error": "Set CARES_HARDWARE_USER_ID to an existing positive numeric user ID.",
                }, {}

            heart_rate = body.get("heart_rate")
            spo2 = body.get("spo2")
            device_id = body.get("device_id", "CARES_ESP32_01")

            if isinstance(heart_rate, bool) or not isinstance(heart_rate, (int, float)):
                raise ValueError("heart_rate must be numeric.")
            if not math.isfinite(float(heart_rate)) or not 30 <= float(heart_rate) <= 220:
                raise ValueError("heart_rate must be between 30 and 220.")

            if isinstance(spo2, bool) or not isinstance(spo2, (int, float)):
                raise ValueError("spo2 must be numeric.")
            if not math.isfinite(float(spo2)) or not 70 <= float(spo2) <= 100:
                raise ValueError("spo2 must be between 70 and 100.")

            if not isinstance(device_id, str) or not device_id.strip():
                raise ValueError("device_id must be a non-empty string.")

            location = None
            has_location = "latitude" in body or "longitude" in body
            if has_location:
                if "latitude" not in body or "longitude" not in body:
                    raise ValueError("Both latitude and longitude are required for a GPS location.")
                location = self.backend.ingest_location(
                    hardware_user_id,
                    {
                        "latitude": body["latitude"],
                        "longitude": body["longitude"],
                        "timestamp": time.time(),
                        "source": "NEO-6M",
                    },
                )

            sample = PhysiologicalSample(
                timestamp=time.time(),
                heart_rate_bpm=float(heart_rate),
                additional_metrics={
                    "hr_valid": 1.0,
                    # Firmware sends derived HR, not raw BVP waveform data.
                    "bvp_quality": 0.0,
                    "spo2": float(spo2),
                },
            )

            result = self.backend.process_sample(
                user_id=hardware_user_id,
                sample=sample,
                source="REAL_HARDWARE",
            )

            return 201, {
                "ok": True,
                "device_id": device_id,
                "heart_rate": float(heart_rate),
                "spo2": float(spo2),
                "location": location,
                "received_at": time.time(),
                "result": result,
            }, {}


        #* All routes below this point require an authenticated user.*

        user_id = self._user(headers)

        #* ---------------------------------------------------------*

        #* ESP32 physiological sample ingestion*

        #* POST /api/hardware/sample*

        #

        #* Expected JSON:

        #* {*

        #*   "heart_rate_bpm": 75,*

        #*   "hr_valid": 1,*

        #*   "bvp_quality": 0.9,*

        #*   "timestamp": 1790000000.0*

        #* }*

        #* ---------------------------------------------------------*

        if path == "/api/hardware/sample" and method == "POST":

            hr = body.get("heart_rate_bpm")

            hr_valid = body.get("hr_valid", 0)

            quality = body.get("bvp_quality", 0.0)

            timestamp = body.get("timestamp", time.time())

            #* Validate heart rate.*

            if isinstance(hr, bool) or not isinstance(hr, (int, float)):

                raise ValueError("heart_rate_bpm must be numeric.")

            if not math.isfinite(float(hr)) or not 30 <= float(hr) <= 220:

                raise ValueError(

                    "heart_rate_bpm must be between 30 and 220."

                )

            #* Validate timestamp.*

            if (

                isinstance(timestamp, bool)

                or not isinstance(timestamp, (int, float))

            ):

                raise ValueError("timestamp must be numeric.")

            if not math.isfinite(float(timestamp)) or timestamp < 0:

                raise ValueError(

                    "timestamp must be a non-negative number."

                )

            #* Validate HR validity flag.*

            if (

                isinstance(hr_valid, bool)

                or not isinstance(hr_valid, (int, float))

                or hr_valid not in (0, 1)

            ):

                raise ValueError("hr_valid must be 0 or 1.")

            #* Validate signal quality.*

            if (

                isinstance(quality, bool)

                or not isinstance(quality, (int, float))

            ):

                raise ValueError("bvp_quality must be numeric.")

            if (

                not math.isfinite(float(quality))

                or not 0 <= float(quality) <= 1

            ):

                raise ValueError(

                    "bvp_quality must be between 0 and 1."

                )

            sample = PhysiologicalSample(

                timestamp=float(timestamp),

                heart_rate_bpm=float(hr),

                additional_metrics={

                    "hr_valid": float(hr_valid),

                    "bvp_quality": float(quality),

                },

            )

            result = self.backend.process_sample(

                user_id=user_id,

                sample=sample,

                source="REAL_HARDWARE",

            )

            return 201, {

                "ok": True,

                "result": result,

            }, {}

        #* Guardian contacts*

        if path == "/api/guardian" and method == "GET":

            return (

                200,

                {"guardians": self.backend.list_guardians(user_id)},

                {},

            )

        if path == "/api/guardian" and method == "POST":

            return (

                201,

                {

                    "guardian": self.backend.add_guardian(

                        user_id,

                        body.get("name"),

                        body.get("phone_number"),

                        body.get("relationship"),

                    )

                },

                {},

            )

        guardian_id = self._id_after(path, "/api/guardian/")

        if guardian_id is not None and method == "PUT":

            return (

                200,

                {

                    "guardian": self.backend.update_guardian(

                        user_id,

                        guardian_id,

                        **body,

                    )

                },

                {},

            )

        if guardian_id is not None and method == "DELETE":

            self.backend.delete_guardian(user_id, guardian_id)

            return 200, {"ok": True}, {}

        #* Dashboard*

        if path == "/api/dashboard/current" and method == "GET":

            current = self.backend.get_current(user_id)

            current["demo"] = self.demo_controller.status(user_id)

            if current["demo"].get("active"):

                current["location"] = None

            return 200, current, {}

        if path == "/api/dashboard/history" and method == "GET":

            return (

                200,

                {

                    "events": self.backend.history(

                        user_id,

                        self._limit(query),

                        query.get("session_id", [None])[0],

                        query.get("source", [None])[0],

                    )

                },

                {},

            )

        #* Baseline*

        if path == "/api/baseline/current" and method == "GET":

            return (

                200,

                {"baseline": self.backend.baseline_current(user_id)},

                {},

            )

        if path == "/api/baseline/daily" and method == "GET":

            return (

                200,

                {

                    "records": self.backend.list_daily_records(

                        user_id,

                        self._limit(query),

                    )

                },

                {},

            )

        if path == "/api/baseline/adaptation" and method == "GET":

            return (

                200,

                {

                    "events": self.backend.list_adaptation_events(

                        user_id,

                        self._limit(query),

                    )

                },

                {},

            )

        #* Location*

        if path == "/api/location" and method == "POST":

            return (

                201,

                {

                    "location": self.backend.ingest_location(

                        user_id,

                        body,

                    )

                },

                {},

            )

        if path == "/api/location/latest" and method == "GET":

            demo = self.demo_controller.status(user_id)

            return (

                200,

                {

                    "location": (

                        None

                        if demo.get("active")

                        else self.backend.latest_location(user_id)

                    )

                },

                {},

            )

        #* Actions*

        if path == "/api/actions" and method == "GET":

            return (

                200,

                {

                    "actions": self.backend.list_actions(

                        user_id,

                        self._limit(query),

                    )

                },

                {},

            )

        action_id = self._id_after(path, "/api/actions/")

        if action_id is not None and method == "GET":

            return (

                200,

                {

                    "action": self.backend.get_action(

                        user_id,

                        action_id,

                    )

                },

                {},

            )

        if action_id is not None and method == "PATCH":

            return (

                200,

                {

                    "action": self.backend.update_action_status(

                        user_id,

                        action_id,

                        body.get("status"),

                        body.get("metadata"),

                    )

                },

                {},

            )

        #* Incidents*

        if path == "/api/incidents" and method == "GET":

            return (

                200,

                {

                    "incidents": self.backend.list_incidents(

                        user_id,

                        self._limit(query),

                    )

                },

                {},

            )

        incident_id = self._id_after(path, "/api/incidents/")

        if incident_id is not None and method == "GET":

            return (

                200,

                {

                    "incident": self.backend.get_incident(

                        user_id,

                        incident_id,

                    )

                },

                {},

            )

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

        authorization = next(

            (

                value

                for key, value in headers.items()

                if key.lower() == "authorization"

            ),

            "",

        )

        if authorization.lower().startswith("bearer "):

            return authorization[7:].strip()

        return _cookie(headers, "cares_session")

    def _user(self, headers: Mapping[str, str]) -> int:

        return self.backend.authenticate(self._token(headers))
