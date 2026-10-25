"""Conversation controller. UI-agnostic so it can be unit tested end to end.

Slot filling (start -> destination -> time -> preference -> tastes) is
deterministic; Nemotron handles language understanding and, once a trip is
planned, open-ended follow-ups through the tool-using concierge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from .concierge import Toolbox, TripContext, fallback_brief, run_concierge
from .config import Settings
from .formatting import format_all_routes, format_route
from .geocode import Geocoder
from .intent import extract_intent
from .limits import QuotaExceeded
from .live_info import TavilyClient
from .llm import NemotronClient
from .errands import ErrandsClient, MCPTransport
from .style import YouCamClient
from .taste import QlooClient
from .transport import choose_best_route, get_routes, route_by_mode

log = logging.getLogger(__name__)

GREETING = (
    "Hi 👋 I'm **Tastebound**. I plan UK journeys and find stops that match your taste.\n\n"
    "Where are you **starting from**? (a town, station or postcode)"
)
ASK_TIME = "When do you plan to set off? (e.g. *tomorrow at 9am*)"
ASK_PREFERENCE = "Do you want to **save time**, **save money**, or go **balanced**?"
ASK_TASTES = (
    "Last one 🎧 Tell me a few things you love (artists, films, books, food...) and I'll find "
    "places at your destination that fit. Or say **skip**."
)
HELP = (
    "You can say things like:\n\n"
    "• *Show all options*\n"
    "• *Cheapest* / *Fastest*\n"
    "• *Train option*\n"
    "• *Somewhere for brunch near my destination*\n"
    "• *Any strikes on this route?*\n"
    "• *Start over*"
)
LIMIT_NOTE = "ℹ️ *The AI concierge has hit its demo usage limit, so this is a simpler answer. Full answers return automatically later.*"
MAX_MESSAGE_CHARS = 500  # long inputs only burn tokens; trips need a sentence or two
SKIP_WORDS = {"skip", "no", "nope", "none", "nothing", "no thanks", "pass"}


@dataclass
class Services:
    geocoder: Geocoder
    llm: NemotronClient | None = None
    qloo: QlooClient | None = None
    tavily: TavilyClient | None = None
    youcam: YouCamClient | None = None
    errands: ErrandsClient | None = None

    @classmethod
    def from_settings(cls, settings: Settings) -> "Services":
        return cls(
            geocoder=Geocoder(settings.nominatim_user_agent, settings.http_timeout),
            llm=NemotronClient(settings) if settings.llm_enabled else None,
            qloo=QlooClient(settings.qloo_api_key, settings.qloo_base_url, settings.http_timeout)
            if settings.qloo_enabled
            else None,
            tavily=TavilyClient(settings.tavily_api_key, settings.http_timeout) if settings.tavily_enabled else None,
            youcam=YouCamClient(settings.youcam_api_key) if settings.youcam_enabled else None,
            errands=ErrandsClient(MCPTransport(settings.tendem_api_key)) if settings.tendem_enabled else None,
        )


@dataclass
class Reply:
    text: str
    trace: list[str] = field(default_factory=list)


class TripAgent:
    def __init__(self, services: Services):
        self.services = services
        self.ctx = TripContext()
        self.tastes_asked = False
        self.history: list[dict] = []

    @property
    def completed(self) -> bool:
        return self.ctx.chosen is not None

    def greeting(self) -> str:
        return GREETING

    def reset(self) -> None:
        self.ctx = TripContext()
        self.tastes_asked = False
        self.history = []

    def handle(self, message: str) -> Reply:
        message = message.strip()[:MAX_MESSAGE_CHARS]
        intent = extract_intent(message, self.services.llm)
        if intent.get("tastes"):
            self._add_tastes(intent["tastes"])

        if self.completed:
            reply = self._after_plan(message, intent)
        else:
            reply = self._fill_slots(message, intent)

        self.history += [{"role": "user", "content": message}, {"role": "assistant", "content": reply.text}]
        return reply

    # ------------------------------------------------------------------
    # Slot filling
    # ------------------------------------------------------------------
    def _fill_slots(self, message: str, intent: dict) -> Reply:
        ctx = self.ctx
        if ctx.start is None:
            place = self.services.geocoder.lookup(message)
            if not place:
                return Reply("I couldn't find that place in the UK 🤔 Could you try a town, station or postcode?")
            ctx.start = place
            return Reply(f"Got it 👍 Starting from **{place.name}**.\n\nWhere are you heading?")

        if ctx.destination is None:
            place = self.services.geocoder.lookup(message)
            if not place:
                return Reply("I couldn't find that destination 🤔 Please try again.")
            ctx.destination = place
            return Reply(f"Great! Destination: **{place.name}**.\n\n{ASK_TIME}")

        if ctx.start_time is None:
            if not intent.get("start_time"):
                return Reply(ASK_TIME)
            ctx.start_time = intent["start_time"]
            if intent.get("preference"):
                ctx.preference = intent["preference"]
            else:
                return Reply(f"Thanks ⏰\n\n{ASK_PREFERENCE}")

        if ctx.preference is None:
            if not intent.get("preference"):
                return Reply(ASK_PREFERENCE)
            ctx.preference = intent["preference"]

        if not ctx.tastes and not self.tastes_asked:
            self.tastes_asked = True
            return Reply(ASK_TASTES)

        if not ctx.tastes and intent.get("preference"):
            # "actually save money" in reply to the tastes question is a preference change
            ctx.preference = intent["preference"]
        elif not ctx.tastes and message.strip().lower() not in SKIP_WORDS:
            # Free-text answer to the tastes question that intent parsing missed
            self._add_tastes([t.strip() for t in message.split(",") if t.strip()])

        return self._plan()

    def _plan(self) -> Reply:
        ctx = self.ctx
        ctx.routes = get_routes(ctx.start.coords, ctx.destination.coords)
        ctx.chosen = choose_best_route(ctx.routes, ctx.preference)
        text = format_route(ctx.chosen, "✅ Recommended journey")

        toolbox = self._toolbox()
        extras = ""
        if self.services.llm is not None and (self.services.qloo or self.services.tavily):
            try:
                extras = run_concierge(
                    self.services.llm,
                    toolbox,
                    "Give me a short arrival brief: 2-3 places at my destination that fit my taste, "
                    "and any live disruption I should know about for this journey.",
                )
            except QuotaExceeded:
                extras = "\n\n".join(x for x in (fallback_brief(toolbox), LIMIT_NOTE) if x)
            except Exception as exc:
                log.warning("Concierge failed, using fallback: %s", type(exc).__name__)
                extras = fallback_brief(toolbox)
        else:
            extras = fallback_brief(toolbox)

        if extras:
            text += "\n\n" + extras
        text += "\n\nWant **other options**, a different preference, or ideas for your trip? Just ask."
        return Reply(text, toolbox.trace)

    # ------------------------------------------------------------------
    # After a plan exists
    # ------------------------------------------------------------------
    def _after_plan(self, message: str, intent: dict) -> Reply:
        ctx = self.ctx
        if intent.get("restart_trip"):
            self.reset()
            return Reply("No problem 👍 Let's plan a new trip.\n\nWhere are you **starting from**?")
        # Route commands are short ("cheapest", "train option"). Longer messages such as
        # "cheap brunch near the station" go to the concierge when an LLM is available.
        is_command = self.services.llm is None or len(message.split()) <= 5
        if not is_command:
            intent = {k: v for k, v in intent.items() if k == "tastes"}
        if intent.get("mode_request"):
            route = route_by_mode(ctx.routes, intent["mode_request"])
            if route:
                return Reply(format_route(route, f"🚏 {route.mode.capitalize()} option"))
            available = ", ".join(r.mode for r in ctx.routes)
            return Reply(f"A {intent['mode_request']} option isn't practical for this distance. Available: {available}.")
        # After mode_request: the LLM sometimes labels "any bus option?" as show-all
        if intent.get("show_all_options"):
            return Reply(format_all_routes(ctx.routes))
        if intent.get("preference") and not intent.get("tastes"):
            ctx.preference = intent["preference"]
            ctx.chosen = choose_best_route(ctx.routes, ctx.preference)
            return Reply(format_route(ctx.chosen, f"🔁 Best for *{ctx.preference}*"))
        if intent.get("acknowledgement"):
            return Reply("You're welcome 😊 Have a great trip! Ask me anything else about it.")

        # Open-ended request: hand over to the Nemotron concierge
        toolbox = self._toolbox()
        limited = False
        if self.services.llm is not None:
            try:
                return Reply(run_concierge(self.services.llm, toolbox, message, self.history), toolbox.trace)
            except QuotaExceeded:
                limited = True
            except Exception as exc:
                log.warning("Concierge failed: %s", type(exc).__name__)
        note = f"\n\n{LIMIT_NOTE}" if limited else ""
        if intent.get("tastes"):
            brief = fallback_brief(toolbox)
            if brief:
                return Reply(brief + note, toolbox.trace)
        return Reply(HELP + note)

    def _add_tastes(self, tastes: list[str]) -> None:
        for taste in tastes:
            if taste.lower() not in {t.lower() for t in self.ctx.tastes}:
                self.ctx.tastes.append(taste)

    def _toolbox(self) -> Toolbox:
        return Toolbox(self.ctx, self.services.qloo, self.services.tavily)
