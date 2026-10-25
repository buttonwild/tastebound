"""Thin wrapper around Nebius Token Factory's OpenAI-compatible API."""

from __future__ import annotations

import json
import re
from typing import Any

from .budget import SpendGuard
from .limits import UsageLimiter
from .config import Settings

_THINK_RE = re.compile(r"<think>.*?</think>", re.S)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.S)
# Nemotron Super sometimes emits internal citation markers: 【{"cursor": 0, "loc": 1}】 or 【https://...】
_CURSOR_CITE_RE = re.compile(r"【\{[^】]*\}】")
_URL_CITE_RE = re.compile(r"【(https?://[^】\s]+)】")


def clean_text(content: str | None) -> str:
    """Strip reasoning traces and code fences that some models emit."""
    if not content:
        return ""
    content = _THINK_RE.sub("", content).strip()
    content = _CURSOR_CITE_RE.sub("", content)
    content = _URL_CITE_RE.sub(r" ([source](\1))", content)
    return _FENCE_RE.sub("", content).strip()


def parse_json_object(content: str | None) -> dict:
    """Best-effort extraction of a single JSON object from model output."""
    text = clean_text(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {}
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


class NemotronClient:
    """Routes calls to a fast Nemotron model or a reasoning Nemotron model.

    Every call goes through a SpendGuard, which refuses calls that could push
    cumulative spend over the hard cap (raises budget.BudgetExceeded).
    """

    def __init__(
        self,
        settings: Settings,
        client: Any = None,
        guard: SpendGuard | None = None,
        limiter: UsageLimiter | None = None,
    ):
        self.settings = settings
        self.guard = guard or SpendGuard()
        # App-wide hourly/daily call limits: they bound worst-case spend even if a
        # free host wipes the spend ledger, and stop abuse spread over many sessions.
        self.limiter = limiter or UsageLimiter("nebius")
        if client is None:
            from openai import OpenAI

            client = OpenAI(
                base_url=settings.nebius_base_url,
                api_key=settings.nebius_api_key,
                timeout=settings.http_timeout * 3,  # 45s by default
                max_retries=0,  # retries would be unbudgeted repeat calls
            )
        self._client = client

    def _create(self, model: str, messages: list[dict], max_tokens: int, **kwargs):
        self.limiter.acquire()  # raises QuotaExceeded; nothing is sent
        reserved = self.guard.reserve(model, {"messages": messages, **kwargs}, max_tokens)
        response = self._client.chat.completions.create(
            model=model, messages=messages, max_tokens=max_tokens, **kwargs
        )
        self.guard.settle(model, reserved, getattr(response, "usage", None))
        return response

    def complete_json(self, system: str, user: str, max_tokens: int = 256) -> dict:
        # Thinking off: with it on, Nemotron Nano can spend the whole token budget
        # reasoning and return empty content. Extraction doesn't need it.
        response = self._create(
            self.settings.fast_model,
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens,
            temperature=0.0,
            extra_body={"chat_template_kwargs": {"enable_thinking": False}},
        )
        return parse_json_object(response.choices[0].message.content)

    def chat_with_tools(self, messages: list[dict], tools: list[dict], max_tokens: int = 4096):
        """One reasoning-model turn; returns the assistant message object."""
        kwargs = {"tools": tools, "tool_choice": "auto"} if tools else {}
        response = self._create(self.settings.reasoning_model, messages, max_tokens, temperature=0.3, **kwargs)
        return response.choices[0].message
