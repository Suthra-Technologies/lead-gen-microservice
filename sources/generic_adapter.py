"""
Generic Source Adapter Module
Fallback adapter for registered solar communities (e.g. sirensolar.org,
ecorenovator.org, builditsolar.com) adhering to BaseSourceAdapter contract.
"""

import re
from typing import Dict, List, Tuple, Optional, Any
from urllib.parse import urlparse, urljoin, parse_qs
from bs4 import BeautifulSoup
import dateparser

from sources.base_adapter import BaseSourceAdapter
from config import BLOCKED_URL_PATTERNS, EXCLUDED_TOPICS, BLOCKED_EXTENSIONS


class GenericSourceAdapter(BaseSourceAdapter):
    def __init__(self, source_id: str, name: str, domain: str):
        super().__init__(source_id, name, domain)

    def is_valid_url(self, url: str) -> Tuple[bool, str]:
        """Validates generic candidate URL against domain, extensions, and blocked patterns."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        if domain != self.domain and not domain.endswith("." + self.domain):
            return False, f"Domain '{domain}' does not match '{self.domain}'"

        path_lower = parsed.path.lower()
        full_lower = url.lower()

        for ext in BLOCKED_EXTENSIONS:
            if path_lower.endswith(ext):
                return False, f"Blocked extension '{ext}'"

        for pattern in BLOCKED_URL_PATTERNS:
            if pattern in path_lower or pattern in full_lower:
                return False, f"Matches blocked pattern '{pattern}'"

        for topic in EXCLUDED_TOPICS:
            if topic in path_lower or topic in full_lower:
                return False, f"Matches excluded category '{topic}'"

        # Check pagination (> page 3)
        page_match = re.search(r'/page(?:[4-9]|\d{2,})\b', path_lower)
        if page_match:
            return False, "Exceeds pagination limit (> page 3)"

        return True, "OK"

    def classify_url_type(self, url: str) -> str:
        valid, _ = self.is_valid_url(url)
        if not valid:
            return "IGNORED"

        path_lower = urlparse(url).path.lower()
        if re.search(r"/(?:threads|topic|post|comments)/", path_lower) or re.search(r"/\d{4,8}-[a-z0-9-]+", path_lower):
            return "THREAD"

        return "CATEGORY"

    def discover_category_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        discovered = set()
        for a in soup.find_all("a", href=True):
            clean_url = urljoin(current_url, a["href"].strip()).split("#")[0]
            if self.classify_url_type(clean_url) == "CATEGORY":
                discovered.add(clean_url)
        return sorted(list(discovered))

    def discover_thread_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        discovered = set()
        for a in soup.find_all("a", href=True):
            clean_url = urljoin(current_url, a["href"].strip()).split("#")[0]
            if self.classify_url_type(clean_url) == "THREAD":
                discovered.add(clean_url)
        return sorted(list(discovered))

    def discover_pagination_links(self, soup: BeautifulSoup, current_url: str, max_page: int = 3) -> List[str]:
        discovered = set()
        for a in soup.find_all("a", href=True):
            clean_url = urljoin(current_url, a["href"].strip()).split("#")[0]
            page_match = re.search(r'/page([0-9]+)\b', clean_url)
            if page_match:
                page_num = int(page_match.group(1))
                if 1 <= page_num <= max_page and self.is_valid_url(clean_url)[0]:
                    discovered.add(clean_url)
        return sorted(list(discovered))

    def extract_thread_data(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        return {
            "title": title,
            "author": "Anonymous",
            "published_at": None,
            "latest_reply_at": None,
            "main_content": "",
            "replies": [],
            "location_raw": None
        }

