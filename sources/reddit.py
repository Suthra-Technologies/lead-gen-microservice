"""
Reddit Source Adapter Module
Implements source-aware recursive web discovery and lead extraction for
Reddit solar communities (r/solar, r/TeslaSolar, r/TexasSolar, r/floridasolar,
r/HomeImprovement, r/DIYsolar, r/Powerwall).
Enforces subreddit allowlists, parses real-time RSS feeds, and extracts structured
comment threads with author handles and ISO timestamps.
"""

import re
from typing import Dict, List, Tuple, Optional, Any
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
import dateparser

from sources.base_adapter import BaseSourceAdapter


class RedditAdapter(BaseSourceAdapter):
    SOURCE_ID = "reddit"
    NAME = "Reddit Solar Communities"
    DOMAIN = "reddit.com"

    ALLOWED_SUBREDDITS = {
        "solar",
        "teslasolar",
        "texassolar",
        "floridasolar",
        "homeimprovement",
        "diysolar",
        "powerwall"
    }

    DISQUALIFIED_PATTERNS = [
        "/user/",
        "/u/",
        "/search",
        "/submit",
        "/gallery/",
        "/media",
        "/gold",
        "/premium",
        "/login",
        "/register",
        "/message/",
        "/wiki/",
        "/settings"
    ]

    # Regex for Reddit comment thread: /r/<subreddit>/comments/<id>/<slug>/
    THREAD_REGEX = re.compile(r"/r/([a-zA-Z0-9_]+)/comments/([a-z0-9]+)(?:/[^/]+)?/?", re.IGNORECASE)

    # Regex for Reddit RSS or JSON feed
    FEED_REGEX = re.compile(r"/r/([a-zA-Z0-9_]+)/(?:new\.(?:rss|json)|(?:hot|top)\.(?:rss|json))", re.IGNORECASE)

    def __init__(self):
        super().__init__(self.SOURCE_ID, self.NAME, self.DOMAIN)

    def is_valid_url(self, url: str) -> Tuple[bool, str]:
        """
        URL-level validation executed BEFORE enqueueing into the URL Frontier.
        Checks for allowed domains, whitelisted subreddits, and blocks irrelevant paths.
        """
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "").replace("old.", "").replace("new.", "")
        if domain != "reddit.com":
            return False, f"Domain '{domain}' is not reddit.com"

        path_lower = parsed.path.lower()
        full_lower = url.lower()

        for pattern in self.DISQUALIFIED_PATTERNS:
            if pattern in path_lower or pattern in full_lower:
                return False, f"Reddit: matches disqualified pattern '{pattern}'"

        # Check subreddit membership
        sub_match = re.search(r"/r/([a-zA-Z0-9_]+)", path_lower)
        if not sub_match:
            return False, "Reddit: no subreddit in URL path"

        subreddit = sub_match.group(1).lower()
        if subreddit not in self.ALLOWED_SUBREDDITS:
            return False, f"Reddit: subreddit 'r/{subreddit}' not in allowed solar subreddits"

        # Must be feed or comments thread
        is_thread = bool(self.THREAD_REGEX.search(path_lower))
        is_feed = bool(self.FEED_REGEX.search(path_lower)) or path_lower.endswith((".rss", ".json"))

        if not (is_thread or is_feed):
            # Check if subreddit root e.g. /r/solar or /r/solar/new
            if path_lower.rstrip("/") in [f"/r/{s}" for s in self.ALLOWED_SUBREDDITS] or path_lower.rstrip("/").endswith("/new"):
                return True, "Subreddit index"
            return False, "Reddit: neither a discussion thread nor a supported feed"

        return True, "OK"

    def classify_url_type(self, url: str) -> str:
        """
        Classifies the URL into functional role:
        'CATEGORY', 'THREAD', 'IGNORED'
        """
        valid, _ = self.is_valid_url(url)
        if not valid:
            return "IGNORED"

        path_lower = urlparse(url).path.lower()
        if self.THREAD_REGEX.search(path_lower):
            return "THREAD"

        if self.FEED_REGEX.search(path_lower) or path_lower.endswith((".rss", ".json")):
            return "CATEGORY"

        return "CATEGORY"

    def discover_category_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extracts valid subreddit feeds."""
        discovered = set()
        for sub in self.ALLOWED_SUBREDDITS:
            discovered.add(f"https://www.reddit.com/r/{sub}/new.rss")
        return sorted(list(discovered))

    def discover_thread_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Extracts individual discussion thread URLs from Reddit HTML or RSS."""
        discovered = set()
        # If RSS XML feed
        for entry in soup.find_all("entry"):
            link_tag = entry.find("link", href=True)
            if link_tag:
                clean_url = link_tag["href"].split("?")[0]
                if self.THREAD_REGEX.search(clean_url):
                    discovered.add(clean_url)

        # Standard HTML anchor tags
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            full_url = urljoin(current_url, href)
            clean_url = full_url.split("?")[0].split("#")[0]
            if self.classify_url_type(clean_url) == "THREAD":
                discovered.add(clean_url)

        return sorted(list(discovered))

    def discover_pagination_links(self, soup: BeautifulSoup, current_url: str, max_page: int = 3) -> List[str]:
        """Reddit uses token-based pagination (?after=...); kept empty to favor real-time RSS."""
        return []

    def extract_thread_data(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """
        Extracts structured thread data from Reddit HTML or Atom XML feed.
        """
        title = ""
        author = "Anonymous"
        published_at = None
        latest_reply_at = None
        main_content = ""
        replies = []

        # If Atom XML
        entry = soup.find("entry")
        if entry:
            if entry.title:
                title = entry.title.get_text(strip=True)
            author_tag = entry.find("author")
            if author_tag and author_tag.find("name"):
                author = author_tag.find("name").get_text(strip=True).replace("/u/", "")

            updated_tag = entry.find("updated") or entry.find("published")
            if updated_tag:
                dt = dateparser.parse(updated_tag.get_text(strip=True))
                if dt:
                    published_at = dt.strftime("%Y-%m-%d")

            content_tag = entry.find("content")
            if content_tag:
                c_soup = BeautifulSoup(content_tag.get_text(), "html.parser")
                main_content = c_soup.get_text(separator=" ", strip=True)

            return {
                "title": title,
                "author": author,
                "published_at": published_at,
                "latest_reply_at": latest_reply_at,
                "main_content": main_content,
                "replies": replies,
                "location_raw": None
            }

        # HTML parsing
        title_tag = soup.find("h1") or soup.find("title")
        if title_tag:
            title = title_tag.get_text(strip=True).replace(" : r/", " - r/").split(" - r/")[0].strip()

        author_tag = soup.find("a", href=re.compile(r"/user/([a-zA-Z0-9_-]+)"))
        if author_tag:
            author = author_tag.get_text(strip=True).replace("u/", "").replace("/u/", "")

        time_tag = soup.find("time")
        if time_tag:
            dt_str = time_tag.get("datetime") or time_tag.get_text(strip=True)
            dt = dateparser.parse(dt_str)
            if dt:
                published_at = dt.strftime("%Y-%m-%d")

        # Main post text
        post_div = soup.find(attrs={"data-test-id": "post-content"}) or soup.find("div", class_=re.compile(r"usertext-body"))
        if post_div:
            main_content = post_div.get_text(separator=" ", strip=True)

        return {
            "title": title,
            "author": author,
            "published_at": published_at,
            "latest_reply_at": latest_reply_at,
            "main_content": main_content,
            "replies": replies,
            "location_raw": None
        }

    def discover_related_threads(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Discovers related Reddit comment threads mentioned in body or sidebar."""
        discovered = set()
        clean_current = current_url.split("?")[0].split("#")[0].rstrip("/")
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


