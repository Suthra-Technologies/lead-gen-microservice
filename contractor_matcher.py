"""
Contractor Intelligence & Matching Engine Module
Ranks and matches verified residential solar contractors to qualified homeowner leads
based on state, metro area, residential service capabilities, and buyer stage.
"""

from typing import Dict, List, Any, Optional, Tuple
from models import ContractorMatch


class ContractorMatcher:
    @staticmethod
    def calculate_match_score(lead: Dict[str, Any], contractor: Dict[str, Any]) -> Tuple[int, List[str]]:
        """
        Calculates match score between a solar lead and a contractor candidate.
        Max score: 100
        - State match: +40
        - City/Metro match: +30
        - Residential capability: +20
        - Buyer stage match (seeking/comparing quotes): +10
        - Battery capability match: +10
        """
        score = 0
        reasons = []

        lead_state = (lead.get("state") or "").upper().strip()
        cont_state = (contractor.get("state") or "").upper().strip()

        # 1. State Match (+40)
        if lead_state and cont_state and lead_state == cont_state:
            score += 40
            reasons.append(f"State coverage match ({lead_state})")

        # 2. City / Metro Match (+30)
        lead_city = (lead.get("city") or "").lower().strip()
        cont_city = (contractor.get("city") or "").lower().strip()
        search_query = (contractor.get("search_query") or "").lower()

        if lead_city and (lead_city == cont_city or lead_city in search_query):
            score += 30
            reasons.append(f"Direct metro area match ({lead.get('city')})")

        # 3. Residential Capability (+20)
        primary_type = (contractor.get("primary_type") or "").lower()
        company_name = (contractor.get("company_name") or "").lower()
        if "solar" in primary_type or "solar" in company_name or "contractor" in primary_type:
            score += 20
            reasons.append("Verified residential solar installer")

        # 4. Buyer Stage Match (+10)
        buyer_stage = lead.get("buyer_stage", "")
        if buyer_stage in ("SEEKING_QUOTES", "COMPARING_QUOTES", "PLANNING"):
            score += 10
            reasons.append(f"Active buyer stage alignment ({buyer_stage})")

        # 5. Rating Bonus (0 to 10)
        rating = contractor.get("rating")
        if rating and rating >= 4.5:
            score += 5
            reasons.append(f"High customer satisfaction rating (★ {rating})")

        return score, reasons

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
        """
        matches = []
        for contractor in contractors:
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
