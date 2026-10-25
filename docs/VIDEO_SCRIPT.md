# Demo video script (≤ 3:00)

One recording serves both hackathons; swap the 15-second "sponsor beat" (marked ⇄) per submission.
Target length **2:45**. Record the hosted demo at 1080p, browser zoom 110%, sidebar open.
No copyrighted music (YouTube Audio Library or silence). Upload to YouTube as **Public** or **Unlisted**, whichever each hackathon's rules accept (Nebius: public).

**Before recording:** start a fresh session. Check the sidebar shows four 🟢 lines and the usage panel. Have the prompts below in a text file to paste.

| Time | Screen | Voice-over |
|---|---|---|
| 0:00–0:15 | Title card: "Tastebound — the travel agent that knows your taste" | "Travel apps get you there. They don't know what *you'll* love when you arrive, and they can't tell you if the line is closed this weekend. Tastebound does both." |
| 0:15–0:40 | Type: `Westminster, London` → `Manchester Piccadilly` → `tomorrow at 9am` → `balanced please` | "It's a chat agent. Every turn is parsed by **NVIDIA Nemotron Nano on Nebius Token Factory** into structured intent, with thinking switched off so it answers in under half a second." |
| 0:40–1:15 | Type: `I love Joy Division, Wes Anderson films and good coffee`. Wait for the plan; scroll through journey → taste picks → live disruption | "Now the reasoning model, **Nemotron Super**, takes over. It decides to call two tools: **Qloo's taste graph**, to turn my favourite band and films into places near Piccadilly ranked by cultural affinity, and **Tavily**, to check for live disruption on this route." |
| 1:15–1:30 | Open the 🛠️ *Agent tool calls* expander | "Nothing is hidden: here are the exact tool calls the agent made." |
| 1:30–1:55 | Type: `Any rail strikes or engineering works I should know about this week?` Hover the **Sources** links | "Live answers are sourced. Tavily searches are limited to the past week, and the code appends the links whenever the model doesn't cite them, so every claim is checkable." |
| 1:55–2:15 | Type: `Where could I get brunch near my destination that suits my taste?` | "Follow-ups stay in context. Same tastes, new vibe: Qloo's brunch tag plus my interests, and the agent explains *why* each place fits me." |
| 2:15–2:30 | ⇄ **Nebius cut:** cropped sidebar showing only the integration lines and model names (never the budget or usage lines). **Qloo cut:** sidebar "Your tastes" + Qloo 🟢 | ⇄ **Nebius:** "Two Nemotron models routed by job: Nano for fast calls, Super for reasoning. A hard spend cap in code refuses any call that could cross the budget. A full conversation costs about six tenths of a cent." **Qloo:** "Without Qloo, any LLM suggests the same tourist spots. With Qloo, a Joy Division fan and a Wes Anderson fan get different arrival briefs, because the agent knows what people actually love." |
| 2:30–2:45 | Usage-limits expander, then end card with repo URL + "MIT licensed" | "It's a public demo with real API bills, so every API has app-wide hourly, daily and cumulative limits, and it degrades gracefully instead of failing. Next: handing phone-only bookings to a real human. Tastebound, open source on GitHub." |

**Fallback if a live call is slow during recording:** keep the take. The app degrades to a template brief, but re-record that segment if possible, since judges should see the Nemotron answer.
