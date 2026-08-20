"""
LA County Tax Bill Fetcher

Attempts to fetch actual tax bill data from the Tax Collector portal.
Falls back to estimated tax calculation from assessed values.

Known Portals (all have bot protection):
- https://vcheck.ttc.lacounty.gov/ - Tax balance verification
- https://www.propertytax.lacounty.gov/ - Property tax portal
- https://portal.assessor.lacounty.gov/parceldetail/{APN} - Assessor details

The parcel layer provides assessed values and exemption amounts. For properties
with welfare tax exemptions, Roll_RealEstateExemp will show the exempt amount.
"""

from dataclasses import dataclass
from typing import Optional
import time

from .parcel_resolver import Parcel


# California base property tax rate (Prop 13)
CA_BASE_TAX_RATE = 0.01  # 1%

# Typical LA County additional assessments (varies by TRA)
# This is an approximation; actual rates vary from ~1.1% to ~1.4%
LA_COUNTY_TYPICAL_ADDITIONAL = 0.002  # 0.2%


@dataclass
class TaxEstimate:
    """Estimated or actual tax information for a parcel."""
    ain: str
    apn: str

    # Assessment values (from parcel layer)
    roll_year: Optional[int]
    assessed_land: Optional[float]
    assessed_improvements: Optional[float]
    assessed_total: Optional[float]

    # Exemptions (from parcel layer)
    homeowners_exemption: Optional[float]
    real_estate_exemption: Optional[float]  # Welfare exemption appears here
    total_exemptions: Optional[float]

    # Taxable value
    net_taxable_value: Optional[float]

    # Estimated annual tax (if not fetched from TTC)
    estimated_annual_tax: Optional[float]
    tax_rate_used: float  # For audit trail

    # Actual tax (if successfully scraped from TTC)
    actual_tax_amount: Optional[float]
    actual_source_url: Optional[str]

    # Status
    has_welfare_exemption: bool
    data_source: str  # "parcel_layer", "ttc_scraped", "estimated"
    notes: str


def estimate_tax_from_parcel(parcel: Parcel) -> TaxEstimate:
    """
    Estimate tax based on parcel assessment data.

    This is an approximation. Actual tax bills include:
    - Base 1% (Prop 13)
    - Voter-approved bonds and assessments
    - Special district charges
    - Direct assessments

    Total typically ranges from 1.1% to 1.4% of assessed value.

    Args:
        parcel: Parcel object with assessment data

    Returns:
        TaxEstimate with calculated values
    """
    # Calculate totals
    land = parcel.roll_land_value or 0
    improvements = parcel.roll_imp_value or 0
    assessed_total = land + improvements

    # Sum exemptions
    ho_exemp = parcel.homeowners_exemp or 0
    re_exemp = parcel.real_estate_exemp or 0
    pp_exemp = parcel.pers_prop_exemp or 0
    fix_exemp = parcel.fixture_exemp or 0
    total_exemp = ho_exemp + re_exemp + pp_exemp + fix_exemp

    # Calculate net taxable value
    net_taxable = max(0, assessed_total - total_exemp)

    # Estimate tax using typical LA County rate
    effective_rate = CA_BASE_TAX_RATE + LA_COUNTY_TYPICAL_ADDITIONAL
    estimated_tax = net_taxable * effective_rate

    # Check for welfare exemption
    has_welfare = re_exemp > 0

    notes = []
    if has_welfare:
        notes.append(f"Welfare exemption: ${re_exemp:,.0f}")
    if total_exemp > 0:
        notes.append(f"Total exemptions reduce taxable value by ${total_exemp:,.0f}")

    return TaxEstimate(
        ain=parcel.ain,
        apn=parcel.apn,
        roll_year=parcel.roll_year,
        assessed_land=land if land else None,
        assessed_improvements=improvements if improvements else None,
        assessed_total=assessed_total if assessed_total else None,
        homeowners_exemption=ho_exemp if ho_exemp else None,
        real_estate_exemption=re_exemp if re_exemp else None,
        total_exemptions=total_exemp if total_exemp else None,
        net_taxable_value=net_taxable if net_taxable else None,
        estimated_annual_tax=estimated_tax if estimated_tax else None,
        tax_rate_used=effective_rate,
        actual_tax_amount=None,
        actual_source_url=None,
        has_welfare_exemption=has_welfare,
        data_source="estimated",
        notes="; ".join(notes) if notes else ""
    )


def fetch_tax_from_ttc(ain: str, timeout: int = 30) -> Optional[dict]:
    """
    Attempt to fetch actual tax data from TTC portal.

    Note: TTC portals have bot protection (Incapsula). This function
    may not work reliably and should be used with appropriate delays.

    Args:
        ain: Assessor's Identification Number (10 digits, no dashes)
        timeout: Request timeout in seconds

    Returns:
        Dict with tax data if successful, None if blocked/failed
    """
    # TODO: Implement when TTC access is available
    # Options:
    # 1. Manual session with cookies
    # 2. Headless browser with proper fingerprinting
    # 3. Request access to TTC bulk data
    return None


def get_tax_info(parcel: Parcel, try_scrape: bool = False) -> TaxEstimate:
    """
    Get tax information for a parcel.

    By default, estimates tax from assessment data. Set try_scrape=True
    to attempt fetching actual data from TTC (may be blocked).

    Args:
        parcel: Parcel object with assessment data
        try_scrape: Whether to attempt TTC scraping

    Returns:
        TaxEstimate with tax information
    """
    # Start with estimate
    estimate = estimate_tax_from_parcel(parcel)

    if try_scrape:
        # Attempt to fetch actual data
        actual = fetch_tax_from_ttc(parcel.ain)
        if actual:
            estimate.actual_tax_amount = actual.get("amount_due")
            estimate.actual_source_url = actual.get("source_url")
            estimate.data_source = "ttc_scraped"

    return estimate


if __name__ == "__main__":
    from parcel_resolver import resolve_parcels

    # Test with a known address
    lat, lon = 34.0727, -118.2976  # 103 S Edgemont St
    result = resolve_parcels(lat, lon)

    if result.parcels:
        parcel = result.parcels[0]
        print(f"Parcel: {parcel.ain} ({parcel.apn})")
        print(f"Address: {parcel.situs_address}")

        estimate = estimate_tax_from_parcel(parcel)
        print(f"\nAssessed Value: ${estimate.assessed_total:,.0f}")
        print(f"  Land: ${estimate.assessed_land:,.0f}")
        print(f"  Improvements: ${estimate.assessed_improvements:,.0f}")
        print(f"Total Exemptions: ${estimate.total_exemptions:,.0f}")
        print(f"Net Taxable: ${estimate.net_taxable_value:,.0f}")
        print(f"Estimated Annual Tax: ${estimate.estimated_annual_tax:,.0f}")
        print(f"Has Welfare Exemption: {estimate.has_welfare_exemption}")
        if estimate.notes:
            print(f"Notes: {estimate.notes}")
