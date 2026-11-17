"""
Main Crawler Orchestrator
Coordinates the URL Frontier, Async HTML Downloader, and Parser Workers.
Implements the end-to-end architecture from Figure 9-4 with complete observability,
debug logging, crash recovery, CLI controls, Deno Gateway routing, and lead exports.
"""

import sys
import time
import json
import argparse
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from config import (
    DEFAULT_SEED_URLS,
    DATA_DIR,
    LOGS_DIR,
    LEADS_DIR,
    CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
    CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER,
    CLASSIFICATION_RESIDENTIAL_EXISTING,
    CLASSIFICATION_RESIDENTIAL_UPGRADE,
    CLASSIFICATION_RESIDENTIAL_REPAIR,
    CLASSIFICATION_COMMERCIAL,
    CLASSIFICATION_DIY_OFFGRID,
    CLASSIFICATION_TECHNICAL,
    CLASSIFICATION_GENERAL_INFO,
    CLASSIFICATION_SPAM_VENDOR
)
from storage import CrawlerStorage
from frontier import URLFrontier
from downloader import HTMLDownloader
from parser import ContentParser
from sources.google_places import GooglePlacesSource
from contractor_matcher import ContractorMatcher


def setup_logging(debug_mode: bool = False) -> Path:
    """
    Configures console logger and writes timestamped logs to logs/ directory.
    Also appends to data/crawler.log for cumulative history.
    Returns the path to the timestamped log file.
    """
    log_level = logging.DEBUG if debug_mode else logging.INFO
    log_format = "%(asctime)s [%(levelname)-7s] [%(name)s] %(message)s"
    date_format = "%H:%M:%S"

    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    root_logger.handlers.clear()

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(logging.Formatter(log_format, datefmt=date_format))
    root_logger.addHandler(console_handler)

    # Timestamped File Handler in logs/
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file_path = LOGS_DIR / f"crawler_{timestamp}.log"
    file_handler = logging.FileHandler(log_file_path, mode="a", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # Always log full DEBUG to file
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-7s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root_logger.addHandler(file_handler)

    # Cumulative File Handler in data/crawler.log
    cumulative_handler = logging.FileHandler(DATA_DIR / "crawler.log", mode="a", encoding="utf-8")
    cumulative_handler.setLevel(logging.DEBUG)
    cumulative_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-7s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    root_logger.addHandler(cumulative_handler)

    # Silence noisy third-party loggers
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("trafilatura").setLevel(logging.WARNING)

    return log_file_path


logger = logging.getLogger("Crawler.Main")


class CrawlerCoordinator:
    def __init__(
        self,
        seeds: List[str] = DEFAULT_SEED_URLS,
        max_pages: int = 25,
        fresh_start: bool = False,
        log_file: Optional[Path] = None,
        places_enabled: bool = False,
        places_queries: Optional[List[str]] = None
    ):
        self.seeds = seeds
        self.max_pages = max_pages
        self.fresh_start = fresh_start
        self.log_file = log_file
        self.places_enabled = places_enabled
        self.places_queries = places_queries
        self.storage = CrawlerStorage()
        self.frontier = URLFrontier(self.storage)
        self.downloader = HTMLDownloader()
        self.parser = ContentParser(self.storage)
        self.places_source = GooglePlacesSource(storage=self.storage)
        self.installers_export_file: Optional[Path] = None

        # Asynchronous Queue decoupling Downloader from Parser
        self.raw_page_queue: asyncio.Queue = asyncio.Queue(maxsize=100)

        # Run-level metrics and atomic slot reservation
        self.crawled_count = 0
        self.claimed_slots = 0
        self.duplicates_count = 0
        self.exact_duplicates_discarded = 0
        self.simhash_duplicates_discarded = 0
        self.leads_discovered = 0
        self.failed_count = 0

        # Detailed Quality & Buyer Intent Metrics
        self.residential_new_buyer_count = 0
        self.residential_quote_seeker_count = 0
        self.upgrade_count = 0
        self.repair_count = 0
        self.existing_owner_count = 0
        self.commercial_rejected = 0
        self.technical_rejected = 0
        self.diy_rejected = 0
        self.spam_rejected = 0
        self.general_info_rejected = 0

        self.is_running = True
        self._slot_lock = asyncio.Lock()
        self.export_file: Optional[Path] = None
        self.contacts_export_file: Optional[Path] = None
        self.matched_leads_count = 0

    async def initialize_frontier(self):
        """Prepares the URL Frontier: either resets DB for fresh start, or restores pending URLs."""
        if self.fresh_start:
            logger.warning("[Main Mode] --fresh specified. Wiping database state for a clean crawl.")
            self.storage.reset_db()
        else:
            # Restore previously discovered PENDING URLs from SQLite
            restored = await self.frontier.restore_pending_urls(limit=5000)
            if restored > 0:
                logger.info(f"[Main State] Restored {restored} unfinished PENDING URLs from past crawl.")

        # Ingest Seed URLs
        logger.info(f"[Main Seeding] Ingesting {len(self.seeds)} initial seed URLs...")
        for url in self.seeds:
            await self.frontier.add_url(url, depth=0)

        stats = self.frontier.get_queue_stats()
        logger.info(
            f"[Frontier Status] In-Memory Active Domains: {stats['active_domains']} | "
            f"Total URLs in memory: {stats['total_in_memory_urls']}"
        )

    async def downloader_worker(self, worker_id: int):
        """Pulls URLs from the Frontier, downloads HTML, and enqueues to the Parser."""
        logger.debug(f"[Downloader-{worker_id}] Worker loop started.")
        while self.is_running:
            # Atomic slot reservation to strictly enforce max_pages limit
            async with self._slot_lock:
                if self.claimed_slots >= self.max_pages:
                    break
                self.claimed_slots += 1

            next_target = await self.frontier.get_next_url()
            if not next_target:
                # Release claimed slot if no URL is ready
                async with self._slot_lock:
                    self.claimed_slots -= 1
                await asyncio.sleep(0.5)
                continue

            url, domain, depth, url_hash = next_target
            self.storage.update_url_status(url_hash, "IN_PROGRESS")

            # Retrieve cached ETag and Last-Modified headers for conditional GET
            url_rec = self.storage.get_url_record(url_hash)
            etag = url_rec.get("etag") if url_rec else None
            last_modified = url_rec.get("last_modified") if url_rec else None
            page_type = url_rec.get("page_type") if url_rec else "CATEGORY"
            revisit_hrs = self.frontier.get_revisit_interval_hours(page_type)

            logger.info(f"[Downloader-{worker_id}] Processing target: {url} (Depth: {depth})")
            result = await self.downloader.fetch(url, etag=etag, last_modified=last_modified)

            if not result or result.get("skipped"):
                async with self._slot_lock:
                    self.claimed_slots -= 1
                status_code = result.get("status") if result else 0
                self.storage.update_url_fetch_result(
                    url_hash=url_hash,
                    status="SKIPPED",
                    http_code=status_code,
                    revisit_interval_hours=2.0
                )
                logger.debug(f"[Downloader-{worker_id}] Skipped payload for {url} (HTTP {status_code})")
                continue

            status_code = result.get("status", 0)
            html = result.get("html")
            is_praw = result.get("is_reddit_praw", False)
            not_modified = result.get("not_modified", False)

            if not_modified or status_code == 304:
                # HTTP 304 Not Modified: Cache is fresh, schedule next refresh without re-parsing
                async with self._slot_lock:
                    self.claimed_slots -= 1
                self.storage.update_url_fetch_result(
                    url_hash=url_hash,
                    status="CRAWLED",
                    http_code=304,
                    etag=result.get("etag") or etag,
                    last_modified=result.get("last_modified") or last_modified,
                    revisit_interval_hours=revisit_hrs
                )
                logger.info(f"[Downloader-{worker_id}] 304 Not Modified: {url} (Scheduled next refresh in {revisit_hrs}h)")
                continue

            if status_code == 429:
                # HTTP 429 Rate Limited: Enforce Retry-After backoff
                async with self._slot_lock:
                    self.claimed_slots -= 1
                retry_seconds = result.get("retry_after", 300)
                self.storage.update_url_fetch_result(
                    url_hash=url_hash,
                    status="SKIPPED",
                    http_code=429,
                    retry_after_seconds=retry_seconds
                )
                logger.warning(f"[Downloader-{worker_id}] Rate-limited (429) on {url} (Backing off for {retry_seconds}s)")
                continue

            if (status_code == 200 and html) or is_praw:
                self.storage.update_url_fetch_result(
                    url_hash=url_hash,
                    status="CRAWLED",
                    http_code=status_code,
                    etag=result.get("etag"),
                    last_modified=result.get("last_modified"),
                    revisit_interval_hours=revisit_hrs
                )
                self.crawled_count += 1
                payload = result.get("reddit_posts") if is_praw else html
                await self.raw_page_queue.put((result["url"], payload, depth, is_praw))
                logger.info(
                    f"[Downloader-{worker_id}] SUCCESS -> {url} "
                    f"(Pages Downloaded this session: {self.crawled_count}/{self.max_pages}) | "
                    f"Transport: {result.get('transport', 'UNKNOWN')} | "
                    f"Parser Queue Size: {self.raw_page_queue.qsize()}"
                )
            else:
                async with self._slot_lock:
                    self.claimed_slots -= 1
                self.storage.update_url_fetch_result(
                    url_hash=url_hash,
                    status="FAILED",
                    http_code=status_code,
                    revisit_interval_hours=2.0
                )
                self.failed_count += 1
                logger.warning(f"[Downloader-{worker_id}] FAILED -> {url} (HTTP {status_code})")

        logger.debug(f"[Downloader-{worker_id}] Worker loop terminated.")

    async def parser_worker(self, worker_id: int):
        """Pulls raw HTML from the queue, checks duplicates, extracts leads & links."""
        logger.debug(f"[Parser-{worker_id}] Worker loop started.")
        while self.is_running:
            try:
                item = await asyncio.wait_for(self.raw_page_queue.get(), timeout=2.5)
                if len(item) == 4:
                    url, payload, depth, is_praw = item
                else:
                    url, payload, depth = item
                    is_praw = False
            except asyncio.TimeoutError:
                if not self.frontier.has_pending_urls() and self.raw_page_queue.empty():
                    break
                continue

            try:
                logger.debug(f"[Parser-{worker_id}] Processing payload: {url}")
                if is_praw:
                    parse_result = self.parser.parse_reddit_posts_list(url, payload)
                else:
                    parse_result = self.parser.parse_page(url, payload, depth)

                outbound_links = parse_result.get("outbound_links", [])
                newly_enqueued = 0
                for outbound_url in outbound_links:
                    if await self.frontier.add_url(outbound_url, depth=depth + 1):
                        newly_enqueued += 1

                if parse_result.get("is_duplicate"):
                    self.duplicates_count += 1
                    dup_type = parse_result.get("duplicate_type")
                    if dup_type == "exact":
                        self.exact_duplicates_discarded += 1
                    else:
                        self.simhash_duplicates_discarded += 1
                else:
                    classification = parse_result.get("classification")
                    if classification == CLASSIFICATION_COMMERCIAL:
                        self.commercial_rejected += 1
                    elif classification == CLASSIFICATION_TECHNICAL:
                        self.technical_rejected += 1
                    elif classification == CLASSIFICATION_DIY_OFFGRID:
                        self.diy_rejected += 1
                    elif classification == CLASSIFICATION_SPAM_VENDOR:
                        self.spam_rejected += 1
                    elif classification == CLASSIFICATION_GENERAL_INFO:
                        self.general_info_rejected += 1
                    elif classification == CLASSIFICATION_RESIDENTIAL_EXISTING:
                        self.existing_owner_count += 1
                    elif classification == CLASSIFICATION_RESIDENTIAL_UPGRADE:
                        self.upgrade_count += 1
                    elif classification == CLASSIFICATION_RESIDENTIAL_REPAIR:
                        self.repair_count += 1
                    elif classification == CLASSIFICATION_RESIDENTIAL_NEW_BUYER:
                        self.residential_new_buyer_count += 1
                    elif classification == CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER:
                        self.residential_quote_seeker_count += 1

                    if parse_result.get("solar_lead"):
                        self.leads_discovered += 1

                    logger.debug(
                        f"[Parser-{worker_id}] {url} -> "
                        f"Discovered {len(outbound_links)} links ({newly_enqueued} new URLs enqueued)"
                    )
            except Exception as e:
                logger.error(f"[Parser-{worker_id} Error] Error processing {url}: {e}", exc_info=True)
            finally:
                self.raw_page_queue.task_done()

        logger.debug(f"[Parser-{worker_id}] Worker loop terminated.")

    async def run(self):
        # Phase 1: Google Places Solar Contractor Discovery (if requested)
        if self.places_enabled:
            logger.info("[Main Places] Running Google Places Solar Contractor Discovery Phase...")
            try:
                places_res = self.places_source.run_discovery(queries=self.places_queries)
                self.installers_export_file = places_res.get("export_file")
                logger.info(f"[Main Places] Discovered {places_res['total_discovered']} verified solar contractor places.")
            except Exception as e:
                logger.error(f"[Main Places Error] {e}", exc_info=True)

        await self.downloader.start()
        await self.initialize_frontier()

        num_downloaders = 3
        num_parsers = 2

        downloader_tasks = [
            asyncio.create_task(self.downloader_worker(i + 1))
            for i in range(num_downloaders)
        ]
        parser_tasks = [
            asyncio.create_task(self.parser_worker(i + 1))
            for i in range(num_parsers)
        ]

        logger.info(
            f"[Main] Crawler active with {num_downloaders} Downloaders and {num_parsers} Parsers. "
            f"Target: {self.max_pages} pages | Transport: {self.downloader.active_transport}"
        )

        # Supervisory loop
        while self.is_running and self.crawled_count < self.max_pages:
            await asyncio.sleep(2.0)
            stats = self.frontier.get_queue_stats()
            logger.info(
                f"[Monitor Status] Progress: {self.crawled_count}/{self.max_pages} pages | "
                f"Frontier Active Domains: {stats['active_domains']} | URLs in Memory: {stats['total_in_memory_urls']} | "
                f"Duplicates Prevented: {stats.get('canonical_duplicates_prevented', 0)} | "
                f"Parser Queue: {self.raw_page_queue.qsize()}"
            )
            all_downloaders_done = all(d.done() for d in downloader_tasks)
            if (not self.frontier.has_pending_urls() or all_downloaders_done) and self.raw_page_queue.empty():
                logger.info("[Main] Downloaders finished and queues are exhausted. Crawl completed.")
                break

        self.is_running = False

        # Shutdown workers
        await asyncio.gather(*downloader_tasks, return_exceptions=True)
        await self.raw_page_queue.join()
        for p in parser_tasks:
            p.cancel()

        await self.downloader.close()

        # Phase 3: Automated Contractor Matching Engine
        try:
            contractors = self.storage.get_solar_installers(limit=500)
            recent_leads = self.storage.get_recent_leads(limit=100)
            if contractors and recent_leads:
                logger.info(f"[Main Matching] Matching {len(recent_leads)} qualified leads against {len(contractors)} verified contractors...")
                for l in recent_leads:
                    if not l.get("assigned_contractor_id"):
                        matches = ContractorMatcher.match_lead(l, contractors, top_n=1, min_score=40)
                        if matches and l.get("id"):
                            best = matches[0]
                            self.storage.assign_lead_contractor(
                                lead_id=l["id"],
                                contractor_id=best["place_id"],
                                new_status="ASSIGNED",
                                reason=f"Matched to {best['company_name']} ({best.get('city')}, {best.get('state')}) (Score: {best['match_score']})"
                            )
                            self.matched_leads_count += 1
                if self.matched_leads_count > 0:
                    logger.info(f"[Main Matching] Successfully assigned {self.matched_leads_count} leads to local contractors.")
        except Exception as e:
            logger.warning(f"Contractor matching failed: {e}")

        # Export consolidated leads
        try:
            self.export_file = self.storage.export_all_leads_summary()
            logger.info(f"[Leads Export] Exported consolidated leads to {self.export_file}")
        except Exception as e:
            logger.warning(f"Could not export consolidated leads summary: {e}")

        # Export verified business contacts
        try:
            self.contacts_export_file = self.storage.export_business_contacts_summary()
            logger.info(f"[Contacts Export] Exported verified business contacts to {self.contacts_export_file}")
        except Exception as e:
            logger.warning(f"Could not export business contacts: {e}")

        # Export verified solar installers from Google Places
        try:
            self.installers_export_file = self.storage.export_installers_to_json()
            if self.installers_export_file:
                logger.info(f"[Installers Export] Exported verified solar contractors to {self.installers_export_file}")
        except Exception as e:
            logger.warning(f"Could not export solar installers: {e}")

        self.print_summary()

    def print_summary(self):
        db_stats = self.storage.get_database_stats()
        quality_metrics = self.storage.get_quality_metrics()
        q_stats = self.frontier.get_queue_stats()
        duplicates_prevented = q_stats.get("canonical_duplicates_prevented", 0)
        business_contacts = self.storage.get_business_contacts(limit=50)
        source_report = self.storage.get_source_report()

        freshness_map = quality_metrics.get("by_freshness", {})
        fresh_leads_count = freshness_map.get("0-7 days", 0) + freshness_map.get("8-30 days", 0)
        total_leads = db_stats['leads_count']
        yield_rate = round((total_leads / max(1, self.crawled_count)) * 100.0, 1) if self.crawled_count > 0 else 0.0

        print("\n" + "=" * 80)
        print("                     SOLAR CRAWLER RUN SUMMARY")
        print("=" * 80)
        print(f"Session Pages Downloaded         : {self.crawled_count} / {self.max_pages}")
        print(f"Canonical Duplicates Prevented   : {duplicates_prevented} (before network fetch)")
        print(f"Content Duplicates Discarded     : {self.duplicates_count} "
              f"(Exact SHA-256: {self.exact_duplicates_discarded}, SimHash Near-Dup: {self.simhash_duplicates_discarded})")
        print(f"Session Genuine Leads Discovered : {self.leads_discovered}")
        print(f"Session Failed Downloads         : {self.failed_count}")
        print(f"Verified Business Inquiries      : {len(business_contacts)}")
        print(f"★ Key Metric: Crawl Yield Rate   : {yield_rate} qualified leads per 100 pages downloaded")
        print("-" * 80)
        print("Network Transport Usage:")
        print(f"  Primary Active Transport       : {self.downloader.active_transport}")
        print(f"  Deno Gateway Requests          : {self.downloader.gateway_requests}")
        print(f"  Direct HTTPX Requests          : {self.downloader.direct_requests}")
        print(f"  Reddit PRAW Requests           : {self.downloader.praw_requests}")
        print("-" * 80)
        print("Three-Tier Lead Segmentation (Distinct Customer Audiences):")
        print(f"  [1] New Buyers (First-time installation / quote seekers): {quality_metrics.get('new_buyers_count', 0)}")
        print(f"      - Session Turnkey Quotes   : {self.residential_new_buyer_count}")
        print(f"      - Session Quote Comparison : {self.residential_quote_seeker_count}")
        print(f"  [2] Existing Customers (Additional panels / battery)   : {quality_metrics.get('expansion_count', 0)}")
        print(f"      - Session System Upgrades  : {self.upgrade_count}")
        print(f"  [3] Service Customers (Repairs / inverter replacements) : {quality_metrics.get('repair_count', 0)}")
        print(f"      - Session System Repairs   : {self.repair_count}")
        print("  Non-Buyer Filters Dropped:")
        print(f"      - Commercial Projects      : {self.commercial_rejected} (MW scale, commercial lighting)")
        print(f"      - Technical Teardowns      : {self.technical_rejected} (optimizer teardown, busbar, NEC)")
        print(f"      - DIY / Off-Grid Builds    : {self.diy_rejected} (van/RV, houseboat, DIY battery)")
        print(f"      - Vendor Spam / Self-Promo : {self.spam_rejected}")
        print(f"      - General Info / Articles  : {self.general_info_rejected}")
        print("-" * 80)
        print("Lead Freshness Distribution:")
        print(f"  0-7 days (Hot / Active Now)    : {freshness_map.get('0-7 days', 0)}")
        print(f"  8-30 days (Recent Discussions) : {freshness_map.get('8-30 days', 0)}")
        print(f"  31-90 days (Medium-term)       : {freshness_map.get('31-90 days', 0)}")
        print(f"  Older than 90 days (Archive)   : {freshness_map.get('Older than 90 days', 0)}")
        print("-" * 80)
        print("Cumulative Database State:")
        print(f"  Total URLs in Store            : {db_stats['total_urls']}")
        print(f"  URLs by Status                 : {db_stats['urls_by_status']}")
        print(f"  Total Unique Content Hashes    : {db_stats['content_hashes']}")
        print(f"  Total Qualified Leads in DB    : {db_stats['leads_count']}")
        print(f"  Leads by Stage                 : {quality_metrics.get('by_stage', {})}")
        print(f"  Leads by Category              : {quality_metrics.get('by_category', {})}")
        print(f"  CRM Lifecycle Stages           : {quality_metrics.get('by_lifecycle', {})}")
        print(f"  Data Quality Statuses          : {quality_metrics.get('by_validation', {})}")
        if self.matched_leads_count > 0:
            print(f"  Contractor Matched Leads       : {self.matched_leads_count} leads assigned")

        # Recursive Source Discovery Hierarchy Stats
        src_disc = q_stats.get("source_discovery_stats", {})
        if any(src_disc.values()):
            print("-" * 80)
            print("Recursive Source Discovery Engine Hierarchy:")
            print(f"  Categories Discovered          : {src_disc.get('categories_discovered', 0)}")
            print(f"  Subcategories Discovered       : {src_disc.get('subcategories_discovered', 0)}")
            print(f"  Pagination URLs Discovered     : {src_disc.get('pagination_discovered', 0)}")
            print(f"  Discussion Threads Discovered  : {src_disc.get('threads_discovered', 0)}")
            print(f"  Valid Residential Threads      : {src_disc.get('valid_residential_threads', 0)}")
            print(f"  Rejected Commercial Threads    : {src_disc.get('rejected_commercial', 0)}")
            print(f"  Rejected Member Profiles       : {src_disc.get('rejected_members', 0)}")
            print(f"  Duplicate URLs Blocked         : {src_disc.get('duplicate_urls', 0)}")

        if business_contacts:
            print("-" * 80)
            print("Verified Public Contact Enrichment (Separated & Auditable):")
            for bc in business_contacts[:6]:
                user_str = f" | User: {bc['author_username']}" if bc.get("author_username") else ""
                print(f"  • [{bc['contact_type']}] {bc['contact_value']} ({bc.get('company_name') or bc.get('source_name')}{user_str})")
                print(f"    Source: {bc.get('source_permalink') or bc['source_url']}")

        if source_report:
            print("-" * 80)
            print("USA Source Registry Health & Discovery Quality:")
            for src in source_report:
                if src.get("total_downloaded", 0) > 0 or src.get("status") == "UNAVAILABLE_BLOCKED":
                    status_tag = f"[{src['status']}]" if src['status'] != "ACTIVE" else "[ACTIVE]"
                    last_crawled = f" | Last Crawl: {src.get('last_successful_crawl_at', 'N/A')}" if src.get("last_successful_crawl_at") else ""
                    latest_post = f" | Latest Post: {src.get('latest_post_date', 'N/A')}" if src.get("latest_post_date") else ""
                    print(f"  {status_tag:<22} {src['name']:<28} | Crawled: {src['total_downloaded']:<3} | Leads: {src['leads_discovered']:<2} | Yield: {src['quality_score']}%{latest_post}")

        print("-" * 80)

        leads = self.storage.get_recent_leads(limit=10)
        if leads:
            print("Verified Solar Leads & Buyer Intent Evidence:")
            for idx, lead in enumerate(leads, 1):
                author_str = f"Author: {lead.get('author') or 'Anonymous'}"
                loc_str = f"Loc: {lead.get('state') or 'USA'}" + (f" ({lead.get('zip_code')})" if lead.get('zip_code') else "")
                metrics = []
                if lead.get("system_size_kw"):
                    metrics.append(f"{lead['system_size_kw']} kW")
                if lead.get("electricity_bill"):
                    metrics.append(f"${lead['electricity_bill']:.0f}/mo bill")
                if lead.get("property_type"):
                    metrics.append(lead["property_type"])
                metric_str = f" | {', '.join(metrics)}" if metrics else ""

                freshness_str = f"Freshness: {lead.get('lead_freshness') or 'Unknown'}"
                if lead.get("published_at"):
                    freshness_str += f" (Posted: {lead.get('published_at')}"
                    if lead.get("latest_reply_at"):
                        freshness_str += f", Reply: {lead.get('latest_reply_at')}"
                    freshness_str += ")"

                print(f"  [{idx}] [{lead.get('lead_category', 'Lead').upper()}] Score: {lead.get('lead_score')} | Stage: {lead.get('buyer_stage')} | Class: {lead.get('classification_type')}")
                print(f"      Title    : {lead.get('post_title') or 'Discussion'}")
                print(f"      Source   : {lead.get('source_name')} | {author_str} | {loc_str}{metric_str}")
                print(f"      {freshness_str}")
                
                # Multi-dimensional qualification and solar opportunity
                intent_score = lead.get("purchase_intent_score")
                if intent_score is not None:
                    res_c = int((lead.get("residential_confidence") or 0.0) * 100)
                    loc_c = int((lead.get("location_confidence") or 0.0) * 100)
                    fresh_c = int((lead.get("freshness_score") or 0.0) * 100)
                    print(f"      Multi-Dim: Intent: {intent_score:.1f}/10 | Residential: {res_c}% | Location: {loc_c}% | Freshness: {fresh_c}%")
                
                if lead.get("estimated_system_size_kw") or lead.get("estimated_monthly_usage_kwh"):
                    kw_est = f"{lead.get('estimated_system_size_kw')} kW" if lead.get("estimated_system_size_kw") else "N/A"
                    kwh_est = f"{lead.get('estimated_monthly_usage_kwh'):.0f} kWh/mo" if lead.get("estimated_monthly_usage_kwh") else "N/A"
                    offset_est = f"{lead.get('estimated_solar_offset_percent'):.0f}%" if lead.get("estimated_solar_offset_percent") else "N/A"
                    print(f"      Estimated: Size: {kw_est} | Usage: {kwh_est} | Solar Offset: {offset_est}")

                val_stat = lead.get("validation_status")
                if val_stat:
                    flags = lead.get("validation_flags") or "None"
                    print(f"      Status   : CRM: {lead.get('lead_status', 'NEW')} | Quality: {val_stat} (Flags: {flags})")

                if lead.get("assigned_contractor_id"):
                    print(f"      Contractor Assigned: {lead['assigned_contractor_id']}")

                print(f"      URL      : {lead.get('post_url')}")
                
                # Print auditable evidence snippets
                evidence = lead.get("evidence_snippets")
                if isinstance(evidence, str):
                    try:
                        evidence = json.loads(evidence)
                    except Exception:
                        evidence = [evidence]
                if evidence:
                    print("      Evidence :")
                    for ev in evidence[:2]:
                        print(f"        • {ev}")
                print()

        installer_stats = self.storage.get_installer_stats()
        if installer_stats.get("total_installers", 0) > 0:
            print("-" * 80)
            print("Google Places Verified Residential Solar Contractors:")
            print(f"  Total Verified Contractors    : {installer_stats['total_installers']}")
            print(f"  With Direct Phone Numbers     : {installer_stats['with_phones']}")
            print(f"  With Official Websites        : {installer_stats['with_websites']}")
            print(f"  Geographic Breakdown by State : {installer_stats['by_state']}")
            
            top_contractors = self.storage.get_solar_installers(limit=6)
            if top_contractors:
                print("  Sample Verified Contractors Discovered:")
                for c in top_contractors:
                    rating_str = f"★ {c['rating']} ({c['user_ratings_total']} reviews)" if c.get("rating") else "Unrated"
                    phone_str = f" | Phone: {c['phone_number']}" if c.get("phone_number") else ""
                    print(f"    • {c['company_name']} ({c.get('city') or 'USA'}, {c.get('state') or ''}) - {rating_str}{phone_str}")
                    if c.get("website_url"):
                        print(f"      Website: {c['website_url']}")

        print("Output Locations:")
        if self.log_file:
            print(f"  Session Log File               : {self.log_file}")
        if self.export_file:
            print(f"  Leads Export JSON              : {self.export_file}")
        if self.contacts_export_file:
            print(f"  Business Contacts JSON         : {self.contacts_export_file}")
        if self.installers_export_file:
            print(f"  Solar Contractors JSON         : {self.installers_export_file}")
        print(f"  SQLite Database                : {self.storage.db_path}")
        print(f"  Leads Directory                : {LEADS_DIR}")
        print("=" * 80)


def main():
    parser = argparse.ArgumentParser(description="Distributed Solar Lead Discovery Web Crawler")
    parser.add_argument("max_pages", nargs="?", type=int, default=15, help="Maximum pages to download in this run (default: 15)")
    parser.add_argument("--fresh", action="store_true", help="Clear past database state and start a fresh crawl from seeds")
    parser.add_argument("--debug", action="store_true", help="Enable verbose DEBUG logging")
    parser.add_argument("--status", action="store_true", help="Print current database statistics without crawling")
    parser.add_argument("--places", action="store_true", help="Run Google Places solar contractor discovery using GOOGLE_MAPS_API_KEY")
    parser.add_argument("--places-only", action="store_true", help="Run Google Places discovery only (skips web crawling)")
    parser.add_argument("--places-query", nargs="+", help="Specific custom queries for Google Places (e.g. 'residential solar in Austin TX')")

    args = parser.parse_args()
    log_file_path = setup_logging(debug_mode=args.debug)

    if args.status:
        storage = CrawlerStorage()
        stats = storage.get_database_stats()
        quality_metrics = storage.get_quality_metrics()
        platform_metrics = storage.get_platform_metrics()

        print("\n" + "=" * 80)
        print("          SOLAR LEAD INTELLIGENCE PLATFORM — SYSTEM STATUS")
        print("=" * 80)
        print("1. HOMEOWNER LEAD INTELLIGENCE (Consumer Buying & Service Opportunities):")
        print(f"   Total Qualified Leads in DB : {platform_metrics['homeowner_leads']['total_qualified_leads']}")
        print(f"   • New Buyers (Quote Seekers): {platform_metrics['homeowner_leads']['new_buyers']}")
        print(f"   • Existing Customer Upgrades: {platform_metrics['homeowner_leads']['existing_customer_upgrades']}")
        print(f"   • Service / Repair Inquiries: {platform_metrics['homeowner_leads']['service_repairs']}")
        print(f"   • By Category               : {platform_metrics['homeowner_leads']['by_category']}")
        print(f"   • By Freshness Tier         : {platform_metrics['homeowner_leads']['by_freshness']}")

        print("\n2. DISCUSSION FORUM EXTRACTION (Discussions & Granular Posts):")
        print(f"   Total Posts Extracted       : {platform_metrics['discussion_forum_extraction']['total_posts_extracted']}")
        print(f"   Unique Threads Extracted    : {platform_metrics['discussion_forum_extraction']['unique_threads_extracted']}")
        print(f"   Total URLs Tracked          : {stats['total_urls']}")
        print(f"   URLs by Fetch Status        : {stats['urls_by_status']}")
        print(f"   Unique Content Hashes       : {stats['content_hashes']}")

        inst = platform_metrics['contractor_discovery_places']
        print("\n3. GOOGLE PLACES CONTRACTOR DIRECTORY (Separate Business Discovery Pipeline):")
        print(f"   Total Businesses Discovered : {inst['places_discovered']}")
        print(f"   • Direct Business Phones    : {inst['with_phones']}")
        print(f"   • Official Company Websites : {inst['with_websites']}")
        print(f"   • Business Categories       : {inst['by_business_category']}")
        print(f"   • States Covered            : {inst['states_covered']}")

        print(f"\nDatabase File: {storage.db_path}")
        print("=" * 80 + "\n")
        return

    if args.places_only:
        places_source = GooglePlacesSource()
        logger.info("[Main Places-Only] Running Google Places contractor discovery exclusively...")
        queries = [" ".join(args.places_query)] if args.places_query and len(args.places_query) == 1 else args.places_query
        res = places_source.run_discovery(queries=queries)
        export_file = places_source.storage.export_installers_to_json()
        stats = places_source.storage.get_installer_stats()
        print("\n" + "=" * 80)
        print("           GOOGLE PLACES SOLAR CONTRACTOR DISCOVERY SUMMARY")
        print("=" * 80)
        print(f"Total Verified Contractors Discovered : {stats['total_installers']}")
        print(f"With Direct Phone Numbers             : {stats['with_phones']}")
        print(f"With Official Company Websites        : {stats['with_websites']}")
        print(f"Geographic Breakdown by State         : {stats['by_state']}")
        top = places_source.storage.get_solar_installers(limit=10)
        if top:
            print("-" * 80)
            print("Verified Residential Solar Contractors:")
            for c in top:
                rating_str = f"★ {c['rating']} ({c['user_ratings_total']} reviews)" if c.get("rating") else "Unrated"
                phone_str = f" | Phone: {c['phone_number']}" if c.get("phone_number") else ""
                print(f"  • {c['company_name']} ({c.get('city') or 'USA'}, {c.get('state') or ''}) - {rating_str}{phone_str}")
                if c.get("website_url"):
                    print(f"    Website: {c['website_url']}")
                if c.get("formatted_address"):
                    print(f"    Address: {c['formatted_address']}")
        print("-" * 80)
        if export_file:
            print(f"Export JSON: {export_file}")
        print("=" * 80 + "\n")
        return

    coordinator = CrawlerCoordinator(
        max_pages=args.max_pages,
        fresh_start=args.fresh,
        log_file=log_file_path,
        places_enabled=args.places,
        places_queries=args.places_query
    )

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coordinator.run())
    except KeyboardInterrupt:
        logger.warning("[Main] KeyboardInterrupt received. Shutting down gracefully...")
    finally:
        loop.close()


if __name__ == "__main__":
    main()
