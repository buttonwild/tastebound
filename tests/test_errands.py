"""Tendem errands: operator gate, arming switch, caps, real-balance floor. Fake transport only."""

from types import SimpleNamespace

import pytest

import tastebound.errands as errands
from tastebound.errands import (
    HARD_MAX_TASK_USD, HARD_MAX_TOTAL_USD, HARD_MIN_BALANCE_USD, LOCKOUT_FAILURES, LOCKOUT_WINDOW_S,
    AccessDenied, ErrandError, ErrandGuard, ErrandsClient, OperatorGate, RestaurantCheck, payload_from_result,
)
from tastebound.limits import LimitSpec, QuotaExceeded, UsageLimiter

STRONG = "x" * 10 + "correct-horse-battery"


class FakeTransport:
    def __init__(self, balance=45.0, price={"amount": 5, "currency": "USD", "formatted": "$5.00"}, approve_outcome=None):
        self.calls = []
        self.balance, self.price = balance, price
        self.approve_outcome = approve_outcome or {"approved": True, "status": "ACTING"}

    def call(self, tool, args):
        self.calls.append((tool, args))
        if tool == "get_account":
            return {"balance": {"amount": self.balance, "currency": "USD", "formatted": f"${self.balance:.2f}"}}
        if tool == "get_contract":
            return {"task_id": args["task_id"], "state": "available" if self.price else "estimating",
                    "contract": {"title": "Restaurant check", "input_prompt": "Call ..."}, "price": self.price}
        if tool == "create_task":
            return {"task_id": "t-1", "status": "LISTENING"}
        if tool == "approve_task":
            return self.approve_outcome
        return {}

    def tools(self):
        return [t for t, _ in self.calls]


def make(tmp_path, transport=None, live=True, **kw):
    t = transport or FakeTransport()
    c = ErrandsClient(t, guard=kw.pop("guard", ErrandGuard(tmp_path / "g.json")),
                      limiter=UsageLimiter("tendem", LimitSpec(100, 100), tmp_path / "l.json"), live=live, **kw)
    return c, t


CHECK = RestaurantCheck(restaurant="Test Bistro", address="1 Test Street", city="Manchester", date="Saturday 25 October 2026")


# ---------------------------------------------------------------- operator gate
class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def gate(tmp_path, secret=STRONG, clock=None):
    slept = []
    g = OperatorGate(secret, tmp_path / "op.json", clock=clock or Clock(), sleep=slept.append)
    return g, slept


def test_gate_disabled_without_strong_passphrase(tmp_path):
    for secret in ("", "short-pass"):
        g, _ = gate(tmp_path, secret)
        assert not g.enabled
        with pytest.raises(AccessDenied):
            g.verify(secret)


def test_gate_accepts_correct_and_delays_wrong(tmp_path):
    g, slept = gate(tmp_path)
    assert g.verify(STRONG) is True and slept == []
    assert g.verify("wrong") is False and slept == [errands.FAILURE_DELAY_S]


def test_gate_uses_constant_time_compare(tmp_path, monkeypatch):
    seen = []
    real = errands.hmac.compare_digest
    monkeypatch.setattr(errands.hmac, "compare_digest", lambda a, b: seen.append((len(a), len(b))) or real(a, b))
    g, _ = gate(tmp_path)
    g.verify("short")
    g.verify(STRONG + "-much-longer-than-the-secret")
    assert seen == [(32, 32), (32, 32)]  # digests: equal length regardless of input


def test_gate_locks_out_globally_after_repeated_failures(tmp_path):
    clock = Clock()
    g, _ = gate(tmp_path, clock=clock)
    for _ in range(LOCKOUT_FAILURES):
        assert g.verify("nope") is False
    other_session = OperatorGate(STRONG, tmp_path / "op.json", clock=clock, sleep=lambda s: None)
    with pytest.raises(AccessDenied, match="locked"):
        other_session.verify(STRONG)  # even the right passphrase, from any session
    assert g.locked_for() > 0
    clock.t += LOCKOUT_WINDOW_S + 1
    assert g.verify(STRONG) is True and g.locked_for() == 0


