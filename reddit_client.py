"""
Reddit API Client (PRAW Integration)
Provides official, rate-limit compliant access to Reddit discussions and searches.
Falls back gracefully if API credentials are not yet configured in .env.
"""

import os
import logging
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv

logger = logging.getLogger("Crawler.Reddit")
load_dotenv()


class RedditClient:
    def __init__(self):
        self.client_id = os.getenv("REDDIT_CLIENT_ID")
        self.client_secret = os.getenv("REDDIT_CLIENT_SECRET")
        self.user_agent = os.getenv("REDDIT_USER_AGENT", "SolarLeadDiscoveryBot/1.0")
        self.reddit = None
        self._initialize_client()

    def _initialize_client(self):
        if not self.client_id or not self.client_secret or self.client_id == "your_client_id_here":
            logger.warning(
                "[Reddit API Notice] Reddit credentials not configured in .env. "
                "Reddit crawling is paused until REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set."
            )
            return

        try:
            import praw
            self.reddit = praw.Reddit(
                client_id=self.client_id,
                client_secret=self.client_secret,
                user_agent=self.user_agent,
                check_for_async=False
            )
            # Read-only verification
            self.reddit.read_only = True
            logger.info("[Reddit API] PRAW client successfully authenticated in read-only mode.")
        except Exception as e:
            logger.error(f"[Reddit API Auth Error] Failed to initialize PRAW: {e}")
            self.reddit = None

    def is_configured(self) -> bool:
        return self.reddit is not None

    def fetch_subreddit_posts(self, subreddit_name: str, query: Optional[str] = None, limit: int = 25) -> List[Dict[str, Any]]:
        """
        Fetches submissions from Reddit using PRAW.
        If query is provided, performs a search; otherwise pulls new posts.
        """
        if not self.is_configured():
            return []

        posts = []
        try:
            subreddit = self.reddit.subreddit(subreddit_name)
            if query:
                submissions = subreddit.search(query, sort="new", limit=limit)
            else:
                submissions = subreddit.new(limit=limit)

            for submission in submissions:
                posts.append({
                    "title": submission.title,
                    "selftext": submission.selftext,
                    "author": str(submission.author) if submission.author else "Anonymous",
                    "post_url": f"https://www.reddit.com{submission.permalink}",
                    "score": submission.score,
                    "created_utc": submission.created_utc,
                    "num_comments": submission.num_comments
                })
            logger.info(f"[Reddit PRAW] Successfully fetched {len(posts)} posts from r/{subreddit_name}")
        except Exception as e:
            logger.error(f"[Reddit PRAW Error] Failed fetching r/{subreddit_name}: {e}")

        return posts

