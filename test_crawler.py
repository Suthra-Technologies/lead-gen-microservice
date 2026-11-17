"""
Unit and Regression Test Suite for Solar Lead Discovery & Buyer Intent Engine
Tests:
1. URL Canonicalization & Forum Thread Normalization (Stripping ?p=... while keeping q=...)
2. Frontier filtering & Allowlist enforcement (dropping /filedata/fetch, /member/, commercial categories, > page 3)
3. 10-Way Content Classification (Residential New Buyer vs Quote Seeker vs Upgrade vs Repair vs Commercial vs Technical vs DIY)
4. Contextual Capacity Extraction (8 MW commercial vs 8.5 kW residential vs BMW in unrelated text)
5. Regression Tests for 4 Specific Discussions (ID 4, ID 5, ID 6, ID 7)
6. Standalone Product Mentions (Enphase, Tesla Powerwall alone produce 0 buyer intent)
7. Lead Intent Scoring, Property Extraction & Evidence Snippet Generation
8. Lead Freshness & Date Extraction (0-7 days vs Older than 90 days)
9. Deno Gateway Health Check and Fallback Logic
"""

import os
import sys
import unittest
import asyncio
from pathlib import Path
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

from deduplicator import Deduplicator
from frontier import URLFrontier
from storage import CrawlerStorage
from parser import ContentParser
from models import SolarLead, ClassificationType, LeadFreshness, LeadCategory, BuyerStage
from config import (
    CLASSIFICATION_COMMERCIAL,
    CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
    CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER,
    CLASSIFICATION_RESIDENTIAL_EXISTING,
    CLASSIFICATION_RESIDENTIAL_UPGRADE,
    CLASSIFICATION_RESIDENTIAL_REPAIR,
    CLASSIFICATION_TECHNICAL,
    CLASSIFICATION_DIY_OFFGRID,
    CLASSIFICATION_GENERAL_INFO,
    CLASSIFICATION_SPAM_VENDOR,
    DATA_DIR
)
from downloader import HTMLDownloader
import httpx


class TestDeduplicationAndNormalization(unittest.TestCase):
    def test_forum_thread_anchor_parameter_stripped(self):
        """Thread URL with ?p=443368 should be normalized to canonical thread URL."""
        raw_url = "https://www.solarpaneltalk.com/forum/solar-panels-for-home/12345-my-first-solar-quote?p=443368"
        canonical, domain, url_hash = Deduplicator.canonicalize_url(raw_url)
        
        self.assertEqual(domain, "www.solarpaneltalk.com")
        self.assertNotIn("p=", canonical)
        self.assertEqual(
            canonical,
            "https://www.solarpaneltalk.com/forum/solar-panels-for-home/12345-my-first-solar-quote"
        )

    def test_search_query_parameters_preserved(self):
        """Search URLs must retain query parameters like q= and sort=."""
        raw_url = "https://www.reddit.com/r/solar/search.json?sort=new&q=solar+quote&limit=25"
        canonical, domain, url_hash = Deduplicator.canonicalize_url(raw_url)

        self.assertIn("q=solar+quote", canonical)
        self.assertIn("sort=new", canonical)
        self.assertIn("limit=25", canonical)

    def test_utm_and_fragments_stripped(self):
        """Marketing utm tags and HTML fragments (#post1234) must be removed."""
        raw_url = "https://www.solarpaneltalk.com/forum/solar-panels-for-home/12345-quote?utm_source=chatgpt&utm_medium=referral#post-999"
        canonical, domain, url_hash = Deduplicator.canonicalize_url(raw_url)

        self.assertNotIn("utm_source", canonical)
        self.assertNotIn("utm_medium", canonical)
        self.assertNotIn("#post-999", canonical)


