"""
URL Frontier Module
Manages FIFO and Residential-Priority Politeness Queues per domain,
enforces strict domain allowlists, blocks irrelevant URL patterns,
coordinates with the URL Seen deduplication layer, and supports crash recovery/resume.
"""

import re
import time
import asyncio
import logging
from collections import deque
from typing import Optional, Dict, Tuple, List, Any
from config import (
    PER_DOMAIN_DELAY_SECONDS,
    MAX_CRAWL_DEPTH,
    BLOCKED_EXTENSIONS,
    ALLOWED_DOMAINS,
    BLOCKED_URL_PATTERNS,
    RESIDENTIAL_TOPICS,
    EXCLUDED_TOPICS
)
from deduplicator import Deduplicator, BloomFilter
from storage import CrawlerStorage
from sources import get_adapter_for_domain

logger = logging.getLogger("Crawler.Frontier")


class URLFrontier:
    def __init__(self, storage: CrawlerStorage, politeness_delay: float = PER_DOMAIN_DELAY_SECONDS):
        self.storage = storage
        self.politeness_delay = politeness_delay
        
        # Fast in-memory Bloom filter backed by SQLite
        self.bloom_filter = BloomFilter(size=2_000_000, num_hashes=5)
        
        # Politeness Structure: domain -> deque of (url, depth, url_hash, is_priority)
        self.domain_queues: Dict[str, deque] = {}
        
        # Domain tracking for politeness delay: domain -> last_request_timestamp
        self.domain_last_crawled: Dict[str, float] = {}
        
        # List of active domains for round-robin rotation
        self.active_domains: List[str] = []
        self._domain_index = 0
        
        # Set of in-memory hashes to prevent duplicate enqueues within the same session
        self._in_memory_hashes = set()
        self.canonical_duplicates_prevented = 0
        
        # Source-Level Discovery Metrics Tracking
        self.source_discovery_stats: Dict[str, Dict[str, int]] = {}

        # Thread/Async lock for queue modifications
        self._lock = asyncio.Lock()

    def _record_discovery_metric(self, domain: str, metric: str, count: int = 1):
        """Records granular discovery events per source platform."""
        adapter = get_adapter_for_domain(domain)
        source_id = adapter.source_id if adapter else domain.lower().replace("www.", "")
        if source_id not in self.source_discovery_stats:
            self.source_discovery_stats[source_id] = {
                "categories_discovered": 0,
                "subcategories_discovered": 0,
                "pagination_discovered": 0,
                "threads_discovered": 0,
                "valid_residential_threads": 0,
                "rejected_commercial": 0,
                "rejected_members": 0,
                "duplicate_urls": 0
            }
        self.source_discovery_stats[source_id][metric] = (
            self.source_discovery_stats[source_id].get(metric, 0) + count
        )

    def get_source_discovery_stats(self, source_id: Optional[str] = None) -> Dict[str, Any]:
        """Returns discovery metrics for all sources or a specific source."""
        if source_id:
            return self.source_discovery_stats.get(source_id, {})
        return dict(self.source_discovery_stats)

    # High purchase intent slug keywords
    BUYER_SLUG_SIGNALS = [
        "quote", "installer", "comparing", "cost", "proposal", "recommendation",
        "pricing", "review", "contractor", "fair-price", "estimate", "powerwall"
    ]

    def should_crawl(self, raw_url: str, depth: int) -> Tuple[bool, str]:
        """
        Evaluates whether a candidate URL should enter the crawl queue:
        - Must be HTTP/HTTPS
        - Depth <= MAX_CRAWL_DEPTH
        - Must be in ALLOWED_DOMAINS (excludes third-party ads/affiliates)
        - Must NOT match BLOCKED_URL_PATTERNS (member profiles, announcements, login)
        - Must NOT match EXCLUDED_TOPICS (commercial lighting, warehouse, street lights)
        - Must NOT exceed page 3 pagination (prevents digging into 10-year old archive pages)
        """
        if not raw_url.startswith(("http://", "https://")):
            return False, "Invalid protocol"

        if depth > MAX_CRAWL_DEPTH:
            return False, f"Depth {depth} exceeds max {MAX_CRAWL_DEPTH}"

        lower_url = raw_url.lower().split("?")[0]
        for ext in BLOCKED_EXTENSIONS:
            if lower_url.endswith(ext):
                return False, f"Blocked file extension ({ext})"

        # Avoid crawling historical archive pagination (> page 3)
        if re.search(r'/page(?:[4-9]|\d{2,})\b', lower_url):
            return False, "Exceeds pagination freshness limit (> page 3)"

        canonical_url, domain, url_hash = Deduplicator.canonicalize_url(raw_url)
        if not domain:
            return False, "Missing hostname"

        # Check domain allowlist
        base_domain = domain.lower()
        is_allowed = any(base_domain == d or base_domain.endswith("." + d) for d in ALLOWED_DOMAINS)
        if not is_allowed:
            return False, f"External domain '{domain}' not in ALLOWED_DOMAINS"

        # Check blocked URL patterns
        canonical_lower = canonical_url.lower()
        for pattern in BLOCKED_URL_PATTERNS:
            if pattern.lower() in canonical_lower:
                if any(m in pattern for m in ["member", "user", "profile"]):
                    self._record_discovery_metric(domain, "rejected_members")
                elif any(c in pattern for c in ["commercial", "industrial", "lighting", "warehouse"]):
                    self._record_discovery_metric(domain, "rejected_commercial")
                return False, f"Matches blocked pattern '{pattern}'"

        # Check commercial / irrelevant topic exclusions
        for topic in EXCLUDED_TOPICS:
            if topic.lower() in canonical_lower:
                self._record_discovery_metric(domain, "rejected_commercial")
                return False, f"Matches excluded category '{topic}'"

        # Source Adapter Pre-enqueue Validation (platform-specific category, geo, and section rules)
        adapter = get_adapter_for_domain(domain)
        is_allowed, reason = adapter.is_valid_url(canonical_url)
        if not is_allowed:
            if "commercial" in reason.lower() or "lighting" in reason.lower():
                self._record_discovery_metric(domain, "rejected_commercial")
            elif "member" in reason.lower() or "user" in reason.lower():
                self._record_discovery_metric(domain, "rejected_members")
            return False, reason

        url_type = adapter.classify_url_type(canonical_url)
        if url_type == "IGNORED":
            return False, f"Classified as IGNORED by {adapter.name} adapter"

        return True, "OK"

    def get_url_priority(self, url: str) -> int:
        """
        Calculates priority score:
        3: Explicit Buyer Intent in URL slug (quotes, installers, proposals)
        2: Residential solar topic
        1: Generic page
        """
        lower = url.lower()
        if any(sig in lower for sig in self.BUYER_SLUG_SIGNALS):
            return 3
        if any(topic.lower() in lower for topic in RESIDENTIAL_TOPICS):
            return 2
        return 1

    def is_residential_priority(self, url: str) -> bool:
        """Checks if URL directly mentions residential solar keywords."""
        return self.get_url_priority(url) >= 2

    async def restore_pending_urls(self, limit: int = 5000) -> int:
        """
        Restores unfinished PENDING URLs from SQLite into in-memory politeness queues,
        filtering out any that no longer meet allowlist criteria.
        """
        pending_list = self.storage.get_pending_urls(limit=limit)
        restored = 0
        async with self._lock:
            for url, domain, depth, url_hash in pending_list:
                if url_hash in self._in_memory_hashes:
                    continue

                allowed, reason = self.should_crawl(url, depth)
                if not allowed:
                    self.storage.update_url_status(url_hash, "SKIPPED", 0)
                    continue

                if domain not in self.domain_queues:
                    self.domain_queues[domain] = deque()
                    self.active_domains.append(domain)
                    self.domain_last_crawled[domain] = 0.0

                is_priority = self.is_residential_priority(url)
                if is_priority:
                    self.domain_queues[domain].appendleft((url, depth, url_hash))
                else:
                    self.domain_queues[domain].append((url, depth, url_hash))

                self._in_memory_hashes.add(url_hash)
                self.bloom_filter.add(url_hash)
                restored += 1

        if restored > 0:
            logger.info(f"[Frontier Resume] Restored {restored} filtered PENDING URLs across {len(self.active_domains)} domains.")
        return restored

    async def add_url(
        self,
        raw_url: str,
        depth: int = 0,
        parent_url: Optional[str] = None,
        discovery_method: str = "crawl"
    ) -> bool:
        """
        Ingests a candidate URL into the frontier with residential intent prioritization
        and recursive discovery tracking.
        """
        allowed, reason = self.should_crawl(raw_url, depth)
        if not allowed:
            logger.debug(f"[Frontier Filtered] {raw_url} -> {reason}")
            return False

        canonical_url, domain, url_hash = Deduplicator.canonicalize_url(raw_url)
        adapter = get_adapter_for_domain(domain)
        source_id = adapter.source_id if adapter else domain.lower().replace("www.", "")
        url_type = adapter.classify_url_type(canonical_url)

        async with self._lock:
            if url_hash in self._in_memory_hashes:
                self.canonical_duplicates_prevented += 1
                self._record_discovery_metric(domain, "duplicate_urls")
                return False

            existing_status = self.storage.get_url_status(url_hash)
            if existing_status:
                self.canonical_duplicates_prevented += 1
                self._record_discovery_metric(domain, "duplicate_urls")
                status, code = existing_status
                if status in ("CRAWLED", "FAILED", "SKIPPED"):
                    logger.debug(f"[Frontier Skipped] Already visited ({status} HTTP {code}): {canonical_url}")
                    return False

            self.bloom_filter.add(url_hash)
            priority = self.get_url_priority(canonical_url)

            # Record discovery metrics by page type
            if url_type == "CATEGORY":
                self._record_discovery_metric(domain, "categories_discovered")
            elif url_type == "SUBCATEGORY":
                self._record_discovery_metric(domain, "subcategories_discovered")
            elif url_type == "THREAD":
                self._record_discovery_metric(domain, "threads_discovered")
                self._record_discovery_metric(domain, "valid_residential_threads")
            elif url_type in ("THREAD_PAGINATION", "CATEGORY_PAGINATION"):
                self._record_discovery_metric(domain, "pagination_discovered")

            if not existing_status:
                self.storage.save_new_url(
                    url_hash=url_hash,
                    url=canonical_url,
                    domain=domain,
                    depth=depth,
                    source_id=source_id,
                    parent_url=parent_url,
                    page_type=url_type,
                    discovery_method=discovery_method,
                    priority=priority
                )

            if domain not in self.domain_queues:
                self.domain_queues[domain] = deque()
                self.active_domains.append(domain)
                self.domain_last_crawled[domain] = 0.0

            # Tier 3 (Explicit Buyer Intent in slug): push to front of queue
            if priority == 3:
                self.domain_queues[domain].appendleft((canonical_url, depth, url_hash))
                logger.info(f"★ Enqueued [Tier 1 BUYER INTENT Depth {depth}]: {canonical_url}")
            elif priority == 2:
                self.domain_queues[domain].append((canonical_url, depth, url_hash))
                logger.info(f"Enqueued [Residential Depth {depth}]: {canonical_url}")
            else:
                self.domain_queues[domain].append((canonical_url, depth, url_hash))
                logger.info(f"Enqueued [General Depth {depth}]: {canonical_url}")

            self._in_memory_hashes.add(url_hash)
            return True

    async def get_next_url(self) -> Optional[Tuple[str, str, int, str]]:
        """
        Fetches the next eligible URL respecting per-domain politeness delay.
        """
        while True:
            async with self._lock:
                if not self.active_domains:
                    return None

                total_domains = len(self.active_domains)
                now = time.time()
                min_wait = float("inf")

                for _ in range(total_domains):
                    self._domain_index = (self._domain_index + 1) % len(self.active_domains)
                    domain = self.active_domains[self._domain_index]
                    queue = self.domain_queues.get(domain)

                    if not queue:
                        continue

                    last_crawl = self.domain_last_crawled.get(domain, 0.0)
                    time_since_crawl = now - last_crawl
                    
                    if time_since_crawl >= self.politeness_delay:
                        url, depth, url_hash = queue.popleft()
                        self.domain_last_crawled[domain] = time.time()
                        
                        if not queue:
                            self.active_domains.remove(domain)
                            del self.domain_queues[domain]
                            if self.active_domains:
                                self._domain_index %= len(self.active_domains)
                        
                        return url, domain, depth, url_hash
                    else:
                        wait = self.politeness_delay - time_since_crawl
                        if wait < min_wait:
                            min_wait = wait

            if min_wait != float("inf") and min_wait > 0:
                await asyncio.sleep(min(min_wait, 0.5))
            else:
                return None

    def has_pending_urls(self) -> bool:
        return len(self.active_domains) > 0

    def get_queue_stats(self) -> Dict[str, Any]:
        return {
            "active_domains": len(self.active_domains),
            "domains": list(self.active_domains),
            "total_in_memory_urls": sum(len(q) for q in self.domain_queues.values()),
            "canonical_duplicates_prevented": self.canonical_duplicates_prevented,
            "source_discovery_stats": self.source_discovery_stats
        }
