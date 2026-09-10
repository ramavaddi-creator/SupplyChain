"""
Seeds the database with realistic SYNTHETIC data spanning ~24 months so
YoY, seasonal pattern detection, and backtested projections all have real
history to work with. Deliberately builds in:
  - a seasonal multiplier layer (see commodities.SEASONAL_FACTORS) so
    recurring yearly patterns actually exist and can be honestly detected
  - lagged transmission down the price chain (mandi -> official -> retail -> supermarket)
  - asymmetric pass-through (rises faster/fuller than falls) for stickiness
  - occasional multi-day price shocks for Shock classification
  - city cost-factor differences (fresh produce vs packaged goods)
This file is clearly synthetic; swap in backend/connectors/*.py once you
run this with real internet access + API keys.
"""
import random
from datetime import date, timedelta
from database import get_conn, init_db
from commodities import (CITIES, COMMODITIES, CHANNEL_CHAIN, FMCG_CHAIN, QC_PREMIUM_RANGE, seasonal_multiplier,
                          CITY_POPULATION_MILLIONS, PER_CAPITA_MONTHLY_CONSUMPTION, TYPICAL_DAILY_ARRIVALS_TONNES)
import external_reality as ER

random.seed(42)

DAYS_CORE = 730          # 24 months daily for core commodities
WEEKS_NONCORE = 104      # 24 months weekly for non-core commodities
TODAY = date(2026, 7, 20)

# Which commodities get a genuine (modest) external-driver effect wired in,
# so the driver_correlation() engine has something real to detect rather
# than two coincidentally-seasonal but unrelated series.
RAINFALL_SENSITIVE = {"Tomato", "Onion", "Potato", "Green Chilli"}
IMPORT_SENSITIVE = {"Sunflower Oil", "Groundnut Oil", "Mustard Oil", "Rice Bran Oil"}
EXPORT_SENSITIVE = {"Onion": "onion", "Rice (regular)": "rice", "Basmati Rice": "rice"}

# Commodities where grade/variety genuinely moves price a lot in Indian
# markets (AGMARKNET itself reports a grade field) -- modeled as a discounted
# secondary "Non-FAQ" series at local_retail only, alongside the primary FAQ series.
GRADE_SENSITIVE = {
    "Onion": 0.82, "Turmeric": 0.78, "Red Chilli": 0.75,
    "Rice (regular)": 0.85, "Basmati Rice": 0.70,
}


def apply_rainfall_effect(prices, dates, lag_days=35, exposure=0.15):
    """Below-normal rainfall -> supply stress -> price up, with a lag for
    the crop-cycle delay. Deliberately modest (exposure ~0.15) -- weather
    is a contributor, not the only driver."""
    out = list(prices)
    for t in range(len(prices)):
        src_idx = t - lag_days
        if src_idx < 0:
            continue
        d = dates[src_idx]
        ratio = ER.rainfall_deviation_ratio(d.year, d.month)
        out[t] = out[t] * (ratio ** (-exposure))
    return out


def apply_driver_effect(prices, dates, driver_lookup_fn, lag_days, exposure):
    """Injects a scaled, lagged version of the DRIVER'S OWN day-over-day pct
    change directly into the price's day-over-day pct change -- the same
    mechanism used for internal mandi->retail propagation. This matters:
    a smooth multiplicative drift is easy to bury under daily noise when
    later measured via day-over-day correlation, whereas injecting at the
    pct-change level is what the detector actually looks for."""
    driver_vals = [driver_lookup_fn(d.isoformat()) for d in dates]
    out = [prices[0]]
    for t in range(1, len(prices)):
        own_pct = prices[t] / prices[t - 1] - 1
        src_idx = t - lag_days
        driver_pct = 0.0
        if src_idx >= 1 and driver_vals[src_idx] is not None and driver_vals[src_idx - 1] not in (None, 0):
            driver_pct = driver_vals[src_idx] / driver_vals[src_idx - 1] - 1
        out.append(max(out[-1] * (1 + own_pct + exposure * driver_pct), 0.5))
    return out


