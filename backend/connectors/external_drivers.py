"""
External driver connectors -- Phase 2 of the frozen PRD (section 34),
now reasonable to bring forward given 24 months of price history exists
to correlate against. Deliberately limited to two well-defined, genuinely
public drivers rather than the full wishlist (currency, global commodities,
crop-production estimates) -- narrow and real beats broad and speculative.

Neither PPAC nor IMD expose a single stable JSON API; both publish
structured pages/CSVs that change format periodically. As with CPI/WPI,
the honest V1 approach is a manually-maintained CSV you point this at,
rather than pretending a scraper is more stable than it is.
"""
import os
import csv
from .base import BaseConnector


class PPACFuelConnector(BaseConnector):
    """Petroleum Planning & Analysis Cell daily retail diesel/petrol prices.
    Set PPAC_CSV_PATH to a CSV you maintain with columns: date,value (INR/litre, diesel)."""
    source_name = "PPAC Fuel Price"
    source_type = "government"
    quality_grade_default = "C"

    def fetch(self, **kwargs):
        path = os.environ.get("PPAC_CSV_PATH")
        if not path or not os.path.exists(path):
            return None
        with open(path) as f:
            return list(csv.DictReader(f))

    def parse(self, raw):
        return raw or []

    def normalize(self, parsed_rows):
        out = []
        for r in parsed_rows:
            try:
                out.append({"date": r["date"], "value": float(r["value"])})
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def validate(self, rows):
        good = [r for r in rows if r.get("value") is not None and r["value"] > 0]
        return good, len(rows) - len(good)

    def save(self, rows):
        cur = self.conn.cursor()
        saved = 0
        for r in rows:
            cur.execute(
                """INSERT OR IGNORE INTO external_drivers
                   (driver_type, scope, observation_date, value, unit, source_id, data_quality_score)
                   VALUES ('diesel_price','national',?,?, 'INR/litre', ?, ?)""",
                (r["date"], r["value"], self.get_source_id(), self.quality_grade_default),
            )
            saved += cur.rowcount
        self.conn.commit()
        return saved


class IMDRainfallConnector(BaseConnector):
    """India Meteorological Dept monthly rainfall (mm) by state/subdivision.
    Set IMD_CSV_PATH to a CSV with columns: date (YYYY-MM-01),scope,value."""
    source_name = "IMD Rainfall"
    source_type = "government"
    quality_grade_default = "C"

    def fetch(self, **kwargs):
        path = os.environ.get("IMD_CSV_PATH")
        if not path or not os.path.exists(path):
            return None
        with open(path) as f:
            return list(csv.DictReader(f))

    def parse(self, raw):
        return raw or []

    def normalize(self, parsed_rows):
        out = []
        for r in parsed_rows:
            try:
                out.append({"date": r["date"], "scope": r.get("scope", "Telangana"), "value": float(r["value"])})
            except (KeyError, ValueError, TypeError):
                continue
        return out

    def validate(self, rows):
        good = [r for r in rows if r.get("value") is not None and r["value"] >= 0]
        return good, len(rows) - len(good)

    def save(self, rows):
        cur = self.conn.cursor()
        saved = 0
        for r in rows:
            cur.execute(
                """INSERT OR IGNORE INTO external_drivers
                   (driver_type, scope, observation_date, value, unit, source_id, data_quality_score)
                   VALUES ('rainfall_mm', ?, ?, ?, 'mm', ?, ?)""",
                (r["scope"], r["date"], r["value"], self.get_source_id(), self.quality_grade_default),
            )
            saved += cur.rowcount
        self.conn.commit()
        return saved
