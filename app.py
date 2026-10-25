"""Streamlit chat UI for Tastebound. All logic lives in the tastebound package."""

import os

import streamlit as st

from tastebound.agent import Services, TripAgent
from tastebound.config import Settings
from tastebound.limits import UsageLimiter
from tastebound.errands import OperatorGate
from tastebound.operator_panel import render_operator_panel
from tastebound.style_panel import render_style_panel

st.set_page_config(page_title="Tastebound: taste-aware UK trip agent", page_icon="🧭", layout="centered")


def _load_streamlit_secrets() -> None:
    """Let hosted deployments supply keys via st.secrets instead of env vars."""
    try:
        secrets = dict(st.secrets)
    except Exception:  # no secrets.toml present
        return
    for key, value in secrets.items():
        # TOML numbers (e.g. TASTEBOUND_NEBIUS_BUDGET_USD = 10) must not be skipped
        if isinstance(value, (str, int, float)) and not isinstance(value, bool) and not os.environ.get(key):
            os.environ[key] = str(value)


@st.cache_resource
def get_services() -> tuple[Services, Settings]:
    _load_streamlit_secrets()
    settings = Settings.from_env()
    return Services.from_settings(settings), settings


services, settings = get_services()

if "agent" not in st.session_state:
    st.session_state.agent = TripAgent(services)
    st.session_state.messages = [{"role": "assistant", "content": st.session_state.agent.greeting()}]

agent: TripAgent = st.session_state.agent

with st.sidebar:
    st.header("🧭 Tastebound")
    st.caption("Taste-aware UK trip agent")
    st.markdown(
        "\n".join(
            [
                f"- {'🟢' if settings.llm_enabled else '⚪'} NVIDIA Nemotron via Nebius Token Factory",
                f"- {'🟢' if settings.qloo_enabled else '⚪'} Qloo Taste AI",
                f"- {'🟢' if settings.tavily_enabled else '⚪'} Tavily live search",
                f"- {'🟢' if settings.youcam_enabled else '⚪'} YouCam try-on & Skin AI",
                "- 🟢 OpenStreetMap geocoding",
            ]
        )
    )
    if settings.llm_enabled:
        st.caption(f"Fast: `{settings.fast_model}`  \nReasoning: `{settings.reasoning_model}`")
        budget = services.llm.guard.snapshot()
        st.caption(f"Nebius budget: ${budget['spent_usd']:.4f} of ${budget['cap_usd']:g} used (hard cap)")
        if budget["remaining_usd"] < 0.01:
            st.warning("Nebius budget cap reached. Running in offline fallback mode.")
    with st.expander("Usage limits (app-wide)"):
        for name, enabled in (("nebius", settings.llm_enabled), ("tavily", settings.tavily_enabled), ("qloo", settings.qloo_enabled), ("youcam", settings.youcam_enabled)):
            if enabled:
                u = UsageLimiter(name).snapshot()
                line = f"**{name}**: {u['hour_used']}/{u['per_hour']} this hour · {u['day_used']}/{u['per_day']} today"
                if u["total"] is not None:
                    line += f" · {u['total_used']}/{u['total']} total"
                st.caption(line)
        if services.youcam is not None:
            g = services.youcam.guard.snapshot()
            st.caption(f"**youcam units**: {g['units_used']}/{g['max_units']} used (balance floor {services.youcam.min_balance:.0f})")
    if agent.ctx.tastes:
        st.markdown("**Your tastes:** " + ", ".join(agent.ctx.tastes))
    if st.button("🔄 New trip"):
        agent.reset()
        st.session_state.messages = [{"role": "assistant", "content": agent.greeting()}]
        st.rerun()
    st.caption("Journey times and fares are estimates, not live timetables.")

render_operator_panel(OperatorGate(), services.youcam, services.errands)

st.markdown("<h1 style='text-align:center;'>🧭 Tastebound</h1>", unsafe_allow_html=True)

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])
        if msg.get("trace"):
            with st.expander("🛠️ Agent tool calls"):
                st.code("\n".join(msg["trace"]), language="text")

render_style_panel(services.youcam, agent.ctx, services.tavily)

MAX_TURNS_PER_SESSION = int(os.environ.get("TASTEBOUND_MAX_TURNS_PER_SESSION", "40"))
turns_used = sum(1 for m in st.session_state.messages if m["role"] == "user")

if turns_used >= MAX_TURNS_PER_SESSION:
    st.info("This demo session has reached its message limit. Click **🔄 New trip** in the sidebar to start again.")
    user_input = None
else:
    user_input = st.chat_input("Type your message...")
if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user"):
        st.markdown(user_input)
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            reply = agent.handle(user_input)
        st.markdown(reply.text)
    st.session_state.messages.append({"role": "assistant", "content": reply.text, "trace": reply.trace})
    st.rerun()
