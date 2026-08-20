"""
Address Normalization Module

Parses and normalizes US addresses for consistent matching.
"""

import re
from dataclasses import dataclass
from typing import Optional

try:
    import usaddress
    USADDRESS_AVAILABLE = True
except ImportError:
    USADDRESS_AVAILABLE = False


# Common street suffix normalizations
SUFFIX_MAP = {
    'avenue': 'AVE', 'ave': 'AVE', 'av': 'AVE',
    'boulevard': 'BLVD', 'blvd': 'BLVD',
    'circle': 'CIR', 'cir': 'CIR',
    'court': 'CT', 'ct': 'CT',
    'drive': 'DR', 'dr': 'DR',
    'expressway': 'EXPY', 'expy': 'EXPY',
    'freeway': 'FWY', 'fwy': 'FWY',
    'highway': 'HWY', 'hwy': 'HWY',
    'lane': 'LN', 'ln': 'LN',
    'parkway': 'PKWY', 'pkwy': 'PKWY',
    'place': 'PL', 'pl': 'PL',
    'road': 'RD', 'rd': 'RD',
    'square': 'SQ', 'sq': 'SQ',
    'street': 'ST', 'str': 'ST', 'st': 'ST',
    'terrace': 'TER', 'ter': 'TER',
    'trail': 'TRL', 'trl': 'TRL',
    'way': 'WAY',
}

# Directional normalizations
DIRECTIONAL_MAP = {
    'north': 'N', 'n': 'N',
    'south': 'S', 's': 'S',
    'east': 'E', 'e': 'E',
    'west': 'W', 'w': 'W',
    'northeast': 'NE', 'ne': 'NE',
    'northwest': 'NW', 'nw': 'NW',
    'southeast': 'SE', 'se': 'SE',
    'southwest': 'SW', 'sw': 'SW',
}

# Unit type patterns
UNIT_PATTERNS = [
    r'\b(?:apt|apartment|unit|suite|ste|#|no\.?)\s*[#]?\s*(\w+)',
    r'\s+#\s*(\w+)\s*$',
]


@dataclass
class ParsedAddress:
    """Parsed and normalized address components."""
    street_number: str
    pre_directional: str  # N, S, E, W
    street_name: str
    street_suffix: str    # AVE, ST, BLVD
    post_directional: str
    unit: str             # Apt 5, #100
    city: str
    state: str
    zip_code: str
    normalized: str       # Full normalized string
    normalized_no_unit: str  # For fallback matching
    raw: str              # Original input


def normalize_suffix(suffix: str) -> str:
    """Normalize street suffix to standard abbreviation."""
    if not suffix:
        return ''
    return SUFFIX_MAP.get(suffix.lower().strip('.'), suffix.upper())


def normalize_directional(directional: str) -> str:
    """Normalize directional to standard abbreviation."""
    if not directional:
        return ''
    return DIRECTIONAL_MAP.get(directional.lower().strip('.'), directional.upper())


def extract_unit(address: str) -> tuple[str, str]:
    """Extract unit number from address string.

    Returns: (address_without_unit, unit)
    """
    for pattern in UNIT_PATTERNS:
        match = re.search(pattern, address, re.IGNORECASE)
        if match:
            unit = match.group(1) if match.lastindex else match.group(0)
            # Remove the unit part from address
            cleaned = re.sub(pattern, '', address, flags=re.IGNORECASE).strip()
            return cleaned, unit.strip()
    return address, ''