def trend_series(base_price, days, shock_chance=0.06, noise_sigma=0.004, drift=0.00005):
    """Underlying random-walk trend (pre-seasonal-adjustment)."""
    prices = [base_price]
    t = 0
    while t < days - 1:
        if random.random() < shock_chance / 10:
            direction = random.choice([1, 1, -1])
            magnitude = random.uniform(0.08, 0.24)
            duration = random.randint(4, 9)
            for d in range(duration):
                if len(prices) >= days:
                    break
                step = (magnitude / duration) * direction
                new_p = prices[-1] * (1 + step + random.gauss(0, noise_sigma) + drift)
                prices.append(max(new_p, 0.5))
                t += 1
        else:
            new_p = prices[-1] * (1 + random.gauss(0, noise_sigma) + drift)
            prices.append(max(new_p, 0.5))
            t += 1
    return prices[:days]


def apply_seasonality(prices, start_date, commodity):
    out = []
    for i, p in enumerate(prices):
        d = start_date + timedelta(days=i)
        out.append(p * seasonal_multiplier(commodity, d.month))
    return out


def generate_arrivals(mandi_prices, base_arrivals, city_scale, noise_sigma=0.025):
    """Mandi arrivals (tonnes/day), constructed with a genuine inverse
    relationship to price (when arrivals drop, price rises) so the
    price-sensitivity/elasticity metric has a real signal to detect --
    the same design principle used for the rainfall/import/export drivers."""
    base_p = mandi_prices[0]
    out = []
    for p in mandi_prices:
        rel = (p / base_p) ** -0.5 if base_p else 1.0
        val = base_arrivals * city_scale * rel * (1 + random.gauss(0, noise_sigma))
        out.append(max(val, 1))
    return out


def downstream_series(upstream_prices, lag, alpha_up, alpha_down, base_ratio, noise_sigma=0.003, speed_scale=0.22):
    """Downstream channel price, modeled as MEAN-REVERTING toward a moving
    anchor (lagged upstream price x ratio) rather than compounding an
    independent percentage change every day. The earlier version applied
    alpha_up/alpha_down as a daily compounding multiplier on upstream's pct
    change -- over a 730-day series, even a small asymmetry between
    alpha_up and alpha_down ratchets into an unbounded gap (this is exactly
    what produced the unrealistic groundnut oil channel spread). Here,
    alpha_up/alpha_down instead control how FAST the gap to the anchor
    closes each day (asymmetric speed = genuine stickiness), which stays
    bounded no matter how long the series runs."""
    n = len(upstream_prices)
    out = [upstream_prices[0] * base_ratio]
    for t in range(1, n):
        src_idx = max(0, t - lag)
        anchor = upstream_prices[src_idx] * base_ratio
        gap_pct = anchor / out[-1] - 1
        speed = alpha_up if gap_pct >= 0 else alpha_down
        step = speed * speed_scale * gap_pct + random.gauss(0, noise_sigma)
        out.append(max(out[-1] * (1 + step), 0.5))
    return out


