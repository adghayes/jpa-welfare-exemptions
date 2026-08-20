# LA County Tax Rate Lookup

## Summary

The GIS parcel layer contains Tax Rate Area (TRA) codes but NOT actual tax rates. Actual rates can be queried from the LA County Auditor-Controller.

## GIS Layer Fields

- `TaxRateArea` - 5-digit TRA code (e.g., "00461")
- `TaxRateCity` - City name (e.g., "LOS ANGELES")

## Tax Rate API

**Endpoint**: `POST https://onlineapps.auditor.lacounty.gov/TRA/TRA/Search`

**Parameters**:
- `Area` - TRA number without leading zeros (e.g., "461" not "00461")
- `FiscalYearID` - 36 for 2025/2026, 35 for 2024/2025

**Example Request**:
```python
import requests
resp = requests.post(
    "https://onlineapps.auditor.lacounty.gov/TRA/TRA/Search",
    data={"Area": 461, "FiscalYearID": 36}
)
```

**Example Response** (TRA 461, Los Angeles):
| Taxing Agency | Rate |
|---------------|------|
| GENERAL | 1.000000 |
| UNIFIED SCHOOLS | 0.119605 |
| COMMNTY COLLEGE | 0.048543 |
| CITY-LOS ANGELES | 0.012232 |
| METRO WATER DIST | 0.007000 |
| COUNTY | 0.000000 |
| **TOTAL** | **1.18738** |

## Rate Variation

Tax rates vary by TRA across LA County:
- Range: ~1.17% to ~1.25%
- All LA city TRAs in our dataset: 1.18738%
- Base "GENERAL" rate is always 1.0% (Prop 13)

## Future Implementation

Could add `tax_rate_fetcher.py` to query rates by TRA and calculate estimated taxes:
```
Estimated Tax = Net Taxable Value × Tax Rate
```

For a parcel with:
- Net Taxable Value: $1,000,000
- TRA Rate: 1.18738%
- Estimated Annual Tax: $11,874
