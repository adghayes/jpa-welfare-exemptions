"""Validate parcel assignments and probe for missing multi-parcel siblings.

The address -> AIN link is the one step in the pipeline that is asserted by
a human rather than generated, so this stage checks those assertions:

1. assignment-mismatch — the assigned parcel's county situs disagrees with
   the grant's address at the STREET or CITY level. House-number near-misses
   (6838 vs 6840) pass silently: new construction routinely carries a situs
   adjacent to its marketing address. Assignments whose notes document why
   the situs won't match (portal map picks, in-transition AINs, common-area
   lots) are skipped.
2. possible-missing-parcel — another parcel shares an assigned parcel's
   exact situs but is not assigned to any project (how Hansen Village's
   second parcel would have been caught).
3. units-undercount — the property's parcels account for well under the
   grant's documented unit count (< UNITS_RATIO), using county unit fields
   (LA Units1-5, SANDAG unitqty). Only checked when the county reports
   nonzero units, so pre-development lots don't false-alarm.

Reads the last-built output/dataset/grants.csv for grant addresses; run
build_dataset once before this, and again after to fold the findings in.

Output: output/pipeline/assignment_checks.csv (check, project_id, detail)

Usage:
    python scripts/check_parcel_assignments.py
"""

import csv
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from scripts.build_basic_list import ABBREV  # noqa: E402

LA_URL = "https://cache.gis.lacounty.gov/cache/rest/services/LACounty_Cache/LACounty_Parcel/MapServer/0/query"
SD_URL = "https://geo.sandag.org/server/rest/services/Hosted/Parcels/FeatureServer/0/query"
OUT = Path("output/pipeline/assignment_checks.csv")
UNITS_RATIO = 0.7
RATE_LIMIT = 0.3

SKIP_NOTE = re.compile(r"no county situs|in transition|renumbered|common-area|portal map|situs reviewed",
                       re.IGNORECASE)
DIRECTIONALS = {"n", "s", "e", "w", "north", "south", "east", "west"}
NOISE = {"ca", "california", "ave", "st", "blvd", "dr", "pl", "rd", "way", "ln",
         "lane", "court", "ct", "cir", "hwy", "unit", "apt", "no"}
# City-of-LA neighborhoods that appear as "city" in addresses but situs says LOS ANGELES
LA_NEIGHBORHOODS = {"westchester", "canoga park", "lake view terrace", "north hollywood",
                    "van nuys", "reseda", "winnetka", "san pedro", "wilmington",
                    "hollywood", "north hills", "panorama city", "sylmar", "tarzana",
                    "sherman oaks", "studio city", "encino", "chatsworth", "pacoima",
                    "sun valley", "tujunga", "playa del rey", "venice", "harbor city"}

findings: list[dict] = []


def emit(check: str, pid: str, detail: str) -> None:
    findings.append({"check": check, "project_id": pid, "detail": detail})


def street_name_tokens(segment: str) -> set[str]:
    """Street-NAME tokens from a street segment: drop the leading house
    number, directionals, and a trailing suffix — keep everything else,
    so streets named after cities (San Pedro St) survive."""
    toks = [ABBREV.get(w, w) for w in
            str(segment).lower().replace(".", "").replace(",", " ").split()]
    if toks and re.fullmatch(r"[\d-]+", toks[0]):
        toks = toks[1:]
    while toks and toks[0] in DIRECTIONALS:
        toks = toks[1:]
    if len(toks) > 1 and toks[-1] in NOISE:
        toks = toks[:-1]
    return {t for t in toks if not re.fullmatch(r"\d{5}(-\d+)?", t)}


def address_street_tokens(address: str) -> set[str]:
    """The address's street segment is everything before the first comma."""
    return street_name_tokens(str(address).split(",")[0])


def situs_street_tokens(v: dict) -> set[str]:
    """Prefer the county's own street field; fall back to parsing the full
    situs (street = tokens after house number, before trailing city/state)."""
    street = str(v.get("situs_street", "")).strip()
    if street:
        return street_name_tokens(street)
    toks = str(v.get("situs_address", "")).split()
    if toks and "CA" in toks:
        toks = toks[:toks.index("CA")]
    return street_name_tokens(" ".join(toks[:4]))


