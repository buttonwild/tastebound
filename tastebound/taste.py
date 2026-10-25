"""Qloo Taste AI client: turn what a traveller loves into places worth visiting.

Flow (per Qloo's hackathon guide):
  1. /search resolves free-text tastes ("Radiohead", "Wes Anderson") to entity IDs
  2. /v2/insights with filter.type=urn:entity:place, signal.interests.entities
     and filter.location near the destination returns taste-matched places.

Note: Qloo's terms forbid storing their response data in a public repo, so
tests use synthetic fixtures only.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import requests

from .limits import QuotaExceeded, UsageLimiter

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TasteEntity:
    entity_id: str
    name: str
    types: tuple[str, ...] = ()


@dataclass(frozen=True)
class TastePlace:
    name: str
    entity_id: str
    address: str | None = None
    affinity: float | None = None
    rating: float | None = None
    price_level: int | None = None
    tags: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "address": self.address,
            "affinity": self.affinity,
            "rating": self.rating,
            "price_level": self.price_level,
            "tags": list(self.tags),
        }


def _items(data: dict, key: str) -> list[dict]:
    """Qloo nests lists either as results=[...] or results={key: [...]}."""
    results = data.get("results")
    if isinstance(results, dict):
        results = results.get(key)
    return [r for r in results or [] if isinstance(r, dict)]


class QlooError(RuntimeError):
    pass


class QlooClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout: float = 15.0,
        session: requests.Session | None = None,
        limiter: UsageLimiter | None = None,
    ):
        self._limiter = limiter or UsageLimiter("qloo")
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers["X-Api-Key"] = api_key

    def _get(self, path: str, params: dict) -> dict:
        try:
            self._limiter.acquire()
        except QuotaExceeded as exc:
            raise QlooError(f"Taste lookups paused: {exc}") from exc
        try:
            resp = self._session.get(f"{self._base}{path}", params=params, timeout=self._timeout)
        except requests.RequestException as exc:
            raise QlooError(f"Qloo request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise QlooError(f"Qloo {path} returned HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError as exc:
            raise QlooError("Qloo returned non-JSON") from exc

    def search_entity(self, query: str) -> TasteEntity | None:
        data = self._get("/search", {"query": query, "take": 1})
        results = _items(data, "entities")
        if not results:
            return None
        hit = results[0]
        return TasteEntity(
            entity_id=hit["entity_id"],
            name=hit.get("name", query),
            types=tuple(hit.get("types") or ()),
        )

    def resolve_tastes(self, tastes: list[str]) -> list[TasteEntity]:
        """Resolve tastes in parallel; a failed lookup just drops that taste."""

        def lookup(taste: str) -> TasteEntity | None:
            try:
                return self.search_entity(taste)
            except QlooError as exc:
                log.warning("Qloo search failed for a taste: %s", exc)
                return None

        if not tastes:
            return []
        with ThreadPoolExecutor(max_workers=min(len(tastes), 5)) as pool:
            return [e for e in pool.map(lookup, tastes) if e]

    def places_near(
        self,
        lat: float,
        lon: float,
        entity_ids: list[str],
        tag_ids: list[str] | None = None,
        radius_m: int = 3000,
        take: int = 5,
    ) -> list[TastePlace]:
        params: dict = {
            "filter.type": "urn:entity:place",
            # The hackathon API rejects "lat,lon" despite the docs; WKT is longitude first
            "filter.location": f"POINT({lon} {lat})",
            "filter.location.radius": radius_m,
            "take": take,
        }
        if entity_ids:
            params["signal.interests.entities"] = ",".join(entity_ids)
        if tag_ids:
            params["filter.tags"] = ",".join(tag_ids)
        if not entity_ids and not tag_ids:
            # Without a taste signal fall back to well-rated popular places
            params["filter.popularity.min"] = 0.7
        data = self._get("/v2/insights", params)
        return [self._to_place(e) for e in _items(data, "entities")]

    def search_tags(self, query: str, take: int = 3) -> list[str]:
        data = self._get("/v2/tags", {"filter.query": query, "take": take})
        return [t.get("id") or t.get("tag_id") for t in _items(data, "tags") if t.get("id") or t.get("tag_id")]

    @staticmethod
    def _to_place(entity: dict) -> TastePlace:
        props = entity.get("properties") or {}
        query = entity.get("query") or {}
        tags = tuple(t.get("name") for t in (entity.get("tags") or []) if t.get("name"))[:6]
        return TastePlace(
            name=entity.get("name", "Unknown"),
            entity_id=entity.get("entity_id", ""),
            address=props.get("address"),
            affinity=query.get("affinity"),
            rating=props.get("business_rating"),
            price_level=props.get("price_level"),
            tags=tags,
        )
