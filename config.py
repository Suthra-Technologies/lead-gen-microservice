"""
Crawler Configuration Module
Defines prioritized seed tiers (P1, P2, P3), politeness settings,
domain allowlists, URL pattern blocklists, residential topic filters,
Deno.js Fetch Gateway integration, and intent-based solar lead scoring dictionary.
"""

from pathlib import Path
import os
from dotenv import load_dotenv

# Base Paths
BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
LEADS_DIR = BASE_DIR / "leads"
RAW_HTML_DIR = DATA_DIR / "raw_html"
DB_PATH = DATA_DIR / "crawler_storage.db"

# Google Maps / Places API Configuration
GOOGLE_MAPS_API_KEY = os.getenv("GOOGLE_MAPS_API_KEY", "").strip()
DEFAULT_PLACES_QUERIES = [
    "residential solar installer in Austin TX",
    "solar panel contractor in Phoenix AZ",
    "solar installation company in San Diego CA",
    "solar installers in Orlando FL",
    "residential solar in Dallas TX",
    "home solar panel installation in Los Angeles CA",
    "solar installer in San Antonio TX",
    "solar contractors in Tampa FL",
    "residential solar installer in Las Vegas NV",
    "solar panel installation in Denver CO",
]

# Ensure runtime directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LEADS_DIR.mkdir(parents=True, exist_ok=True)
RAW_HTML_DIR.mkdir(parents=True, exist_ok=True)

# -------------------------------------------------------------------------
# Seed URL Tiers & USA Source Registry Integration
# -------------------------------------------------------------------------
from source_registry import USA_SOURCE_REGISTRY, SourceRegistry

P1_SEEDS = SourceRegistry.get_active_seeds(priority_filter="P1_HIGH")
P2_SEEDS = SourceRegistry.get_active_seeds(priority_filter="P2_MEDIUM")
P3_SEEDS = SourceRegistry.get_active_seeds(priority_filter="P3_SUPPORTING")

# Default Seed Group: Prioritize P1 High Intent seeds followed by P2 Medium Intent
DEFAULT_SEED_URLS = P1_SEEDS + P2_SEEDS

# -------------------------------------------------------------------------
# Scope & Domain Control (Avoid crawling arbitrary external ads/affiliates)
# -------------------------------------------------------------------------
ALLOWED_DOMAINS = set()
for s in USA_SOURCE_REGISTRY.values():
    ALLOWED_DOMAINS.add(s.domain.lower())
    ALLOWED_DOMAINS.add(f"www.{s.domain.lower()}")


# Patterns strictly blocked to prevent crawling member profiles, admin, attachments, and noise
BLOCKED_URL_PATTERNS = [
    "/member/", "/members/", "/user/", "/users/", "/u/", "/profile/",
    "/register", "/login", "/logout", "/password", "/searchJSON",
    "/filedata/fetch", "/filedata/", "/attachment/", "/attachments/",
    "/announcements", "/announcement", "/calendar", "/tags/", "/tag/",
    "/help/", "/rules", "/privacy", "/terms", "/online", "/staff",
    "/commercial-solar", "/commercial-", "/solar-lighting", "/solar-street-light",
    "/industrial-solar", "/solar-water-pumps", "/solar-thermal", "/solar-pool-heating",
    "/wholesale-solar", "/advertis", "/affiliate", "/wp-login",
    "/misc/", "/sendmessage", "/subscription", "/report"
]

# Topics prioritized for residential solar buyer interest
RESIDENTIAL_TOPICS = [
    "solar-panels-for-home",
    "solar-panel-installation",
    "solar-panels-for-your-home",
    "residential-solar",
    "solar-quotes",
    "solar-cost",
    "home-solar",
    "battery-storage",
    "quote",
    "installer",
    "rooftop",
    "electric-bill"
]

# Topics explicitly excluded from lead discovery
EXCLUDED_TOPICS = [
    "commercial-solar",
    "solar-street-light",
    "industrial-solar",
    "announcements",
    "solar-lighting",
    "retail-cabinet",
    "warehouse",
    "utility-scale"
]

# -------------------------------------------------------------------------
# Deno.js Fetch Gateway Settings
# -------------------------------------------------------------------------
USE_FETCH_GATEWAY = True               # Primary transport: Deno.js Fetch Gateway
FETCH_GATEWAY_URL = "http://127.0.0.1:8000/fetch"
GATEWAY_HEALTH_URL = "http://127.0.0.1:8000/health"
FALLBACK_TO_DIRECT_HTTPX = True       # Configurable fallback if Deno service is unavailable

