# Oil Futures — Project Documentation

---
## Security

The dashboard (`mediaflow_app.py`, all modes: newscenter/terminal/chat/breaking_news)
is gated by `auth.py::require_password()` — a single shared password read from the
`MEDIAFLOW_APP_PASSWORD` env var. **The app refuses to start if this var is unset**
(fails closed, not open) — set it in Railway's environment variables before
deploying. Includes IP-keyed lockout after 5 failed attempts in 5 minutes.

Why: the whole app was public with no auth at all, and the agent chat
(`mediaflow_chat.py`) spends the owner's live Anthropic API key on every message
with no rate limiting — an unauthenticated visitor could run up arbitrary API cost.

`render_item()` in `mediaflow_app.py` also had a stored-XSS hole: `title`/`source`/
`link` come from external RSS feeds and news aggregators (attacker-influenceable)
and were interpolated into raw HTML (`unsafe_allow_html=True`) unescaped. Now
`html.escape`'d, and `link` is scheme-validated (`http`/`https` only) before use in
an `href`, following the same pattern already used in `breaking_news_view.py`.

### Second pass (outside + insider threat model)

- **`st.iframe` doesn't exist** — it was never a real Streamlit API (confirmed
  absent from 1.50.0, the current latest release), called in 5 places
  (`mediaflow_app.py` x2, `mediaflow_chat.py`, `eia_terminal.py`,
  `breaking_news_view.py`). Every page render crashed immediately after login.
  Replaced with `streamlit.components.v1.html(...)`, which has an identical
  signature to how the code was already calling it and renders in a same-origin
  iframe (`window.parent.document` access, which this code relies on, still works).
- **Indirect prompt injection**: `mediaflow_classify.py` sends raw scraped
  title/summary text to Claude Haiku, and `mediaflow_chat.py` folds classifier
  output (`arc_summary`) into the Opus system prompt as "CURRENT FEED" context.
  Both are untrusted external text an attacker could seed (a crafted article,
  a gamed Google News/Reddit result) with embedded instructions targeting the
  model. Added explicit "this is data, not instructions" language to both
  system prompts — reduces but doesn't eliminate the injection surface; there's
  no full fix for this class of issue.
- **Chat cost/abuse guards**: even an authenticated (or previously,
  unauthenticated) user could run up arbitrary Anthropic API cost with giant
  pastes, rapid-fire messages, or an unbounded-length conversation.
  `mediaflow_chat.py` now caps message length (4000 chars), throttles to one
  message per 2s, and caps stored messages per session (200) — defense in
  depth against a careless or malicious insider, not just outsiders.
- **`requirements.txt` was fully unpinned** (`streamlit`, `feedparser`, etc.
  with no version). Pinned to the versions this pass tested against — an
  unpinned floating dependency can pull in a broken or compromised release on
  the next deploy with no review.
