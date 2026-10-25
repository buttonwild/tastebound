import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tastebound.geocode import Place  # noqa: E402

# Synthetic coordinates only; no third-party API responses are stored in this repo.
PLACES = {
    "westminster": Place("Westminster, London", 51.5014, -0.1419, "London"),
    "bank": Place("Bank, City of London", 51.5079, -0.0877, "London"),
    "trafalgar": Place("Trafalgar Square, London", 51.5080, -0.1281, "London"),
    "manchester": Place("Manchester", 53.4808, -2.2426, "Manchester"),
    "edinburgh": Place("Edinburgh", 55.9533, -3.1883, "Edinburgh"),
}


class FakeGeocoder:
    def lookup(self, query):
        return PLACES.get(query.strip().lower())


def tool_call(call_id, name, args):
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


class FakeLLM:
    """Scripted stand-in for NemotronClient."""

    def __init__(self, intents=None, turns=None):
        self.intents = intents or {}
        self.turns = list(turns or [])
        self.seen_messages = []

    def complete_json(self, system, user, max_tokens=512):
        return self.intents.get(user, {})

    def chat_with_tools(self, messages, tools, max_tokens=1024):
        self.seen_messages.append(list(messages))
        return self.turns.pop(0)


class FakeQloo:
    def __init__(self):
        self.calls = []

    def resolve_tastes(self, tastes):
        from tastebound.taste import TasteEntity

        self.calls.append(("resolve", tuple(tastes)))
        return [TasteEntity(f"id-{i}", t) for i, t in enumerate(tastes)]

    def search_tags(self, query, take=3):
        self.calls.append(("tags", query))
        return ["urn:tag:test:" + query.replace(" ", "_")]

    def places_near(self, lat, lon, entity_ids, tag_ids=None, radius_m=3000, take=5):
        from tastebound.taste import TastePlace

        self.calls.append(("places", round(lat, 2), tuple(entity_ids), tuple(tag_ids or ())))
        return [TastePlace(name="Test Cafe One", entity_id="p1", tags=("indie", "coffee")),
                TastePlace(name="Test Bar Two", entity_id="p2", tags=("live music",))]


class FakeTavily:
    def __init__(self):
        self.queries = []

    def search(self, query, max_results=4, topic="news"):
        self.queries.append(query)
        return {"answer": "No major disruption.", "results": [{"title": "Test headline", "url": "https://example.com/a", "snippet": ""}]}


@pytest.fixture
def geocoder():
    return FakeGeocoder()


@pytest.fixture(autouse=True)
def isolated_spend_ledger(request, tmp_path, monkeypatch):
    """Offline tests must never touch the real Nebius spend ledger."""
    if request.node.get_closest_marker("live") is None:
        monkeypatch.setenv("TASTEBOUND_SPEND_LEDGER", str(tmp_path / "spend.json"))
        monkeypatch.setenv("TASTEBOUND_LIMITS_LEDGER", str(tmp_path / "limits.json"))
        monkeypatch.setenv("TASTEBOUND_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("TASTEBOUND_YOUCAM_LEDGER", str(tmp_path / "youcam.json"))
        monkeypatch.setenv("TASTEBOUND_TENDEM_LEDGER", str(tmp_path / "tendem.json"))
        monkeypatch.setenv("TASTEBOUND_OPERATOR_LEDGER", str(tmp_path / "operator.json"))
        monkeypatch.delenv("TASTEBOUND_TENDEM_LIVE", raising=False)
