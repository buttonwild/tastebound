"""Hard spending cap for Nebius Token Factory calls.

Every call is charged its worst case (estimated prompt + full max_tokens output)
*before* it is sent, and refused if that could push cumulative spend over the
cap. After the call the reservation is replaced by the cost of the reported
token usage. A failed call keeps its reservation (it may still have been
billed). Costs carry a safety factor, so the guard over-counts real spend.

The ledger is a JSON file updated under an exclusive file lock, so the cap
holds across restarts and across processes on the same machine. Each machine
(e.g. a hosted deployment) keeps its own ledger.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path

from .ledger import locked_json, state_dir
from .limits import QuotaExceeded

HARD_CAP_USD = 50.0
SAFETY_FACTOR = 1.25

# USD per 1M tokens (input, output), Nebius Token Factory list prices.
PRICES_PER_M = {
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.06, 0.24),
    "nvidia/nemotron-3-super-120b-a12b": (0.30, 0.90),
}
# Deliberately high price for any model not in the table.
UNKNOWN_MODEL_PRICE_PER_M = (3.0, 15.0)

CHARS_PER_TOKEN = 3  # conservative: real English averages ~4


class BudgetExceeded(QuotaExceeded):
    """Cumulative spend cap reached."""


def default_ledger_path() -> Path:
    return Path(os.environ.get("TASTEBOUND_SPEND_LEDGER", state_dir() / "nebius_spend.json"))


def cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    price_in, price_out = PRICES_PER_M.get(model, UNKNOWN_MODEL_PRICE_PER_M)
    return (prompt_tokens * price_in + completion_tokens * price_out) / 1_000_000 * SAFETY_FACTOR


def estimate_prompt_tokens(payload) -> int:
    return len(json.dumps(payload, ensure_ascii=False, default=str)) // CHARS_PER_TOKEN + 16


class SpendGuard:
    def __init__(self, ledger_path: Path | None = None, cap_usd: float | None = None):
        self.ledger_path = Path(ledger_path or default_ledger_path())
        requested = cap_usd if cap_usd is not None else float(os.environ.get("TASTEBOUND_NEBIUS_BUDGET_USD", HARD_CAP_USD))
        # The env var can lower the cap but never raise it above the hard limit
        self.cap_usd = max(0.0, min(requested, HARD_CAP_USD))

    @contextmanager
    def _ledger(self):
        with locked_json(self.ledger_path) as data:
            data.setdefault("spent_usd", 0.0)
            data.setdefault("calls", 0)
            data.setdefault("refused", 0)
            data.setdefault("prompt_tokens", 0)
            data.setdefault("completion_tokens", 0)
            yield data

    def snapshot(self) -> dict:
        with self._ledger() as data:
            snap = dict(data)
        snap["cap_usd"] = self.cap_usd
        snap["remaining_usd"] = max(0.0, self.cap_usd - snap["spent_usd"])
        return snap

    def reserve(self, model: str, prompt_payload, max_tokens: int) -> float:
        """Charge the worst-case cost up front; raise BudgetExceeded if over cap."""
        worst = cost_usd(model, estimate_prompt_tokens(prompt_payload), max_tokens)
        with self._ledger() as data:
            spent = data["spent_usd"]
            allowed = spent + worst <= self.cap_usd
            if allowed:
                data["spent_usd"] += worst
            else:
                data["refused"] += 1
        # Raise only after the ledger (including the refusal count) is saved
        if not allowed:
            raise BudgetExceeded(f"Nebius budget cap reached: spent ${spent:.4f} of ${self.cap_usd:g}")
        return worst

    def settle(self, model: str, reserved: float, usage) -> float:
        """Replace a reservation with the cost of actual usage (if reported)."""
        prompt = getattr(usage, "prompt_tokens", None)
        completion = getattr(usage, "completion_tokens", None)
        if prompt is None or completion is None:
            return reserved  # unknown usage: keep the worst-case charge
        actual = cost_usd(model, prompt, completion)
        with self._ledger() as data:
            data["spent_usd"] = max(0.0, data["spent_usd"] - reserved) + actual
            data["calls"] += 1
            data["prompt_tokens"] += prompt
            data["completion_tokens"] += completion
        return actual
