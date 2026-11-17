"""
DIY Solar Forum Source Adapter Module
Implements source-aware recursive web discovery and lead extraction for
DIY Solar Forum (XenForo forum).
Handles What's New feeds, residential grid-assist & battery discussions,
hard-blocks off-grid vehicles/RVs, and bounds XenForo pagination strictly to pages 1-3.
"""

import re
from typing import Dict, List, Tuple, Optional, Any
from urllib.parse import urlparse, urljoin, parse_qs
from bs4 import BeautifulSoup
import dateparser

from sources.base_adapter import BaseSourceAdapter


class DIYSolarForumAdapter(BaseSourceAdapter):
    SOURCE_ID = "diysolarforum"
    NAME = "DIY Solar Forum"
    DOMAIN = "diysolarforum.com"

    # Whitelisted categories and feeds
    ALLOWED_CATEGORIES = {
        "whats-new",
        "new-to-solar-start-here",
        "general-solar-discussion",
        "grid-tie-solar-grid-assist",
        "diy-solar-general-discussion",
        "solar-panels",
        "inverters",
        "batteries-lifepo4",
        "upgrading-an-existing-system"
    }

    # Hard-blocked vehicle/RV categories and admin paths
    DISQUALIFIED_PATTERNS = [
        "vehicle-mounted",
        "rv-solar",
        "van-solar",
        "boat-solar",
        "camper",
        "members/",
        "account/",
        "login",
        "register",
        "attachments/",
        "misc/",
        "help/",
        "search/",
        "lost-password",
        "two-step",
        "privacy",
        "terms"
    ]

    # Regex for XenForo thread slug: e.g. /threads/my-solar-plan.12345/
    THREAD_REGEX = re.compile(r"/threads/[a-z0-9-%]+\.(\d+)/?", re.IGNORECASE)

    # Regex for XenForo pagination: /page-2, /page-3
    PAGE_REGEX = re.compile(r"/page-([0-9]+)\b", re.IGNORECASE)

    def __init__(self):
        super().__init__(self.SOURCE_ID, self.NAME, self.DOMAIN)

    def is_valid_url(self, url: str) -> Tuple[bool, str]:
        """
        URL-level validation executed BEFORE enqueueing into the URL Frontier.
        Checks for allowed domains, XenForo path patterns, and bounds pagination.
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        if domain != self.domain:
            return False, f"Domain '{domain}' does not match DIY Solar Forum domain '{self.domain}'"

        path_lower = parsed.path.lower()
        full_lower = url.lower()

        # Hard-block disqualified patterns (vehicles, admin, attachments)
        for pattern in self.DISQUALIFIED_PATTERNS:
            if pattern in path_lower or pattern in full_lower:
                return False, f"DIYSolarForum: matches disqualified pattern '{pattern}'"

        # Check pagination freshness limit (> page 3)
        page_match = self.PAGE_REGEX.search(path_lower)
        if page_match:
            page_num = int(page_match.group(1))
            if page_num > 3:
                return False, f"DIYSolarForum: page-{page_num} exceeds max pagination limit 3"

        qs = parse_qs(parsed.query)
        if "page" in qs:
            try:
                page_num = int(qs["page"][0])
                if page_num > 3:
                    return False, f"DIYSolarForum: query page {page_num} exceeds max pagination limit 3"
            except (ValueError, IndexError):
                pass

        # Allow What's New feeds
        if "/whats-new" in path_lower:
            return True, "OK"

        # Check forums or threads path
        if not (path_lower.startswith("/forums") or path_lower.startswith("/threads")):
            return False, "DIYSolarForum: outside /forums/ or /threads/ hierarchy"

        # If forum category, check allowlist
        if path_lower.startswith("/forums"):
            is_relevant = any(cat in path_lower for cat in self.ALLOWED_CATEGORIES)
            if not is_relevant:
                return False, "DIYSolarForum: non-residential category"

        return True, "OK"

    def classify_url_type(self, url: str) -> str:
        """
        Classifies the URL into functional role:
        'CATEGORY', 'SUBCATEGORY', 'THREAD', 'THREAD_PAGINATION', 'CATEGORY_PAGINATION', 'IGNORED'
        """
        valid, _ = self.is_valid_url(url)
        if not valid:
            return "IGNORED"

        parsed = urlparse(url)
        path = parsed.path.rstrip("/").lower()

        is_thread = bool(self.THREAD_REGEX.search(path))
        is_paged = bool(self.PAGE_REGEX.search(path))

        if is_thread:
            if is_paged:
                return "THREAD_PAGINATION"
            return "THREAD"

        if is_paged:
            return "CATEGORY_PAGINATION"

        if "/whats-new" in path:
            return "CATEGORY"

        if path.startswith("/forums"):
            return "CATEGORY"

        return "IGNORED"

    def discover_category_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extracts valid category and What's New feed URLs."""
        discovered = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(current_url, href)
            clean_url = full_url.split("#")[0]
            url_type = self.classify_url_type(clean_url)
            if url_type in ("CATEGORY", "CATEGORY_PAGINATION"):
                discovered.add(clean_url)
        return sorted(list(discovered))

    def discover_thread_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extracts individual discussion thread URLs."""
        discovered = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(current_url, href)
            clean_url = full_url.split("#")[0]
            # Strip trailing /unread, /latest
            clean_url = re.sub(r'/(unread|latest)/?$', '/', clean_url)
            url_type = self.classify_url_type(clean_url)
            if url_type == "THREAD":
                discovered.add(clean_url)
        return sorted(list(discovered))

    def discover_pagination_links(self, soup: BeautifulSoup, current_url: str, max_page: int = 3) -> List[str]:
        """Extracts next page links for threads or categories up to max_page limit."""
        discovered = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(current_url, href)
            clean_url = full_url.split("#")[0]
            clean_url = re.sub(r'/(unread|latest)/?$', '/', clean_url)
            url_type = self.classify_url_type(clean_url)
            if url_type in ("THREAD_PAGINATION", "CATEGORY_PAGINATION"):
                page_match = self.PAGE_REGEX.search(clean_url)
                if page_match:
                    page_num = int(page_match.group(1))
                    if 1 <= page_num <= max_page:
                        discovered.add(clean_url)
        return sorted(list(discovered))

    def extract_thread_data(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """
        Extracts structured XenForo thread discussion data:
        - title: Thread title
        - author: Original poster handle
        - published_at: OP publication date (YYYY-MM-DD)
        - latest_reply_at: Most recent reply date in thread (YYYY-MM-DD)
        - main_content: Text of original post
        - replies: List of reply text snippets
        - location_raw: User location if provided
        """
        title = ""
        title_tag = soup.find("h1", class_="p-title-value") or soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" | DIY Solar Power Forum")[0].strip()

        messages = soup.find_all("article", class_=re.compile(r"message--post\b"))
        author = "Anonymous"
        published_at = None
        latest_reply_at = None
        main_content = ""
        replies = []
        location_raw = None

        if messages:
            first_msg = messages[0]

            # Author extraction
            author_tag = first_msg.find("a", class_="username") or first_msg.find(class_=re.compile(r"message-name"))
            if author_tag:
                author = author_tag.get_text(strip=True)

            # Location from user extras
            loc_tag = first_msg.find("dl", class_=re.compile(r"pairs--userLocation")) or first_msg.find(class_=re.compile(r"user-location"))
            if loc_tag:
                dd = loc_tag.find("dd")
                if dd:
                    location_raw = dd.get_text(strip=True)

            # Timestamp extraction
            time_tag = first_msg.find("time")
            if time_tag:
                dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
                dt = dateparser.parse(dt_str)
                if dt:
                    published_at = dt.strftime("%Y-%m-%d")

            # Main content
            content_tag = first_msg.find("div", class_="bbWrapper")
            if content_tag:
                main_content = content_tag.get_text(separator=" ", strip=True)

            # Replies
            for msg in messages[1:]:
                c_tag = msg.find("div", class_="bbWrapper")
                if c_tag:
                    rep_text = c_tag.get_text(separator=" ", strip=True)
                    if rep_text:
                        replies.append(rep_text)

                t_tag = msg.find("time")
                if t_tag:
                    dt_str = t_tag.get("datetime") or t_tag.get_text(strip=True)
                    dt = dateparser.parse(dt_str)
                    if dt:
                        latest_reply_at = dt.strftime("%Y-%m-%d")

        # Fallback to meta tags if published_at is not found
        if not published_at:
            meta_pub = soup.find("meta", attrs={"property": "article:published_time"})
            if meta_pub and meta_pub.get("content"):
                dt = dateparser.parse(meta_pub["content"])
                if dt:
                    published_at = dt.strftime("%Y-%m-%d")

        return {
            "title": title,
            "author": author,
            "published_at": published_at,
            "latest_reply_at": latest_reply_at,
            "main_content": main_content,
            "replies": replies,
            "location_raw": location_raw
        }

    def discover_related_threads(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Discovers related threads from XenForo similar threads blocks."""
        discovered = set()
        clean_current = current_url.split("?")[0].split("#")[0].rstrip("/")
        for container in soup.find_all(class_=re.compile(r"(block--similarThreads|similar-threads|structItem--thread)")):
            for a in container.find_all("a", href=True):
                clean_url = urljoin(current_url, a["href"]).split("#")[0].split("?")[0]
                clean_url = re.sub(r'/(unread|latest)/?$', '/', clean_url)
                if self.classify_url_type(clean_url) == "THREAD" and clean_url.rstrip("/") != clean_current:
                    discovered.add(clean_url)
        for thread_url in self.discover_thread_links(soup, current_url):
            if thread_url.rstrip("/") != clean_current:
                discovered.add(thread_url)
        return sorted(list(discovered))

    def extract_dates(self, soup: BeautifulSoup) -> Dict[str, Optional[str]]:
        data = self.extract_thread_data(soup, "")
        return {
            "thread_created_at": data.get("published_at"),
            "latest_thread_activity_at": data.get("latest_reply_at"),
            "last_buyer_intent_at": data.get("published_at")
        }


