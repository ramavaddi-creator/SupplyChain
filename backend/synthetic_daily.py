"""
Appends ONE new day of synthetic data for every (product, city, channel)
series that's missing today's observation. This exists so the app keeps
"moving forward" day by day while you're still on seed data -- once real
API keys are set for the connectors, run_daily_ingestion() in scheduler.py
prefers real data and only falls back to this when a source has no data
(missing key, no network, etc).

Every row written this way is tagged with the same 'Manual / Seed Synthetic'
source and quality grade E as the original seed -- it is never confused
with real observations in the Sources page or anywhere else.
"""
import random
from datetime import date, timedelta
from database import get_conn
from commodities import CITIES, COMMODITIES, seasonal_multiplier

NOISE_SIGMA = 0.004
DRIFT = 0.00005
SHOCK_CHANCE_PER_DAY = 0.006


def _next_price(last_price, commodity, last_date, new_date, apply_seasonality=True):
    step = random.gauss(0, NOISE_SIGMA) + DRIFT
    if random.random() < SHOCK_CHANCE_PER_DAY:
        step += random.uniform(-0.06, 0.10)
    new_price = last_price * (1 + step)
    if apply_seasonality:
        old_factor = seasonal_multiplier(commodity, last_date.month)
        new_factor = seasonal_multiplier(commodity, new_date.month)
        if old_factor:
            new_price *= (new_factor / old_factor)
    return max(new_price, 0.5)


def append_one_day(target_date=None):
    """Idempotent: safe to call more than once for the same day (later calls
    are no-ops for series that already have that date)."""
    conn = get_conn()
    cur = conn.cursor()
    target_date = target_date or date.today()
    target_str = target_date.isoformat()

    cur.execute("SELECT id FROM data_sources WHERE source_name='Manual / Seed Synthetic'")
    source_row = cur.fetchone()
    if not source_row:
        conn.close()
        return {"error": "seed source not found -- run seed.py first"}
    source_id = source_row["id"]

    cur.execute("SELECT id, city FROM locations")
    city_by_id = {r["id"]: r["city"] for r in cur.fetchall()}

    rows_to_insert = []
    appended_series = 0
    skipped_existing = 0

    for row in COMMODITIES:
        (category, commodity, sub_category, standard_unit, essential, structural,
         regional, has_mandi, core, base_price, w_essential, w_actual, freq) = row

        cur.execute("SELECT id FROM product_master WHERE commodity=?", (commodity,))
        prow = cur.fetchone()
        if not prow:
            continue
        product_id = prow["id"]

        cur.execute(
            "SELECT DISTINCT location_id, channel FROM price_observations WHERE product_id=? AND (grade='FAQ' OR grade IS NULL)",
            (product_id,),
        )
        series_keys = cur.fetchall()

        for sk in series_keys:
            location_id, channel = sk["location_id"], sk["channel"]

            cur.execute(
                """SELECT observation_date, normalized_unit_price FROM price_observations
                   WHERE product_id=? AND location_id=? AND channel=? AND (grade='FAQ' OR grade IS NULL)
                   ORDER BY observation_date DESC LIMIT 1""",
                (product_id, location_id, channel),
            )
            last = cur.fetchone()
            if not last:
                continue
            last_date = date.fromisoformat(last["observation_date"])

            if last_date >= target_date:
                skipped_existing += 1
                continue

            # non-core commodities are weekly-cadence: only append every 7 days
            if not core and (target_date - last_date).days < 7:
                continue

            new_price = round(_next_price(last["normalized_unit_price"], commodity, last_date, target_date,
                                           apply_seasonality=(channel in ("mandi", "local_retail"))), 2)
            rows_to_insert.append((
                target_str, product_id, location_id, source_id, channel,
                1, standard_unit, new_price, 1, standard_unit, new_price, 0, "E",
            ))
            appended_series += 1

    if rows_to_insert:
        cur.executemany(
            """INSERT INTO price_observations
               (observation_date, product_id, location_id, source_id, channel,
                original_quantity, original_unit, original_price,
                standard_quantity, standard_unit, normalized_unit_price,
                provisional_flag, data_quality_score)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows_to_insert,
        )
        conn.commit()

    result = {"date": target_str, "series_appended": appended_series, "series_already_current": skipped_existing}
    conn.close()
    return result


if __name__ == "__main__":
    print(append_one_day())
