"""
Storage Layer
SQLite-backed persistence for URLs, Content Deduplication hashes, and Extracted Solar Leads.
Includes enhanced schema for buyer stages, lead scores, content classification,
freshness categorization, and verifiable evidence snippets, with JSON export into leads/ folder.
"""

import sqlite3
import json
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple
from config import DB_PATH, LEADS_DIR

logger = logging.getLogger("Crawler.Storage")


class CrawlerStorage:
    def __init__(self, db_path=DB_PATH):
        self.db_path = str(db_path)
        self._mem_conn = None
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:", timeout=30.0)
            self._mem_conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            
            # URL Storage Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS urls (
                    url_hash TEXT PRIMARY KEY,
                    url TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    depth INTEGER NOT NULL,
                    status TEXT NOT NULL, -- PENDING, IN_PROGRESS, CRAWLED, FAILED, SKIPPED
                    http_code INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    crawled_at TIMESTAMP
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_urls_domain ON urls (domain);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_urls_status ON urls (status);")

            # Content Deduplication Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS content_hashes (
                    content_hash TEXT PRIMARY KEY,
                    simhash TEXT NOT NULL,
                    url TEXT NOT NULL,
                    title TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Enhanced Solar Leads Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS solar_leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT,
                    source_url TEXT,
                    post_url TEXT NOT NULL UNIQUE,
                    author TEXT,
                    post_title TEXT,
                    intent_snippet TEXT,
                    lead_score REAL NOT NULL,
                    lead_category TEXT NOT NULL, -- HOT_BUYER, WARM_BUYER, EXISTING_CUSTOMER_EXPANSION, RESEARCHING
                    classification_type TEXT NOT NULL DEFAULT 'RESIDENTIAL_NEW_BUYER',
                    lead_freshness TEXT DEFAULT 'Unknown',
                    published_at TEXT,
                    latest_reply_at TEXT,
                    property_type TEXT,
                    country TEXT DEFAULT 'USA',
                    state TEXT,
                    city TEXT,
                    zip_code TEXT,
                    system_size_kw REAL,
                    electricity_bill REAL,
                    buyer_stage TEXT DEFAULT 'UNKNOWN',
                    matched_keywords TEXT,
                    evidence_snippets TEXT,
                    crawled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Automatic Schema Migration for Existing Databases
            cursor.execute("PRAGMA table_info(urls);")
            url_cols = {col["name"] for col in cursor.fetchall()}
            if "source_id" not in url_cols:
                cursor.execute("ALTER TABLE urls ADD COLUMN source_id TEXT;")
            if "parent_url" not in url_cols:
                cursor.execute("ALTER TABLE urls ADD COLUMN parent_url TEXT;")
            if "page_type" not in url_cols:
                cursor.execute("ALTER TABLE urls ADD COLUMN page_type TEXT;")
            if "discovery_method" not in url_cols:
                cursor.execute("ALTER TABLE urls ADD COLUMN discovery_method TEXT;")
            if "priority" not in url_cols:
                cursor.execute("ALTER TABLE urls ADD COLUMN priority INTEGER DEFAULT 1;")

            cursor.execute("PRAGMA table_info(solar_leads);")
            existing_cols = {col["name"] for col in cursor.fetchall()}
            if "classification_type" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN classification_type TEXT NOT NULL DEFAULT 'RESIDENTIAL_NEW_BUYER';")
            if "primary_classification" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN primary_classification TEXT NOT NULL DEFAULT 'RESIDENTIAL_NEW_BUYER';")
            if "secondary_classifications" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN secondary_classifications TEXT;")
            if "lead_freshness" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN lead_freshness TEXT DEFAULT 'Unknown';")
            if "published_at" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN published_at TEXT;")
            if "latest_reply_at" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN latest_reply_at TEXT;")
            if "property_type" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN property_type TEXT;")
            if "evidence_snippets" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN evidence_snippets TEXT;")
            if "lead_segment" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN lead_segment TEXT DEFAULT 'NEW_BUYER';")
            if "purchase_intent_score" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN purchase_intent_score REAL DEFAULT 0.0;")
            if "residential_confidence" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN residential_confidence REAL DEFAULT 1.0;")
            if "location_confidence" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN location_confidence REAL DEFAULT 0.0;")
            if "freshness_score" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN freshness_score REAL DEFAULT 0.0;")
            if "evidence_confidence" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN evidence_confidence REAL DEFAULT 0.0;")
            if "thread_created_at" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN thread_created_at TEXT;")
            if "latest_thread_activity_at" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN latest_thread_activity_at TEXT;")
            if "last_buyer_intent_at" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN last_buyer_intent_at TEXT;")
            if "thread_freshness" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN thread_freshness TEXT DEFAULT 'Unknown';")
            if "buyer_intent_freshness" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN buyer_intent_freshness TEXT DEFAULT 'Unknown';")
            if "lead_status" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN lead_status TEXT DEFAULT 'NEW';")
            if "qualification_status" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN qualification_status TEXT DEFAULT 'QUALIFIED';")
            if "validation_status" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN validation_status TEXT DEFAULT 'PASSED';")
            if "validation_flags" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN validation_flags TEXT;")
            if "estimated_monthly_usage_kwh" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN estimated_monthly_usage_kwh REAL;")
            if "estimated_annual_usage_kwh" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN estimated_annual_usage_kwh REAL;")
            if "estimated_system_size_kw" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN estimated_system_size_kw REAL;")
            if "estimated_solar_offset_percent" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN estimated_solar_offset_percent REAL;")
            if "assigned_contractor_id" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN assigned_contractor_id TEXT;")
            if "composite_qualification_score" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN composite_qualification_score REAL DEFAULT 0.0;")
            if "hard_disqualified" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN hard_disqualified INTEGER DEFAULT 0;")
            if "disqualification_reason" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN disqualification_reason TEXT;")
            if "needs_human_review" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN needs_human_review INTEGER DEFAULT 0;")
            if "review_reason" not in existing_cols:
                cursor.execute("ALTER TABLE solar_leads ADD COLUMN review_reason TEXT;")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_score ON solar_leads (lead_score);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_category ON solar_leads (lead_category);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_class ON solar_leads (classification_type);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_segment ON solar_leads (lead_segment);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_status ON solar_leads (lead_status);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_leads_qual_score ON solar_leads (composite_qualification_score);")

            # 1. Lead Evidence Ledger Table (Provenance & Auditability)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lead_evidence (
                    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    source_url TEXT NOT NULL,
                    source_author TEXT,
                    evidence_text TEXT NOT NULL,
                    evidence_type TEXT,
                    evidence_polarity TEXT DEFAULT 'POSITIVE',
                    published_at TEXT,
                    extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    extraction_method TEXT DEFAULT 'RULE_REGEX_V1',
                    model_version TEXT DEFAULT '2.0.0',
                    confidence REAL DEFAULT 1.0,
                    score_contribution REAL DEFAULT 0.0,
                    FOREIGN KEY (lead_id) REFERENCES solar_leads(id)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_evidence_lead ON lead_evidence (lead_id);")

            # 2. Intent Events Table (3-Timestamp Evidence Level)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS intent_events (
                    intent_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    evidence_id INTEGER,
                    intent_type TEXT NOT NULL,
                    intent_polarity TEXT DEFAULT 'POSITIVE',
                    intent_published_at TEXT,
                    intent_confidence REAL DEFAULT 1.0,
                    recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (lead_id) REFERENCES solar_leads(id),
                    FOREIGN KEY (evidence_id) REFERENCES lead_evidence(evidence_id)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_events_lead ON intent_events (lead_id);")

            # 3. Human Review Queue Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS review_queue (
                    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    review_reason TEXT NOT NULL,
                    review_priority TEXT DEFAULT 'NORMAL',
                    review_status TEXT DEFAULT 'PENDING',
                    assigned_reviewer TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_at TIMESTAMP,
                    reviewer_notes TEXT,
                    FOREIGN KEY (lead_id) REFERENCES solar_leads(id)
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue (review_status);")

            # 4. Source Compliance & Privacy Policy Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS source_compliance (
                    source_id TEXT PRIMARY KEY,
                    terms_url TEXT,
                    robots_policy_status TEXT DEFAULT 'PERMITTED',
                    api_policy_status TEXT DEFAULT 'COMPLIANT',
                    permitted_collection_method TEXT DEFAULT 'PUBLIC_HTML',
                    commercial_use_status TEXT DEFAULT 'RESTRICTED_REVIEW',
                    reviewed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reviewed_by TEXT DEFAULT 'SYSTEM_AUDITOR'
                );
            """)

            # 5. Privacy Suppression Records & Deletion Requests
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS suppression_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_value TEXT NOT NULL UNIQUE,
                    entity_type TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS deletion_requests (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL UNIQUE,
                    entity_value TEXT NOT NULL,
                    requested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP,
                    status TEXT DEFAULT 'PROCESSED'
                );
            """)

            # Lead Lifecycle History Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS lead_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    lead_id INTEGER NOT NULL,
                    old_status TEXT,
                    new_status TEXT NOT NULL,
                    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    changed_by TEXT DEFAULT 'SYSTEM',
                    reason TEXT
                );
            """)

            # Approved USA Sources Registry Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    us_geo_relevance TEXT,
                    status TEXT NOT NULL,
                    priority TEXT NOT NULL,
                    total_downloaded INTEGER DEFAULT 0,
                    leads_discovered INTEGER DEFAULT 0,
                    quality_score REAL DEFAULT 0.0,
                    latest_post_date TEXT,
                    last_successful_crawl_at TIMESTAMP,
                    new_posts_discovered INTEGER DEFAULT 0,
                    duplicate_posts_count INTEGER DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)

            cursor.execute("PRAGMA table_info(sources);")
            src_cols = {col["name"] for col in cursor.fetchall()}
            if "latest_post_date" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN latest_post_date TEXT;")
            if "last_successful_crawl_at" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN last_successful_crawl_at TIMESTAMP;")
            if "new_posts_discovered" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN new_posts_discovered INTEGER DEFAULT 0;")
            if "duplicate_posts_count" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN duplicate_posts_count INTEGER DEFAULT 0;")
            if "categories_discovered" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN categories_discovered INTEGER DEFAULT 0;")
            if "subcategories_discovered" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN subcategories_discovered INTEGER DEFAULT 0;")
            if "pagination_discovered" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN pagination_discovered INTEGER DEFAULT 0;")
            if "threads_discovered" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN threads_discovered INTEGER DEFAULT 0;")
            if "valid_residential_threads" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN valid_residential_threads INTEGER DEFAULT 0;")
            if "rejected_commercial" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN rejected_commercial INTEGER DEFAULT 0;")
            if "rejected_members" not in src_cols:
                cursor.execute("ALTER TABLE sources ADD COLUMN rejected_members INTEGER DEFAULT 0;")

            # Business Contact Information Table (Conservative)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS business_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_name TEXT,
                    source_url TEXT NOT NULL,
                    company_name TEXT,
                    contact_type TEXT NOT NULL, -- BUSINESS_EMAIL, BUSINESS_PHONE, CONTACT_FORM, VOLUNTARY_PUBLIC_CONTACT
                    contact_value TEXT NOT NULL UNIQUE,
                    author_username TEXT,
                    source_permalink TEXT,
                    evidence_snippet TEXT,
                    verification_status TEXT DEFAULT 'VERIFIED_PUBLIC_BUSINESS',
                    discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("PRAGMA table_info(business_contacts);")
            bc_cols = {col["name"] for col in cursor.fetchall()}
            if "author_username" not in bc_cols:
                cursor.execute("ALTER TABLE business_contacts ADD COLUMN author_username TEXT;")
            if "source_permalink" not in bc_cols:
                cursor.execute("ALTER TABLE business_contacts ADD COLUMN source_permalink TEXT;")

            cursor.execute("CREATE INDEX IF NOT EXISTS idx_contacts_val ON business_contacts (contact_value);")

            # Solar Installers / Places Leads Table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS solar_installers (
                    place_id TEXT PRIMARY KEY,
                    company_name TEXT NOT NULL,
                    formatted_address TEXT,
                    city TEXT,
                    state TEXT,
                    zip_code TEXT,
                    phone_number TEXT,
                    website_url TEXT,
                    google_maps_url TEXT,
                    rating REAL,
                    user_ratings_total INTEGER,
                    business_status TEXT DEFAULT 'OPERATIONAL',
                    primary_type TEXT,
                    metro_area TEXT,
                    search_query TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_installers_state ON solar_installers (state);")
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_installers_city ON solar_installers (city);")
            conn.commit()

        # Seed source registry table
        self._sync_source_registry()

    # --- URL Operations ---

    def is_url_seen(self, url_hash: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM urls WHERE url_hash = ? LIMIT 1;", (url_hash,))
            return cursor.fetchone() is not None

    def get_url_status(self, url_hash: str) -> Optional[Tuple[str, Optional[int]]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, http_code FROM urls WHERE url_hash = ? LIMIT 1;", (url_hash,))
            row = cursor.fetchone()
            return (row["status"], row["http_code"]) if row else None

    def get_pending_urls(self, limit: int = 1000) -> List[Tuple[str, str, int, str]]:
        """Returns list of (url, domain, depth, url_hash) for unfinished URLs."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT url, domain, depth, url_hash 
                FROM urls 
                WHERE status = 'PENDING' 
                ORDER BY depth ASC, created_at ASC 
                LIMIT ?;
            """, (limit,))
            return [(row["url"], row["domain"], row["depth"], row["url_hash"]) for row in cursor.fetchall()]

    def save_new_url(
        self,
        url_hash: str,
        url: str,
        domain: str,
        depth: int,
        source_id: Optional[str] = None,
        parent_url: Optional[str] = None,
        page_type: Optional[str] = None,
        discovery_method: Optional[str] = None,
        priority: int = 1
    ) -> bool:
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO urls (url_hash, url, domain, depth, status, source_id, parent_url, page_type, discovery_method, priority)
                    VALUES (?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?)
                """, (url_hash, url, domain, depth, source_id, parent_url, page_type, discovery_method, priority))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            return False

    def update_url_status(self, url_hash: str, status: str, http_code: Optional[int] = None):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE urls
                SET status = ?, http_code = ?, crawled_at = CURRENT_TIMESTAMP
                WHERE url_hash = ?
            """, (status, http_code, url_hash))
            conn.commit()

    def reset_db(self):
        """Clears all URLs, content hashes, and leads for a fresh crawl run."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM urls;")
            cursor.execute("DELETE FROM content_hashes;")
            cursor.execute("DELETE FROM solar_leads;")
            conn.commit()

    def get_database_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT status, COUNT(*) FROM urls GROUP BY status;")
            url_counts = dict(cursor.fetchall())
            cursor.execute("SELECT COUNT(*) FROM content_hashes;")
            content_hashes = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM solar_leads;")
            leads_count = cursor.fetchone()[0]
            return {
                "urls_by_status": url_counts,
                "total_urls": sum(url_counts.values()),
                "content_hashes": content_hashes,
                "leads_count": leads_count
            }

    # --- Content Deduplication Operations ---

    def is_exact_content_seen(self, content_hash: str) -> bool:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM content_hashes WHERE content_hash = ? LIMIT 1;", (content_hash,))
            return cursor.fetchone() is not None

    def check_near_duplicate_simhash(self, new_simhash: int, max_hamming_distance: int = 3) -> Optional[Tuple[str, int]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT url, simhash FROM content_hashes")
            for row in cursor.fetchall():
                simhash_str = row["simhash"]
                try:
                    existing_simhash = int(simhash_str, 16) if isinstance(simhash_str, str) else int(simhash_str)
                except (ValueError, TypeError):
                    continue
                xor_val = new_simhash ^ existing_simhash
                distance = bin(xor_val).count("1")
                if distance <= max_hamming_distance:
                    return (row["url"], distance)
        return None

    def save_content_hash(self, content_hash: str, simhash: int, url: str, title: Optional[str] = None):
        simhash_hex = f"{simhash:016x}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO content_hashes (content_hash, simhash, url, title)
                VALUES (?, ?, ?, ?)
            """, (content_hash, simhash_hex, url, title))
            conn.commit()

    def save_content_hash_atomic(self, content_hash: str, simhash: int, url: str, title: Optional[str] = None) -> bool:
        """
        Atomically attempts to store content hash.
        Returns True if newly inserted, False if it was already stored (exact duplicate).
        Prevents concurrency race conditions between parser workers.
        """
        simhash_hex = f"{simhash:016x}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO content_hashes (content_hash, simhash, url, title)
                VALUES (?, ?, ?, ?);
            """, (content_hash, simhash_hex, url, title))
            conn.commit()
            return cursor.rowcount > 0

    # --- Solar Leads Operations & JSON Export ---

    def save_solar_lead(self, lead_data: Dict[str, Any]) -> bool:
        """
        Inserts or updates an enriched solar lead with intent classification,
        segmentation (New Buyer vs Expansion vs Service), freshness, quote metrics,
        location hints, and evidence snippets.
        Also exports individual JSON into leads/ directory.
        """
        try:
            # Determine 3-way lead segment
            lead_segment = lead_data.get("lead_segment")
            if not lead_segment:
                class_type = lead_data.get("classification_type", "")
                if class_type in ("RESIDENTIAL_NEW_BUYER", "RESIDENTIAL_QUOTE_SEEKER"):
                    lead_segment = "NEW_BUYER"
                elif class_type == "RESIDENTIAL_UPGRADE":
                    lead_segment = "EXISTING_CUSTOMER"
                elif class_type == "RESIDENTIAL_REPAIR":
                    lead_segment = "SERVICE_CUSTOMER"
                else:
                    lead_segment = "NON_BUYER"
            lead_data["lead_segment"] = lead_segment

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO solar_leads (
                        source_name, source_url, post_url, author, post_title,
                        intent_snippet, lead_score, lead_category, lead_segment, classification_type,
                        primary_classification, secondary_classifications,
                        lead_freshness, published_at, latest_reply_at,
                        thread_created_at, latest_thread_activity_at, last_buyer_intent_at,
                        thread_freshness, buyer_intent_freshness,
                        purchase_intent_score, residential_confidence, location_confidence,
                        freshness_score, evidence_confidence,
                        lead_status, qualification_status, validation_status, validation_flags,
                        property_type, country, state, city, zip_code,
                        system_size_kw, electricity_bill,
                        estimated_monthly_usage_kwh, estimated_annual_usage_kwh,
                        estimated_system_size_kw, estimated_solar_offset_percent,
                        assigned_contractor_id,
                        composite_qualification_score, hard_disqualified, disqualification_reason,
                        needs_human_review, review_reason,
                        buyer_stage, matched_keywords, evidence_snippets
                    ) VALUES (
                        ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?, ?,
                        ?, ?, ?, ?, ?,
                        ?, ?,
                        ?, ?,
                        ?, ?,
                        ?,
                        ?, ?, ?,
                        ?, ?,
                        ?, ?, ?
                    )
                    ON CONFLICT(post_url) DO UPDATE SET
                        lead_score = excluded.lead_score,
                        lead_category = excluded.lead_category,
                        lead_segment = excluded.lead_segment,
                        classification_type = excluded.classification_type,
                        primary_classification = excluded.primary_classification,
                        secondary_classifications = excluded.secondary_classifications,
                        lead_freshness = excluded.lead_freshness,
                        published_at = excluded.published_at,
                        latest_reply_at = excluded.latest_reply_at,
                        thread_created_at = excluded.thread_created_at,
                        latest_thread_activity_at = excluded.latest_thread_activity_at,
                        last_buyer_intent_at = excluded.last_buyer_intent_at,
                        thread_freshness = excluded.thread_freshness,
                        buyer_intent_freshness = excluded.buyer_intent_freshness,
                        purchase_intent_score = excluded.purchase_intent_score,
                        residential_confidence = excluded.residential_confidence,
                        location_confidence = excluded.location_confidence,
                        freshness_score = excluded.freshness_score,
                        evidence_confidence = excluded.evidence_confidence,
                        composite_qualification_score = excluded.composite_qualification_score,
                        hard_disqualified = excluded.hard_disqualified,
                        disqualification_reason = excluded.disqualification_reason,
                        needs_human_review = excluded.needs_human_review,
                        review_reason = excluded.review_reason,
                        lead_status = excluded.lead_status,
                        qualification_status = excluded.qualification_status,
                        validation_status = excluded.validation_status,
                        validation_flags = excluded.validation_flags,
                        property_type = excluded.property_type,
                        buyer_stage = excluded.buyer_stage,
                        intent_snippet = excluded.intent_snippet,
                        evidence_snippets = excluded.evidence_snippets,
                        system_size_kw = excluded.system_size_kw,
                        electricity_bill = excluded.electricity_bill,
                        estimated_monthly_usage_kwh = excluded.estimated_monthly_usage_kwh,
                        estimated_annual_usage_kwh = excluded.estimated_annual_usage_kwh,
                        estimated_system_size_kw = excluded.estimated_system_size_kw,
                        estimated_solar_offset_percent = excluded.estimated_solar_offset_percent,
                        assigned_contractor_id = COALESCE(excluded.assigned_contractor_id, solar_leads.assigned_contractor_id)
                """, (
                    lead_data.get("source_name", "Web Discussion"),
                    lead_data.get("source_url", ""),
                    lead_data["post_url"],
                    lead_data.get("author", "Anonymous"),
                    lead_data.get("post_title", ""),
                    lead_data.get("intent_snippet", "")[:1500],
                    lead_data["lead_score"],
                    lead_data["lead_category"],
                    lead_segment,
                    lead_data.get("classification_type", "RESIDENTIAL_NEW_BUYER"),
                    lead_data.get("primary_classification") or lead_data.get("classification_type", "RESIDENTIAL_NEW_BUYER"),
                    json.dumps(lead_data.get("secondary_classifications", [])),
                    lead_data.get("lead_freshness", "Unknown"),
                    lead_data.get("published_at"),
                    lead_data.get("latest_reply_at"),
                    lead_data.get("thread_created_at") or lead_data.get("published_at"),
                    lead_data.get("latest_thread_activity_at") or lead_data.get("latest_reply_at"),
                    lead_data.get("last_buyer_intent_at") or lead_data.get("published_at"),
                    lead_data.get("thread_freshness") or lead_data.get("lead_freshness", "Unknown"),
                    lead_data.get("buyer_intent_freshness") or lead_data.get("lead_freshness", "Unknown"),
                    lead_data.get("purchase_intent_score", 0.0),
                    lead_data.get("residential_confidence", 1.0),
                    lead_data.get("location_confidence", 0.0),
                    lead_data.get("freshness_score", 0.0),
                    lead_data.get("evidence_confidence", 0.0),
                    lead_data.get("lead_status", "NEW"),
                    lead_data.get("qualification_status", "QUALIFIED"),
                    lead_data.get("validation_status", "PASSED"),
                    json.dumps(lead_data.get("validation_flags", [])),
                    lead_data.get("property_type"),
                    lead_data.get("country", "USA"),
                    lead_data.get("state"),
                    lead_data.get("city"),
                    lead_data.get("zip_code"),
                    lead_data.get("system_size_kw"),
                    lead_data.get("electricity_bill"),
                    lead_data.get("estimated_monthly_usage_kwh"),
                    lead_data.get("estimated_annual_usage_kwh"),
                    lead_data.get("estimated_system_size_kw"),
                    lead_data.get("estimated_solar_offset_percent"),
                    lead_data.get("assigned_contractor_id"),
                    lead_data.get("composite_qualification_score", 0.0),
                    1 if lead_data.get("hard_disqualified") else 0,
                    lead_data.get("disqualification_reason"),
                    1 if lead_data.get("needs_human_review") else 0,
                    lead_data.get("review_reason"),
                    lead_data.get("buyer_stage", "UNKNOWN"),
                    json.dumps(lead_data.get("matched_keywords", [])),
                    json.dumps(lead_data.get("evidence_snippets", []))
                ))
                lead_id = cursor.lastrowid
                if not lead_id or lead_id == 0:
                    cursor.execute("SELECT id FROM solar_leads WHERE post_url = ?;", (lead_data["post_url"],))
                    row = cursor.fetchone()
                    lead_id = row["id"] if row else 1

                # Data Provenance: Persist structured evidence items into lead_evidence ledger
                structured_evidence = lead_data.get("structured_evidence", [])
                for ev in structured_evidence:
                    cursor.execute("""
                        INSERT INTO lead_evidence (
                            lead_id, source_url, source_author, evidence_text,
                            evidence_type, evidence_polarity, published_at,
                            extraction_method, model_version, confidence, score_contribution
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        lead_id,
                        lead_data.get("post_url", ""),
                        lead_data.get("author", "Anonymous"),
                        ev.get("evidence_text", ""),
                        ev.get("evidence_type", "PURCHASE_INTENT"),
                        ev.get("evidence_polarity", "POSITIVE"),
                        ev.get("published_at") or lead_data.get("published_at"),
                        ev.get("extraction_method", "RULE_REGEX_V1"),
                        ev.get("model_version", "2.0.0"),
                        ev.get("confidence", 1.0),
                        ev.get("score_contribution", 0.0)
                    ))
                    ev_id = cursor.lastrowid
                    # Record granular intent event
                    cursor.execute("""
                        INSERT INTO intent_events (
                            lead_id, evidence_id, intent_type, intent_polarity,
                            intent_published_at, intent_confidence
                        ) VALUES (?, ?, ?, ?, ?, ?);
                    """, (
                        lead_id,
                        ev_id,
                        ev.get("evidence_type", "PURCHASE_INTENT"),
                        ev.get("evidence_polarity", "POSITIVE"),
                        ev.get("published_at") or lead_data.get("published_at"),
                        ev.get("confidence", 1.0)
                    ))

                # Human-In-The-Loop: Route to review_queue if flagged
                if lead_data.get("needs_human_review"):
                    cursor.execute("""
                        INSERT INTO review_queue (lead_id, review_reason, review_priority)
                        VALUES (?, ?, ?);
                    """, (
                        lead_id,
                        lead_data.get("review_reason", "Low confidence or ambiguous intent"),
                        "HIGH" if lead_data.get("composite_qualification_score", 0) > 70 else "NORMAL"
                    ))

                conn.commit()

                # Export single JSON file to leads/ folder
                self._export_lead_to_file(lead_id or 1, lead_data)
                return True
        except Exception as e:
            logger.error(f"Failed to save solar lead: {e}", exc_info=True)
            return False

    def _export_lead_to_file(self, lead_id: int, lead_data: Dict[str, Any]):
        try:
            timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"lead_{lead_id}_{timestamp_str}.json"
            filepath = LEADS_DIR / filename
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(lead_data, f, indent=2, default=str)
        except Exception as e:
            logger.warning(f"Could not export lead JSON file: {e}")

    def export_all_leads_summary(self) -> Path:
        """Exports a consolidated leads summary file into leads/ folder."""
        leads = self.get_recent_leads(limit=500)
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_file = LEADS_DIR / f"leads_export_{timestamp_str}.json"
        with open(export_file, "w", encoding="utf-8") as f:
            json.dump(leads, f, indent=2, default=str)
        return export_file

    def get_leads_count(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM solar_leads;")
            return cursor.fetchone()[0]

    def get_recent_leads(self, limit: int = 10, category: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if category:
                cursor.execute("""
                    SELECT * FROM solar_leads 
                    WHERE lead_category = ? 
                    ORDER BY lead_score DESC, id DESC LIMIT ?;
                """, (category, limit))
            else:
                cursor.execute("SELECT * FROM solar_leads ORDER BY lead_score DESC, id DESC LIMIT ?;", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_quality_metrics(self) -> Dict[str, Any]:
        """Calculates lead distribution across segments, classification types, stages, and freshness."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT classification_type, COUNT(*) FROM solar_leads GROUP BY classification_type;")
            by_class = dict(cursor.fetchall())
            cursor.execute("SELECT lead_freshness, COUNT(*) FROM solar_leads GROUP BY lead_freshness;")
            by_freshness = dict(cursor.fetchall())
            cursor.execute("SELECT lead_category, COUNT(*) FROM solar_leads GROUP BY lead_category;")
            by_category = dict(cursor.fetchall())
            cursor.execute("SELECT buyer_stage, COUNT(*) FROM solar_leads GROUP BY buyer_stage;")
            by_stage = dict(cursor.fetchall())
            cursor.execute("SELECT lead_segment, COUNT(*) FROM solar_leads GROUP BY lead_segment;")
            by_segment = dict(cursor.fetchall())
            cursor.execute("SELECT COUNT(*) FROM business_contacts;")
            contacts_count = cursor.fetchone()[0]

            new_buyers = by_segment.get("NEW_BUYER", 0) or (by_class.get("RESIDENTIAL_NEW_BUYER", 0) + by_class.get("RESIDENTIAL_QUOTE_SEEKER", 0))
            expansion = by_segment.get("EXISTING_CUSTOMER", 0) or by_class.get("RESIDENTIAL_UPGRADE", 0)
            repairs = by_segment.get("SERVICE_CUSTOMER", 0) or by_class.get("RESIDENTIAL_REPAIR", 0)

            cursor.execute("SELECT lead_status, COUNT(*) FROM solar_leads GROUP BY lead_status;")
            by_lifecycle = dict(cursor.fetchall())
            cursor.execute("SELECT validation_status, COUNT(*) FROM solar_leads GROUP BY validation_status;")
            by_validation = dict(cursor.fetchall())

            return {
                "by_classification": by_class,
                "by_freshness": by_freshness,
                "by_category": by_category,
                "by_stage": by_stage,
                "by_segment": by_segment,
                "by_lifecycle": by_lifecycle,
                "by_validation": by_validation,
                "new_buyers_count": new_buyers,
                "expansion_count": expansion,
                "repair_count": repairs,
                "business_contacts_count": contacts_count
            }

    def update_lead_status(self, lead_id: int, new_status: str, reason: str = "", changed_by: str = "SYSTEM") -> bool:
        """Transitions a lead's CRM lifecycle status and records an audit log entry."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT lead_status FROM solar_leads WHERE id = ?;", (lead_id,))
            row = cursor.fetchone()
            if not row:
                return False
            old_status = row["lead_status"]
            cursor.execute("UPDATE solar_leads SET lead_status = ? WHERE id = ?;", (new_status, lead_id))
            cursor.execute("""
                INSERT INTO lead_history (lead_id, old_status, new_status, changed_by, reason)
                VALUES (?, ?, ?, ?, ?);
            """, (lead_id, old_status, new_status, changed_by, reason))
            conn.commit()
            return True

    def assign_lead_contractor(self, lead_id: int, contractor_id: str, new_status: str = "ASSIGNED", reason: str = "", changed_by: str = "CONTRACTOR_MATCHER") -> bool:
        """Assigns a verified contractor to a lead and records CRM audit log."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT lead_status FROM solar_leads WHERE id = ?;", (lead_id,))
            row = cursor.fetchone()
            if not row:
                return False
            old_status = row["lead_status"]
            cursor.execute("UPDATE solar_leads SET assigned_contractor_id = ?, lead_status = ? WHERE id = ?;", (contractor_id, new_status, lead_id))
            cursor.execute("""
                INSERT INTO lead_history (lead_id, old_status, new_status, changed_by, reason)
                VALUES (?, ?, ?, ?, ?);
            """, (lead_id, old_status, new_status, changed_by, reason))
            conn.commit()
            return True

    def get_lead_history(self, lead_id: int) -> List[Dict[str, Any]]:
        """Returns the full audit and status change history for a lead."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM lead_history WHERE lead_id = ? ORDER BY changed_at ASC;
            """, (lead_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_lead_evidence(self, lead_id: int) -> List[Dict[str, Any]]:
        """Returns all granular evidence ledger records for a lead."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM lead_evidence WHERE lead_id = ? ORDER BY evidence_id ASC;
            """, (lead_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_intent_events(self, lead_id: int) -> List[Dict[str, Any]]:
        """Returns time-series intent events for a lead."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM intent_events WHERE lead_id = ? ORDER BY intent_event_id ASC;
            """, (lead_id,))
            return [dict(row) for row in cursor.fetchall()]

    def get_review_queue(self, status: str = "PENDING", limit: int = 50) -> List[Dict[str, Any]]:
        """Returns leads awaiting human review."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT r.*, l.post_title, l.post_url, l.lead_score, l.composite_qualification_score, l.state
                FROM review_queue r
                JOIN solar_leads l ON r.lead_id = l.id
                WHERE r.review_status = ?
                ORDER BY r.created_at ASC LIMIT ?;
            """, (status, limit))
            return [dict(row) for row in cursor.fetchall()]

    def process_review_decision(self, review_id: int, decision: str, notes: str = "", reviewer: str = "HUMAN_OPERATOR") -> bool:
        """Processes a reviewer's decision (APPROVED, REJECTED, MODIFIED)."""
        now = datetime.now(timezone.utc)
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT lead_id FROM review_queue WHERE review_id = ?;", (review_id,))
            row = cursor.fetchone()
            if not row:
                return False
            lead_id = row["lead_id"]

            cursor.execute("""
                UPDATE review_queue 
                SET review_status = ?, reviewed_at = ?, assigned_reviewer = ?, reviewer_notes = ?
                WHERE review_id = ?;
            """, (decision, now, reviewer, notes, review_id))

            new_status = "QUALIFIED" if decision == "APPROVED" else "DISQUALIFIED"
            cursor.execute("""
                UPDATE solar_leads 
                SET qualification_status = ?, needs_human_review = 0, lead_status = ?
                WHERE id = ?;
            """, (new_status, new_status, lead_id))

            cursor.execute("""
                INSERT INTO lead_history (lead_id, old_status, new_status, changed_by, reason)
                VALUES (?, 'PENDING_REVIEW', ?, ?, ?);
            """, (lead_id, new_status, reviewer, f"Human review decision: {decision}. Notes: {notes}"))

            conn.commit()
            return True

    def is_suppressed(self, entity_value: str) -> bool:
        """Checks if a user, URL, or domain is in privacy suppression registry."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM suppression_records WHERE entity_value = ? LIMIT 1;", (entity_value,))
            return cursor.fetchone() is not None

    def add_suppression(self, entity_value: str, entity_type: str, reason: str) -> bool:
        """Adds an entity to the suppression registry (TCPA/CAN-SPAM/opt-out compliance)."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO suppression_records (entity_value, entity_type, reason)
                VALUES (?, ?, ?);
            """, (entity_value, entity_type, reason))
            conn.commit()
            return cursor.rowcount > 0

    def process_deletion_request(self, entity_value: str) -> int:
        """Purges an entity's records in compliance with data privacy regulations (CCPA/CPRA)."""
        deleted_count = 0
        now = datetime.now(timezone.utc)
        req_id = f"DEL_{now.strftime('%Y%m%d%H%M%S')}_{abs(hash(entity_value)) % 10000}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM solar_leads WHERE author = ? OR post_url = ?;", (entity_value, entity_value))
            deleted_count += cursor.rowcount
            cursor.execute("DELETE FROM business_contacts WHERE author_username = ? OR contact_value = ?;", (entity_value, entity_value))
            deleted_count += cursor.rowcount
            cursor.execute("""
                INSERT INTO deletion_requests (request_id, entity_value, processed_at, status)
                VALUES (?, ?, ?, 'COMPLETED');
            """, (req_id, entity_value, now))
            conn.commit()
        return deleted_count

    # --- Source Registry & Business Contact Operations ---

    def _sync_source_registry(self):
        try:
            from source_registry import USA_SOURCE_REGISTRY
            with self._get_connection() as conn:
                cursor = conn.cursor()
                for s in USA_SOURCE_REGISTRY.values():
                    cursor.execute("""
                        INSERT INTO sources (source_id, name, domain, source_type, us_geo_relevance, status, priority)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(source_id) DO UPDATE SET
                            name = excluded.name,
                            domain = excluded.domain,
                            source_type = excluded.source_type,
                            us_geo_relevance = excluded.us_geo_relevance,
                            status = excluded.status,
                            priority = excluded.priority;
                    """, (s.source_id, s.name, s.domain, s.source_type, s.us_geo_relevance, s.status, s.priority))
                conn.commit()
        except Exception as e:
            logger.debug(f"Could not sync source registry: {e}")

    def save_business_contact(self, contact_data: Dict[str, Any]) -> bool:
        """Saves a verified public contact record, ignoring duplicates."""
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO business_contacts (
                        source_name, source_url, company_name, contact_type,
                        contact_value, author_username, source_permalink, evidence_snippet, verification_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(contact_value) DO NOTHING;
                """, (
                    contact_data.get("source_name", "Web Source"),
                    contact_data["source_url"],
                    contact_data.get("company_name"),
                    contact_data["contact_type"],
                    contact_data["contact_value"],
                    contact_data.get("author_username"),
                    contact_data.get("source_permalink") or contact_data["source_url"],
                    contact_data.get("evidence_snippet"),
                    contact_data.get("verification_status", "VERIFIED_PUBLIC_BUSINESS")
                ))
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save business contact: {e}")
            return False

    def get_business_contacts(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM business_contacts ORDER BY id DESC LIMIT ?;", (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def export_business_contacts_summary(self) -> Path:
        """Exports verified business contacts into leads/ folder."""
        contacts = self.get_business_contacts(limit=500)
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        export_file = LEADS_DIR / f"business_contacts_{timestamp_str}.json"
        with open(export_file, "w", encoding="utf-8") as f:
            json.dump(contacts, f, indent=2, default=str)
        return export_file

    def update_source_metrics(
        self,
        domain: str,
        pages_downloaded: int = 1,
        leads_found: int = 0,
        posts_discovered: int = 0,
        duplicates: int = 0,
        latest_post_date: Optional[str] = None
    ):
        try:
            domain_clean = domain.lower().replace("www.", "")
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE sources 
                    SET total_downloaded = total_downloaded + ?,
                        leads_discovered = leads_discovered + ?,
                        new_posts_discovered = new_posts_discovered + ?,
                        duplicate_posts_count = duplicate_posts_count + ?,
                        latest_post_date = COALESCE(?, latest_post_date),
                        last_successful_crawl_at = CURRENT_TIMESTAMP,
                        quality_score = CASE 
                            WHEN (total_downloaded + ?) > 0 
                            THEN ROUND(CAST(leads_discovered + ? AS REAL) * 100.0 / (total_downloaded + ?), 1)
                            ELSE 0.0 END,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE domain LIKE ?;
                """, (
                    pages_downloaded,
                    leads_found,
                    posts_discovered,
                    duplicates,
                    latest_post_date,
                    pages_downloaded,
                    leads_found,
                    pages_downloaded,
                    f"%{domain_clean}%"
                ))
                conn.commit()
        except Exception as e:
            logger.debug(f"Could not update source metrics for {domain}: {e}")

    def get_source_report(self) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM sources ORDER BY priority ASC, total_downloaded DESC;")
            return [dict(row) for row in cursor.fetchall()]

    # --- Solar Installers (Google Places API) Operations ---

    def save_solar_installer(self, installer_data: Dict[str, Any]) -> bool:
        """Saves or updates a verified solar contractor from Google Places API."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO solar_installers (
                    place_id, company_name, formatted_address, city, state, zip_code,
                    phone_number, website_url, google_maps_url, rating, user_ratings_total,
                    business_status, primary_type, metro_area, search_query
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(place_id) DO UPDATE SET
                    company_name = excluded.company_name,
                    formatted_address = excluded.formatted_address,
                    city = COALESCE(excluded.city, solar_installers.city),
                    state = COALESCE(excluded.state, solar_installers.state),
                    zip_code = COALESCE(excluded.zip_code, solar_installers.zip_code),
                    phone_number = COALESCE(excluded.phone_number, solar_installers.phone_number),
                    website_url = COALESCE(excluded.website_url, solar_installers.website_url),
                    google_maps_url = COALESCE(excluded.google_maps_url, solar_installers.google_maps_url),
                    rating = excluded.rating,
                    user_ratings_total = excluded.user_ratings_total;
            """, (
                installer_data.get("place_id"),
                installer_data.get("company_name"),
                installer_data.get("formatted_address"),
                installer_data.get("city"),
                installer_data.get("state"),
                installer_data.get("zip_code"),
                installer_data.get("phone_number"),
                installer_data.get("website_url"),
                installer_data.get("google_maps_url"),
                installer_data.get("rating"),
                installer_data.get("user_ratings_total"),
                installer_data.get("business_status", "OPERATIONAL"),
                installer_data.get("primary_type"),
                installer_data.get("metro_area"),
                installer_data.get("search_query")
            ))
            conn.commit()
            return cursor.rowcount > 0

    def get_solar_installers(self, limit: int = 100, state: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            if state:
                cursor.execute("""
                    SELECT * FROM solar_installers WHERE state = ? ORDER BY rating DESC, user_ratings_total DESC LIMIT ?;
                """, (state.upper(), limit))
            else:
                cursor.execute("""
                    SELECT * FROM solar_installers ORDER BY rating DESC, user_ratings_total DESC LIMIT ?;
                """, (limit,))
            return [dict(row) for row in cursor.fetchall()]

    def get_installer_stats(self) -> Dict[str, Any]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM solar_installers;")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT state, COUNT(*) FROM solar_installers GROUP BY state ORDER BY COUNT(*) DESC;")
            by_state = {row[0] or "Unknown": row[1] for row in cursor.fetchall()}
            cursor.execute("SELECT COUNT(DISTINCT phone_number) FROM solar_installers WHERE phone_number IS NOT NULL AND phone_number != '';")
            with_phones = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(DISTINCT website_url) FROM solar_installers WHERE website_url IS NOT NULL AND website_url != '';")
            with_websites = cursor.fetchone()[0]
            return {
                "total_installers": total,
                "by_state": by_state,
                "with_phones": with_phones,
                "with_websites": with_websites
            }

    def export_installers_to_json(self) -> Optional[Path]:
        installers = self.get_solar_installers(limit=1000)
        if not installers:
            return None
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_file = LEADS_DIR / f"places_contractors_{timestamp}.json"
        with open(export_file, "w", encoding="utf-8") as f:
            json.dump(installers, f, indent=2, default=str)
        return export_file
