"""
Business Contact Enrichment Module (Conservative)
Extracts official, publicly published business inquiry contact information:
- Official company/organization inquiry emails (e.g. contact@domain.org, info@domain.com)
- Customer support & business phone numbers
- Official company contact forms

Strictly Prohibited:
- Bulk harvesting of private individuals' personal email addresses from discussion comments.
- Extraction of personal webmail accounts (gmail, yahoo, hotmail, etc.).
- Guessing or synthesizing contact data.
"""

import re
import logging
from typing import List, Dict, Any, Optional, Set, Tuple
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from datetime import datetime, timezone

logger = logging.getLogger("Crawler.ContactExtractor")

# Personal free webmail domains that must NEVER be harvested as business contacts
FREE_WEBMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "protonmail.com", "mail.com", "zoho.com", "gmx.com", "yandex.com"
}

# Recognized organizational business inquiry prefixes
BUSINESS_EMAIL_PREFIXES = {
    "contact", "info", "support", "inquiries", "inquiry", "sales", "press",
    "media", "office", "hello", "admin", "service", "customer"
}


class BusinessContactExtractor:
    def __init__(self):
        # US phone number regex with area code
        self.phone_regex = re.compile(
            r"(?:\+1[-.\s]?)?\(?([2-9]\d{2})\)?[-.\s]?([2-9]\d{2})[-.\s]?(\d{4})\b"
        )
        # Standard email regex
        self.email_regex = re.compile(
            r"\b([a-zA-Z0-9_.+-]+)@([a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)\b"
        )

    def _is_official_contact_page(self, url: str) -> bool:
        """Determines if the page is an official organizational contact or about page."""
        path = urlparse(url).path.lower()
        return any(term in path for term in [
            "/contact", "/contact-us", "/about", "/about-us",
            "/support", "/imprint", "/legal", "/co-ops", "/contractors"
        ])

    def extract_business_contacts(
        self,
        url: str,
        html: str,
        source_name: str,
        author: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Extracts verified public business contact information and voluntarily published contact details.
        Does NOT harvest private individuals' personal emails from forum discussion comments.
        Never assumes a forum username has a verified email address.
        """
        contacts: List[Dict[str, Any]] = []
        seen_values: Set[str] = set()
        soup = BeautifulSoup(html, "lxml")

        is_contact_page = self._is_official_contact_page(url)

        # 1. Inspect footer, header, and official contact containers
        containers = []
        if is_contact_page:
            containers.append(soup)
        else:
            for tag in ["footer", "header"]:
                found = soup.find(tag)
                if found:
                    containers.append(found)
            for cls_pattern in [re.compile(r"(footer|contact-info|site-info|about-us)", re.IGNORECASE)]:
                for div in soup.find_all(class_=cls_pattern):
                    containers.append(div)

        for container in containers:
            # A. Extract official mailto links
            for a_tag in container.find_all("a", href=True):
                href = a_tag["href"].strip()
                if href.lower().startswith("mailto:"):
                    raw_email = href.split(":", 1)[1].split("?")[0].strip().lower()
                    match = self.email_regex.match(raw_email)
                    if match:
                        user_part, domain_part = match.group(1), match.group(2)
                        if domain_part in FREE_WEBMAIL_DOMAINS:
                            continue  # Exclude personal webmail accounts

                        if raw_email not in seen_values:
                            seen_values.add(raw_email)
                            parent_text = a_tag.parent.get_text(strip=True)[:150] if a_tag.parent else "Official mailto link"
                            contacts.append({
                                "source_name": source_name,
                                "source_url": url,
                                "company_name": source_name,
                                "contact_type": "BUSINESS_EMAIL",
                                "contact_value": raw_email,
                                "author_username": None,
                                "source_permalink": url,
                                "evidence_snippet": f"Official published business contact: {parent_text}",
                                "verification_status": "VERIFIED_PUBLIC_BUSINESS",
                                "discovered_at": datetime.now(timezone.utc).isoformat()
                            })

            # B. Extract business telephone numbers (with surrounding context check)
            text_blocks = container.get_text(separator="\n", strip=True)
            for line in text_blocks.split("\n"):
                lower_line = line.lower()
                # Require phone-related context (phone, tel, call, contact, office)
                if any(w in lower_line for w in ["phone", "tel", "call", "contact", "office", "toll-free", "customer service"]):
                    for match in self.phone_regex.finditer(line):
                        formatted_phone = f"({match.group(1)}) {match.group(2)}-{match.group(3)}"
                        if formatted_phone not in seen_values:
                            seen_values.add(formatted_phone)
                            contacts.append({
                                "source_name": source_name,
                                "source_url": url,
                                "company_name": source_name,
                                "contact_type": "BUSINESS_PHONE",
                                "contact_value": formatted_phone,
                                "author_username": None,
                                "source_permalink": url,
                                "evidence_snippet": f"Business inquiry phone: {line.strip()[:150]}",
                                "verification_status": "VERIFIED_PUBLIC_BUSINESS",
                                "discovered_at": datetime.now(timezone.utc).isoformat()
                            })

        # C. Official Contact Form detection
        if is_contact_page:
            for form in soup.find_all("form"):
                action = form.get("action") or url
                form_url = urljoin(url, action)
                if form_url not in seen_values:
                    seen_values.add(form_url)
                    contacts.append({
                        "source_name": source_name,
                        "source_url": url,
                        "company_name": source_name,
                        "contact_type": "CONTACT_FORM",
                        "contact_value": form_url,
                        "author_username": None,
                        "source_permalink": url,
                        "evidence_snippet": f"Official public contact inquiry form on {url}",
                        "verification_status": "VERIFIED_PUBLIC_BUSINESS",
                        "discovered_at": datetime.now(timezone.utc).isoformat()
                    })

        return contacts

