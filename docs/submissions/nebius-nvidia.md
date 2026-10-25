# Devpost submission — Nebius × NVIDIA Global AI Hackathon

- **Deadline:** Oct 30, 2026 10:00am PDT = **Oct 31, 01:00 (UTC+8)**
- **Track:** Best Apps and Agents · also eligible for **Best Use of Tavily**
- **Fill before submitting:** `[DEMO_URL]`, `[VIDEO_URL]`, `[REPO_URL]` (repo must be public, with MIT visible in About; needs user approval)

---

## Project name
Tastebound

## Tagline (≤ 200 chars)
The travel agent that knows your taste: Nemotron reasons over Qloo's taste graph and Tavily's sourced live news to plan a UK trip that fits you.

## Inspiration
Travel planners are good at getting you from A to B and bad at everything that makes a trip feel like *yours*: where to eat when you arrive, whether the line is closed this weekend, and whether a suggestion comes from evidence or from a confident guess. We wanted an agent that reasons over real signals and shows its sources. Tastebound was designed and built by a solo developer.

## What it does
- Plans a UK journey in a short chat: start, destination, time, and a time/money/balanced preference. It compares bus, train and flight options, with estimates clearly labelled.
- Asks what you love (bands, films, books, food) and uses **Qloo's taste graph** to recommend places at your destination ranked by cultural affinity, explaining *why* each one fits.
- Checks **Tavily** for past-week disruption news (strikes, engineering works, weather) and always attaches the sources.
- Handles open-ended follow-ups ("brunch near my destination that suits my taste?") through a tool-calling concierge.
- Shows every tool call the agent made, in an expander under each answer.

## How we built it
- **Two NVIDIA Nemotron models on Nebius Token Factory, routed by job.**
  - `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` parses every turn into structured intent. We call it with `chat_template_kwargs.enable_thinking=false`, which gives about 0.4s per call.
  - `nvidia/nemotron-3-super-120b-a12b` runs the concierge: a bounded tool loop (OpenAI-compatible `tools`) over `find_taste_places` (Qloo), `search_live_travel_info` (Tavily) and `get_route_options`. It may only make claims that come from tool results.
- **Python + Streamlit.** The agent is UI-agnostic and covered by 68 offline tests (fakes with synthetic data) plus live smoke tests.
- **Built for a public demo with real bills.** The demo has a hard $10 Token Factory spend cap ($50 maximum in code). Each call is charged its worst case before it is sent and refused if it could cross the cap. On top of that, app-wide hourly/daily limits per API stop abuse spread across many sessions, and the app degrades gracefully when a limit is hit.

## Challenges we ran into
- With thinking on and `max_tokens=256`, Nano used all of its tokens reasoning and returned empty content. Disabling thinking fixed it; a `/no_think` prompt prefix did not.
- With thinking off, Nano invented fields for short inputs (a bare place name came back with a departure time). Few-shot examples took our 14-case intent eval from **5/14 to 13/14**. We also check that extracted values appear in the user's message, and take transport modes from deterministic keywords.
- Nemotron Super sometimes emits internal citation markers (`【{"cursor": 0, "loc": 1}】`), which we clean up.

## Accomplishments that we're proud of
- An agent whose recommendations are grounded in tool results, with sources you can click.
- A full 8-turn planning conversation costs about **$0.006** on Token Factory.
- Spend and abuse safety is enforced in code, not left to hope.

## What we learned
Routing a small model to the frequent, simple calls and a larger reasoning model to the few calls that need it gives both speed and quality, and Token Factory's OpenAI compatibility makes the split a one-line change per call.

## What's next for Tastebound
- **Getting things done:** hand phone-only restaurant bookings and other real-world errands to vetted human experts via Tendem, behind a per-task price cap.
- Destination outfit try-on (YouCam).
- A general "research mode" with cite-every-claim answers.
- Deploy on Nebius Serverless Endpoints (a Dockerfile is included).

## Built with
nvidia-nemotron · nebius-token-factory · tavily · qloo · python · streamlit · openai-sdk · openstreetmap

## Existing project — what was significantly updated during the submission period
The project started as a single-file UK journey-planning chatbot prototype (Hugging Face Mistral-7B intent parsing whose output was mostly ignored, plus heuristic route estimates). During the submission period it was rebuilt as a modular, tested agent package. New in this period: Nemotron on Token Factory with model routing, the tool-calling concierge, Qloo taste recommendations, Tavily sourced live info, spend and usage limits, and the hosted demo.

## Feedback on Nebius Token Factory / NVIDIA models
- OpenAI compatibility was drop-in, including `tools` / `tool_choice`, and `/v1/models` made model discovery easy.
- Please document prominently that Nemotron 3 models think by default and can return empty content when `max_tokens` is small. Also document `chat_template_kwargs.enable_thinking` (it works; `/no_think` didn't for us).
- Nemotron Super's tool selection and argument filling were reliable in our tests. The stray citation markers in final text would be worth fixing.
- We saw one transient spell of >60s responses from Super; a public status/latency page would help.
- **Wish:** per-API-key spending limits in Token Factory. Our card has no limit, so we had to build a hard cap ourselves.

## Links
- Demo: `[DEMO_URL]` · Video: `[VIDEO_URL]` · Code: `[REPO_URL]`
