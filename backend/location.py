"""Hardware location validation and optional reverse-geocoding adapters."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from urllib.parse import urlencode
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class HardwareLocation:
    latitude: float
    longitude: float
    accuracy: float | None
    timestamp: float
    source: str


@dataclass(frozen=True)
class GeocodedLocation:
    formatted_address: str
    provider: str


class LocationValidationError(ValueError):
    pass


def validate_hardware_location(
    latitude: object,
    longitude: object,
    accuracy: object = None,
    timestamp: object = None,
    source: object = None,
) -> HardwareLocation:
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        raise LocationValidationError("Latitude and longitude must be numeric.")

    if not math.isfinite(lat) or not -90.0 <= lat <= 90.0:
        raise LocationValidationError("Invalid GPS latitude.")
    if not math.isfinite(lon) or not -180.0 <= lon <= 180.0:
        raise LocationValidationError("Invalid GPS longitude.")

    parsed_accuracy = None
    if accuracy is not None:
        try:
            parsed_accuracy = float(accuracy)
        except (TypeError, ValueError):
            raise LocationValidationError("Accuracy must be numeric.")
        if not math.isfinite(parsed_accuracy) or parsed_accuracy < 0:
            raise LocationValidationError("Accuracy must be non-negative.")

    try:
        parsed_timestamp = float(timestamp)
    except (TypeError, ValueError):
        raise LocationValidationError("Location timestamp must be numeric.")
    if not math.isfinite(parsed_timestamp) or parsed_timestamp < 0:
        raise LocationValidationError("Location timestamp must be non-negative.")

    parsed_source = str(source or "").strip()
    if not parsed_source or len(parsed_source) > 100:
        raise LocationValidationError("A location source is required.")

    return HardwareLocation(lat, lon, parsed_accuracy, parsed_timestamp, parsed_source)


class ReverseGeocoder:
    """Provider abstraction; without a configured key it returns coordinates."""

    def __init__(self, api_key: str | None = None, timeout: float = 5.0) -> None:
        self.api_key = api_key or os.getenv("GOOGLE_GEOCODING_API_KEY")
        self.timeout = timeout

    def resolve(self, location: HardwareLocation) -> GeocodedLocation:
        if not self.api_key:
            return GeocodedLocation(
                formatted_address=(f"{location.latitude:.6f}, {location.longitude:.6f}"),
                provider="coordinates",
            )

        query = urlencode(
            {
                "latlng": f"{location.latitude},{location.longitude}",
                "key": self.api_key,
            }
        )
        request = Request(
            f"https://maps.googleapis.com/maps/api/geocode/json?{query}",
            headers={"User-Agent": "CARES-Backend/1.0"},
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            results = payload.get("results") or []
            if payload.get("status") == "OK" and results:
                address = results[0].get("formatted_address")
                if address:
                    return GeocodedLocation(address, "google")
        except Exception:
            pass

        return GeocodedLocation(
            formatted_address=(f"{location.latitude:.6f}, {location.longitude:.6f}"),
            provider="coordinates",
        )