def test_success_resets_failure_count(tmp_path):
    g, _ = gate(tmp_path)
    for _ in range(LOCKOUT_FAILURES - 1):
        g.verify("nope")
    assert g.verify(STRONG)
    for _ in range(LOCKOUT_FAILURES - 1):
        g.verify("nope")
    assert g.verify(STRONG)  # not locked: counter was reset


# ---------------------------------------------------------------- arming + access
def test_write_tools_need_operator_and_live_flag(tmp_path):
    c, t = make(tmp_path, live=False)
    with pytest.raises(AccessDenied, match="Operator"):
        c.submit(CHECK, operator=False)
    with pytest.raises(AccessDenied, match="disarmed"):
        c.submit(CHECK, operator=True)
    with pytest.raises(AccessDenied):
        c.approve("t-1", "$5.00", operator=True)
    assert t.calls == []  # nothing reached Tendem


def test_live_flag_defaults_off(tmp_path, monkeypatch):
    monkeypatch.delenv("TASTEBOUND_TENDEM_LIVE", raising=False)
    assert ErrandsClient(FakeTransport(), guard=ErrandGuard(tmp_path / "g.json")).live is False
    monkeypatch.setenv("TASTEBOUND_TENDEM_LIVE", "true")  # only the exact value "1" arms it
    assert ErrandsClient(FakeTransport(), guard=ErrandGuard(tmp_path / "g.json")).live is False


def test_unknown_tools_are_refused(tmp_path):
    c, t = make(tmp_path)
    with pytest.raises(ErrandError):
        c._call("delete_everything", {}, operator=True)
    assert t.calls == []


def test_read_calls_work_without_operator(tmp_path):
    c, t = make(tmp_path, live=False)
    assert c.balance() == 45.0
    assert c.quote("t-1").price_display == "$5.00"
    assert t.tools() == ["get_account", "get_contract"]


# ---------------------------------------------------------------- task template
def test_submit_sends_only_the_approved_template(tmp_path):
    c, t = make(tmp_path)
    assert c.submit(CHECK, operator=True) == "t-1"
    tool, args = t.calls[0]
    assert tool == "create_task" and args["name"] == "Restaurant check: Test Bistro, Manchester"
    for phrase in ("open tonight", "table for 2", "around 19:30", "walk-ins", "Do NOT make a booking"):
        assert phrase in args["description"]


@pytest.mark.parametrize("bad", [
    dict(restaurant=""), dict(address="a\nb"), dict(phone="call me maybe"), dict(time="7.30pm"), dict(party_size=20),
])
def test_template_validation(bad):
    base = dict(restaurant="R", address="A", city="C", date="D")
    with pytest.raises(ValueError):
        RestaurantCheck(**{**base, **bad}).brief()


# ---------------------------------------------------------------- approval caps
def test_approve_happy_path_uses_server_price_string(tmp_path):
    c, t = make(tmp_path)
    c.approve("t-1", "$5.00", operator=True)
    assert t.tools() == ["get_contract", "get_account", "approve_task"]
    assert t.calls[-1][1] == {"task_id": "t-1", "name": "Restaurant check", "price": "$5.00"}
    assert c.guard.snapshot()["spent_usd"] == 5.0 and c.guard.snapshot()["tasks_approved"] == 1


def test_unquoted_or_changed_quote_is_not_approved(tmp_path):
    c, t = make(tmp_path, FakeTransport(price=None))
    with pytest.raises(ErrandError, match="not been quoted"):
        c.approve("t-1", "$5.00", operator=True)
    c, t = make(tmp_path, FakeTransport(price={"amount": 7, "formatted": "$7.00"}))
    with pytest.raises(ErrandError, match="changed"):
        c.approve("t-1", "$5.00", operator=True)
    assert "approve_task" not in t.tools()


def test_quote_above_per_task_cap_is_refused(tmp_path):
    c, t = make(tmp_path, FakeTransport(price={"amount": 12.5, "formatted": "$12.50"}))
    with pytest.raises(QuotaExceeded, match="per-task cap"):
        c.approve("t-1", "$12.50", operator=True)
    assert "approve_task" not in t.tools()


