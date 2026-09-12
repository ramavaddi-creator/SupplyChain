"""
Global commodity benchmark connectors -- World Bank Pink Sheet and FAO Food
Price Index. Both genuinely free, no login, no API key required (verified
directly this session via real fetches). These feed external_drivers as
international-scope series, for comparing India's domestic price moves
against the global backdrop (frozen PRD's edible-oil pressure model:
Indian oilseed crop + global price + imports + FX + duty + freight).

Both landing pages were confirmed to rotate their actual file URL (the
World Bank one changed hash *during this same research session*), so
both connectors scrape the current link off a stable landing page rather
than hardcoding a specific file URL, same defensive pattern used for WPI.
"""
import re
import io
import csv
import requests
from .base import BaseConnector


class WorldBankCommodityConnector(BaseConnector):
    source_name = "World Bank Pink Sheet"
    source_type = "international"
    quality_grade_default = "B"

    LANDING_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
    TARGET_COMMODITIES = {"palm oil": "global_palm_oil_price", "soybean oil": "global_soybean_oil_price"}

    def fetch(self, **kwargs):
        try:
            page = requests.get(self.LANDING_PAGE, timeout=30)
            page.raise_for_status()
        except requests.exceptions.Timeout:
            print("World Bank: landing page request timed out after 30s")
            return None
        except requests.exceptions.ConnectionError as e:
            print(f"World Bank: connection failed reaching landing page -- {e}")
            return None
        except requests.exceptions.HTTPError as e:
            print(f"World Bank: landing page returned HTTP error -- {e}")
            return None
        except Exception as e:
            print(f"World Bank: unexpected error fetching landing page -- {type(e).__name__}: {e}")
            return None

        match = re.search(r'(https://thedocs\.worldbank\.org/[^\s")\]]*CMO-Historical-Data-Monthly\.xlsx)', page.text)
        if not match:
            snippet = page.text[:300].replace("\n", " ")
            print(f"World Bank: landing page fetched OK, but couldn't find the monthly xlsx link. Page starts with: {snippet}")
            return None
        xlsx_url = match.group(1)
        print(f"World Bank: found xlsx link -- {xlsx_url}")

        try:
            resp = requests.get(xlsx_url, timeout=30)
            resp.raise_for_status()
        except requests.exceptions.Timeout:
            print("World Bank: .xlsx download timed out after 30s")
            return None
        except requests.exceptions.ConnectionError as e:
            print(f"World Bank: connection failed downloading .xlsx -- {e}")
            return None
        except requests.exceptions.HTTPError as e:
            print(f"World Bank: .xlsx download returned HTTP error -- {e}")
            return None
        except Exception as e:
            print(f"World Bank: unexpected error downloading xlsx -- {type(e).__name__}: {e}")
            return None

        try:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(resp.content), read_only=True, data_only=True)
            print(f"World Bank: xlsx downloaded and parsed successfully ({len(resp.content)} bytes)")
        except Exception as e:
            print(f"World Bank: downloaded file but openpyxl couldn't parse it -- {type(e).__name__}: {e}")
            return None
        return wb

    def parse(self, raw):
        """Real structure confirmed via direct inspection this session:
        commodity names are COLUMN headers in one row (e.g. row 5), and
        period labels like '1960M01' are in column A of each data row
        below that -- the reverse of what was originally assumed here.
        Finds the target commodities' column positions first, then reads
        down column A for the actual monthly values."""
        if raw is None:
            return []
        rows = []
        try:
            if "Monthly Prices" not in raw.sheetnames:
                print(f"World Bank: no Monthly Prices sheet found -- actual sheets are: {raw.sheetnames}")
                return []
            ws = raw["Monthly Prices"]
            commodity_col = {}
            for row in ws.iter_rows(min_row=1, max_row=10, values_only=True):
                if not row:
                    continue
                for col_idx, val in enumerate(row):
                    if isinstance(val, str):
                        val_lower = val.strip().lower()
                        for target_label, driver_type in self.TARGET_COMMODITIES.items():
                            if target_label in val_lower:
                                commodity_col[driver_type] = col_idx
                if commodity_col:
                    break
            if not commodity_col:
                print("World Bank: couldn't find Palm oil / Soybean oil column headers in the first 10 rows")
                return []
            print(f"World Bank: found target commodity columns -- {commodity_col}")

            for row in ws.iter_rows(values_only=True):
                if not row or not isinstance(row[0], str) or not re.match(r"^\d{4}M\d{2}$", row[0].strip()):
                    continue
                period = row[0].strip()
                for driver_type, col_idx in commodity_col.items():
                    if col_idx < len(row) and isinstance(row[col_idx], (int, float)):
                        rows.append({"driver_type": driver_type, "period_label": period, "value": row[col_idx]})
        except Exception as e:
            print(f"World Bank: error while parsing rows -- {type(e).__name__}: {e}")
            return []
        return rows

    def normalize(self, parsed_rows):
        out = []
        for r in parsed_rows:
            try:
                year, month = int(r["period_label"][:4]), int(r["period_label"][5:7])
                value = float(r["value"])
            except (ValueError, TypeError, IndexError):
                continue
            out.append({
                "driver_type": r["driver_type"], "date": f"{year:04d}-{month:02d}-01", "value": value,
            })
        return out

    def validate(self, rows):
        good, rejected = [], 0
        for r in rows:
            if r.get("driver_type") and r.get("date") and isinstance(r.get("value"), (int, float)):
                good.append(r)
            else:
                rejected += 1
        return good, rejected

    def save(self, rows):
        cur = self.conn.cursor()
        source_id = self.get_source_id()
        saved = 0
        for r in rows:
            cur.execute(
                """INSERT INTO external_drivers (driver_type, scope, observation_date, value, unit, source_id, data_quality_score)
                   VALUES (?, 'international', ?, ?, 'USD/mt', ?, ?)
                   ON CONFLICT (driver_type, scope, observation_date) DO NOTHING""",
                (r["driver_type"], r["date"], r["value"], source_id, self.quality_grade_default),
            )
            saved += cur.rowcount
        self.conn.commit()
        return saved