def get_json(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def main() -> None:
    assignments = pd.read_csv("manual/parcel_assignments.csv", dtype=str).fillna("")
    assignments = assignments[(assignments["ain"].str.strip() != "")
                              & (assignments["legacy_redundant"].str.lower() != "true")]
    grants_path = Path("output/dataset/grants.csv")
    if not grants_path.exists():
        sys.exit("output/dataset/grants.csv not found — run scripts/build_dataset.py "
                 "first (this check validates against the last build, then "
                 "build_dataset.py again folds the findings in)")
    grants = pd.read_csv(grants_path, dtype=str).fillna("")
    ginfo = grants.set_index("project_id")[
        ["address", "city", "county", "total_units", "authorization_status",
         "manual_status", "property_name"]].to_dict("index")

    values = {}
    for path in sorted(Path("output/pipeline").glob("*_roll_values.csv")):
        df = pd.read_csv(path, dtype=str).fillna("")
        for _, r in df.iterrows():
            values[r["ain"]] = r.to_dict()

    # ---- 1. situs vs address validation (per property, noise-gated) --------
    SD_JURIS = {"SD": "san diego", "CN": "unincorporated", "CB": "carlsbad",
                "LM": "la mesa", "ES": "escondido", "CV": "chula vista",
                "EC": "el cajon", "NC": "national city", "OC": "oceanside",
                "VS": "vista", "SM": "san marcos", "PW": "poway"}
    n_checked = 0
    for pid, grp in assignments.groupby("project_id"):
        g = ginfo.get(pid)
        if not g:
            continue
        address = str(g["address"]).strip()
        if not address:
            continue
        st_addr = address_street_tokens(address)
        any_situs, any_street_match, samples = False, False, []
        for _, a in grp.iterrows():
            v = values.get(a["ain"])
            if not v or SKIP_NOTE.search(a["notes"]):
                continue
            situs = str(v.get("situs_address", "")).strip()
            if not situs:
                continue
            any_situs = True
            if situs_street_tokens(v) & st_addr:
                any_street_match = True
                break
            samples.append(f"{a['ain']}: {situs}")
        if not any_situs:
            continue
        n_checked += 1
        if not any_street_match:
            emit("assignment-mismatch", pid,
                 f"{g['property_name'][:32]}: no assigned parcel's situs matches "
                 f"address {address[:50]!r} (e.g. {'; '.join(samples[:2])})")
            continue
        # street agrees somewhere; city sanity check (Ingraham failure mode)
        g_city = str(g["city"]).lower().strip()
        if not g_city or g_city in LA_NEIGHBORHOODS or g_city == "unincorporated":
            continue
        city_ok = False
        for _, a in grp.iterrows():
            v = values.get(a["ain"])
            if not v:
                continue
            situs = str(v.get("situs_address", "")).lower()
            juris = SD_JURIS.get(situs.split()[-1].upper() if situs else "", "")
            if (g_city in situs or juris == g_city
                    or any(w in situs for w in g_city.split() if len(w) > 3)
                    or (g_city == "los angeles"
                        and any(n in situs for n in LA_NEIGHBORHOODS))):
                city_ok = True
                break
        if any_situs and not city_ok and g["county"] != "San Diego":
            emit("assignment-mismatch", pid,
                 f"{g['property_name'][:32]}: grant city {g['city']!r} appears in "
                 f"no assigned parcel's situs")

    # ---- 1b. per-AIN verdicts (drives the workbook's AIN tint) --------------
    verdicts = []
    for _, a in assignments.iterrows():
        g = ginfo.get(a["project_id"], {})
        v = values.get(a["ain"])
        if SKIP_NOTE.search(a["notes"]):
            verdict = "documented"       # human documented why situs won't match
        elif not v or not str(v.get("situs_address", "")).strip():
            verdict = "no-situs"         # county has no situs record (new lot)
        else:
            address = str(g.get("address", "")).strip() or a["situs_address"]
            if not str(address).strip():
                verdict = "no-address"
            elif situs_street_tokens(v) & address_street_tokens(str(address)):
                verdict = "situs-match"
            else:
                verdict = "mismatch"
        verdicts.append({"project_id": a["project_id"], "ain": a["ain"],
                         "assignment_check": verdict})
    pd.DataFrame(verdicts).to_csv("output/pipeline/assignment_verdicts.csv", index=False)

    # ---- 2. same-situs sweep (LA + SD) --------------------------------------
    assigned_ains = set(assignments["ain"])
    # AINs mentioned in a project's assignment notes (e.g. "siblings excluded:
    # ...") are reviewed decisions — never re-proposed
    noted_ains: dict[str, set] = {}
    for _, a in assignments.iterrows():
        for tok in re.findall(r"\b\d{7,14}\b", a["notes"]):
            noted_ains.setdefault(a["project_id"], set()).add(tok)
    seen_situs = set()
    for _, a in assignments.iterrows():
        v = values.get(a["ain"])
        if not v:
            continue
        situs = v.get("situs_address", "").strip()
        county = a["county"]
        g = ginfo.get(a["project_id"], {})
        if not situs or county not in ("Los Angeles", "San Diego"):
            continue
        # only sweep residential properties that are live
        if g.get("manual_status") == "dead":
            continue
        key = (county, situs.split(" UNIT ")[0].split(" NO ")[0])
        if key in seen_situs:
            continue
        seen_situs.add(key)
        parts = situs.split()
        house = parts[0]
        try:
            if county == "Los Angeles":
                v_house = str(v.get("situs_house_no", "")).strip()
                v_street = str(v.get("situs_street", "")).strip().replace("'", "''")
                if not v_house or not v_street:
                    continue
                own_dir = ""
                m_dir = re.match(r"^\S+\s+([NSEW])\s+\S", situs)
                if m_dir:
                    own_dir = m_dir.group(1)
                where = f"SitusHouseNo = '{v_house}' AND SitusStreet = '{v_street}'"
                where += (f" AND SitusDirection = '{own_dir}'" if own_dir
                          else " AND (SitusDirection IS NULL OR SitusDirection = '')")
                q = {"where": where,
                     "outFields": "AIN,SitusFullAddress,SitusZIP,UseDescription,Roll_LandValue,Roll_ImpValue",
                     "returnGeometry": "false", "f": "json"}
                data = get_json(f"{LA_URL}?{urllib.parse.urlencode(q)}")
                feats = [f["attributes"] for f in data.get("features", [])]
                # same street NAME exists across the county; require same ZIP
                # (skip the leading house number — it can be 5 digits too)
                own_zip = None
                for tok in situs.split()[1:]:
                    if re.fullmatch(r"\d{5}(-\d+)?", tok):
                        own_zip = tok[:5]
                sibs = [(str(f["AIN"]), f.get("SitusFullAddress", ""),
                         f.get("UseDescription", "") or "",
                         (f.get("Roll_LandValue") or 0) + (f.get("Roll_ImpValue") or 0))
                        for f in feats
                        if not own_zip or str(f.get("SitusZIP", ""))[:5] == own_zip]
            else:
                v_street = str(v.get("situs_street", "")).strip().upper().replace("'", "''")
                v_juris = str(v.get("situs_juris", "")).strip().upper()
                if not v_street:
                    continue
                q = {"where": (f"situs_address = {house} AND UPPER(situs_street) = '{v_street}'"
                               + (f" AND UPPER(situs_juris) = '{v_juris}'" if v_juris else "")),
                     "outFields": "apn,situs_address,situs_street,situs_juris,asr_landuse,asr_total",
                     "returnGeometry": "false", "f": "json"}
                data = get_json(f"{SD_URL}?{urllib.parse.urlencode(q)}")
                feats = [f["attributes"] for f in data.get("features", [])]
                sibs = [(str(f["apn"]), f"{f.get('situs_address','')} {f.get('situs_street','')} {f.get('situs_juris','')}",
                         str(f.get("asr_landuse", "")), f.get("asr_total") or 0)
                        for f in feats]
        except Exception as exc:
            emit("sweep-error", a["project_id"], f"situs sweep failed for {situs!r}: {exc}")
            time.sleep(RATE_LIMIT)
            continue
        time.sleep(RATE_LIMIT)

        missing = [(ain, s, use, tot) for ain, s, use, tot in sibs
                   if ain not in assigned_ains
                   and ain not in noted_ains.get(a["project_id"], set())]
        for ain, s, use, tot in missing[:5]:
            emit("possible-missing-parcel", a["project_id"],
                 f"{g.get('property_name','')[:30]}: parcel {ain} shares situs "
                 f"{s!r} ({use[:24]}, ${tot:,.0f}) but is not assigned to any project")

    # ---- 3. units reconciliation --------------------------------------------
    by_prop: dict[str, list] = {}
    units_reviewed: set = set()
    for _, a in assignments.iterrows():
        by_prop.setdefault(a["project_id"], []).append(a["ain"])
        if "units reviewed" in a["notes"].lower():
            units_reviewed.add(a["project_id"])
    for pid, ains in by_prop.items():
        g = ginfo.get(pid)
        if (not g or g["manual_status"] == "dead"
                or g["authorization_status"] != "operative"
                or pid in units_reviewed):
            continue
        try:
            grant_units = float(str(g["total_units"]).replace(",", ""))
        except ValueError:
            continue
        county_units = 0.0
        any_units = False
        apartment_use = False
        for ain in ains:
            v = values.get(ain)
            if not v:
                continue
            if "apartment" in str(v.get("use_description", "")).lower():
                apartment_use = True
            if str(v.get("units", "")).strip() not in ("", "0", "0.0"):
                any_units = True
                county_units += float(v["units"])
        # LA unit fields are only trustworthy on apartment-use parcels
        # (86% within 20% of the grant; pre-development lots are wild)
        if (any_units and apartment_use and grant_units > 0
                and county_units < UNITS_RATIO * grant_units):
            emit("units-undercount", pid,
                 f"{g['property_name'][:34]}: county records {county_units:.0f} units "
                 f"on assigned parcels vs {grant_units:.0f} in the grant — "
                 "parcels may be missing")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["check", "project_id", "detail"])
        w.writeheader()
        w.writerows(findings)
    counts = pd.Series([f["check"] for f in findings]).value_counts().to_dict() if findings else {}
    vc = pd.Series([v["assignment_check"] for v in verdicts]).value_counts().to_dict()
    print(f"checked situs on {n_checked} properties; verdicts {vc}")
    print(f"wrote {OUT}: {len(findings)} findings {counts}")


if __name__ == "__main__":
    main()
