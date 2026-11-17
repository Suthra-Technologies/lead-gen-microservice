"""
Google Places Source Unit & Integration Test Suite
Validates Google Places API client configuration, address component parsing,
contractor profile ingestion, business contact enrichment, and installer queries.
"""

import unittest
from storage import CrawlerStorage
from sources.google_places import GooglePlacesSource


class TestGooglePlacesSource(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(":memory:")
        self.client = GooglePlacesSource(api_key="AIzaFakeTestKeyForUnitTests1234567890", storage=self.storage)

    def test_configuration_detection(self):
        self.assertTrue(self.client.is_configured())

        empty_client = GooglePlacesSource(api_key="", storage=self.storage)
        self.assertFalse(empty_client.is_configured())

        placeholder_client = GooglePlacesSource(api_key="your_google_maps_api_key_here", storage=self.storage)
        self.assertFalse(placeholder_client.is_configured())

    def test_address_components_parsing(self):
        components = [
            {"longText": "1250", "shortText": "1250", "types": ["street_number"]},
            {"longText": "South Capital of Texas Highway", "shortText": "S Capital of Texas Hwy", "types": ["route"]},
            {"longText": "Austin", "shortText": "Austin", "types": ["locality", "political"]},
            {"longText": "Texas", "shortText": "TX", "types": ["administrative_area_level_1", "political"]},
            {"longText": "78746", "shortText": "78746", "types": ["postal_code"]}
        ]
        city, state, zip_code = self.client._parse_address_components(components)
        self.assertEqual(city, "Austin")
        self.assertEqual(state, "TX")
        self.assertEqual(zip_code, "78746")

    def test_ingest_and_enrich_creates_installers_and_contacts(self):
        mock_places = [
            {
                "place_id": "ChIJ_test_austin_solar_001",
                "company_name": "Austin Sunshine Solar",
                "formatted_address": "100 Congress Ave, Austin, TX 78701, USA",
                "city": "Austin",
                "state": "TX",
                "zip_code": "78701",
                "phone_number": "(512) 555-0199",
                "website_url": "https://www.austinsunshinesolar.com",
                "google_maps_url": "https://maps.google.com/?cid=123456789",
                "rating": 4.9,
                "user_ratings_total": 85,
                "business_status": "OPERATIONAL",
                "primary_type": "Solar energy contractor",
                "search_query": "residential solar in Austin TX"
            },
            {
                "place_id": "ChIJ_test_phoenix_solar_002",
                "company_name": "Desert Sun Power",
                "formatted_address": "200 E Camelback Rd, Phoenix, AZ 85012, USA",
                "city": "Phoenix",
                "state": "AZ",
                "zip_code": "85012",
                "phone_number": "(602) 555-0188",
                "website_url": "https://www.desertsunpower.com",
                "google_maps_url": "https://maps.google.com/?cid=987654321",
                "rating": 5.0,
                "user_ratings_total": 120,
                "business_status": "OPERATIONAL",
                "primary_type": "Solar energy contractor",
                "search_query": "solar installer in Phoenix AZ"
            }
        ]

        counts = self.client.ingest_and_enrich(mock_places)
        self.assertEqual(counts["installers_saved"], 2)
        self.assertEqual(counts["phones_added"], 2)
        self.assertEqual(counts["websites_added"], 2)

        # Check database records
        installers = self.storage.get_solar_installers()
        self.assertEqual(len(installers), 2)
        self.assertEqual(installers[0]["company_name"], "Desert Sun Power")  # Higher rating 5.0 first

        # Check state filter
        tx_installers = self.storage.get_solar_installers(state="TX")
        self.assertEqual(len(tx_installers), 1)
        self.assertEqual(tx_installers[0]["company_name"], "Austin Sunshine Solar")

        # Check stats aggregation
        stats = self.storage.get_installer_stats()
        self.assertEqual(stats["total_installers"], 2)
        self.assertEqual(stats["with_phones"], 2)
        self.assertEqual(stats["with_websites"], 2)
        self.assertEqual(stats["by_state"]["TX"], 1)
        self.assertEqual(stats["by_state"]["AZ"], 1)

        # Check business contacts table enrichment
        contacts = self.storage.get_business_contacts()
        contact_values = {c["contact_value"] for c in contacts}
        self.assertIn("(512) 555-0199", contact_values)
        self.assertIn("(602) 555-0188", contact_values)
        self.assertIn("https://www.austinsunshinesolar.com", contact_values)
        self.assertIn("https://www.desertsunpower.com", contact_values)


if __name__ == "__main__":
    unittest.main()

