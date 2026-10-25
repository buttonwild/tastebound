"""Runtime configuration.

All secrets come from environment variables (or Streamlit secrets, which the
app copies into the environment). Nothing secret is ever hard-coded here.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or default


@dataclass(frozen=True)
class Settings:
    # Nebius Token Factory (OpenAI-compatible) serving NVIDIA Nemotron models
    nebius_api_key: str | None = field(default=None, repr=False)
    nebius_base_url: str = "https://api.tokenfactory.nebius.com/v1/"
    # Fast model: per-turn intent parsing. Reasoning model: concierge agent.
    fast_model: str = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    reasoning_model: str = "nvidia/nemotron-3-super-120b-a12b"

    # Qloo Taste AI (hackathon keys only work against the hackathon host)
    qloo_api_key: str | None = field(default=None, repr=False)
    qloo_base_url: str = "https://hackathon.api.qloo.com"

    # Tavily live web search
    tavily_api_key: str | None = field(default=None, repr=False)

    # YouCam (Perfect Corp.) v2 API: Bearer API key only. YOUCAM_SECRET_KEY is
    # for the legacy v1 auth flow and is deliberately never read.
    youcam_api_key: str | None = field(default=None, repr=False)

    # Tendem human errands (hosted MCP server, API-key auth)
    tendem_api_key: str | None = field(default=None, repr=False)

    # Nominatim's usage policy requires an identifying User-Agent
    nominatim_user_agent: str = "tastebound/0.2 (+https://github.com/)"
    http_timeout: float = 15.0

    @classmethod
    def from_env(cls) -> "Settings":
        defaults = cls()
        return cls(
            nebius_api_key=_env("NEBIUS_API_KEY"),
            nebius_base_url=_env("NEBIUS_BASE_URL", defaults.nebius_base_url),
            fast_model=_env("TASTEBOUND_FAST_MODEL", defaults.fast_model),
            reasoning_model=_env("TASTEBOUND_REASONING_MODEL", defaults.reasoning_model),
            qloo_api_key=_env("QLOO_API_KEY"),
            qloo_base_url=_env("QLOO_API_URL", _env("QLOO_BASE_URL", defaults.qloo_base_url)),
            tavily_api_key=_env("TAVILY_API_KEY"),
            youcam_api_key=_env("YOUCAM_API_KEY"),
            tendem_api_key=_env("TENDEM_API_KEY"),
            nominatim_user_agent=_env("NOMINATIM_USER_AGENT", defaults.nominatim_user_agent),
            http_timeout=float(_env("TASTEBOUND_HTTP_TIMEOUT", str(defaults.http_timeout))),
        )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.nebius_api_key)

    @property
    def qloo_enabled(self) -> bool:
        return bool(self.qloo_api_key)

    @property
    def tavily_enabled(self) -> bool:
        return bool(self.tavily_api_key)

    @property
    def youcam_enabled(self) -> bool:
        return bool(self.youcam_api_key)

    @property
    def tendem_enabled(self) -> bool:
        return bool(self.tendem_api_key)
