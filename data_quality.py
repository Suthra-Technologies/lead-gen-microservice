"""
Lead Data Quality & Verification Engine Module
Validates lead integrity, evidence provenance, location accuracy, and numerical boundaries
to ensure only high-confidence, auditable leads enter sales pipelines.
"""

import re
from typing import Dict, List, Any, Tuple


VALID_US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC"
}

ZIP_REGEX = re.compile(r"^\d{5}(-\d{4})?$")


class LeadDataQualityValidator:
    @staticmethod
    def validate_lead(lead: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluates lead data quality across 8 critical dimensions:
        1. Source permalink presence and format
        2. Evidence snippet provenance and completeness
        3. Score-to-keyword consistency
        4. System capacity bounds (0.5 - 500.0 kW)
        5. Electric bill bounds ($20 - $10,000/mo)
        6. US ZIP code format
        7. US state code validity
        8. Residential classification confidence
        Returns:
            {
                "is_valid": bool,
                "status": "PASSED" | "WARNING" | "FAILED",
                "flags": List[str]
            }
        """
        flags = []
        is_fatal = False

        # 1. Source Permalink
        post_url = lead.get("post_url") or ""
        if not post_url or not post_url.startswith(("http://", "https://")):
            flags.append("Fatal: Missing or malformed source permalink")
            is_fatal = True

        # 2. Evidence Snippet
        evidence = lead.get("evidence_snippets") or []
        score = lead.get("lead_score", 0.0)
        if score > 0.0 and not evidence:
            flags.append("Fatal: Positive lead score with no supporting evidence snippets")
            is_fatal = True

        # 3. Score-to-Keyword Consistency
        keywords = lead.get("matched_keywords") or []
        if score >= 7.0 and not keywords:
            flags.append("Warning: High purchase intent score with empty keyword list")

        # 4. System Capacity Bounds
        kw = lead.get("system_size_kw")
        if kw is not None:
            if not (0.5 <= kw <= 500.0):
                flags.append(f"Warning: System size ({kw} kW) outside standard residential bounds (0.5 - 500 kW)")

        # 5. Electric Bill Bounds
        bill = lead.get("electricity_bill")
        if bill is not None:
            if not (20.0 <= bill <= 10000.0):
                flags.append(f"Warning: Electric bill (${bill}) outside normal residential bounds ($20 - $10,000)")

        # 6. US ZIP Code Validation
        zip_code = lead.get("zip_code")
        if zip_code:
            clean_zip = str(zip_code).strip()
            if not ZIP_REGEX.match(clean_zip):
                flags.append(f"Warning: Malformed US ZIP code '{clean_zip}'")

        # 7. US State Code Validation
        state = lead.get("state")
        if state:
            clean_state = str(state).strip().upper()
            if clean_state not in VALID_US_STATES:
                flags.append(f"Warning: Unrecognized US state code '{clean_state}'")

        # 8. Classification and Stage Checks
        class_type = lead.get("classification_type") or lead.get("primary_classification") or ""
        if class_type in ("COMMERCIAL_SOLAR", "SPAM_VENDOR", "DIY_OFF_GRID"):
            flags.append(f"Fatal: Non-residential or vendor classified lead ({class_type})")
            is_fatal = True

        # Determine Status
        if is_fatal:
            status = "FAILED"
            is_valid = False
        elif flags:
            status = "WARNING"
            is_valid = True
        else:
            status = "PASSED"
            is_valid = True

        return {
            "is_valid": is_valid,
            "status": status,
            "flags": flags
        }

