"""UK place lookup via OpenStreetMap Nominatim."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests

log = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float
    city: str | None = None

    @property
    def coords(self) -> tuple[float, float]:
        return (self.lat, self.lon)


class Geocoder:
    def __init__(self, user_agent: str, timeout: float = 15.0, session: requests.Session | None = None):
        self._session = session or requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._timeout = timeout
        self._cache: dict[str, Place | None] = {}

    def lookup(self, query: str) -> Place | None:
        """Return the best UK match for a free-text place, or None."""
        key = query.strip().lower()
        if not key:
            return None
        if key in self._cache:
            return self._cache[key]

        params = {"q": query, "countrycodes": "gb", "format": "json", "limit": 1, "addressdetails": 1}
        try:
            resp = self._session.get(NOMINATIM_URL, params=params, timeout=self._timeout)
            resp.raise_for_status()
            data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Geocoding failed: %s", type(exc).__name__)
            return None  # not cached, so a transient failure can be retried

        place = None
        if data:
            hit = data[0]
            address = hit.get("address", {})
            city = address.get("city") or address.get("town") or address.get("village") or address.get("county")
            # Nominatim's display_name is a full address; the first parts are enough to confirm
            name = ", ".join(hit["display_name"].split(", ")[:3])
            place = Place(name=name, lat=float(hit["lat"]), lon=float(hit["lon"]), city=city)
        self._cache[key] = place
        return place
