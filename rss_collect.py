"""
MediaFlow RSS collector — run manually or on a scheduler.
Each run fetches all active feeds, filters for relevance,
deduplicates against previously seen URLs/article fingerprints, and appends
new items to the running log.
"""

import feedparser
import hashlib
import requests
import json
import re
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

# ── paths ────────────────────────────────────────────────────────────────────
HERE        = Path(__file__).parent
DATA_DIR    = Path(os.environ.get("DATA_DIR", HERE))
LOG_FILE    = DATA_DIR / "mediaflow_log.txt"
STATE_FILE  = DATA_DIR / "mediaflow_seen.json"   # persists seen URLs/fingerprints
ITEMS_FILE  = DATA_DIR / "mediaflow_items.json"  # structured item store for classifier
KEYS_FILE   = Path(r"C:\Users\Owen\.claude\keys.env")

# ── active feeds (dead feeds removed after probe) ───────────────────────────
FEEDS = {
    # Tier 1
    "Al Jazeera":           "https://www.aljazeera.com/xml/rss/all.xml",
    "Middle East Eye":      "https://www.middleeasteye.net/rss",
    "BBC World":            "http://feeds.bbci.co.uk/news/world/rss.xml",
    "Guardian World":       "https://www.theguardian.com/world/rss",
    "France24 Middle East": "https://www.france24.com/en/middle-east/rss",
    # Tier 2 — Iranian state/IRGC framing (full body in feed)
    "Mehr News":            "https://en.mehrnews.com/rss",
    # IRNA (en.irna.ir/rss) — removed: confirmed live June 7 but consistently
    # times out in production (12s read timeout, 0 items ever collected).
    # Probe again if Mehr News degrades; may need User-Agent spoofing or mirror URL.
    # Tier 2 — Energy-specific
    "OilPrice.com":         "https://oilprice.com/rss/main",
    "EIA Today in Energy":  "https://www.eia.gov/rss/todayinenergy.xml",
    "EIA Press Releases":   "https://www.eia.gov/rss/press_rss.xml",
    "Japan PMO":            "https://japan.kantei.go.jp/index-e2.rdf",
    # Tier 3 — European/analytical
    "DW World":             "https://rss.dw.com/rdf/rss-en-all",
    "RFE/RL":               "https://www.rferl.org/api/epiqq",
    # Tier 3 — Israeli
    "Times of Israel":      "https://www.timesofisrael.com/feed/",
    # Gulf/Saudi perspective salvaged after direct Arab News/Gulf News failures
    "Arab News PK":         "https://www.arabnews.pk/rss.xml",
    "Gulf Today News":      "https://www.gulftoday.ae/rssFeed/55/",
    "Gulf Today Business":  "https://www.gulftoday.ae/rssFeed/52/",
    # Shipping specialists
    "gCaptain":             "https://gcaptain.com/feed/",
    "Marine Insight":       "https://www.marineinsight.com/feed/",
    # Google News search queries
    "GNews: iran hormuz":       "https://news.google.com/rss/search?q=iran+hormuz&hl=en-US&gl=US&ceid=US:en",
    "GNews: iran strait oil":   "https://news.google.com/rss/search?q=iran+strait+oil&hl=en-US&gl=US&ceid=US:en",
    "GNews: IRGC missile":      "https://news.google.com/rss/search?q=IRGC+missile&hl=en-US&gl=US&ceid=US:en",
    "GNews: hormuz tanker":     "https://news.google.com/rss/search?q=hormuz+tanker&hl=en-US&gl=US&ceid=US:en",
    "GNews: iran nuclear deal": "https://news.google.com/rss/search?q=iran+nuclear+deal&hl=en-US&gl=US&ceid=US:en",
    "GNews: hormuz war risk insurance": "https://news.google.com/rss/search?q=hormuz+war+risk+insurance&hl=en-US&gl=US&ceid=US:en",
    "GNews: hormuz AIS tanker":         "https://news.google.com/rss/search?q=hormuz+AIS+tanker&hl=en-US&gl=US&ceid=US:en",
    "GNews: brent wti iran hormuz":     "https://news.google.com/rss/search?q=brent+wti+iran+hormuz&hl=en-US&gl=US&ceid=US:en",
    "GNews: OPEC spare capacity hormuz":"https://news.google.com/rss/search?q=OPEC+spare+capacity+hormuz&hl=en-US&gl=US&ceid=US:en",
    "GNews: CENTCOM iran hormuz":       "https://news.google.com/rss/search?q=CENTCOM+iran+hormuz&hl=en-US&gl=US&ceid=US:en",
    "GNews: OFAC iran oil sanctions":   "https://news.google.com/rss/search?q=OFAC+iran+oil+sanctions&hl=en-US&gl=US&ceid=US:en",
    "GNews: Kharg Jask oil terminal":   "https://news.google.com/rss/search?q=Kharg+Jask+oil+terminal&hl=en-US&gl=US&ceid=US:en",
    "GNews: japan china iran oil":      "https://news.google.com/rss/search?q=japan+china+iran+oil&hl=en-US&gl=US&ceid=US:en",
    "GNews: russia oil exports sanctions": "https://news.google.com/rss/search?q=russia+oil+exports+sanctions&hl=en-US&gl=US&ceid=US:en",
    "GNews: ukraine russian energy":       "https://news.google.com/rss/search?q=ukraine+russian+oil+refinery+pipeline&hl=en-US&gl=US&ceid=US:en",
    # Bing News search queries
    "Bing: iran hormuz":        "https://www.bing.com/news/search?q=iran+hormuz&format=rss",
    "Bing: hormuz tanker":      "https://www.bing.com/news/search?q=hormuz+tanker&format=rss",
    "Bing: IRGC missile":       "https://www.bing.com/news/search?q=IRGC+missile&format=rss",
    "Bing: hormuz war risk":    "https://www.bing.com/news/search?q=hormuz+war+risk+insurance&format=rss",
    "Bing: hormuz AIS tanker":  "https://www.bing.com/news/search?q=hormuz+AIS+tanker&format=rss",
    "Bing: brent WTI iran":     "https://www.bing.com/news/search?q=brent+WTI+iran&format=rss",
    "Bing: CENTCOM iran hormuz":"https://www.bing.com/news/search?q=CENTCOM+iran+hormuz&format=rss",
    "Bing: japan china iran oil":"https://www.bing.com/news/search?q=japan+china+iran+oil&format=rss",
    "Bing: russia oil exports": "https://www.bing.com/news/search?q=russia+oil+exports+sanctions&format=rss",
    "Bing: ukraine russian energy": "https://www.bing.com/news/search?q=ukraine+russian+oil+refinery+pipeline&format=rss",
    # Reddit
    "Reddit r/iran":        "https://www.reddit.com/r/iran/.rss",
    "Reddit r/worldnews":   "https://www.reddit.com/r/worldnews/.rss",
    "Reddit r/energy":      "https://www.reddit.com/r/energy/.rss",
}

