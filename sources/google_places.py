"""
Google Places API Source Module
Discovers and extracts verified residential solar contractors, installation companies,
and service providers using the Google Places API (New).
Extracts business name, phone number, physical address, city, state, zip code,
star rating, review count, and official website URI. Enriches both the
solar_installers registry and business_contacts auditable log.
"""

import os
import re
import logging
from typing import Dict, List, Optional, Any, Tuple
import httpx

from config import GOOGLE_MAPS_API_KEY, DEFAULT_PLACES_QUERIES
from storage import CrawlerStorage
from models import SolarPlaceLead, BusinessContact, BusinessContactType

logger = logging.getLogger("Crawler.GooglePlaces")


class GooglePlacesSource:
    SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"
    PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places"

    FIELD_MASK = (
        "places.id,"
        "places.displayName,"
        "places.formattedAddress,"
        "places.addressComponents,"
        "places.nationalPhoneNumber,"
        "places.internationalPhoneNumber,"
        "places.websiteUri,"
        "places.googleMapsUri,"
        "places.rating,"
        "places.userRatingCount,"
        "places.businessStatus,"
        "places.primaryTypeDisplayName"
    )

    def __init__(self, api_key: Optional[str] = None, storage: Optional[CrawlerStorage] = None):
        self.api_key = (api_key if api_key is not None else GOOGLE_MAPS_API_KEY or "").strip()
        self.storage = storage or CrawlerStorage()
        self.client = httpx.Client(timeout=15.0)

    def is_configured(self) -> bool:
        """Checks if a valid Google Maps API Key is configured."""
        return bool(self.api_key and len(self.api_key) >= 20 and not self.api_key.startswith("your_"))

    def _parse_address_components(self, components: List[Dict[str, Any]]) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        """Extracts city, 2-letter state code, and postal code from Google address components."""
        city = None
        state = None
        zip_code = None

        for comp in components or []:
            types = comp.get("types", [])
            if "locality" in types:
                city = comp.get("longText") or comp.get("shortText")
            elif "administrative_area_level_1" in types:
                state = comp.get("shortText") or comp.get("longText")
            elif "postal_code" in types:
                zip_code = comp.get("shortText") or comp.get("longText")

        return city, state, zip_code

    def search_places(self, query: str, page_size: int = 20) -> List[Dict[str, Any]]:
        """
        Executes text query against Places API (New) endpoint.
        Returns list of normalized installer records.
        """
        if not self.is_configured():
            logger.warning("[Google Places] GOOGLE_MAPS_API_KEY is not configured or placeholder. Skipping.")
            return []

        headers = {
            "Content-Type": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "X-Goog-FieldMask": self.FIELD_MASK
        }
        payload = {
            "textQuery": query,
            "pageSize": min(page_size, 20)
        }

        try:
            logger.info(f"[Google Places] Querying: '{query}' (pageSize={payload['pageSize']})...")
            response = self.client.post(self.SEARCH_TEXT_URL, headers=headers, json=payload)
            
            if response.status_code != 200:
                logger.error(f"[Google Places Error] HTTP {response.status_code}: {response.text[:300]}")
                return []

            data = response.json()
            raw_places = data.get("places", [])
            logger.info(f"[Google Places Success] Discovered {len(raw_places)} places for query '{query}'.")

            results = []
            for item in raw_places:
                place_id = item.get("id")
                if not place_id:
                    continue

                display_name = item.get("displayName", {})
                company_name = display_name.get("text", "Unknown Solar Contractor") if isinstance(display_name, dict) else str(display_name)
                formatted_address = item.get("formattedAddress")
                address_components = item.get("addressComponents", [])
                city, state, zip_code = self._parse_address_components(address_components)

                phone = item.get("nationalPhoneNumber")
                website = item.get("websiteUri")
                google_maps_url = item.get("googleMapsUri")
                rating = float(item.get("rating", 0.0)) if item.get("rating") is not None else None
                user_ratings_total = int(item.get("userRatingCount", 0)) if item.get("userRatingCount") is not None else None
                business_status = item.get("businessStatus", "OPERATIONAL")
                
                type_obj = item.get("primaryTypeDisplayName", {})
                primary_type = type_obj.get("text") if isinstance(type_obj, dict) else None

                # Business categorization to separate installers from cleaners, screens, and roofing
                classification = self.classify_business(company_name, primary_type, query)

                record = {
                    "place_id": place_id,
                    "company_name": company_name,
                    "formatted_address": formatted_address,
                    "city": city,
                    "state": state,
                    "zip_code": zip_code,
                    "phone_number": phone,
                    "website_url": website,
                    "google_maps_url": google_maps_url,
                    "rating": rating,
                    "user_ratings_total": user_ratings_total,
                    "business_status": business_status,
                    "primary_type": primary_type,
                    "search_query": query,
                    "business_category": classification["business_category"],
                    "services_offered": classification["services_offered"],
                    "residential_service": classification["residential_service"],
                    "commercial_service": classification["commercial_service"],
                    "license_verification_status": "UNVERIFIED",
                    "verification_source": "places_discovered"
                }
                results.append(record)

            return results

        except Exception as e:
            logger.error(f"[Google Places Exception] {query}: {e}", exc_info=True)
            return []

    @staticmethod
    def classify_business(name: str, primary_type: Optional[str] = None, query: str = "") -> Dict[str, Any]:
        """
        Classifies discovered businesses into distinct categories to prevent non-installers
        (solar cleaners, sunscreens, roofing-only, general tinting) from being treated
        as residential installation contractors.
        """
        combined = f"{name} {primary_type or ''} {query}".lower()

        # 1. Non-solar screens / window film / sun control
        if any(term in combined for term in ["screen", "screens", "sunscreen", "solar screen", "window tint", "tinting", "shading"]):
            return {
                "business_category": "Non-Solar Screens/Lighting",
                "residential_service": False,
                "commercial_service": False,
                "services_offered": "Window tinting, sun screens, shading"
            }

        # 2. Solar panel cleaning / washing
        if any(term in combined for term in ["cleaning", "clean", "wash", "panel cleaning", "pressure wash", "bird proofing"]):
            return {
                "business_category": "Cleaning Service",
                "residential_service": True,
                "commercial_service": False,
                "services_offered": "Solar panel cleaning, bird proofing, washing"
            }

        # 3. Solar panel repair / maintenance
        if any(term in combined for term in ["repair", "service and repair", "inverter repair", "troubleshooting", "maintenance"]):
            return {
                "business_category": "Repair Service",
                "residential_service": True,
                "commercial_service": False,
                "services_offered": "Inverter replacement, wiring repair, solar maintenance"
            }

        # 4. Solar battery / storage specialist
        if any(term in combined for term in ["battery", "storage systems", "powerwall", "enphase storage"]):
            return {
                "business_category": "Battery Specialist",
                "residential_service": True,
                "commercial_service": False,
                "services_offered": "Home battery backup, energy storage systems"
            }

        # 5. Solar roofing
        if any(term in combined for term in ["roofing and solar", "solar roofing", "roof and solar"]):
            return {
                "business_category": "Roofing/Solar",
                "residential_service": True,
                "commercial_service": False,
                "services_offered": "Rooftop replacement, integrated solar roofing"
            }

        # 6. Commercial solar only
        if any(term in combined for term in ["commercial solar", "utility scale", "industrial solar", "solar farm"]):
            return {
                "business_category": "Commercial Installer",
                "residential_service": False,
                "commercial_service": True,
                "services_offered": "Commercial and utility-scale solar development"
            }

        # Default: Residential solar installation
        return {
            "business_category": "Installer",
            "residential_service": True,
            "commercial_service": False,
            "services_offered": "Turnkey residential solar PV installation"
        }

    def ingest_and_enrich(self, places: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Saves discovered places to solar_installers table and enriches business_contacts.
        Returns counts of ingested installers, phones, and websites.
        """
        ingested_count = 0
        phones_count = 0
        websites_count = 0

        for p in places:
            # 1. Save to solar_installers
            saved = self.storage.save_solar_installer(p)
            if saved:
                ingested_count += 1

            company = p.get("company_name", "Solar Contractor")
            addr = p.get("formatted_address", "")
            g_url = p.get("google_maps_url") or f"https://www.google.com/maps/place/?q=place_id:{p.get('place_id')}"

            # 2. Enrich verified business phone
            phone = p.get("phone_number")
            if phone:
                try:
                    bc_phone = BusinessContact(
                        source_name="Google Places API",
                        source_url=g_url,
                        company_name=company,
                        contact_type=BusinessContactType.BUSINESS_PHONE.value,
                        contact_value=phone,
                        source_permalink=g_url,
                        evidence_snippet=f"Verified business phone for {company} located at {addr} (Rating: {p.get('rating') or 'N/A'})",
                        verification_status="VERIFIED_GOOGLE_MAPS"
                    )
                    if self.storage.save_business_contact(bc_phone.model_dump()):
                        phones_count += 1
                except Exception as e:
                    logger.debug(f"[Google Places Contact Error] Phone {phone}: {e}")

            # 3. Enrich verified company website
            website = p.get("website_url")
            if website:
                try:
                    bc_web = BusinessContact(
                        source_name="Google Places API",
                        source_url=website,
                        company_name=company,
                        contact_type="WEBSITE",
                        contact_value=website,
                        source_permalink=g_url,
                        evidence_snippet=f"Official website for {company} in {p.get('city') or 'USA'}, {p.get('state') or ''}",
                        verification_status="VERIFIED_GOOGLE_MAPS"
                    )
                    if self.storage.save_business_contact(bc_web.model_dump()):
                        websites_count += 1
                except Exception as e:
                    logger.debug(f"[Google Places Contact Error] Website {website}: {e}")

        logger.info(
            f"[Google Places Ingestion] Saved {ingested_count} installers | "
            f"{phones_count} new business phones | {websites_count} new company websites."
        )
        return {
            "installers_saved": ingested_count,
            "phones_added": phones_count,
            "websites_added": websites_count
        }

    def run_discovery(
        self,
        queries: Optional[List[str]] = None,
        max_per_query: int = 20
    ) -> Dict[str, Any]:
        """
        Executes discovery batch across specified queries or default US solar metros.
        """
        target_queries = queries or DEFAULT_PLACES_QUERIES
        all_results = []
        query_stats = {}

        logger.info(f"[Google Places Batch] Starting discovery run across {len(target_queries)} targeted metro queries...")
        for q in target_queries:
            places = self.search_places(q, page_size=max_per_query)
            counts = self.ingest_and_enrich(places)
            all_results.extend(places)
            query_stats[q] = {
                "discovered": len(places),
                **counts
            }

        # Update source metrics in source registry
        self.storage.update_source_metrics(
            domain="places.googleapis.com",
            pages_downloaded=len(target_queries),
            leads_found=len(all_results),
            posts_discovered=len(all_results),
            duplicates=0
        )

        export_path = self.storage.export_installers_to_json()
        stats = self.storage.get_installer_stats()

        return {
            "total_discovered": len(all_results),
            "installer_stats": stats,
            "export_file": str(export_path) if export_path else None,
            "query_stats": query_stats
        }
