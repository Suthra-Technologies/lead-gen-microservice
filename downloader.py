"""
HTML Downloader Module
Modern asynchronous network fetcher powered by HTTPX and PRAW Reddit integration.
Includes full Deno.js Fetch Gateway integration with startup health checks,
explicit transport selection logging, and configurable direct fallback.
"""

import time
import asyncio
import logging
from urllib.parse import urlparse, parse_qs
from typing import Optional, Dict, Any
import httpx
from config import (
    DEFAULT_USER_AGENT,
    REQUEST_TIMEOUT_SECONDS,
    USE_FETCH_GATEWAY,
    FETCH_GATEWAY_URL,
    GATEWAY_HEALTH_URL,
    FALLBACK_TO_DIRECT_HTTPX
)
from reddit_client import RedditClient

logger = logging.getLogger("Crawler.Downloader")


class HTMLDownloader:
    def __init__(self, user_agent: str = DEFAULT_USER_AGENT, timeout_seconds: int = REQUEST_TIMEOUT_SECONDS):
        self.user_agent = user_agent
        self.timeout = httpx.Timeout(timeout_seconds, connect=8.0)
        self.client: Optional[httpx.AsyncClient] = None
        self.reddit_client = RedditClient()
        self.active_transport: Optional[str] = None
        
        # Transport usage metrics
        self.gateway_requests = 0
        self.direct_requests = 0
        self.praw_requests = 0

    async def start(self):
        headers = {
            "User-Agent": self.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        }
        limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=self.timeout,
            limits=limits,
            follow_redirects=True,
            verify=False
        )

        # Gateway Health Check & Transport Selection
        if USE_FETCH_GATEWAY:
            is_healthy = await self._check_gateway_health()
            if is_healthy:
                self.active_transport = "DENO_GATEWAY"
                logger.info(
                    f"[Transport Selected] Deno.js Fetch Gateway ({FETCH_GATEWAY_URL}) - "
                    f"Health Check: PASSED"
                )
            else:
                if FALLBACK_TO_DIRECT_HTTPX:
                    self.active_transport = "DIRECT_HTTPX"
                    logger.warning(
                        f"[Transport Warning] Deno Gateway unreachable at {GATEWAY_HEALTH_URL}. "
                        f"FALLING BACK TO DIRECT HTTPX client."
                    )
                else:
                    raise RuntimeError(f"Deno Gateway unreachable at {GATEWAY_HEALTH_URL} and fallback is disabled.")
        else:
            self.active_transport = "DIRECT_HTTPX"
            logger.info("[Transport Selected] Direct HTTPX client (Gateway disabled in config).")

    async def _check_gateway_health(self) -> bool:
        """Verifies Deno.js Gateway is running and healthy."""
        try:
            res = await self.client.get(GATEWAY_HEALTH_URL, timeout=3.0)
            if res.status_code == 200:
                data = res.json()
                logger.debug(f"[Gateway Health Data] {data}")
                return True
        except Exception as e:
            logger.debug(f"[Gateway Health Check Failed] {e}")
        return False

    async def close(self):
        if self.client and not self.client.is_closed:
            await self.client.aclose()
            logger.info(
                f"[Downloader Closed] Total Requests -> "
                f"Gateway: {self.gateway_requests} | Direct HTTPX: {self.direct_requests} | PRAW: {self.praw_requests}"
            )

    async def _fetch_via_gateway(self, url: str) -> Optional[Dict[str, Any]]:
        """Routes HTTP request through Deno.js Fetch Gateway service."""
        try:
            res = await self.client.post(FETCH_GATEWAY_URL, json={"url": url}, timeout=self.timeout)
            self.gateway_requests += 1

            if res.status_code == 200:
                data = res.json()
                from_cache = data.get("from_cache", False)
                logger.info(f"[Downloader: Deno Gateway] 200 OK {'(Cache Hit)' if from_cache else ''} -> {url}")
                return {
                    "url": data.get("url", url),
                    "status": data.get("status", 200),
                    "html": data.get("body", ""),
                    "skipped": False,
                    "is_reddit_praw": False,
                    "transport": "DENO_GATEWAY"
                }
            elif res.status_code in (403, 429):
                data = res.json()
                logger.warning(f"[Downloader: Deno Gateway] {res.status_code} {data.get('action')} on {url}")
                return {
                    "url": url,
                    "status": res.status_code,
                    "html": None,
                    "skipped": False,
                    "is_reddit_praw": False,
                    "transport": "DENO_GATEWAY"
                }
        except Exception as e:
            if FALLBACK_TO_DIRECT_HTTPX:
                logger.warning(f"[Downloader: Gateway Error] {e}. Falling back to Direct HTTPX for {url}")
            else:
                logger.error(f"[Downloader: Gateway Error] {e}. Fallback disabled.")
                return {"url": url, "status": 502, "html": None, "skipped": False, "transport": "DENO_GATEWAY"}
        return None

    async def fetch(
        self,
        url: str,
        etag: Optional[str] = None,
        last_modified: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Downloads a web page via the active transport (Deno Gateway or direct HTTPX),
        with conditional HTTP caching (ETag / Last-Modified), rate-limit handling,
        and PRAW delegation for Reddit URLs.
        """
        domain = urlparse(url).netloc.lower()

        # Handle Reddit: Use PRAW when configured; fallback to public live RSS feed if unconfigured
        if "reddit.com" in domain:
            is_rss = url.endswith(".rss") or "/new.rss" in url
            if is_rss:
                # Direct or Gateway HTTP fetch for Reddit RSS feed
                pass
            elif self.reddit_client.is_configured():
                parsed = urlparse(url)
                path_parts = [p for p in parsed.path.split("/") if p]
                subreddit_name = "solar"
                if "r" in path_parts:
                    r_idx = path_parts.index("r")
                    if r_idx + 1 < len(path_parts):
                        subreddit_name = path_parts[r_idx + 1]

                query = None
                if "search" in parsed.path:
                    qs = parse_qs(parsed.query)
                    query = qs.get("q", [None])[0]

                self.praw_requests += 1
                loop = asyncio.get_running_loop()
                posts = await loop.run_in_executor(
                    None, self.reddit_client.fetch_subreddit_posts, subreddit_name, query, 25
                )
                return {
                    "url": url,
                    "status": 200,
                    "html": "",
                    "skipped": False,
                    "is_reddit_praw": True,
                    "reddit_posts": posts,
                    "transport": "REDDIT_PRAW"
                }
            elif "/new.json" in url or "/search.json" in url or "/r/" in url:
                # Convert JSON endpoint to public RSS feed fallback
                rss_url = url.split("?")[0].replace(".json", ".rss")
                logger.info(f"[Downloader Reddit Fallback] PRAW unconfigured; fetching live public RSS: {rss_url}")
                url = rss_url
            else:
                logger.warning(
                    f"[Downloader Paused] Reddit URL '{url}' requires PRAW credentials in .env. "
                    "Skipping until REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are configured."
                )
                return {"url": url, "status": 401, "html": None, "skipped": True}

        # 1. Try Deno Fetch Gateway if selected
        if self.active_transport == "DENO_GATEWAY":
            gw_result = await self._fetch_via_gateway(url)
            if gw_result is not None:
                return gw_result

        # 2. Direct HTTPX fetching (as primary or fallback)
        if not self.client:
            raise RuntimeError("Downloader is not started. Call await downloader.start() first.")

        self.direct_requests += 1
        start_time = time.perf_counter()
        logger.info(f"[Downloader: Direct HTTPX] Downloading: {url}")

        req_headers = {}
        if etag:
            req_headers["If-None-Match"] = etag
        if last_modified:
            req_headers["If-Modified-Since"] = last_modified

        try:
            response = await self.client.get(url, headers=req_headers if req_headers else None)
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            status = response.status_code
            content_bytes = len(response.content)
            content_type = response.headers.get("Content-Type", "").lower()
            resp_etag = response.headers.get("ETag")
            resp_last_modified = response.headers.get("Last-Modified")

            logger.info(
                f"[Downloader: Direct HTTPX] HTTP Status: {status} | URL: {response.url} | "
                f"Size: {content_bytes:,} bytes | Time: {elapsed_ms:.0f}ms | Type: {content_type.split(';')[0]}"
            )

            # Handle HTTP 304 Not Modified
            if status == 304:
                logger.info(f"[Downloader: 304 Not Modified] Content unchanged for {url}")
                return {
                    "url": str(response.url),
                    "status": 304,
                    "html": None,
                    "not_modified": True,
                    "skipped": False,
                    "etag": resp_etag or etag,
                    "last_modified": resp_last_modified or last_modified,
                    "transport": "DIRECT_HTTPX"
                }

            # Allow HTML, XHTML, and XML/RSS feeds
            is_html_or_xml = any(t in content_type for t in ["text/html", "application/xhtml", "xml", "rss", "atom"])
            if not is_html_or_xml:
                logger.info(f"[Downloader Skipped] Non-HTML/XML payload ({content_type}) for {url}")
                return {"url": str(response.url), "status": status, "html": None, "skipped": True, "transport": "DIRECT_HTTPX"}

            if status == 200:
                return {
                    "url": str(response.url),
                    "status": status,
                    "html": response.text,
                    "skipped": False,
                    "not_modified": False,
                    "is_reddit_praw": False,
                    "etag": resp_etag,
                    "last_modified": resp_last_modified,
                    "transport": "DIRECT_HTTPX"
                }
            elif status == 403:
                logger.warning(f"[Downloader Blocked] HTTP 403 Forbidden on {url} (Bot protection / access denied)")
                return {"url": str(response.url), "status": status, "html": None, "skipped": False, "transport": "DIRECT_HTTPX"}
            elif status == 429:
                retry_header = response.headers.get("Retry-After")
                retry_seconds = 300  # Default 5-minute backoff
                if retry_header:
                    try:
                        retry_seconds = int(retry_header)
                    except ValueError:
                        pass
                logger.warning(f"[Downloader Rate-Limited] HTTP 429 on {url} (Retry-After: {retry_seconds}s)")
                return {
                    "url": str(response.url),
                    "status": 429,
                    "html": None,
                    "skipped": False,
                    "retry_after": retry_seconds,
                    "transport": "DIRECT_HTTPX"
                }
            else:
                logger.warning(f"[Downloader Warning] HTTP {status} returned for {url}")
                return {"url": str(response.url), "status": status, "html": None, "skipped": False, "transport": "DIRECT_HTTPX"}

        except httpx.TimeoutException:
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            logger.warning(f"[Downloader Timeout] Request timed out after {elapsed_ms:.0f}ms: {url}")
            return {"url": url, "status": 408, "html": None, "skipped": False, "transport": "DIRECT_HTTPX"}
        except httpx.ConnectError as e:
            logger.warning(f"[Downloader Connection Error] Could not connect to {url}: {e}")
            return {"url": url, "status": 503, "html": None, "skipped": False, "transport": "DIRECT_HTTPX"}
        except Exception as e:
            logger.exception(f"[Downloader Exception] Unexpected failure downloading {url}: {e}")
            return {"url": url, "status": 500, "html": None, "skipped": False, "transport": "DIRECT_HTTPX"}