# -------------------------------------------------------------------------
# Crawler Runtime Controls
# -------------------------------------------------------------------------
MAX_CRAWL_DEPTH = 3
MAX_CONCURRENT_DOWNLOADS = 6
PER_DOMAIN_DELAY_SECONDS = 1.5  # Politeness delay between requests to the same host
REQUEST_TIMEOUT_SECONDS = 12

# Network Headers
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Link Filtering
BLOCKED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".mp4", ".mp3", ".avi", ".mov", ".zip", ".tar", ".gz", ".rar",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot"
}

# -------------------------------------------------------------------------
# Content-Based Classification Taxonomy
# -------------------------------------------------------------------------
CLASSIFICATION_RESIDENTIAL_NEW_BUYER = "RESIDENTIAL_NEW_BUYER"
CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER = "RESIDENTIAL_QUOTE_SEEKER"
CLASSIFICATION_RESIDENTIAL_EXISTING = "RESIDENTIAL_EXISTING_OWNER"
CLASSIFICATION_RESIDENTIAL_UPGRADE = "RESIDENTIAL_UPGRADE"
CLASSIFICATION_RESIDENTIAL_REPAIR = "RESIDENTIAL_REPAIR"
CLASSIFICATION_COMMERCIAL = "COMMERCIAL_SOLAR"
CLASSIFICATION_DIY_OFFGRID = "DIY_OFF_GRID"
CLASSIFICATION_TECHNICAL = "TECHNICAL_DISCUSSION"
CLASSIFICATION_GENERAL_INFO = "GENERAL_INFORMATION"
CLASSIFICATION_SPAM_VENDOR = "SPAM_VENDOR"

# Backward compatibility alias
CLASSIFICATION_RESIDENTIAL_TURNKEY = "RESIDENTIAL_NEW_BUYER"

COMMERCIAL_PHRASES = [
    "commercial solar", "commercial rooftop", "commercial warehouse",
    "warehouse installation", "solar farm", "utility-scale", "utility scale",
    "commercial lighting", "commercial street light", "commercial building",
    "industrial solar", "3-phase commercial", "medium voltage",
    "ground mount farm", "commercial project"
]

TECHNICAL_SIGNALS = [
    "optimizer compatibility", "torque spec", "torque specs", "busbar",
    "lug connection", "cleaning busbar", "disconnect requirement", "nec code",
    "grounding outbuilding", "fusing panels in parallel", "replace optimizer",
    "replacing optimizers", "sn#", "serial number", "solar movies",
    "installation movie", "teardown of a real", "teardown"
]

DIY_SIGNALS = [
    "houseboat", "diy build", "lifepo4 diy", "diy solar project", "rv solar",
    "campervan", "diy battery", "building my own solar", "self install",
    "jk bms", "eve cells", "diy 48v"
]

EXISTING_OWNER_SIGNALS = [
    "my existing solar", "already have solar", "existing system",
    "existing install", "existing set up", "installed last year",
    "my solar panels produced", "my inverter", "system on my roof",
    "installed 10 years ago", "installed 12 year", "existing 12 year"
]

UPGRADE_SIGNALS = [
    "additional panels", "add more panels", "expand my system",
    "adding panels", "add a battery to my existing", "system expansion",
    "adding solar panels to existing"
]

REPAIR_SIGNALS = [
    "stopped working", "inverter failed", "error code", "replace inverter",
    "warranty claim", "broken panel", "inverter died", "repair existing",
    "diagnose a potential inverter issue"
]

# -------------------------------------------------------------------------
# Intent-Based Solar Lead Scoring System & Centralized Thresholds
# -------------------------------------------------------------------------
LEAD_THRESHOLDS = {
    "hot": 7.0,
    "warm": 4.0,
    "researching": 2.0
}
HOT_MAX_AGE_DAYS = 30
SCORE_MIN = -20.0
SCORE_MAX = 50.0

# Solar Opportunity Estimator Defaults
DEFAULT_ELECTRICITY_RATES = {
    "CA": 0.32,
    "TX": 0.15,
    "FL": 0.16,
    "AZ": 0.14,
    "NY": 0.23,
    "NJ": 0.19,
    "MA": 0.28,
    "CO": 0.15,
    "NV": 0.14,
    "DEFAULT": 0.16
}
DEFAULT_SPECIFIC_YIELD_KWH_PER_KW = 1400.0  # US average annual kWh produced per kW installed