class TestFrontierFiltering(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(DATA_DIR / "test_storage.db")
        self.frontier = URLFrontier(self.storage)

    def test_allowlist_domains_accepted(self):
        allowed, reason = self.frontier.should_crawl("https://www.solarpaneltalk.com/forum/solar-panels-for-home", depth=0)
        self.assertTrue(allowed, f"Should allow solarpaneltalk: {reason}")

    def test_external_affiliates_rejected(self):
        allowed, reason = self.frontier.should_crawl("https://www.amazon.com/dp/B08SolarInverter", depth=1)
        self.assertFalse(allowed)
        self.assertIn("ALLOWED_DOMAINS", reason)

    def test_blocked_patterns_rejected(self):
        allowed, _ = self.frontier.should_crawl("https://www.solarpaneltalk.com/filedata/fetch?id=12345", depth=1)
        self.assertFalse(allowed)

        allowed, _ = self.frontier.should_crawl("https://www.solarpaneltalk.com/member/9876-john-doe", depth=1)
        self.assertFalse(allowed)

        allowed, _ = self.frontier.should_crawl("https://www.solarpaneltalk.com/forum/commercial-solar-lighting/warehouse", depth=1)
        self.assertFalse(allowed)

    def test_deep_pagination_rejected_to_prevent_old_archives(self):
        """URLs exceeding page 3 pagination should be rejected to protect crawl budget."""
        allowed, reason = self.frontier.should_crawl("https://www.solarpaneltalk.com/forum/solar-panels-for-home/page11", depth=1)
        self.assertFalse(allowed)
        self.assertIn("pagination freshness limit", reason)

    def test_source_adapter_blocks_houzz_contractors_and_spt_commercial(self):
        """Source-aware adapters must reject contractor directories and commercial lighting before enqueueing."""
        # Houzz contractor directory in Pune, India
        houzz_pune = "https://www.houzz.com/professionals/landscape-contractors/pune-16-in-probr0-bo~t_11812~r_1259229"
        allowed, reason = self.frontier.should_crawl(houzz_pune, depth=1)
        self.assertFalse(allowed)
        self.assertIn("Houzz", reason)

        # SolarPanelTalk commercial lighting
        spt_comm = "https://www.solarpaneltalk.com/forum/solar-power-lighting/commercial-solar-lighting"
        allowed, reason = self.frontier.should_crawl(spt_comm, depth=1)
        self.assertFalse(allowed)


class TestContentClassificationAndContextualCapacity(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(DATA_DIR / "test_storage.db")
        self.parser = ContentParser(self.storage)

    def test_commercial_1_5_mw_rejected(self):
        """Thread with 1.5 MW rooftop project must be classified as COMMERCIAL_SOLAR."""
        title = "1.5 MW rooftop project for commercial warehouse"
        body = (
            "We are planning a 1.5 MW commercial solar rooftop installation on our logistics center. "
            "Looking for 3-phase commercial inverters and medium voltage interconnection details."
        )
        classification, reasons = self.parser.classify_content(title, body)
        self.assertEqual(classification, CLASSIFICATION_COMMERCIAL)
        self.assertTrue(any("1.5 MW" in r or "Commercial" in r for r in reasons))

    def test_mw_in_unrelated_word_not_falsely_rejected(self):
        """Words like 'BMW' or 'somewhere' or 'MW' in policy without capacity must NOT trigger commercial rejection."""
        title = "Trip to home improvement store in my BMW"
        body = "I drove somewhere in Phoenix to see solar panels for my house rooftop. The utility company mentioned MW in their rate hearing."
        classification, reasons = self.parser.classify_content(title, body)
        self.assertNotEqual(classification, CLASSIFICATION_COMMERCIAL)

    def test_residential_turnkey_accepted(self):
        """Homeowner comparing quotes for residential rooftop must be RESIDENTIAL_QUOTE_SEEKER or NEW_BUYER."""
        title = "Need advice on solar quote for my home in Arizona"
        body = (
            "Hi everyone, I just received my first quote for residential rooftop solar on my house. "
            "They proposed an 8.5 kW system with 21 panels for $24,000 ($2.82/watt). "
            "My current electric bill is $220/month. Does this quote look fair or should I keep looking for installers?"
        )
        classification, reasons = self.parser.classify_content(title, body)
        self.assertIn(classification, (CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER, CLASSIFICATION_RESIDENTIAL_NEW_BUYER))

    def test_technical_troubleshooting_rejected(self):
        """Optimizer error codes and busbar torque specs must be classified as TECHNICAL_DISCUSSION."""
        title = "Inverter error code 32 and optimizer replacement"
        body = (
            "My SolarEdge optimizer 4 failed with error 32. Does anyone know the busbar torque specs "
            "and DC fusing requirements according to NEC 690?"
        )
        classification, reasons = self.parser.classify_content(title, body)
        self.assertEqual(classification, CLASSIFICATION_TECHNICAL)

    def test_diy_offgrid_rejected(self):
        """Houseboat/RV battery build must be classified as DIY_OFFGRID."""
        title = "DIY 48V LiFePO4 battery build for off grid houseboat"
        body = (
            "Building my own 16s battery pack with 280Ah EVE cells for my off grid houseboat setup. "
            "Any recommendations on a JK BMS with active balancer?"
        )
        classification, reasons = self.parser.classify_content(title, body)
        self.assertEqual(classification, CLASSIFICATION_DIY_OFFGRID)

    def test_vendor_spam_rejected(self):
        """Solar installer advertisements must be classified as SPAM_VENDOR."""
        title = "Premier Solar Contractors in Dallas"
        body = "We provide solar installation services across Texas. Call our team today for a free consultation from us."
        classification, reasons = self.parser.classify_content(title, body)
        self.assertEqual(classification, CLASSIFICATION_SPAM_VENDOR)


class TestRealDiscussionsRegressionSuite(unittest.TestCase):
    """
    Regression tests for the four specific discussions analyzed in the review:
    ID 4: PV systems pricing dropping in 2023 (General info / policy)
    ID 6: myEnergy additional panels to existing set up with IQ7A inverters (Existing owner upgrade)
    ID 7: Solar Movies: Solar Panel Installation Movie (Technical / video)
    ID 5: Upgrade Existing Solar Panels (Existing owner upgrade/repair)
    """
    def setUp(self):
        self.storage = CrawlerStorage(DATA_DIR / "test_storage.db")
        self.parser = ContentParser(self.storage)

    def test_id4_pricing_dropping_is_general_info(self):
        """ID 4: Policy discussion on net metering & POCOs must be GENERAL_INFORMATION and produce 0.0 intent score."""
        title = "PV systems pricing dropping in 2023 - Solar Panels Forum"
        body = (
            "One of the arguments the POCOs use to justify attacks on net metering. "
            "Next year we will see how utility rate cases affect residential adoption."
        )
        classification, reasons = self.parser.classify_content(title, body)
        self.assertIn(classification, (CLASSIFICATION_GENERAL_INFO, CLASSIFICATION_TECHNICAL))
        
        score, keywords, evidence = self.parser.score_buyer_intent(classification, title, body)
        self.assertEqual(score, 0.0)
        category = self.parser.determine_lead_category(score, classification, LeadFreshness.OLDER_90.value)
        self.assertEqual(category, LeadCategory.NON_BUYER.value)

    def test_id6_additional_panels_is_existing_upgrade(self):
        """ID 6: Existing solar customer wanting additional panels must be RESIDENTIAL_UPGRADE, not a new buyer."""
        title = "myEnergy additional panels to existing set up with IQ7A inverters"
        body = (
            "Hi, we have an existing set up professionally installed by a MSC registered agent. "
            "Currently we have 4 panels on one outbuilding with an enphase envoy and Tesla gateway. "
            "We want additional panels to existing set up."
        )
        classification, reasons = self.parser.classify_content(title, body)
        self.assertEqual(classification, CLASSIFICATION_RESIDENTIAL_UPGRADE)
        
        stage = self.parser.extract_buyer_stage(classification, title, body)
        self.assertEqual(stage, BuyerStage.EXISTING_CUSTOMER.value)
        
        score, keywords, evidence = self.parser.score_buyer_intent(classification, title, body)
        category = self.parser.determine_lead_category(score, classification, LeadFreshness.OLDER_90.value)
        self.assertEqual(category, LeadCategory.EXISTING_CUSTOMER_EXPANSION.value)

    def test_id7_solar_movies_is_technical_rejected(self):
        """ID 7: 2010 video of installation must be TECHNICAL_DISCUSSION and rejected from leads."""
        title = "Solar Movies: Solar Panel Installation Movie"
        body = (
            "Link to a you tube video commercial install, filmed by homeowner. "
            "18 Sharp 235-Watt panels with Enphase M190 inverters."
        )
        classification, reasons = self.parser.classify_content(title, body)
        self.assertIn(classification, (CLASSIFICATION_TECHNICAL, CLASSIFICATION_GENERAL_INFO))
        
        score, keywords, evidence = self.parser.score_buyer_intent(classification, title, body)
        self.assertEqual(score, 0.0)
        category = self.parser.determine_lead_category(score, classification, LeadFreshness.OLDER_90.value)
        self.assertEqual(category, LeadCategory.NON_BUYER.value)

    def test_id5_upgrade_existing_panels_is_existing_upgrade_or_repair(self):
        """ID 5: 12-year old install on garage must be RESIDENTIAL_UPGRADE or REPAIR, not a new turnkey buyer."""
        title = "Upgrade Existing Solar Panels"
        body = (
            "I have an existing 12 year old solar install on my garage using 8 x Enphase Microinverters "
            "and Kyocera 240w panels. It's a 1.92 kW system. Panels are degraded and inverters need upgrade."
        )
        classification, reasons = self.parser.classify_content(title, body)
        self.assertIn(classification, (CLASSIFICATION_RESIDENTIAL_UPGRADE, CLASSIFICATION_RESIDENTIAL_REPAIR))
        self.assertNotEqual(classification, CLASSIFICATION_RESIDENTIAL_NEW_BUYER)

    def test_product_mentions_alone_produce_zero_intent(self):
        """Mentioning product names (Enphase, Tesla Powerwall) alone without purchase intent must yield 0.0 score."""
        title = "My garage solar setup"
        body = "I have an existing solar system with Enphase microinverters and a Tesla Powerwall. Asking about net metering."
        classification, reasons = self.parser.classify_content(title, body)
        
        # When scored without explicit purchase phrase
        score, keywords, evidence = self.parser.score_buyer_intent(CLASSIFICATION_RESIDENTIAL_EXISTING, title, body)
        self.assertEqual(score, 0.0)
        category = self.parser.determine_lead_category(score, classification, LeadFreshness.DAYS_0_7.value)
        self.assertEqual(category, LeadCategory.NON_BUYER.value)


class TestLeadScoringFreshnessAndEvidence(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(DATA_DIR / "test_storage.db")
        self.parser = ContentParser(self.storage)

    def test_freshness_classification_recent(self):
        """HTML with date from 2 days ago should be 0-7 days."""
        now = datetime.now(timezone.utc)
        recent_date = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        html = f'<html><head><meta property="article:published_time" content="{recent_date}"></head><body>Text</body></html>'
        soup = BeautifulSoup(html, "lxml")
        freshness, created, reply = self.parser.extract_dates_and_freshness(soup, "Text")
        self.assertEqual(freshness, LeadFreshness.DAYS_0_7.value)
        self.assertIsNotNone(created)

    def test_freshness_classification_old(self):
        """HTML referencing 2023 or older should be categorized as Older than 90 days."""
        html = '<html><body><time datetime="2023-04-15">April 15, 2023</time><p>Old solar discussion</p></body></html>'
        soup = BeautifulSoup(html, "lxml")
        freshness, created, reply = self.parser.extract_dates_and_freshness(soup, "Old solar discussion")
        self.assertEqual(freshness, LeadFreshness.OLDER_90.value)

    def test_evidence_sentence_extraction(self):
        """Exact sentence demonstrating intent must be extracted for human auditing."""
        text = (
            "Hello neighbors. We recently purchased our single family house in Mesa. "
            "We are looking for a solar installer to help us go solar. "
            "Our power bill is getting too high during the summer months."
        )
        sentence = self.parser._find_evidence_sentence(text, "looking for a solar installer")
        self.assertIsNotNone(sentence)
        self.assertIn("looking for a solar installer", sentence.lower())

    def test_pydantic_solar_lead_validation(self):
        """Valid residential lead parses and passes strict Pydantic schema validation."""
        lead = SolarLead(
            source_name="SolarPanelTalk",
            source_url="solarpaneltalk.com",
            post_url="https://www.solarpaneltalk.com/forum/solar-panels-for-home/123-quote-review",
            author="PhoenixHomeowner",
            post_title="Review my 9 kW SunPower quote in AZ",
            intent_snippet="Got quoted $26,000 for 9 kW system. Current power bill is $240/mo.",
            lead_score=14.0,
            lead_category=LeadCategory.HOT_BUYER.value,
            classification_type=ClassificationType.RESIDENTIAL_QUOTE_SEEKER.value,
            lead_freshness=LeadFreshness.DAYS_0_7.value,
            published_at="2026-11-15",
            latest_reply_at="2026-11-16",
            property_type="Single Family Residential",
            country="USA",
            state="AZ",
            city="Phoenix",
            zip_code="85001",
            system_size_kw=9.0,
            electricity_bill=240.0,
            buyer_stage=BuyerStage.COMPARING_QUOTES.value,
            matched_keywords=["solar quote", "price per watt", "electric bill"],
            evidence_snippets=["Quote pricing: $2.88/W (+4.0)", "Electric bill stated: $240/mo (+3.0)"]
        )
        lead_dict = lead.model_dump()
        self.assertEqual(lead_dict["state"], "AZ")
        self.assertEqual(lead_dict["lead_score"], 14.0)
        self.assertEqual(lead_dict["lead_category"], "HOT_BUYER")
        self.assertEqual(lead_dict["buyer_stage"], "COMPARING_QUOTES")
        self.assertEqual(lead_dict["property_type"], "Single Family Residential")


class TestGatewayTransport(unittest.TestCase):
    def test_gateway_health_check_mechanism(self):
        """Verifies health check query against gateway endpoint."""
        downloader = HTMLDownloader()
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            downloader.client = httpx.AsyncClient(timeout=1.0)
            health = loop.run_until_complete(downloader._check_gateway_health())
            self.assertIsInstance(health, bool)
        finally:
            loop.run_until_complete(downloader.client.aclose())
            loop.close()


class TestBusinessContactExtraction(unittest.TestCase):
    def setUp(self):
        from contact_extractor import BusinessContactExtractor
        self.extractor = BusinessContactExtractor()

    def test_official_business_email_extracted_from_footer(self):
        html = """
        <html><body>
            <div class="content">Welcome to SIREN Solar Q&A</div>
            <footer>
                <p>Have questions? Email our office: <a href="mailto:contact@sirensolar.org">contact@sirensolar.org</a></p>
                <p>Office Phone: (812) 332-1234</p>
            </footer>
        </body></html>
        """
        contacts = self.extractor.extract_business_contacts("https://www.sirensolar.org", html, "SIREN Solar Q&A")
        contact_values = [c["contact_value"] for c in contacts]
        self.assertIn("contact@sirensolar.org", contact_values)
        self.assertIn("(812) 332-1234", contact_values)

    def test_personal_emails_strictly_ignored(self):
        html = """
        <html><body>
            <div class="forum-comment">
                <p>Hey guys, my name is John, reach me at john.doe1234@gmail.com for solar questions.</p>
                <p>Or my yahoo: jsolar@yahoo.com</p>
            </div>
            <footer>
                <a href="mailto:info@solarpaneltalk.com">info@solarpaneltalk.com</a>
            </footer>
        </body></html>
        """
        contacts = self.extractor.extract_business_contacts("https://www.solarpaneltalk.com/forum", html, "SolarPanelTalk")
        contact_values = [c["contact_value"] for c in contacts]
        self.assertNotIn("john.doe1234@gmail.com", contact_values)
        self.assertNotIn("jsolar@yahoo.com", contact_values)
        self.assertIn("info@solarpaneltalk.com", contact_values)

    def test_official_contact_form_extracted(self):
        html = """
        <html><body>
            <h1>Contact Us</h1>
            <form action="/submit-inquiry" method="post">
                <input name="name"/>
            </form>
        </body></html>
        """
        contacts = self.extractor.extract_business_contacts("https://www.sirensolar.org/contact-us/", html, "SIREN Solar Q&A")
        form_contacts = [c for c in contacts if c["contact_type"] == "CONTACT_FORM"]
        self.assertTrue(len(form_contacts) > 0)
        self.assertEqual(form_contacts[0]["contact_value"], "https://www.sirensolar.org/submit-inquiry")


class TestSourceRegistry(unittest.TestCase):
    def test_source_registry_lookups(self):
        from source_registry import SourceRegistry
        allowed = SourceRegistry.get_allowed_domains()
        self.assertIn("solarpaneltalk.com", allowed)
        self.assertIn("sirensolar.org", allowed)
        self.assertIn("diysolarforum.com", allowed)
        self.assertIn("houzz.com", allowed)

        seeds = SourceRegistry.get_active_seeds()
        self.assertTrue(len(seeds) >= 10)

        # Check blocked source status
        sun_source = SourceRegistry.get_source_by_id("solarunitedneighbors")
        self.assertIsNotNone(sun_source)
        self.assertEqual(sun_source.status, "UNAVAILABLE_BLOCKED")


class TestThreeCustomerSegments(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(DATA_DIR / "test_storage.db")
        self.parser = ContentParser(self.storage)

    def test_segment_assignment(self):
        from models import LeadSegment
        self.assertEqual(self.parser.determine_lead_segment(CLASSIFICATION_RESIDENTIAL_NEW_BUYER), LeadSegment.NEW_BUYER.value)
        self.assertEqual(self.parser.determine_lead_segment(CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER), LeadSegment.NEW_BUYER.value)
        self.assertEqual(self.parser.determine_lead_segment(CLASSIFICATION_RESIDENTIAL_UPGRADE), LeadSegment.EXISTING_CUSTOMER.value)
        self.assertEqual(self.parser.determine_lead_segment(CLASSIFICATION_RESIDENTIAL_REPAIR), LeadSegment.SERVICE_CUSTOMER.value)

    def test_repair_service_lead_qualified(self):
        title = "Inverter error code red light flashing SolarEdge residential repair"
        text = "My SolarEdge inverter is showing an error 18-36 red light and not producing power. Anyone have a repair technician recommendation?"
        classification, _ = self.parser.classify_content(title, text)
        self.assertEqual(classification, CLASSIFICATION_RESIDENTIAL_REPAIR)
        score, keywords, evidence = self.parser.score_buyer_intent(classification, title, text)
        self.assertTrue(score >= 4.0)
        segment = self.parser.determine_lead_segment(classification)
        self.assertEqual(segment, "SERVICE_CUSTOMER")


class TestRedditRSSLiveParsing(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(DATA_DIR / "test_storage.db")
        self.parser = ContentParser(self.storage)

    def test_reddit_rss_entry_parsed_with_freshness(self):
        now_iso = datetime.now(timezone.utc).isoformat()
        sample_rss = f"""<?xml version="1.0" encoding="UTF-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
            <entry>
                <title>Solar Quote Review: 8.5 kW system in Florida for $2.85/W</title>
                <link href="https://www.reddit.com/r/solar/comments/test1234/solar_quote_review/"/>
                <author><name>/u/SunshineStateHomeowner</name></author>
                <content type="html">&lt;p&gt;Looking for installer feedback. Got a quote for 8.5 kW system at $2.85 per watt with Enphase microinverters. Electric bill is $220/mo.&lt;/p&gt;</content>
                <updated>{now_iso}</updated>
            </entry>
        </feed>"""
        res = self.parser.parse_reddit_rss("https://www.reddit.com/r/solar/new.rss", sample_rss)
        lead = res.get("solar_lead")
        self.assertIsNotNone(lead)
        self.assertEqual(lead["lead_segment"], "NEW_BUYER")
        self.assertEqual(lead["lead_freshness"], "0-7 days")
        self.assertEqual(lead["lead_category"], "HOT_BUYER")
        self.assertEqual(lead["author"], "SunshineStateHomeowner")


def tearDownModule():
    test_db = DATA_DIR / "test_storage.db"
    if test_db.exists():
        try:
            test_db.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
