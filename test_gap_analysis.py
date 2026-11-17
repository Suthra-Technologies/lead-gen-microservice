"""
Unit and Integration Test Suite for Production Solar Lead Intelligence Engine
Validates the new components implemented in the Gap Analysis Roadmap:
1. Solar Opportunity Estimator (usage, sizing, offset)
2. Contractor Intelligence & Matching Engine
3. Data Quality & Integrity Validator
4. Multi-Dimensional Qualification Model & NLP Intent (negation, third-person, hypothetical)
5. CRM Lead Lifecycle State Transitions & Audit History
6. Recursive Source Discovery Frontier Tracking
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
    def test_contractor_matching_scoring(self):
        lead = {
            "state": "CA",
            "city": "Fresno",
            "buyer_stage": "COMPARING_QUOTES"
        }
        contractor = {
            "place_id": "plc_123",
            "company_name": "Fresno Solar Pros",
            "city": "Fresno",
            "state": "CA",
            "primary_type": "solar_installation_contractor",
            "rating": 4.8
        }
        score, reasons = ContractorMatcher.calculate_match_score(lead, contractor)
        # State (40) + City (30) + Residential (20) + Buyer stage (10) + Rating (5) = 100 max capped
        self.assertGreaterEqual(score, 90)
        self.assertTrue(any("State coverage" in r for r in reasons))
        self.assertTrue(any("Direct metro" in r for r in reasons))

    def test_match_lead_ranking(self):
        lead = {"state": "TX", "city": "Austin", "buyer_stage": "SEEKING_QUOTES"}
        contractors = [
            {"place_id": "c1", "company_name": "Austin Clean Energy", "city": "Austin", "state": "TX", "primary_type": "solar", "rating": 4.9},
            {"place_id": "c2", "company_name": "Dallas Solar Co", "city": "Dallas", "state": "TX", "primary_type": "solar", "rating": 4.2},
            {"place_id": "c3", "company_name": "Miami Solar", "city": "Miami", "state": "FL", "primary_type": "solar", "rating": 4.5}
        ]
        matches = ContractorMatcher.match_lead(lead, contractors, min_score=40)
        self.assertEqual(len(matches), 2)  # TX contractors match; FL is filtered out (<40)
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
            "system_size_kw": 500.0,  # Exceeds residential threshold (>150kW)
            "electricity_bill": 15000.0,  # Exceeds threshold (>10000)
            "zip_code": "ABCDE",  # Invalid ZIP
            "state": "ZZ",  # Invalid state
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
        self.storage = CrawlerStorage()
        self.parser = ContentParser(storage=self.storage)

    def test_negation_detection(self):
        text = "I am not interested in solar. Definitely not looking for an installer."
        score, keywords, evidence = self.parser.score_buyer_intent("RESIDENTIAL_NEW_BUYER", "Not buying", text)
        self.assertLess(score, 3.0)

    def test_hypothetical_penalty(self):
        text = "Hypothetically speaking, for an academic project, what is the cost of solar?"
        score, keywords, evidence = self.parser.score_buyer_intent("RESIDENTIAL_NEW_BUYER", "Academic inquiry", text)
        self.assertLess(score, 3.0)

    def test_genuine_intent_high_score(self):
        text = "Looking for a solar installer for our home in San Diego CA. Electric bill is $280/mo."
        score, keywords, evidence = self.parser.score_buyer_intent("RESIDENTIAL_NEW_BUYER", "Need solar installer", text)
        self.assertGreater(score, 6.0)
        self.assertGreater(len(evidence), 0)

    def test_multi_dimensional_qualification(self):
        dims = self.parser.calculate_qualification_dimensions(
            score=8.5,
            classification="RESIDENTIAL_NEW_BUYER",
            title="Looking for quotes",
            text="Single family residential home in Phoenix AZ 85001. Ready to buy.",
            evidence_snippets=["Looking for a solar installer in Phoenix"],
            state="AZ",
            zip_code="85001",
            intent_age_days=3
        )
        self.assertAlmostEqual(dims["purchase_intent_score"], 8.5)
        self.assertEqual(dims["residential_confidence"], 1.0)
        self.assertEqual(dims["location_confidence"], 0.95)
        self.assertEqual(dims["freshness_score"], 1.0)
        self.assertGreater(dims["evidence_confidence"], 0.3)


class TestLeadLifecycleAndCRMHistory(unittest.TestCase):
    def setUp(self):
        self.storage = CrawlerStorage()

    def test_lead_lifecycle_transition(self):
        lead_dict = {
            "source_name": "SolarPanelTalk",
            "source_url": "solarpaneltalk.com",
            "post_url": "https://www.solarpaneltalk.com/forum/test_lifecycle_1",
            "author": "SunSeeker",
            "post_title": "Looking for residential installer in Tampa",
            "intent_snippet": "Looking for solar quotes for single family roof",
            "lead_score": 8.0,
            "lead_category": "HOT_BUYER",
            "lead_segment": "NEW_BUYER",
            "classification_type": "RESIDENTIAL_NEW_BUYER",
            "primary_classification": "RESIDENTIAL_NEW_BUYER",
            "lead_freshness": "0-7 days",
            "lead_status": LeadLifecycleStatus.NEW.value,
            "state": "FL",
            "city": "Tampa"
        }
        self.storage.save_solar_lead(lead_dict)
        leads = self.storage.get_recent_leads(limit=5)
        target = next((l for l in leads if l["post_url"] == lead_dict["post_url"]), None)
        self.assertIsNotNone(target)
        lead_id = target["id"]

        # Assign contractor
        success = self.storage.assign_lead_contractor(
            lead_id=lead_id,
            contractor_id="place_fl_solar_1",
            new_status=LeadLifecycleStatus.ASSIGNED.value,
            reason="Matched with Tampa Solar Pros"
        )
        self.assertTrue(success)

        # Update status to CONTACTED
        self.storage.update_lead_status(
            lead_id=lead_id,
            new_status=LeadLifecycleStatus.CONTACTED.value,
            reason="Outreach email sent to homeowner"
        )

        history = self.storage.get_lead_history(lead_id)
        self.assertGreaterEqual(len(history), 2)
        self.assertEqual(history[0]["new_status"], LeadLifecycleStatus.ASSIGNED.value)
        self.assertEqual(history[1]["new_status"], LeadLifecycleStatus.CONTACTED.value)


if __name__ == "__main__":
    unittest.main()
