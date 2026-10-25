"""Human errands via Tendem (hosted MCP server): last-mile verification calls.

Only one task template exists: a phone call asking a restaurant whether it is
open tonight, has a table for 2 around 19:30, and takes walk-ins. Nothing is
booked.

Layers of protection; everything below must pass before money can move:
  * OperatorGate: passphrase from TASTEBOUND_OPERATOR_PASSPHRASE, compared in
    constant time; a global, file-backed failure counter locks the gate after
    repeated failures. No passphrase (or a weak one) = operator mode disabled.
  * Arming switch: create/approve/message calls also need TASTEBOUND_TENDEM_LIVE=1
    (off by default and on the public demo).
  * ErrandGuard (persistent ledger): per-task cap, cumulative cap, task count.
  * Real-balance floor: before approval, Tendem's actual balance is read and
    approval is refused if it would end below the floor. Holds when a free
    host wipes the ledger.
  * Approval re-reads the live contract and requires the operator to confirm
    the exact price string the server quoted.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from .ledger import locked_json, state_dir
from .limits import QuotaExceeded, UsageLimiter

log = logging.getLogger(__name__)

TENDEM_MCP_URL = "https://mcp.tendem.ai/mcp"

HARD_MAX_TASK_USD = 12.0
HARD_MAX_TOTAL_USD = 30.0
HARD_MAX_TASKS = 2
DEFAULT_MAX_TASKS = 1  # only demo task 1 is approved
HARD_MIN_BALANCE_USD = 20.0  # reserve kept on the small hackathon credit, beyond the $30 cap

MIN_PASSPHRASE_LEN = 20
LOCKOUT_FAILURES = 5
LOCKOUT_WINDOW_S = 15 * 60
FAILURE_DELAY_S = 1.0

READ_TOOLS = frozenset({"get_account", "get_task", "get_contract", "get_task_result", "list_tasks", "read_chat"})
WRITE_TOOLS = frozenset({"create_task", "send_message", "approve_task", "cancel_task"})

_APPROVE_LOCK = threading.Lock()
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class ErrandError(RuntimeError):
    pass


class AccessDenied(ErrandError):
    pass


# --------------------------------------------------------------------------- config helpers
def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    return float(raw) if raw else default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def live_enabled() -> bool:
    return os.environ.get("TASTEBOUND_TENDEM_LIVE", "").strip() == "1"


def parse_money(value: Any) -> tuple[float | None, str | None]:
    """(amount, server-formatted string) from the shapes Tendem uses."""
    if value is None or isinstance(value, bool):
        return None, None
    if isinstance(value, (int, float)):
        return float(value), None
    if isinstance(value, str):
        match = _NUMBER.search(value.replace(",", ""))
        return (float(match.group()) if match else None), value.strip() or None
    if isinstance(value, dict):
        amount, _ = parse_money(value.get("amount"))
        formatted = value.get("formatted") if isinstance(value.get("formatted"), str) else None
        return amount, formatted
    return None, None


# --------------------------------------------------------------------------- operator gate
class OperatorGate:
    def __init__(
        self,
        passphrase: str | None = None,
        ledger_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        secret = passphrase if passphrase is not None else os.environ.get("TASTEBOUND_OPERATOR_PASSPHRASE", "")
        secret = secret.strip()
        # Store only a digest; a weak or missing passphrase disables operator mode
        self._digest = hashlib.sha256(secret.encode()).digest() if len(secret) >= MIN_PASSPHRASE_LEN else None
        self.ledger_path = Path(ledger_path or os.environ.get("TASTEBOUND_OPERATOR_LEDGER", state_dir() / "operator_gate.json"))
        self._clock = clock
        self._sleep = sleep

    @property
    def enabled(self) -> bool:
        return self._digest is not None

    def _recent_failures(self, data: dict) -> list[float]:
        now = self._clock()
        failures = [t for t in data.get("failures", []) if now - t < LOCKOUT_WINDOW_S]
        data["failures"] = failures
        return failures

    def locked_for(self) -> float:
        """Seconds until the gate unlocks after too many failures (0 = open)."""
        with locked_json(self.ledger_path) as data:
            failures = self._recent_failures(data)
        if len(failures) < LOCKOUT_FAILURES:
            return 0.0
        return max(0.0, LOCKOUT_WINDOW_S - (self._clock() - failures[0]))

    def verify(self, attempt: str) -> bool:
        """Constant-time check; failures are rate limited globally (all sessions)."""
        if not self.enabled:
            raise AccessDenied("Operator mode is disabled (no strong passphrase configured)")
        with locked_json(self.ledger_path) as data:
            failures = self._recent_failures(data)
            locked = len(failures) >= LOCKOUT_FAILURES
            ok = False
            if not locked:
                candidate = hashlib.sha256((attempt or "").strip().encode()).digest()
                ok = hmac.compare_digest(candidate, self._digest)
                if ok:
                    data["failures"] = []
                else:
                    failures.append(self._clock())
                    data["failures"] = failures
        if locked:
            raise AccessDenied("Too many failed attempts; operator mode is locked for a while")
        if not ok:
            self._sleep(FAILURE_DELAY_S)  # fixed delay on every failure
        return ok


# --------------------------------------------------------------------------- spend guard
class ErrandGuard:
    """Persistent caps: per task, cumulative, and number of approved tasks."""

    def __init__(self, ledger_path: Path | None = None):
        self.ledger_path = Path(ledger_path or os.environ.get("TASTEBOUND_TENDEM_LEDGER", state_dir() / "tendem_spend.json"))
        self.max_task_usd = min(_env_float("TASTEBOUND_TENDEM_MAX_TASK_USD", HARD_MAX_TASK_USD), HARD_MAX_TASK_USD)
        self.max_total_usd = min(_env_float("TASTEBOUND_TENDEM_BUDGET_USD", HARD_MAX_TOTAL_USD), HARD_MAX_TOTAL_USD)
        self.max_tasks = max(0, min(_env_int("TASTEBOUND_TENDEM_MAX_TASKS", DEFAULT_MAX_TASKS), HARD_MAX_TASKS))

    def reserve(self, price_usd: float) -> None:
        reason = None
        with locked_json(self.ledger_path) as data:
            spent = data.setdefault("spent_usd", 0.0)
            tasks = data.setdefault("tasks_approved", 0)
            if price_usd > self.max_task_usd:
                reason = f"quote ${price_usd:.2f} is above the per-task cap ${self.max_task_usd:.2f}"
            elif spent + price_usd > self.max_total_usd:
                reason = f"cumulative cap ${self.max_total_usd:.2f} would be exceeded (spent ${spent:.2f})"
            elif tasks + 1 > self.max_tasks:
                reason = f"task limit reached ({tasks} of {self.max_tasks})"
            if reason:
                data["refused"] = data.get("refused", 0) + 1
            else:
                data["spent_usd"] = spent + price_usd
                data["tasks_approved"] = tasks + 1
        if reason:
            raise QuotaExceeded(f"Tendem {reason}")

    def release(self, price_usd: float) -> None:
        with locked_json(self.ledger_path) as data:
            data["spent_usd"] = max(0.0, data.get("spent_usd", 0.0) - price_usd)
            data["tasks_approved"] = max(0, data.get("tasks_approved", 0) - 1)

    def snapshot(self) -> dict:
        with locked_json(self.ledger_path) as data:
            return {
                "spent_usd": data.get("spent_usd", 0.0),
                "tasks_approved": data.get("tasks_approved", 0),
                "max_task_usd": self.max_task_usd,
                "max_total_usd": self.max_total_usd,
                "max_tasks": self.max_tasks,
            }


# --------------------------------------------------------------------------- the only task template
@dataclass(frozen=True)
class RestaurantCheck:
    restaurant: str
    address: str
    city: str
    date: str  # e.g. "Saturday 25 October 2026"
    phone: str | None = None
    time: str = "19:30"
    party_size: int = 2

    def validate(self) -> None:
        for name in ("restaurant", "address", "city", "date"):
            value = getattr(self, name)
            if not value or not value.strip() or len(value) > 200 or "\n" in value:
                raise ValueError(f"{name} must be a single line of 1-200 characters")
        if self.phone and not re.fullmatch(r"\+?[0-9 ()-]{6,20}", self.phone):
            raise ValueError("phone must look like a phone number")
        if not re.fullmatch(r"\d{1,2}:\d{2}", self.time):
            raise ValueError("time must be HH:MM")
        if not 1 <= self.party_size <= 6:
            raise ValueError("party_size must be 1-6")

    @property
    def title(self) -> str:
        return f"Restaurant check: {self.restaurant}, {self.city}"

    def brief(self) -> str:
        self.validate()
        phone = f", phone {self.phone}" if self.phone else " (find the public phone number)"
        return (
            f"Call {self.restaurant}, {self.address}, {self.city}{phone}, on {self.date} between "
            f"15:00 and 18:00 local time.\n"
            f"Ask: (1) Are you open tonight, and until what time? (2) Is a table for {self.party_size} "
            f"available around {self.time}? (3) Do you accept walk-ins, or bookings only?\n"
            f"Do NOT make a booking or give any personal details.\n"
            f"Deliverable: three short answers plus the local time of the call. If there is no answer "
            f"after 2 attempts, report 'no answer'. Unverifiable details must not be a rejection reason."
        )


@dataclass(frozen=True)
class Quote:
    task_id: str
    state: str | None
    title: str | None
    scope: str | None
    price_usd: float | None
    price_display: str | None
    raw: dict = field(default_factory=dict, repr=False)

    @property
    def quoted(self) -> bool:
        return self.price_usd is not None


# --------------------------------------------------------------------------- transport
class Transport(Protocol):
    def call(self, tool: str, args: dict) -> dict: ...


def payload_from_result(result: Any) -> dict:
    """Normalise an MCP CallToolResult (structured content, JSON text, or 'Result data:' text)."""
    texts = [getattr(c, "text", "") for c in getattr(result, "content", []) or []]
    joined = "\n".join(t for t in texts if t).strip()
    if getattr(result, "isError", False):
        raise ErrandError(f"Tendem tool error: {joined[:200] or 'unknown'}")
    structured = getattr(result, "structuredContent", None)
    if isinstance(structured, dict):
        inner = structured.get("result")
        return inner if set(structured) == {"result"} and isinstance(inner, dict) else structured
    if not joined:
        return {}
    for candidate in (joined, joined.split("Result data:", 1)[-1]):
        try:
            decoded = json.loads(candidate.strip())
            return decoded if isinstance(decoded, dict) else {"content": decoded}
        except json.JSONDecodeError:
            continue
    return {"content": joined}


class MCPTransport:
    """Tendem's hosted MCP server over streamable HTTP with API-key auth (mcp SDK 1.x)."""

    def __init__(self, api_key: str, url: str = TENDEM_MCP_URL, timeout: float = 60.0):
        self._headers = {"Authorization": f"ApiKey {api_key}"}
        self._url = url
        self._timeout = timeout

    async def _call(self, tool: str, args: dict) -> dict:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self._url, headers=self._headers, timeout=self._timeout) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return payload_from_result(await session.call_tool(tool, args))

    def call(self, tool: str, args: dict) -> dict:
        return asyncio.run(self._call(tool, args))