# Crawl & Pagination Budgets
MAX_CATEGORY_PAGES = 3
MAX_THREAD_REPLY_PAGES = 2

# Advanced Intent NLP Signals
NEGATION_SIGNALS = [
    "don't need an installer",
    "dont need an installer",
    "do not need an installer",
    "do not need a solar installer",
    "not looking for an installer",
    "not looking for solar",
    "not interested in getting solar",
    "not interested in solar",
    "not interested in quotes",
    "never getting solar",
    "would not recommend solar",
    "no intention of buying",
    "no intention to buy",
    "decided against solar",
    "cancelled my solar",
    "cancelled our contract",
    "scam company",
    "stay away from solar"
]

HISTORICAL_INTENT_SIGNALS = [
    "installed last year",
    "installed 2 years ago",
    "installed 3 years ago",
    "installed 4 years ago",
    "installed 5 years ago",
    "installed 10 years ago",
    "installed in 2018",
    "installed in 2019",
    "installed in 2020",
    "installed in 2021",
    "installed in 2022",
    "installed in 2023",
    "had solar for years",
    "existing system on my roof",
    "my old quotes from",
    "system was installed back in"
]

THIRD_PERSON_SIGNALS = [
    "my neighbor is looking",
    "my neighbor wants",
    "my neighbor got",
    "my brother lives in",
    "my brother is comparing",
    "my brother wants",
    "a friend of mine is",
    "a friend told me",
    "my coworker was asking",
    "my parents are thinking",
    "my parents need",
    "someone i know"
]

HYPOTHETICAL_SIGNALS = [
    "hypothetically speaking",
    "just out of curiosity",
    "purely hypothetical",
    "wondering if solar would ever",
    "for an academic project",
    "for my school project",
    "for a research paper"
]

SOLAR_INTENT_WEIGHTS = {
    # High Purchase Intent Signals (+5)
    "need a solar installation quote": 5.0,
    "solar installation quote": 5.0,
    "looking for a solar installer": 5.0,
    "looking for solar installer": 5.0,
    "installer recommendation": 5.0,
    "installer recommendations": 5.0,
    "can anyone recommend a solar installer": 5.0,
    "recommend an installer": 5.0,
    "recommend a solar installer": 5.0,
    "requesting solar quotes": 5.0,
    
    # Active Quote Comparison / Review Signals (+4 to +4.5)
    "comparing two solar proposals": 4.5,
    "comparing solar quotes": 4.5,
    "comparing quotes": 4.0,
    "quote comparison": 4.5,
    "review my quote": 4.5,
    "is this a good quote": 4.0,
    "is this quote reasonable": 4.0,
    "fair price per watt": 4.0,
    "got quoted": 4.0,
    
    # Active Homeowner Conversion Signals (+3.5 to +4)
    "want to install solar on my house": 4.0,
    "want to install solar": 4.0,
    "planning to install solar": 4.0,
    "planning a new installation": 4.0,
    "switch to solar": 3.5,
    "going solar": 3.5,
    "rooftop solar quote": 4.0,
    "how much would a solar installation cost": 3.5,
    "thinking about getting solar": 3.5,
    
    # Economic & Bill Reduction Signals (+3)
    "high electric bill": 3.0,
    "high electricity bill": 3.0,
    "solar financing": 2.0,
    "solar loan": 2.0,
    
    # Storage Addition with Installation Intent (+3)
    "need a battery installed": 3.0,
    "want to add battery backup": 2.5,
    "adding battery storage": 2.5,
    
    # Negative Intent: DIY Only Intent (-3.0 to -4.0)
    "diy solar project only": -4.0,
    "diy solar": -3.0,
    "building my own solar": -3.5,
    "diy installation": -3.0,
    "self install": -2.5,
    
    # Negative Intent: Vendor / Installer Advertising (-5.0)
    "we provide solar installation": -5.0,
    "contact us for solar services": -5.0,
    "our company installs": -5.0,
    "free solar consultation from us": -5.0,
    "solar contractor serving": -4.0,
    "call our team today": -4.0,
    "solar sales rep": -3.0,
}

# Lead Classification Thresholds
LEAD_CATEGORY_HOT = 7.0         # 7.0+: Hot Buyer (Ready to purchase / quote comparison)
LEAD_CATEGORY_WARM = 4.0        # 4.0 - 6.5: Warm Buyer (Actively planning / researching)
LEAD_CATEGORY_RESEARCHING = 2.0 # 2.0 - 3.5: Early stage research
