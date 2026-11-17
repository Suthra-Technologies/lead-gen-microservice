"""
Solar Crawler Implementation Test Suite
Verifies all 14 acceptance criteria specified in the November 17, 2026 Implementation Report:
1. Previously crawled URL becomes eligible after its revisit interval.
2. Recently crawled URL is skipped until its next scheduled fetch.
3. A URL cannot be matched against itself during duplicate detection.
4. Identical content from different URLs is identified as duplicate.
5. Different discussion content is not discarded as a duplicate.
6. Category pages produce valid subcategory and thread URLs.
7. Pagination discovers additional discussion threads.
8. Revisiting a category detects newly added posts/threads.
9. Revisiting a thread extracts only newly added posts.
10. Reddit HTTP 429 follows retry scheduling and does not loop.
11. Failed HTTP requests can be retried according to policy.
12. Solar cleaning businesses are not classified as installation contractors.
13. Contractor records and homeowner leads have separate metrics.
14. Every qualified homeowner lead retains evidence and its original URL.
"""

import unittest
import asyncio
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup

from storage import CrawlerStorage
from frontier import URLFrontier
from deduplicator import Deduplicator
from parser import ContentParser
from sources.solarpaneltalk import SolarPanelTalkAdapter
from sources.google_places import GooglePlacesSource
from contractor_matcher import ContractorMatcher


