"""YouCam (Perfect Corp.) client: apparel virtual try-on + skin analysis.

Two independent hard caps protect the free unit grant:
  1. A local ledger (UnitGuard) reserves a task's units before it is created;
     cumulative use is capped (default and hard maximum: 600 units).
  2. Before every paid task the *real* account balance is read (a free call)
     and the task is refused if it would take the balance below a floor.
     This holds even when a free host wipes the local ledger.
App-wide hourly/daily task limits (limits.py) apply on top.

Units are only consumed when a task succeeds, so a task that ends in
'error' has its reservation released; anything else keeps it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .ledger import locked_json, state_dir
from .limits import QuotaExceeded, UsageLimiter

log = logging.getLogger(__name__)

BASE_URL = "https://yce-api-01.makeupar.com"

# Units per result, from GET /s2s/v2.0/credit/feature-cost (verified 2026-10-24)
CLOTH_UNITS = 2
SKIN_SD_UNITS = {4: 9, 8: 12, 12: 14, 16: 16}  # max concerns -> units (SD)
SD_CONCERNS = (
    "wrinkle", "pore", "texture", "acne", "moisture", "oiliness", "redness", "radiance",
    "eye_bag", "dark_circle", "age_spot", "firmness", "droopy_upper_eyelid", "droopy_lower_eyelid",
)
GARMENT_CATEGORIES = ("full_body", "lower_body", "upper_body", "shoes", "auto")
MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # API limit for images

HARD_MAX_UNITS = 600
HARD_MIN_BALANCE = 440  # keeps at most 600 units of the small hackathon grant usable

# Serialises "read real balance -> create task" so concurrent sessions can't
# both pass the floor check on the same balance (units are charged at create).
_PAID_TASK_LOCK = threading.Lock()


class YouCamError(RuntimeError):
    pass


def skin_units(n_concerns: int) -> int:
    for limit, units in sorted(SKIN_SD_UNITS.items()):
        if n_concerns <= limit:
            return units
    raise ValueError("At most 16 skin concerns per task")


@dataclass(frozen=True)
class Garment:
    label: str
    template_id: str | None = None
    image_url: str | None = None
    category: str = "auto"
    thumb: str | None = None


@dataclass(frozen=True)
class TryOnResult:
    image_url: str  # pre-signed, short-lived; never persisted or logged
    units: int


@dataclass(frozen=True)
class SkinReport:
    scores: dict[str, float]  # concern -> ui_score (0-100, higher is better)
    skin_age: float | None
    units: int
    raw: dict = field(default_factory=dict, repr=False)


class UnitGuard:
    """Persistent cumulative cap on YouCam units (reserve before create)."""

    def __init__(self, ledger_path: Path | None = None, max_units: int | None = None):
        self.ledger_path = Path(ledger_path or os.environ.get("TASTEBOUND_YOUCAM_LEDGER", state_dir() / "youcam_units.json"))
        requested = max_units if max_units is not None else int(os.environ.get("TASTEBOUND_YOUCAM_MAX_UNITS", HARD_MAX_UNITS))
        self.max_units = max(0, min(requested, HARD_MAX_UNITS))

    def reserve(self, units: int) -> None:
        with locked_json(self.ledger_path) as data:
            used = data.setdefault("units_used", 0)
            allowed = used + units <= self.max_units
            if allowed:
                data["units_used"] = used + units
            else:
                data["refused"] = data.get("refused", 0) + 1
        if not allowed:
            raise QuotaExceeded(f"YouCam unit cap reached ({used} of {self.max_units} used)")

    def release(self, units: int) -> None:
        with locked_json(self.ledger_path) as data:
            data["units_used"] = max(0, data.get("units_used", 0) - units)

    def snapshot(self) -> dict:
        with locked_json(self.ledger_path) as data:
            return {"units_used": data.get("units_used", 0), "max_units": self.max_units, "refused": data.get("refused", 0)}


class YouCamClient:
    def __init__(
        self,
        api_key: str,
        timeout: float = 30.0,
        session: requests.Session | None = None,
        limiter: UsageLimiter | None = None,
        guard: UnitGuard | None = None,
        min_balance: float | None = None,
        sleep=time.sleep,
    ):
        self._session = session or requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"
        self._timeout = timeout
        self._limiter = limiter or UsageLimiter("youcam")
        self.guard = guard or UnitGuard()
        requested = min_balance if min_balance is not None else float(os.environ.get("TASTEBOUND_YOUCAM_MIN_BALANCE", HARD_MIN_BALANCE))
        # The env var can raise the floor (safer) but never lower it below the hard floor
        self.min_balance = max(requested, HARD_MIN_BALANCE)
        self._sleep = sleep

    # ---------------------------------------------------------------- HTTP
    def _request(self, method: str, path: str, **kw) -> dict:
        try:
            resp = self._session.request(method, BASE_URL + path, timeout=self._timeout, **kw)
        except requests.RequestException as exc:
            raise YouCamError(f"YouCam request failed: {type(exc).__name__}") from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code != 200:
            code = body.get("error_code") or body.get("error") or ""
            raise YouCamError(f"YouCam {path} returned HTTP {resp.status_code} {code}".strip())
        return body

    # ---------------------------------------------------------------- free calls
    def balance(self) -> float:
        body = self._request("GET", "/s2s/v1.0/client/credit")
        return float(sum((r.get("amount_dec") or 0) for r in body.get("results", [])))

    def list_garments(self, page_size: int = 20) -> list[Garment]:
        body = self._request("GET", "/s2s/v2.0/task/template/cloth", params={"page_size": page_size})
        data = body.get("data", body)
        return [
            Garment(label=t.get("title") or t["id"], template_id=t["id"], thumb=t.get("thumb"), category=t.get("category_name") or "auto")
            for t in data.get("templates", [])
            if t.get("id")
        ]

    def upload(self, image: bytes, content_type: str = "image/jpeg", file_name: str = "photo.jpg") -> str:
        """Two-step upload: register the file, then PUT the bytes to the pre-signed URL."""
        if len(image) > MAX_UPLOAD_BYTES:
            raise YouCamError("Image is larger than 10 MB")
        body = self._request(
            "POST",
            "/s2s/v2.0/file",
            json={"files": [{"content_type": content_type, "file_name": file_name, "file_size": len(image)}]},
        )
        info = body.get("data", body)["files"][0]
        put = info["requests"][0]
        try:
            resp = requests.request(put.get("method", "PUT"), put["url"], headers=put.get("headers", {}), data=image, timeout=self._timeout * 2)
        except requests.RequestException as exc:
            raise YouCamError(f"Upload failed: {type(exc).__name__}") from exc
        if resp.status_code not in (200, 201, 204):
            raise YouCamError(f"Upload returned HTTP {resp.status_code}")
        return info["file_id"]

    # ---------------------------------------------------------------- paid calls
    def floor_status(self, units: int = 0) -> dict:
        """Live check against the provider's real balance; no local state involved.

        Used by every paid task (via _preflight) and by the operator diagnostics,
        so the online check exercises exactly the code path that protects spend.
        """
        remaining = self.balance()  # free API call, never cached
        return {
            "balance": remaining,
            "floor": self.min_balance,
            "headroom": remaining - self.min_balance,
            "allowed": remaining - units >= self.min_balance,
        }

    def _preflight(self, units: int) -> None:
        """All caps are checked before a paid task is created. Raises QuotaExceeded.

        Order matters: the real-balance floor is checked first and does not read
        any local file, so it still holds when a free host wipes the ledgers.
        A failed balance read raises YouCamError, so no task is created (fails closed).
        """
        status = self.floor_status(units)
        if not status["allowed"]:
            raise QuotaExceeded(f"YouCam balance floor reached ({status['balance']:.0f} left, floor {self.min_balance:.0f})")
        self._limiter.acquire()
        self.guard.reserve(units)

    def _run(self, path: str, payload: dict, units: int, timeout_s: float) -> dict:
        with _PAID_TASK_LOCK:
            self._preflight(units)
            try:
                created = self._request("POST", path, json=payload)
                task_id = created.get("data", created)["task_id"]
            except Exception:
                self.guard.release(units)  # the task was never created
                raise
        delay, waited = 2.0, 0.0
        while waited < timeout_s:
            self._sleep(delay)
            waited += delay
            body = self._request("GET", f"{path}/{task_id}")
            data = body.get("data", body)
            status = data.get("task_status")
            if status == "success":
                return data
            if status == "error":
                self.guard.release(units)  # failed tasks consume no units
                raise YouCamError(f"YouCam task failed: {data.get('error') or data.get('failure_reason') or 'unknown'}")
            delay = min(delay * 1.5, 8.0)
        # Still running: keep the reservation (it may yet succeed and be billed)
        raise YouCamError("YouCam task is still processing; try again shortly")

    def try_on(self, garment: Garment, src_file_id: str | None = None, src_file_url: str | None = None, timeout_s: float = 120) -> TryOnResult:
        if bool(src_file_id) == bool(src_file_url):
            raise ValueError("Give exactly one of src_file_id / src_file_url")
        payload: dict = {"src_file_id": src_file_id} if src_file_id else {"src_file_url": src_file_url}
        if garment.template_id:
            payload["template_id"] = garment.template_id
        elif garment.image_url:
            if garment.category not in GARMENT_CATEGORIES:
                raise ValueError(f"garment_category must be one of {GARMENT_CATEGORIES}")
            payload.update(ref_file_url=garment.image_url, garment_category=garment.category)
        else:
            raise ValueError("Garment needs a template_id or an image_url")
        data = self._run("/s2s/v2.0/task/cloth", payload, CLOTH_UNITS, timeout_s)
        return TryOnResult(image_url=_result_url(data), units=CLOTH_UNITS)

    def analyze_skin(self, concerns: list[str], src_file_id: str | None = None, src_file_url: str | None = None, timeout_s: float = 120) -> SkinReport:
        if bool(src_file_id) == bool(src_file_url):
            raise ValueError("Give exactly one of src_file_id / src_file_url")
        bad = [c for c in concerns if c not in SD_CONCERNS]
        if bad or not concerns:
            raise ValueError(f"Unsupported or empty SD concerns: {bad}")
        units = skin_units(len(concerns))
        payload = {"dst_actions": list(concerns), "format": "json", **({"src_file_id": src_file_id} if src_file_id else {"src_file_url": src_file_url})}
        data = self._run("/s2s/v2.0/task/skin-analysis", payload, units, timeout_s)
        return _skin_report(data, units)


def _result_url(data: dict) -> str:
    results = data.get("results")
    candidates = [data.get("url")]
    if isinstance(results, dict):
        candidates += [results.get("url"), *(results.get("urls") or [])]
    elif isinstance(results, list):
        candidates += [r.get("url") if isinstance(r, dict) else r for r in results]
    url = next((c for c in candidates if isinstance(c, str) and c.startswith("http")), None)
    if not url:
        raise YouCamError("YouCam task succeeded but returned no image URL")
    return url


def _skin_report(data: dict, units: int) -> SkinReport:
    results = data.get("results") or {}
    output = results.get("output") if isinstance(results, dict) else results
    scores: dict[str, float] = {}
    skin_age = None
    for item in output or []:
        if not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "skin_age":
            skin_age = item.get("score") or item.get("ui_score")
        elif kind and item.get("ui_score") is not None:
            scores[kind] = float(item["ui_score"])
    return SkinReport(scores=scores, skin_age=skin_age, units=units, raw=data)
