import json
from types import SimpleNamespace

import pytest

from tastebound.budget import HARD_CAP_USD, SAFETY_FACTOR, BudgetExceeded, SpendGuard, cost_usd
from tastebound.config import Settings
from tastebound.limits import QuotaExceeded
from tastebound.llm import NemotronClient

SUPER = "nvidia/nemotron-3-super-120b-a12b"


def usage(p, c):
    return SimpleNamespace(prompt_tokens=p, completion_tokens=c)


def test_cost_uses_price_table_with_safety_factor():
    assert cost_usd(SUPER, 1_000_000, 1_000_000) == pytest.approx((0.30 + 0.90) * SAFETY_FACTOR)
    assert cost_usd("some/unknown", 1_000_000, 0) == pytest.approx(3.0 * SAFETY_FACTOR)


def test_cap_cannot_exceed_hard_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("TASTEBOUND_NEBIUS_BUDGET_USD", "1000")
    assert SpendGuard(tmp_path / "l.json").cap_usd == HARD_CAP_USD
    monkeypatch.setenv("TASTEBOUND_NEBIUS_BUDGET_USD", "5")
    assert SpendGuard(tmp_path / "l.json").cap_usd == 5


def test_reserve_then_settle_records_actual_usage(tmp_path):
    guard = SpendGuard(tmp_path / "l.json", cap_usd=1)
    reserved = guard.reserve(SUPER, {"messages": ["hi"]}, max_tokens=1000)
    assert guard.snapshot()["spent_usd"] == pytest.approx(reserved)
    guard.settle(SUPER, reserved, usage(100, 50))
    snap = guard.snapshot()
    assert snap["spent_usd"] == pytest.approx(cost_usd(SUPER, 100, 50))
    assert (snap["calls"], snap["prompt_tokens"], snap["completion_tokens"]) == (1, 100, 50)


def test_missing_usage_keeps_worst_case_charge(tmp_path):
    guard = SpendGuard(tmp_path / "l.json", cap_usd=1)
    reserved = guard.reserve(SUPER, {}, 1000)
    guard.settle(SUPER, reserved, None)
    assert guard.snapshot()["spent_usd"] == pytest.approx(reserved)


def test_refuses_when_worst_case_would_exceed_cap(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(json.dumps({"spent_usd": 0.999}))
    guard = SpendGuard(path, cap_usd=1)
    with pytest.raises(BudgetExceeded):
        guard.reserve(SUPER, {}, max_tokens=10_000)
    snap = guard.snapshot()
    assert snap["refused"] == 1 and snap["spent_usd"] == pytest.approx(0.999)


def test_ledger_persists_across_instances(tmp_path):
    path = tmp_path / "l.json"
    a = SpendGuard(path, cap_usd=1)
    a.settle(SUPER, a.reserve(SUPER, {}, 100), usage(1000, 100))
    assert SpendGuard(path, cap_usd=1).snapshot()["spent_usd"] == pytest.approx(cost_usd(SUPER, 1000, 100))


def test_client_refuses_call_without_contacting_api(tmp_path):
    path = tmp_path / "l.json"
    path.write_text(json.dumps({"spent_usd": HARD_CAP_USD}))
    calls = []
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: calls.append(kw))))
    client = NemotronClient(Settings(nebius_api_key="k"), client=fake, guard=SpendGuard(path))
    with pytest.raises(BudgetExceeded):
        client.complete_json("s", "u")
    assert calls == []


def test_client_settles_with_reported_usage(tmp_path):
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok": true}'))], usage=usage(200, 20))
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: response)))
    guard = SpendGuard(tmp_path / "l.json")
    settings = Settings(nebius_api_key="k")
    assert NemotronClient(settings, client=fake, guard=guard).complete_json("s", "u") == {"ok": True}
    assert guard.snapshot()["spent_usd"] == pytest.approx(cost_usd(settings.fast_model, 200, 20))


def test_budget_exceeded_degrades_to_keyword_intent(tmp_path, geocoder):
    from tastebound.intent import extract_intent

    path = tmp_path / "l.json"
    path.write_text(json.dumps({"spent_usd": HARD_CAP_USD}))
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: 1 / 0)))
    client = NemotronClient(Settings(nebius_api_key="k"), client=fake, guard=SpendGuard(path))
    assert extract_intent("cheapest please", client)["preference"] == "save money"


def test_hourly_call_limit(tmp_path, monkeypatch):
    monkeypatch.setenv("TASTEBOUND_NEBIUS_MAX_PER_HOUR", "2")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="{}"))], usage=usage(10, 2))
    calls = []
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: calls.append(kw) or response)))
    client = NemotronClient(Settings(nebius_api_key="k"), client=fake, guard=SpendGuard(tmp_path / "l.json"))
    client.complete_json("s", "u")
    client.complete_json("s", "u")
    with pytest.raises(QuotaExceeded):
        client.complete_json("s", "u")
    assert len(calls) == 2