# --------------------------------------------------------------------------- client
class ErrandsClient:
    def __init__(
        self,
        transport: Transport,
        guard: ErrandGuard | None = None,
        limiter: UsageLimiter | None = None,
        min_balance: float | None = None,
        live: bool | None = None,
    ):
        self._transport = transport
        self.guard = guard or ErrandGuard()
        self._limiter = limiter or UsageLimiter("tendem")
        requested = min_balance if min_balance is not None else _env_float("TASTEBOUND_TENDEM_MIN_BALANCE", HARD_MIN_BALANCE_USD)
        self.min_balance = max(requested, HARD_MIN_BALANCE_USD)  # env can only raise the floor
        self.live = live_enabled() if live is None else live

    def _call(self, tool: str, args: dict, *, operator: bool = False) -> dict:
        if tool not in READ_TOOLS | WRITE_TOOLS:
            raise ErrandError(f"Tool {tool} is not allowed")
        if tool in WRITE_TOOLS:
            if not operator:
                raise AccessDenied("Operator unlock required")
            if not self.live:
                raise AccessDenied("Live Tendem calls are disarmed (set TASTEBOUND_TENDEM_LIVE=1)")
        self._limiter.acquire()
        return self._transport.call(tool, args)

    # ---- read-only
    def balance(self) -> float:
        data = self._call("get_account", {})
        amount, _ = parse_money(data.get("balance"))
        if amount is None:
            # e.g. "Tendem balance: $45.00. Top up at ..." (the sentence's full stop is not part of the number)
            match = re.search(r"balance:\s*\$([0-9][0-9,]*(?:\.[0-9]+)?)", str(data.get("content", "")))
            amount = float(match.group(1).replace(",", "")) if match else None
        if amount is None:
            raise ErrandError("Could not read Tendem balance")
        return amount

    def floor_status(self, price_usd: float = 0.0) -> dict:
        remaining = self.balance()  # live read, never cached; no local state involved
        return {
            "balance": remaining,
            "floor": self.min_balance,
            "headroom": remaining - self.min_balance,
            "allowed": remaining - price_usd >= self.min_balance,
        }

    def quote(self, task_id: str) -> Quote:
        data = self._call("get_contract", {"task_id": task_id})
        inner = data.get("contract") if isinstance(data.get("contract"), dict) else {}
        amount, formatted = parse_money(data.get("price"))
        if amount is not None and not formatted:
            formatted = f"${amount:.2f}"
        return Quote(
            task_id=task_id,
            state=data.get("state"),
            title=inner.get("title"),
            scope=inner.get("input_prompt") or inner.get("description"),
            price_usd=amount,
            price_display=formatted,
            raw=data,
        )

    def status(self, task_id: str) -> dict:
        return self._call("get_task", {"task_id": task_id})

    def result(self, task_id: str) -> dict:
        return self._call("get_task_result", {"task_id": task_id})

    # ---- operator + armed only
    def submit(self, check: RestaurantCheck, *, operator: bool) -> str:
        brief = check.brief()
        data = self._call("create_task", {"name": check.title, "description": brief}, operator=operator)
        task_id = data.get("task_id") or data.get("id")
        if not task_id:
            raise ErrandError("Tendem did not return a task id")
        return str(task_id)

    def answer(self, task_id: str, message: str, *, operator: bool) -> None:
        if not message.strip() or len(message) > 1000:
            raise ValueError("message must be 1-1000 characters")
        self._call("send_message", {"task_id": task_id, "message": message}, operator=operator)

    def approve(self, task_id: str, confirmed_price: str, *, operator: bool) -> dict:
        """Approve the server's *current* quote, only if every cap passes.

        `confirmed_price` must equal the price string the operator was shown,
        so a quote that changed after review is never approved.
        """
        if not operator:
            raise AccessDenied("Operator unlock required")
        if not self.live:
            raise AccessDenied("Live Tendem calls are disarmed (set TASTEBOUND_TENDEM_LIVE=1)")
        with _APPROVE_LOCK:
            quote = self.quote(task_id)  # fresh from the server
            if not quote.quoted:
                raise ErrandError("Task has not been quoted yet; nothing to approve")
            if quote.price_display != confirmed_price:
                raise ErrandError("The quote changed since you reviewed it; review it again")
            if quote.price_usd > self.guard.max_task_usd:
                raise QuotaExceeded(f"Tendem quote {quote.price_display} is above the per-task cap ${self.guard.max_task_usd:.2f}")
            floor = self.floor_status(quote.price_usd)
            if not floor["allowed"]:
                raise QuotaExceeded(f"Tendem balance floor reached (${floor['balance']:.2f} left, floor ${self.min_balance:.2f})")
            self.guard.reserve(quote.price_usd)
            try:
                outcome = self._call(
                    "approve_task",
                    {"task_id": task_id, "name": quote.title or "Restaurant check", "price": quote.price_display},
                    operator=True,
                )
            except Exception:
                # Unknown outcome: keep the reservation (it may have been charged)
                raise
            if outcome.get("topup_url") or outcome.get("reason") == "insufficient_balance":
                self.guard.release(quote.price_usd)  # nothing was charged; never follow top-up links
                raise ErrandError("Tendem balance too low; top-up is not done automatically")
            return outcome
