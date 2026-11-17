# Distributed-Ready Residential Solar Energy Lead Crawler

An industry-standard web crawler engineered for discovering and qualifying residential solar installation intent signals and prospective buyer leads, implementing the classical distributed web crawler architecture (Alex Xu, *System Design Interview*, Figure 9-4).

---

## Architecture Overview

```
[Seed URLs / URL Frontier] ──(Politeness Queue & Residential Priority)
           │
           ▼
[HTML Downloader Pool (Async HTTPX)]
           │
           ├──▶ Option A: [Deno.js Fetch Gateway (Port 8000)]
           │      ├── Domain Allowlist Check
           │      ├── Per-Domain Politeness Rate Limiting (1.5s delay)
           │      ├── In-Memory TTL Cache (1 hour)
           │      └── Transparent 403 / 429 / 5xx Handling
           │
           └──▶ Option B: Direct HTTPX (Automatic Fallback if Gateway Offline)
           │
           └──▶ Option C: PRAW Reddit OAuth Client (for Reddit API requests)
           │
           ▼ (Decoupled asyncio.Queue)
[Parser Workers (CPU Pool)]
           ├── 1. Canonical URL Normalizer (Strips ?p=... & anchors; prevents 82% duplicate waste)
           ├── 2. Exact (SHA-256) & Near-Duplicate (SimHash Hamming ≤ 3) Content Deduplication
           ├── 3. 6-Way Content Classifier (Rejects MW scale, commercial lighting, technical repairs, DIY)
           ├── 4. Weighted Purchase Intent Scorer & Evidence Snippet Generator
           ├── 5. 4-Tier Lead Freshness Classifier (0-7d, 8-30d, 31-90d, >90d)
           └── 6. Pydantic Schema Validator
           │
           ▼
[Storage & Observability Layer]
   ├── SQLite Database (`data/crawler_storage.db`)
   ├── Individual & Consolidated JSON Exports (`leads/`)
   └── Timestamped Session Logs (`logs/crawler_YYYYMMDD_HHMMSS.log`)
```

---

## Observability & CLI Options

The crawler provides real-time progress monitoring, request latency, payload sizes, duplicate prevention counters, and database state.

### Command-Line Arguments

```powershell
# 1. Run standard crawl for N pages (e.g. 50 pages)
py main.py 50

# 2. Run with verbose DEBUG logging (shows DOM parsing, SimHash distance, and scoring breakdown)
py main.py 50 --debug

# 3. Fresh crawl: Wipe previous SQLite state and restart clean from initial seeds
py main.py 50 --fresh

# 4. Check current database state and quality distributions without crawling
py main.py --status

# 5. Run the comprehensive unit and integration test suite
py test_crawler.py
```