def normalize_address(raw_address: str) -> ParsedAddress:
    """
    Parse and normalize an address string.

    Args:
        raw_address: Raw address string (e.g., "123 Main St, Los Angeles, CA 90012")

    Returns:
        ParsedAddress with normalized components
    """
    if not raw_address:
        return ParsedAddress(
            street_number='', pre_directional='', street_name='',
            street_suffix='', post_directional='', unit='',
            city='', state='', zip_code='',
            normalized='', normalized_no_unit='', raw=''
        )

    # Clean input
    raw_address = raw_address.strip()
    raw_clean = re.sub(r'\s+', ' ', raw_address)  # Collapse whitespace

    # Extract unit first (before usaddress parsing)
    address_no_unit, unit = extract_unit(raw_clean)

    # Initialize components
    components = {
        'AddressNumber': '',
        'StreetNamePreDirectional': '',
        'StreetName': '',
        'StreetNamePostType': '',
        'StreetNamePostDirectional': '',
        'PlaceName': '',
        'StateName': '',
        'ZipCode': '',
    }

    if USADDRESS_AVAILABLE:
        try:
            parsed, addr_type = usaddress.tag(address_no_unit)
            components.update(parsed)
        except usaddress.RepeatedLabelError:
            # Fallback to simple parsing
            components = _simple_parse(address_no_unit)
    else:
        components = _simple_parse(address_no_unit)

    # Normalize components
    street_number = components.get('AddressNumber', '').strip()
    pre_dir = normalize_directional(components.get('StreetNamePreDirectional', ''))
    street_name = components.get('StreetName', '').upper().strip()
    street_suffix = normalize_suffix(components.get('StreetNamePostType', ''))
    post_dir = normalize_directional(components.get('StreetNamePostDirectional', ''))
    city = components.get('PlaceName', '').upper().strip()
    state = components.get('StateName', '').upper().strip()
    zip_code = components.get('ZipCode', '').strip()

    # Handle case where unit might be in the parsed components
    if not unit:
        occ_type = components.get('OccupancyType', '')
        occ_id = components.get('OccupancyIdentifier', '')
        if occ_type or occ_id:
            unit = f"{occ_type} {occ_id}".strip()

    # Build normalized strings
    street_parts = [p for p in [street_number, pre_dir, street_name, street_suffix, post_dir] if p]
    street_str = ' '.join(street_parts)

    normalized_no_unit = ', '.join([p for p in [street_str, city, state, zip_code] if p])

    if unit:
        normalized = f"{street_str} {unit}, {city}, {state} {zip_code}".strip(', ')
    else:
        normalized = normalized_no_unit

    return ParsedAddress(
        street_number=street_number,
        pre_directional=pre_dir,
        street_name=street_name,
        street_suffix=street_suffix,
        post_directional=post_dir,
        unit=unit.upper() if unit else '',
        city=city,
        state=state,
        zip_code=zip_code,
        normalized=normalized,
        normalized_no_unit=normalized_no_unit,
        raw=raw_address
    )


def _simple_parse(address: str) -> dict:
    """Simple regex-based address parsing fallback."""
    components = {}

    # Try to extract ZIP code
    zip_match = re.search(r'\b(\d{5})(?:-\d{4})?\b', address)
    if zip_match:
        components['ZipCode'] = zip_match.group(1)
        address = address[:zip_match.start()].strip(' ,')

    # Try to extract state
    state_match = re.search(r'\b(CA|CALIFORNIA)\b', address, re.IGNORECASE)
    if state_match:
        components['StateName'] = 'CA'
        address = address[:state_match.start()].strip(' ,')

    # Split remaining by comma
    parts = [p.strip() for p in address.split(',')]

    if len(parts) >= 2:
        components['PlaceName'] = parts[-1]
        street = parts[0]
    else:
        street = parts[0]

    # Parse street
    street_parts = street.split()
    if street_parts:
        # First part is usually number
        if street_parts[0].isdigit() or re.match(r'^\d+[A-Za-z]?$', street_parts[0]):
            components['AddressNumber'] = street_parts[0]
            street_parts = street_parts[1:]

        # Check for directional
        if street_parts and street_parts[0].lower() in DIRECTIONAL_MAP:
            components['StreetNamePreDirectional'] = street_parts[0]
            street_parts = street_parts[1:]

        # Last part might be suffix
        if street_parts and street_parts[-1].lower().strip('.') in SUFFIX_MAP:
            components['StreetNamePostType'] = street_parts[-1]
            street_parts = street_parts[:-1]

        # Remaining is street name
        if street_parts:
            components['StreetName'] = ' '.join(street_parts)

    return components


if __name__ == '__main__':
    # Test with sample addresses
    test_addresses = [
        "103 S EDGEMONT ST LOS ANGELES CA 90004-5551",
        "1057 S WESTERN AVE LOS ANGELES CA 90006-2344",
        "10705 AVALON BLVD LOS ANGELES CA 90061-2521",
        "1422 6TH ST SANTA MONICA CA 90401-2542",
        "12300 Sherman Way, North Hollywood, CA 91605",
    ]

    for addr in test_addresses:
        parsed = normalize_address(addr)
        print(f"Raw: {addr}")
        print(f"  Normalized: {parsed.normalized}")
        print(f"  Number: {parsed.street_number}, Street: {parsed.street_name}, Suffix: {parsed.street_suffix}")
        print(f"  City: {parsed.city}, State: {parsed.state}, ZIP: {parsed.zip_code}")
        print()
