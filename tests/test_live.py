"""Optional live checks against real APIs. Skipped unless the keys are set.

Run with:  pytest -m live
"""

import os

import pytest

from tastebound.config import Settings

pytestmark = pytest.mark.live
settings = Settings.from_env()


@pytest.mark.skipif(not settings.llm_enabled, reason="NEBIUS_API_KEY not set")
def test_nemotron_intent():
    from tastebound.intent import SYSTEM_PROMPT, extract_intent
    from tastebound.llm import NemotronClient

    client = NemotronClient(settings)
    # Call the model directly so the keyword fallback cannot mask an empty reply
    raw = client.complete_json(SYSTEM_PROMPT, "huge Radiohead fan; cheap-ish but not painfully slow")
    assert "Radiohead" in raw.get("tastes", [])
    intent = extract_intent("cheapest but not too slow, and I love Radiohead", client)
    assert intent.get("preference") in {"balanced", "save money"}


@pytest.mark.skipif(not settings.qloo_enabled, reason="QLOO_API_KEY not set")
def test_qloo_places_near_manchester():
    from tastebound.taste import QlooClient

    client = QlooClient(settings.qloo_api_key, settings.qloo_base_url)
    entities = client.resolve_tastes(["Oasis"])
    assert client.places_near(53.4808, -2.2426, [e.entity_id for e in entities]) is not None


@pytest.mark.skipif(not settings.tavily_enabled, reason="TAVILY_API_KEY not set")
def test_tavily_search():
    from tastebound.live_info import TavilyClient

    assert "results" in TavilyClient(settings.tavily_api_key).search("UK rail strikes this week")


@pytest.mark.skipif(not os.environ.get("TASTEBOUND_LIVE_GEOCODE"), reason="set TASTEBOUND_LIVE_GEOCODE=1")
def test_nominatim():
    from tastebound.geocode import Geocoder

    assert Geocoder(settings.nominatim_user_agent).lookup("Manchester Piccadilly") is not None


@pytest.mark.skipif(not settings.youcam_enabled, reason="YOUCAM_API_KEY not set")
def test_youcam_free_calls_only():
    """Free endpoints only (balance, template list): the live suite never spends units."""
    from tastebound.style import YouCamClient

    client = YouCamClient(settings.youcam_api_key)
    assert client.balance() >= 0
    assert client.list_garments(page_size=3)
