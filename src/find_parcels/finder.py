"""
Parcel Finder

Given an address, find all associated parcels in LA County.

Strategy:
1. Geocode the address to get coordinates
2. Spatial query at coordinates to find initial parcel(s)
3. Extract AIN prefix (map book + page = first 7 digits)
4. Query all parcels with that AIN prefix
"""

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

import requests


# API endpoints
GEOCODER_URL = "https://public.gis.lacounty.gov/public/rest/services/CAMS_Locator/GeocodeServer/findAddressCandidates"
PARCEL_URL = "https://cache.gis.lacounty.gov/cache/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"

# Rate limiting
DEFAULT_RATE_LIMIT = 0.3  # seconds between requests
_last_request_time = 0


@dataclass
class Parcel:
    """A parcel from the LA County parcel layer."""
    ain: str                     # 10-digit Assessor's Identification Number
    apn: str                     # 12-digit formatted APN (e.g., "7157-031-025")
    situs_address: str           # Full situs address
    situs_house_no: str          # Street number
    situs_direction: str         # N, S, E, W
    situs_street: str            # Street name with suffix
    situs_city: str              # City
    situs_zip: str               # ZIP code
    use_description: str         # Property use description
    use_type: str                # Use type code
    roll_year: Optional[int] = None
    roll_land_value: Optional[float] = None
    roll_imp_value: Optional[float] = None
    homeowners_exemp: Optional[float] = None
    real_estate_exemp: Optional[float] = None
    pers_prop_exemp: Optional[float] = None
    fixture_exemp: Optional[float] = None
    tax_rate_area: str = ""
    tax_rate_city: str = ""


@dataclass
class FindParcelsResult:
    """Result of finding parcels for an address."""
    input_address: str
    geocoded_address: Optional[str] = None
    geocode_score: Optional[float] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    tract_number: Optional[str] = None     # Tract number from Legal Description
    ain_prefix: Optional[str] = None       # The 7-digit AIN prefix (fallback)
    parcels: list[Parcel] = field(default_factory=list)
    initial_parcel_count: int = 0          # Parcels found at geocoded location
    error: Optional[str] = None


