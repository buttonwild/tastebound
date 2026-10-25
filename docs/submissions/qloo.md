# Devpost submission — Qloo Agentic Hackathon

- **Deadline:** Oct 30, 2026 11:45pm EDT = **Oct 31, 11:45 (UTC+8)**
- **Required:** externally hosted, working demo (`[DEMO_URL]`), public repo with OSI license visible in About (`[REPO_URL]`; needs user approval), text description. A video is optional; reuse the Qloo cut of `docs/VIDEO_SCRIPT.md`.

---

## Project name
Tastebound

## Tagline
A travel agent that knows what you love: Qloo's taste graph turns your favourite bands, films and food into places worth visiting when you arrive.

## The problem
Every travel assistant answers "where should I eat in Manchester?" with the same list. LLMs on their own can't know that someone who loves Joy Division and Wes Anderson films will walk past the chain café and into a moody, design-led coffee bar. Without cultural grounding, "personalised" travel advice is really just popular travel advice.

## What it does
Tastebound is a chat agent for UK trips:
1. It plans the journey (start, destination, time, preference) and recommends the best option.
2. It asks what you love. You might say "Joy Division, Wes Anderson films and good coffee".
3. **The agent calls Qloo** to:
   - resolve each taste to a Qloo entity via `/search`;
   - query `/v2/insights` for `urn:entity:place` with `signal.interests.entities` around the destination (`filter.location` as a WKT point);
   - optionally narrow by vibe through `/v2/tags` (e.g. `urn:tag:...:brunch`).
4. It explains each pick using Qloo's own tags and affinity scores ("industrial vibe, specialty coffee, high affinity for your tastes").
5. Follow-ups reuse what it knows about you. "Brunch near my destination that suits my taste?" triggers a new Qloo query that combines the brunch tag with your interests.

Live disruption news (Tavily, with sources) and journey estimates complete the arrival brief.

## Why it only works because of Qloo
Remove Qloo and the agent can still plan the train, but its recommendations collapse to generic tourist spots. With Qloo, two travellers on the same train get different arrival briefs, because the agent knows *what people with their taste actually love* instead of guessing.

## How it's agentic
A **NVIDIA Nemotron Super** reasoning model runs a bounded tool-calling loop.
- It decides *when* to call Qloo (and with which tastes and vibe), when to search live news, and when to look up routes.
- It answers only from tool results.
- Every tool call is shown in the UI, so judges can see the agent's decisions.

A fast Nemotron Nano model parses each message. Tastes mentioned anywhere in the conversation ("I'm also into jazz") are picked up automatically.

## Technical notes
- Qloo integration: `tastebound/taste.py` (search → insights → tags), with parallel taste resolution and a per-turn cache.
- One finding: the hackathon API rejects `filter.location=lat,lon` (HTTP 400, "WKT format is invalid") even though the docs list it, so we send `POINT(lon lat)`.
- Responsible use of the hackathon key: Qloo calls are limited app-wide (hourly/daily), and no Qloo response data is stored in the repo. Tests use synthetic fixtures only.
- 68 offline tests and live smoke tests against the real Qloo hackathon API.

## Existing project — what changed during the submission period
The project began as a UK journey-planning chatbot prototype with no recommendations. The Qloo integration, the tool-calling agent, the taste capture in conversation and the hosted demo were all built during the submission period.

## What's next
- Taste-matched *destinations*, not just places (Qloo `urn:entity:destination`): "where should a Britpop fan spend a weekend?"
- Handing phone-only bookings at the places Qloo finds to a vetted human (Tendem), behind a price cap.
- Group trips: blend several travellers' tastes into one plan.

## Built with
qloo · nvidia-nemotron · nebius-token-factory · tavily · python · streamlit

## Links
- Live demo: `[DEMO_URL]` · Code: `[REPO_URL]`