class TestCrawlerImplementation(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(":memory:")
        self.frontier = URLFrontier(self.storage)
        self.parser = ContentParser(storage=self.storage)
        self.adapter = SolarPanelTalkAdapter()

    # --- Test 1: Previously crawled URL becomes eligible after revisit interval ---
    def test_01_crawled_url_eligible_after_revisit_interval(self):
        url = "https://www.solarpaneltalk.com/forum/solar-panels-for-home"
        _, _, url_hash = Deduplicator.canonicalize_url(url)
        self.storage.save_new_url(url_hash=url_hash, url=url, domain="solarpaneltalk.com", depth=0, page_type="CATEGORY")
        
        # Simulate crawl completed 10 hours ago with 4h revisit interval -> next_fetch was 6h ago
        now = datetime.now(timezone.utc)
        past_next_fetch = (now - timedelta(hours=6)).isoformat()
        with self.storage._get_connection() as conn:
            conn.cursor().execute("""
                UPDATE urls 
                SET status = 'CRAWLED', http_code = 200, next_fetch_at = ?, last_fetched_at = ?
                WHERE url_hash = ?;
            """, (past_next_fetch, (now - timedelta(hours=10)).isoformat(), url_hash))
            conn.commit()

        eligible = self.storage.get_eligible_urls(limit=10)
        self.assertTrue(any(item["url"] == url for item in eligible), "Past next_fetch URL must be eligible")

    # --- Test 2: Recently crawled URL is skipped until next scheduled fetch ---
    def test_02_recently_crawled_url_skipped(self):
        url = "https://www.solarpaneltalk.com/forum/solar-panels-for-home"
        _, _, url_hash = Deduplicator.canonicalize_url(url)
        self.storage.save_new_url(url_hash=url_hash, url=url, domain="solarpaneltalk.com", depth=0, page_type="CATEGORY")
        
        # Simulate crawl 10 minutes ago with next_fetch 4 hours in the future
        now = datetime.now(timezone.utc)
        future_next_fetch = (now + timedelta(hours=4)).isoformat()
        with self.storage._get_connection() as conn:
            conn.cursor().execute("""
                UPDATE urls 
                SET status = 'CRAWLED', http_code = 200, next_fetch_at = ?
                WHERE url_hash = ?;
            """, (future_next_fetch, url_hash))
            conn.commit()

        eligible = self.storage.get_eligible_urls(limit=10)
        self.assertFalse(any(item["url"] == url for item in eligible), "Future next_fetch URL must NOT be eligible")

        # Frontier add_url should skip it
        loop = asyncio.new_event_loop()
        try:
            added = loop.run_until_complete(self.frontier.add_url(url, depth=0))
            self.assertFalse(added, "Recently crawled URL should be skipped by frontier")
        finally:
            loop.close()

    # --- Test 3: A URL cannot be matched against itself during duplicate detection ---
    def test_03_self_match_prevention(self):
        url = "https://www.solarpaneltalk.com/forum/solar-panels-for-home/123-my-test-thread"
        content = "We are comparing solar quotes from two installers in Phoenix Arizona."
        content_hash = Deduplicator.hash_content_exact(content)
        simhash = Deduplicator.compute_simhash(content)

        # Store content hash for URL
        self.storage.save_content_hash_atomic(content_hash, simhash, url, "Test Thread")

        # Check near duplicate excluding self
        match = self.storage.check_near_duplicate_simhash(simhash, max_hamming_distance=3, exclude_url=url)
        self.assertIsNone(match, "URL must NOT match against itself as near-duplicate")

    # --- Test 4: Identical content from different URLs is identified as duplicate ---
    def test_04_identical_content_from_different_urls_detected(self):
        url1 = "https://www.solarpaneltalk.com/forum/thread-1"
        url2 = "https://www.solarpaneltalk.com/forum/thread-2-duplicate"
        content = "We have a 10 kW system quote in Austin Texas for $2.80/W from SunPower."
        content_hash = Deduplicator.hash_content_exact(content)
        simhash = Deduplicator.compute_simhash(content)

        self.storage.save_content_hash_atomic(content_hash, simhash, url1, "Original Thread")

        # Second URL with identical hash
        matched_url = self.storage.get_exact_content_match(content_hash, exclude_url=url2)
        self.assertEqual(matched_url, url1, "Exact duplicate content from another URL must match url1")

        # Near duplicate check excluding url2 should also match url1
        near_match = self.storage.check_near_duplicate_simhash(simhash, max_hamming_distance=3, exclude_url=url2)
        self.assertIsNotNone(near_match)
        self.assertEqual(near_match[0], url1)

    # --- Test 5: Different discussion content is not discarded as duplicate ---
    def test_05_different_discussion_content_preserved(self):
        url1 = "https://www.solarpaneltalk.com/forum/thread-1"
        url2 = "https://www.solarpaneltalk.com/forum/thread-2"
        content1 = "Comparing two solar quotes in San Diego California for an 8 kW system."
        content2 = "Need an authorized repair technician for SolarEdge inverter in Orlando Florida."

        simhash1 = Deduplicator.compute_simhash(content1)
        simhash2 = Deduplicator.compute_simhash(content2)

        self.storage.save_content_hash_atomic(Deduplicator.hash_content_exact(content1), simhash1, url1, "Quote Thread")
        match = self.storage.check_near_duplicate_simhash(simhash2, max_hamming_distance=3, exclude_url=url2)
        self.assertIsNone(match, "Different discussion content must not be discarded as duplicate")

    # --- Test 6: Category pages produce valid subcategory and thread URLs ---
    def test_06_category_produces_valid_subcategories_and_threads(self):
        current_url = "https://www.solarpaneltalk.com/forum/solar-panels-for-home"
        html = """
        <html><body>
            <a href="/forum/solar-panels-for-home/solar-panel-installation">Installation Subforum</a>
            <a href="/forum/solar-panels-for-home/solar-panel-system-equipment">Equipment Subforum</a>
            <a href="/forum/solar-panels-for-home/443616-looking-for-advice-pv-installation">Looking for Advice</a>
            <a href="/forum/solar-panels-for-home/443617-solar-quote-review">Solar Quote Review</a>
            <a href="/members/12345-johndoe">Member Profile (Should be ignored)</a>
            <a href="/forum/commercial-solar/443618-commercial-project">Commercial (Should be ignored)</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        categories = self.adapter.discover_categories(soup, current_url)
        threads = self.adapter.discover_threads(soup, current_url)

        self.assertEqual(len(categories), 2)
        self.assertEqual(len(threads), 2)
        self.assertTrue(all("/members/" not in t for t in threads))
        self.assertTrue(all("/commercial" not in t for t in threads))

    # --- Test 7: Pagination discovers additional discussion threads ---
    def test_07_pagination_discovers_additional_threads(self):
        current_url = "https://www.solarpaneltalk.com/forum/solar-quotes-pricing"
        html = """
        <html><body>
            <a href="/forum/solar-quotes-pricing/page2">Page 2</a>
            <a href="/forum/solar-quotes-pricing/page3">Page 3</a>
            <a href="/forum/solar-quotes-pricing/page4">Page 4 (Exceeds freshness limit)</a>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        pagination_links = self.adapter.discover_pagination(soup, current_url, max_page=3)
        self.assertEqual(len(pagination_links), 2)
        self.assertIn("https://www.solarpaneltalk.com/forum/solar-quotes-pricing/page2", pagination_links)
        self.assertIn("https://www.solarpaneltalk.com/forum/solar-quotes-pricing/page3", pagination_links)
        self.assertNotIn("https://www.solarpaneltalk.com/forum/solar-quotes-pricing/page4", pagination_links)

    # --- Test 8: Revisiting a category detects newly added threads ---
    def test_08_revisiting_category_detects_new_threads(self):
        current_url = "https://www.solarpaneltalk.com/forum/solar-quotes-pricing"
        loop = asyncio.new_event_loop()
        try:
            # First visit: thread 1 exists
            html_v1 = '<html><body><a href="/forum/solar-quotes-pricing/441001-thread-one">Thread 1</a></body></html>'
            soup_v1 = BeautifulSoup(html_v1, "lxml")
            threads_v1 = self.adapter.discover_threads(soup_v1, current_url)
            for t in threads_v1:
                loop.run_until_complete(self.frontier.add_url(t, depth=1))

            # Second visit: thread 1 still there, thread 2 newly posted!
            html_v2 = """
            <html><body>
                <a href="/forum/solar-quotes-pricing/441001-thread-one">Thread 1</a>
                <a href="/forum/solar-quotes-pricing/441002-thread-two">Thread 2 New</a>
            </body></html>
            """
            soup_v2 = BeautifulSoup(html_v2, "lxml")
            threads_v2 = self.adapter.discover_threads(soup_v2, current_url)
            new_enqueued = 0
            for t in threads_v2:
                if loop.run_until_complete(self.frontier.add_url(t, depth=1)):
                    new_enqueued += 1

            self.assertEqual(new_enqueued, 1, "Only thread 2 should be newly enqueued on category refresh")
        finally:
            loop.close()

    # --- Test 9: Revisiting a thread extracts only newly added posts ---
    def test_09_revisiting_thread_extracts_new_posts(self):
        thread_url = "https://www.solarpaneltalk.com/forum/solar-panels-for-home/443616-pv-advice"
        # Initial post
        post1 = {
            "post_id": "spt_443616_op",
            "thread_url": thread_url,
            "source_name": "SolarPanelTalk",
            "thread_title": "Looking for solar advice in Dallas",
            "author_name": "DallasHomeowner",
            "post_text": "Our electric bill is $280/mo in Dallas Texas. Looking for solar quotes.",
            "post_order": 0,
            "is_original_post": True,
            "posted_at": "2026-11-15"
        }
        res1 = self.storage.save_discussion_post(post1)
        self.assertTrue(res1, "First post should be saved")

        # Second revisit: post1 is already in DB, reply post2 is new
        res1_again = self.storage.save_discussion_post(post1)
        self.assertFalse(res1_again, "Existing post must not be duplicated")

        post2 = {
            "post_id": "spt_443616_rep_1",
            "thread_url": thread_url,
            "source_name": "SolarPanelTalk",
            "thread_title": "Looking for solar advice in Dallas",
            "author_name": "InstallerDave",
            "post_text": "In Dallas Oncor territory you should expect around $2.60 to $2.85 per watt.",
            "post_order": 1,
            "is_original_post": False,
            "posted_at": "2026-11-16"
        }
        res2 = self.storage.save_discussion_post(post2)
        self.assertTrue(res2, "New reply post should be successfully saved")

        thread_posts = self.storage.get_discussion_posts(thread_url=thread_url)
        self.assertEqual(len(thread_posts), 2)

    # --- Test 10: Reddit HTTP 429 follows retry scheduling and does not loop ---
    def test_10_reddit_http_429_retry_schedule(self):
        url = "https://www.reddit.com/r/solar/new.rss"
        _, _, url_hash = Deduplicator.canonicalize_url(url)
        self.storage.save_new_url(url_hash=url_hash, url=url, domain="reddit.com", depth=0, page_type="FEED")

        # Simulate HTTP 429 with Retry-After: 600s
        self.storage.update_url_fetch_result(
            url_hash=url_hash,
            status="SKIPPED",
            http_code=429,
            retry_after_seconds=600
        )

        rec = self.storage.get_url_record(url_hash)
        self.assertEqual(rec["status"], "SKIPPED")
        self.assertEqual(rec["last_status_code"], 429)
        self.assertIsNotNone(rec["next_fetch_at"])

        # Should NOT be eligible immediately
        eligible = self.storage.get_eligible_urls(limit=10)
        self.assertFalse(any(item["url"] == url for item in eligible), "429 backoff must prevent immediate retry")

    # --- Test 11: Failed HTTP requests can be retried according to policy ---
    def test_11_failed_http_request_retry_policy(self):
        url = "https://diysolarforum.com/forums/grid-tie-solar-grid-assist.37"
        _, _, url_hash = Deduplicator.canonicalize_url(url)
        self.storage.save_new_url(url_hash=url_hash, url=url, domain="diysolarforum.com", depth=0, page_type="CATEGORY")

        # Failed with 500 server error, scheduled for retry in past (e.g. 1 hour ago)
        now = datetime.now(timezone.utc)
        past_retry = (now - timedelta(hours=1)).isoformat()
        with self.storage._get_connection() as conn:
            conn.cursor().execute("""
                UPDATE urls 
                SET status = 'FAILED', http_code = 500, next_fetch_at = ?
                WHERE url_hash = ?;
            """, (past_retry, url_hash))
            conn.commit()

        eligible = self.storage.get_eligible_urls(limit=10)
        self.assertTrue(any(item["url"] == url for item in eligible), "Failed URL past retry window must be eligible")

    # --- Test 12: Solar cleaning businesses are not classified as installation contractors ---
    def test_12_cleaning_businesses_not_classified_as_installers(self):
        # 1. Classification check
        cleaner = GooglePlacesSource.classify_business(
            name="Apex Solar Panel Cleaning & Washing LLC",
            primary_type="solar_cleaning_service",
            query="solar in Phoenix AZ"
        )
        self.assertEqual(cleaner["business_category"], "Cleaning Service")

        screen = GooglePlacesSource.classify_business(
            name="Valley Sun Screens and Solar Shading",
            primary_type="window_tinting_service",
            query="solar in Phoenix AZ"
        )
        self.assertEqual(screen["business_category"], "Non-Solar Screens/Lighting")

        installer = GooglePlacesSource.classify_business(
            name="Freedom Solar Power Installation",
            primary_type="solar_energy_contractor",
            query="solar in Austin TX"
        )
        self.assertEqual(installer["business_category"], "Installer")

        # 2. Contractor Matcher Gate check
        lead = {"state": "AZ", "city": "phoenix", "post_title": "Looking for residential solar installer"}
        cleaning_contractor = {
            "company_name": "Apex Solar Panel Cleaning",
            "business_category": "Cleaning Service",
            "state": "AZ",
            "city": "phoenix",
            "business_status": "OPERATIONAL"
        }
        score, reasons = ContractorMatcher.calculate_match_score(lead, cleaning_contractor)
        self.assertEqual(score, 0, "Cleaning business must be rejected with 0 score")
        self.assertTrue(any("Cleaning Service" in r for r in reasons))

    # --- Test 13: Contractor records and homeowner leads have separate metrics ---
    def test_13_contractor_records_and_homeowner_leads_have_separate_metrics(self):
        # Insert 1 homeowner lead
        self.storage.save_solar_lead({
            "source_name": "SolarPanelTalk",
            "source_url": "solarpaneltalk.com",
            "post_url": "https://www.solarpaneltalk.com/forum/lead_test_1",
            "lead_score": 8.0,
            "lead_category": "HOT_BUYER",
            "lead_segment": "NEW_BUYER"
        })

        # Insert 2 contractor records
        self.storage.save_solar_installer({
            "place_id": "ChIJ_TEST_001",
            "company_name": "Solar Pros Texas",
            "state": "TX",
            "phone_number": "512-555-0100",
            "business_category": "Installer"
        })
        self.storage.save_solar_installer({
            "place_id": "ChIJ_TEST_002",
            "company_name": "Clean Panels Inc",
            "state": "TX",
            "phone_number": "512-555-0200",
            "business_category": "Cleaning Service"
        })

        metrics = self.storage.get_platform_metrics()
        # Verify strict separation
        self.assertEqual(metrics["homeowner_leads"]["total_qualified_leads"], 1)
        self.assertEqual(metrics["contractor_discovery_places"]["places_discovered"], 2)
        self.assertNotIn("places_discovered", metrics["homeowner_leads"])

    # --- Test 14: Every qualified homeowner lead retains evidence and its original URL ---
    def test_14_qualified_lead_retains_evidence_and_original_url(self):
        test_url = "https://www.solarpaneltalk.com/forum/solar-quotes/443999-quote-review-san-diego"
        title = "Comparing quotes for 8.5 kW system in San Diego CA"
        body = "Our electric bill is $320/month for our house in San Diego California. Comparing quotes from two local installers."

        lead_dict = self.parser.build_and_save_lead(
            url=test_url,
            title=title,
            visible_text=body,
            author="SanDiegoSun",
            classification="RESIDENTIAL_QUOTE_SEEKER",
            score=7.0,
            matched_keywords=["comparing quotes", "electric bill stated ($320/mo)"],
            evidence_snippets=["Intent (+4.0): comparing quotes from two local installers", "Pain point: $320/month electric bill"],
            published_str="2026-11-16",
            latest_reply_str="2026-11-17"
        )
        self.assertIsNotNone(lead_dict)
        self.assertEqual(lead_dict["post_url"], test_url)

        # Retrieve from storage
        leads = self.storage.get_recent_leads(limit=5)
        saved = next((l for l in leads if l["post_url"] == test_url), None)
        self.assertIsNotNone(saved)
        self.assertEqual(saved["post_url"], test_url)
        self.assertIsNotNone(saved["evidence_snippets"])

        evidence_records = self.storage.get_lead_evidence(saved["id"])
        self.assertGreaterEqual(len(evidence_records), 2)
        self.assertEqual(evidence_records[0]["extraction_method"], "RULE_REGEX_V2")


if __name__ == "__main__":
    unittest.main()
