# Hackathon submission notes

**Strategy:** one product (Tastebound), one module per API, a different lead module per event. Every module has a hard limit, and nothing is published or submitted without the user's approval.

| Hackathon | Deadline (UTC+8) | Lead module | Also shown | Repo | Materials |
|---|---|---|---|---|---|
| Nebius × NVIDIA Global AI | **Oct 31 01:00** (Oct 30 10:00 PDT) | Nemotron reasoning concierge | Qloo, Tavily (Best Use of Tavily) | public | [submission text](docs/submissions/nebius-nvidia.md) · [video script](docs/VIDEO_SCRIPT.md) |
| Qloo Agentic | **Oct 31 11:45** (Oct 30 23:45 EDT) | Qloo taste reasoning | Nemotron, Tavily | public + hosted demo | [submission text](docs/submissions/qloo.md) · video optional |
| YouCam Skin AI & eCommerce VTO ([page](https://youcam-api-skin-ai-ecommerce.devpost.com)) | **Nov 3 00:45** (Nov 2 11:45 EST) | `style.py` outfit try-on (topic: eCommerce VTO) + skin check | Qloo, Tavily, Nemotron | public, or private shared with the organiser | [module + verified API facts](docs/modules/youcam.md) · needs video + screenshots |
| ODSC × Nebius (online) | **Nov 20** (build Oct 28 – Nov 20) | Sourced research (Tavily + Nemotron) | Qloo | public | section below |
| *(any)* | — | `errands.py` human tasks via Tendem (to build) | — | — | [module design](docs/modules/tendem.md) |

## What existed before vs. what was built during the submission period

Both hackathons require this for projects that pre-date the submission period.

**Before:** a UK journey-planning chatbot prototype. It had a single Streamlit file holding the UI, state machine and logic; a Hugging Face Mistral-7B call for intent parsing whose output was mostly ignored; heuristic distance-based route options; and Nominatim geocoding with no timeouts or error handling.

**Significantly updated during the submission period:**
- Re-architected into a UI-agnostic agent package (`tastebound/`) with 45 offline tests, including full conversation flows
- Replaced Hugging Face with **NVIDIA Nemotron on Nebius Token Factory**, using two models routed by job (fast intent parsing, reasoning concierge)
- New **tool-calling concierge agent** (`concierge.py`) that grounds answers in tool results
- New **Qloo Taste AI integration**: tastes → `/search` entities → `/v2/insights` places near the destination, with optional `/v2/tags` vibe filters
- New **Tavily** live travel-news tool
- Taste capture in the conversation, and open-ended follow-ups after a plan
- Fixed bugs: dead `mode_request` feature, inconsistent route schema (`time` vs `duration`), a "current location" step that did nothing, an unreachable train note, geocoding crashes on network errors, and route preferences that never differed for medium/long trips
- Dockerfile for Nebius Serverless Endpoints, env-based secret handling
- Hard $50 spending cap on Token Factory calls (`budget.py`)
- Sourced answers: Tavily results limited to the past week, with source links appended to answers

---

## Nebius × NVIDIA Global AI Hackathon

- **Track:** Best Apps and Agents
- **Also eligible:** Best Use of Tavily (makes a runtime Tavily API call via `search_live_travel_info`)
- **Requirement check:**
  - [x] Runtime calls to the Token Factory inference API (`tastebound/llm.py`), verified live
  - [x] Uses NVIDIA open models: `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` (fast) + `nvidia/nemotron-3-super-120b-a12b` (reasoning)
  - [x] Public repo with OSI license (MIT) shown in the About section
  - [x] README with setup instructions
  - [ ] Working demo URL (deploy, then paste here)
  - [ ] ≤3 min public YouTube demo showing Token Factory + Nemotron in use
  - [x] Feedback on Token Factory / Nemotron (below, from real usage; review and edit before submitting)

**How Nemotron is used:** Nano parses every chat turn into structured JSON intent at low latency and cost. Super runs the concierge loop: it chooses tools (Qloo, Tavily, route options), reads their results and writes a grounded answer. This follows the track's own advice: Super for reasoning, Nano for the fast everyday calls.

**Feedback (from building this; edit before submitting):**
- **OpenAI compatibility:** drop-in with the `openai` SDK (base URL + key), including `tools` / `tool_choice` function calling. `/v1/models` made model discovery easy.
- **Nemotron Nano thinking eats the token budget:** with thinking on and `max_tokens=256`, Nano used all 256 tokens reasoning and returned **empty content** (`finish_reason=length`). `extra_body={"chat_template_kwargs": {"enable_thinking": false}}` fixed it: 22 output tokens, about 1.3s. A `/no_think` system-prompt prefix did *not* work. It would help to document this prominently.
- **Hallucinated fields with thinking off:** for short inputs (a bare place name), Nano filled in fields using example values copied from the prompt. Few-shot examples improved our 14-case intent eval from 5/14 to 13/14. We also verify extracted values appear in the message.
- **Nemotron Super tool calling** chose sensible tools and arguments (Qloo vs Tavily vs routes) and synthesised the results well. It sometimes emits internal citation markers like `【{"cursor": 0, "loc": 1}】`, which we strip in post-processing.
- **Latency:** Nano about 0.4s per call with thinking off. A Super turn with tools takes about 5–9s end to end. We saw one transient spell where Super requests exceeded 60s; a status/latency page would help.
- **Cost:** one full 8-turn conversation is about $0.006.
- **Wish:** per-key spending limits in Token Factory itself. We had to build our own hard cap because the card has no limit.

---

## Qloo Agentic Hackathon

- **Category fit:** (a) functions as an agentic tool, and (b) a tool-using agent built with a model-driven tool loop
- **Requirement check:**
  - [x] Integrates the Qloo API (`tastebound/taste.py`), against `hackathon.api.qloo.com`, verified live
  - Note: the hackathon API rejects `filter.location=lat,lon` (HTTP 400 "WKT format is invalid") even though the docs list it. We send `POINT(lon lat)`.
  - [x] Public repo + OSI license visible in About
  - [ ] **Externally hosted, fully published demo** (required; local-only does not qualify)
  - [ ] Text description (draft below)
- Videos are not required.

**Description draft:**
> Travel planners know how to get you there, but not what you'll love when you arrive. Tastebound is a UK trip agent that plans the journey and then uses Qloo's taste graph to pick places at your destination that match what you actually like: the bands, films, books and food you mention in conversation. A Nemotron-powered concierge decides when to call Qloo, Tavily (live disruptions) and its own route planner, and explains *why* each place fits you, using Qloo's tags. Without Qloo it can only suggest generic tourist spots. With Qloo, a Joy Division fan heading to Manchester gets a different arrival brief from a Wes Anderson fan.

---

## ODSC × Nebius

**Confirmed timeline (registration):** online livestream kickoff **Oct 28, 2026**, build period **Oct 28 – Nov 20**, submission deadline **Nov 20, 2026**. Requirements (from the challenge brief): inference on Nebius Token Factory, Tavily web search, public repo with README, short demo.

**Challenge:** *Build a research agent that turns a question into a sourced, verifiable answer.*

**Fit:** Tastebound is a domain-specific research agent. Questions like "any strikes or engineering works on my route this weekend?" get Nemotron + Tavily answers with source links appended by code, limited to the past week. Requirements met: Token Factory ✔, Tavily ✔, README ✔. Still needed: public repo, short demo.

**Plan for the build period (Oct 28 – Nov 20):** work added after Oct 28 is what counts as new for this event. The natural extension is a **research mode**: the same concierge loop with a general web-search tool, a cite-every-claim prompt, and the existing `with_sources` sourcing. Check the kickoff livestream for the final judging criteria, and whether one project may also be entered in other hackathons, before committing to it.

---

## Pre-submit checklist

- [x] Model IDs verified with `scripts/list_models.py` (defaults are correct)
- [x] `pytest` (60) and `pytest -m live` (4) pass with real keys
- [ ] Deploy to Streamlit Community Cloud (repo stays private; see README "Deploy the public demo") with keys in the app's Secrets and `TASTEBOUND_NEBIUS_BUDGET_USD = 10`
- [ ] Rotate any key that was ever on disk in the original project folder or shared in a zip
- [x] Private repo `buttonwild/tastebound` created with clean history
- [ ] Make the repo **public** when ready to submit (required by all three): `gh repo edit buttonwild/tastebound --visibility public --accept-visibility-change-consequences`
- [ ] Add the repo description/topics so the MIT license shows in the About section
