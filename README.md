# 🧭 Tastebound

**The travel agent that knows your taste, researches with sources, and (soon) gets things done.**

Tell Tastebound where you're going and what you love. It plans the UK journey, then uses a cultural taste graph to pick places at your destination that fit *you*. It checks live news for disruptions and links every source so you can verify it. On the roadmap: handing the jobs an AI can't do, like phoning a restaurant that only takes bookings by phone, to a vetted human.

| Module | API | What it does | Status |
|---|---|---|---|
| `concierge.py` + `llm.py` | **NVIDIA Nemotron** on Nebius Token Factory | Nano parses each turn; Super runs a tool-calling concierge that reasons over tool results | ✅ live |
| `taste.py` | **Qloo Taste AI** | Tastes → Qloo entities → places near the destination ranked by cultural affinity | ✅ live |
| `live_info.py` | **Tavily** | Past-week travel news (strikes, engineering works, weather), with source links | ✅ live |
| `geocode.py` | OpenStreetMap Nominatim | UK place lookup | ✅ live |
| `errands.py` | **Tendem** (human experts) | Last-mile check: a person phones a restaurant to confirm it's open, has a table, takes walk-ins | 🔐 implemented, operator-only, disarmed by default ([docs/modules/tendem.md](docs/modules/tendem.md)) |
| `style.py` | **YouCam** (Perfect Corp.) | "Pack for your destination": outfit virtual try-on + skin check with destination-weather tips | ✅ try-on live · skin check implemented ([docs/modules/youcam.md](docs/modules/youcam.md)) |

Each API is one module with one hard limit, and every module is optional: the app still runs with any subset of keys.

> Journey times and fares are heuristic estimates, not live timetables. The app says so in the UI.

## How it works

```
             ┌─────────────────────────── Streamlit chat UI (app.py) ───────────────────────────┐
user msg ──► │ TripAgent (agent.py)                                                              │
             │   1. intent.py   Nemotron Nano (fast) → JSON intent  ⟶ keyword fallback          │
             │   2. slot filling: start → destination → time → preference → tastes             │
             │   3. transport.py  journey options + choose_best_route (pure Python)             │
             │   4. concierge.py  Nemotron Super (reasoning) tool loop:                         │
             │        ├─ find_taste_places      → Qloo /search + /v2/insights (urn:entity:place)│
             │        ├─ search_live_travel_info → Tavily /search                               │
             │        └─ get_route_options      → transport.py                                  │
             └───────────────────────────────────────────────────────────────────────────────────┘
```

- **Two Nemotron models, routed by job.** A fast model parses every turn into structured intent. A reasoning model runs the concierge: it decides which tools to call, reads their results and writes an answer grounded only in them.
- **Qloo is the differentiator.** "I love Joy Division and Wes Anderson films" is resolved to Qloo entities, which then rank places near the destination by cultural affinity. Without Qloo, the agent can only suggest generic tourist spots.
- **Degrades gracefully.** Every external service is optional. With no keys at all the app still plans journeys, using keyword intent parsing and no recommendations. With Qloo/Tavily but no LLM, a deterministic brief is produced instead.
- **Agent tool calls are visible** in the chat (🛠️ expander), so you can see what the agent actually did.
- **Verifiable live info.** Tavily searches are limited to the past week, and answers that use them always end with their source links.
- **Hard spending cap.** Every Token Factory call goes through a guard that refuses calls once estimated cumulative spend could exceed **$50** (see below).

## Safety & abuse limits

The demo is public, and the keys behind it have real bills, so every paid or quota-limited API has a hard limit enforced **in code, before the request is sent**.

| API | App-wide limits (defaults) | Hard maximum (env can only lower) |
|---|---|---|
| Nebius Token Factory | **$50 cumulative spend** (demo: $10) · 300 calls/hour · 1,500/day | $50 · 600/h · 3,000/day |
| Tavily | 60 searches/hour · 200/day · **500 credits cumulative** | 120/h · 400/day · 1,000 credits |
| Qloo | 300 calls/hour · 2,000/day | 600/h · 4,000/day |
| YouCam | **600 units cumulative** + **real-balance floor 440** · 20 tasks/hour · 60/day · 3 try-ons + 1 skin check per session | 600 units · 40/h · 120/day |
| Tendem | $12/task · $30 cumulative · 1 task · **real-balance floor $20** · operator passphrase (constant-time, global lockout) · `TASTEBOUND_TENDEM_LIVE=1` arming switch | $12 · $30 · 2 tasks |
| Per browser session | 40 messages · 500 characters per message | — |

