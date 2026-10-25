"""Tavily live web search for travel disruptions, events and local tips."""

from __future__ import annotations

import logging

import requests

from .limits import QuotaExceeded, UsageLimiter

log = logging.getLogger(__name__)

TAVILY_URL = "https://api.tavily.com/search"
CREDITS_PER_SEARCH = {"basic": 1, "advanced": 2}


class TavilyError(RuntimeError):
    pass


class TavilyClient:
    def __init__(
        self,
        api_key: str,
        timeout: float = 15.0,
        session: requests.Session | None = None,
        limiter: UsageLimiter | None = None,
    ):
        self._limiter = limiter or UsageLimiter("tavily")  # units are Tavily credits
        self._timeout = timeout
        self._session = session or requests.Session()
        self._session.headers["Authorization"] = f"Bearer {api_key}"

    def search(self, query: str, max_results: int = 4, topic: str = "news", time_range: str | None = "week") -> dict:
        payload = {
            "query": query,
            "topic": topic,
            "search_depth": "basic",  # 1 Tavily credit per search
            "max_results": max_results,
            "include_answer": True,
        }
        if time_range:
            # Travel disruption news goes stale fast; old articles mislead
            payload["time_range"] = time_range
        try:
            self._limiter.acquire(CREDITS_PER_SEARCH["basic"])
        except QuotaExceeded as exc:
            raise TavilyError(f"Live search paused: {exc}") from exc
        try:
            resp = self._session.post(TAVILY_URL, json=payload, timeout=self._timeout)
        except requests.RequestException as exc:
            raise TavilyError(f"Tavily request failed: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise TavilyError(f"Tavily returned HTTP {resp.status_code}")
        data = resp.json()
        return {
            "answer": data.get("answer"),
            "results": [
                {"title": r.get("title"), "url": r.get("url"), "snippet": (r.get("content") or "")[:300]}
                for r in data.get("results", [])
            ],
        }
