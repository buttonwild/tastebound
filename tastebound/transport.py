"""Indicative UK journey options between two points.

Durations and fares are heuristic estimates (no live timetable or fare data);
the UI labels them as estimates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from geopy.distance import geodesic

MODES = ("walk", "bus", "train", "flight")

LOCAL_BUS_MAX_KM = 15


@dataclass(frozen=True)
class Route:
    mode: str
    distance_km: float
    duration_min: float
    cost_gbp: float
    note: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def estimate_cost(distance_km: float, mode: str) -> float:
    if mode == "walk":
        return 0.0
    if mode == "bus":
        if distance_km <= LOCAL_BUS_MAX_KM:
            return 3.0  # flat urban fare
        return round(5 + distance_km * 0.06, 2)  # long-distance coach
    if mode == "train":
        return round(distance_km * 0.25, 2)
    if mode == "flight":
        return round(40 + distance_km * 0.12, 2)  # base fare + distance
    raise ValueError(f"Unknown mode: {mode}")


def _feasible_modes(distance_km: float) -> tuple[str, ...]:
    if distance_km <= 1:
        return ("walk",)
    if distance_km <= 5:
        return ("walk", "bus")
    if distance_km <= LOCAL_BUS_MAX_KM:
        return ("bus",)
    if distance_km <= 150:
        return ("bus", "train")
    return ("bus", "train", "flight")


def _duration_min(distance_km: float, mode: str) -> float:
    if mode == "walk":
        return distance_km / 5 * 60
    if mode == "bus":
        speed = 30 if distance_km <= LOCAL_BUS_MAX_KM else 65
        return max(distance_km / speed * 60, 10)
    if mode == "train":
        return max(distance_km / 90 * 60, 20)
    if mode == "flight":
        return distance_km / 700 * 60 + 90  # check-in and security overhead
    raise ValueError(f"Unknown mode: {mode}")


def get_routes(start: tuple[float, float], end: tuple[float, float]) -> list[Route]:
    if not start or not end or len(start) != 2 or len(end) != 2:
        raise ValueError("Coordinates must be (latitude, longitude) tuples")

    distance_km = geodesic(start, end).km
    routes = []
    for mode in _feasible_modes(distance_km):
        note = None
        if mode == "flight":
            note = "Includes airport check-in and security time"
        elif mode == "bus" and distance_km > LOCAL_BUS_MAX_KM:
            note = "Long-distance coach"
        routes.append(
            Route(
                mode=mode,
                distance_km=round(distance_km, 2),
                duration_min=round(_duration_min(distance_km, mode), 1),
                cost_gbp=estimate_cost(distance_km, mode),
                note=note,
            )
        )
    return routes


def choose_best_route(routes: list[Route], preference: str) -> Route:
    if not routes:
        raise ValueError("No routes available to choose from")
    if preference == "save money":
        return min(routes, key=lambda r: (r.cost_gbp, r.duration_min))
    if preference == "balanced":
        fastest = min(r.duration_min for r in routes)
        reasonable = [r for r in routes if r.duration_min <= 2 * fastest]
        return min(reasonable, key=lambda r: (r.cost_gbp, r.duration_min))
    return min(routes, key=lambda r: (r.duration_min, r.cost_gbp))


def route_by_mode(routes: list[Route], mode: str) -> Route | None:
    return next((r for r in routes if r.mode == mode), None)
