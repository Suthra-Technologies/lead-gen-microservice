"""
Unit and Integration Test Suite for Production Solar Lead Intelligence Engine
Validates the Industry-Standard Gap Analysis (V2) requirements:
1. Solar Opportunity Estimator (usage, sizing, offset)
2. Calibrated 100-Point Contractor Intelligence & Matching Engine
3. Data Quality & Integrity Validator (7 checks)
4. Multi-Dimensional Qualification Model & Standardized Formula Q (0.30I + 0.20R + 0.15L + 0.15F + 0.15E + 0.05B)
5. Hard Disqualification Gates (commercial, spam, negative intent, hypothetical)
6. Evidence Ledger (lead_evidence) & Provenance
7. Granular Intent Events (intent_events) & 3-Timestamp Intelligence
8. Human-in-the-Loop Review Queue (review_queue)
9. Privacy Suppression & Deletion Processing
10. Atomic Concurrency-Safe Deduplication
"""

import unittest
from opportunity_estimator import SolarOpportunityEstimator
from contractor_matcher import ContractorMatcher
from data_quality import LeadDataQualityValidator
from parser import ContentParser
from storage import CrawlerStorage
from models import LeadLifecycleStatus, QualificationStatus, ValidationStatus


class TestSolarOpportunityEstimator(unittest.TestCase):
    def test_opportunity_estimation_from_bill(self):
        # In California ($0.32/kWh), $320 bill = 1000 kWh/mo = 12000 kWh/yr -> ~8.57 kW system
        opp = SolarOpportunityEstimator.estimate_opportunity(electricity_bill=320.0, state="CA")
        self.assertIsNotNone(opp)
        self.assertEqual(opp["estimated_monthly_usage_kwh"], 1000.0)
        self.assertEqual(opp["estimated_annual_usage_kwh"], 12000.0)
        self.assertAlmostEqual(opp["estimated_system_size_kw"], 8.57, places=2)
        self.assertEqual(opp["estimated_solar_offset_percent"], 100.0)

    def test_opportunity_estimation_with_reported_kw(self):
        # Reported 10 kW system with $160 bill in TX ($0.15/kWh)
        # Required size ~ 9.14 kW -> 10 kW yields ~ 109.4% solar offset
        opp = SolarOpportunityEstimator.estimate_opportunity(
            electricity_bill=160.0,
            state="TX",
            reported_kw=10.0
        )
        self.assertAlmostEqual(opp["estimated_system_size_kw"], 9.14, places=2)
        self.assertAlmostEqual(opp["estimated_solar_offset_percent"], 109.4, places=1)

    def test_opportunity_estimation_reported_kw_only(self):
        # Stated 8 kW with no bill
        opp = SolarOpportunityEstimator.estimate_opportunity(reported_kw=8.0)
        self.assertEqual(opp["estimated_system_size_kw"], 8.0)
        self.assertEqual(opp["estimated_annual_usage_kwh"], 11200.0)
        self.assertEqual(opp["estimated_solar_offset_percent"], 100.0)

    def test_opportunity_estimation_empty(self):
        opp = SolarOpportunityEstimator.estimate_opportunity(electricity_bill=None, state=None)
        self.assertIsNone(opp["estimated_system_size_kw"])
        self.assertIsNone(opp["estimated_monthly_usage_kwh"])


