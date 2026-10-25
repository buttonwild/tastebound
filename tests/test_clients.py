"""HTTP client tests with a fake session (synthetic payloads only)."""

import pytest
import requests

from tastebound.config import Settings
from tastebound.geocode import Geocoder
from tastebound.live_info import TavilyClient, TavilyError
from tastebound.llm import NemotronClient, parse_json_object
from tastebound.taste import QlooClient, QlooError


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = list(responses)
        self.requests = []

    def _next(self, method, url, **kw):
        self.requests.append((method, url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    def get(self, url, **kw):
        return self._next("GET", url, **kw)

    def post(self, url, **kw):
        return self._next("POST", url, **kw)


def test_qloo_places_near_builds_documented_request():
    session = FakeSession([FakeResponse({"results": {"entities": [{
        "name": "Synthetic Diner", "entity_id": "x1",
        "properties": {"address": "1 Test St", "business_rating": 4.5, "price_level": 2},
        "query": {"affinity": 0.91}, "tags": [{"name": "Brunch"}, {"name": "Vegan"}],
    }]}})])
    client = QlooClient("k", "https://hackathon.api.qloo.com/", session=session)
    places = client.places_near(53.48, -2.24, ["e1", "e2"], ["urn:tag:t"])
    method, url, kw = session.requests[0]
    assert (method, url) == ("GET", "https://hackathon.api.qloo.com/v2/insights")
    assert session.headers["X-Api-Key"] == "k"
    assert kw["params"]["filter.type"] == "urn:entity:place"
    assert kw["params"]["filter.location"] == "POINT(-2.24 53.48)"
    assert kw["params"]["signal.interests.entities"] == "e1,e2"
    assert kw["params"]["filter.tags"] == "urn:tag:t"
    assert places[0].name == "Synthetic Diner" and places[0].affinity == 0.91 and places[0].tags == ("Brunch", "Vegan")


def test_qloo_without_signals_uses_popularity():
    session = FakeSession([FakeResponse({"results": {"entities": []}})])
    QlooClient("k", "https://h", session=session).places_near(1, 2, [])
    assert session.requests[0][2]["params"]["filter.popularity.min"] == 0.7


def test_qloo_search_and_tags_accept_both_shapes():
    session = FakeSession([
        FakeResponse({"results": [{"entity_id": "a1", "name": "Synthetic Band", "types": ["urn:entity:artist"]}]}),
        FakeResponse({"results": {"entities": [{"entity_id": "a2", "name": "Other"}]}}),
        FakeResponse({"results": {"tags": [{"id": "urn:tag:x"}, {"name": "no id"}]}}),
    ])
    client = QlooClient("k", "https://h", session=session)
    assert client.search_entity("band").entity_id == "a1"
    assert client.search_entity("other").entity_id == "a2"
    assert client.search_tags("brunch") == ["urn:tag:x"]


def test_qloo_errors():
    session = FakeSession([FakeResponse({}, 401), requests.ConnectionError(), FakeResponse({}, 500)])
    client = QlooClient("k", "https://h", session=session)
    with pytest.raises(QlooError):
        client.search_entity("x")
    with pytest.raises(QlooError):
        client.places_near(1, 2, ["e"])
    assert client.resolve_tastes(["x"]) == []  # resolve swallows per-taste failures


def test_tavily_request_and_errors():
    session = FakeSession([
        FakeResponse({"answer": "ok", "results": [{"title": "T", "url": "https://e", "content": "c" * 500}]}),
        FakeResponse({}, 429),
    ])
    client = TavilyClient("secret", session=session)
    out = client.search("strikes")
    assert session.headers["Authorization"] == "Bearer secret"
    assert session.requests[0][2]["json"]["query"] == "strikes"
    assert session.requests[0][2]["json"]["time_range"] == "week"
    assert len(out["results"][0]["snippet"]) == 300
    with pytest.raises(TavilyError):
        client.search("again")


def test_geocoder_parses_caches_and_survives_errors():
    session = FakeSession([
        requests.Timeout(),
        FakeResponse([{"display_name": "York, England", "lat": "53.96", "lon": "-1.08", "address": {"city": "York"}}]),
    ])
    geo = Geocoder("test-agent", session=session)
    assert geo.lookup("York") is None  # transient failure is not cached
    place = geo.lookup("York")
    assert place.city == "York" and place.coords == (53.96, -1.08)
    assert geo.lookup(" york ") is place  # cached
    assert session.requests[1][2]["params"]["countrycodes"] == "gb"
    assert session.headers["User-Agent"] == "test-agent"


def test_parse_json_object_handles_noise():
    assert parse_json_object('<think>x</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! {"a": 2} hope that helps') == {"a": 2}
    assert parse_json_object("[1, 2]") == {}
    assert parse_json_object(None) == {}


def test_nemotron_client_routes_models():
    calls = []

    class Completions:
        def create(self, **kw):
            calls.append(kw)
            from types import SimpleNamespace
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"x": true}', tool_calls=None))])

    from types import SimpleNamespace
    fake = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    settings = Settings(nebius_api_key="k", fast_model="fast-m", reasoning_model="reason-m")
    client = NemotronClient(settings, client=fake)
    assert client.complete_json("s", "u") == {"x": True}
    client.chat_with_tools([], [{"type": "function"}])
    client.chat_with_tools([], [])
    assert [c["model"] for c in calls] == ["fast-m", "reason-m", "reason-m"]
    assert "tools" in calls[1] and "tools" not in calls[2]


def test_settings_from_env_and_repr_hides_keys(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "super-secret-value")
    monkeypatch.setenv("TASTEBOUND_FAST_MODEL", "custom/model")
    monkeypatch.delenv("QLOO_API_KEY", raising=False)
    s = Settings.from_env()
    assert s.llm_enabled and not s.qloo_enabled and s.fast_model == "custom/model"
    assert "super-secret-value" not in repr(s)


def test_clean_text_rewrites_model_citation_markers():
    from tastebound.llm import clean_text

    raw = 'Minster【{"cursor": 0, "loc": 0}】. Works on Sunday【https://example.com/a】.'
    assert clean_text(raw) == "Minster. Works on Sunday ([source](https://example.com/a))."
