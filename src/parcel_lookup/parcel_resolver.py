"""
LA County Parcel Resolver

Queries the LA County Parcel MapServer to find parcels at given coordinates.
"""

import time
from dataclasses import dataclass
from typing import Optional

import requests


PARCEL_URL = "https://cache.gis.lacounty.gov/cache/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"

# Rate limiting
DEFAULT_RATE_LIMIT = 0.5  # seconds between requests
_last_request_time = 0

# Fields to retrieve from parcel layer
DEFAULT_FIELDS = [
    "AIN",
    "APN",
    "SitusFullAddress",
    "SitusStreet",
    "SitusCity",
    "SitusZIP",
    "UseDescription",
    "UseType",
    "Roll_Year",
    "Roll_LandValue",
    "Roll_ImpValue",
    "Roll_HomeOwnersExemp",
    "Roll_RealEstateExemp",
    "Roll_PersPropExemp",
    "Roll_FixtureExemp",
    "TaxRateArea",
    "TaxRateCity",
]


@dataclass
class Parcel:
    """A parcel from the LA County parcel layer."""
    ain: str                     # 10-digit Assessor's Identification Number
    apn: str                     # 12-digit formatted APN (e.g., "5518-013-007")
    situs_address: str           # Full situs address
    situs_street: str            # Street portion only
    situs_city: str              # City
    situs_zip: str               # ZIP code
    use_description: str         # Property use description
    use_type: str                # Use type code
    roll_year: Optional[int]     # Assessment roll year
    roll_land_value: Optional[float]   # Land assessed value
    roll_imp_value: Optional[float]    # Improvement assessed value
    # Exemption values from roll
    homeowners_exemp: Optional[float]
    real_estate_exemp: Optional[float]  # Welfare exemption would appear here
    pers_prop_exemp: Optional[float]
    fixture_exemp: Optional[float]
    tax_rate_area: str           # Tax rate area code
    tax_rate_city: str           # Tax rate city
    attributes: dict             # All raw attributes


@dataclass
class ParcelResult:
    """Result of a parcel spatial query."""
    lat: float
    lon: float
    parcels: list[Parcel]
    error: Optional[str]


