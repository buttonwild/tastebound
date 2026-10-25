"""Intent extraction: Nemotron (fast model) with a deterministic keyword fallback.

The keyword parser always runs, so the conversation still works when the LLM
is unavailable or returns something unusable. LLM values take precedence.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)

PREFERENCES = {"save time", "save money", "balanced"}
MODES = {"walk", "bus", "train", "flight"}
BOOL_FIELDS = (
    "show_all_options",
    "restart_trip",
    "acknowledgement",
)

SYSTEM_PROMPT = """You label ONE chat message sent to a UK travel assistant.
Output a JSON object containing ONLY the fields the message explicitly expresses.
Most messages express zero, one or two fields. If none apply, output {}.
Never copy values from these instructions. Never output false or empty values.

Fields:
- start_time: when they leave, copied from the message
- preference: "save time" | "save money" | "balanced" (a cost/speed trade-off such
  as "cheap but not too slow" is "balanced")
- show_all_options: true if they ask to see/compare all options
- restart_trip: true if they want to plan a new or different trip
- acknowledgement: true only for thanks / ok / great style replies
- tastes: list of things they say they like (artists, films, books, food, hobbies)

A bare place name is NOT any field. "skip" or "no" is NOT any field.

Examples:
"Leeds" -> {}
"Bristol Temple Meads" -> {}
"skip" -> {}
"Friday 7pm" -> {"start_time": "Friday 7pm"}
"next Monday, cheapest please" -> {"start_time": "next Monday", "preference": "save money"}
"thanks!" -> {"acknowledgement": true}
"show me everything" -> {"show_all_options": true}
"big fan of Bowie and Thai food" -> {"tastes": ["Bowie", "Thai food"]}
"""

_TIME_RE = re.compile(
    r"\b(now|asap|today|tonight|tomorrow|morning|afternoon|evening|noon|midnight|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|weekend|"
    r"\d{1,2}(:\d{2})?\s*(am|pm)|\d{1,2}:\d{2})\b",
    re.I,
)
_TASTE_RE = re.compile(r"\b(?:i (?:really )?(?:love|like|enjoy|am into|'m into)|fan of)\s+(.+)", re.I)


def _has(text: str, *words: str) -> bool:
    """Match words at a word start, so "rail" hits "railway" but not "trail"."""
    return any(re.search(r"\b" + w, text) for w in words)


def keyword_intent(message: str) -> dict:
    text = message.lower().strip()
    intent: dict = {}

    # Balanced first so "cheapest but not too slow" is not read as save money
    if _has(text, "balanced", "not too slow", "middle", "mix of"):
        intent["preference"] = "balanced"
    elif _has(text, "cheap", "budget", "save money", "lowest cost"):
        intent["preference"] = "save money"
    elif _has(text, "fast", "quick", "save time", "asap"):
        intent["preference"] = "save time"

    for mode, words in {
        "walk": ("walk", "on foot"),
        "bus": ("bus", "coach"),
        "train": ("train", "rail"),
        "flight": ("flight", "fly", "plane"),
    }.items():
        if _has(text, *words):
            intent["mode_request"] = mode
            break

    if _has(text, "all option", "other option", "show option", "compare"):
        intent["show_all_options"] = True
    if _has(text, "change start", "change the trip", "start again", "start over", "new trip", "restart"):
        intent["restart_trip"] = True
    if re.fullmatch(r"(thanks|thank you|thx|cheers|ok|okay|great|cool|got it)[.! ]*", text):
        intent["acknowledgement"] = True

    match = _TIME_RE.search(text)
    if match:
        intent["start_time"] = message.strip()

    taste = _TASTE_RE.search(message)
    if taste:
        parts = re.split(r",|\band\b|&", taste.group(1))
        intent["tastes"] = [p.strip(" .!") for p in parts if p.strip(" .!")]

    return intent


def _grounded(value: str, message: str) -> bool:
    """True if some word of an extracted value actually appears in the message."""
    words = re.findall(r"\w+", value.lower())
    return any(w in message.lower() for w in words if len(w) > 1)


def _sanitize(raw: dict, message: str) -> dict:
    """Keep only valid LLM fields. Free-text values must be grounded in the message,
    because small models sometimes invent them; mode requests come from keywords only."""
    clean: dict = {}
    for key in BOOL_FIELDS:
        if raw.get(key) is True:
            clean[key] = True
    if raw.get("preference") in PREFERENCES:
        clean["preference"] = raw["preference"]
    start_time = raw.get("start_time")
    if isinstance(start_time, str) and start_time.strip() and _grounded(start_time, message):
        clean["start_time"] = start_time.strip()
    tastes = raw.get("tastes")
    if isinstance(tastes, list):
        tastes = [t.strip() for t in tastes if isinstance(t, str) and t.strip() and _grounded(t, message)]
        if tastes:
            clean["tastes"] = tastes[:5]
    return clean


def extract_intent(message: str, llm=None) -> dict:
    """Return a sanitized intent dict. Never raises."""
    intent = keyword_intent(message)
    if llm is None:
        return intent
    try:
        llm_intent = _sanitize(llm.complete_json(SYSTEM_PROMPT, message, max_tokens=256), message)
    except Exception as exc:  # LLM problems must never break the conversation
        log.warning("Intent LLM call failed: %s", type(exc).__name__)
        return intent
    intent.update(llm_intent)
    return intent
