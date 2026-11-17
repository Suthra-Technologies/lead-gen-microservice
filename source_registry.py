"""
USA Solar Source Registry Module
Maintains the centralized registry of approved US residential solar discussion sources,
geographic relevance, category patterns, pagination schemas, access methods, and crawl permissions.
"""

from typing import Dict, List, Any, Optional
from dataclasses import dataclass, asdict


@dataclass
class SolarSource:
    source_id: str
    name: str
    domain: str
    seed_urls: List[str]
    source_type: str            # FORUM, COMMUNITY_QA, HOME_IMPROVEMENT, REDDIT_COMMUNITY, COOP_ADVOCACY
    us_geo_relevance: str       # National US, Texas, Indiana & Midwest, California, etc.
    categories: List[str]       # Relevant subcategories
    pagination_pattern: str     # Regex or template (e.g. /page{n}, ?page={n})
    access_method: str          # DENO_GATEWAY_HTTPX, PRAW_OAUTH
    status: str                 # ACTIVE, UNAVAILABLE_BLOCKED, PAUSED_CREDS
    priority: str               # P1_HIGH, P2_MEDIUM, P3_SUPPORTING
    notes: str = ""


# Centralized Registry of Approved US Sources
USA_SOURCE_REGISTRY: Dict[str, SolarSource] = {
    "solarpaneltalk": SolarSource(
        source_id="solarpaneltalk",
        name="SolarPanelTalk",
        domain="solarpaneltalk.com",
        seed_urls=[
            "https://www.solarpaneltalk.com/forum/solar-quotes-pricing?sort=lastpost&order=desc",
            "https://www.solarpaneltalk.com/forum/solar-panels-for-home?sort=lastpost&order=desc",
            "https://www.solarpaneltalk.com/forum/solar-panels-for-home/solar-panel-installation?sort=lastpost&order=desc",
            "https://www.solarpaneltalk.com/forum/solar-panels-for-home/solar-panel-system-equipment?sort=lastpost&order=desc",
        ],
        source_type="FORUM",
        us_geo_relevance="National US (CA, AZ, TX, FL, NY, NJ)",
        categories=[
            "solar-quotes-pricing",
            "solar-panels-for-home",
            "solar-panel-installation",
            "solar-panel-system-equipment"
        ],
        pagination_pattern=r"/page[2-3]\b",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P1_HIGH",
        notes="Premier residential discussion forum sorted by latest activity; high volume of installer quotes & quote comparisons."
    ),
    "diysolarforum": SolarSource(
        source_id="diysolarforum",
        name="DIY Solar Forum",
        domain="diysolarforum.com",
        seed_urls=[
            "https://diysolarforum.com/whats-new/posts/",
            "https://diysolarforum.com/forums/new-to-solar-start-here.3/",
            "https://diysolarforum.com/forums/general-solar-discussion.4/",
            "https://diysolarforum.com/forums/grid-tie-solar-grid-assist.37/",
        ],
        source_type="FORUM",
        us_geo_relevance="National US",
        categories=[
            "whats-new",
            "new-to-solar-start-here",
            "general-solar-discussion",
            "grid-tie-solar-grid-assist"
        ],
        pagination_pattern=r"/page-[2-3]\b",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P1_HIGH",
        notes="High volume of recent daily homeowner solar planning, equipment, and battery discussions via What's New feed."
    ),
    "sirensolar": SolarSource(
        source_id="sirensolar",
        name="SIREN Solar Q&A",
        domain="sirensolar.org",
        seed_urls=[
            "https://www.sirensolar.org/solar-qa/",
            "https://www.sirensolar.org/contact-us/",
        ],
        source_type="COMMUNITY_QA",
        us_geo_relevance="Indiana & Midwest US",
        categories=[
            "solar-qa",
            "contact-us"
        ],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P2_MEDIUM",
        notes="Community solar initiative providing Indiana homeowner Q&A and official non-profit business contact."
    ),
    "houzz": SolarSource(
        source_id="houzz",
        name="Houzz Solar Discussions",
        domain="houzz.com",
        seed_urls=[
            "https://www.houzz.com/discussions/solar-energy/sort/newest",
            "https://www.houzz.com/discussions/solar-energy",
        ],
        source_type="HOME_IMPROVEMENT",
        us_geo_relevance="National US",
        categories=[
            "discussions/solar-energy"
        ],
        pagination_pattern=r"[?&]p=[2-3]",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P2_MEDIUM",
        notes="Residential homeowners discussing home renovation and rooftop solar contractor selection sorted by newest."
    ),
    "ecorenovator": SolarSource(
        source_id="ecorenovator",
        name="EcoRenovator Forum",
        domain="ecorenovator.org",
        seed_urls=[
            "https://ecorenovator.org/forum/forumdisplay.php?f=11",  # Solar Power & Heating
            "https://ecorenovator.org/forum/forumdisplay.php?f=2",   # Renewable Energy
        ],
        source_type="FORUM",
        us_geo_relevance="National US",
        categories=[
            "Solar Power & Heating",
            "Renewable Energy"
        ],
        pagination_pattern=r"page=[2-3]",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P3_SUPPORTING",
        notes="Green home renovations and residential solar energy discussions."
    ),
    "builditsolar": SolarSource(
        source_id="builditsolar",
        name="Build It Solar",
        domain="builditsolar.com",
        seed_urls=[
            "https://www.builditsolar.com/Projects/PV/pv.htm",
        ],
        source_type="COMMUNITY_QA",
        us_geo_relevance="National US",
        categories=["pv-projects"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P3_SUPPORTING",
        notes="Community renewable energy database and DIY residential solar projects."
    ),
    "solarunitedneighbors": SolarSource(
        source_id="solarunitedneighbors",
        name="Solar United Neighbors (SUN)",
        domain="solarunitedneighbors.org",
        seed_urls=[
            "https://www.solarunitedneighbors.org/co-ops/",
        ],
        source_type="COOP_ADVOCACY",
        us_geo_relevance="State-Based US Co-ops (TX, FL, VA, OH, PA, IN)",
        categories=[
            "co-ops",
            "solar-groups"
        ],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="UNAVAILABLE_BLOCKED",
        priority="P3_SUPPORTING",
        notes="Restricted by Cloudflare bot protection (HTTP 403). Compliant crawlers mark as unavailable without bypass."
    ),
    "naws_forum": SolarSource(
        source_id="naws_forum",
        name="Northern Arizona Wind & Sun",
        domain="forum.solar-electric.com",
        seed_urls=[
            "https://forum.solar-electric.com/discussions",
        ],
        source_type="FORUM",
        us_geo_relevance="Southwest & National US",
        categories=["discussions"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="UNAVAILABLE_BLOCKED",
        priority="P3_SUPPORTING",
        notes="Protected by Cloudflare (HTTP 403 on automated requests); recorded compliantly."
    ),
    "bogleheads": SolarSource(
        source_id="bogleheads",
        name="Bogleheads Personal Finance (Solar)",
        domain="bogleheads.org",
        seed_urls=[
            "https://www.bogleheads.org/forum/viewforum.php?f=11",
        ],
        source_type="COMMUNITY_QA",
        us_geo_relevance="National US",
        categories=["consumer-finance"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="UNAVAILABLE_BLOCKED",
        priority="P3_SUPPORTING",
        notes="Consumer finance board with frequent solar quote audits; protected by bot check (HTTP 403)."
    ),
    "citydata": SolarSource(
        source_id="citydata",
        name="City-Data Home Improvement",
        domain="city-data.com",
        seed_urls=[
            "https://www.city-data.com/forum/house/",
        ],
        source_type="FORUM",
        us_geo_relevance="Regional US (CA, TX, FL, AZ)",
        categories=["house"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="UNAVAILABLE_BLOCKED",
        priority="P3_SUPPORTING",
        notes="Regional US homeowner discussions; protected by automated rate limit (HTTP 429)."
    ),
    "biggerpockets": SolarSource(
        source_id="biggerpockets",
        name="BiggerPockets Real Estate Solar",
        domain="biggerpockets.com",
        seed_urls=[
            "https://www.biggerpockets.com/forums/12-real-estate-investing-energy-green-solar",
        ],
        source_type="FORUM",
        us_geo_relevance="National US",
        categories=["green-solar"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="UNAVAILABLE_BLOCKED",
        priority="P3_SUPPORTING",
        notes="Real estate investor solar discussions; protected by Cloudflare bot shield."
    ),
    "reddit_solar": SolarSource(
        source_id="reddit_solar",
        name="Reddit r/solar",
        domain="reddit.com",
        seed_urls=[
            "https://www.reddit.com/r/solar/new.rss",
            "https://www.reddit.com/r/solar/new.json?limit=25",
        ],
        source_type="REDDIT_COMMUNITY",
        us_geo_relevance="National US",
        categories=["new", "quotes", "installers"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P1_HIGH",
        notes="Premier Reddit solar buyer community; fetched live via RSS and PRAW."
    ),
    "reddit_teslasolar": SolarSource(
        source_id="reddit_teslasolar",
        name="Reddit r/TeslaSolar",
        domain="reddit.com",
        seed_urls=[
            "https://www.reddit.com/r/TeslaSolar/new.rss",
            "https://www.reddit.com/r/TeslaSolar/new.json?limit=25",
        ],
        source_type="REDDIT_COMMUNITY",
        us_geo_relevance="National US",
        categories=["new", "tesla-quotes", "powerwall"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P1_HIGH",
        notes="Tesla Solar Roof and Powerwall quote comparisons and contractor experiences."
    ),
    "reddit_texassolar": SolarSource(
        source_id="reddit_texassolar",
        name="Reddit r/TexasSolar",
        domain="reddit.com",
        seed_urls=[
            "https://www.reddit.com/r/TexasSolar/new.rss",
            "https://www.reddit.com/r/TexasSolar/new.json?limit=25",
        ],
        source_type="REDDIT_COMMUNITY",
        us_geo_relevance="Texas (ERCOT, Oncor, CenterPoint)",
        categories=["new", "texas-quotes", "installers"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P1_HIGH",
        notes="High-intent Texas homeowners comparing proposals across deregulated retail electric providers."
    ),
    "reddit_floridasolar": SolarSource(
        source_id="reddit_floridasolar",
        name="Reddit r/floridasolar",
        domain="reddit.com",
        seed_urls=[
            "https://www.reddit.com/r/floridasolar/new.rss",
        ],
        source_type="REDDIT_COMMUNITY",
        us_geo_relevance="Florida (FPL, Duke Energy)",
        categories=["florida-solar"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P1_HIGH",
        notes="Florida homeowners comparing residential solar quotes under net metering rules."
    ),
    "reddit_homeimprovement": SolarSource(
        source_id="reddit_homeimprovement",
        name="Reddit r/HomeImprovement (Solar)",
        domain="reddit.com",
        seed_urls=[
            "https://www.reddit.com/r/HomeImprovement/new.rss",
        ],
        source_type="REDDIT_COMMUNITY",
        us_geo_relevance="National US",
        categories=["home-improvement-solar"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P2_MEDIUM",
        notes="General homeowners planning residential solar installations alongside home renovations."
    ),
    "reddit_diysolar": SolarSource(
        source_id="reddit_diysolar",
        name="Reddit r/DIYsolar",
        domain="reddit.com",
        seed_urls=[
            "https://www.reddit.com/r/DIYsolar/new.rss",
        ],
        source_type="REDDIT_COMMUNITY",
        us_geo_relevance="National US",
        categories=["diysolar"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P2_MEDIUM",
        notes="DIY builds and battery additions."
    ),
    "reddit_powerwall": SolarSource(
        source_id="reddit_powerwall",
        name="Reddit r/Powerwall",
        domain="reddit.com",
        seed_urls=[
            "https://www.reddit.com/r/Powerwall/new.rss",
        ],
        source_type="REDDIT_COMMUNITY",
        us_geo_relevance="National US",
        categories=["powerwall"],
        pagination_pattern="",
        access_method="DENO_GATEWAY_HTTPX",
        status="ACTIVE",
        priority="P2_MEDIUM",
        notes="Residential battery backup additions."
    ),
    "google_places": SolarSource(
        source_id="google_places",
        name="Google Places Solar Installers",
        domain="places.googleapis.com",
        seed_urls=[
            "https://places.googleapis.com/v1/places:searchText",
        ],
        source_type="GOOGLE_PLACES_API",
        us_geo_relevance="National US (CA, TX, FL, AZ, NV, CO)",
        categories=["residential-solar-installers", "contractors", "verified-business"],
        pagination_pattern="",
        access_method="GOOGLE_PLACES_API",
        status="ACTIVE",
        priority="P1_HIGH",
        notes="Verified residential solar contractor discovery via Google Places API (New) with phone, website, address, and ratings."
    )
}


class SourceRegistry:
    @classmethod
    def get_all_sources(cls) -> List[SolarSource]:
        return list(USA_SOURCE_REGISTRY.values())

    @classmethod
    def get_active_sources(cls) -> List[SolarSource]:
        return [s for s in USA_SOURCE_REGISTRY.values() if s.status == "ACTIVE"]

    @classmethod
    def get_active_seeds(cls, priority_filter: Optional[str] = None) -> List[str]:
        seeds = []
        for s in cls.get_active_sources():
            if priority_filter and s.priority != priority_filter:
                continue
            seeds.extend(s.seed_urls)
        return seeds

    @classmethod
    def get_allowed_domains(cls) -> set:
        return {s.domain for s in USA_SOURCE_REGISTRY.values()}

    @classmethod
    def get_source_by_domain(cls, domain: str) -> Optional[SolarSource]:
        domain_clean = domain.lower().replace("www.", "")
        for s in USA_SOURCE_REGISTRY.values():
            if s.domain.lower().replace("www.", "") == domain_clean:
                return s
        return None

    @classmethod
    def get_source_by_id(cls, source_id: str) -> Optional[SolarSource]:
        return USA_SOURCE_REGISTRY.get(source_id)
