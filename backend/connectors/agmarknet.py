"""
AGMARKNET connector -- mandi / agricultural wholesale market prices.

Real endpoint: data.gov.in's AGMARKNET "Variety-wise Daily Market Prices" API
(resource id 9ef84268-d588-465a-a308-a864a43d0070).

Requires an API key (free, from https://data.gov.in -> My Account -> API Key)
set as the AGMARKNET_API_KEY environment variable. This connector will not
have network access inside this build sandbox -- it is written to run for
real once you run the app on your own machine with internet access.
"""
import os
import requests
from datetime import datetime
from .base import BaseConnector

API_URL = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"


class AGMARKNETConnector(BaseConnector):
    source_name = "AGMARKNET"
    source_type = "government"
    quality_grade_default = "A"

    def fetch(self, state=None, commodity=None, limit=500):
        api_key = os.environ.get("AGMARKNET_API_KEY")
        if not api_key:
            return None
        params = {
            "api-key": api_key,
            "format": "json",
            "limit": limit,
        }
        if state:
            params["filters[state]"] = state
        if commodity:
            params["filters[commodity]"] = commodity
        try:
            resp = requests.get(API_URL, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json().get("records", [])
        except Exception:
            return None

    def parse(self, raw):
        rows = []
        for rec in raw:
            rows.append({
                "date": rec.get("arrival_date"),
                "state": rec.get("state"),
                "district": rec.get("district"),
                "market": rec.get("market"),
                "commodity": rec.get("commodity"),
                "variety": rec.get("variety"),
                "grade": rec.get("grade"),
                "arrivals": rec.get("arrivals"),
                "min_price": rec.get("min_price"),
                "max_price": rec.get("max_price"),
                "modal_price": rec.get("modal_price"),
                "original_unit": "quintal",
            })
        return rows

    def normalize(self, parsed_rows):
        out = []
        for r in parsed_rows:
            try:
                modal = float(r["modal_price"])
            except (TypeError, ValueError):
                continue
            # AGMARKNET modal_price is Rs per quintal (100kg)
            r["standard_quantity"] = 1
            r["standard_unit"] = "kg"
            r["normalized_unit_price"] = round(modal / 100.0, 2)
            r["original_price"] = modal
            r["original_quantity"] = 1
            out.append(r)
        return out

    def save(self, rows):
        saved = 0
        for r in rows:
            product_id = self.resolve_product(r.get("commodity"))
            location_id = self.resolve_location(r.get("district") or r.get("market"))
            if not product_id or not location_id or not r.get("date"):
                continue  # unmatched commodity/city or missing date -- skipped, not guessed
            self.insert_observation(
                product_id=product_id, location_id=location_id, channel="mandi",
                observation_date=r["date"],
                original_quantity=r.get("original_quantity", 1), original_unit=r.get("original_unit", "quintal"),
                original_price=r.get("original_price"),
                standard_quantity=r.get("standard_quantity", 1), standard_unit=r.get("standard_unit", "kg"),
                normalized_unit_price=r["normalized_unit_price"],
                data_quality_score=self.quality_grade_default,
            )
            saved += 1
        self.conn.commit()
        return saved