# ── NewsAPI config ───────────────────────────────────────────────────────────
OFFICIAL_PAGES = {
    "OFAC Recent Actions":       "https://ofac.treasury.gov/recent-actions",
    "US Treasury Releases":      "https://home.treasury.gov/news/press-releases",
    "White House Statements":    "https://www.whitehouse.gov/briefings-statements/",
    "White House Actions":       "https://www.whitehouse.gov/presidential-actions/",
    "UKMTO Warnings":            "https://www.ukmto.org/ukmto-products/warnings",
    "China State Council News":  "https://english.www.gov.cn/news/",
}

OFFICIAL_LINK_RE = {
    "OFAC Recent Actions":      re.compile(r"/recent-actions/\d{8}(?:_|$)"),
    "US Treasury Releases":     re.compile(r"/news/(press-releases|featured-stories)/"),
    "White House Statements":   re.compile(r"/briefings-statements/\d{4}/"),
    "White House Actions":      re.compile(r"/presidential-actions/\d{4}/"),
    "UKMTO Warnings":           re.compile(r"/ukmto-products/warnings/"),
    "China State Council News": re.compile(r"/news/\d{6}/"),
}

NEWSAPI_URL = "https://newsapi.org/v2/everything"
NEWSAPI_QUERIES = [
    "iran hormuz",
    "hormuz tanker",
    "iran nuclear deal",
    "iran oil sanctions",
    "brent wti iran hormuz",
    "japan china iran oil",
]

