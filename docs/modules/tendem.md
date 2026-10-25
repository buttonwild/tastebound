# Module design: `errands.py` — real-world tasks via Tendem

Status: **implemented, disarmed. No real task has been created or sent.** Module: `tastebound/errands.py`, UI: `tastebound/operator_panel.py`, tests: `tests/test_errands.py`, `tests/test_operator_panel.py`. Updated 2026-10-24.

## As built: access and spend controls

| Layer | Behaviour |
|---|---|
| **Operator gate** | `TASTEBOUND_OPERATOR_PASSPHRASE` (≥ 20 chars, otherwise operator mode is disabled). Only a SHA-256 digest is kept in memory, and attempts are compared with `hmac.compare_digest` on equal-length digests (constant time). **Failures are rate-limited globally**: a file-locked ledger shared by all sessions, 5 failures in 15 min lock the gate (even for the right passphrase), and each failure adds a fixed 1 s delay. Success resets the counter. Unlock lasts 30 min per browser session |
| **Arming switch** | `create_task`, `approve_task`, `send_message` and `cancel_task` also require `TASTEBOUND_TENDEM_LIVE=1` (exact value). Default off, and **off on the public demo**. Unknown tools are refused |
| **Single template** | Only `RestaurantCheck` (approved task 1): open tonight + table for 2 around 19:30 + walk-ins; explicitly **no booking**, no personal details. Inputs are validated (single-line fields, phone format, HH:MM, party 1–6) |
| **Per-task cap** | **$12** (env can only lower) |
| **Cumulative cap** | **$30** (env can only lower), persistent ledger; the quoted price is reserved before approval |
| **Task count** | **1** by default (only task 1 approved), hard max 2 |
| **Real-balance floor** | Before approval, Tendem's **actual balance** is read (`get_account`, never cached) and approval is refused if `balance − quote < $20` (a reserve kept on the small hackathon credit, beyond the $30 cap). Env can only raise it. Holds even when a free host wipes the ledger |
| **Approval integrity** | Re-reads the live contract, refuses unquoted contracts, and requires the operator to confirm the **exact server price string** (a changed quote is refused). Sends the server's own formatted price. Serialized with a lock |
| **Top-up** | An insufficient-balance outcome releases the reservation, and the `topup_url` is **never followed** |
| **Unknown outcome** | If `approve_task` errors (e.g. timeout), the reservation is kept (it may have been charged) |
| **App-wide call limit** | `tendem`: 30 calls/hour, 120/day (all MCP calls, read and write) |

**Live wiring verified read-only (2026-10-24):** `MCPTransport` → `get_account` returned the balance (a small hackathon credit). `floor_status` confirmed the $20 floor is active, with headroom for the $30 cap. `submit()` was refused with "disarmed". **Before any real task:** report the specific restaurant, the exact brief text and Tendem's quote to the user, and get approval, then set `TASTEBOUND_TENDEM_LIVE=1` only for that run.


**Connection verified (read-only, 2026-10-24):** `TENDEM_API_KEY` authenticates against `https://mcp.tendem.ai/mcp` (server `tendem-mcp` 0.1.0). `list_tools` returns all 11 tools, and `get_account` reports the account balance (a small promotional hackathon credit, protected by the hard caps above). The check script allowlists `get_account` only, so nothing that creates, approves or messages is callable. Client note: use `mcp>=1.10,<2`. The 2.x SDK renamed the transport (`streamablehttp_client` → `streamable_http_client`), and `langchain-tendem` targets 1.x.

## What Tendem is (research summary)

