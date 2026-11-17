"""
Solar Lead Intelligence Platform — Gold Standard Evaluation Benchmark
Measures Precision, Recall, F1 Score, and Location Extraction Accuracy
against labeled real-world solar discussion datasets.
"""

import sys
import logging
from typing import Dict, Any, List
from storage import CrawlerStorage
from parser import (
    ContentParser,
    CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
    CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER,
    CLASSIFICATION_RESIDENTIAL_EXISTING,
    CLASSIFICATION_RESIDENTIAL_UPGRADE,
    CLASSIFICATION_RESIDENTIAL_REPAIR,
    CLASSIFICATION_COMMERCIAL,
    CLASSIFICATION_DIY_OFFGRID,
    CLASSIFICATION_TECHNICAL,
    CLASSIFICATION_GENERAL_INFO,
    CLASSIFICATION_SPAM_VENDOR
)

logging.basicConfig(level=logging.WARNING)

BENCHMARK_DATASET: List[Dict[str, Any]] = [
    # 1. High-intent residential buyer seeking installer
    {
        "id": "BENCH-001",
        "title": "Looking for recommended solar installers in San Diego CA",
        "text": "We just bought a house in San Diego, California 92101. Our electric bill is $320/month. Looking for reputable local solar installers to get quotes from. How much should we expect to pay?",
        "ground_truth": {
            "is_buyer_lead": True,
            "expected_classification": CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
            "expected_state": "CA",
            "should_be_hard_disqualified": False,
        }
    },
    # 2. High-intent quote comparison with PPW
    {
        "id": "BENCH-002",
        "title": "Solar quote comparison: SunPower vs Tesla in Austin Texas",
        "text": "Got two quotes for our residential home in Texas: 1) SunPower 8.5 kW system at $2.85/W ($24,225) with SunVault. 2) Tesla 8.16 kW system at $2.20/W with 1 Powerwall. Which installer quote is better value?",
        "ground_truth": {
            "is_buyer_lead": True,
            "expected_classification": CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER,
            "expected_state": "TX",
            "should_be_hard_disqualified": False,
        }
    },
    # 3. Commercial solar farm discussion (Must be Hard Disqualified)
    {
        "id": "BENCH-003",
        "title": "Financing a 2.5 MW commercial solar project in Arizona",
        "text": "We are developing a 2.5 MW commercial ground mount array on a commercial building rooftop in Phoenix Arizona. Looking for commercial PPA terms and utility interconnection requirements.",
        "ground_truth": {
            "is_buyer_lead": False,
            "expected_classification": CLASSIFICATION_COMMERCIAL,
            "expected_state": "AZ",
            "should_be_hard_disqualified": True,
        }
    },
    # 4. DIY off-grid battery / camper setup (Must be Hard Disqualified)
    {
        "id": "BENCH-004",
        "title": "DIY off-grid solar build with 48V LifePO4 server rack batteries",
        "text": "Building a DIY solar setup for our remote hunting cabin. Using a Victron MultiPlus inverter, 48V 100Ah LiFePO4 server rack battery, and Renogy solar panels. Doing the DIY wiring myself.",
        "ground_truth": {
            "is_buyer_lead": False,
            "expected_classification": CLASSIFICATION_DIY_OFFGRID,
            "expected_state": None,
            "should_be_hard_disqualified": True,
        }
    },
    # 5. Technical inverter repair / troubleshooting (Service lead or Disqualified from new purchase)
    {
        "id": "BENCH-005",
        "title": "SolarEdge HD-Wave inverter error code 18x8B - need repair technician",
        "text": "My SolarEdge inverter stopped working yesterday morning with error code 18x8B. System was installed 5 years ago in Florida FL. Need an authorized repair technician to replace inverter under warranty.",
        "ground_truth": {
            "is_buyer_lead": True,  # Service customer lead
            "expected_classification": CLASSIFICATION_RESIDENTIAL_REPAIR,
            "expected_state": "FL",
            "should_be_hard_disqualified": False,
        }
    },
    # 6. Negative intent / deciding against solar (Must be Hard Disqualified)
    {
        "id": "BENCH-006",
        "title": "Decided against solar after evaluating payback period",
        "text": "I was looking at solar for our home in Orlando, Florida. But with interest rates at 8.5% and NEM 3.0, not interested in solar anymore. Solar is not worth it for us right now. Not buying.",
        "ground_truth": {
            "is_buyer_lead": False,
            "expected_classification": CLASSIFICATION_GENERAL_INFO,
            "expected_state": "FL",
            "should_be_hard_disqualified": True,
        }
    },
    # 7. Purely hypothetical / academic math problem (Must be Hard Disqualified)
    {
        "id": "BENCH-007",
        "title": "Hypothetical solar calculation: If someone were to install 10kW",
        "text": "Just a hypothetical theoretical question for an engineering paper: what if someone installed a 10 kW system in Nevada? Hypothetically speaking, how many kWh would it produce annually?",
        "ground_truth": {
            "is_buyer_lead": False,
            "expected_classification": CLASSIFICATION_GENERAL_INFO,
            "expected_state": "NV",
            "should_be_hard_disqualified": True,
        }
    },
    # 8. Vendor self-promotion spam (Must be Hard Disqualified)
    {
        "id": "BENCH-008",
        "title": "Top rated solar company in Colorado - special promotion 20% off!",
        "text": "We are Apex Solar, a premier installer in Colorado CO! Call us now for exclusive discounts on Tier-1 panels! Free estimates and limited time financing rates available on our website.",
        "ground_truth": {
            "is_buyer_lead": False,
            "expected_classification": CLASSIFICATION_SPAM_VENDOR,
            "expected_state": "CO",
            "should_be_hard_disqualified": True,
        }
    },
    # 9. Existing solar customer adding battery storage / upgrade
    {
        "id": "BENCH-009",
        "title": "Adding battery storage to existing 7kW solar system in North Carolina",
        "text": "We have an existing rooftop solar array (7 kW) installed 3 years ago in North Carolina NC. Looking to add battery backup (Tesla Powerwall 3 or Enphase 5P) to our house for power outages. Any installer recommendations?",
        "ground_truth": {
            "is_buyer_lead": True,
            "expected_classification": CLASSIFICATION_RESIDENTIAL_UPGRADE,
            "expected_state": "NC",
            "should_be_hard_disqualified": False,
        }
    },
    # 10. High-intent electric bill shock seeking proposals
    {
        "id": "BENCH-010",
        "title": "PG&E electric bills hit $550/mo - need solar proposals in Sacramento CA",
        "text": "Our PG&E utility bill reached $550/month this summer for our single family home in Sacramento California. We need to go solar immediately. Looking for proposals and recommended installers.",
        "ground_truth": {
            "is_buyer_lead": True,
            "expected_classification": CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
            "expected_state": "CA",
            "should_be_hard_disqualified": False,
        }
    },
    # 11. Forum sticky rules / FAQ (Must be Hard Disqualified)
    {
        "id": "BENCH-011",
        "title": "READ THIS FIRST: Forum Rules and Solar FAQ for New Members",
        "text": "Welcome to the solar forum! Please read the rules before posting. Do not post referral links or affiliate spam. Frequently asked questions about solar terminology and net metering are answered here.",
        "ground_truth": {
            "is_buyer_lead": False,
            "expected_classification": CLASSIFICATION_GENERAL_INFO,
            "expected_state": None,
            "should_be_hard_disqualified": True,
        }
    },
    # 12. Technical busbar & wire gauge NEC code discussion (Must be Hard Disqualified)
    {
        "id": "BENCH-012",
        "title": "NEC 705.12 120% rule question on 200A main service panel busbar",
        "text": "Need clarification on NEC code. If I have a 200A busbar with a 200A main breaker, can I backfeed 40A or do I need to derate the main breaker to 175A? Wondering about the 120% rule calculation.",
        "ground_truth": {
            "is_buyer_lead": False,
            "expected_classification": CLASSIFICATION_TECHNICAL,
            "expected_state": None,
            "should_be_hard_disqualified": True,
        }
    },
    # 13. Active quote review with 3 installer bids in Georgia
    {
        "id": "BENCH-013",
        "title": "Reviewing 3 quotes for 11 kW rooftop system in Atlanta Georgia",
        "text": "We received three quotes for our home in Atlanta Georgia (GA 30301). Quote A: $2.90/W with REC Alpha panels. Quote B: $2.75/W with Qcells. Quote C: $3.10/W with Enphase microinverters. Which quote offers the best warranty?",
        "ground_truth": {
            "is_buyer_lead": True,
            "expected_classification": CLASSIFICATION_RESIDENTIAL_QUOTE_SEEKER,
            "expected_state": "GA",
            "should_be_hard_disqualified": False,
        }
    },
    # 14. Existing homeowner not planning to buy (General Discussion)
    {
        "id": "BENCH-014",
        "title": "5 years of solar production data from my rooftop system",
        "text": "Sharing my 5-year generation stats from our rooftop panels in Ohio. Generated 45,000 kWh total over five years. Everything has worked smoothly with no degradation issues so far.",
        "ground_truth": {
            "is_buyer_lead": False,
            "expected_classification": CLASSIFICATION_RESIDENTIAL_EXISTING,
            "expected_state": "OH",
            "should_be_hard_disqualified": True,  # Existing owner sharing data without active upgrade/repair intent
        }
    },
    # 15. Active buyer planning new residential solar installation in Illinois
    {
        "id": "BENCH-015",
        "title": "Planning residential solar in Chicago Illinois - Shines incentive",
        "text": "We own a house in Chicago Illinois and want to install solar to take advantage of the Illinois Shines SREC program. Our electricity bill is about $190/month. Who are the most reputable certified installers?",
        "ground_truth": {
            "is_buyer_lead": True,
            "expected_classification": CLASSIFICATION_RESIDENTIAL_NEW_BUYER,
            "expected_state": "IL",
            "should_be_hard_disqualified": False,
        }
    }
]


