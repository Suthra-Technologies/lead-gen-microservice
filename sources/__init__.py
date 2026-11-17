"""
Sources Package
Exports BaseSourceAdapter, specialized adapters, and helper functions to retrieve
the appropriate adapter for any domain or URL.
"""

from typing import Dict, Optional
from urllib.parse import urlparse

from sources.base_adapter import BaseSourceAdapter
from sources.solarpaneltalk import SolarPanelTalkAdapter
from sources.diysolarforum import DIYSolarForumAdapter
from sources.reddit import RedditAdapter
from sources.houzz import HouzzAdapter
from sources.generic_adapter import GenericSourceAdapter
from sources.google_places import GooglePlacesSource
from source_registry import USA_SOURCE_REGISTRY

_ADAPTER_INSTANCES: Dict[str, BaseSourceAdapter] = {
    "solarpaneltalk.com": SolarPanelTalkAdapter(),
    "diysolarforum.com": DIYSolarForumAdapter(),
    "reddit.com": RedditAdapter(),
    "houzz.com": HouzzAdapter(),
}


def get_adapter_for_domain(domain: str) -> BaseSourceAdapter:
    """Returns the registered adapter for the domain, or GenericSourceAdapter if none is specialized."""
    domain_clean = domain.lower().replace("www.", "").replace("old.", "").replace("new.", "")
    if domain_clean in _ADAPTER_INSTANCES:
        return _ADAPTER_INSTANCES[domain_clean]

    for key, adapter in _ADAPTER_INSTANCES.items():
        if domain_clean.endswith("." + key):
            return adapter

    # Find source in USA_SOURCE_REGISTRY
    source_meta = None
    for s in USA_SOURCE_REGISTRY.values():
        if s.domain.lower().replace("www.", "") == domain_clean:
            source_meta = s
            break

    if source_meta:
        generic = GenericSourceAdapter(source_meta.source_id, source_meta.name, source_meta.domain)
        _ADAPTER_INSTANCES[domain_clean] = generic
        return generic

    # Fallback generic
    generic = GenericSourceAdapter(domain_clean, domain_clean, domain_clean)
    _ADAPTER_INSTANCES[domain_clean] = generic
    return generic


def get_adapter_for_url(url: str) -> BaseSourceAdapter:
    """Extracts domain from URL and returns corresponding adapter."""
    domain = urlparse(url).netloc
    return get_adapter_for_domain(domain)


__all__ = [
    "BaseSourceAdapter",
    "SolarPanelTalkAdapter",
    "DIYSolarForumAdapter",
    "RedditAdapter",
    "HouzzAdapter",
    "GenericSourceAdapter",
    "GooglePlacesSource",
    "get_adapter_for_domain",
    "get_adapter_for_url",
]