def test_real_balance_floor_holds_with_fresh_ledger(tmp_path):
    """A wiped ledger must not unlock spending: $25 balance - $6 quote < $20 floor."""
    c, t = make(tmp_path, FakeTransport(balance=25.0, price={"amount": 6, "formatted": "$6.00"}))
    assert c.guard.snapshot()["spent_usd"] == 0
    with pytest.raises(QuotaExceeded, match="floor"):
        c.approve("t-1", "$6.00", operator=True)
    assert "approve_task" not in t.tools() and c.guard.snapshot()["tasks_approved"] == 0


def test_cumulative_cap_and_task_count(tmp_path, monkeypatch):
    monkeypatch.setenv("TASTEBOUND_TENDEM_MAX_TASKS", "2")
    guard = ErrandGuard(tmp_path / "g.json")
    guard.reserve(10.0)
    guard.reserve(10.0)
    with pytest.raises(QuotaExceeded, match="task limit"):
        guard.reserve(1.0)
    guard2 = ErrandGuard(tmp_path / "g2.json")
    guard2.reserve(12.0)
    guard2.reserve(12.0) if guard2.max_tasks > 1 else None
    with pytest.raises(QuotaExceeded):
        guard2.reserve(12.0)  # 36 > 30 cumulative (or task limit)


def test_default_allows_only_one_task(tmp_path):
    c, t = make(tmp_path)
    c.approve("t-1", "$5.00", operator=True)
    with pytest.raises(QuotaExceeded, match="task limit"):
        c.approve("t-2", "$5.00", operator=True)
    assert t.tools().count("approve_task") == 1


def test_env_cannot_loosen_caps(tmp_path, monkeypatch):
    monkeypatch.setenv("TASTEBOUND_TENDEM_MAX_TASK_USD", "500")
    monkeypatch.setenv("TASTEBOUND_TENDEM_BUDGET_USD", "500")
    monkeypatch.setenv("TASTEBOUND_TENDEM_MAX_TASKS", "50")
    monkeypatch.setenv("TASTEBOUND_TENDEM_MIN_BALANCE", "0")
    g = ErrandGuard(tmp_path / "g.json")
    assert (g.max_task_usd, g.max_total_usd, g.max_tasks) == (HARD_MAX_TASK_USD, HARD_MAX_TOTAL_USD, errands.HARD_MAX_TASKS)
    assert ErrandsClient(FakeTransport(), guard=g).min_balance == HARD_MIN_BALANCE_USD


def test_topup_response_releases_reservation_and_is_not_followed(tmp_path):
    c, t = make(tmp_path, FakeTransport(approve_outcome={"approved": False, "reason": "insufficient_balance", "topup_url": "https://example.com/pay"}))
    with pytest.raises(ErrandError, match="top-up is not done"):
        c.approve("t-1", "$5.00", operator=True)
    assert c.guard.snapshot() == {**c.guard.snapshot(), "spent_usd": 0.0, "tasks_approved": 0}


def test_unknown_approve_outcome_keeps_reservation(tmp_path):
    class Flaky(FakeTransport):
        def call(self, tool, args):
            if tool == "approve_task":
                raise TimeoutError("network")
            return super().call(tool, args)

    c, _ = make(tmp_path, Flaky())
    with pytest.raises(TimeoutError):
        c.approve("t-1", "$5.00", operator=True)
    assert c.guard.snapshot()["spent_usd"] == 5.0  # may have been charged: stay conservative


# ---------------------------------------------------------------- MCP payload parsing
def text(t, **kw):
    return SimpleNamespace(content=[SimpleNamespace(text=t)], structuredContent=kw.get("structured"), isError=kw.get("error", False))


def test_payload_parsing_shapes():
    assert payload_from_result(text("", structured={"result": {"a": 1}})) == {"a": 1}
    assert payload_from_result(text('{"b": 2}')) == {"b": 2}
    assert payload_from_result(text('Tendem balance: $45.00. Result data:\n{"balance": {"amount": 45}}')) == {"balance": {"amount": 45}}
    assert payload_from_result(text("plain words")) == {"content": "plain words"}
    with pytest.raises(ErrandError):
        payload_from_result(text("boom", error=True))


def test_balance_from_text_fallback(tmp_path):
    class TextOnly(FakeTransport):
        def call(self, tool, args):
            return {"content": "Tendem balance: $41.50. Top up at ..."} if tool == "get_account" else super().call(tool, args)

    c, _ = make(tmp_path, TextOnly())
    assert c.balance() == 41.5
