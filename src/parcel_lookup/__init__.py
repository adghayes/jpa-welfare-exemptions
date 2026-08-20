"""
LA County Parcel Lookup Pipeline

Resolves addresses to parcel identifiers (AIN/APN) and fetches tax data.
"""

from .address_normalize import normalize_address, ParsedAddress
from .geocoder import geocode_address, GeocodeResult, GeocodeCandidate
from .parcel_resolver import resolve_parcels, Parcel, ParcelResult
from .tax_fetcher import get_tax_info, estimate_tax_from_parcel, TaxEstimate
from .cache import ParcelCache

__all__ = [
    'normalize_address',
    'ParsedAddress',
    'geocode_address',
    'GeocodeResult',
    'GeocodeCandidate',
    'resolve_parcels',
    'Parcel',
    'ParcelResult',
    'get_tax_info',
    'estimate_tax_from_parcel',
    'TaxEstimate',
    'ParcelCache',
]