def _rate_limit(rate_limit: float = DEFAULT_RATE_LIMIT):
    """Apply rate limiting between requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < rate_limit:
        time.sleep(rate_limit - elapsed)
    _last_request_time = time.time()


def geocode_address(
    address: str,
    rate_limit: float = DEFAULT_RATE_LIMIT
) -> tuple[Optional[str], Optional[float], Optional[float], Optional[float]]:
    """
    Geocode an address using LA County CAMS Locator.

    Returns: (matched_address, score, lat, lon) or (None, None, None, None) on failure
    """
    _rate_limit(rate_limit)

    params = {
        'Single Line Input': address,
        'f': 'json',
        'outSR': 4326,
        'maxLocations': 5
    }

    try:
        response = requests.get(GEOCODER_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return None, None, None, None

    candidates = data.get('candidates', [])
    if not candidates:
        return None, None, None, None

    # Get best match
    best = max(candidates, key=lambda c: c.get('score', 0))
    loc = best.get('location', {})

    return (
        best.get('address'),
        best.get('score'),
        loc.get('y'),  # latitude
        loc.get('x')   # longitude
    )


def query_parcels_spatial(
    lat: float,
    lon: float,
    buffer_meters: float = 15.0,
    rate_limit: float = DEFAULT_RATE_LIMIT
) -> list[dict]:
    """
    Query parcels at given coordinates with buffer.

    Returns list of parcel attribute dicts.
    """
    _rate_limit(rate_limit)

    params = {
        'geometry': f'{lon},{lat}',
        'geometryType': 'esriGeometryPoint',
        'inSR': 4326,
        'spatialRel': 'esriSpatialRelIntersects',
        'distance': buffer_meters,
        'units': 'esriSRUnit_Meter',
        'outFields': '*',
        'returnGeometry': 'false',
        'f': 'json',
    }

    try:
        response = requests.get(PARCEL_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return []

    if 'error' in data:
        return []

    return [f.get('attributes', {}) for f in data.get('features', [])]


def query_parcels_by_ain_prefix(
    ain_prefix: str,
    rate_limit: float = DEFAULT_RATE_LIMIT
) -> list[dict]:
    """
    Query all parcels with given AIN prefix.

    Args:
        ain_prefix: 7-digit AIN prefix (map book + page)

    Returns list of parcel attribute dicts.
    """
    _rate_limit(rate_limit)

    params = {
        'where': f"AIN LIKE '{ain_prefix}%'",
        'outFields': '*',
        'returnGeometry': 'false',
        'f': 'json',
        'resultRecordCount': 500,  # Max reasonable for a single property
    }

    try:
        response = requests.get(PARCEL_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return []

    if 'error' in data:
        return []

    return [f.get('attributes', {}) for f in data.get('features', [])]


def extract_tract_number(legal_description: str) -> Optional[str]:
    """
    Extract tract number from legal description.

    Legal descriptions often start with "TRACT NO XXXXX" or "TRACT # XXXXX" for subdivisions.
    Returns the tract number if found, None otherwise.
    """
    import re
    if not legal_description:
        return None

    # Match "TRACT NO 12345", "TRACT NO. 12345", or "TRACT # 12345" patterns
    match = re.search(r'TRACT\s+(?:NO\.?|#)\s*(\d+)', legal_description, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def query_parcels_by_tract(
    tract_number: str,
    rate_limit: float = DEFAULT_RATE_LIMIT
) -> list[dict]:
    """
    Query all parcels in a given tract.

    Args:
        tract_number: Tract number (e.g., "27326")

    Returns list of parcel attribute dicts.
    """
    _rate_limit(rate_limit)

    # Handle both "TRACT NO 12345" and "TRACT # 12345" formats
    params = {
        'where': f"LegalDescription LIKE 'TRACT NO {tract_number}%' OR LegalDescription LIKE 'TRACT NO. {tract_number}%' OR LegalDescription LIKE 'TRACT # {tract_number}%' OR LegalDescription LIKE 'TRACT #{tract_number}%'",
        'outFields': '*',
        'returnGeometry': 'false',
        'f': 'json',
        'resultRecordCount': 500,
    }

    try:
        response = requests.get(PARCEL_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return []

    if 'error' in data:
        return []

    return [f.get('attributes', {}) for f in data.get('features', [])]


def _attrs_to_parcel(attrs: dict) -> Parcel:
    """Convert API attributes dict to Parcel object."""
    # Parse numeric values safely
    def safe_float(val):
        try:
            return float(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    def safe_int(val):
        try:
            return int(val) if val is not None else None
        except (ValueError, TypeError):
            return None

    return Parcel(
        ain=attrs.get('AIN', ''),
        apn=attrs.get('APN', ''),
        situs_address=attrs.get('SitusFullAddress', '') or '',
        situs_house_no=str(attrs.get('SitusHouseNo', '') or ''),
        situs_direction=attrs.get('SitusDirection', '') or '',
        situs_street=attrs.get('SitusStreet', '') or '',
        situs_city=attrs.get('SitusCity', '') or '',
        situs_zip=attrs.get('SitusZIP', '') or '',
        use_description=attrs.get('UseDescription', '') or '',
        use_type=attrs.get('UseType', '') or '',
        roll_year=safe_int(attrs.get('Roll_Year')),
        roll_land_value=safe_float(attrs.get('Roll_LandValue')),
        roll_imp_value=safe_float(attrs.get('Roll_ImpValue')),
        homeowners_exemp=safe_float(attrs.get('Roll_HomeOwnersExemp')),
        real_estate_exemp=safe_float(attrs.get('Roll_RealEstateExemp')),
        pers_prop_exemp=safe_float(attrs.get('Roll_PersPropExemp')),
        fixture_exemp=safe_float(attrs.get('Roll_FixtureExemp')),
        tax_rate_area=attrs.get('TaxRateArea', '') or '',
        tax_rate_city=attrs.get('TaxRateCity', '') or '',
    )


def find_parcels(
    address: str,
    buffer_meters: float = 50.0,
    min_geocode_score: float = 80.0,
    rate_limit: float = DEFAULT_RATE_LIMIT,
    expand_prefix: bool = True,
) -> FindParcelsResult:
    """
    Find all parcels associated with an address.

    Given an address like "5601 NORTH PARAMOUNT BOULEVARD, Long Beach",
    this function:
    1. Geocodes the address to coordinates
    2. Queries parcels at those coordinates
    3. Extracts the common AIN prefix (map book + page)
    4. Returns ALL parcels sharing that prefix (if expand_prefix=True)

    This handles apartment complexes, condo developments, and other
    multi-parcel properties where individual units have different
    situs addresses but share a common AIN prefix.

    Args:
        address: Street address with city (e.g., "5601 N PARAMOUNT BLVD, Long Beach, CA")
        buffer_meters: Search radius around geocoded point (default 25m)
        min_geocode_score: Minimum geocode confidence score (0-100)
        rate_limit: Seconds between API requests
        expand_prefix: If True, return ALL parcels with same AIN prefix (for complexes).
                      If False, only return parcels found at geocoded location.

    Returns:
        FindParcelsResult with all associated parcels
    """
    result = FindParcelsResult(input_address=address)

    # Step 1: Geocode
    matched_addr, score, lat, lon = geocode_address(address, rate_limit)

    if not matched_addr or score is None:
        result.error = "Could not geocode address"
        return result

    if score < min_geocode_score:
        result.error = f"Geocode score {score} below minimum {min_geocode_score}"
        return result

    result.geocoded_address = matched_addr
    result.geocode_score = score
    result.lat = lat
    result.lon = lon

    # Step 2: Spatial query at coordinates
    initial_attrs = query_parcels_spatial(lat, lon, buffer_meters, rate_limit)
    result.initial_parcel_count = len(initial_attrs)

    if not initial_attrs:
        # Try larger buffer
        initial_attrs = query_parcels_spatial(lat, lon, buffer_meters * 2, rate_limit)
        result.initial_parcel_count = len(initial_attrs)

        if not initial_attrs:
            result.error = f"No parcels found at coordinates ({lat:.4f}, {lon:.4f})"
            return result

    if not expand_prefix:
        # Just return the parcels found at the geocoded location
        result.parcels = sorted(
            [_attrs_to_parcel(a) for a in initial_attrs],
            key=lambda p: p.ain
        )
        return result

    # Step 3: Try to extract tract number from legal descriptions
    tract_counts = Counter()
    for attrs in initial_attrs:
        legal_desc = attrs.get('LegalDescription', '')
        tract = extract_tract_number(legal_desc)
        if tract:
            tract_counts[tract] += 1

    # If we found tract numbers, use tract-based lookup (more precise)
    if tract_counts:
        best_tract = tract_counts.most_common(1)[0][0]
        result.tract_number = best_tract

        # Query all parcels in this tract
        all_attrs = {}
        tract_attrs = query_parcels_by_tract(best_tract, rate_limit)
        for attrs in tract_attrs:
            ain = attrs.get('AIN', '')
            if ain:
                all_attrs[ain] = attrs

        result.parcels = sorted(
            [_attrs_to_parcel(a) for a in all_attrs.values()],
            key=lambda p: p.ain
        )
        return result

    # Fallback: Use AIN prefix (less precise, for properties without tract numbers)
    prefix_counts = Counter()
    for attrs in initial_attrs:
        ain = attrs.get('AIN', '')
        if len(ain) >= 7:
            prefix_counts[ain[:7]] += 1

    if not prefix_counts:
        result.parcels = [_attrs_to_parcel(a) for a in initial_attrs]
        return result

    best_prefix = prefix_counts.most_common(1)[0][0]
    result.ain_prefix = best_prefix

    all_attrs = {}
    prefix_attrs = query_parcels_by_ain_prefix(best_prefix, rate_limit)
    for attrs in prefix_attrs:
        ain = attrs.get('AIN', '')
        if ain:
            all_attrs[ain] = attrs

    result.parcels = sorted(
        [_attrs_to_parcel(a) for a in all_attrs.values()],
        key=lambda p: p.ain
    )

    return result


if __name__ == '__main__':
    # Test with the example address
    test_address = "5601 NORTH PARAMOUNT BOULEVARD, Long Beach, CA"

    print(f"Finding parcels for: {test_address}")
    print("=" * 60)

    result = find_parcels(test_address)

    if result.error:
        print(f"ERROR: {result.error}")
    else:
        print(f"Geocoded: {result.geocoded_address} (score: {result.geocode_score})")
        print(f"Coordinates: ({result.lat:.4f}, {result.lon:.4f})")
        if result.tract_number:
            print(f"Tract: {result.tract_number}")
        if result.ain_prefix:
            print(f"AIN prefix: {result.ain_prefix}")
        print(f"Initial parcels at location: {result.initial_parcel_count}")
        print(f"Total parcels found: {len(result.parcels)}")
        print()
        print("Parcels:")
        for p in result.parcels:
            print(f"  {p.ain} | {p.situs_address} | {p.use_description}")