def seed():
    init_db()
    conn = get_conn()
    cur = conn.cursor()

    city_ids = {}
    for city, state, tier, fresh_f, pkg_f in CITIES:
        cur.execute("INSERT OR IGNORE INTO locations (city, state, tier) VALUES (?,?,?)", (city, state, tier))
        cur.execute("SELECT id FROM locations WHERE city=? AND state=?", (city, state))
        city_ids[city] = cur.fetchone()["id"]

    sources = [
        ("AGMARKNET", "government", "AGMARKNETConnector", "daily", "A"),
        ("Consumer Affairs Price Monitoring", "government", "ConsumerAffairsConnector", "daily", "A"),
        ("CPI (MOSPI)", "government", "CPIConnector", "monthly", "A"),
        ("WPI (OEA)", "government", "WPIConnector", "monthly", "A"),
        ("Manual / Seed Synthetic", "manual", "SeedGenerator", "n/a", "E"),
    ]
    source_ids = {}
    for name, stype, connector, freq, grade in sources:
        cur.execute(
            "INSERT OR IGNORE INTO data_sources (source_name, source_type, connector_name, update_frequency, quality_grade_default, active_status) VALUES (?,?,?,?,?,1)",
            (name, stype, connector, freq, grade),
        )
        cur.execute("SELECT id FROM data_sources WHERE source_name=?", (name,))
        source_ids[name] = cur.fetchone()["id"]
    seed_source_id = source_ids["Manual / Seed Synthetic"]

    product_ids = {}
    for row in COMMODITIES:
        (category, commodity, sub_category, standard_unit, essential, structural,
         regional, has_mandi, core, base_price, w_essential, w_actual, freq) = row
        cur.execute(
            """INSERT INTO product_master
               (category, commodity, sub_category, brand, variant, pack_quantity, pack_unit,
                standard_unit, branded_flag, essential_flag, regional_relevance, structural_flag, active_status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1)""",
            (category, commodity, sub_category, None, None, 1, standard_unit,
             standard_unit, 0, essential, regional, structural),
        )
        pid = cur.lastrowid
        product_ids[commodity] = pid
        if w_essential > 0:
            cur.execute(
                "INSERT INTO basket_definitions (basket_name, basket_type, product_id, weight, purchase_frequency, active_status) VALUES ('essential', ?, ?, ?, ?, 1)",
                (category, pid, w_essential, freq),
            )
        if w_actual > 0:
            cur.execute(
                "INSERT INTO basket_definitions (basket_name, basket_type, product_id, weight, purchase_frequency, active_status) VALUES ('actual', ?, ?, ?, ?, 1)",
                (category, pid, w_actual, freq),
            )
    conn.commit()

    rows_to_insert = []
    for row in COMMODITIES:
        (category, commodity, sub_category, standard_unit, essential, structural,
         regional, has_mandi, core, base_price, w_essential, w_actual, freq) = row
        pid = product_ids[commodity]
        is_fresh = category == "Vegetables"

        cities_for_product = CITIES if core else CITIES[:1]
        if not core and regional == "telangana":
            cities_for_product = [c for c in CITIES if c[0] in ("Hyderabad", "Siddipet", "Nizamabad")]

        n_days = DAYS_CORE if core else WEEKS_NONCORE * 7
        start_date = TODAY - timedelta(days=n_days - 1)

        for city, state, tier, fresh_f, pkg_f in cities_for_product:
            city_factor = fresh_f if is_fresh else pkg_f
            city_base = base_price * city_factor * random.uniform(0.97, 1.03)

            if has_mandi:
                mandi_trend = trend_series(city_base, n_days, shock_chance=0.055)
                mandi_prices = apply_seasonality(mandi_trend, start_date, commodity)
                date_list = [start_date + timedelta(days=i) for i in range(n_days)]
                if commodity in RAINFALL_SENSITIVE:
                    mandi_prices = apply_rainfall_effect(mandi_prices, date_list)
                if commodity in EXPORT_SENSITIVE:
                    export_key = EXPORT_SENSITIVE[commodity]
                    mandi_prices = apply_driver_effect(
                        mandi_prices, date_list,
                        lambda iso, k=export_key: ER.global_export_price(k, iso),
                        lag_days=20, exposure=0.65,
                    )
                arrivals_series = None
                if commodity in TYPICAL_DAILY_ARRIVALS_TONNES:
                    city_scale = max(0.15, CITY_POPULATION_MILLIONS.get(city, 5) / 10.0)
                    arrivals_series = generate_arrivals(mandi_prices, TYPICAL_DAILY_ARRIVALS_TONNES[commodity], city_scale)
                chain_series = {"mandi": mandi_prices}
                for chan, label, lag, a_up, a_down, ratio in CHANNEL_CHAIN[1:]:
                    chain_series[chan] = downstream_series(mandi_prices, lag, a_up, a_down, ratio)
                supermarket_series = chain_series["supermarket"]
                chain_series["quick_commerce"] = [p * (1 + random.uniform(*QC_PREMIUM_RANGE)) for p in supermarket_series]
                channels_here = ["mandi", "official", "local_retail", "supermarket", "quick_commerce"]
            else:
                arrivals_series = None
                base_trend = trend_series(city_base, n_days, shock_chance=0.04)
                base_series = apply_seasonality(base_trend, start_date, commodity)
                date_list = [start_date + timedelta(days=i) for i in range(n_days)]
                if commodity in IMPORT_SENSITIVE:
                    base_series = apply_driver_effect(
                        base_series, date_list, ER.import_cost_index,
                        lag_days=25, exposure=0.45,
                    )
                chain_series = {"local_retail": base_series}
                for chan, label, lag, a_up, a_down, ratio in FMCG_CHAIN[1:]:
                    chain_series[chan] = downstream_series(base_series, lag, a_up, a_down, ratio)
                supermarket_series = chain_series["supermarket"]
                chain_series["quick_commerce"] = [p * (1 + random.uniform(*QC_PREMIUM_RANGE)) for p in supermarket_series]
                channels_here = ["local_retail", "supermarket", "quick_commerce"]

            full_len = len(next(iter(chain_series.values())))
            date_indices = range(full_len) if core else range(0, full_len, 7)

            for chan in channels_here:
                series = chain_series[chan]
                # FIXED: this previously assigned "A" for mandi/official
                # channels and "B"/"C" elsewhere -- meaning 100% synthetic
                # data was tagged with the SAME grade as real government
                # data, making it indistinguishable by data_quality_score
                # alone. Every row from this generator is synthetic; it must
                # always be graded "E", matching the source's own declared
                # quality_grade_default in data_sources. Downstream pattern
                # detection (intelligence.py) filters on this column
                # specifically to exclude synthetic data from anything
                # presented as a real finding -- the old mislabeling made
                # that filter silently ineffective.
                grade_quality = "E"
                for idx in date_indices:
                    if idx >= len(series):
                        continue
                    obs_date = start_date + timedelta(days=idx)
                    price = round(series[idx], 2)
                    arrivals_val = round(arrivals_series[idx], 1) if (chan == "mandi" and arrivals_series and idx < len(arrivals_series)) else None
                    rows_to_insert.append((
                        obs_date.isoformat(), pid, city_ids[city], seed_source_id, chan, "FAQ",
                        1, standard_unit, price, 1, standard_unit, price, 0, grade_quality, arrivals_val,
                    ))

                # Secondary Non-FAQ grade series (local_retail only) for
                # grade-sensitive commodities -- same dates, discounted price,
                # slightly higher volatility (lower grades are less uniformly priced).
                if commodity in GRADE_SENSITIVE and chan == "local_retail":
                    discount = GRADE_SENSITIVE[commodity]
                    for idx in date_indices:
                        if idx >= len(series):
                            continue
                        obs_date = start_date + timedelta(days=idx)
                        non_faq_price = round(series[idx] * discount * random.uniform(0.96, 1.04), 2)
                        rows_to_insert.append((
                            obs_date.isoformat(), pid, city_ids[city], seed_source_id, chan, "Non-FAQ",
                            1, standard_unit, non_faq_price, 1, standard_unit, non_faq_price, 0, grade_quality, None,
                        ))

    cur.executemany(
        """INSERT INTO price_observations
           (observation_date, product_id, location_id, source_id, channel, grade,
            original_quantity, original_unit, original_price,
            standard_quantity, standard_unit, normalized_unit_price,
            provisional_flag, data_quality_score, arrivals)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows_to_insert,
    )
    conn.commit()
    print(f"Inserted {len(rows_to_insert)} price observations spanning {DAYS_CORE} days (~{DAYS_CORE/365:.1f} years) for core commodities")

    months = []
    y, m = TODAY.year, TODAY.month
    for i in range(25):
        months.append((y, m))
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    months.reverse()

    headline_cpi, food_cpi, wpi_food = 5.0, 6.5, 3.2
    for idx, (yy, mm) in enumerate(months):
        headline_cpi += random.uniform(-0.3, 0.35)
        food_cpi += random.uniform(-0.5, 0.6) + (0.4 if mm in (7, 8, 9) else -0.1 if mm in (1, 2) else 0)
        wpi_food += random.uniform(-0.4, 0.5)
        mom_h = round(random.uniform(-0.3, 0.3), 2)
        mom_f = round(random.uniform(-0.5, 0.5), 2)
        mom_w = round(random.uniform(-0.4, 0.4), 2)
        cur.execute(
            "INSERT INTO inflation_benchmarks (benchmark_type, commodity_group, state, month, year, value, mom_change, yoy_change, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("CPI", "Headline", None, mm, yy, round(headline_cpi, 2), mom_h, round(headline_cpi, 2), "final"),
        )
        cur.execute(
            "INSERT INTO inflation_benchmarks (benchmark_type, commodity_group, state, month, year, value, mom_change, yoy_change, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("CPI_FOOD", "Food", None, mm, yy, round(food_cpi, 2), mom_f, round(food_cpi, 2), "final"),
        )
        cur.execute(
            "INSERT INTO inflation_benchmarks (benchmark_type, commodity_group, state, month, year, value, mom_change, yoy_change, status) VALUES (?,?,?,?,?,?,?,?,?)",
            ("WPI", "Food", None, mm, yy, round(wpi_food, 2), mom_w, round(wpi_food, 2), "provisional" if idx == len(months) - 1 else "final"),
        )
    conn.commit()
    print("Inserted CPI/WPI benchmarks (25 months)")
    conn.close()


if __name__ == "__main__":
    seed()