MAX_WORKERS = 12
REQUEST_TIMEOUT = (4, 12)
HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 MediaFlow/1.0"}


def load_env_key(name: str) -> str:
    if os.environ.get(name):
        return os.environ[name].strip()
    if KEYS_FILE.exists():
        for line in KEYS_FILE.read_text(encoding="utf-8").splitlines():
            if line.startswith(name):
                return line.split("=", 1)[1].strip()
    return ""

# Low-quality sources to exclude from NewsAPI results
NEWSAPI_BLOCKLIST = {
    "freerepublic.com", "naturalnews.com", "breitbart.com",
    "infowars.com", "zerohedge.com", "sputnikglobe.com",
}

# ── relevance filter ─────────────────────────────────────────────────────────
KEYWORDS = [
    "iran", "hormuz", "irgc", "tehran", "persian gulf",
    "strait", "khamenei", "pezeshkian", "nuclear deal",
    "jcpoa", "enrichment", "sanctions", "arabian sea",
    "gulf of oman", "ballistic missile", "tanker seizure",
    "operation epic fury", "middle east", "mideast", "oil",
    "crude", "opec", "spr", "energy security", "shipping",
    "maritime", "vessel", "fujairah", "kuwait", "bahrain",
    "brent", "wti", "refinery", "refineries", "oil pipeline",
    "diesel", "fuel oil",
]
KEYWORD_RE = re.compile("|".join(KEYWORDS), re.IGNORECASE)

# BBC false-positive titles that match keywords but aren't crisis-relevant
EXCLUDE_RE = re.compile(
    r"world cup|football|sport|cricket|tennis|olympics",
    re.IGNORECASE
)


def is_relevant(entry: dict) -> bool:
    text = " ".join([
        entry.get("title", ""),
        entry.get("summary", ""),
        " ".join(t.get("term", "") for t in entry.get("tags", [])),
    ])
    if EXCLUDE_RE.search(text):
        return False
    return bool(KEYWORD_RE.search(text))


def load_seen() -> set:
    if STATE_FILE.exists():
        seen = set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        for item in list(seen):
            if item.startswith("article:"):
                continue
            canonical = canonical_url(item)
            if canonical:
                seen.add(f"article:url:{canonical}")
        return seen
    return set()


def save_seen(seen: set) -> None:
    STATE_FILE.write_text(
        json.dumps(sorted(seen), indent=2), encoding="utf-8"
    )


