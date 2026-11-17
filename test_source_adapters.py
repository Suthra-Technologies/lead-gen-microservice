"""
Source Adapters Unit & Regression Test Suite
Validates platform-specific URL filtering, category whitelisting, commercial filtering,
geo scoping, pagination bounds, date separation, and frontier integration.
"""

import unittest
from bs4 import BeautifulSoup

from sources.solarpaneltalk import SolarPanelTalkAdapter
from sources.diysolarforum import DIYSolarForumAdapter
from sources.reddit import RedditAdapter
from sources.houzz import HouzzAdapter
from sources import get_adapter_for_url, get_adapter_for_domain
from storage import CrawlerStorage
from frontier import URLFrontier


class TestSourceAdapters(unittest.TestCase):
    def setUp(self):
        self.spt = SolarPanelTalkAdapter()
        self.diy = DIYSolarForumAdapter()
        self.reddit = RedditAdapter()
        self.houzz = HouzzAdapter()

    # -------------------------------------------------------------------------
    # 1. SolarPanelTalk Adapter Tests
    # -------------------------------------------------------------------------
    def test_solarpaneltalk_valid_urls(self):
        allowed_urls = [
            "https://www.solarpaneltalk.com/forum/solar-quotes-pricing?sort=lastpost&order=desc",
            "https://www.solarpaneltalk.com/forum/solar-panels-for-home",
            "https://www.solarpaneltalk.com/forum/solar-panels-for-home/solar-panel-installation",
            "https://www.solarpaneltalk.com/forum/solar/how-much-do-solar-panels-cost/443616-looking-for-advice-pv-installation-in-mesa-az",
            "https://www.solarpaneltalk.com/forum/solar-quotes-pricing/442299-looking-for-installer-in-socal/page2",
        ]
        for url in allowed_urls:
            valid, reason = self.spt.is_valid_url(url)
            self.assertTrue(valid, f"Expected {url} to be valid, got: {reason}")

    def test_solarpaneltalk_disqualified_urls(self):
        disqualified_urls = [
            # Commercial lighting category bleed
            "https://www.solarpaneltalk.com/forum/solar-power-lighting/commercial-solar-lighting",
            # Industrial solar
            "https://www.solarpaneltalk.com/forum/industrial-solar-installations",
            # Member profiles
            "https://www.solarpaneltalk.com/forum/member/12345-johndoe",
            "https://www.solarpaneltalk.com/forum/members/list",
            # Search / Attachments
            "https://www.solarpaneltalk.com/forum/searchJSON",
            "https://www.solarpaneltalk.com/filedata/fetch?id=123",
            # Pagination exceeding limit 3
            "https://www.solarpaneltalk.com/forum/solar-quotes-pricing/page4",
            "https://www.solarpaneltalk.com/forum/solar-quotes-pricing?sort=lastpost&page=5",
        ]
        for url in disqualified_urls:
            valid, reason = self.spt.is_valid_url(url)
            self.assertFalse(valid, f"Expected {url} to be rejected, but it passed: {reason}")

    def test_solarpaneltalk_classification(self):
        cat_url = "https://www.solarpaneltalk.com/forum/solar-quotes-pricing"
        thread_url = "https://www.solarpaneltalk.com/forum/solar-quotes-pricing/442299-socal-quote"
        paged_thread = "https://www.solarpaneltalk.com/forum/solar-quotes-pricing/442299-socal-quote/page2"
        paged_cat = "https://www.solarpaneltalk.com/forum/solar-quotes-pricing/page2"
        bad_url = "https://www.solarpaneltalk.com/forum/solar-power-lighting/commercial-solar-lighting"

        self.assertEqual(self.spt.classify_url_type(cat_url), "CATEGORY")
        self.assertEqual(self.spt.classify_url_type(thread_url), "THREAD")
        self.assertEqual(self.spt.classify_url_type(paged_thread), "THREAD_PAGINATION")
        self.assertEqual(self.spt.classify_url_type(paged_cat), "CATEGORY_PAGINATION")
        self.assertEqual(self.spt.classify_url_type(bad_url), "IGNORED")

    def test_solarpaneltalk_date_separation(self):
        html = """
        <html>
        <head><title>Looking for quote advice - SolarPanelTalk</title></head>
        <body>
            <div class="b-post" id="post-1">
                <div class="b-post__author">SolarSeeker2026</div>
                <time class="b-post__timestamp" datetime="2026-01-15T10:00:00Z">Jan 15, 2026</time>
                <div class="b-userinfo__location">Austin, Texas</div>
                <div class="b-post__content">I received three quotes for a 9.6 kW system...</div>
            </div>
            <div class="b-post" id="post-2">
                <div class="b-post__author">SolarProExpert</div>
                <time class="b-post__timestamp" datetime="2026-11-10T14:30:00Z">Nov 10, 2026</time>
                <div class="b-post__content">Those prices are fair for Austin market.</div>
            </div>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        data = self.spt.extract_thread_data(soup, "https://www.solarpaneltalk.com/forum/solar-quotes-pricing/123-quote")

        self.assertEqual(data["author"], "SolarSeeker2026")
        self.assertEqual(data["published_at"], "2026-01-15")
        self.assertEqual(data["latest_reply_at"], "2026-11-10")
        self.assertIn("Austin, Texas", data["location_raw"])
        self.assertIn("9.6 kW system", data["main_content"])
        self.assertEqual(len(data["replies"]), 1)

    # -------------------------------------------------------------------------
    # 2. DIY Solar Forum Adapter Tests
    # -------------------------------------------------------------------------
    def test_diysolarforum_valid_urls(self):
        allowed_urls = [
            "https://diysolarforum.com/whats-new/posts/",
            "https://diysolarforum.com/forums/new-to-solar-start-here.3/",
            "https://diysolarforum.com/forums/grid-tie-solar-grid-assist.37/",
            "https://diysolarforum.com/threads/dual-quattro-16kw-off-grid-build-in-wnc.81338/",
            "https://diysolarforum.com/threads/dual-quattro-16kw-off-grid-build-in-wnc.81338/page-2",
        ]
        for url in allowed_urls:
            valid, reason = self.diy.is_valid_url(url)
            self.assertTrue(valid, f"Expected {url} to be valid, got: {reason}")

    def test_diysolarforum_disqualified_urls(self):
        disqualified_urls = [
            # High pagination (e.g. page-140 from past logs)
            "https://diysolarforum.com/threads/srne-20kw-parallel-asp-journey.85905/page-140",
            "https://diysolarforum.com/threads/some-thread.123/page-4",
            # Vehicle mounted / RVs
            "https://diysolarforum.com/forums/vehicle-mounted-systems.14/",
            # Member / Admin paths
            "https://diysolarforum.com/members/will-prowse.1/",
            "https://diysolarforum.com/account/preferences",
            "https://diysolarforum.com/attachments/wiring-diagram-jpg.1234/",
        ]
        for url in disqualified_urls:
            valid, reason = self.diy.is_valid_url(url)
            self.assertFalse(valid, f"Expected {url} to be rejected, but it passed: {reason}")

    def test_diysolarforum_thread_data(self):
        html = """
        <html>
        <head><title>Grid tie battery setup | DIY Solar Power Forum</title></head>
        <body>
            <h1 class="p-title-value">Grid tie battery setup</h1>
            <article class="message--post">
                <a class="username">GridHomeowner</a>
                <time class="u-dt" datetime="2026-03-20T12:00:00Z">March 20, 2026</time>
                <dl class="pairs--userLocation"><dd>Orlando, FL</dd></dl>
                <div class="bbWrapper">Planning a home battery addition with 2x Powerwall 3.</div>
            </article>
            <article class="message--post">
                <a class="username">BatteryHelper</a>
                <time class="u-dt" datetime="2026-11-12T09:00:00Z">Nov 12, 2026</time>
                <div class="bbWrapper">Make sure you have permission to operate from Duke Energy.</div>
            </article>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        data = self.diy.extract_thread_data(soup, "https://diysolarforum.com/threads/grid-tie-battery-setup.12345/")

        self.assertEqual(data["title"], "Grid tie battery setup")
        self.assertEqual(data["author"], "GridHomeowner")
        self.assertEqual(data["published_at"], "2026-03-20")
        self.assertEqual(data["latest_reply_at"], "2026-11-12")
        self.assertEqual(data["location_raw"], "Orlando, FL")

    # -------------------------------------------------------------------------
    # 3. Houzz Adapter Tests (Hard-blocking contractor directories & Pune, India)
    # -------------------------------------------------------------------------
    def test_houzz_valid_urls(self):
        allowed_urls = [
            "https://www.houzz.com/discussions/solar-energy",
            "https://www.houzz.com/discussions/solar-energy/sort/newest",
            "https://www.houzz.com/discussions/solar-energy?p=2",
            "https://www.houzz.com/discussions/6012345/solar-panel-quote-for-san-diego-home",
        ]
        for url in allowed_urls:
            valid, reason = self.houzz.is_valid_url(url)
            self.assertTrue(valid, f"Expected {url} to be valid, got: {reason}")

    def test_houzz_disqualified_urls(self):
        disqualified_urls = [
            # Exact Pune contractor directories from previous run logs
            "https://www.houzz.com/professionals/landscape-contractors/pune-16-in-probr0-bo~t_11812~r_1259229",
            "https://www.houzz.com/professionals/adu-contractors/pune-16-in-probr0-bo~t_34256~r_1259229",
            # Houzz Pro marketing & CRM feature pages
            "https://pro.houzz.com/for-pros/feature-project-management",
            "https://pro.houzz.com/for-pros/feature-premium-profile",
            "https://www.houzz.com/for-pros/feature-estimates",
            "https://pro.houzz.com/for-pros/feature-sales-crm",
            # Generic non-solar discussions
            "https://www.houzz.com/discussions/kitchen-remodel-tile-selection",
            # Pagination exceeding limit 3
            "https://www.houzz.com/discussions/solar-energy?p=4",
        ]
        for url in disqualified_urls:
            valid, reason = self.houzz.is_valid_url(url)
            self.assertFalse(valid, f"Expected {url} to be rejected, but it passed: {reason}")

    # -------------------------------------------------------------------------
    # 4. Reddit Adapter Tests
    # -------------------------------------------------------------------------
    def test_reddit_valid_urls(self):
        allowed_urls = [
            "https://www.reddit.com/r/solar/new.rss",
            "https://www.reddit.com/r/TeslaSolar/new.rss",
            "https://www.reddit.com/r/TexasSolar/new.rss",
            "https://www.reddit.com/r/Powerwall/new.rss",
            "https://www.reddit.com/r/solar/comments/1abc23/installer_quote_comparison_in_houston/",
        ]
        for url in allowed_urls:
            valid, reason = self.reddit.is_valid_url(url)
            self.assertTrue(valid, f"Expected {url} to be valid, got: {reason}")

    def test_reddit_disqualified_urls(self):
        disqualified_urls = [
            # Duplicate hash search pages from log
            "https://www.reddit.com/r/solar/search?q=looking+for+installer",
            "https://www.reddit.com/r/solar/search?q=quote+comparison",
            # User profile pages
            "https://www.reddit.com/user/solar_installer_tx",
            # Non-solar subreddits
            "https://www.reddit.com/r/gaming/comments/123/fun_game/",
            "https://www.reddit.com/r/cryptocurrency/new.rss",
        ]
        for url in disqualified_urls:
            valid, reason = self.reddit.is_valid_url(url)
            self.assertFalse(valid, f"Expected {url} to be rejected, but it passed: {reason}")

    # -------------------------------------------------------------------------
    # 5. Frontier Pre-enqueue Integration Tests
    # -------------------------------------------------------------------------
    def test_frontier_blocks_disqualified_urls(self):
        storage = CrawlerStorage(":memory:")
        frontier = URLFrontier(storage)

        # 1. Houzz Pune contractor directory should be blocked by frontier
        houzz_pune = "https://www.houzz.com/professionals/landscape-contractors/pune-16-in-probr0-bo~t_11812~r_1259229"
        allowed, reason = frontier.should_crawl(houzz_pune, depth=1)
        self.assertFalse(allowed)
        self.assertIn("Houzz", reason)

        # 2. SolarPanelTalk commercial lighting category should be blocked
        spt_commercial = "https://www.solarpaneltalk.com/forum/solar-power-lighting/commercial-solar-lighting"
        allowed, reason = frontier.should_crawl(spt_commercial, depth=1)
        self.assertFalse(allowed)

        # 3. DIY Solar Forum page-140 should be blocked
        diy_p140 = "https://diysolarforum.com/threads/srne-20kw-parallel-asp-journey.85905/page-140"
        allowed, reason = frontier.should_crawl(diy_p140, depth=1)
        self.assertFalse(allowed)

        # 4. Valid residential thread should be permitted
        valid_thread = "https://www.solarpaneltalk.com/forum/solar-quotes-pricing/442299-looking-for-installer-in-socal"
        allowed, reason = frontier.should_crawl(valid_thread, depth=1)
        self.assertTrue(allowed, f"Expected valid thread to pass, got: {reason}")


if __name__ == "__main__":
    unittest.main()