[Tendem](https://tendem.ai) (by Toloka) hands tasks to vetted human experts with AI orchestration. It has a dedicated **Calls & Errands** category ([tendem.ai/for/calls](https://tendem.ai/for/calls)) whose own examples include "Book a table for 2 in Lisbon (they speak Portuguese)", "Confirm a dinner reservation" and "Get quotes from 5 caterers". Calls are made in major European languages, are recorded and transcribed, and identify who is calling. That is exactly the gap Tastebound has when a Qloo-recommended place only takes bookings by phone.

| Question | Answer |
|---|---|
| API? | **Yes.** A hosted MCP server at `https://mcp.tendem.ai/mcp` (streamable HTTP). Headless auth: `Authorization: ApiKey <key>`. Create the key at [agent.tendem.ai/mcp](https://agent.tendem.ai/mcp) → *Agent builders* tab |
| Tools | `create_task`, `get_task` (long-poll via `wait_for_change_seconds`), `get_contract` (scope + price), `approve_task` (**the only call that charges**), `get_task_result`, `list_tasks`, `cancel_task`, `send_message`, `read_chat`, `get_account`, `get_file_upload_url` |
| Official client | [`langchain-tendem`](https://pypi.org/project/langchain-tendem/) 0.2.0 (MIT). It includes a typed `Tendem` client usable without LangChain agents, and its `approve_task` refuses quotes above `max_price` |
| Lifecycle | create → Tendem scopes, may ask questions (`await_input`) → quote (`await_user_approval`) → approve → work (`awaiting_tendem_work`) → `fetch_result` → done. Insufficient balance → `await_user_topup` with a top-up URL (never auto-pay) |
| Pricing | Per task, **quoted before any charge**, with no subscription. Published examples are tiered "under $7 / under $50 / under $200". First 3 tasks are 50% off (site banner). Trivial briefs may be answered free during scoping. No per-call API fee found |
| Turnaround | "2–24 hours" typical; scoping is observed at 3–5 minutes (client constants) |
| Limits / policy | Briefs must be self-contained (the expert sees only the brief); one unit of work per task; legitimate calls only (no cold-calling or robocalls); data scraping refused |
| Env var | `TENDEM_API_KEY` (official name; we keep it) |

## Where it fits in Tastebound

```
Qloo picks a place ─► agent checks: bookable online? (Qloo external ids, website)
                        └─ no / phone-only ─► "📞 Have a person call to book?" card
user fills party size, time, name (consent checkbox) ─► Nemotron drafts a self-contained brief
user reviews brief ─► create_task ─► scoping Q&A (agent answers from trip context; asks user if unsure)
quote arrives ─► confirmation card: price · scope · ETA ─► user clicks Approve
guard checks caps ─► approve_task ─► background status polling ─► confirmation number in chat
```

## Hard limits (layered; all enforced in code before `approve_task`)

| Layer | Default | Hard max | Why |
|---|---|---|---|
| Per-task price cap | `TASTEBOUND_TENDEM_MAX_TASK_USD=12` | $12 | A phone booking should cost little; anything higher is a scoping problem |
| Cumulative spend | `TASTEBOUND_TENDEM_BUDGET_USD=30` | **$30** | Persistent ledger: the quoted price is reserved before approval and never refunded |
| Task count | `TASTEBOUND_TENDEM_MAX_TASKS=1` | 2 total | Only demo task 1 is approved |
| Who may approve | Only the **operator**, via a passphrase in secrets (`TASTEBOUND_OPERATOR_PASSPHRASE`) | — | Public visitors must never spend the balance |
| Who triggers approval | Only a UI click; never the LLM | — | The concierge gets *no* approve tool; approval isn't model-callable |
| Public visitors | **Dry-run**: shows the drafted brief and "what would be sent"; no `create_task` | — | Also avoids spamming Tendem's experts |
| Balance check | `get_account` before approval; a top-up URL is shown to the operator, never followed | — | No silent top-ups |

Existing app-wide limiters (`limits.py`) also get a `tendem` entry (e.g. 10 calls/hour, 50/day) for the non-charging calls.

## Interface sketch

```python
# tastebound/errands.py
@dataclass(frozen=True)
class ErrandQuote:
    task_id: str
    price_usd: float
    scope: str
    eta: str | None

class ErrandsClient:
    """Wraps the official Tendem client; adds Tastebound's caps and dry-run mode."""

    def __init__(self, api_key: str, guard: ErrandGuard, dry_run: bool = True): ...
    def draft_brief(self, place: TastePlace, booking: BookingRequest, ctx: TripContext) -> str: ...
    def submit(self, brief: str) -> str:                       # create_task → task_id (refused in dry-run)
    def status(self, task_id: str) -> ErrandStatus:            # get_task, short long-poll
    def answer(self, task_id: str, reply: str) -> None:        # send_message
    def quote(self, task_id: str) -> ErrandQuote:              # get_contract
    def approve(self, quote: ErrandQuote, operator_ok: bool) -> None:
        # guard.reserve(quote.price_usd) checks per-task + cumulative + count caps;
        # then the Tendem client's approve_task(max_price=...) checks the price again
    def result(self, task_id: str) -> ErrandResult:            # get_task_result (markdown + files)

class ErrandGuard:  # same ledger pattern as budget.py: reserve before charge, fail closed
    def reserve(self, price_usd: float) -> None: ...
```

- **Dependency:** pin `langchain-tendem==0.2.0` for the typed client, which pulls `mcp`. The lighter alternative is to call the 11 tools with the `mcp` SDK directly; that's ~150 lines and our own guard either way.
- **Streamlit:** store the task id in the ledger so it survives reruns and restarts, and poll with an `st.fragment(run_every=30)` status widget.

**Brief template.** Each line is checkable from the deliverable alone, so Tendem's auto-generated QA can pass it:

```
Call <restaurant>, <address>, <phone if known>, during opening hours.
Goal: book a table for <n> on <date> at <time> (acceptable window: <±30 min>), name "<name>".
If unavailable, ask for the nearest available time within <window> and do NOT book outside it.
Deliverable: (1) booked yes/no, (2) date/time confirmed, (3) reference number if given,
(4) any deposit/cancellation terms mentioned. Unverifiable details must not be a rejection reason.
```

## Demo plan (1–2 small tasks, needs user approval before sending)

1. **Information-only call** (cheapest, no no-show risk): ask a phone-booking restaurant from a real Tastebound plan whether they have a table for 2 on a given evening and vegan options. Do **not** book. Expected cost is in the "under $7" tier, possibly 50% off.
2. *(Optional)* a **real booking** the user actually intends to keep, with the user's own name and consent.
3. Turnaround can be hours, so for the video show the flow live up to "quote approved", then cut to the completed result.

## Proposed demo tasks (awaiting user approval; nothing created)

Both are **last-mile verification calls**: the cheapest kind of task, and the exact gap in Tastebound's flow. Qloo and Tavily tell you *what* to visit; a human confirms it is *actually open and available tonight*. Information only, **no booking**, so there's no no-show risk and no personal details beyond a first name.

| # | Task | Why it fits | Expected price | Cap |
|---|---|---|---|---|
| 1 | **Restaurant check:** call one Qloo-recommended restaurant near the trip's destination (picked live from the app during the demo). Confirm (a) open tonight and closing time, (b) whether a table for 2 is free around 19:30, (c) walk-in vs booking only. **Do not book.** | Turns a taste-matched pick into a verified, actionable plan | "under $7" tier (first 3 tasks −50%) | **$12** |
| 2 *(optional)* | **Hotel check:** call one hotel near the destination station. Ask tonight's walk-in rate for a double room and whether rooms are available. **Do not reserve.** | Grounds the journey plan in a real arrival option | "under $7" tier | **$12** |

- **Budget:** at most 2 tasks and **$24 worst case**, within the $30 cumulative cap. The guard refuses any quote above $12.
- **Brief template (task 1):** "Call <restaurant>, <address>, <phone if listed>, today between 15:00 and 18:00 local time. Ask: (1) Are you open tonight, and until what time? (2) Is a table for 2 available around 19:30? (3) Do you take walk-ins or bookings only? Do NOT make a booking. Deliverable: three short answers plus the time of the call. If there's no answer after 2 attempts, report 'no answer'. Unverifiable details must not be a rejection reason."
- **Flow:** the operator unlocks errands with `TASTEBOUND_OPERATOR_PASSPHRASE`, reviews the brief, sees Tendem's quote, and approves only if it's ≤ $12. Turnaround is 2–24 h, so run it the day before recording and show the completed result in the video.
- **Operator passphrase:** set in `.env` (2026-10-24). For the hosted app, it's added through Streamlit secrets.

## Manual handoff (if no API key or the account isn't ready)

Tastebound drafts the same brief and shows **"Copy brief → open agent.tendem.ai"**. The user pastes it in Tendem's web UI, approves the quote there, and pastes the result back. No spending path exists in the app at all.

## What the user needs to provide

1. ~~Tendem account + API key~~ ✅ done: `TENDEM_API_KEY` is in `.env`, the account has a small gift credit, and the connection is verified.
3. ~~An operator passphrase~~ ✅ set in `.env`; add it to Streamlit secrets via `streamlit-secrets-add.toml`.
4. Approval of the specific 1–2 demo tasks (restaurant, time, name) before anything is sent.