class TestContractorMatcher(unittest.TestCase):
    def test_calibrated_100_point_scoring(self):
        lead = {
            "state": "CA",
            "city": "Fresno",
            "buyer_stage": "COMPARING_QUOTES",
            "secondary_classifications": ["STORAGE_ADDITION"]
        }
        contractor = {
            "place_id": "plc_123",
            "company_name": "Fresno Solar Pros",
            "city": "Fresno",
            "state": "CA",
            "primary_type": "solar_installation_contractor",
            "phone_number": "(559) 555-0199",
            "website_url": "https://fresnosolarpros.com",
            "rating": 4.9,
            "business_status": "OPERATIONAL"
        }
        score, reasons = ContractorMatcher.calculate_match_score(lead, contractor)
        # State (15) + City (15) + Residential (20) + Capability (15) + Verification (15) + Rating (10) + Stage (5) + Operational (5) = 100
        self.assertEqual(score, 100)
        self.assertTrue(any("State coverage" in r for r in reasons))
        self.assertTrue(any("Direct metro" in r for r in reasons))
        self.assertTrue(any("Dual verified" in r for r in reasons))

    def test_state_mismatch_hard_gate(self):
        lead = {"state": "CA", "city": "San Diego"}
        contractor = {"state": "TX", "city": "Dallas", "business_status": "OPERATIONAL"}
        score, reasons = ContractorMatcher.calculate_match_score(lead, contractor)
        self.assertEqual(score, 0)
        self.assertTrue(any("Disqualified: State mismatch" in r for r in reasons))

    def test_match_lead_ranking(self):
        lead = {"state": "TX", "city": "Austin", "buyer_stage": "SEEKING_QUOTES"}
        contractors = [
            {"place_id": "c1", "company_name": "Austin Clean Energy", "city": "Austin", "state": "TX", "primary_type": "solar", "phone_number": "512-111", "website_url": "http://a.com", "rating": 4.9},
            {"place_id": "c2", "company_name": "Dallas Solar Co", "city": "Dallas", "state": "TX", "primary_type": "solar", "rating": 4.2},
            {"place_id": "c3", "company_name": "Miami Solar", "city": "Miami", "state": "FL", "primary_type": "solar", "rating": 4.5}
        ]
        matches = ContractorMatcher.match_lead(lead, contractors, min_score=40)
        self.assertEqual(len(matches), 2)  # TX contractors match; FL is filtered out (0 score)
        self.assertEqual(matches[0]["place_id"], "c1")
        self.assertGreater(matches[0]["match_score"], matches[1]["match_score"])


class TestDataQualityValidator(unittest.TestCase):
    def test_valid_lead(self):
        lead = {
            "post_url": "https://solarpaneltalk.com/forum/solar/123",
            "evidence_snippets": ["I need a 8kW solar quote for my home in Austin TX"],
            "lead_score": 8.0,
            "matched_keywords": ["need solar quote"],
            "system_size_kw": 8.0,
            "electricity_bill": 220.0,
            "zip_code": "78701",
            "state": "TX",
            "classification_type": "RESIDENTIAL_NEW_BUYER"
        }
        res = LeadDataQualityValidator.validate_lead(lead)
        self.assertEqual(res["status"], ValidationStatus.PASSED.value)
        self.assertEqual(len(res["flags"]), 0)

    def test_invalid_lead_flags(self):
        lead = {
            "post_url": "invalid_url",
            "evidence_snippets": [],
            "lead_score": 10.0,
            "matched_keywords": [],
            "system_size_kw": 500.0,
            "electricity_bill": 15000.0,
            "zip_code": "ABCDE",
            "state": "ZZ",
            "classification_type": "COMMERCIAL"
        }
        res = LeadDataQualityValidator.validate_lead(lead)
        self.assertEqual(res["status"], ValidationStatus.FAILED.value)
        self.assertTrue(any("evidence snippets" in f for f in res["flags"]))
        self.assertTrue(any("Electric bill" in f for f in res["flags"]))
        self.assertTrue(any("ZIP" in f for f in res["flags"]))
        self.assertTrue(any("state" in f for f in res["flags"]))


