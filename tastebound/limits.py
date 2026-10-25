"""App-wide usage limits for every paid or quota-limited API.

The per-browser-session turn limit stops one tab from looping; it does nothing
against someone opening new sessions. These limits are global: one ledger for
the whole app, shared by every session, thread and process on the machine.

Each service gets fixed UTC windows (per hour, per day) plus an optional
cumulative cap (e.g. Tavily credits). Units are reserved *before* the request
and not refunded on failure, so the count over-approximates real usage.
Environment variables can lower a limit but never raise it above its hard
maximum.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .ledger import locked_json, state_dir


class QuotaExceeded(RuntimeError):
    """A usage limit or spending cap was reached; callers degrade gracefully."""


@dataclass(frozen=True)
class LimitSpec:
    per_hour: int
    per_day: int
    total: int | None = None  # cumulative cap, never resets


# Defaults and hard maxima per service: (default, hard_max)
DEFAULTS = {
    "nebius": {"per_hour": (300, 600), "per_day": (1500, 3000), "total": (None, None)},
    "tavily": {"per_hour": (60, 120), "per_day": (200, 400), "total": (500, 1000)},  # credits
    "qloo": {"per_hour": (300, 600), "per_day": (2000, 4000), "total": (None, None)},
    "youcam": {"per_hour": (20, 40), "per_day": (60, 120), "total": (None, None)},  # paid tasks
    "tendem": {"per_hour": (30, 60), "per_day": (120, 240), "total": (None, None)},  # all MCP calls
}


def _bounded(env_name: str, default: int | None, hard_max: int | None) -> int | None:
    raw = os.environ.get(env_name, "").strip()
    value = int(float(raw)) if raw else default
    if value is None:
        return None
    if hard_max is not None:
        value = min(value, hard_max)
    return max(0, value)


def spec_for(service: str) -> LimitSpec:
    d = DEFAULTS[service]
    prefix = f"TASTEBOUND_{service.upper()}_"
    return LimitSpec(
        per_hour=_bounded(prefix + "MAX_PER_HOUR", *d["per_hour"]),
        per_day=_bounded(prefix + "MAX_PER_DAY", *d["per_day"]),
        total=_bounded(prefix + "MAX_TOTAL", *d["total"]),
    )


def default_limits_path() -> Path:
    return Path(os.environ.get("TASTEBOUND_LIMITS_LEDGER", state_dir() / "usage_limits.json"))


class UsageLimiter:
    def __init__(self, service: str, spec: LimitSpec | None = None, ledger_path: Path | None = None, clock=None):
        self.service = service
        self.spec = spec or spec_for(service)
        self.ledger_path = Path(ledger_path or default_limits_path())
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def _windows(self) -> tuple[str, str]:
        now = self._clock()
        return now.strftime("%Y-%m-%dT%H"), now.strftime("%Y-%m-%d")

    def _rolled(self, data: dict) -> dict:
        hour, day = self._windows()
        entry = data.setdefault(self.service, {})
        if entry.get("hour") != hour:
            entry["hour"], entry["hour_used"] = hour, 0
        if entry.get("day") != day:
            entry["day"], entry["day_used"] = day, 0
        entry.setdefault("total_used", 0)
        entry.setdefault("refused", 0)
        return entry

    def acquire(self, units: int = 1) -> None:
        """Reserve units or raise QuotaExceeded (the refusal is recorded first)."""
        reason = None
        with locked_json(self.ledger_path) as data:
            entry = self._rolled(data)
            if entry["hour_used"] + units > self.spec.per_hour:
                reason = f"hourly limit ({self.spec.per_hour})"
            elif entry["day_used"] + units > self.spec.per_day:
                reason = f"daily limit ({self.spec.per_day})"
            elif self.spec.total is not None and entry["total_used"] + units > self.spec.total:
                reason = f"cumulative limit ({self.spec.total})"
            if reason:
                entry["refused"] += 1
            else:
                entry["hour_used"] += units
                entry["day_used"] += units
                entry["total_used"] += units
        if reason:
            raise QuotaExceeded(f"{self.service} {reason} reached")

    def snapshot(self) -> dict:
        with locked_json(self.ledger_path) as data:
            entry = dict(self._rolled(data))
        entry.update(per_hour=self.spec.per_hour, per_day=self.spec.per_day, total=self.spec.total)
        return entry
