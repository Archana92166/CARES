"""
CARES Hardware Location Service.

GPS coordinates are always supplied by the connected hardware.
This module never invents or estimates a user's location.

It provides:
    - latitude / longitude
    - human-readable address when reverse geocoding is available
    - Google Maps URL for the exact coordinates
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import json


@dataclass(frozen=True)
class HardwareLocation:
    latitude: float
    longitude: float


@dataclass(frozen=True)
class ResolvedLocation:
    latitude: float
    longitude: float
    address: str
    map_url: str


class LocationService:
    """
    Converts hardware GPS coordinates into a guardian-readable location.

    The GPS coordinates must come from the physical/device hardware.
    """

    @staticmethod
    def from_hardware(
        latitude: float,
        longitude: float,
    ) -> HardwareLocation:

        latitude = float(latitude)
        longitude = float(longitude)

        if not -90.0 <= latitude <= 90.0:
            raise ValueError("Invalid GPS latitude.")

        if not -180.0 <= longitude <= 180.0:
            raise ValueError("Invalid GPS longitude.")

        return HardwareLocation(
            latitude=latitude,
            longitude=longitude,
        )

    @staticmethod
    def google_maps_url(
        latitude: float,
        longitude: float,
    ) -> str:

        return (
            "https://www.google.com/maps/search/?api=1"
            f"&query={latitude},{longitude}"
        )

    @staticmethod
    def reverse_geocode(
        location: HardwareLocation,
    ) -> ResolvedLocation:

        """
        Reverse-geocode hardware GPS coordinates.

        Uses OpenStreetMap Nominatim when network access is available.
        If reverse geocoding fails, the exact coordinates are retained
        and the Google Maps URL remains available.
        """

        address = (
            f"{location.latitude:.6f}, "
            f"{location.longitude:.6f}"
        )

        try:
            query = (
                f"{location.latitude},"
                f"{location.longitude}"
            )

            url = (
                "https://nominatim.openstreetmap.org/reverse"
                f"?lat={quote_plus(str(location.latitude))}"
                f"&lon={quote_plus(str(location.longitude))}"
                "&format=jsonv2"
            )

            request = Request(
                url,
                headers={
                    "User-Agent": "CARES-Guardian/1.0"
                },
            )

            with urlopen(request, timeout=5) as response:
                payload = json.loads(
                    response.read().decode("utf-8")
                )

            address = payload.get(
                "display_name",
                address,
            )

        except Exception:
            # GPS coordinates remain authoritative even if
            # reverse geocoding is unavailable.
            pass

        return ResolvedLocation(
            latitude=location.latitude,
            longitude=location.longitude,
            address=address,
            map_url=LocationService.google_maps_url(
                location.latitude,
                location.longitude,
            ),
        )


__all__ = [
    "HardwareLocation",
    "ResolvedLocation",
    "LocationService",
]