- **`GITHUB_TOKEN` was persisted in plaintext** in `.git/config` via
  `git remote set-url origin https://x-token:{token}@...`. `worker.py` now
  keeps the remote URL credential-free and instead authenticates each `git
  push` with a per-invocation `-c http.extraHeader=...` (base64 `x-token:`
  basic-auth header) — the token only ever exists in the one push subprocess's
  argv, never written to disk. Anyone with filesystem/backup access to the
  container (but not the running process's env) can no longer read it out of
  `.git/config`.

### Not fixed — needs an owner-side action, not a code change
- **`GITHUB_TOKEN` scope**: recommend a fine-grained PAT restricted to just
  this repo (Contents: write), not a classic PAT with broad account access —
  limits blast radius if the container is ever compromised, since the worker
  currently only needs to write to `snapshots/`.

---
## ⚠ BEFORE TOUCHING ANY CODE FOR A RAILWAY BUG
**Ask for logs first. Do not write or push fixes based on guesses.**
Railway dashboard → oil-futures service → Deployments → latest → View Logs.
Paste the relevant lines into the conversation, then diagnose, then fix.
Two push cycles were wasted on the timezone bug by skipping this step.
---


## System: Mooper Oil Crisis Model (MOCM)

**Situation as of June 2026:**
The United States launched Operation Epic Fury against Iran approximately 100 days ago.
This is an active war, not a latent risk. The Strait of Hormuz is in a state of
near-closure. Oil tankers are functionally stuck. Iranian ballistic missiles struck
US allies Bahrain and Kuwait on June 6. Iran launched missiles at Israel on June 7,
calling it a "warning." US forces have downed Iranian drones targeting Hormuz shipping
traffic. Iran has partially closed its western airspace. Meanwhile, US equity markets
are still hitting record highs, apparently pricing in AI euphoria over war risk.

The gap between physical reality and market pricing is the central problem this
system is built to measure. That gap is currently wide, and it is closing.

---

## System Architecture

Two layers, operating in parallel and feeding into each other.

---

## Layer 1: ForcingFunction

**Purpose:** Identify and weight the variables whose activation has direct causal
impact on a regime shift in the oil futures market — from speculative selling
(paper-driven, risk-off) to physical buying (real supply disruption, basis-driven).

This is not a prediction model. It is a measurement instrument. Its job is to
quantify how far the transition has progressed and what would accelerate or
reverse it from here.

### Key Conceptual Distinctions

- **Speculative selling:** Managed money / CTA positioning, risk-off flows, momentum shorts
- **Physical buying:** Refiner demand, SPR restocking, cargo diversion, backwardation-driven
  storage economics, force majeure on supply routes
- **Character change:** The point at which physical necessity overrides speculative
  positioning — visible in term structure (prompt premium), physical differentials
  widening, spot/futures basis behavior, and shipping rate spikes

### Methodology: Two-Axis Calibration

**Axis 1 — Historical (What caused reliable consequences in past crises?)**
Go back to major geopolitically-driven oil supply disruption events. For each,
establish what variables moved, at what levels, and with what market consequences.
Use this to derive the variable taxonomy, thresholds, and relative weights.
Reference episodes (priority order):
  - 1973 Arab Oil Embargo
  - 1980-88 Iran-Iraq War / Tanker War (most structurally similar)
  - 1990-91 Gulf War (fastest inventory depletion on record)
  - 2011-12 Iran sanctions + Hormuz closure threat
  - 2019 Gulf of Oman tanker seizures + Aramco drone strikes
  - 2020 Soleimani assassination (useful as a *false* regime-change case)

**Axis 2 — Empirical (What do current observable conditions actually show?)**
Map each historically-validated variable to a publicly available or reliably
inferable real-time data source. Score current state against calibrated thresholds.

**Epistemological posture:** Applied historical science, not algorithmic trading.
The human analyst is the final inference engine. The model produces the best
possible structured evidence. ML methods are on the table where task requirements
justify them — the criterion is whether a method produces insight that earns
its complexity.

### Two Sub-Problems

1. **Variable Discovery + Data Access** — what belongs in the model and how to read it
2. **Threshold + Weight Calibration** — at what level does each variable become
   significant, and how much does it contribute to the overall forcing signal

### Status
Not yet started. ForcingFunction variable set will be informed by what MediaFlow
has already revealed about current physical conditions. The near-closure of Hormuz
and active US-Iran kinetic exchange are the baseline state, not hypotheticals.

---

## Layer 2: MediaFlow

**Purpose:** Monitor, collate, organize, and dynamically represent the news feed.
Primary real-time input layer for the ForcingFunction. Makes the schizophrenic
headline cycle legible by separating it into distinct narrative arcs, each with
its own clean chronology. Contradictions between arcs are preserved and flagged,
not resolved.

### Two Sub-Components

**MediaFlow-Ingest** (`rss_collect.py`)
Polls RSS feeds on a schedule. Filters for relevance. Deduplicates against
`mediaflow_seen.json`. Appends human-readable entries to `mediaflow_log.txt`
and structured records (with stable 12-char MD5 IDs) to `mediaflow_items.json`.
Fully incremental — skips any URL/fingerprint already in the seen set.

**MediaFlow-Classifier** (`mediaflow_classify.py`)
Reads `mediaflow_items.json`, diffs against already-classified IDs in
`mediaflow_classified.json`, and sends only new items to the configured LLM in
batches of 30, with up to 4 concurrent API requests. Each item is assigned an
arc, a one-sentence factual summary, and a conflict flag. Writes merged results
back to `mediaflow_classified.json`. Fully incremental; already-classified items
are skipped, but malformed historical rows are repaired without an API call.

Classifier API notes:
- Payload JSON is compacted before sending to reduce token/byte overhead.
- Batch calls retry with backoff.
- Model output is validated before persistence; null/invalid arcs are repaired.
- `UNMAPPED` is an explicit arc for unrelated feed noise.
- Next efficiency frontier: split fast arc labeling from slower summary generation.

Classifier provider configuration:
- `CLASSIFIER_PROVIDER`: `anthropic` (default), `openai`, or `gemini`.
  The aliases `claude`, `chatgpt`, and `google` are also accepted.
- `CLASSIFIER_MODEL`: optional model override. Provider-specific
  `ANTHROPIC_MODEL`, `OPENAI_MODEL`, or `GEMINI_MODEL` settings are used next.
- API keys: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, or `GEMINI_API_KEY`
  (`GOOGLE_API_KEY` is also accepted for Gemini).
- `CLASSIFIER_API_TIMEOUT_SECONDS`: per-request timeout, default 90 seconds.
- `CLASSIFIER_KEYS_FILE`: optional local env-file path. The legacy
  `~/.claude/keys.env` location remains the default local fallback.

Provider HTTP/SDK details are isolated in `classifier_providers.py`; batching,
retry, parsing, validation, and persistence remain provider-independent.

Arc taxonomy is a single `ARCS` list at the top of `mediaflow_classify.py`.
Add or remove arc names there; the system prompt rebuilds automatically.

**MediaFlow-Dashboard** (`mediaflow_app.py`)
Streamlit app (white background). Tabs: All | Kinetic | Diplomatic |
Strait/Shipping | Market | IEA/Supply | Other/Unmapped when needed. Items
newest-first, conflict items flagged with ⚡. Tab labels show visible/total
counts, and truncated tabs explicitly state how many older items are hidden by
the item limit. Sidebar: single "Update feed" button runs collect then classify
in sequence. Optional auto-refresh uses a non-blocking browser-side reload timer.
Run with: `python -m streamlit run mediaflow_app.py`

### Narrative Arcs (initial set)
- **Kinetic** — military incidents, strikes, missile launches, naval movements, drone activity
- **Diplomatic** — government statements, negotiations, JCPOA/nuclear talks, UN/IAEA
- **Strait/Shipping** — tanker diversions, Hormuz traffic, drone/mining threats, war risk insurance
- **Market** — futures moves, physical differentials, shipping rates, positioning data
- **IEA/Supply** — IEA/EIA communications, OPEC+ releases, inventory and production data

Operational bucket:
- **UNMAPPED** — unrelated/noisy feed items retained for auditability, not a crisis arc

### Data Sources Master List

**TIER 1 — Confirmed Live (probed 2026-06-07)**
| Source               | URL                                         | Notes                                      |
|----------------------|---------------------------------------------|--------------------------------------------|
| Al Jazeera           | aljazeera.com/xml/rss/all.xml               | Good tags; summaries brief                 |
| Middle East Eye      | middleeasteye.net/rss                       | Highest signal density; live-blog HTML     |
| BBC World            | feeds.bbci.co.uk/news/world/rss.xml         | Low hit rate; some false positives         |
| Guardian World       | theguardian.com/world/rss                   | Best field set; rich tags                  |
| France24 Middle East | france24.com/en/middle-east/rss             | Best summary quality; dedicated ME feed    |

**TIER 2 — Probed 2026-06-07**
| Source               | URL                                         | Status  | Notes                                       |
|----------------------|---------------------------------------------|---------|---------------------------------------------|
| Mehr News (IRGC)     | en.mehrnews.com/rss                         | LIVE    | 29/30 relevant; FULL BODY in feed           |
| IRNA (Iranian state) | en.irna.ir/rss                              | LIVE    | 29/30 relevant; FULL BODY in feed           |
| OilPrice.com         | oilprice.com/rss/main                       | LIVE    | 9/15 relevant; energy-specific              |
| Iran International   | iranintl.com/feed                           | LIVE*   | 50 entries, 0 relevant — likely video feed; find text RSS |
| Tasnim News          | tasnimnews.com/en/rss                       | DEAD    | All URLs failed                             |
| Anadolu Agency       | aa.com.tr/en/rss/...                        | DEAD    | 404/400 on all tried paths                  |
| Arab News            | arabnews.com/rss.xml                        | BLOCKED | 403 on .com paths; Arab News PK RSS salvaged |
| Arab News PK         | arabnews.pk/rss.xml                         | LIVE    | 10 entries, 3 relevant; Saudi-family mirror |

**TIER 3 — Probed 2026-06-07**
| Source               | URL                                         | Status  | Notes                                       |
|----------------------|---------------------------------------------|---------|---------------------------------------------|
| DW World             | rss.dw.com/rdf/rss-en-all                   | LIVE    | 146 entries, 17 relevant; European lens     |
| RFE/RL               | rferl.org/api/epiqq                         | LIVE    | 20 entries, 8 relevant; best analytical depth |
| Times of Israel      | timesofisrael.com/feed/                     | LIVE    | 15 entries, 2 relevant; Israeli military framing |
| Haaretz              | haaretz.com/...                             | DEAD    | 301 redirect + paywalled                    |
| Gulf News            | gulfnews.com/rss                            | DEAD    | 404 all paths                               |
| Gulf Today News      | gulftoday.ae/rssFeed/55/                    | LIVE    | 50 entries, 22 relevant; UAE/Gulf substitute |
| Gulf Today Business  | gulftoday.ae/rssFeed/52/                    | LIVE    | 50 entries, 22 relevant; market/trade lens  |
| Rudaw                | rudaw.net/english/rss                       | DEAD    | 200 but empty on all paths                  |
| Jerusalem Post       | jpost.com/rss/...                           | STALE   | Returns entries from June 2025              |

**GOOGLE NEWS RSS — Aggregator Layer (probed 2026-06-07)**
No API key. Free. 100 results per keyword query. Solves wire gap, Gulf gap,
Tasnim/Anadolu/PressTV gaps simultaneously. Source field populated — usable
for arc pre-classification. Link format is Google redirect URLs, not direct
article URLs. Deduplication across queries is the primary integration challenge.

| Query                  | URL pattern                                                        | Key sources surfaced                        |
|------------------------|--------------------------------------------------------------------|---------------------------------------------|
| iran hormuz            | news.google.com/rss/search?q=iran+hormuz&hl=en-US&gl=US&ceid=US:en | Fortune, CNN, CNBC, ISW, i24, NYT, WSJ      |
| iran strait oil        | ...q=iran+strait+oil...                                            | Reuters, Bloomberg, Al Jazeera, PBS, BBC    |
| IRGC missile           | ...q=IRGC+missile...                                               | Tasnim, PressTV, Gulf News, Anadolu, IRNA   |
| hormuz tanker          | ...q=hormuz+tanker...                                              | Reuters, Bloomberg, FT, gCaptain, Marine Insight |
| iran nuclear deal      | ...q=iran+nuclear+deal...                                          | NYT, WSJ, Al Jazeera, Times of Israel, CNBC |
| hormuz war risk insurance | ...q=hormuz+war+risk+insurance...                              | Insurance premiums, shipowner risk pricing |
| hormuz AIS tanker      | ...q=hormuz+AIS+tanker...                                          | Dark tanker/AIS gap reporting              |
| brent wti iran hormuz  | ...q=brent+wti+iran+hormuz...                                      | Futures/market reaction                    |
| OPEC spare capacity hormuz | ...q=OPEC+spare+capacity+hormuz...                            | Offset capacity and quota realism          |
| CENTCOM iran hormuz    | ...q=CENTCOM+iran+hormuz...                                        | Official US military reporting             |
| OFAC iran oil sanctions| ...q=OFAC+iran+oil+sanctions...                                    | Sanctions/enforcement arc                  |
| Kharg Jask oil terminal| ...q=Kharg+Jask+oil+terminal...                                    | Iranian export infrastructure              |
| japan china iran oil   | ...q=japan+china+iran+oil...                                       | Asia demand/reserve exposure               |

**TIER 4 — Official Data (not news, but ForcingFunction inputs)**
| Source               | URL                                         | What it provides                           |
|----------------------|---------------------------------------------|--------------------------------------------|
| EIA                  | eia.gov/rss/                                | US inventory/production data releases      |
| IEA                  | iea.org/rss/news.xml                        | International supply/demand releases       |
| US Treasury OFAC     | home.treasury.gov/rss                       | Sanctions announcements                    |
| CENTCOM              | centcom.mil (press releases)                | Official US military strike announcements  |

**Official Layer — Added 2026-06-08**
| Source               | URL / Method                                | Status / Notes                             |
|----------------------|---------------------------------------------|--------------------------------------------|
| EIA Today in Energy  | eia.gov/rss/todayinenergy.xml               | LIVE RSS; energy context and China demand/supply signals |
| EIA Press Releases   | eia.gov/rss/press_rss.xml                   | LIVE RSS; official EIA releases            |
| Japan PMO            | japan.kantei.go.jp/index-e2.rdf             | LIVE RSS; includes Middle East ministerial actions |
| OFAC Recent Actions  | ofac.treasury.gov/recent-actions            | HTML monitor; Iran sanctions/designations  |
| US Treasury Releases | home.treasury.gov/news/press-releases       | HTML monitor; sanctions/financial measures |
| White House          | whitehouse.gov briefings/actions            | HTML monitor; official US posture          |
| UKMTO Warnings       | ukmto.org/ukmto-products/warnings           | HTML monitor; intermittent blocking, retained |
| China State Council  | english.www.gov.cn/news/                    | HTML monitor; China official state signals |

CENTCOM, MARAD, OPEC, State Department, Japan MOFA, Japan METI, China MOFCOM,
Xinhua, and China Daily did not yield clean live RSS in this pass. Keep covered
through Google/Bing/NewsAPI unless a dedicated parser is added.

**NEWSAPI (live key in keys.env)**
| Queries                                           | Notes                                              |
|---------------------------------------------------|----------------------------------------------------|
| iran hormuz, hormuz tanker, iran nuclear deal,    | 100 req/day free tier; ~12-24hr delay; source      |
| iran oil sanctions                                | blocklist applied; adds CNA, Forbes, India, CBC    |

**TIER 5 — Dead / Paywalled / Broken in Production**
| Source               | Notes                                                              |
|----------------------|--------------------------------------------------------------------|
| Reuters              | RSS no longer publicly accessible                                  |
| AP                   | HTTP 404                                                           |
| Platts / Lloyd's     | Paywalled ($1k+/mo) — Phase 2 consideration                        |
| IRNA                 | Confirmed live June 7 (29/30 relevant, full body) but 12s read timeout on every production cycle; 0 items ever collected. Re-probe if Mehr News degrades. May need User-Agent spoofing or mirror URL. |
| Tasnim News          | All URLs failed during probe                                       |
| Anadolu Agency       | 404/400 on all tried paths                                         |
| Haaretz              | 301 redirect + paywalled                                           |
| Gulf News            | 404 all paths                                                      |
| ISW                  | 301 redirect — correct URL unknown                                 |

---

### Additional Feeds — To Probe (prioritized)

Identified after Google News RSS discovery — sources missed by outlet-first thinking.

**Priority 1 — Search engine aggregators (probed 2026-06-07)**
| Source              | URL                                               | Status  | Notes                                    |
|---------------------|---------------------------------------------------|---------|------------------------------------------|
| Bing: iran hormuz   | bing.com/news/search?q=iran+hormuz&format=rss     | LIVE    | 12/12 relevant; best summary quality     |
| Bing: hormuz tanker | bing.com/news/search?q=hormuz+tanker&format=rss   | LIVE    | 11/11 relevant                           |
| Bing: IRGC missile  | bing.com/news/search?q=IRGC+missile&format=rss    | LIVE    | 12/12 relevant                           |
| Bing: hormuz war risk | bing.com/news/search?q=hormuz+war+risk+insurance&format=rss | LIVE | Insurance cost summaries |
| Bing: hormuz AIS tanker | bing.com/news/search?q=hormuz+AIS+tanker&format=rss | LIVE | AIS/dark tanker summaries |
| Bing: brent WTI iran | bing.com/news/search?q=brent+WTI+iran&format=rss | LIVE | Market price summaries |
| Bing: CENTCOM iran hormuz | bing.com/news/search?q=CENTCOM+iran+hormuz&format=rss | LIVE | US military summaries |
| Bing: japan china iran oil | bing.com/news/search?q=japan+china+iran+oil&format=rss | LIVE | Asian exposure summaries |
| Yahoo News          | news.yahoo.com/rss/                               | SKIP    | 2/50 relevant; no summaries; low value   |

**Priority 2 — Specialist direct RSS (probed 2026-06-07)**
| Source         | URL                          | Status  | Notes                                         |
|----------------|------------------------------|---------|-----------------------------------------------|
| gCaptain       | gcaptain.com/feed/           | LIVE    | 9/12 relevant; Reuters/Bloomberg wire in feed |
| Marine Insight | marineinsight.com/feed/      | LIVE    | 11/15 relevant; FULL BODY                     |
| ISW            | understandingwar.org/feed    | 301     | Redirect — needs correct URL                  |

**Priority 3 — Reddit (probed 2026-06-07)**
| Source          | URL                            | Status  | Notes                                      |
|-----------------|--------------------------------|---------|--------------------------------------------|
| r/iran          | reddit.com/r/iran/.rss         | LIVE    | 25/25 relevant; diaspora + counter-narrative |
| r/worldnews     | reddit.com/r/worldnews/.rss    | LIVE    | 9/25 relevant; community live-blog         |
| r/energy        | reddit.com/r/energy/.rss       | LIVE    | 8/25 relevant; demand destruction signal   |
| r/geopolitics   | reddit.com/r/geopolitics/.rss  | MONITOR | 6/25 relevant; lower signal density        |

**Priority 4 — Financial/market RSS (probed 2026-06-07)**
| Source       | URL                                           | Status  | Notes                              |
|--------------|-----------------------------------------------|---------|-------------------------------------|
| MarketWatch  | feeds.marketwatch.com/marketwatch/topstories  | MONITOR | 1/10 relevant; low volume          |
| Seeking Alpha| seekingalpha.com/feed/tag/oil-gas             | DEAD    | 404                                |

**Priority 5 — Think tanks (probed 2026-06-07)**
| Source    | Status  | Notes                    |
|-----------|---------|--------------------------|
| RAND      | BLOCKED | 403                      |
| CSIS      | DEAD    | 404                      |
| Carnegie  | EMPTY   | 200 but 0 entries        |
| Brookings | DEAD    | 302 redirect, empty      |

---

**Gulf/Saudi Gap**
Al Arabiya and Arab News `.com` both return blanket 403 — blocking at
network/WAF level, not user-agent. Browser spoofing does not help. A salvage
pass found Arab News PK (`arabnews.pk/rss.xml`) and Gulf Today News/Business
RSS as live substitutes. Google News and NewsAPI still remain useful for Gulf
News, Al Arabiya, and Arab News `.com` content that cannot be fetched directly.

**TODO — Telegram**
High-value OSINT accounts (TankerTrackers, OSINTdefender, naval monitors, shipping
trackers) often break kinetic and strait events before any RSS feed. Telegram has
no official API rate limits for public channels. Explore as Phase 2 real-time layer
— likely the fastest signal source for the kinetic and strait arcs.

**MarineTraffic**
Free API tier for vessel position data. Actual tanker traffic volume through Hormuz
is a direct ForcingFunction variable, not just a news item. Probe separately.

### Files
- `rss_collect.py` — collector; run manually or on scheduler
- `mediaflow_classify.py` — provider-neutral incremental parallel arc classifier
- `classifier_providers.py` — Anthropic, OpenAI, and Gemini API adapters
- `mediaflow_app.py` — Streamlit dashboard with visible/total tab counts
- `mediaflow_items.json` — structured item store with stable IDs (collector output)
- `mediaflow_classified.json` — arc-classified items (classifier output, dashboard input)
- `mediaflow_log.txt` — human-readable running log
- `mediaflow_seen.json` — URL/fingerprint dedup state
- `explore/` — probe scripts and outputs from feed discovery phase (reference only)

### Breaking News (Hormuz Flow Watch reports)

Fourth dashboard destination (`breaking_news` mode), backed by the Hormuz Flow
Watch Reports Google Doc rather than the MediaFlow classification pipeline.

- `breaking_news.py` — pure parser/source module (no Streamlit dependency):
  splits the doc's plain-text export on `HFW-` report headings, extracts
  required sections by label (not position, since the live doc mixes two
  historical report templates), and fetches the doc via its public
  `export?format=txt` endpoint.
- `breaking_news_view.py` — Streamlit renderer; routes off
  `st.session_state.mode == "breaking_news"`, polls every ~60s while the view
  is open, caches the last good parse across transient fetch failures.
- `breaking_news_test.py` — plain-assert parser test script (no pytest; this
  repo has no test framework). Run with `python breaking_news_test.py`.
  Includes a frozen snapshot of the live 18-report document as a
  compatibility baseline.
- `breaking_news_view_test.py` — plain-assert tests for the view layer's
  state logic: `compute_selection()` (auto-advance/preserve-selection) and
  `_do_fetch_and_reparse()`'s cache-preservation behavior, via a minimal fake
  session-state object. Run with `python breaking_news_view_test.py`.
- `breaking_news_fixture_live.txt` — that frozen snapshot fixture.

**Configuration:**
- `BREAKING_NEWS_DOC_ID` (optional) — Google Doc ID for the source document.
  Defaults to the current Hormuz Flow Watch Reports doc if unset.
- `BREAKING_NEWS_EXPORT_URL` (optional) — full export URL override, if the
  source ever needs to point somewhere other than a plain doc-ID export.
- **Required manual step:** the source Google Doc must be shared as
  "Anyone with the link can view" — the app fetches it unauthenticated via
  the public export endpoint (no OAuth/service account/credential JSON is
  used or required for this feature).

### Location Status Monitor (Hormuz Node Status snapshots)

Fifth dashboard destination (`node_status` mode), reached via the 4th nav
button (`□`). Backed by the Hormuz Node Status Google Doc — independent of both
the MediaFlow pipeline and `breaking_news.py`.

- `node_status_view.py` — parser and Streamlit renderer in one module. Splits
  the doc's plain-text export on `HSN-` report headings, selects the
  lexicographically-latest report ID (`YYYYMMDD-HHMM`, so lex order is
  chronological order), and renders each node in the `Node status:` section as
  a color-coded card (Category 1 red / 2 orange / 3 amber / 4 purple). Polls
  every ~300s while the view is open. Node lines are pipe-delimited:
  `Name | Category N | Status | Note`, where the note is optional.
- `node_status_test.py` — plain-assert tests (no pytest) covering the parser,
  all `fetch_hsn_text` error paths, card-render escaping, and
  `_do_fetch_and_reparse()`'s cache-preservation behavior via a fake
  session-state object, using the same pattern as `breaking_news_view_test.py`.
  Run with `python node_status_test.py`.
- `node_status_fixture_live.txt` — frozen plain-text export of the real HSN doc
  (4 reports, 17 nodes in the newest), used as a compatibility baseline exactly
  like `breaking_news_fixture_live.txt`. **Regenerate this whenever the doc's
  format changes**, and never let the synthetic fixtures stand in for it: the
  feature originally shipped with a heading separator that matched nothing in
  the real document, and a fully synthetic 40-test suite passed anyway.

**Source format (verified against the live doc, Aug 2026).** Headings are
`HSN-YYYYMMDD-HHMM — summary — timestamp` using an **em dash**, not `---`;
Google Docs autocorrects `---` as you type, which is the same thing HFW hit.
The parser accepts em dash, en dash and `---` so an author toggling autocorrect
cannot silently break the view. Node lines are
`Name | Category N | Status | Note`, note optional, and `No change` is a common
3-field form. Node names legitimately contain apostrophes, en dashes, slashes
and colons (`Ras Tanura/Ju'aymah`, `Saudi East–West Pipeline`, `Kuwait: Al-Zour`).

**Source-drift handling.** The doc is hand-maintained, so the parser assumes it
will drift and refuses to fail silently — a monitoring view that renders an
empty grid confidently is worse than one that says it lost the thread:
- A parse yielding zero nodes never replaces a cache that has nodes. It keeps
  the last good snapshot and flags it stale, matching `breaking_news_view.py`.
  A renamed `Node status:` heading therefore degrades to "showing last good
  data", not to a blank page.
- Pipe-bearing lines inside the node section that fail to parse are counted
  (`HsnSnapshot.skipped_lines`) and surfaced in the view rather than dropped.
- Delimiters are split on the bare `|`, not `" | "`, so inconsistent spacing in
  the doc doesn't silently drop every node.
- `NEXT_SECTION_RE` uses the same character class and 80-char bound as
  `breaking_news.py`'s `LABEL_RE`. A narrower class fails to terminate the node
  section on real headers like `Top 5 sources:`, letting later pipe-bearing
  prose be ingested as phantom nodes.
- Report IDs are matched as `\d{8}-\d{4}`, not a loose `[\w-]+`. That is what
  makes the lexicographic "latest" sort equivalent to a chronological one — a
  loose pattern lets a stray `HSN-DRAFT-…` heading sort above every real report.
- The category field is matched anchored (`^Category\s+([1-4])$`). Matching the
  first digit anywhere read `Category 12` as Category 1 and accepted a bare `3`.
- A duplicate report ID resolves to the **last** copy in document order — the
  natural way a correction is made to a hand-maintained doc is to append a
  re-issue — and the duplicate count is surfaced in the view. (This differs
  deliberately from `breaking_news.py`, which keeps the first copy and emits a
  `duplicate_id` error; that view lists all reports, this one shows only the
  newest, so a superseded copy winning would be silently wrong.)

**Timezones.** `tz_convert.py` holds the shared browser-side converter: the
server emits `<span data-utc="…">original text</span>` and the script rewrites
it into the viewer's zone. This was previously welded into
`mediaflow_app.py::inject_tz_converter()` together with a 2-minute page-reload
timer, which is why no sub-view could use it — a forced reload inside the chat
or a report view would interrupt the reader. The two are now separate:
`tz_convert.inject_converter()` is conversion only, and `inject_tz_converter()`
layers the reload on top for the newscenter. (The converter also now disconnects
its previous MutationObserver; each Streamlit rerun used to stack another
full-document observer on the same parent document.)

`node_status_view.parse_report_timestamp()` turns the heading's human string
into that UTC instant. It **fails closed at every step** — if it cannot be
confident, it returns `None` and the doc's original string is rendered
untouched. A timestamp left in the author's zone is inconvenient; one silently
shifted to the wrong zone is a false reading on a crisis instrument. Specifics:
- Zone abbreviations map to *fixed* offsets, because the abbreviation already
  encodes the offset — "EDT" means UTC-4 regardless of whether that date is
  really in DST, so the doc is honoured rather than second-guessed.
- Ambiguous abbreviations are deliberately **absent** from the table and are
  never guessed: `AST` is Atlantic *and* Arabia, `CST` is US Central, China and
  Cuba, `IST` is India, Israel and Ireland, `BST` is Britain and Bangladesh,
  `CDT` is US Central and Cuba. On a Gulf dashboard, guessing `AST` is a silent
  7-hour error. An unrecognised zone token also blocks the report-ID fallback,
  since that fallback's own zone assumption would be equally unfounded.
- With no zone stated, the time is read in `HSN_SOURCE_TZ` (default
  `America/New_York`, via `ZoneInfo`, so DST is applied correctly). An invalid
  value degrades to UTC rather than taking the view down.
- Last resort is the report ID itself (`HSN-20260804-0803` is the same wall
  clock as "8:03 AM EDT"), read in the same source zone.

Breaking News still renders its timestamps raw. HFW headings use
`2026-07-12 15:16 EDT`, a format `parse_report_timestamp` already handles, so
adopting it there is a small follow-up rather than new work.

**Accessibility.** Badge colours are one step darker than the obvious Tailwind
ramp because badge text is white at ~11px — WCAG "small text", needing 4.5:1.
The original `#ea580c`/`#d97706` measured 3.56:1 and 3.19:1. Current values
measure 6.47 / 5.18 / 5.02 / 7.10:1. Body greys were likewise raised from
`#999`/`#aaa` (2.85:1, 2.23:1) to `#6b7280`. If these are ever restyled, keep
the ratios — categories 1–3 are adjacent hues, so the text label in the badge
is what carries the meaning for colour-blind viewers.

**Configuration:**
- `HSN_DOC_ID` (optional) — Google Doc ID for the source document. Defaults to
  the current Hormuz Node Status doc if unset.
- `HSN_EXPORT_URL` (optional) — full export URL override.
- **Required manual step:** as with Breaking News, the source Google Doc must be
  shared as "Anyone with the link can view" — fetched unauthenticated via the
  public export endpoint.

### Design Principles
- Each arc is a clean chronological list — one entry per distinct event, not per article
- Contradictions are flagged explicitly, not silently resolved
- Output is minimalist: date, arc, one-line summary, source(s), confidence flag
- The cross-checking mechanism must distinguish duplicate reporting from genuine conflict
- Ingest dedup handles full duplicate articles with URL/title fingerprints;
  event-level clustering remains the processor's responsibility.

### Status
Ingest + classifier + dashboard all operational as of 2026-06-08.
1406 items collected and classified. Dashboard live at localhost:8501.
Collector fetches feeds/pages/API queries in bounded parallel I/O. Classifier
uses batches of 30 with up to 4 concurrent API calls and repairs malformed
historical labels locally. Dashboard auto-refresh is non-blocking and display
counts now expose hidden/truncated items.

Next: review classification quality on Kinetic arc; tune relevance filter
(some general news/feed noise now lands in UNMAPPED); consider splitting fast
arc labeling from slower summary generation; begin ForcingFunction layer.

---

## Architecture Notes

- ForcingFunction consumes structured signal inputs — some from MediaFlow, some from market data
- MediaFlow operates independently and can be read without ForcingFunction context
- Python primary language unless otherwise specified
- Data sources documented as added
- All outputs human-readable and suitable for rapid situational assessment

---

## Probe Records

- `gdelt_explore.py` + `GDELT_output.txt` — GDELT DOC API probe; concluded unsuitable
  for real-time use (rate limits, metadata only). Better for historical event lookup.
- `rss_explore.py` + `RSS_output.txt` — RSS feed probe; confirmed as primary backbone.
  `rss_collect.py` operational.
