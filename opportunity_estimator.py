"""
Solar Opportunity Estimator Module
Calculates residential solar capacity requirements, estimated electricity usage,
system sizing (kW), and offset percentages based on reported electric bills and state rates.
"""

from typing import Dict, Any, Optional
from config import DEFAULT_ELECTRICITY_RATES, DEFAULT_SPECIFIC_YIELD_KWH_PER_KW
from models import SolarOpportunity


class SolarOpportunityEstimator:
    @staticmethod
    def get_electricity_rate(state: Optional[str] = None) -> float:
        """Returns the average residential electricity rate ($/kWh) for a US state."""
        if state:
            st = state.strip().upper()
            if st in DEFAULT_ELECTRICITY_RATES:
                return DEFAULT_ELECTRICITY_RATES[st]
        return DEFAULT_ELECTRICITY_RATES["DEFAULT"]

    @classmethod
    def estimate_opportunity(
        cls,
        electricity_bill: Optional[float] = None,
        state: Optional[str] = None,
        reported_kw: Optional[float] = None
    ) -> Dict[str, Any]:
        """
        Estimates energy usage and required solar system capacity.
        Returns a dictionary containing calculated solar opportunity metrics.
        """
        rate = cls.get_electricity_rate(state)
        specific_yield = DEFAULT_SPECIFIC_YIELD_KWH_PER_KW

        monthly_kwh = None
        annual_kwh = None
        estimated_kw = None
        offset_pct = None

        if electricity_bill and electricity_bill >= 20.0:
            monthly_kwh = round(electricity_bill / rate, 1)
            annual_kwh = round(monthly_kwh * 12.0, 1)
            estimated_kw = round(annual_kwh / specific_yield, 2)

            if reported_kw and reported_kw > 0.0:
                offset_pct = round(min(150.0, (reported_kw / estimated_kw) * 100.0), 1)
            else:
                offset_pct = 100.0
        elif reported_kw and reported_kw > 0.0:
            # If bill was not reported but system size was stated
            estimated_kw = reported_kw
            annual_kwh = round(reported_kw * specific_yield, 1)
            monthly_kwh = round(annual_kwh / 12.0, 1)
            offset_pct = 100.0

        return {
            "rate_per_kwh": rate,
            "estimated_monthly_usage_kwh": monthly_kwh,
            "estimated_annual_usage_kwh": annual_kwh,
            "estimated_system_size_kw": estimated_kw,
            "estimated_solar_offset_percent": offset_pct,
            "calculation_basis": "HOMEOWNER_REPORTED" if reported_kw else "ESTIMATED_FROM_BILL"
        }

