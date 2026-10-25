"""App-wide usage limits: they must hold across sessions, instances and restarts."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from conftest import FakeQloo, FakeTavily
from tastebound.agent import LIMIT_NOTE, MAX_MESSAGE_CHARS, Services, TripAgent
from tastebound.limits import DEFAULTS, LimitSpec, QuotaExceeded, UsageLimiter, spec_for
from tastebound.live_info import TavilyClient, TavilyError
from tastebound.taste import QlooClient, QlooError


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 24, 10, 30, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


def test_hourly_daily_and_total_windows(tmp_path):
    clock = Clock()
    lim = UsageLimiter("tavily", LimitSpec(per_hour=2, per_day=3, total=4), tmp_path / "l.json", clock)
    lim.acquire(); lim.acquire()
    with pytest.raises(QuotaExceeded, match="hourly"):
        lim.acquire()
    clock.now += timedelta(hours=1)
    lim.acquire()
    with pytest.raises(QuotaExceeded, match="daily"):
        lim.acquire()
    clock.now += timedelta(days=1)
    lim.acquire()
    with pytest.raises(QuotaExceeded, match="cumulative"):
        lim.acquire()  # total never resets
    assert lim.snapshot()["refused"] == 3


def test_limit_is_shared_by_every_instance(tmp_path):
    """New browser sessions build new clients; the ledger is still shared."""
    path = tmp_path / "l.json"
    spec = LimitSpec(per_hour=3, per_day=100)
    for _ in range(3):
        UsageLimiter("qloo", spec, path).acquire()
    with pytest.raises(QuotaExceeded):
        UsageLimiter("qloo", spec, path).acquire()
    UsageLimiter("nebius", spec, path).acquire()  # services are counted separately


def test_env_can_lower_but_not_raise_limits(monkeypatch):
    monkeypatch.setenv("TASTEBOUND_TAVILY_MAX_TOTAL", "999999")
    monkeypatch.setenv("TASTEBOUND_TAVILY_MAX_PER_DAY", "10")
    spec = spec_for("tavily")
    assert spec.total == DEFAULTS["tavily"]["total"][1] == 1000
    assert spec.per_day == 10


def test_multi_unit_reservation_for_credits(tmp_path):
    lim = UsageLimiter("tavily", LimitSpec(per_hour=10, per_day=10, total=3), tmp_path / "l.json")
    lim.acquire(2)
    with pytest.raises(QuotaExceeded):
        lim.acquire(2)
    lim.acquire(1)


class Session:
    def __init__(self):
        self.headers, self.calls = {}, 0

    def post(self, *a, **k):
        self.calls += 1
        return SimpleNamespace(status_code=200, json=lambda: {"results": []})

    get = post


def test_tavily_and_qloo_stop_before_sending(tmp_path):
    path = tmp_path / "l.json"
    tav_session, qloo_session = Session(), Session()
    tavily = TavilyClient("k", session=tav_session, limiter=UsageLimiter("tavily", LimitSpec(1, 1, 1), path))
    qloo = QlooClient("k", "https://h", session=qloo_session, limiter=UsageLimiter("qloo", LimitSpec(1, 1), path))
    tavily.search("a")
    qloo.search_entity("a")
    with pytest.raises(TavilyError, match="paused"):
        tavily.search("b")
    with pytest.raises(QlooError, match="paused"):
        qloo.search_entity("b")
    assert (tav_session.calls, qloo_session.calls) == (1, 1)


def test_agent_degrades_with_note_when_llm_is_limited(geocoder):
    class LimitedLLM:
        def complete_json(self, *a, **k):
            raise QuotaExceeded("nebius hourly limit reached")

        def chat_with_tools(self, *a, **k):
            raise QuotaExceeded("nebius hourly limit reached")

    agent = TripAgent(Services(geocoder=geocoder, llm=LimitedLLM(), qloo=FakeQloo(), tavily=FakeTavily()))
    for m in ["Westminster", "Manchester", "now", "balanced"]:
        agent.handle(m)
    final = agent.handle("I love jazz")
    assert "Recommended journey" in final.text and "Test Cafe One" in final.text
    assert LIMIT_NOTE in final.text
    assert LIMIT_NOTE in agent.handle("what museums are worth seeing near there?").text


def test_long_messages_are_truncated(geocoder):
    seen = []

    class SpyLLM:
        def complete_json(self, system, user, max_tokens=256):
            seen.append(user)
            return {}

    TripAgent(Services(geocoder=geocoder, llm=SpyLLM())).handle("x" * 5000)
    assert len(seen[0]) == MAX_MESSAGE_CHARS