### Log & Output Destinations
- **Console Output**: Real-time worker activity, transport type, HTTP status, and queue depth.
- **Session Log File**: Granular logs written to [logs/crawler_YYYYMMDD_HHMMSS.log](file:///c:/Users/Lenovo/Desktop/crawl/logs).
- **Leads JSON Exports**: Single records and consolidated summaries written to [leads/](file:///c:/Users/Lenovo/Desktop/crawl/leads).
- **SQLite Database**: Persistent relational state saved in [data/crawler_storage.db](file:///c:/Users/Lenovo/Desktop/crawl/data/crawler_storage.db).

---

## Deno.js Fetch Gateway Service

The crawler includes an independent **Deno.js Fetch Gateway** service (`gateway/gateway.ts`) to centralize request handling, rate limiting, and caching.

### Running the Gateway

1. In a separate terminal, start the Deno gateway:
   ```bash
   deno run --allow-net gateway/gateway.ts
   ```
2. The service listens on port 8000 and exposes `/health` and `/fetch`.
3. In [config.py](file:///c:/Users/Lenovo/Desktop/crawl/config.py):
   ```python
   USE_FETCH_GATEWAY = True
   FETCH_GATEWAY_URL = "http://127.0.0.1:8000/fetch"
   GATEWAY_HEALTH_URL = "http://127.0.0.1:8000/health"
   FALLBACK_TO_DIRECT_HTTPX = True
   ```
4. **Startup Verification**: On startup, `HTMLDownloader` sends a `GET /health` request to verify the gateway. If healthy, requests log `Transport: DENO_GATEWAY`. If unavailable and fallback is enabled, it logs a warning and falls back to `DIRECT_HTTPX`.

---

## Precision Filtering & Deduplication

### 1. Forum Thread Canonicalization (Stops the 82% Duplicate Waste)
Forum engines (XenForo, vBulletin) generate separate URLs for every individual post reply using anchor parameters (e.g. `?p=443368`, `#post-12345`). 
- The deduplicator normalizes all thread URLs to their canonical root path.
- Search queries (e.g. `q=solar+quote`, `sort=new`) are strictly preserved.
- **Impact**: In a 50-page test, this prevented **186 redundant requests** before sending any network traffic.

### 2. 6-Way Content Classification
Before lead scoring, discussion content is classified into one of six categories:
1. `RESIDENTIAL_TURNKEY`: Homeowner residential rooftop solar, quotes, and installer requests (**Kept & Scored**).
2. `COMMERCIAL_SOLAR`: Megawatt-scale projects, warehouses, commercial solar lighting, industrial farms (**Rejected**).
3. `TECHNICAL_DISCUSSION`: Inverter error codes, busbar torque specs, NEC code compliance, optimizer repairs (**Rejected**).
4. `DIY_OFF_GRID`: Houseboat batteries, DIY camper van builds, custom LiFePO4 pack welding (**Rejected**).
5. `RESIDENTIAL_EXISTING_OWNER`: Past installations, multi-year production reviews (**Filtered**).
6. `GENERAL_INFORMATION`: Policy, state regulations, or general news (**Filtered**).

### 3. 4-Tier Lead Freshness Classification
Leads are categorized by post age to prevent prioritizing obsolete historical threads:
- **0–7 days**: Immediate, high-priority sales leads.
- **8–30 days**: Warm, active purchase consideration.
- **31–90 days**: Stale buyer leads.
- **Older than 90 days**: Historical reference (cannot be classified as Hot Lead).

---

## Intent-Based Lead Scoring System

Pages are scored based on explicit homeowner purchase signals:

| Signal / Statement | Intent Weight | Intent Purpose |
|---|---|---|
| Need a solar installation quote | **+5.0** | Active purchase inquiry |
| Comparing two solar proposals | **+5.0** | Warm quote comparison |
| Looking for a solar installer | **+5.0** | Direct installer search |
| Price per watt query (`$X/W`) | **+5.0** | Pricing evaluation |
| Want to install solar on my house | **+4.0** | Homeowner conversion |
| Electricity bill is `$X/mo` | **+3.0** | High utility bill pain point |
| Need a solar battery / Powerwall | **+2.5** | Storage addition |
| Asking about net metering | **+2.0** | Policy / grid tie inquiry |

### Lead Scoring Categories
- **Hot Lead ($\ge 7.0$)**: Ready-to-buy homeowners comparing proposals within the last 30 days.
- **Warm Lead ($4.0 - 6.5$)**: Planning residential installation, reviewing quotes or financing.
- **Researching ($2.0 - 3.5$)**: Early-stage feasibility, battery, or net metering inquiries.
- **Ignored ($< 2.0$)**: Low relevance or non-buyer discussions.

---

## Seed URL Priority Tiers

| Tier | Priority | Main Purpose | Sources |
|---|---|---|---|
| **P1** | **Highest** | Homeowners actively comparing quotes & searching for installers | `Reddit r/solar`, `r/TeslaSolar`, `SolarPanelTalk` |
| **P2** | **Medium-High** | Homeowners planning installations, researching batteries & costs | `DIY Solar Forum`, `Reddit r/DIYsolar`, `r/Powerwall` |
| **P3** | **Regional** | State & city-level homeowner purchase discussions | `Reddit California`, `Texas`, `Florida`, `Arizona`, `Houzz` |

Configure seeds in [config.py](file:///c:/Users/Lenovo/Desktop/crawl/config.py#L28-L64).
