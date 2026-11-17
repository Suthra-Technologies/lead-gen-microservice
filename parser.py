"""
Content Parser & Buyer Intent Detection Engine
Performs 10-way content classification (New Buyer, Quote Seeker, Upgrade, Repair,
Commercial, Technical, DIY, General Info, Spam), computes evidence-based purchase intent,
extracts thread creation & latest reply dates with 4-tier freshness categorization,
and validates with strict Pydantic schemas.
"""

import json
import re
import logging
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs
from typing import Dict, Any, List, Set, Tuple, Optional
from bs4 import BeautifulSoup
import trafilatura
import dateparser

from config import (
    SOLAR_INTENT_WEIGHTS,
    LEAD_CATEGORY_HOT,
    LEAD_CATEGORY_WARM,
    LEAD_CATEGORY_RESEARCHING,
    LEAD_THRESHOLDS,
    HOT_MAX_AGE_DAYS,
    NEGATION_SIGNALS,
    HISTORICAL_INTENT_SIGNALS,
    THIRD_PERSON_SIGNALS,
    HYPOTHETICAL_SIGNALS,
    CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
    CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER,
    CLASSIFICATION_RESIDENTIAL_EXISTING,
    CLASSIFICATION_RESIDENTIAL_UPGRADE,
    CLASSIFICATION_RESIDENTIAL_REPAIR,
    CLASSIFICATION_COMMERCIAL,
    CLASSIFICATION_DIY_OFFGRID,
    CLASSIFICATION_TECHNICAL,
    CLASSIFICATION_GENERAL_INFO,
    CLASSIFICATION_SPAM_VENDOR,
    COMMERCIAL_PHRASES,
    TECHNICAL_SIGNALS,
    DIY_SIGNALS,
    EXISTING_OWNER_SIGNALS,
    UPGRADE_SIGNALS,
    REPAIR_SIGNALS
)
from deduplicator import Deduplicator
from storage import CrawlerStorage
from models import (
    SolarLead,
    LeadCategory,
    LeadSegment,
    BuyerStage,
    ClassificationType,
    LeadFreshness,
    LeadLifecycleStatus,
    QualificationStatus,
    ValidationStatus
)
from contact_extractor import BusinessContactExtractor
from opportunity_estimator import SolarOpportunityEstimator
from data_quality import LeadDataQualityValidator
from sources import get_adapter_for_url, get_adapter_for_domain

logger = logging.getLogger("Crawler.Parser")


