"""
Base connector interface. Every real data source (AGMARKNET, Consumer Affairs,
CPI, WPI, retail CSV) implements this same shape so one broken source never
stops the app, per the frozen PRD's connector architecture requirement.
"""
from abc import ABC, abstractmethod
from datetime import datetime


class BaseConnector(ABC):
    source_name = "base"
    source_type = "unknown"
    quality_grade_default = "D"

    def __init__(self, db_conn):
        self.conn = db_conn
        self._product_cache = None
        self._location_cache = None

    def _load_caches(self):
        if self._product_cache is None:
            cur = self.conn.cursor()
            cur.execute("SELECT id, commodity FROM product_master WHERE active_status=1")
            self._product_cache = {r["commodity"].strip().lower(): r["id"] for r in cur.fetchall()}
        if self._location_cache is None:
            cur = self.conn.cursor()
            cur.execute("SELECT id, city, state FROM locations")
            self._location_cache = {}
            for r in cur.fetchall():
                self._location_cache[r["city"].strip().lower()] = r["id"]

    def resolve_product(self, commodity_name):
        """Best-effort exact/case-insensitive match against product_master.
        Real AGMARKNET/Consumer Affairs naming will sometimes differ from
        our product_master commodity names (e.g. 'Onion' vs 'Onion Green') --
        this is a V1 match; refine the mapping as real data reveals gaps."""
        if not commodity_name:
            return None
        self._load_caches()
        return self._product_cache.get(commodity_name.strip().lower())

    def resolve_location(self, city_or_district_name):
        if not city_or_district_name:
            return None
        self._load_caches()
        return self._location_cache.get(city_or_district_name.strip().lower())

    def get_source_id(self):
        cur = self.conn.cursor()
        cur.execute("SELECT id FROM data_sources WHERE source_name=?", (self.source_name,))
        row = cur.fetchone()
        return row["id"] if row else None

    def insert_observation(self, product_id, location_id, channel, observation_date,
                            original_quantity, original_unit, original_price,
                            standard_quantity, standard_unit, normalized_unit_price,
                            provisional_flag=0, data_quality_score=None):
        """Shared insert into price_observations -- this is what makes a
        connector's save() actually persist data, not just count rows."""
        cur = self.conn.cursor()
        cur.execute(
            """INSERT INTO price_observations
               (observation_date, product_id, location_id, source_id, channel,
                original_quantity, original_unit, original_price,
                standard_quantity, standard_unit, normalized_unit_price,
                provisional_flag, data_quality_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (observation_date, product_id, location_id, self.get_source_id(), channel,
             original_quantity, original_unit, original_price,
             standard_quantity, standard_unit, normalized_unit_price,
             provisional_flag, data_quality_score or self.quality_grade_default),
        )

    @abstractmethod
    def fetch(self, **kwargs):
        """Retrieve raw data from the external source. Must not raise on
        network failure -- catch and return None/[] so health_check can log it."""
        raise NotImplementedError

    @abstractmethod
    def parse(self, raw):
        """Turn raw source payload into a list of dicts with common fields."""
        raise NotImplementedError

    @abstractmethod
    def normalize(self, parsed_rows):
        """Convert original quantity/unit/price into standard_quantity/unit
        and normalized_unit_price. Must NEVER overwrite the original values."""
        raise NotImplementedError

    def validate(self, rows):
        """Drop rows missing essential fields; flag outliers. Returns
        (good_rows, rejected_count)."""
        good = []
        rejected = 0
        for r in rows:
            if r.get("normalized_unit_price") is None or r.get("normalized_unit_price") <= 0:
                rejected += 1
                continue
            good.append(r)
        return good, rejected

    @abstractmethod
    def save(self, rows):
        """Persist validated rows into price_observations (or the relevant table)."""
        raise NotImplementedError

    def health_check(self, records_ingested=0):
        """Record source status into data_sources / ingestion_logs."""
        cur = self.conn.cursor()
        now = datetime.utcnow().isoformat()
        cur.execute(
            "UPDATE data_sources SET last_run_at=?, last_status=?, records_ingested=records_ingested+? WHERE source_name=?",
            (now, self._last_status, records_ingested, self.source_name),
        )
        cur.execute(
            "INSERT INTO ingestion_logs (source_id, run_at, status, records_ingested, error) VALUES (?,?,?,?,?)",
            (self.get_source_id(), now, self._last_status, records_ingested,
             self._last_status if self._last_status.startswith("error") else None),
        )
        self.conn.commit()

    def run(self, **kwargs):
        """Standard fetch -> parse -> normalize -> validate -> save pipeline.
        Never raises: a broken connector must not take down the app."""
        self._last_status = "ok"
        try:
            raw = self.fetch(**kwargs)
            if not raw:
                self._last_status = "no_data (no API key / no network / no results)"
                self.health_check(0)
                return 0
            parsed = self.parse(raw)
            normalized = self.normalize(parsed)
            good, rejected = self.validate(normalized)
            count = self.save(good)
            self._last_status = f"ok ({count} rows saved, {rejected} rejected, {len(good)-count if len(good)>=count else 0} unmatched)"
            self.health_check(count)
            return count
        except Exception as e:
            self._last_status = f"error: {e}"
            self.health_check(0)
            return 0