- **Global, not per session.** Opening new tabs or sessions doesn't reset anything: one ledger per app (`~/.tastebound/`) is shared by every session, thread and process, under a file lock.
- **Spend cap (`budget.py`).** Each Token Factory call is charged its worst case (estimated prompt + full `max_tokens`) up front and refused if that could cross the cap. After the call, the charge is replaced with the reported usage. Prices are list price × 1.25, and unknown models are charged at a deliberately high rate.
- **Usage limits (`limits.py`).** Fixed UTC hour/day windows plus cumulative caps. Units are reserved before the request and never refunded, so counts over-approximate.
- **Graceful degradation.** When a limit is hit, the request is never sent. Journey planning keeps working, the concierge falls back to a template brief with a short note, and the sidebar shows usage.
- **Fails closed.** A corrupt ledger blocks calls instead of allowing them.
- **Keys stay server-side.** They come from env or the host's secret store and are never sent to the browser, logged or committed. Error details are hidden from viewers (`.streamlit/config.toml`), and logs record exception types only.
- **Hosted caveat.** Free hosts wipe the disk on restart, resetting the ledger, so cumulative caps then hold per container lifetime. The hourly/daily limits still bound the worst case (≈$1.7/hour of Nebius at the hard maximum). Also watch each provider's dashboard.

Measured cost: one full planning conversation (8 turns, ~15 Nebius calls) ≈ **$0.006**, plus 2 Tavily credits.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in your keys; .env is git-ignored
set -a; source .env; set +a
streamlit run app.py
```

| Variable | Required for | Notes |
|---|---|---|
| `NEBIUS_API_KEY` | Nemotron intent + concierge | [Token Factory](https://tokenfactory.nebius.com) |
| `TASTEBOUND_FAST_MODEL` / `TASTEBOUND_REASONING_MODEL` | optional | Defaults `nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B` / `nvidia/nemotron-3-super-120b-a12b` (verified on Token Factory). `python scripts/list_models.py` lists the others |
| `TASTEBOUND_NEBIUS_BUDGET_USD` | optional | Lower the spending cap (max $50) |
| `QLOO_API_KEY` | taste-matched places | Hackathon keys only work against `https://hackathon.api.qloo.com` (the default; override with `QLOO_API_URL`) |
| `TAVILY_API_KEY` | live travel news | [Tavily](https://app.tavily.com) |
| `YOUCAM_API_KEY` | outfit try-on + skin check | YouCam API console. Only the API key is needed; the v1 secret key is not used |
| `NOMINATIM_USER_AGENT` | recommended | Nominatim's policy asks for an identifying UA |

On Streamlit Community Cloud, put the same keys in **App settings → Secrets**. The app copies them into the environment at startup.

### Deploy the public demo (Streamlit Community Cloud, free)

The GitHub repo can stay **private**; only the running app is public.

1. Sign in at [share.streamlit.io](https://share.streamlit.io) with GitHub and allow access to private repos.
2. **Create app** → repo `buttonwild/tastebound`, branch `main`, main file `app.py`, Python 3.12.
3. **Advanced settings → Secrets**: paste this and fill in the values. Keys live only in Streamlit's secret store, never in the repo:

   ```toml
   NEBIUS_API_KEY = "..."
   QLOO_API_KEY = "..."
   QLOO_API_URL = "https://hackathon.api.qloo.com"
   TAVILY_API_KEY = "..."
   TASTEBOUND_NEBIUS_BUDGET_USD = 10
   NOMINATIM_USER_AGENT = "tastebound-demo/0.2 (+https://<your-app>.streamlit.app)"
   ```

4. Deploy, then **Share → Make this app public**.

See [Safety & abuse limits](#safety--abuse-limits) for the app-wide limits and the free-host ledger caveat.

### Deploy on Nebius

```bash
docker build -t tastebound .
docker run -p 8501:8501 --env-file .env tastebound
```

The same image can be deployed as a **Nebius Serverless Endpoint** (port 8501). Pass keys as endpoint environment variables; never bake them into the image.

## Tests

```bash
pytest              # 113 offline tests: transport, intent, agent flow, tool loop, API clients, spend cap, usage limits, YouCam, Tendem, operator panel
pytest -m live      # optional smoke tests against the real APIs (needs keys)
```

The offline tests use fakes with **synthetic** data. Qloo's terms don't allow storing their API responses in a public repo, so none are committed.

## Project layout

```
app.py                     Streamlit UI (rendering only)
tastebound/agent.py        conversation controller + service wiring
tastebound/concierge.py    Nemotron tool-calling loop, tools, deterministic fallback
tastebound/intent.py       Nemotron intent extraction + keyword fallback
tastebound/llm.py          Token Factory (OpenAI-compatible) client, model routing
tastebound/budget.py       hard spending cap for Token Factory calls
tastebound/limits.py       app-wide hourly/daily/cumulative limits per API
tastebound/style.py        YouCam client: try-on, skin analysis, unit cap + balance floor
tastebound/style_panel.py  Streamlit "Pack for your destination" panel
tastebound/errands.py      Tendem errands: operator gate, caps, real-balance floor, MCP transport
tastebound/operator_panel.py  operator-only diagnostics + restaurant-check flow
tastebound/ledger.py       file-locked JSON state shared by the limits
docs/                      module designs, demo video script, submission texts
tastebound/taste.py        Qloo client
tastebound/live_info.py    Tavily client
tastebound/transport.py    journey options, cost model, route choice
tastebound/geocode.py      Nominatim client with caching and timeouts
scripts/list_models.py     list Nemotron models available to your key
```

## License

[MIT](LICENSE)
