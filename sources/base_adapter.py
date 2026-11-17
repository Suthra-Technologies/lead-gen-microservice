"""
Base Source Adapter Module
Defines the standard interface for source-aware recursive web discovery and lead extraction.
Every registered discussion source implements this contract to enforce platform-specific
URL filtering, category discovery, thread identification, pagination, and date extraction.
"""

import hashlib
from abc import ABC, abstractmethod
from typing import Dict, List, Tuple, Optional, Any
from urllib.parse import urlparse
from bs4 import BeautifulSoup


class BaseSourceAdapter(ABC):
    def __init__(self, source_id: str, name: str, domain: str):
        self.source_id = source_id
        self.name = name
        self.domain = domain.lower().replace("www.", "")

    @abstractmethod
    def is_valid_url(self, url: str) -> Tuple[bool, str]:
        """
        URL-level validation executed BEFORE enqueueing into the URL Frontier.
        Enforces category allowlists, excludes member profiles, non-US listings,
        and irrelevant sections (e.g. commercial lighting, admin functions).
        Returns: (is_allowed: bool, reason: str)
        """
        pass

    @abstractmethod
    def classify_url_type(self, url: str) -> str:
        """
        Classifies the URL into its functional role:
        'CATEGORY', 'SUBCATEGORY', 'THREAD', 'THREAD_PAGINATION', 'CATEGORY_PAGINATION', 'IGNORED'
        """
        pass

    def classify_url(self, url: str) -> str:
        """Alias for classify_url_type for API consistency."""
        return self.classify_url_type(url)

    @abstractmethod
    def discover_category_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """
        Extracts valid residential category and subcategory URLs.
        Filters out commercial, off-topic, or non-residential boards.
        """
        pass

    def discover_categories(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Standardized interface for category & subcategory discovery."""
        return self.discover_category_links(soup, current_url)

    @abstractmethod
    def discover_thread_links(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """
        Extracts individual discussion thread URLs from category/index pages.
        """
        pass

    def discover_threads(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """Standardized interface for thread discovery."""
        return self.discover_thread_links(soup, current_url)

    @abstractmethod
    def discover_pagination_links(self, soup: BeautifulSoup, current_url: str, max_page: int = 3) -> List[str]:
        """
        Extracts next page links for threads or categories up to max_page limit.
        """
        pass

    def discover_pagination(self, soup: BeautifulSoup, current_url: str, max_page: int = 3) -> List[str]:
        """Standardized interface for pagination discovery."""
        return self.discover_pagination_links(soup, current_url, max_page)

    def discover_related_threads(self, soup: BeautifulSoup, current_url: str) -> List[str]:
        """
        Extracts related or recommended discussion threads from a thread page.
        Default implementation extracts internal thread links that match thread regex.
        """
        threads = self.discover_thread_links(soup, current_url)
        # Exclude self
        clean_current = current_url.split("?")[0].split("#")[0].rstrip("/")
        return [t for t in threads if t.rstrip("/") != clean_current]

    @abstractmethod
    def extract_thread_data(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """
        Extracts structured thread discussion data:
        - title: Post title
        - author: Original poster handle
        - published_at: OP publication date (YYYY-MM-DD)
        - latest_reply_at: Most recent reply date in thread (YYYY-MM-DD)
        - main_content: Text of original post
        - replies: List of reply text snippets
        - location_raw: Voluntarily disclosed user location (e.g. from author box)
        """
        pass

    def extract_thread(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """Standardized interface for thread extraction."""
        return self.extract_thread_data(soup, url)

    def extract_replies(self, soup: BeautifulSoup) -> List[str]:
        """Extracts text of reply posts from a discussion thread."""
        thread_data = self.extract_thread_data(soup, "")
        return thread_data.get("replies", [])

    def extract_dates(self, soup: BeautifulSoup) -> Dict[str, Optional[str]]:
        """Extracts thread creation date and latest reply date."""
        thread_data = self.extract_thread_data(soup, "")
        return {
            "thread_created_at": thread_data.get("published_at"),
            "latest_thread_activity_at": thread_data.get("latest_reply_at"),
            "last_buyer_intent_at": thread_data.get("published_at")
        }

    def extract_posts(self, soup: BeautifulSoup, url: str) -> List[Dict[str, Any]]:
        """
        Extracts individual discussion posts (OP and subsequent replies)
        with structured post-level metadata for granular storage and independent lead evaluation.
        """
        thread_data = self.extract_thread_data(soup, url)
        posts = []
        title = thread_data.get("title", "")
        main_content = thread_data.get("main_content", "")
        author = thread_data.get("author", "Anonymous")
        published_at = thread_data.get("published_at")
        location_raw = thread_data.get("location_raw")

        if main_content or title:
            post_id = f"{self.source_id}_op_{hashlib.md5(f'{url}_op'.encode('utf-8')).hexdigest()[:12]}"
            posts.append({
                "post_id": post_id,
                "thread_url": url,
                "source_name": self.name,
                "thread_title": title,
                "author_name": author,
                "post_text": main_content,
                "post_order": 0,
                "is_original_post": True,
                "posted_at": published_at,
                "reply_count": len(thread_data.get("replies", [])),
                "location_text": location_raw,
                "last_activity_at": thread_data.get("latest_reply_at") or published_at
            })

        replies = thread_data.get("replies", [])
        for idx, rep in enumerate(replies, start=1):
            if not rep:
                continue
            rep_id = f"{self.source_id}_rep_{hashlib.md5(f'{url}_{idx}_{rep[:50]}'.encode('utf-8')).hexdigest()[:12]}"
            posts.append({
                "post_id": rep_id,
                "thread_url": url,
                "source_name": self.name,
                "thread_title": title,
                "author_name": f"{author}_reply_{idx}",
                "post_text": rep,
                "post_order": idx,
                "is_original_post": False,
                "posted_at": thread_data.get("latest_reply_at") or published_at,
                "reply_count": len(replies),
                "location_text": location_raw,
                "last_activity_at": thread_data.get("latest_reply_at")
            })

        return posts

