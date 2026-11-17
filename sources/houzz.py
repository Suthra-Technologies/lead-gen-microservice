"""
Houzz Solar Discussions Source Adapter Module
Implements source-aware recursive web discovery and lead extraction for
Houzz Residential Solar Discussions.
Strictly scopes crawling to US residential solar discussions, hard-blocks contractor directories
(/professionals/), pro marketing pages (/for-pros/), and international geographic listings.
"""

import re
from typing import Dict, List, Tuple, Optional, Any
from urllib.parse import urlparse, urljoin, parse_qs
from bs4 import BeautifulSoup
import dateparser

from sources.base_adapter import BaseSourceAdapter


class HouzzAdapter(BaseSourceAdapter):
    SOURCE_ID = "houzz"
    NAME = "Houzz Solar Discussions"
    DOMAIN = "houzz.com"

    # Strictly allowed category paths
    ALLOWED_CATEGORIES = {
        "/discussions/solar-energy",
        "/discussions/solar-energy/sort/newest"
    }

    # Disqualified patterns (strictly blocks directory, pro, and non-residential paths)
    DISQUALIFIED_PATTERNS = [
        "/professionals/",
        "/for-pros/",
        "/houzz-pro/",
        "pro.houzz.com",
        "/photos/",
        "/products/",
        "/ideabooks/",
        "/magazine/",
        "/pro/",
        "/editProfile/",
        "/user/",
        "/probr0",
        "-in-probr0",
        "pune-",
        "/india/",
        "/uk/",
        "/au/",
        "/de/",
        "/fr/",
        "/ru/",
        "/jp/",
        "/login",
        "/signin",
        "/register",
        "/writeReview"
    ]

    # Regex for Houzz discussion thread: /discussions/<slug>-dsvw-vd~<id> or /discussions/<id>/<slug>
    THREAD_REGEX = re.compile(r"/discussions/(?:[a-z0-9-]+-dsvw-vd~\d+|\d{6,10}(?:/[a-z0-9-]+)?)", re.IGNORECASE)

    def __init__(self):
        super().__init__(self.SOURCE_ID, self.NAME, self.DOMAIN)

    def is_valid_url(self, url: str) -> Tuple[bool, str]:
        """
        URL-level validation executed BEFORE enqueueing into the URL Frontier.
        Ensures strictly residential solar discussions; blocks contractor directories & foreign geo.
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")
        if domain != self.domain:
            return False, f"Domain '{domain}' is not houzz.com"

        path_lower = parsed.path.lower()
        full_lower = url.lower()

        # Hard-block disqualified patterns
        for pattern in self.DISQUALIFIED_PATTERNS:
            if pattern in path_lower or pattern in full_lower:
                return False, f"Houzz: matches disqualified pattern '{pattern}'"

        # Must be under /discussions/
        if not path_lower.startswith("/discussions"):
            return False, "Houzz: outside /discussions/ section"

        # Check pagination freshness limit (> page 3)
        qs = parse_qs(parsed.query)
        if "p" in qs:
            try:
                page_num = int(qs["p"][0])
                if page_num > 3:
                    return False, f"Houzz: page {page_num} exceeds max pagination limit 3"
            except (ValueError, IndexError):
                pass

        # Must either be solar-energy category or a solar discussion thread
        if "/discussions/solar-energy" in path_lower:
            return True, "OK"

        if self.THREAD_REGEX.search(path_lower):
            # Check solar relevance in thread slug
            solar_terms = ["solar", "sun", "photovoltaic", "nem", "inverter", "powerwall", "enphase", "panel", "quote"]
            if any(term in path_lower for term in solar_terms):
                return True, "OK"
            return False, "Houzz: discussion thread is not solar-related"

        return False, "Houzz: non-solar discussion URL"

    def classify_url_type(self, url: str) -> str:
        """
        Classifies the URL into functional role:
        'CATEGORY', 'THREAD', 'CATEGORY_PAGINATION', 'IGNORED'
        """
        valid, _ = self.is_valid_url(url)
        if not valid:
            return "IGNORED"

        parsed = urlparse(url)
        path_lower = parsed.path.lower()
        qs = parse_qs(parsed.query)

        if self.THREAD_REGEX.search(path_lower):
            return "THREAD"

        if "/discussions/solar-energy" in path_lower:
            if "p" in qs:
                return "CATEGORY_PAGINATION"
            return "CATEGORY"

        return "IGNORED"

    def discover_category_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extracts valid solar energy category and newest sort URLs."""
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
        """Extracts individual solar discussion thread URLs."""
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
        """Extracts pagination links up to max_page limit."""
        discovered = set()
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(current_url, href)
            clean_url = full_url.split("#")[0]
            url_type = self.classify_url_type(clean_url)
            if url_type == "CATEGORY_PAGINATION":
                qs = parse_qs(urlparse(clean_url).query)
                if "p" in qs:
                    try:
                        page_num = int(qs["p"][0])
                        if 1 <= page_num <= max_page:
                            discovered.add(clean_url)
                    except ValueError:
                        pass
        return sorted(list(discovered))

    def extract_thread_data(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """
        Extracts structured thread discussion data from Houzz:
        - title: Post title
        - author: Original poster handle
        - published_at: OP publication date (YYYY-MM-DD)
        - latest_reply_at: Most recent reply date in thread (YYYY-MM-DD)
        - main_content: Text of original post
        - replies: List of reply text snippets
        - location_raw: Voluntarily disclosed user location
        """
        title = ""
        title_tag = soup.find("h1") or soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).split(" | Houzz")[0].strip()

        author = "Anonymous"
        author_tag = soup.find(class_=re.compile(r"(author|userName|user-name)")) or soup.find("a", href=re.compile(r"/user/"))
        if author_tag:
            author = author_tag.get_text(strip=True)

        published_at = None
        latest_reply_at = None

        time_tags = soup.find_all("time")
        if time_tags:
            dt_first = dateparser.parse(time_tags[0].get("datetime") or time_tags[0].get_text(strip=True))
            if dt_first:
                published_at = dt_first.strftime("%Y-%m-%d")
            if len(time_tags) > 1:
                dt_last = dateparser.parse(time_tags[-1].get("datetime") or time_tags[-1].get_text(strip=True))
                if dt_last:
                    latest_reply_at = dt_last.strftime("%Y-%m-%d")

        main_content = ""
        content_tag = soup.find(class_=re.compile(r"(discussion-body|post-content|topic-content)"))
        if content_tag:
            main_content = content_tag.get_text(separator=" ", strip=True)

        replies = []
        comment_tags = soup.find_all(class_=re.compile(r"(comment-body|reply-content)"))
        for c in comment_tags:
            rep_text = c.get_text(separator=" ", strip=True)
            if rep_text:
                replies.append(rep_text)

        return {
            "title": title,
            "author": author,
            "published_at": published_at,
            "latest_reply_at": latest_reply_at,
            "main_content": main_content,
            "replies": replies,
            "location_raw": None
        }