def run_benchmark(verbose: bool = True) -> Dict[str, Any]:
    storage = CrawlerStorage(":memory:")
    parser = ContentParser(storage=storage)

    total_samples = len(BENCHMARK_DATASET)
    tp = 0
    fp = 0
    fn = 0
    tn = 0

    state_correct = 0
    state_eval_total = 0

    details = []

    for item in BENCHMARK_DATASET:
        sample_id = item["id"]
        title = item["title"]
        text = item["text"]
        gt = item["ground_truth"]

        # Run parsing pipeline
        classification, _ = parser.classify_content(title, text)
        score, keywords, snippets = parser.score_buyer_intent(classification, title, text)
        detected_state, _, _ = parser._extract_location(f"{title} {text}")
        is_hypo = any(h in text.lower() for h in ["hypothetical", "just wondering", "hypothetically"])

        qual_dims = parser.calculate_qualification_dimensions(
            score=score,
            classification=classification,
            title=title,
            text=text,
            evidence_snippets=snippets,
            state=detected_state,
            zip_code=None,
            intent_age_days=1,
            buyer_stage="PLANNING",
            is_hypothetical=is_hypo
        )

        hard_disqualified = qual_dims["hard_disqualified"]
        composite_score = qual_dims["composite_qualification_score"]

        # Prediction: Qualified buyer if NOT hard-disqualified and composite score >= 40.0
        predicted_buyer_lead = (not hard_disqualified) and (composite_score >= 40.0)

        # Evaluate Lead Qualification
        actual_buyer_lead = gt["is_buyer_lead"]
        if actual_buyer_lead and predicted_buyer_lead:
            tp += 1
            verdict = "TP (Hit)"
        elif not actual_buyer_lead and not predicted_buyer_lead:
            tn += 1
            verdict = "TN (Correct Rejection)"
        elif not actual_buyer_lead and predicted_buyer_lead:
            fp += 1
            verdict = "FP (False Alarm)"
        else:
            fn += 1
            verdict = "FN (Missed Lead)"

        # Evaluate Geographic Extraction
        if gt["expected_state"]:
            state_eval_total += 1
            if detected_state == gt["expected_state"]:
                state_correct += 1
                state_match = "MATCH"
            else:
                state_match = f"MISMATCH ({detected_state} != {gt['expected_state']})"
        else:
            state_match = "N/A"

        details.append({
            "id": sample_id,
            "title": title[:45],
            "classification": classification,
            "raw_score": score,
            "composite_score": composite_score,
            "disqualified": hard_disqualified,
            "disqualification_reason": qual_dims["disqualification_reason"],
            "verdict": verdict,
            "state_match": state_match
        })

    # Metrics calculation
    precision = (tp / (tp + fp)) * 100.0 if (tp + fp) > 0 else 0.0
    recall = (tp / (tp + fn)) * 100.0 if (tp + fn) > 0 else 0.0
    f1_score = (2 * precision * recall) / (precision + recall) if (precision + recall) > 0 else 0.0
    accuracy = ((tp + tn) / total_samples) * 100.0
    state_accuracy = (state_correct / state_eval_total) * 100.0 if state_eval_total > 0 else 100.0

    fpr = (fp / (fp + tn)) * 100.0 if (fp + tn) > 0 else 0.0
    fnr = (fn / (fn + tp)) * 100.0 if (fn + tp) > 0 else 0.0

    # Regression gates
    gate_precision_passed = precision >= 85.0
    gate_recall_passed = recall >= 80.0
    gate_f1_passed = f1_score >= 82.0
    all_gates_passed = gate_precision_passed and gate_recall_passed and gate_f1_passed

    report = {
        "total_samples": total_samples,
        "true_positives": tp,
        "false_positives": fp,
        "true_negatives": tn,
        "false_negatives": fn,
        "precision": round(precision, 2),
        "recall": round(recall, 2),
        "f1_score": round(f1_score, 2),
        "accuracy": round(accuracy, 2),
        "false_positive_rate": round(fpr, 2),
        "false_negative_rate": round(fnr, 2),
        "state_extraction_accuracy": round(state_accuracy, 2),
        "gates": {
            "precision_gte_85": gate_precision_passed,
            "recall_gte_80": gate_recall_passed,
            "f1_gte_82": gate_f1_passed,
            "all_passed": all_gates_passed
        },
        "details": details
    }

    if verbose:
        print("=" * 80)
        print("SOLAR LEAD INTELLIGENCE PLATFORM — GOLD STANDARD EVALUATION BENCHMARK")
        print("=" * 80)
        print(f"Total Benchmark Dataset: {total_samples} labeled samples")
        print(f"True Positives: {tp} | True Negatives: {tn} | False Positives: {fp} | False Negatives: {fn}")
        print("-" * 80)
        print(f"Precision:                {precision:.1f}% (Gate: >=85.0% -> {'PASS' if gate_precision_passed else 'FAIL'})")
        print(f"Recall:                   {recall:.1f}% (Gate: >=80.0% -> {'PASS' if gate_recall_passed else 'FAIL'})")
        print(f"F1 Score:                 {f1_score:.1f}% (Gate: >=82.0% -> {'PASS' if gate_f1_passed else 'FAIL'})")
        print(f"Overall Accuracy:         {accuracy:.1f}%")
        print(f"False Positive Rate:      {fpr:.1f}%")
        print(f"False Negative Rate:      {fnr:.1f}%")
        print(f"Geographic Extr. Acc:     {state_accuracy:.1f}% ({state_correct}/{state_eval_total} states matched)")
        print("-" * 80)
        print("SAMPLE BREAKDOWN:")
        for d in details:
            status_symbol = "OK" if "Hit" in d["verdict"] or "Correct" in d["verdict"] else "FAIL"
            print(f"[{status_symbol}] {d['id']}: {d['title']} | Score: {d['composite_score']:.1f} | {d['verdict']} | Geo: {d['state_match']}")
        print("=" * 80)
        if all_gates_passed:
            print(">>> REGRESSION BENCHMARK PASSED: All qualification & accuracy gates met! <<<")
        else:
            print(">>> REGRESSION BENCHMARK FAILED: One or more gates were not met! <<<")
        print("=" * 80)

    return report


if __name__ == "__main__":
    rep = run_benchmark(verbose=True)
    if not rep["gates"]["all_passed"]:
        sys.exit(1)
    sys.exit(0)
