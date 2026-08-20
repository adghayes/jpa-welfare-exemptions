"""
SQLite Caching Layer

Caches geocoding and parcel query results to avoid redundant API calls.
"""

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .geocoder import GeocodeResult, GeocodeCandidate
from .parcel_resolver import ParcelResult, Parcel


DEFAULT_CACHE_PATH = Path(__file__).parent.parent.parent / "data" / "parcel_lookup" / "parcel_cache.db"
DEFAULT_TTL_DAYS = 7


class ParcelCache:
    """SQLite-backed cache for geocoding and parcel data."""

    def __init__(self, db_path: Path = None, ttl_days: int = DEFAULT_TTL_DAYS):
        """
        Initialize cache.

        Args:
            db_path: Path to SQLite database (default: data/parcel_cache.db)
            ttl_days: Cache time-to-live in days
        """
        self.db_path = db_path or DEFAULT_CACHE_PATH
        self.ttl_days = ttl_days
        self._ensure_db()

    def _ensure_db(self):
        """Create database and tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS geocode_cache (
                    normalized_address TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    fetched_at TIMESTAMP NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS parcel_cache (
                    lat_lon_key TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    fetched_at TIMESTAMP NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tax_cache (
                    ain TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    fetched_at TIMESTAMP NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS ain_cache (
                    ain TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    fetched_at TIMESTAMP NOT NULL
                )
            """)
            conn.commit()

    def _is_fresh(self, fetched_at: str) -> bool:
        """Check if cached entry is still fresh."""
        fetched = datetime.fromisoformat(fetched_at)
        return datetime.now() - fetched < timedelta(days=self.ttl_days)

    def _coords_to_key(self, lat: float, lon: float, precision: int = 6) -> str:
        """Convert coordinates to cache key (rounded to ~0.1m precision)."""
        return f"{round(lat, precision)},{round(lon, precision)}"

    # --- Geocode Cache ---

    def get_geocode(self, normalized_address: str) -> Optional[GeocodeResult]:
        """Get cached geocode result."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT result_json, fetched_at FROM geocode_cache WHERE normalized_address = ?",
                (normalized_address,)
            ).fetchone()

            if row and self._is_fresh(row[1]):
                return self._deserialize_geocode(json.loads(row[0]))
        return None

    def set_geocode(self, normalized_address: str, result: GeocodeResult):
        """Cache geocode result."""
        data = self._serialize_geocode(result)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO geocode_cache (normalized_address, result_json, fetched_at)
                VALUES (?, ?, ?)
            """, (normalized_address, json.dumps(data), datetime.now().isoformat()))
            conn.commit()

    def _serialize_geocode(self, result: GeocodeResult) -> dict:
        """Convert GeocodeResult to serializable dict."""
        return {
            "input_address": result.input_address,
            "candidates": [
                {
                    "address": c.address,
                    "score": c.score,
                    "x": c.x,
                    "y": c.y,
                    "attributes": c.attributes
                }
                for c in result.candidates
            ],
            "error": result.error
        }

    def _deserialize_geocode(self, data: dict) -> GeocodeResult:
        """Convert dict to GeocodeResult."""
        candidates = [
            GeocodeCandidate(
                address=c["address"],
                score=c["score"],
                x=c["x"],
                y=c["y"],
                attributes=c["attributes"]
            )
            for c in data.get("candidates", [])
        ]
        return GeocodeResult(
            input_address=data["input_address"],
            candidates=candidates,
            best_match=candidates[0] if candidates else None,
            error=data.get("error")
        )

    # --- Parcel Cache ---

    def get_parcels(self, lat: float, lon: float) -> Optional[ParcelResult]:
        """Get cached parcel result."""
        key = self._coords_to_key(lat, lon)
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT result_json, fetched_at FROM parcel_cache WHERE lat_lon_key = ?",
                (key,)
            ).fetchone()

            if row and self._is_fresh(row[1]):
                return self._deserialize_parcels(json.loads(row[0]))
        return None

    def set_parcels(self, lat: float, lon: float, result: ParcelResult):
        """Cache parcel result."""
        key = self._coords_to_key(lat, lon)
        data = self._serialize_parcels(result)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO parcel_cache (lat_lon_key, result_json, fetched_at)
                VALUES (?, ?, ?)
            """, (key, json.dumps(data), datetime.now().isoformat()))
            conn.commit()

    def _serialize_parcels(self, result: ParcelResult) -> dict:
        """Convert ParcelResult to serializable dict."""
        return {
            "lat": result.lat,
            "lon": result.lon,
            "parcels": [
                {
                    "ain": p.ain,
                    "apn": p.apn,
                    "situs_address": p.situs_address,
                    "situs_street": p.situs_street,
                    "situs_city": p.situs_city,
                    "situs_zip": p.situs_zip,
                    "use_description": p.use_description,
                    "use_type": p.use_type,
                    "roll_year": p.roll_year,
                    "roll_land_value": p.roll_land_value,
                    "roll_imp_value": p.roll_imp_value,
                    "homeowners_exemp": p.homeowners_exemp,
                    "real_estate_exemp": p.real_estate_exemp,
                    "pers_prop_exemp": p.pers_prop_exemp,
                    "fixture_exemp": p.fixture_exemp,
                    "tax_rate_area": p.tax_rate_area,
                    "tax_rate_city": p.tax_rate_city,
                    "attributes": p.attributes
                }
                for p in result.parcels
            ],
            "error": result.error
        }

    def _deserialize_parcels(self, data: dict) -> ParcelResult:
        """Convert dict to ParcelResult."""
        parcels = [
            Parcel(
                ain=p["ain"],
                apn=p["apn"],
                situs_address=p["situs_address"],
                situs_street=p["situs_street"],
                situs_city=p["situs_city"],
                situs_zip=p["situs_zip"],
                use_description=p["use_description"],
                use_type=p["use_type"],
                roll_year=p.get("roll_year"),
                roll_land_value=p.get("roll_land_value"),
                roll_imp_value=p.get("roll_imp_value"),
                homeowners_exemp=p.get("homeowners_exemp"),
                real_estate_exemp=p.get("real_estate_exemp"),
                pers_prop_exemp=p.get("pers_prop_exemp"),
                fixture_exemp=p.get("fixture_exemp"),
                tax_rate_area=p.get("tax_rate_area", ""),
                tax_rate_city=p.get("tax_rate_city", ""),
                attributes=p["attributes"]
            )
            for p in data.get("parcels", [])
        ]
        return ParcelResult(
            lat=data["lat"],
            lon=data["lon"],
            parcels=parcels,
            error=data.get("error")
        )

    # --- Tax Cache ---

    def get_tax(self, ain: str) -> Optional[dict]:
        """Get cached tax result."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT result_json, fetched_at FROM tax_cache WHERE ain = ?",
                (ain,)
            ).fetchone()

            if row and self._is_fresh(row[1]):
                return json.loads(row[0])
        return None

    def set_tax(self, ain: str, result: dict):
        """Cache tax result."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO tax_cache (ain, result_json, fetched_at)
                VALUES (?, ?, ?)
            """, (ain, json.dumps(result), datetime.now().isoformat()))
            conn.commit()

    # --- AIN Cache ---

    def get_parcel_by_ain(self, ain: str) -> Optional[Parcel]:
        """Get cached parcel result by AIN."""
        # Normalize AIN
        ain_clean = ain.replace("-", "").strip()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT result_json, fetched_at FROM ain_cache WHERE ain = ?",
                (ain_clean,)
            ).fetchone()

            if row and self._is_fresh(row[1]):
                data = json.loads(row[0])
                if data is None:
                    return None  # Cached "not found"
                return self._deserialize_parcel(data)
        return None  # Not in cache

    def set_parcel_by_ain(self, ain: str, parcel: Optional[Parcel]):
        """Cache parcel result by AIN. Pass None to cache a 'not found' result."""
        ain_clean = ain.replace("-", "").strip()
        data = self._serialize_parcel(parcel) if parcel else None
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO ain_cache (ain, result_json, fetched_at)
                VALUES (?, ?, ?)
            """, (ain_clean, json.dumps(data), datetime.now().isoformat()))
            conn.commit()

    def is_ain_cached(self, ain: str) -> bool:
        """Check if AIN is in cache (even if result is 'not found')."""
        ain_clean = ain.replace("-", "").strip()
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT fetched_at FROM ain_cache WHERE ain = ?",
                (ain_clean,)
            ).fetchone()
            return row is not None and self._is_fresh(row[0])

    def _serialize_parcel(self, p: Parcel) -> dict:
        """Convert single Parcel to serializable dict."""
        return {
            "ain": p.ain,
            "apn": p.apn,
            "situs_address": p.situs_address,
            "situs_street": p.situs_street,
            "situs_city": p.situs_city,
            "situs_zip": p.situs_zip,
            "use_description": p.use_description,
            "use_type": p.use_type,
            "roll_year": p.roll_year,
            "roll_land_value": p.roll_land_value,
            "roll_imp_value": p.roll_imp_value,
            "homeowners_exemp": p.homeowners_exemp,
            "real_estate_exemp": p.real_estate_exemp,
            "pers_prop_exemp": p.pers_prop_exemp,
            "fixture_exemp": p.fixture_exemp,
            "tax_rate_area": p.tax_rate_area,
            "tax_rate_city": p.tax_rate_city,
            "attributes": p.attributes
        }

    def _deserialize_parcel(self, p: dict) -> Parcel:
        """Convert dict to single Parcel."""
        return Parcel(
            ain=p["ain"],
            apn=p["apn"],
            situs_address=p["situs_address"],
            situs_street=p["situs_street"],
            situs_city=p["situs_city"],
            situs_zip=p["situs_zip"],
            use_description=p["use_description"],
            use_type=p["use_type"],
            roll_year=p.get("roll_year"),
            roll_land_value=p.get("roll_land_value"),
            roll_imp_value=p.get("roll_imp_value"),
            homeowners_exemp=p.get("homeowners_exemp"),
            real_estate_exemp=p.get("real_estate_exemp"),
            pers_prop_exemp=p.get("pers_prop_exemp"),
            fixture_exemp=p.get("fixture_exemp"),
            tax_rate_area=p.get("tax_rate_area", ""),
            tax_rate_city=p.get("tax_rate_city", ""),
            attributes=p.get("attributes", {})
        )

    # --- Utilities ---

    def clear(self, table: str = None):
        """Clear cache, optionally for a specific table."""
        with sqlite3.connect(self.db_path) as conn:
            if table:
                conn.execute(f"DELETE FROM {table}")
            else:
                conn.execute("DELETE FROM geocode_cache")
                conn.execute("DELETE FROM parcel_cache")
                conn.execute("DELETE FROM tax_cache")
                conn.execute("DELETE FROM ain_cache")
            conn.commit()

    def stats(self) -> dict:
        """Get cache statistics."""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}
            for table in ["geocode_cache", "parcel_cache", "tax_cache", "ain_cache"]:
                try:
                    count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    stats[table] = count
                except sqlite3.OperationalError:
                    stats[table] = 0  # Table doesn't exist yet
            return stats


if __name__ == "__main__":
    # Test cache
    cache = ParcelCache()
    print(f"Cache path: {cache.db_path}")
    print(f"Stats: {cache.stats()}")
