"""
Consumer Affairs connector -- Dept. of Consumer Affairs daily retail/
wholesale reference prices, Price Monitoring Cell (PMC).

CORRECTED from the original JSON-API assumption: verified as of Sept 2026,
no live JSON API exists for this dataset. The official data.gov.in catalog
entries for this dataset only go up to April 2015 (stale archive, not a
live feed). The genuinely current source is CEDA's (Ashoka University) daily
food prices portal (dca.ceda.ashoka.edu.in), which re-hosts DoCA/PMC data
with daily updates -- but only via a manual CSV download form (one
commodity/centre per request, 1-year max lookback, non-commercial license,
citation required). No programmatic API exists there either.

So this connector follows the same honest pattern as CPI/WPI: point
CA_CSV_PATH at a CSV you download by hand from CEDA (or the DoCA PMC portal
directly) and re-run periodically. This is not a bug to be fixed with a key
-- it's the actual shape of the data source.
"""
import os
import csv
from .base import BaseConnector


class ConsumerAffairsConnector(BaseConnector):
    source_name = "Consumer Affairs Price Monitoring"
    source_type = "government"
    quality_grade_default = "A"

    def fetch(self, **kwargs):
        path = os.environ.get("CA_CSV_PATH")
        if not path or not os.path.exists(path):
            return None
        with open(path) as f:
            return list(csv.DictReader(f))

    def parse(self, raw):
        rows = []
        for rec in raw or []:
            rows.append({
                "date": rec.get("date"),
                "commodity": rec.get("commodity"),
                "centre": rec.get("centre") or rec.get("state"),
                "unit": rec.get("unit", "kg"),
                "retail_price": rec.get("retail_price"),
                "wholesale_price": rec.get("wholesale_price"),
                "provisional": rec.get("provisional", "False") in ("True", "1", "true"),
            })
        return rows

    def normalize(self, parsed_rows):
        out = []
        for r in parsed_rows:
            try:
                price = float(r["retail_price"])
            except (TypeError, ValueError):
                continue
            r["standard_quantity"] = 1
            r["standard_unit"] = "kg"
            r["normalized_unit_price"] = round(price, 2)
            r["provisional_flag"] = 1 if r.get("provisional") else 0
            out.append(r)
        return out

    def save(self, rows):
        saved = 0
        for r in rows:
            product_id = self.resolve_product(r.get("commodity"))
            location_id = self.resolve_location(r.get("centre"))
            if not product_id or not location_id or not r.get("date"):
                continue
            self.insert_observation(
                product_id=product_id, location_id=location_id, channel="official",
                observation_date=r["date"],
                original_quantity=1, original_unit=r.get("unit", "kg"),
                original_price=r["normalized_unit_price"],
                standard_quantity=1, standard_unit="kg",
                normalized_unit_price=r["normalized_unit_price"],
                provisional_flag=r.get("provisional_flag", 0),
                data_quality_score=self.quality_grade_default,
            )
            saved += 1
        self.conn.commit()
        return saved