class ContentParser:
    RESIDENTIAL_CONTEXT_TERMS = [
        "home", "house", "roof", "rooftop", "residential", "hoa",
        "electric bill", "power bill", "utility bill", "quote", "installer",
        "powerwall", "battery", "net metering", "nem", "inverter", "sq ft",
        "sqft", "kwh", "residential solar", "single family"
    ]

    VENDOR_SPAM_SIGNALS = [
        "we provide solar installation", "contact us for solar", "our company installs",
        "free solar consultation from us", "solar contractor serving", "call our team today",
        "we are a leading solar", "our experienced solar installers"
    ]

    QUOTE_COMPARISON_PHRASES = [
        "quote comparison", "comparing quotes", "comparing solar quotes",
        "review my quote", "is this quote reasonable", "is this quote fair",
        "received a quote", "got a quote", "got quoted", "quote for",
        "two quotes", "three quotes", "competing quotes", "solar proposal", "solar proposals"
    ]

    INSTALLER_SEARCH_PHRASES = [
        "looking for a solar installer", "looking for solar installer",
        "looking for solar installers", "looking for recommended solar installers",
        "need a solar installation quote", "need solar proposals", "need to go solar",
        "installer recommendation", "installer recommendations", "recommended installers", "recommended solar installers",
        "can anyone recommend a solar installer", "recommend an installer", "recommend a solar installer",
        "want to install solar on my house", "want to install solar",
        "planning to install solar", "planning a new installation",
        "how much would a solar installation cost", "thinking about getting solar"
    ]

    def __init__(self, storage: CrawlerStorage):
        self.storage = storage
        self.contact_extractor = BusinessContactExtractor()

        # Regex for Solar & Energy Capacity Metrics
        self.mw_regex = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:mw|megawatts?|mega\s*watts?)\b", re.IGNORECASE)
        self.kw_regex = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:kw|kilowatts?|kilo\s*watts?)\b", re.IGNORECASE)
        self.bill_regex = re.compile(r"\$\s*(\d{2,4})\s*(?:\/|\s*per\s*)?(?:mo|month|electric|bill|power)", re.IGNORECASE)
        self.ppw_regex = re.compile(r"\$\s*([1-5](?:\.\d{1,2})?)\s*(?:\/|\s*per\s*)?w(?:att)?", re.IGNORECASE)
        self.zip_regex = re.compile(r"\b\d{5}(?:-\d{4})?\b")

        # Top US Solar States
        self.us_states = {
            "california": "CA", "texas": "TX", "florida": "FL", "arizona": "AZ",
            "massachusetts": "MA", "nevada": "NV", "colorado": "CO", "new york": "NY",
            "new jersey": "NJ", "north carolina": "NC", "georgia": "GA", "virginia": "VA",
            "illinois": "IL", "pennsylvania": "PA", "ohio": "OH", "washington": "WA"
        }

    def _determine_source_name(self, url: str) -> str:
        domain = urlparse(url).netloc.lower()
        if "reddit.com" in domain:
            if "/r/solar" in url.lower():
                return "Reddit r/solar"
            if "/r/teslasolar" in url.lower():
                return "Reddit r/TeslaSolar"
            if "/r/diysolar" in url.lower():
                return "Reddit r/DIYsolar"
            if "/r/powerwall" in url.lower():
                return "Reddit r/Powerwall"
            if "/r/texassolar" in url.lower():
                return "Reddit r/TexasSolar"
            if "/r/homeimprovement" in url.lower():
                return "Reddit r/HomeImprovement"
            return "Reddit Community"
        elif "solarpaneltalk.com" in domain:
            return "SolarPanelTalk"
        elif "diysolarforum.com" in domain:
            return "DIY Solar Forum"
        elif "sirensolar.org" in domain:
            return "SIREN Solar Q&A"
        elif "houzz.com" in domain:
            return "Houzz Solar Discussions"
        elif "ecorenovator.org" in domain:
            return "EcoRenovator"
        elif "solarunitedneighbors.org" in domain:
            return "Solar United Neighbors"
        return domain

    def _is_discussion_thread(self, url: str) -> bool:
        """Checks if URL is an actual discussion thread rather than category/search page."""
        parsed = urlparse(url)
        path = parsed.path.rstrip("/").lower()

        if not path or path in ("", "/"):
            return False

        broad_roots = {
            "/forum", "/forums", "/discussions", "/r/solar", "/r/solar/new",
            "/r/teslasolar", "/r/teslasolar/new", "/r/diysolar", "/r/powerwall",
            "/online", "/search", "/portal", "/whats-new", "/whats-new/posts"
        }
        if path in broad_roots or path.endswith(("/whats-new", "/whats-new/posts", "/pv.htm")):
            return False

        if "/comments/" in path:
            return True

        if re.search(r"/(?:threads|topic|post)/", path) or re.search(r"/\d{4,8}-[a-z0-9-]+", path):
            return True

        qs = parse_qs(parsed.query)
        if any(k in qs for k in ("t", "id", "tid", "threadid", "topicid")):
            return True

        segments = [s for s in path.split("/") if s]
        if len(segments) >= 3 and not path.endswith(("/new", "/top", "/hot", "/search", "/discussions")):
            return True

        return False

    def _find_evidence_sentence(self, text: str, phrase: str) -> Optional[str]:
        """Extracts the exact sentence containing phrase for auditable evidence."""
        sentences = re.split(r"(?<=[.!?\n])\s+", text)
        p_lower = phrase.lower()
        for s in sentences:
            if p_lower in s.lower():
                cleaned = " ".join(s.split()).strip()
                if len(cleaned) >= 15:
                    return cleaned[:250]
        return None

    def extract_capacity(self, text: str) -> Tuple[Optional[float], Optional[str]]:
        """
        Extracts numerical capacity with units.
        Returns: (capacity_value, 'kW' | 'MW' | None)
        """
        mw_match = self.mw_regex.search(text)
        if mw_match:
            try:
                return float(mw_match.group(1)), "MW"
            except ValueError:
                pass

        kw_match = self.kw_regex.search(text)
        if kw_match:
            try:
                val = float(kw_match.group(1))
                if 0.5 <= val <= 250.0:
                    return val, "kW"
            except ValueError:
                pass

        return None, None

    def classify_content(self, title: str, text: str) -> Tuple[str, List[str]]:
        """
        Performs 10-way content classification on title + body:
        RESIDENTIAL_NEW_BUYER, RESIDENTIAL_QUOTE_SEEKER, RESIDENTIAL_EXISTING_OWNER,
        RESIDENTIAL_UPGRADE, RESIDENTIAL_REPAIR, COMMERCIAL_SOLAR, DIY_OFF_GRID,
        TECHNICAL_DISCUSSION, GENERAL_INFORMATION, SPAM_VENDOR.
        """
        combined = f"{title}\n{text}".lower()
        reasons = []

        # 1. SPAM / Vendor Self-Promotion Check
        for spam_sig in self.VENDOR_SPAM_SIGNALS:
            if spam_sig in combined:
                return CLASSIFICATION_SPAM_VENDOR, [f"Vendor advertising detected: '{spam_sig}'"]

        # 1b. Forum Stickies / FAQ / Educational Guides
        is_sticky_or_faq = any(k in title.lower() for k in ["sticky", "stickies", "faq", "glossary", "read this first", "forum rules"]) or any(k in combined[:400] for k in ["start off some stickies", "sticky for this section", "frequently asked questions"])
        if is_sticky_or_faq:
            return CLASSIFICATION_GENERAL_INFO, ["Moderator sticky or educational FAQ guide"]

        # 2. Commercial Solar Check
        # Check numerical MW capacity >= 0.5 MW
        mw_match = self.mw_regex.search(combined)
        if mw_match:
            try:
                mw_val = float(mw_match.group(1))
                if mw_val >= 0.5:
                    return CLASSIFICATION_COMMERCIAL, [f"Commercial MW scale capacity detected ({mw_match.group(0)})"]
            except ValueError:
                pass

        # Check explicit commercial phrases
        for phrase in COMMERCIAL_PHRASES:
            if phrase in combined:
                return CLASSIFICATION_COMMERCIAL, [f"Commercial phrase matched: '{phrase}'"]

        # Check large scale capacity (>= 50 kW)
        kw_val, unit = self.extract_capacity(combined)
        if unit == "kW" and kw_val and kw_val >= 50.0:
            return CLASSIFICATION_COMMERCIAL, [f"Large scale capacity ({kw_val} kW >= 50 kW)"]

        # 3. DIY / Off-Grid Check
        diy_hits = [s for s in DIY_SIGNALS if s in combined]
        if len(diy_hits) >= 2 or any(term in title.lower() for term in ["houseboat", "diy build", "lifepo4 diy", "rv solar"]):
            return CLASSIFICATION_DIY_OFFGRID, [f"DIY off-grid signals: {diy_hits[:3]}"]

        # 4. Technical Troubleshooting / Teardown / NEC Code Check
        tech_hits = [s for s in TECHNICAL_SIGNALS if s in combined]
        is_video_or_teardown = any(k in combined for k in ["solar movies", "installation movie", "youtube video", "teardown"])
        if is_video_or_teardown or len(tech_hits) >= 2 or any(term in title.lower() for term in ["torque", "optimizer", "busbar", "fusing", "earthing", "grounding"]):
            return CLASSIFICATION_TECHNICAL, [f"Technical troubleshooting signals: {tech_hits[:3] if tech_hits else 'teardown/code'}"]

        # 5. Solar Repair Inquiry (Service Customer)
        repair_hits = [s for s in REPAIR_SIGNALS if s in combined]
        is_repair = bool(repair_hits) or any(k in combined for k in ["repair technician", "inverter error", "replace inverter", "stopped working", "error code"])
        if is_repair and any(term in combined for term in ["solar", "inverter", "panel", "system"]):
            return CLASSIFICATION_RESIDENTIAL_REPAIR, [f"Homeowner solar equipment repair inquiry: {repair_hits[:2] if repair_hits else ['repair/inverter issue']}"]

        # 6. Existing Solar Owner (Upgrade vs General Existing)
        has_existing = any(term in combined for term in EXISTING_OWNER_SIGNALS)
        upgrade_hits = [s for s in UPGRADE_SIGNALS if s in combined]
        if upgrade_hits or (has_existing and ("upgrade" in title.lower() or "additional panels" in title.lower())):
            return CLASSIFICATION_RESIDENTIAL_UPGRADE, [f"Existing owner seeking system expansion: {upgrade_hits[:2] if upgrade_hits else ['upgrade/expansion']}"]

        if has_existing:
            return CLASSIFICATION_RESIDENTIAL_EXISTING, ["Existing solar owner discussion"]

        # 6. Residential Quote Seeker (High Priority)
        has_ppw = bool(self.ppw_regex.search(combined))
        has_quote_comparison = any(phrase in combined for phrase in self.QUOTE_COMPARISON_PHRASES)
        if (has_quote_comparison or has_ppw) and any(term in combined for term in self.RESIDENTIAL_CONTEXT_TERMS):
            return CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER, ["Homeowner reviewing or comparing installer quotes"]

        # 7. Residential New Buyer / Installer Search (High Priority)
        has_installer_search = any(phrase in combined for phrase in self.INSTALLER_SEARCH_PHRASES)
        if has_installer_search and any(term in combined for term in self.RESIDENTIAL_CONTEXT_TERMS):
            return CLASSIFICATION_RESIDENTIAL_NEW_BUYER, ["Homeowner actively planning or seeking solar installation"]

        # 8. General Information / Policy Default
        return CLASSIFICATION_GENERAL_INFO, ["General solar information, policy, or non-buyer discussion"]

    def extract_buyer_stage(self, classification: str, title: str, text: str) -> str:
        """
        Derives buyer stage strictly from evidence:
        COMPARING_QUOTES, SEEKING_QUOTES, PLANNING, READY_TO_PURCHASE,
        EXISTING_CUSTOMER, SERVICE_REQUIRED, EXPLORING, or UNKNOWN.
        """
        combined = f"{title}\n{text}".lower()

        if classification == CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER:
            return BuyerStage.COMPARING_QUOTES.value

        if classification == CLASSIFICATION_RESIDENTIAL_NEW_BUYER:
            if any(term in combined for term in ["installer recommendation", "looking for a solar installer", "recommend an installer"]):
                return BuyerStage.SEEKING_QUOTES.value
            if any(term in combined for term in ["want to install", "planning to install", "going solar"]):
                return BuyerStage.PLANNING.value
            return BuyerStage.EXPLORING.value

        if classification == CLASSIFICATION_RESIDENTIAL_UPGRADE:
            return BuyerStage.EXISTING_CUSTOMER.value

        if classification == CLASSIFICATION_RESIDENTIAL_REPAIR:
            return BuyerStage.SERVICE_REQUIRED.value

        if classification == CLASSIFICATION_RESIDENTIAL_EXISTING:
            return BuyerStage.EXISTING_CUSTOMER.value

        return BuyerStage.UNKNOWN.value

    def extract_secondary_classifications(self, title: str, text: str, primary: str) -> List[str]:
        """Extracts multi-label facets characterizing secondary intent themes in discussion."""
        combined = f"{title}\n{text}".lower()
        secondaries = []

        # Battery / Storage
        if any(w in combined for w in ["battery", "powerwall", "enphase battery", "storage", "backup power", "franklinwh"]):
            if "BATTERY_STORAGE" not in secondaries and primary != "BATTERY_STORAGE":
                secondaries.append("BATTERY_STORAGE")

        # Quote Comparison
        if any(w in combined for w in self.QUOTE_COMPARISON_PHRASES) or self.ppw_regex.search(combined):
            if primary != CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER:
                secondaries.append("QUOTE_COMPARISON")

        # Installer Search
        if any(w in combined for w in self.INSTALLER_SEARCH_PHRASES):
            if primary != CLASSIFICATION_RESIDENTIAL_NEW_BUYER:
                secondaries.append("INSTALLER_RECOMMENDATION_SEARCH")

        # System Upgrade / Expansion
        if any(w in combined for w in UPGRADE_SIGNALS):
            if primary != CLASSIFICATION_RESIDENTIAL_UPGRADE:
                secondaries.append("SYSTEM_EXPANSION")

        # Existing Solar Owner
        if any(w in combined for w in EXISTING_OWNER_SIGNALS):
            if primary != CLASSIFICATION_RESIDENTIAL_EXISTING:
                secondaries.append("EXISTING_SOLAR_OWNER")

        # Service / Repair
        if any(w in combined for w in REPAIR_SIGNALS) or any(w in combined for w in ["inverter error", "replace inverter", "stopped working"]):
            if primary != CLASSIFICATION_RESIDENTIAL_REPAIR:
                secondaries.append("SERVICE_REPAIR_NEED")

        # Financing / Loan
        if any(w in combined for w in ["solar loan", "solar financing", "lease vs buy", "ppa", "power purchase agreement"]):
            secondaries.append("FINANCING_INQUIRY")

        # High Electric Bill Pain
        if any(w in combined for w in ["high electric bill", "huge power bill", "electric bill is", "utility bill is"]):
            secondaries.append("ELECTRIC_BILL_PAIN")

        return secondaries

    def calculate_qualification_dimensions(
        self,
        score: float,
        classification: str,
        title: str,
        text: str,
        evidence_snippets: List[str],
        state: Optional[str] = None,
        zip_code: Optional[str] = None,
        intent_age_days: Optional[int] = None,
        buyer_stage: str = "UNKNOWN",
        is_hypothetical: bool = False
    ) -> Dict[str, Any]:
        """
        Calculates 6-dimensional qualification confidence and standardized composite score Q:
        Q = 0.30*I + 0.20*R + 0.15*L + 0.15*F + 0.15*E + 0.05*B
        Enforces hard disqualification gates (commercial, spam, negative intent, purely hypothetical).
        Routes ambiguous or low-evidence opportunities to the human review queue.
        """
        lower = f"{title}\n{text}".lower()

        # 1. Purchase Intent (0.0 - 10.0 scale)
        p_intent = max(0.0, min(10.0, round(score, 1)))

        # 2. Residential Confidence
        if classification in (CLASSIFICATION_COMMERCIAL, CLASSIFICATION_SPAM_VENDOR, CLASSIFICATION_DIY_OFFGRID):
            res_conf = 0.0
        elif any(term in lower for term in self.RESIDENTIAL_CONTEXT_TERMS):
            res_conf = 1.0
        else:
            res_conf = 0.7

        # 3. Location Confidence
        is_third_person = any(tp in lower for tp in THIRD_PERSON_SIGNALS)
        if state and zip_code:
            loc_conf = 0.95
        elif state:
            loc_conf = 0.75
        elif zip_code:
            loc_conf = 0.80
        else:
            loc_conf = 0.20

        if is_third_person:
            loc_conf = round(loc_conf * 0.6, 2)

        # 4. Freshness Score
        if intent_age_days is None:
            fresh_score = 0.3
        elif intent_age_days <= 7:
            fresh_score = 1.0
        elif intent_age_days <= 30:
            fresh_score = 0.8
        elif intent_age_days <= 90:
            fresh_score = 0.4
        else:
            fresh_score = 0.1

        # 5. Evidence Confidence
        if not evidence_snippets:
            ev_conf = 0.0
        else:
            ev_conf = min(1.0, round(len(evidence_snippets) * 0.35, 2))

        # 6. Standardized 100-pt Composite Qualification Score Q (Gap Analysis V2)
        I_scaled = p_intent * 10.0     # 0 to 100
        R_scaled = res_conf * 100.0    # 0 to 100
        L_scaled = loc_conf * 100.0    # 0 to 100
        F_scaled = fresh_score * 100.0 # 0 to 100
        E_scaled = ev_conf * 100.0     # 0 to 100

        stage_weights = {
            "SEEKING_QUOTES": 100.0,
            "COMPARING_QUOTES": 95.0,
            "READY_TO_PURCHASE": 90.0,
            "PLANNING": 75.0,
            "EXPLORING": 50.0,
            "EXISTING_CUSTOMER": 40.0,
            "SERVICE_REQUIRED": 40.0,
            "UNKNOWN": 20.0
        }
        B_scaled = stage_weights.get(buyer_stage, 20.0)

        Q = round(0.30 * I_scaled + 0.20 * R_scaled + 0.15 * L_scaled + 0.15 * F_scaled + 0.15 * E_scaled + 0.05 * B_scaled, 1)

        # Hard Disqualification Gates
        hard_disqualified = False
        disqualification_reason = None
        needs_review = False
        review_reason = None

        if classification in (CLASSIFICATION_COMMERCIAL, CLASSIFICATION_SPAM_VENDOR, CLASSIFICATION_DIY_OFFGRID, CLASSIFICATION_GENERAL_INFO, CLASSIFICATION_TECHNICAL):
            hard_disqualified = True
            disqualification_reason = f"Excluded non-buyer category: {classification}"
            Q = 0.0
        elif p_intent <= 0.0:
            hard_disqualified = True
            disqualification_reason = "No positive buyer intent or net negative intent"
            Q = 0.0
        elif is_hypothetical and p_intent < 5.0:
            hard_disqualified = True
            disqualification_reason = "Purely hypothetical inquiry without buying intent"
            Q = 0.0

        if not hard_disqualified:
            if ev_conf == 0.0:
                needs_review = True
                review_reason = "Zero verifiable evidence quotes extracted"
            elif loc_conf < 0.3:
                needs_review = True
                review_reason = "Geographic location ambiguous or unverified"
            elif is_third_person:
                needs_review = True
                review_reason = "Third-person discussion signal detected"

        return {
            "purchase_intent_score": p_intent,
            "residential_confidence": res_conf,
            "location_confidence": loc_conf,
            "freshness_score": fresh_score,
            "evidence_confidence": ev_conf,
            "composite_qualification_score": Q,
            "hard_disqualified": hard_disqualified,
            "disqualification_reason": disqualification_reason,
            "needs_human_review": needs_review,
            "review_reason": review_reason
        }

    def extract_dates_and_freshness(self, soup: BeautifulSoup, text: str) -> Tuple[str, Optional[str], Optional[str]]:
        """
        Extracts original thread creation date and latest reply date.
        Classifies freshness into:
        '0-7 days', '8-30 days', '31-90 days', 'Older than 90 days', or 'Unknown'.
        Returns: (freshness_str, published_at_str, latest_reply_str)
        """
        created_str = None
        reply_str = None

        # 1. Meta Tags (article:published_time, article:modified_time, date)
        meta_pub = soup.find("meta", attrs={"property": "article:published_time"}) or soup.find("meta", attrs={"name": "date"})
        if meta_pub and meta_pub.get("content"):
            created_str = meta_pub["content"].strip()

        meta_mod = soup.find("meta", attrs={"property": "article:modified_time"})
        if meta_mod and meta_mod.get("content"):
            reply_str = meta_mod["content"].strip()

        # 2. Forum <time> tags (first time tag is creation date; last time tag is latest reply)
        time_tags = soup.find_all("time")
        if time_tags:
            first_time = time_tags[0].get("datetime") or time_tags[0].get_text(strip=True)
            if not created_str and first_time:
                created_str = first_time

            if len(time_tags) > 1:
                last_time = time_tags[-1].get("datetime") or time_tags[-1].get_text(strip=True)
                if last_time:
                    reply_str = last_time

        # Parse creation date
        parsed_created = None
        if created_str:
            try:
                parsed_created = dateparser.parse(created_str)
            except Exception:
                pass

        # Regex fallback for year (e.g. 2010, 2023, 2024, 2025, 2026)
        if not parsed_created:
            year_match = re.search(r"\b(20[0-2][0-6])\b", text[:600])
            if year_match:
                found_year = int(year_match.group(1))
                if found_year < 2026:
                    return LeadFreshness.OLDER_90.value, f"{found_year}-01-01", None

        if not parsed_created:
            return LeadFreshness.UNKNOWN.value, None, None

        now = datetime.now(timezone.utc)
        if parsed_created.tzinfo is None:
            parsed_created = parsed_created.replace(tzinfo=timezone.utc)

        age_days = (now - parsed_created).days
        iso_created = parsed_created.strftime("%Y-%m-%d")

        iso_reply = None
        if reply_str:
            try:
                dt_reply = dateparser.parse(reply_str)
                if dt_reply:
                    iso_reply = dt_reply.strftime("%Y-%m-%d")
            except Exception:
                pass

        if age_days <= 7:
            freshness = LeadFreshness.DAYS_0_7.value
        elif age_days <= 30:
            freshness = LeadFreshness.DAYS_8_30.value
        elif age_days <= 90:
            freshness = LeadFreshness.DAYS_31_90.value
        else:
            freshness = LeadFreshness.OLDER_90.value

        return freshness, iso_created, iso_reply

    def extract_property_type(self, text: str) -> Optional[str]:
        """Extracts property type: Single Family Residential, Commercial, or None."""
        lower = text.lower()
        if any(term in lower for term in ["single family", "my house", "our house", "my home", "rooftop on my home", "residence"]):
            return "Single Family Residential"
        if any(term in lower for term in ["warehouse", "commercial building", "business property"]):
            return "Commercial"
        return None

    def _extract_location(self, text: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        lower = text.lower()
        detected_state = None
        for state_name, abbrev in self.us_states.items():
            if re.search(r"\b" + re.escape(state_name) + r"\b", lower) or re.search(r"\b" + abbrev + r"\b", text):
                detected_state = abbrev
                break

        zip_match = self.zip_regex.search(text)
        detected_zip = zip_match.group(0) if zip_match else None
        return detected_state, None, detected_zip

    def _extract_author(self, soup: BeautifulSoup, url: str) -> str:
        reddit_author = soup.find("a", href=re.compile(r"/user/([a-zA-Z0-9_-]+)"))
        if reddit_author and reddit_author.string:
            return reddit_author.string.strip()

        forum_author = soup.find(class_=re.compile(r"(username|author|p-description)"))
        if forum_author:
            return forum_author.get_text(strip=True)[:40]

        meta_author = soup.find("meta", attrs={"name": "author"})
        if meta_author and meta_author.get("content"):
            return meta_author["content"].strip()[:40]

        return "Anonymous"

    def _extract_clean_text(self, html: str, soup: BeautifulSoup) -> str:
        try:
            clean_text = trafilatura.extract(
                html,
                include_comments=True,
                include_tables=True,
                no_fallback=False
            )
            if clean_text and len(clean_text) > 80:
                return clean_text
        except Exception as e:
            logger.debug(f"[Parser] Trafilatura extraction exception: {e}")

        return soup.get_text(separator=" ", strip=True)

    def score_buyer_intent(
        self,
        classification: str,
        title: str,
        text: str
    ) -> Tuple[float, List[str], List[str]]:
        """
        Calculates intent score strictly using verifiable evidence.
        Product mentions (Enphase, Powerwall, IQ7A) produce 0.0 standalone points.
        Returns: (score, matched_keywords, evidence_snippets)
        """
        # Exclude non-buyer categories from positive scoring
        if classification in (
            CLASSIFICATION_COMMERCIAL,
            CLASSIFICATION_TECHNICAL,
            CLASSIFICATION_GENERAL_INFO,
            CLASSIFICATION_SPAM_VENDOR,
            CLASSIFICATION_DIY_OFFGRID
        ):
            return 0.0, [], []

        score = 0.0
        matched_keywords = []
        evidence_snippets = []
        combined_text = f"{title}\n{text}"
        lower_text = combined_text.lower()

        # 1. Match verified intent phrases
        for phrase, weight in SOLAR_INTENT_WEIGHTS.items():
            if phrase in lower_text:
                matched_keywords.append(phrase)
                score += weight
                evidence = self._find_evidence_sentence(combined_text, phrase)
                if evidence:
                    evidence_snippets.append(f"Intent ({weight:+.1f}): \"{evidence}\"")
                else:
                    evidence_snippets.append(f"Matched signal: '{phrase}' ({weight:+.1f})")

        # 2. Price per watt quote evaluation ($X/W -> +4.0)
        ppw_match = self.ppw_regex.search(combined_text)
        if ppw_match:
            score += 4.0
            ppw_val = ppw_match.group(1)
            matched_keywords.append(f"Price signal (${ppw_val}/W)")
            evidence_snippets.append(f"Quote pricing evaluated: ${ppw_val}/watt (+4.0)")

        # 3. Monthly electric bill signal (+3.0 only if installation/quote intent is present)
        bill_match = self.bill_regex.search(combined_text)
        if bill_match and score > 0:
            bill_val = float(bill_match.group(1))
            if 30.0 <= bill_val <= 2500.0:
                score += 3.0
                matched_keywords.append(f"Electric bill stated (${bill_val:.0f}/mo)")
                evidence_snippets.append(f"Pain point cited: ${bill_val:.0f}/month electric bill (+3.0)")

        # 4. Advanced NLP: Negation Detection (-8.0)
        for neg in NEGATION_SIGNALS:
            if neg in lower_text:
                score -= 8.0
                matched_keywords.append(f"Negation: '{neg}'")
                evidence_snippets.append(f"Negative purchase intent detected: \"{neg}\" (-8.0)")

        # 5. Advanced NLP: Third-Person Context Detection
        for tp in THIRD_PERSON_SIGNALS:
            if tp in lower_text:
                matched_keywords.append(f"Third-party: '{tp}'")
                evidence_snippets.append(f"Third-party discussion signal: \"{tp}\" (reduced confidence)")

        # 6. Advanced NLP: Hypothetical / Academic Signals (-3.0)
        for hyp in HYPOTHETICAL_SIGNALS:
            if hyp in lower_text:
                score -= 3.0
                matched_keywords.append(f"Hypothetical: '{hyp}'")
                evidence_snippets.append(f"Hypothetical/academic inquiry detected: \"{hyp}\" (-3.0)")

        # Existing customer upgrade baseline
        if classification == CLASSIFICATION_RESIDENTIAL_UPGRADE:
            if score <= 0.0:
                score = 5.0
                evidence_snippets.append("Existing customer seeking panel expansion or battery addition (+5.0)")

        # Service customer repair baseline
        if classification == CLASSIFICATION_RESIDENTIAL_REPAIR:
            if score <= 0.0:
                score = 4.0
                evidence_snippets.append("Homeowner seeking solar system diagnosis, inverter repair, or replacement (+4.0)")

        score = max(-20.0, min(50.0, score))
        return round(score, 1), matched_keywords, evidence_snippets

    @staticmethod
    def determine_lead_segment(classification: str) -> str:
        """Categorizes into 3 separate customer segments: New Buyer, Existing Customer, Service Customer."""
        if classification in (CLASSIFICATION_RESIDENTIAL_NEW_BUYER, CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER):
            return LeadSegment.NEW_BUYER.value
        elif classification == CLASSIFICATION_RESIDENTIAL_UPGRADE:
            return LeadSegment.EXISTING_CUSTOMER.value
        elif classification == CLASSIFICATION_RESIDENTIAL_REPAIR:
            return LeadSegment.SERVICE_CUSTOMER.value
        return LeadSegment.NON_BUYER.value

    def determine_lead_category(self, score: float, classification: str, freshness: str) -> str:
        """
        Assigns lead category based on classification, score, and freshness.
        Old posts (>30 days) CANNOT be HOT_BUYER (must be WARM_BUYER or RESEARCHING).
        """
        hot_thresh = LEAD_THRESHOLDS["hot"]
        warm_thresh = LEAD_THRESHOLDS["warm"]
        researching_thresh = LEAD_THRESHOLDS["researching"]

        if classification in (CLASSIFICATION_RESIDENTIAL_NEW_BUYER, CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER):
            if score >= hot_thresh and freshness in (LeadFreshness.DAYS_0_7.value, LeadFreshness.DAYS_8_30.value):
                return LeadCategory.HOT_BUYER.value
            elif score >= warm_thresh:
                return LeadCategory.WARM_BUYER.value
            elif score >= researching_thresh:
                return LeadCategory.RESEARCHING.value
            else:
                return LeadCategory.NON_BUYER.value

        if classification == CLASSIFICATION_RESIDENTIAL_UPGRADE:
            return LeadCategory.EXISTING_CUSTOMER_EXPANSION.value

        if classification == CLASSIFICATION_RESIDENTIAL_REPAIR:
            return LeadCategory.WARM_BUYER.value if score >= warm_thresh else LeadCategory.RESEARCHING.value

        return LeadCategory.NON_BUYER.value

    def extract_structured_evidence(
        self,
        combined_text: str,
        matched_keywords: List[str],
        evidence_snippets: List[str],
        published_str: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Builds structured, auditable evidence records with extraction provenance.
        """
        records = []
        for snippet in evidence_snippets:
            ev_type = "PURCHASE_INTENT"
            polarity = "POSITIVE"
            weight = 3.0
            s_lower = snippet.lower()
            if "comparing quotes" in s_lower or "proposal" in s_lower:
                ev_type = "QUOTE_COMPARISON"
                weight = 4.0
            elif "bill" in s_lower or "electric" in s_lower:
                ev_type = "BILL_PAIN_POINT"
                weight = 3.0
            elif "price signal" in s_lower or "/watt" in s_lower:
                ev_type = "PRICE_PER_WATT"
                weight = 4.0
            elif "negat" in s_lower or "not looking" in s_lower or "not interested" in s_lower:
                ev_type = "NEGATION"
                polarity = "NEGATIVE"
                weight = -8.0
            elif "hypothetical" in s_lower:
                ev_type = "HYPOTHETICAL"
                polarity = "NEUTRAL"
                weight = -3.0
            elif "expansion" in s_lower or "upgrade" in s_lower or "storage" in s_lower:
                ev_type = "SYSTEM_UPGRADE"
                weight = 5.0
            elif "repair" in s_lower or "inverter" in s_lower:
                ev_type = "REPAIR_SERVICE"
                weight = 4.0

            clean_text = snippet
            if ":" in snippet:
                clean_text = snippet.split(":", 1)[1].strip().strip('"')

            records.append({
                "evidence_text": clean_text,
                "evidence_type": ev_type,
                "evidence_polarity": polarity,
                "published_at": published_str,
                "extraction_method": "RULE_REGEX_V2",
                "model_version": "2.0.0",
                "confidence": 0.95 if polarity == "POSITIVE" else 0.85,
                "score_contribution": weight
            })
        return records

    def build_and_save_lead(
        self,
        url: str,
        title: str,
        visible_text: str,
        author: str,
        classification: str,
        score: float,
        matched_keywords: List[str],
        evidence_snippets: List[str],
        published_str: Optional[str],
        latest_reply_str: Optional[str],
        location_raw: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Centralized factory for building, validating, enriching, and saving a qualified SolarLead.
        Ensures multi-dimensional scoring, solar opportunity estimation, data quality checks,
        and 3-timestamp tracking are applied consistently across all sources.
        """
        source_name = self._determine_source_name(url)
        lead_segment = self.determine_lead_segment(classification)
        secondary_classifications = self.extract_secondary_classifications(title, visible_text, classification)

        state, city, zip_code = self._extract_location(visible_text)
        if not state and location_raw:
            state, _, zip_code = self._extract_location(location_raw)

        snippet = visible_text[:350].replace("\n", " ")
        property_type = self.extract_property_type(visible_text)

        cap_val, cap_unit = self.extract_capacity(visible_text)
        extracted_kw = cap_val if cap_unit == "kW" else None

        bill_match = self.bill_regex.search(visible_text)
        extracted_bill = float(bill_match.group(1)) if bill_match else None

        # 3-Timestamp Intelligence
        thread_created_at = published_str
        latest_thread_activity_at = latest_reply_str or published_str
        last_buyer_intent_at = published_str  # Intent occurred with buyer post

        # Dual Freshness
        now = datetime.now(timezone.utc)
        intent_age_days = None
        if published_str:
            try:
                dt_intent = dateparser.parse(published_str)
                if dt_intent:
                    if dt_intent.tzinfo is None:
                        dt_intent = dt_intent.replace(tzinfo=timezone.utc)
                    intent_age_days = (now - dt_intent).days
            except Exception:
                pass

        if intent_age_days is not None:
            buyer_intent_freshness = (
                LeadFreshness.DAYS_0_7.value if intent_age_days <= 7 else (
                    LeadFreshness.DAYS_8_30.value if intent_age_days <= 30 else (
                        LeadFreshness.DAYS_31_90.value if intent_age_days <= 90 else LeadFreshness.OLDER_90.value
                    )
                )
            )
        else:
            buyer_intent_freshness = LeadFreshness.UNKNOWN.value

        # Thread Freshness (based on latest reply activity)
        thread_age_days = intent_age_days
        if latest_reply_str:
            try:
                dt_rep = dateparser.parse(latest_reply_str)
                if dt_rep:
                    if dt_rep.tzinfo is None:
                        dt_rep = dt_rep.replace(tzinfo=timezone.utc)
                    thread_age_days = (now - dt_rep).days
            except Exception:
                pass

        if thread_age_days is not None:
            thread_freshness = (
                LeadFreshness.DAYS_0_7.value if thread_age_days <= 7 else (
                    LeadFreshness.DAYS_8_30.value if thread_age_days <= 30 else (
                        LeadFreshness.DAYS_31_90.value if thread_age_days <= 90 else LeadFreshness.OLDER_90.value
                    )
                )
            )
        else:
            thread_freshness = buyer_intent_freshness

        lead_category = self.determine_lead_category(score, classification, buyer_intent_freshness)
        buyer_stage = self.extract_buyer_stage(classification, title, visible_text)
        is_hypo = any(h in visible_text.lower() for h in HYPOTHETICAL_SIGNALS)

        # Multi-Dimensional Qualification Dimensions with Standardized Formula Q
        qual_dims = self.calculate_qualification_dimensions(
            score=score,
            classification=classification,
            title=title,
            text=visible_text,
            evidence_snippets=evidence_snippets,
            state=state,
            zip_code=zip_code,
            intent_age_days=intent_age_days,
            buyer_stage=buyer_stage,
            is_hypothetical=is_hypo
        )

        # Structured Evidence Records with Provenance & Timestamp Tracking
        structured_evidence = self.extract_structured_evidence(
            combined_text=f"{title}\n{visible_text}",
            matched_keywords=matched_keywords,
            evidence_snippets=evidence_snippets,
            published_str=published_str
        )

        # Lifecycle & Qualification Routing (Hard Gates & Review Queue)
        if qual_dims["hard_disqualified"]:
            lead_status = "DISQUALIFIED"
            qual_status = QualificationStatus.DISQUALIFIED.value
        elif qual_dims["needs_human_review"]:
            lead_status = LeadLifecycleStatus.PENDING_REVIEW.value
            qual_status = QualificationStatus.PENDING_REVIEW.value
        elif qual_dims["composite_qualification_score"] >= 60.0:
            lead_status = LeadLifecycleStatus.QUALIFIED.value
            qual_status = QualificationStatus.QUALIFIED.value
        else:
            lead_status = LeadLifecycleStatus.NEW.value
            qual_status = QualificationStatus.QUALIFIED.value if score >= 4.0 else QualificationStatus.DISQUALIFIED.value

        # Solar Opportunity Estimation
        opportunity = SolarOpportunityEstimator.estimate_opportunity(
            electricity_bill=extracted_bill,
            state=state,
            reported_kw=extracted_kw
        )

        lead_data = {
            "source_name": source_name,
            "source_url": urlparse(url).netloc,
            "post_url": url,
            "author": author,
            "post_title": title,
            "intent_snippet": snippet,
            "lead_score": score,
            "lead_category": lead_category,
            "lead_segment": lead_segment,
            "classification_type": classification,
            "primary_classification": classification,
            "secondary_classifications": secondary_classifications,
            "lead_freshness": buyer_intent_freshness,
            "published_at": published_str,
            "latest_reply_at": latest_reply_str,
            "thread_created_at": thread_created_at,
            "latest_thread_activity_at": latest_thread_activity_at,
            "last_buyer_intent_at": last_buyer_intent_at,
            "thread_freshness": thread_freshness,
            "buyer_intent_freshness": buyer_intent_freshness,
            "purchase_intent_score": qual_dims["purchase_intent_score"],
            "residential_confidence": qual_dims["residential_confidence"],
            "location_confidence": qual_dims["location_confidence"],
            "freshness_score": qual_dims["freshness_score"],
            "evidence_confidence": qual_dims["evidence_confidence"],
            "composite_qualification_score": qual_dims["composite_qualification_score"],
            "hard_disqualified": qual_dims["hard_disqualified"],
            "disqualification_reason": qual_dims["disqualification_reason"],
            "needs_human_review": qual_dims["needs_human_review"],
            "review_reason": qual_dims["review_reason"],
            "structured_evidence": structured_evidence,
            "lead_status": lead_status,
            "qualification_status": qual_status,
            "property_type": property_type,
            "country": "USA",
            "state": state,
            "city": city,
            "zip_code": zip_code,
            "system_size_kw": extracted_kw,
            "electricity_bill": extracted_bill,
            "estimated_monthly_usage_kwh": opportunity.get("estimated_monthly_usage_kwh"),
            "estimated_annual_usage_kwh": opportunity.get("estimated_annual_usage_kwh"),
            "estimated_system_size_kw": opportunity.get("estimated_system_size_kw"),
            "estimated_solar_offset_percent": opportunity.get("estimated_solar_offset_percent"),
            "buyer_stage": buyer_stage,
            "matched_keywords": matched_keywords,
            "evidence_snippets": evidence_snippets
        }

        # Data Quality & Integrity Validation
        val_result = LeadDataQualityValidator.validate_lead(lead_data)
        lead_data["validation_status"] = val_result["status"]
        lead_data["validation_flags"] = val_result["flags"]

        try:
            lead_model = SolarLead(**lead_data)
            lead_dict = lead_model.model_dump()
            self.storage.save_solar_lead(lead_dict)
            logger.info(
                f"★ [{lead_segment} | {lead_category}] Score: {score:.1f} | Freshness: {buyer_intent_freshness} | "
                f"Stage: {buyer_stage} | Author: {author} | Title: {title[:50]}... | URL: {url}"
            )
            return lead_dict
        except Exception as e:
            logger.error(f"[Parser Validation Error] {url}: {e}", exc_info=True)
            return None

    def parse_reddit_rss(self, source_url: str, xml_content: str) -> Dict[str, Any]:
        """Parses live Reddit RSS feed entries into structured post dictionaries."""
        soup = BeautifulSoup(xml_content, "xml")
        posts = []
        entries = soup.find_all("entry")
        latest_date = None

        for entry in entries:
            title = entry.title.get_text(strip=True) if entry.title else ""
            link_tag = entry.find("link", href=True)
            post_url = link_tag["href"] if link_tag else source_url

            author_tag = entry.find("author")
            author = author_tag.find("name").get_text(strip=True) if (author_tag and author_tag.find("name")) else "Anonymous"
            if author.startswith("/u/"):
                author = author[3:]

            content_tag = entry.find("content")
            raw_content = content_tag.get_text() if content_tag else ""
            c_soup = BeautifulSoup(raw_content, "lxml")
            selftext = c_soup.get_text(separator=" ", strip=True)

            updated_tag = entry.find("updated")
            created_utc = None
            if updated_tag and updated_tag.text:
                try:
                    dt = datetime.fromisoformat(updated_tag.text.strip())
                    created_utc = dt.timestamp()
                    if not latest_date:
                        latest_date = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    pass

            posts.append({
                "title": title,
                "selftext": selftext,
                "author": author,
                "post_url": post_url,
                "created_utc": created_utc,
                "score": 1
            })

        logger.info(f"[Reddit RSS] Successfully parsed {len(posts)} live posts from {source_url}")
        res = self.parse_reddit_posts_list(source_url, posts)
        res["latest_post_date"] = latest_date
        return res

    def parse_reddit_posts_list(self, source_url: str, posts: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Parses structured posts delivered from PRAW or RSS feeds."""
        outbound_links: Set[str] = set()
        leads_discovered = []
        latest_date = None

        for data in posts:
            title = data.get("title", "")
            selftext = data.get("selftext", "")
            author = data.get("author", "Anonymous")
            post_url = data.get("post_url", source_url)

            if post_url:
                outbound_links.add(post_url)

            combined_text = f"{title}\n{selftext}"
            classification, class_reasons = self.classify_content(title, selftext)

            # Reject commercial, technical, or general informational posts
            if classification not in (
                CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
                CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER,
                CLASSIFICATION_RESIDENTIAL_UPGRADE,
                CLASSIFICATION_RESIDENTIAL_REPAIR
            ):
                logger.debug(f"[Parser Reddit Dropped] {classification}: {title[:50]}")
                continue

            score, matched_keywords, evidence_snippets = self.score_buyer_intent(classification, title, selftext)
            buyer_stage = self.extract_buyer_stage(classification, title, selftext)
            lead_segment = self.determine_lead_segment(classification)

            # Freshness
            created_utc = data.get("created_utc")
            if created_utc:
                dt = datetime.fromtimestamp(created_utc, tz=timezone.utc)
                age_days = (datetime.now(timezone.utc) - dt).days
                freshness = LeadFreshness.DAYS_0_7.value if age_days <= 7 else (
                    LeadFreshness.DAYS_8_30.value if age_days <= 30 else (
                        LeadFreshness.DAYS_31_90.value if age_days <= 90 else LeadFreshness.OLDER_90.value
                    )
                )
                published_str = dt.strftime("%Y-%m-%d")
                if not latest_date:
                    latest_date = published_str
            else:
                freshness = LeadFreshness.UNKNOWN.value
                published_str = None

            lead_category = self.determine_lead_category(score, classification, freshness)

            if lead_category in (
                LeadCategory.HOT_BUYER.value,
                LeadCategory.WARM_BUYER.value,
                LeadCategory.EXISTING_CUSTOMER_EXPANSION.value,
                LeadCategory.RESEARCHING.value
            ):
                lead_dict = self.build_and_save_lead(
                    url=post_url,
                    title=title,
                    visible_text=combined_text,
                    author=author,
                    classification=classification,
                    score=score,
                    matched_keywords=matched_keywords,
                    evidence_snippets=evidence_snippets,
                    published_str=published_str,
                    latest_reply_str=None
                )
                if lead_dict:
                    leads_discovered.append(lead_dict)

        if leads_discovered:
            self.storage.update_source_metrics(
                domain="reddit.com",
                pages_downloaded=1,
                leads_found=len(leads_discovered),
                posts_discovered=len(posts),
                latest_post_date=latest_date
            )

        return {
            "is_duplicate": False,
            "title": f"Reddit: {source_url}",
            "outbound_links": list(outbound_links),
            "solar_lead": leads_discovered[0] if leads_discovered else None
        }

    def parse_page(self, url: str, html: str, depth: int) -> Dict[str, Any]:
        """Parses HTML, classifies content, scores buyer intent with evidence, and extracts links."""
        source_name = self._determine_source_name(url)
        domain = urlparse(url).netloc
        adapter = get_adapter_for_url(url)

        # Automatic handling for Reddit RSS/XML live feeds
        if html.strip().startswith("<?xml") or "<feed " in html[:500] or "<rss" in html[:500]:
            if "reddit.com" in domain or "<feed xmlns=" in html[:300]:
                return self.parse_reddit_rss(url, html)

        # Conservative Business Contact Enrichment (Official contact emails, phones, forms)
        try:
            contacts = self.contact_extractor.extract_business_contacts(url, html, source_name)
            for c in contacts:
                self.storage.save_business_contact(c)
        except Exception as e:
            logger.debug(f"[ContactExtractor Error] {url}: {e}")

        soup = BeautifulSoup(html, "lxml")

        for element in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
            element.decompose()

        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        visible_text = self._extract_clean_text(html, soup)

        # Content Deduplication (Atomic & Concurrency-Safe)
        exact_hash = Deduplicator.hash_content_exact(visible_text)
        simhash = Deduplicator.compute_simhash(visible_text)

        # Atomic insert eliminates race condition between concurrent parser workers
        is_new_exact = self.storage.save_content_hash_atomic(exact_hash, simhash, url, title)
        if not is_new_exact:
            logger.info(f"[Parser Discarded] Exact duplicate content hash (atomic): {url}")
            self.storage.update_source_metrics(domain=domain, pages_downloaded=1, leads_found=0, duplicates=1)
            return {"is_duplicate": True, "duplicate_type": "exact", "outbound_links": [], "solar_lead": None}

        near_duplicate = self.storage.check_near_duplicate_simhash(simhash, max_hamming_distance=3)
        if near_duplicate:
            dup_url, dist = near_duplicate
            logger.info(f"[Parser Discarded] Near-duplicate of {dup_url} (Hamming Dist: {dist}): {url}")
            self.storage.update_source_metrics(domain=domain, pages_downloaded=1, leads_found=0, duplicates=1)
            return {"is_duplicate": True, "duplicate_type": "simhash", "outbound_links": [], "solar_lead": None}

        # Determine thread vs index page
        is_discussion = self._is_discussion_thread(url)
        solar_lead = None
        published_str = None
        classification = "INDEX_PAGE"
        class_reasons = ["Category or forum index"]

        if not is_discussion:
            logger.info(f"[Parser Info] Link harvesting on Category/Index page (Lead scoring bypassed): {url}")
        else:
            # 10-Way Content Classification
            classification, class_reasons = self.classify_content(title, visible_text)
            logger.debug(f"[Parser Classify] {url} -> {classification} ({class_reasons})")

            # Strictly reject commercial, technical, and general informational discussions from buyer leads
            if classification == CLASSIFICATION_COMMERCIAL:
                logger.info(f"[Parser Rejected Commercial] {url} -> {class_reasons[0]}")
            elif classification == CLASSIFICATION_TECHNICAL:
                logger.info(f"[Parser Rejected Technical] {url} -> {class_reasons[0]}")
            elif classification == CLASSIFICATION_DIY_OFFGRID:
                logger.info(f"[Parser Rejected DIY] {url} -> {class_reasons[0]}")
            elif classification == CLASSIFICATION_SPAM_VENDOR:
                logger.info(f"[Parser Rejected Spam] {url} -> {class_reasons[0]}")
            elif classification == CLASSIFICATION_GENERAL_INFO:
                logger.info(f"[Parser Dropped General Info] {url} -> {class_reasons[0]}")
            elif classification == CLASSIFICATION_RESIDENTIAL_EXISTING:
                logger.info(f"[Parser Segment: Existing Owner] {url} -> {class_reasons[0]}")
            elif classification in (
                CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
                CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER,
                CLASSIFICATION_RESIDENTIAL_UPGRADE,
                CLASSIFICATION_RESIDENTIAL_REPAIR
            ):
                # Valid residential lead (New Buyer, Upgrade, or Repair) -> score with evidence
                score, matched_keywords, evidence_snippets = self.score_buyer_intent(
                    classification, title, visible_text
                )
                freshness, published_str, latest_reply_str = self.extract_dates_and_freshness(soup, visible_text)

                # Source-Aware Thread Data Extraction (author, OP creation date, reply date)
                thread_data = adapter.extract_thread_data(soup, url)
                if thread_data:
                    if thread_data.get("published_at"):
                        published_str = thread_data["published_at"]
                    if thread_data.get("latest_reply_at"):
                        latest_reply_str = thread_data["latest_reply_at"]

                author = self._extract_author(soup, url)
                if thread_data and thread_data.get("author") and thread_data["author"] != "Anonymous":
                    author = thread_data["author"]
                location_raw = thread_data.get("location_raw") if thread_data else None

                # Only evaluate genuine leads with sufficient text and evidence
                if len(visible_text) >= 120 and evidence_snippets:
                    solar_lead = self.build_and_save_lead(
                        url=url,
                        title=title,
                        visible_text=visible_text,
                        author=author,
                        classification=classification,
                        score=score,
                        matched_keywords=matched_keywords,
                        evidence_snippets=evidence_snippets,
                        published_str=published_str,
                        latest_reply_str=latest_reply_str,
                        location_raw=location_raw
                    )

        # Outbound Link Extraction with Recursive Discovery Engine
        outbound_links: Set[str] = set()

        # Structured recursive discovery via adapter
        try:
            for cat_link in adapter.discover_categories(soup, url):
                outbound_links.add(cat_link)
            for thr_link in adapter.discover_threads(soup, url):
                outbound_links.add(thr_link)
            for pag_link in adapter.discover_pagination(soup, url):
                outbound_links.add(pag_link)
            if is_discussion:
                for rel_link in adapter.discover_related_threads(soup, url):
                    outbound_links.add(rel_link)
        except Exception as e:
            logger.debug(f"[Recursive Discovery Error] {url}: {e}")

        # General outbound link extraction with adapter pre-filtering
        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href or href.startswith(("#", "javascript:", "mailto:", "tel:")):
                continue
            absolute_url = urljoin(url, href).split("#")[0]
            target_adapter = get_adapter_for_url(absolute_url)
            is_allowed, _ = target_adapter.is_valid_url(absolute_url)
            if is_allowed and target_adapter.classify_url_type(absolute_url) != "IGNORED":
                outbound_links.add(absolute_url)
                continue
            absolute_url = urljoin(url, href).split("#")[0]
            target_adapter = get_adapter_for_url(absolute_url)
            is_allowed, _ = target_adapter.is_valid_url(absolute_url)
            if is_allowed and target_adapter.classify_url_type(absolute_url) != "IGNORED":
                outbound_links.add(absolute_url)

        # Update source quality tracking metrics
        try:
            self.storage.update_source_metrics(
                domain=domain,
                pages_downloaded=1,
                leads_found=1 if solar_lead else 0,
                posts_discovered=len(outbound_links),
                duplicates=0,
                latest_post_date=published_str if solar_lead else None
            )
        except Exception as e:
            logger.debug(f"[Source Metrics Error] {url}: {e}")

        return {
            "is_duplicate": False,
            "title": title,
            "outbound_links": list(outbound_links),
            "solar_lead": solar_lead,
            "classification": classification,
            "class_reasons": class_reasons
        }