class FAOFoodPriceIndexConnector(BaseConnector):
    source_name = "FAO Food Price Index"
    source_type = "international"
    quality_grade_default = "B"

    CSV_URL = "https://www.fao.org/media/docs/worldfoodsituationlibraries/default-document-library/food_price_indices_data.csv"

    def fetch(self, **kwargs):
        try:
            resp = requests.get(self.CSV_URL, params={"download": "true"}, timeout=30)
            resp.raise_for_status()
            print(f"FAO: CSV downloaded successfully ({len(resp.content)} bytes)")
            return resp.text
        except requests.exceptions.Timeout:
            print("FAO: request timed out after 30s")
            return None
        except requests.exceptions.ConnectionError as e:
            print(f"FAO: connection failed -- {e}")
            return None
        except requests.exceptions.HTTPError as e:
            print(f"FAO: HTTP error -- {e}")
            return None
        except Exception as e:
            print(f"FAO: unexpected error -- {type(e).__name__}: {e}")
            return None

    def parse(self, raw):
        """The real file has a title/units preamble before the actual header
        row (confirmed via direct fetch this session) -- search for the row
        starting with 'Date' rather than assume a fixed line number."""
        if raw is None:
            return []
        try:
            lines = raw.splitlines()
            header_idx = None
            for i, line in enumerate(lines):
                if line.strip().lower().startswith("date,"):
                    header_idx = i
                    break
            if header_idx is None:
                print("FAO: couldn't find the 'Date,...' header row in the CSV")
                return []
            reader = csv.DictReader(lines[header_idx:])
            rows = []
            for row in reader:
                if row.get("Date") and row.get("Food Price Index"):
                    rows.append(row)
            print(f"FAO: parsed {len(rows)} monthly rows")
            return rows
        except Exception as e:
            print(f"FAO: error while parsing CSV -- {type(e).__name__}: {e}")
            return []

    def normalize(self, parsed_rows):
        out = []
        for r in parsed_rows:
            try:
                year, month = r["Date"].split("-")
                year, month = int(year), int(month)
                value = float(r["Food Price Index"])
            except (ValueError, KeyError, AttributeError):
                continue
            out.append({"date": f"{year:04d}-{month:02d}-01", "value": value})
        return out

    def validate(self, rows):
        good, rejected = [], 0
        for r in rows:
            if r.get("date") and isinstance(r.get("value"), (int, float)):
                good.append(r)
            else:
                rejected += 1
        return good, rejected

    def save(self, rows):
        cur = self.conn.cursor()
        source_id = self.get_source_id()
        saved = 0
        for r in rows:
            cur.execute(
                """INSERT INTO external_drivers (driver_type, scope, observation_date, value, unit, source_id, data_quality_score)
                   VALUES ('fao_food_price_index', 'international', ?, ?, 'index (2014-2016=100)', ?, ?)
                   ON CONFLICT (driver_type, scope, observation_date) DO NOTHING""",
                (r["date"], r["value"], source_id, self.quality_grade_default),
            )
            saved += cur.rowcount
        self.conn.commit()
        return saved
