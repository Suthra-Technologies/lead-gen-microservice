"""
Deduplication Engine
Handles URL canonicalization, forum thread normalization (stripping pagination/post anchor params),
exact content hashing (SHA-256), SimHash 64-bit fingerprinting, and Bloom filter deduplication.
"""

import re
import hashlib
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from typing import Set, List, Tuple, Optional


class Deduplicator:
    TRACKING_PARAMS = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "msclkid", "ref", "source", "affiliate_id"
    }

    # Forum post anchor parameters that point to the same thread page
    FORUM_POST_ANCHOR_PARAMS = {
        "p", "post", "pid", "postid", "view", "highlight", "goto", "s"
    }

    @classmethod
    def canonicalize_url(cls, url: str) -> Tuple[str, str, str]:
        """
        Normalizes a URL into a canonical form:
        - Lowercases scheme and netloc.
        - Strips URL fragments (#post12345).
        - Strips tracking marketing parameters.
        - Normalizes forum thread URLs: removes post-anchor parameters (?p=...)
          while preserving meaningful search queries (q=, limit=, sort=).
        - Strips unnecessary trailing slashes.
        Returns: (canonical_url, domain, url_hash)
        """
        parsed = urlparse(url.strip())
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        
        # Remove standard default ports
        if ":" in netloc:
            host, port = netloc.split(":", 1)
            if (scheme == "http" and port == "80") or (scheme == "https" and port == "443"):
                netloc = host

        # Normalize path
        path = re.sub(r"/+", "/", parsed.path)
        if not path:
            path = "/"
        elif len(path) > 1 and path.endswith("/"):
            path = path[:-1]

        # Determine if this is a forum thread where ?p= parameters represent post anchors
        is_thread_url = bool(
            re.search(r"/\d{4,8}-[a-z0-9-]+", path) or
            re.search(r"/(?:threads|topic|post)/", path)
        )

        # Filter query parameters
        query_pairs = parse_qsl(parsed.query, keep_blank_values=False)
        filtered_query = []
        for k, v in query_pairs:
            k_lower = k.lower()
            if k_lower in cls.TRACKING_PARAMS:
                continue
            if is_thread_url and k_lower in cls.FORUM_POST_ANCHOR_PARAMS:
                continue
            filtered_query.append((k, v))

        filtered_query.sort()
        new_query = urlencode(filtered_query)

        canonical_url = urlunparse((scheme, netloc, path, "", new_query, ""))
        url_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        
        return canonical_url, netloc, url_hash

    @staticmethod
    def hash_content_exact(text: str) -> str:
        """Computes SHA-256 fingerprint of normalized text content."""
        normalized = " ".join(text.split()).strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    @staticmethod
    def compute_simhash(text: str, hash_bits: int = 64) -> int:
        """
        Computes 64-bit SimHash of text to detect near-duplicate documents.
        """
        tokens = re.findall(r"\w+", text.lower())
        if not tokens:
            return 0

        v = [0] * hash_bits
        for token in tokens:
            h = int(hashlib.md5(token.encode("utf-8")).hexdigest()[:16], 16)
            for i in range(hash_bits):
                bitmask = 1 << i
                if h & bitmask:
                    v[i] += 1
                else:
                    v[i] -= 1

        fingerprint = 0
        for i in range(hash_bits):
            if v[i] > 0:
                fingerprint |= (1 << i)

        return fingerprint

    @staticmethod
    def hamming_distance(simhash1: int, simhash2: int) -> int:
        """Counts differing bits between two SimHashes."""
        return bin(simhash1 ^ simhash2).count("1")


class BloomFilter:
    """
    In-memory Bloom Filter for probabilistic URL Seen checks.
    """
    def __init__(self, size: int = 1_000_000, num_hashes: int = 5):
        self.size = size
        self.num_hashes = num_hashes
        self.bit_array = bytearray((size + 7) // 8)

    def _get_hashes(self, item: str) -> List[int]:
        hashes = []
        md5_digest = hashlib.md5(item.encode("utf-8")).digest()
        sha_digest = hashlib.sha1(item.encode("utf-8")).digest()
        
        h1 = int.from_bytes(md5_digest[:8], byteorder="big")
        h2 = int.from_bytes(sha_digest[:8], byteorder="big")
        
        for i in range(self.num_hashes):
            combined = (h1 + i * h2) % self.size
            hashes.append(combined)
        return hashes

    def add(self, item: str):
        for h in self._get_hashes(item):
            byte_idx = h // 8
            bit_idx = h % 8
            self.bit_array[byte_idx] |= (1 << bit_idx)

    def contains(self, item: str) -> bool:
        for h in self._get_hashes(item):
            byte_idx = h // 8
            bit_idx = h % 8
            if not (self.bit_array[byte_idx] & (1 << bit_idx)):
                return False
        return True