class TestMultiDimensionalQualificationAndNLP(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(":memory:")
        self.parser = ContentParser(storage=self.storage)

    def test_standardized_formula_q(self):
        # Q = 0.30*I + 0.20*R + 0.15*L + 0.15*F + 0.15*E + 0.05*B
        # I=85, R=100, L=95, F=100, E=70, B=95 (COMPARING_QUOTES)
        # Q = 0.30(85) + 0.20(100) + 0.15(95) + 0.15(100) + 0.15(70) + 0.05(95)
        # Q = 25.5 + 20.0 + 14.25 + 15.0 + 10.5 + 4.75 = 90.0
        dims = self.parser.calculate_qualification_dimensions(
            score=8.5,
            classification="RESIDENTIAL_NEW_BUYER",
            title="Comparing quotes for rooftop installation",
            text="Single family residential home in Phoenix AZ 85001. Reviewing proposals.",
            evidence_snippets=["Comparing solar quotes from two installers", "Need recommendation"],
            state="AZ",
            zip_code="85001",
            intent_age_days=3,
            buyer_stage="COMPARING_QUOTES"
        )
        self.assertAlmostEqual(dims["composite_qualification_score"], 90.0, places=1)
        self.assertFalse(dims["hard_disqualified"])

    def test_hard_disqualification_commercial(self):
        dims = self.parser.calculate_qualification_dimensions(
            score=10.0,
            classification="COMMERCIAL_SOLAR",
            title="500kW Warehouse solar array",
            text="Installing commercial array on warehouse property",
            evidence_snippets=["Need 500kW quote"],
            state="CA",
            zip_code="90210",
            intent_age_days=1
        )
        self.assertEqual(dims["composite_qualification_score"], 0.0)
        self.assertTrue(dims["hard_disqualified"])
        self.assertIn("Excluded non-buyer category", dims["disqualification_reason"])

    def test_hard_disqualification_hypothetical(self):
        dims = self.parser.calculate_qualification_dimensions(
            score=2.0,
            classification="RESIDENTIAL_NEW_BUYER",
            title="Curious about solar in 10 years",
            text="Hypothetically speaking, for a school project, how much does solar cost?",
            evidence_snippets=["Hypothetical inquiry"],
            state="TX",
            zip_code=None,
            intent_age_days=5,
            is_hypothetical=True
        )
        self.assertEqual(dims["composite_qualification_score"], 0.0)
        self.assertTrue(dims["hard_disqualified"])
        self.assertIn("hypothetical", dims["disqualification_reason"].lower())

    def test_negation_detection(self):
        text = "I am not interested in solar. Definitely not looking for an installer."
        score, keywords, evidence = self.parser.score_buyer_intent("RESIDENTIAL_NEW_BUYER", "Not buying", text)
        self.assertLess(score, 3.0)

    def test_genuine_intent_high_score(self):
        text = "Looking for a solar installer for our home in San Diego CA. Electric bill is $280/mo."
        score, keywords, evidence = self.parser.score_buyer_intent("RESIDENTIAL_NEW_BUYER", "Need solar installer", text)
        self.assertGreater(score, 6.0)
        self.assertGreater(len(evidence), 0)


class TestLeadEvidenceProvenanceAndIntentEvents(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(":memory:")
        self.parser = ContentParser(storage=self.storage)

    def test_evidence_ledger_persistence(self):
        lead_dict = {
            "source_name": "SolarPanelTalk",
            "source_url": "solarpaneltalk.com",
            "post_url": "https://www.solarpaneltalk.com/forum/test_evidence_provenance_1",
            "author": "CleanEnergyHome",
            "post_title": "Comparing quotes from three local installers",
            "intent_snippet": "We have an 8.5 kW quote and our bill is $320/month in San Diego CA",
            "lead_score": 8.0,
            "lead_category": "HOT_BUYER",
            "lead_segment": "NEW_BUYER",
            "classification_type": "RESIDENTIAL_QUOTE_SEEKER",
            "primary_classification": "RESIDENTIAL_QUOTE_SEEKER",
            "lead_freshness": "0-7 days",
            "published_at": "2026-11-16",
            "state": "CA",
            "city": "San Diego",
            "structured_evidence": [
                {
                    "evidence_text": "Comparing quotes from three local installers in San Diego",
                    "evidence_type": "QUOTE_COMPARISON",
                    "evidence_polarity": "POSITIVE",
                    "published_at": "2026-11-16",
                    "extraction_method": "RULE_REGEX_V2",
                    "model_version": "2.0.0",
                    "confidence": 0.95,
                    "score_contribution": 4.0
                },
                {
                    "evidence_text": "Pain point: $320/month electric bill",
                    "evidence_type": "BILL_PAIN_POINT",
                    "evidence_polarity": "POSITIVE",
                    "published_at": "2026-11-16",
                    "extraction_method": "RULE_REGEX_V2",
                    "model_version": "2.0.0",
                    "confidence": 0.90,
                    "score_contribution": 3.0
                }
            ]
        }
        self.storage.save_solar_lead(lead_dict)
        leads = self.storage.get_recent_leads(limit=5)
        target = next((l for l in leads if l["post_url"] == lead_dict["post_url"]), None)
        self.assertIsNotNone(target)
        lead_id = target["id"]

        # Verify lead_evidence ledger
        evidence_records = self.storage.get_lead_evidence(lead_id)
        self.assertEqual(len(evidence_records), 2)
        self.assertEqual(evidence_records[0]["evidence_type"], "QUOTE_COMPARISON")
        self.assertEqual(evidence_records[0]["model_version"], "2.0.0")

        # Verify intent_events table
        intent_events = self.storage.get_intent_events(lead_id)
        self.assertEqual(len(intent_events), 2)
        self.assertEqual(intent_events[0]["intent_polarity"], "POSITIVE")


class TestHumanReviewQueueAndCompliance(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(":memory:")

    def test_review_queue_routing_and_decision(self):
        lead_dict = {
            "source_name": "Reddit r/solar",
            "source_url": "reddit.com",
            "post_url": "https://reddit.com/r/solar/test_review_routing_1",
            "author": "UncertainBuyer",
            "post_title": "Thinking about solar maybe next year",
            "intent_snippet": "Thinking about solar maybe next year",
            "lead_score": 3.0,
            "lead_category": "RESEARCHING",
            "lead_segment": "NEW_BUYER",
            "classification_type": "RESIDENTIAL_NEW_BUYER",
            "needs_human_review": True,
            "review_reason": "Low evidence confidence and ambiguous timeframe"
        }
        self.storage.save_solar_lead(lead_dict)
        queue = self.storage.get_review_queue(status="PENDING")
        entry = next((item for item in queue if item["post_url"] == lead_dict["post_url"]), None)
        self.assertIsNotNone(entry)
        review_id = entry["review_id"]

        # Process human review approval
        success = self.storage.process_review_decision(
            review_id=review_id,
            decision="APPROVED",
            notes="Manually verified active homeowner",
            reviewer="ADMIN_ALICE"
        )
        self.assertTrue(success)

        # Check that it left pending queue
        updated_queue = self.storage.get_review_queue(status="PENDING")
        self.assertFalse(any(item["review_id"] == review_id for item in updated_queue))

    def test_privacy_suppression_and_deletion(self):
        # 1. Test suppression registry
        added = self.storage.add_suppression("spammer_username", "USERNAME", "TCPA opt-out requested")
        self.assertTrue(added)
        self.assertTrue(self.storage.is_suppressed("spammer_username"))
        self.assertFalse(self.storage.is_suppressed("innocent_homeowner"))

        # 2. Test deletion request processing (CCPA compliance)
        test_url = "https://www.solarpaneltalk.com/forum/delete_me_1"
        self.storage.save_solar_lead({
            "source_name": "Forum",
            "source_url": "forum.com",
            "post_url": test_url,
            "lead_score": 5.0,
            "lead_category": "WARM_BUYER"
        })
        deleted = self.storage.process_deletion_request(test_url)
        self.assertGreaterEqual(deleted, 1)

        # Verify record no longer exists
        leads = self.storage.get_recent_leads(limit=20)
        self.assertFalse(any(l["post_url"] == test_url for l in leads))


class TestAtomicDeduplication(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage(":memory:")

    def test_atomic_content_hash_reservation(self):
        test_hash = "abc123def456_unique_atomic_test"
        simhash = 1234567890

        # First worker call should succeed (returns True)
        first_call = self.storage.save_content_hash_atomic(test_hash, simhash, "https://test.com/p1")
        self.assertTrue(first_call)

        # Second concurrent worker call with same hash must fail (returns False)
        second_call = self.storage.save_content_hash_atomic(test_hash, simhash, "https://test.com/p2")
        self.assertFalse(second_call)


if __name__ == "__main__":
    unittest.main()
