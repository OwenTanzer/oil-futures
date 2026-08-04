# Oil Futures — Project Documentation

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

**MediaFlow-Ingest**
Polls RSS feeds on a schedule. Filters for relevance. Deduplicates. Appends to
running log. State is persisted across runs in mediaflow_seen.json.

**MediaFlow-Classifier / Processor v0** (`mediaflow_classify.py`)
Reads `mediaflow_items.json`, sends only unclassified items to Claude Haiku,
and writes arc, short summary, and conflict flag to `mediaflow_classified.json`.
Current implementation batches 30 items with up to 4 concurrent API calls,
validates model output before persistence, and uses `UNMAPPED` for unrelated
feed noise. Event-level clustering and cross-item contradiction detection remain
future processor work.

### Narrative Arcs (initial set)
- **Kinetic** — military incidents, strikes, missile launches, naval movements, drone activity
- **Diplomatic** — government statements, negotiations, JCPOA/nuclear talks, UN/IAEA
- **Strait/Shipping** — tanker diversions, Hormuz traffic, drone/mining threats, war risk insurance
- **Market** — futures moves, physical differentials, shipping rates, positioning data
- **IEA/Supply** — IEA/EIA communications, OPEC+ releases, inventory and production data

Operational bucket:
- **UNMAPPED** — unrelated/noisy feed items retained for auditability, not a crisis arc

Actor/topic view:
- **Russia** — cross-arc dashboard filter for Russian exports and sanctions, Ukrainian
  attacks on Russian energy infrastructure, OPEC+ policy, and related logistics.
  Russia remains a view over the event-type arcs rather than a separate classifier arc.

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
| russia oil exports sanctions | ...q=russia+oil+exports+sanctions...                          | Russian exports, sanctions, and price-cap effects |
| ukraine russian energy | ...q=ukraine+russian+oil+refinery+pipeline...                       | Attacks and outages affecting Russian energy infrastructure |

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

**TIER 5 — Dead / Paywalled**
| Source               | Notes                                                              |
|----------------------|--------------------------------------------------------------------|
| Reuters              | RSS no longer publicly accessible                                  |
| AP                   | HTTP 404                                                           |
| Platts / Lloyd's     | Paywalled ($1k+/mo) — Phase 2 consideration                        |

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
| Bing: russia oil exports | bing.com/news/search?q=russia+oil+exports+sanctions&format=rss | ADDED | Russian exports and sanctions |
| Bing: ukraine russian energy | bing.com/news/search?q=ukraine+russian+oil+refinery+pipeline&format=rss | ADDED | Russian refinery and pipeline disruptions |
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
- `mediaflow_log.txt` — running human-readable log of all collected items
- `mediaflow_seen.json` — persisted URL state for deduplication across runs

### Design Principles
- Each arc is a clean chronological list — one entry per distinct event, not per article
- Contradictions are flagged explicitly, not silently resolved
- Output is minimalist: date, arc, one-line summary, source(s), confidence flag
- The cross-checking mechanism must distinguish duplicate reporting from genuine conflict
- Ingest dedup handles full duplicate articles with URL/title fingerprints;
  event-level clustering remains the processor's responsibility.

### Status
Ingest + classifier + dashboard operational as of 2026-06-08.
1406 items collected and classified. Dashboard live at localhost:8501.
Collector and classifier both use bounded parallelism; dashboard display counts
now expose item-limit truncation and unmapped records.

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
