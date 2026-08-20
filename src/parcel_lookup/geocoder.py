"""
CAMS Geocoder Client

Geocodes addresses using LA County's CAMS Locator service.
"""

import time
from dataclasses import dataclass
from typing import Optional

import requests


GEOCODE_URL = "https://public.gis.lacounty.gov/public/rest/services/CAMS_Locator/GeocodeServer/findAddressCandidates"

# Rate limiting
DEFAULT_RATE_LIMIT = 0.5  # seconds between requests
_last_request_time = 0


@dataclass
class GeocodeCandidate:
    """A geocoding candidate result."""
    address: str           # Matched/standardized address
    score: float           # Match score (0-100)
    x: float               # Longitude
    y: float               # Latitude
    attributes: dict       # Additional attributes from the geocoder


@dataclass
class GeocodeResult:
    """Result of a geocoding request."""
    input_address: str
    candidates: list[GeocodeCandidate]
    best_match: Optional[GeocodeCandidate]
    error: Optional[str]


def geocode_address(
    address: str,
    max_candidates: int = 5,
    min_score: float = 80.0,
    rate_limit: float = DEFAULT_RATE_LIMIT
) -> GeocodeResult:
    """
    Geocode an address using LA County CAMS Locator.

    Args:
        address: Address string to geocode
        max_candidates: Maximum number of candidates to return
        min_score: Minimum match score to consider
        rate_limit: Seconds to wait between requests

    Returns:
        GeocodeResult with candidates and best match
    """
    global _last_request_time

    if not address or not address.strip():
        return GeocodeResult(
            input_address=address,
            candidates=[],
            best_match=None,
            error="Empty address"
        )

    # Rate limiting
    elapsed = time.time() - _last_request_time
    if elapsed < rate_limit:
        time.sleep(rate_limit - elapsed)

    params = {
        "Single Line Input": address,
        "f": "json",
        "outSR": 4326,  # WGS84 lat/lon
        "maxLocations": max_candidates,
    }

    try:
        _last_request_time = time.time()
        response = requests.get(GEOCODE_URL, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
    except requests.RequestException as e:
        return GeocodeResult(
            input_address=address,
            candidates=[],
            best_match=None,
            error=f"Request failed: {e}"
        )

    # Parse candidates
    candidates = []
    raw_candidates = data.get("candidates", [])

    for raw in raw_candidates:
        score = raw.get("score", 0)
        if score < min_score:
            continue

        location = raw.get("location", {})
        candidate = GeocodeCandidate(
            address=raw.get("address", ""),
            score=score,
            x=location.get("x", 0),
            y=location.get("y", 0),
            attributes=raw.get("attributes", {})
        )
        candidates.append(candidate)

    # Sort by score descending
    candidates.sort(key=lambda c: c.score, reverse=True)

    # Best match is highest scoring
    best_match = candidates[0] if candidates else None

    return GeocodeResult(
        input_address=address,
        candidates=candidates,
        best_match=best_match,
        error=None
    )


def geocode_batch(
    addresses: list[str],
    rate_limit: float = DEFAULT_RATE_LIMIT,
    progress_callback=None
) -> list[GeocodeResult]:
    """
    Geocode multiple addresses.

    Args:
        addresses: List of address strings
        rate_limit: Seconds between requests
        progress_callback: Optional callback(index, total, result)

    Returns:
        List of GeocodeResult objects
    """
    results = []
    total = len(addresses)

    for i, addr in enumerate(addresses):
        result = geocode_address(addr, rate_limit=rate_limit)
        results.append(result)

        if progress_callback:
            progress_callback(i, total, result)

    return results


if __name__ == "__main__":
    # Test with sample LA County addresses
    test_addresses = [
        "103 S EDGEMONT ST LOS ANGELES CA 90004",
        "1057 S WESTERN AVE LOS ANGELES CA 90006",
        "10705 AVALON BLVD LOS ANGELES CA 90061",
        "12300 SHERMAN WAY NORTH HOLLYWOOD CA 91605",
        "1422 6TH ST SANTA MONICA CA 90401",
    ]

    print("Testing CAMS Geocoder...\n")

    for addr in test_addresses:
        result = geocode_address(addr, rate_limit=0.5)

        print(f"Input: {addr}")
        if result.error:
            print(f"  ERROR: {result.error}")
        elif result.best_match:
            bm = result.best_match
            print(f"  Match: {bm.address}")
            print(f"  Score: {bm.score}")
            print(f"  Coords: ({bm.y:.6f}, {bm.x:.6f})")
        else:
            print(f"  No match found")
        print()