def resolve_parcels(
    lat: float,
    lon: float,
    buffer_meters: float = 10.0,
    rate_limit: float = DEFAULT_RATE_LIMIT,
    fields: list[str] = None
) -> ParcelResult:
    """
    Query LA County parcel layer to find parcels at given coordinates.

    Args:
        lat: Latitude (WGS84)
        lon: Longitude (WGS84)
        buffer_meters: Search buffer in meters (default 10m)
        rate_limit: Seconds to wait between requests
        fields: Fields to retrieve (default: DEFAULT_FIELDS)

    Returns:
        ParcelResult with list of matching parcels
    """
    global _last_request_time

    if fields is None:
        fields = DEFAULT_FIELDS

    # Rate limiting
    elapsed = time.time() - _last_request_time
    if elapsed < rate_limit:
        time.sleep(rate_limit - elapsed)

    params = {
        "geometry": f"{lon},{lat}",
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "distance": buffer_meters,
        "units": "esriSRUnit_Meter",
        "outFields": ",".join(fields),
        "returnGeometry": "false",
        "f": "json",
    }

    try:
        _last_request_time = time.time()
        response = requests.get(PARCEL_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return ParcelResult(
            lat=lat,
            lon=lon,
            parcels=[],
            error=f"Request failed: {e}"
        )

    # Check for API error
    if "error" in data:
        return ParcelResult(
            lat=lat,
            lon=lon,
            parcels=[],
            error=f"API error: {data['error'].get('message', 'Unknown')}"
        )

    # Parse features
    parcels = []
    for feature in data.get("features", []):
        attrs = feature.get("attributes", {})

        # Parse roll values
        roll_year = attrs.get("Roll_Year")
        roll_land = attrs.get("Roll_LandValue")
        roll_imp = attrs.get("Roll_ImpValue")

        # Parse exemption values
        ho_exemp = attrs.get("Roll_HomeOwnersExemp")
        re_exemp = attrs.get("Roll_RealEstateExemp")
        pp_exemp = attrs.get("Roll_PersPropExemp")
        fix_exemp = attrs.get("Roll_FixtureExemp")

        parcel = Parcel(
            ain=attrs.get("AIN", ""),
            apn=attrs.get("APN", ""),
            situs_address=attrs.get("SitusFullAddress", ""),
            situs_street=attrs.get("SitusStreet", ""),
            situs_city=attrs.get("SitusCity", ""),
            situs_zip=attrs.get("SitusZIP", ""),
            use_description=attrs.get("UseDescription", ""),
            use_type=attrs.get("UseType", ""),
            roll_year=int(roll_year) if roll_year else None,
            roll_land_value=float(roll_land) if roll_land else None,
            roll_imp_value=float(roll_imp) if roll_imp else None,
            homeowners_exemp=float(ho_exemp) if ho_exemp else None,
            real_estate_exemp=float(re_exemp) if re_exemp else None,
            pers_prop_exemp=float(pp_exemp) if pp_exemp else None,
            fixture_exemp=float(fix_exemp) if fix_exemp else None,
            tax_rate_area=attrs.get("TaxRateArea", ""),
            tax_rate_city=attrs.get("TaxRateCity", ""),
            attributes=attrs
        )
        parcels.append(parcel)

    return ParcelResult(
        lat=lat,
        lon=lon,
        parcels=parcels,
        error=None
    )


def resolve_parcels_batch(
    coordinates: list[tuple[float, float]],
    rate_limit: float = DEFAULT_RATE_LIMIT,
    progress_callback=None
) -> list[ParcelResult]:
    """
    Query parcels for multiple coordinate pairs.

    Args:
        coordinates: List of (lat, lon) tuples
        rate_limit: Seconds between requests
        progress_callback: Optional callback(index, total, result)

    Returns:
        List of ParcelResult objects
    """
    results = []
    total = len(coordinates)

    for i, (lat, lon) in enumerate(coordinates):
        result = resolve_parcels(lat, lon, rate_limit=rate_limit)
        results.append(result)

        if progress_callback:
            progress_callback(i, total, result)

    return results


def resolve_parcel_by_ain(
    ain: str,
    rate_limit: float = DEFAULT_RATE_LIMIT,
    fields: list[str] = None
) -> Optional[Parcel]:
    """
    Query LA County parcel layer by AIN (Assessor's Identification Number).

    Args:
        ain: 10-digit AIN (with or without dashes)
        rate_limit: Seconds to wait between requests
        fields: Fields to retrieve (default: DEFAULT_FIELDS)

    Returns:
        Parcel object if found, None otherwise
    """
    global _last_request_time

    if fields is None:
        fields = DEFAULT_FIELDS

    # Normalize AIN - remove dashes and ensure 10 digits
    ain_clean = ain.replace("-", "").strip()
    if len(ain_clean) != 10:
        return None

    # Rate limiting
    elapsed = time.time() - _last_request_time
    if elapsed < rate_limit:
        time.sleep(rate_limit - elapsed)

    params = {
        "where": f"AIN = '{ain_clean}'",
        "outFields": ",".join(fields),
        "returnGeometry": "false",
        "f": "json",
    }

    try:
        _last_request_time = time.time()
        response = requests.get(PARCEL_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        print(f"Request failed for AIN {ain_clean}: {e}")
        return None

    # Check for API error
    if "error" in data:
        print(f"API error for AIN {ain_clean}: {data['error'].get('message', 'Unknown')}")
        return None

    # Parse first feature (AIN should be unique)
    features = data.get("features", [])
    if not features:
        return None

    attrs = features[0].get("attributes", {})

    # Parse roll values
    roll_year = attrs.get("Roll_Year")
    roll_land = attrs.get("Roll_LandValue")
    roll_imp = attrs.get("Roll_ImpValue")

    # Parse exemption values
    ho_exemp = attrs.get("Roll_HomeOwnersExemp")
    re_exemp = attrs.get("Roll_RealEstateExemp")
    pp_exemp = attrs.get("Roll_PersPropExemp")
    fix_exemp = attrs.get("Roll_FixtureExemp")

    return Parcel(
        ain=attrs.get("AIN", ""),
        apn=attrs.get("APN", ""),
        situs_address=attrs.get("SitusFullAddress", ""),
        situs_street=attrs.get("SitusStreet", ""),
        situs_city=attrs.get("SitusCity", ""),
        situs_zip=attrs.get("SitusZIP", ""),
        use_description=attrs.get("UseDescription", ""),
        use_type=attrs.get("UseType", ""),
        roll_year=int(roll_year) if roll_year else None,
        roll_land_value=float(roll_land) if roll_land else None,
        roll_imp_value=float(roll_imp) if roll_imp else None,
        homeowners_exemp=float(ho_exemp) if ho_exemp else None,
        real_estate_exemp=float(re_exemp) if re_exemp else None,
        pers_prop_exemp=float(pp_exemp) if pp_exemp else None,
        fixture_exemp=float(fix_exemp) if fix_exemp else None,
        tax_rate_area=attrs.get("TaxRateArea", ""),
        tax_rate_city=attrs.get("TaxRateCity", ""),
        attributes=attrs
    )


if __name__ == "__main__":
    # Test with coordinates from geocoded addresses
    test_coords = [
        (34.0727, -118.2976),  # 103 S Edgemont St
        (34.0517, -118.3091),  # 1057 S Western Ave
        (33.9392, -118.2652),  # 10705 Avalon Blvd
    ]

    print("Testing LA County Parcel Resolver...\n")

    for lat, lon in test_coords:
        result = resolve_parcels(lat, lon, rate_limit=0.5)

        print(f"Coords: ({lat:.4f}, {lon:.4f})")
        if result.error:
            print(f"  ERROR: {result.error}")
        elif result.parcels:
            for p in result.parcels:
                print(f"  AIN: {p.ain} | APN: {p.apn}")
                print(f"  Address: {p.situs_address}")
                print(f"  Use: {p.use_description}")
                if p.roll_land_value or p.roll_imp_value:
                    total = (p.roll_land_value or 0) + (p.roll_imp_value or 0)
                    print(f"  Assessed: ${total:,.0f}")
        else:
            print("  No parcels found")
        print()
