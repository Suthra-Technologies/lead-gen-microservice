"""
SolarPanelTalk Source Adapter Module
Implements source-aware recursive web discovery and lead extraction for
SolarPanelTalk (vBulletin 5 forum).
Enforces strict residential category whitelisting, hard-blocks commercial
lighting and utility-scale categories, and separates thread creation date from reply dates.
"""

import re
from typing import Dict, List, Tuple, Optional, Any
from urllib.parse import urlparse, urljoin, parse_qs
from bs4 import BeautifulSoup
import dateparser

from sources.base_adapter import BaseSourceAdapter


class SolarPanelTalkAdapter(BaseSourceAdapter):
    SOURCE_ID = "solarpaneltalk"
    NAME = "SolarPanelTalk"
    DOMAIN = "solarpaneltalk.com"

    # Whitelisted residential category slugs
    ALLOWED_CATEGORIES = {
        "solar-quotes-pricing",
        "solar-panels-for-home",
        "solar-panel-installation",
        "solar-panel-system-equipment",
        "grid-tied-solar-electric",
        "how-much-do-solar-panels-cost",
        "batteries-energy-storage"
    }

    # Hard-blocked category keywords and administrative paths
    DISQUALIFIED_PATTERNS = [
        "commercial-solar",
        "solar-power-lighting",
        "solar-lighting",
        "solar-street-light",
        "industrial-solar",
        "solar-water-pumps",
        "solar-thermal",
        "solar-pool-heating",
        "wholesale-solar",
        "retail-cabinet",
        "warehouse",
        "member/",
        "members/",
        "user/",
        "users/",
        "profile/",
        "searchjson",
        "attachment",
        "filedata",
        "announcement",
        "calendar",
        "help",
        "rules",
        "register",
        "login",
        "logout",
        "sendmessage"
    ]

    # Regex for thread slugs containing numeric IDs: e.g. /443616-looking-for-advice-pv-installation
    THREAD_REGEX = re.compile(r"/(\d{4,8})-[a-z0-9-]+", re.IGNORECASE)

    # Regex for pagination: e.g. /page2, /page3 or page=2, page=3
    PAGE_REGEX = re.compile(r"/page([0-9]+)\b", re.IGNORECASE)

    def __init__(self):
        super().__init__(self.SOURCE_ID, self.NAME, self.DOMAIN)

    def is_valid_url(self, url: str) -> Tuple[bool, str]:
        """
        URL-level validation executed BEFORE enqueueing into the URL Frontier.
        Checks for allowed domains, residential categories, and blocked sections.
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        if domain != self.domain:
            return False, f"Domain '{domain}' does not match SolarPanelTalk domain '{self.domain}'"

        path_lower = parsed.path.lower()
        full_lower = url.lower()

        # Hard-block disqualified patterns
        for pattern in self.DISQUALIFIED_PATTERNS:
            if pattern in path_lower or pattern in full_lower:
                return False, f"SolarPanelTalk: matches disqualified pattern '{pattern}'"

        # Check pagination freshness limit (> page 3)
        page_match = self.PAGE_REGEX.search(path_lower)
        if page_match:
            page_num = int(page_match.group(1))
            if page_num > 3:
                return False, f"SolarPanelTalk: page {page_num} exceeds max pagination limit 3"

        qs = parse_qs(parsed.query)
        if "page" in qs:
            try:
                page_num = int(qs["page"][0])
                if page_num > 3:
                    return False, f"SolarPanelTalk: query page {page_num} exceeds max pagination limit 3"
            except (ValueError, IndexError):
                pass

        # Forum root path check: must be within /forum/
        if not path_lower.startswith("/forum"):
            return False, "SolarPanelTalk: outside /forum/ hierarchy"

        # Check category relevance: must match at least one allowed residential topic
        is_relevant = any(cat in path_lower for cat in self.ALLOWED_CATEGORIES)
        if not is_relevant:
            # Check if it's the main forum index page
            if path_lower.strip("/") == "forum":
                return True, "Main forum index"
            return False, "SolarPanelTalk: non-residential category"

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
        is_paged = bool(self.PAGE_REGEX.search(path)) or ("page" in parse_qs(parsed.query))

        if is_thread:
            if is_paged:
                return "THREAD_PAGINATION"
            return "THREAD"

        if is_paged:
            return "CATEGORY_PAGINATION"

        # Check if subcategory (depth >= 3 segments e.g. /forum/solar-panels-for-home/solar-panel-installation)
        segments = [s for s in path.split("/") if s]
        if len(segments) >= 3:
            return "SUBCATEGORY"

        return "CATEGORY"

    def discover_category_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extracts valid residential category and subcategory URLs."""
        discovered = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(current_url, href)
            # Remove hash anchors
            clean_url = full_url.split("#")[0]
            url_type = self.classify_url_type(clean_url)
            if url_type in ("CATEGORY", "SUBCATEGORY", "CATEGORY_PAGINATION"):
                discovered.add(clean_url)
        return sorted(list(discovered))

    def discover_thread_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extracts individual discussion thread URLs from category/index pages."""
        discovered = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(current_url, href)
            clean_url = full_url.split("#")[0]
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
            url_type = self.classify_url_type(clean_url)
            if url_type in ("THREAD_PAGINATION", "CATEGORY_PAGINATION"):
                page_match = self.PAGE_REGEX.search(clean_url)
                if page_match:
                    page_num = int(page_match.group(1))
                    if 1 <= page_num <= max_page:
                        discovered.add(clean_url)
                else:
                    qs = parse_qs(urlparse(clean_url).query)
                    if "page" in qs:
                        try:
                            page_num = int(qs["page"][0])
                            if 1 <= page_num <= max_page:
                                discovered.add(clean_url)
                        except ValueError:
                            pass
        return sorted(list(discovered))

    def extract_thread_data(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """
        Extracts structured thread discussion data:
        - title: Post title
        - author: Original poster handle
        - published_at: OP publication date (YYYY-MM-DD)
        - latest_reply_at: Most recent reply date in thread (YYYY-MM-DD)
        - main_content: Text of original post
        - replies: List of reply text snippets
        - location_raw: Voluntarily disclosed user location
        """
        title = ""
        title_tag = soup.find("h1", class_=re.compile(r"(main-title|b-post__title|p-title)")) or soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" - SolarPanelTalk")[0].strip()

        # Find posts
        post_elements = soup.find_all(class_=re.compile(r"b-post\b"))
        if not post_elements:
            # Fallback to article or general post container
            post_elements = soup.find_all("article")

        author = "Anonymous"
        published_at = None
        latest_reply_at = None
        main_content = ""
        replies = []
        location_raw = None

        if post_elements:
            # First post is OP
            first_post = post_elements[0]

            # Author extraction
            author_tag = first_post.find(class_=re.compile(r"(b-post__author|author|username)")) or first_post.find("a", href=re.compile(r"/member/"))
            if author_tag:
                author = author_tag.get_text(strip=True)

            # Location from userinfo
            loc_tag = first_post.find(class_=re.compile(r"(location|userinfo__location|b-userinfo__detail)"))
            if loc_tag:
                location_raw = loc_tag.get_text(strip=True)

            # Creation timestamp
            time_tag = first_post.find("time") or first_post.find(class_=re.compile(r"b-post__timestamp"))
            if time_tag:
                dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
                dt = dateparser.parse(dt_str)
                if dt:
                    published_at = dt.strftime("%Y-%m-%d")

            # Content of OP
            content_tag = first_post.find(class_=re.compile(r"(b-post__content|js-post__content|content)"))
            if content_tag:
                main_content = content_tag.get_text(separator=" ", strip=True)

            # Extract replies from subsequent posts
            for post in post_elements[1:]:
                c_tag = post.find(class_=re.compile(r"(b-post__content|js-post__content|content)"))
                if c_tag:
                    rep_text = c_tag.get_text(separator=" ", strip=True)
                    if rep_text:
                        replies.append(rep_text)

                t_tag = post.find("time") or post.find(class_=re.compile(r"b-post__timestamp"))
                if t_tag:
                    dt_str = t_tag.get("datetime") or t_tag.get_text(strip=True)
                    dt = dateparser.parse(dt_str)
                    if dt:
                        latest_reply_at = dt.strftime("%Y-%m-%d")

        # Fallback to meta tags if published_at is not found
        if not published_at:
            meta_pub = soup.find("meta", attrs={"property": "article:published_time"}) or soup.find("meta", attrs={"name": "date"})
            if meta_pub and meta_pub.get("content"):
                dt = dateparser.parse(meta_pub["content"])
                if dt:
                    published_at = dt.strftime("%Y-%m-%d")

        if not latest_reply_at:
            meta_mod = soup.find("meta", attrs={"property": "article:modified_time"})
            if meta_mod and meta_mod.get("content"):
                dt = dateparser.parse(meta_mod["content"])
                if dt:
                    latest_reply_at = dt.strftime("%Y-%m-%d")

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
        """Discovers related threads from thread pages (e.g. vBulletin similar threads)."""
        discovered = set()
        clean_current = current_url.split("?")[0].split("#")[0].rstrip("/")
        for container in soup.find_all(class_=re.compile(r"(similar-threads|related-threads|b-similar-threads)")):
            for a in container.find_all("a", href=True):
                clean_url = urljoin(current_url, a["href"]).split("#")[0].split("?")[0]
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


