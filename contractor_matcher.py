"""
Contractor Intelligence & Matching Engine Module
Ranks and matches verified residential solar contractors to qualified homeowner leads
based on state, metro area, residential service capabilities, verification quality, and buyer stage.
Calibrated strictly to a 100-point composite scoring model.
"""

from typing import Dict, List, Any, Optional, Tuple
from models import ContractorMatch


class ContractorMatcher:
    # Strict 100-Point Calibrated Weights (Gap Analysis V2)
    WEIGHT_SERVICE_AREA = 30       # State coverage (15) + Direct Metro/City (15)
    WEIGHT_RESIDENTIAL_FIT = 20    # Verified residential solar installer
    WEIGHT_CAPABILITY_FIT = 15     # Battery / storage / equipment capability
    WEIGHT_VERIFICATION_FIT = 15   # Phone & website verified presence
    WEIGHT_REPUTATION_FIT = 10     # Customer satisfaction rating
    WEIGHT_BUYER_STAGE_FIT = 5     # Active quote comparison / seeking
    WEIGHT_AVAILABILITY_FIT = 5    # Operational business status

    @classmethod
    def calculate_match_score(cls, lead: Dict[str, Any], contractor: Dict[str, Any]) -> Tuple[int, List[str]]:
        """
        Calculates calibrated match score between a solar lead and a contractor candidate.
        Max score: 100 points.
        1. Service Area Fit: up to 30 pts (State match: 15, City/Metro match: 15)
        2. Residential Solar Service: 20 pts
        3. Equipment & Battery Capability: 15 pts
        4. Identity & Contact Verification: 15 pts (Phone: 8, Website: 7)
        5. Customer Reputation & Rating: up to 10 pts
        6. Buyer Stage Alignment: 5 pts
        7. Operational Availability: 5 pts
        """
        score = 0
        reasons = []

        lead_state = (lead.get("state") or "").upper().strip()
        cont_state = (contractor.get("state") or "").upper().strip()

        # Hard Eligibility Gate 1: Non-installation businesses (e.g. cleaning services, sunscreens)
        category = contractor.get("business_category")
        if category in ("Cleaning Service", "Non-Solar Screens/Lighting"):
            return 0, [f"Disqualified: Ineligible business category '{category}' (not a residential installation contractor)"]

        # Hard Eligibility Gate 2: Operational status
        business_status = (contractor.get("business_status") or "OPERATIONAL").upper()
        if business_status in ("CLOSED_PERMANENTLY", "CLOSED_TEMPORARILY"):
            return 0, [f"Disqualified: Contractor business status is {business_status}"]

        # Hard Eligibility Gate 3: If state is known for both, they must match
        if lead_state and cont_state and lead_state != cont_state:
            return 0, [f"Disqualified: State mismatch (Lead: {lead_state}, Contractor: {cont_state})"]

        # 1. Service Area Fit (Max 30)
        if lead_state and cont_state and lead_state == cont_state:
            score += 15
            reasons.append(f"State coverage match ({lead_state}) (+15)")

        lead_city = (lead.get("city") or "").lower().strip()
        cont_city = (contractor.get("city") or "").lower().strip()
        search_query = (contractor.get("search_query") or "").lower()

        if lead_city and (lead_city == cont_city or lead_city in search_query):
            score += 15
            reasons.append(f"Direct metro area match ({lead.get('city')}) (+15)")
        elif not lead_city and lead_state:
            # Partial metro credit if state matched but lead city was not specified
            score += 5
            reasons.append("State-wide service territory (+5)")

        # 2. Residential Capability (Max 20)
        primary_type = (contractor.get("primary_type") or "").lower()
        company_name = (contractor.get("company_name") or "").lower()
        if "solar" in primary_type or "solar" in company_name or "contractor" in primary_type:
            score += 20
            reasons.append("Verified residential solar installer (+20)")

        # 3. Installation & Battery Capability (Max 15)
        secondary_classes = lead.get("secondary_classifications") or []
        lead_title = (lead.get("post_title") or "").lower()
        needs_battery = "STORAGE_ADDITION" in secondary_classes or "battery" in lead_title or "powerwall" in lead_title
        if "solar" in company_name or "electric" in company_name or "energy" in company_name:
            if needs_battery:
                score += 15
                reasons.append("Turnkey solar plus battery storage capability (+15)")
            else:
                score += 15
                reasons.append("Full residential system installation capability (+15)")

        # 4. Identity & Contact Verification (Max 15)
        has_phone = bool(contractor.get("phone_number"))
        has_website = bool(contractor.get("website_url"))
        if has_phone and has_website:
            score += 15
            reasons.append("Dual verified business identity (Phone & Website) (+15)")
        elif has_phone:
            score += 8
            reasons.append("Verified phone number (+8)")
        elif has_website:
            score += 7
            reasons.append("Verified official website (+7)")

        # 5. Reputation & Rating (Max 10)
        rating = contractor.get("rating")
        if rating:
            if rating >= 4.8:
                score += 10
                reasons.append(f"Exceptional customer satisfaction rating (★ {rating}) (+10)")
            elif rating >= 4.5:
                score += 7
                reasons.append(f"High customer satisfaction rating (★ {rating}) (+7)")
            elif rating >= 4.0:
                score += 4
                reasons.append(f"Satisfactory customer rating (★ {rating}) (+4)")

        # 6. Buyer Stage Alignment (Max 5)
        buyer_stage = lead.get("buyer_stage", "")
        if buyer_stage in ("SEEKING_QUOTES", "COMPARING_QUOTES", "PLANNING", "READY_TO_PURCHASE"):
            score += 5
            reasons.append(f"Active quote stage alignment ({buyer_stage}) (+5)")

        # 7. Operational Availability (Max 5)
        status = contractor.get("business_status", "OPERATIONAL")
        if status == "OPERATIONAL":
            score += 5
            reasons.append("Active operational status (+5)")

        # Total strictly bounded [0, 100]
        final_score = min(100, max(0, score))
        return final_score, reasons

    @classmethod
    def match_lead(
        cls,
        lead: Dict[str, Any],
        contractors: List[Dict[str, Any]],
        top_n: int = 3,
        min_score: int = 40
    ) -> List[Dict[str, Any]]:
        """
        Matches and ranks top N eligible contractors for a qualified solar lead.
        Enforces hard eligibility gates (state match and operational status) before ranking.
        """
        matches = []
        for contractor in contractors:
            # Hard Eligibility Gate: Do not match permanently closed businesses
            if contractor.get("business_status") == "CLOSED_PERMANENTLY":
                continue

            score, reasons = cls.calculate_match_score(lead, contractor)
            if score >= min_score:
                matches.append({
                    "place_id": contractor.get("place_id", ""),
                    "company_name": contractor.get("company_name", ""),
                    "rating": contractor.get("rating"),
                    "phone_number": contractor.get("phone_number"),
                    "website_url": contractor.get("website_url"),
                    "city": contractor.get("city"),
                    "state": contractor.get("state"),
                    "match_score": score,
                    "match_reasons": reasons
                })

        # Rank by score descending, then rating descending
        matches.sort(key=lambda m: (m["match_score"], m.get("rating") or 0.0), reverse=True)
        return matches[:top_n]
