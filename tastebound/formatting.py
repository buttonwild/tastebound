"""Markdown rendering for journey options."""

from __future__ import annotations

from .transport import Route


def format_route(route: Route, title: str) -> str:
    text = (
        f"**{title}**\n\n"
        f"- **Mode:** {route.mode.capitalize()}\n"
        f"- **Distance:** {route.distance_km} km\n"
        f"- **Duration:** ~{route.duration_min:.0f} minutes\n"
        f"- **Estimated cost:** £{route.cost_gbp:.2f}"
    )
    if route.note:
        text += f"\n\nℹ️ {route.note}"
    return text


def format_all_routes(routes: list[Route]) -> str:
    lines = ["🚦 **Available options** (estimates)\n"]
    for r in routes:
        lines.append(f"- **{r.mode.capitalize()}** → ~{r.duration_min:.0f} min, £{r.cost_gbp:.2f}")
    return "\n".join(lines)
