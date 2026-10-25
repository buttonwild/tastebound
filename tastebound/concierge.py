"""Tool-using concierge agent powered by a Nemotron reasoning model.

Nemotron decides which tools to call (Qloo taste places, Tavily live info,
route options), we execute them, and it writes an answer grounded only in
the tool results. When Token Factory is not configured, `fallback_brief`
produces a deterministic, template-based version of the same plan.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from .geocode import Place
from .live_info import TavilyClient, TavilyError
from .llm import clean_text
from .taste import QlooClient, QlooError
from .transport import Route

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 4

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "find_taste_places",
            "description": (
                "Find real places (restaurants, bars, cafes, venues, shops, attractions) near the trip's "
                "start or destination that match the traveller's cultural taste, using Qloo's taste graph."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "where": {"type": "string", "enum": ["start", "destination"]},
                    "tastes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Artists, films, books, brands or places the traveller loves.",
                    },
                    "vibe": {
                        "type": "string",
                        "description": "Optional kind of place, e.g. 'brunch', 'cocktail bar', 'record store'.",
                    },
                },
                "required": ["where"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_live_travel_info",
            "description": (
                "Search the live web for UK travel disruptions, strikes, engineering works, weather or "
                "events relevant to this journey."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_route_options",
            "description": "List the estimated journey options (mode, duration, cost) for the current trip.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

SYSTEM_PROMPT = """You are Tastebound, a UK travel concierge.
You help a traveller with one planned trip. Use tools to ground every specific claim:
- find_taste_places for any recommendation of where to eat, drink, go or shop
- search_live_travel_info for disruptions, strikes, weather or events
- get_route_options for journey modes, times and costs
Never invent place names, disruptions or prices that did not come from a tool.
Journey times and fares are estimates; say so when quoting them.
Explain briefly WHY a place fits the traveller's taste when the tool gives tags.
When you use search_live_travel_info, cite the sources you rely on as markdown
links using only URLs the tool returned.
Keep answers short and skimmable (markdown bullets). If a tool returns an error
or nothing, say so plainly and suggest an alternative."""


@dataclass
class TripContext:
    start: Place | None = None
    destination: Place | None = None
    start_time: str | None = None
    preference: str | None = None
    tastes: list[str] = field(default_factory=list)
    routes: list[Route] = field(default_factory=list)
    chosen: Route | None = None

    def summary(self) -> str:
        parts = [
            f"Start: {self.start.name if self.start else 'unknown'}",
            f"Destination: {self.destination.name if self.destination else 'unknown'}",
            f"Leaving: {self.start_time or 'unspecified'}",
            f"Preference: {self.preference or 'unspecified'}",
            f"Known tastes: {', '.join(self.tastes) if self.tastes else 'none yet'}",
        ]
        if self.chosen:
            parts.append(f"Recommended journey: {json.dumps(self.chosen.to_dict())}")
        return "\n".join(parts)


class Toolbox:
    """Executes concierge tools against the configured services."""

    def __init__(self, ctx: TripContext, qloo: QlooClient | None, tavily: TavilyClient | None):
        self.ctx = ctx
        self.qloo = qloo
        self.tavily = tavily
        self.trace: list[str] = []
        self.sources: list[dict] = []  # web sources seen this turn, for verifiable answers
        self._place_cache: dict[tuple, dict] = {}  # lets the fallback reuse concierge lookups

    def run(self, name: str, args: dict) -> dict:
        handler = {
            "find_taste_places": self.find_taste_places,
            "search_live_travel_info": self.search_live_travel_info,
            "get_route_options": self.get_route_options,
        }.get(name)
        if handler is None:
            return {"error": f"Unknown tool {name}"}
        try:
            result = handler(**args)
        except TypeError as exc:
            result = {"error": f"Bad arguments: {exc}"}
        self.trace.append(f"{name}({json.dumps(args, ensure_ascii=False)})")
        return result

    def find_taste_places(self, where: str = "destination", tastes: list[str] | None = None, vibe: str | None = None) -> dict:
        if self.qloo is None:
            return {"error": "Qloo is not configured"}
        place = self.ctx.start if where == "start" else self.ctx.destination
        if place is None:
            return {"error": f"The trip {where} is not known yet"}
        tastes = tastes or self.ctx.tastes
        key = (where, tuple(t.lower() for t in tastes), (vibe or "").lower())
        if key in self._place_cache:
            return self._place_cache[key]
        try:
            entities = self.qloo.resolve_tastes(tastes)
            tag_ids = self.qloo.search_tags(vibe) if vibe else []
            places = self.qloo.places_near(place.lat, place.lon, [e.entity_id for e in entities], tag_ids)
        except QlooError as exc:
            return {"error": str(exc)}
        result = {
            "near": place.name,
            "matched_tastes": [e.name for e in entities],
            "places": [p.to_dict() for p in places],
        }
        self._place_cache[key] = result
        return result

    def search_live_travel_info(self, query: str) -> dict:
        if self.tavily is None:
            return {"error": "Tavily is not configured"}
        try:
            result = self.tavily.search(query)
        except TavilyError as exc:
            return {"error": str(exc)}
        for r in result.get("results", []):
            if r.get("url") and r["url"] not in {s["url"] for s in self.sources}:
                self.sources.append({"title": r.get("title") or r["url"], "url": r["url"]})
        return result

    def get_route_options(self) -> dict:
        return {"routes": [r.to_dict() for r in self.ctx.routes]}


def with_sources(answer: str, sources: list[dict], limit: int = 4) -> str:
    """Append the web sources when the model cited none of them, so claims are checkable."""
    if not sources or any(s["url"] in answer for s in sources):
        return answer
    links = "\n".join(f"- [{s['title']}]({s['url']})" for s in sources[:limit])
    return f"{answer}\n\n**Sources**\n{links}"


def run_concierge(llm, toolbox: Toolbox, request: str, history: list[dict] | None = None) -> str:
    """Run the Nemotron tool loop and return the final markdown answer."""
    return with_sources(_tool_loop(llm, toolbox, request, history), toolbox.sources)


def _tool_loop(llm, toolbox: Toolbox, request: str, history: list[dict] | None) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": "Current trip:\n" + toolbox.ctx.summary()},
        *(history or [])[-6:],
        {"role": "user", "content": request},
    ]
    for _ in range(MAX_TOOL_ROUNDS):
        msg = llm.chat_with_tools(messages, TOOLS)
        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            return clean_text(msg.content) or "Sorry, I couldn't come up with an answer."
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )
        for tc in tool_calls:
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = toolbox.run(tc.function.name, args if isinstance(args, dict) else {})
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result, ensure_ascii=False)})

    # Out of tool rounds: ask for a final answer without tools
    messages.append({"role": "user", "content": "Answer now using the tool results you already have."})
    msg = llm.chat_with_tools(messages, [])
    return clean_text(msg.content) or "Sorry, I couldn't finish that request."


def fallback_brief(toolbox: Toolbox) -> str:
    """Deterministic taste + live-info brief used when no LLM is configured."""
    ctx = toolbox.ctx
    sections = []
    if toolbox.qloo is not None and ctx.destination is not None:
        result = toolbox.run("find_taste_places", {"where": "destination"})
        places = result.get("places") or []
        if places:
            heading = "🎯 **Picked for your taste near your destination**"
            if result.get("matched_tastes"):
                heading += f" (because you like {', '.join(result['matched_tastes'])})"
            lines = [heading]
            for p in places[:3]:
                why = f" — {', '.join(p['tags'][:3])}" if p.get("tags") else ""
                lines.append(f"- **{p['name']}**{why}")
            sections.append("\n".join(lines))
    if toolbox.tavily is not None and ctx.start and ctx.destination:
        query = f"UK travel disruption {ctx.start.city or ''} to {ctx.destination.city or ''}".strip()
        result = toolbox.run("search_live_travel_info", {"query": query})
        if result.get("results"):
            top = result["results"][0]
            sections.append(f"📰 **Live travel news:** [{top['title']}]({top['url']})")
    return "\n\n".join(sections)