def clean_summary(raw: str) -> str:
    """Strip HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", raw or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:280]


def normalized_title(title: str, source: str = "") -> str:
    text = re.sub(r"<[^>]+>", " ", title or "")
    text = re.sub(r"\s+", " ", text).strip().lower()
    if source:
        source_text = re.escape(source.strip().lower())
        text = re.sub(rf"\s+[-|]\s+{source_text}$", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def make_item_id(link: str, title: str, source: str) -> str:
    canonical = canonical_url(link)
    key = canonical or f"{source.lower()}:{title.lower()}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def canonical_url(raw_url: str) -> str:
    parsed = urlparse(raw_url or "")
    if parsed.netloc.lower().endswith("bing.com") and parsed.path.lower().endswith("/news/apiclick.aspx"):
        target = parse_qs(parsed.query).get("url", [""])[0]
        if target:
            parsed = urlparse(target)
    if parsed.netloc.lower().endswith("news.google.com"):
        return ""
    domain = parsed.netloc.lower().removeprefix("www.")
    path = re.sub(r"/+$", "", parsed.path)
    keep_params = []
    for key, vals in parse_qs(parsed.query).items():
        if key.lower().startswith(("utm_", "fbclid", "gclid", "oc", "cmpid")):
            continue
        for val in vals:
            keep_params.append((key, val))
    query = ""
    if keep_params:
        query = "?" + "&".join(f"{k}={v}" for k, v in sorted(keep_params))
    return f"{domain}{path}{query}".lower()


def article_source(entry: dict, fallback: str) -> str:
    source = entry.get("source", {})
    if isinstance(source, dict):
        return source.get("title") or fallback
    return fallback


def article_keys(source: str, title: str, link: str) -> set[str]:
    keys = {link}
    canonical = canonical_url(link)
    if canonical:
        keys.add(f"article:url:{canonical}")
    title_key = normalized_title(title, source)
    if len(title_key) >= 35:
        keys.add(f"article:source-title:{source.lower()}:{title_key}")
        keys.add(f"article:title:{title_key}")
    return keys


def is_seen(seen: set, source: str, title: str, link: str) -> bool:
    return bool(article_keys(source, title, link) & seen)


def mark_seen(seen: set, source: str, title: str, link: str) -> None:
    seen.update(article_keys(source, title, link))


class LinkExtractor(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__()
        self.base_url = base_url
        self.links = []
        self._href = None
        self._text = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str]]) -> None:
        if tag.lower() == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._href:
            return
        title = " ".join(" ".join(self._text).split())
        if title:
            self.links.append((title, urljoin(self.base_url, self._href)))
        self._href = None
        self._text = []


def fetch_url(source: str, url: str, params: dict | None = None) -> tuple[str, str, str, bytes | None]:
    try:
        r = requests.get(url, params=params, headers=HTTP_HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return source, r.url, f"HTTP {r.status_code}", None
        return source, r.url, "", r.content
    except Exception as e:
        return source, url, str(e), None


def fetch_feed(source: str, url: str) -> tuple[str, list[dict], str]:
    source, _final_url, error, content = fetch_url(source, url)
    if error:
        return source, [], error
    feed = feedparser.parse(content)
    if feed.get("bozo_exception") and not feed.get("entries"):
        return source, [], str(feed["bozo_exception"])
    return source, list(feed.get("entries", [])), ""


def fetch_all_feeds() -> dict[str, list[dict]]:
    results: dict[str, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_feed, source, url): source
            for source, url in FEEDS.items()
        }
        for future in as_completed(futures):
            source, entries, error = future.result()
            if error:
                print(f"  [WARN] {source}: {error}")
            results[source] = entries
    return results


def fetch_official_pages(seen: set) -> list[dict]:
    items = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    pages: dict[str, tuple[str, bytes | None]] = {}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(OFFICIAL_PAGES))) as executor:
        futures = {
            executor.submit(fetch_url, source, url): source
            for source, url in OFFICIAL_PAGES.items()
        }
        for future in as_completed(futures):
            source, final_url, error, content = future.result()
            if error:
                print(f"  [WARN] {source}: {error}")
            pages[source] = (final_url, content)

    for source in OFFICIAL_PAGES:
        final_url, content = pages.get(source, (OFFICIAL_PAGES[source], None))
        if not content:
            continue
        parser = LinkExtractor(final_url)
        parser.feed(content.decode("utf-8", errors="replace"))
        for title, link in parser.links:
            link_path = urlparse(link).path
            if not OFFICIAL_LINK_RE[source].search(link_path):
                continue
            if len(title) < 12 or is_seen(seen, source, title, link):
                continue
            if not is_relevant({"title": title, "summary": ""}):
                continue
            items.append({
                "source":    source,
                "title":     title.strip(),
                "summary":   "",
                "published": now,
                "link":      link,
                "fetched":   now,
            })
            mark_seen(seen, source, title, link)
    return items


def fetch_newsapi(seen: set) -> list[dict]:
    items = []
    api_key = load_env_key("NEWSAPI_KEY")
    if not api_key:
        print("  [WARN] NewsAPI skipped: NEWSAPI_KEY not found")
        return items
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    responses: dict[str, bytes | None] = {}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(NEWSAPI_QUERIES))) as executor:
        futures = {}
        for query in NEWSAPI_QUERIES:
            params = {
                "q": query, "language": "en",
                "sortBy": "publishedAt", "pageSize": 20,
                "apiKey": api_key,
            }
            futures[executor.submit(fetch_url, f"NewsAPI '{query}'", NEWSAPI_URL, params)] = query
        for future in as_completed(futures):
            source, _final_url, error, content = future.result()
            query = futures[future]
            if error:
                print(f"  [WARN] {source}: {error}")
            responses[query] = content

    for query in NEWSAPI_QUERIES:
        content = responses.get(query)
        if not content:
            continue
        try:
            payload = json.loads(content.decode("utf-8"))
        except Exception as e:
            print(f"  [WARN] NewsAPI '{query}': {e}")
            continue
        for a in payload.get("articles", []):
            url = a.get("url", "")
            title = a.get("title", "") or ""
            desc  = a.get("description", "") or ""
            source = f"NewsAPI/{(a.get('source') or {}).get('name','?')}"
            if not url or is_seen(seen, source, title, url):
                continue
            domain = urlparse(url).netloc.lower().removeprefix("www.")
            if any(domain == b or domain.endswith("." + b) for b in NEWSAPI_BLOCKLIST):
                continue
            if not KEYWORD_RE.search(title + " " + desc):
                continue
            items.append({
                "source":    source,
                "title":     title.strip(),
                "summary":   clean_summary(desc),
                "published": a.get("publishedAt", "unknown"),
                "link":      url,
                "fetched":   now,
            })
            mark_seen(seen, source, title, url)
    return items


def fetch_new(seen: set) -> list[dict]:
    new_items = []
    feeds = fetch_all_feeds()
    for source, url in FEEDS.items():
        for entry in feeds.get(source, []):
            link = entry.get("link", "")
            title = entry.get("title", "(no title)").strip()
            item_source = article_source(entry, source)
            if not link or is_seen(seen, item_source, title, link):
                continue
            if not is_relevant(entry):
                continue
            new_items.append({
                "source":    source,
                "title":     title,
                "summary":   clean_summary(entry.get("summary", "")),
                "published": entry.get("published", entry.get("updated", "unknown")),
                "link":      link,
                "fetched":   datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            })
            mark_seen(seen, item_source, title, link)

    new_items += fetch_official_pages(seen)
    new_items += fetch_newsapi(seen)

    # sort oldest-first so the log reads chronologically
    new_items.sort(key=lambda x: x["published"])
    return new_items


def append_to_log(items: list[dict]) -> None:
    lines = []
    for item in items:
        lines.append(
            f"\n[{item['fetched']}]  {item['source'].upper()}\n"
            f"{item['title']}\n"
        )
        if item["summary"] and item["summary"] != item["title"]:
            lines.append(f"{item['summary']}\n")
        lines.append(f"{item['link']}\n")
        lines.append("-" * 68 + "\n")

    with open(LOG_FILE, "a", encoding="utf-8") as f:
        if not LOG_FILE.exists() or LOG_FILE.stat().st_size == 0:
            f.write("MEDIAFLOW - Iran/Hormuz Running News Log\n")
            f.write("=" * 68 + "\n")
        f.writelines(lines)


def run():
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"MediaFlow collect — {now}")

    started = time.perf_counter()

    seen     = load_seen()
    seen_before = len(seen)

    new_items = fetch_new(seen)

    if new_items:
        # assign stable IDs
        for item in new_items:
            item["id"] = make_item_id(item["link"], item["title"], item["source"])

        append_to_log(new_items)
        save_seen(seen)

        # append to structured items store (skip any ID already present)
        existing: list[dict] = []
        if ITEMS_FILE.exists():
            existing = json.loads(ITEMS_FILE.read_text(encoding="utf-8"))
        existing_ids = {i["id"] for i in existing}
        truly_new = [i for i in new_items if i["id"] not in existing_ids]
        if truly_new:
            existing.extend(truly_new)
            ITEMS_FILE.write_text(
                json.dumps(existing, indent=2, ensure_ascii=False), encoding="utf-8"
            )

        elapsed = time.perf_counter() - started
        print(f"  {len(new_items)} new items logged in {elapsed:.1f}s  (seen total: {len(seen)})")
        for item in new_items:
            print(f"  + [{item['source']}] {item['title'][:70]}")
    else:
        elapsed = time.perf_counter() - started
        print(f"  No new relevant items in {elapsed:.1f}s  (seen total: {seen_before})")

    print(f"  Log: {LOG_FILE}")


if __name__ == "__main__":
    run()
