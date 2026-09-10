from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse, PlainTextResponse
from apscheduler.schedulers.background import BackgroundScheduler
import os
import atexit
import io

from database import get_conn
import metrics as M
import insights as I
import intelligence as AI
import export as EX
from scheduler import run_daily_ingestion

app = FastAPI(title="India Household Price Intelligence")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")

_scheduler = BackgroundScheduler()
_last_ingestion_summary = {"status": "not yet run this session"}


def _scheduled_job():
    global _last_ingestion_summary
    _last_ingestion_summary = run_daily_ingestion()


@app.on_event("startup")
def bootstrap_fresh_database():
    """Needed for a fresh deploy (e.g. Render, where the ~150MB pre-built
    hpi.db is deliberately excluded from git -- it exceeds GitHub's 100MB
    file limit, and it's synthetic data anyway, safe to regenerate). Checks
    whether data_sources is empty as the fresh-install signal, and only
    then runs the seed pipeline. Never runs on a restart of an already-
    seeded database -- seed.seed() is not safe to call twice, it would
    duplicate the whole price_observations table."""
    from database import init_db, get_conn
    init_db()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as n FROM data_sources")
    is_fresh = cur.fetchone()["n"] == 0
    conn.close()
    if is_fresh:
        print("Fresh database detected -- running one-time seed pipeline...")
        import seed as SEED
        import drivers_seed as DRIVERS
        import consumption_seed as CONSUMPTION
        SEED.seed()
        DRIVERS.seed_drivers()
        CONSUMPTION.seed_consumption_hces()
        print("Seed pipeline complete.")


@app.on_event("startup")
def start_scheduler():
    # Runs once daily at 06:00 local time. Also fires once shortly after
    # startup so a freshly-started app doesn't wait until 6am to catch up
    # on a missed day.
    _scheduler.add_job(_scheduled_job, "cron", hour=6, minute=0, id="daily_ingestion")
    _scheduler.add_job(_scheduled_job, "date", id="startup_catchup")
    _scheduler.start()
    atexit.register(lambda: _scheduler.shutdown(wait=False))


@app.get("/api/ingestion-status")
def api_ingestion_status():
    return _last_ingestion_summary


def loc_id(conn, city):
    cur = conn.cursor()
    cur.execute("SELECT id FROM locations WHERE city=?", (city,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(404, f"Unknown city: {city}")
    return row["id"]


@app.get("/api/cities")
def api_cities():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT city, state, tier FROM locations ORDER BY tier, city")
    return [dict(r) for r in cur.fetchall()]


@app.get("/api/dashboard")
def api_dashboard(city: str = Query("Hyderabad")):
    conn = get_conn()
    location_id = loc_id(conn, city)

    essential_mom = I.basket_movement(conn, location_id, "essential", 30)
    essential_wow = I.basket_movement(conn, location_id, "essential", 7)
    actual_mom = I.basket_movement(conn, location_id, "actual", 30)
    reality_gap = I.household_reality_gap(conn, location_id, "essential")
    struct_volatile = I.structural_vs_volatile(conn, location_id, "actual", 30)
    material = I.most_material_this_week(conn, location_id, "actual", top_n=5)
    unseen = I.unseen_layer(conn, location_id, top_n=2)
    cpi = I.latest_cpi(conn, "CPI")
    cpi_food = I.latest_cpi(conn, "CPI_FOOD")

    # Behavioural intelligence: pick a representative core commodity per metric
    cur = conn.cursor()
    cur.execute("SELECT id FROM product_master WHERE commodity='Toor Dal'")
    toor_id = cur.fetchone()["id"]
    pass_through = M.consumer_benefit_pass_through(conn, toor_id, location_id, "local_retail")
    lag = M.transmission_lag(conn, toor_id, location_id, "local_retail")
    stickiness = M.price_stickiness(conn, toor_id, location_id, "local_retail")
    spread = M.wholesale_retail_spread(conn, toor_id, location_id)
    qc_premium = M.quick_commerce_premium(conn, toor_id, location_id)

    conn.close()
    return {
        "city": city,
        "observed_essential_basket": essential_mom,
        "observed_essential_basket_wow": essential_wow,
        "observed_actual_basket": actual_mom,
        "household_reality_gap": reality_gap,
        "structural_vs_volatile": struct_volatile,
        "most_material_this_week": material,
        "unseen_layer": unseen,
        "latest_cpi": cpi,
        "latest_cpi_food": cpi_food,
        "behavioural_intelligence_sample": {
            "commodity": "Toor Dal",
            "consumer_benefit_pass_through": pass_through,
            "retail_transmission_lag": lag,
            "price_stickiness": stickiness,
            "wholesale_to_retail_spread": spread,
            "quick_commerce_premium_pct": qc_premium,
        },
    }


@app.get("/api/commodities")
def api_commodities():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id, category, commodity, structural_flag, regional_relevance FROM product_master WHERE active_status=1 ORDER BY category, commodity")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


@app.get("/api/commodity/{product_id}")
def api_commodity_drilldown(product_id: int, city: str = Query("Hyderabad")):
    conn = get_conn()
    location_id = loc_id(conn, city)
    cur = conn.cursor()
    cur.execute("SELECT * FROM product_master WHERE id=?", (product_id,))
    product = cur.fetchone()
    if not product:
        raise HTTPException(404, "Unknown product")
    product = dict(product)

    channels = ["mandi", "official", "local_retail", "supermarket", "quick_commerce"]
    history = {}
    latest = {}
    for chan in channels:
        cur.execute(
            """SELECT observation_date, normalized_unit_price FROM price_observations
               WHERE product_id=? AND location_id=? AND channel=? AND (grade='FAQ' OR grade IS NULL) ORDER BY observation_date ASC""",
            (product_id, location_id, chan),
        )
        rows = cur.fetchall()
        if rows:
            history[chan] = [{"date": r["observation_date"], "price": r["normalized_unit_price"]} for r in rows]
            latest[chan] = rows[-1]["normalized_unit_price"]

    wow_ = M.wow(conn, product_id, location_id, "local_retail")
    mom_ = M.mom(conn, product_id, location_id, "local_retail")
    yoy_ = M.yoy(conn, product_id, location_id, "local_retail")
    spread = M.wholesale_retail_spread(conn, product_id, location_id)
    mh_spread = M.mandi_to_household_spread(conn, product_id, location_id)
    qc_premium = M.quick_commerce_premium(conn, product_id, location_id)
    confidence = M.data_confidence(conn, product_id, location_id, "local_retail")
    lag = M.transmission_lag(conn, product_id, location_id, "local_retail")
    stickiness = M.price_stickiness(conn, product_id, location_id, "local_retail")
    pass_through = M.consumer_benefit_pass_through(conn, product_id, location_id, "local_retail")

    # Telangana lens comparison (only meaningful if product tracked there)
    telangana_cities = ["Hyderabad", "Siddipet", "Nizamabad"]
    telangana = {}
    for tc in telangana_cities:
        try:
            tloc = loc_id(conn, tc)
        except HTTPException:
            continue
        p = M.latest_price(conn, product_id, tloc, "local_retail")
        if p:
            telangana[tc] = p["normalized_unit_price"]

    driver_label = "Insufficient Evidence"
    if lag and wow_ is not None:
        upstream_wow = M.wow(conn, product_id, location_id, "mandi") if "mandi" in latest else None
        if upstream_wow is not None and abs(upstream_wow) > 3:
            driver_label = M.confidence_band_to_causal_label(min(1.0, lag["correlation"]))

    conn.close()
    return {
        "product": product,
        "city": city,
        "latest_prices_per_kg_equivalent": latest,
        "history": history,
        "wow_pct": wow_, "mom_pct": mom_, "yoy_pct": yoy_,
        "wow_plausibility": AI.movement_plausibility(wow_, "WoW"),
        "mom_plausibility": AI.movement_plausibility(mom_, "MoM"),
        "yoy_plausibility": AI.movement_plausibility(yoy_, "YoY"),
        "wholesale_to_retail_spread": spread,
        "mandi_to_household_spread": mh_spread,
        "quick_commerce_premium_pct": qc_premium,
        "signal_confidence": confidence,
        "retail_transmission_lag": lag,
        "price_stickiness": stickiness,
        "consumer_benefit_pass_through": pass_through,
        "telangana_lens": telangana,
        "likely_driver": driver_label,
    }


@app.get("/api/weekly-report")
def api_weekly_report(city: str = Query("Hyderabad")):
    conn = get_conn()
    location_id = loc_id(conn, city)

    material = I.most_material_this_week(conn, location_id, "actual", top_n=10)
    essential_mom = I.basket_movement(conn, location_id, "essential", 30)
    essential_wow = I.basket_movement(conn, location_id, "essential", 7)
    struct_volatile = I.structural_vs_volatile(conn, location_id, "actual", 30)
    reality_gap = I.household_reality_gap(conn, location_id, "essential")
    unseen = I.unseen_layer(conn, location_id, top_n=4)

    telangana_cities = ["Hyderabad", "Siddipet", "Nizamabad"]
    telangana_basket = {}
    for tc in telangana_cities:
        try:
            tloc = loc_id(conn, tc)
        except HTTPException:
            continue
        bm = I.basket_movement(conn, tloc, "essential", 7)
        telangana_basket[tc] = bm

    top_contributors = (essential_mom["contributions"][:5] if essential_mom else [])

    conn.close()
    return {
        "city": city,
        "week_ending": "2026-07-20",
        "most_material_price_movements": material,
        "essential_basket_mom": essential_mom,
        "essential_basket_wow": essential_wow,
        "what_drove_the_basket": top_contributors,
        "structural_vs_volatile": struct_volatile,
        "household_reality_gap": reality_gap,
        "unseen_layer": unseen,
        "telangana_lens": telangana_basket,
    }


_GRADE_EVIDENCE_SCORE = {"A": 90, "B": 70, "C": 50, "D": 30, "E": 10}


def _compute_source_confidence(conn, source, total_cities, obs_count, cities_from_source, is_live):
    """
    Honest, per-source confidence scoring. Only scores a dimension when it can
    be computed from real data this source has actually produced — per the
    frozen PRD, a dimension with no evidence is marked insufficient, never
    given a fabricated number.

    Composite is a weighted SUM across all five dimensions, where an unscored
    dimension contributes 0 (not skipped-and-reweighted). Reweighting across
    only the scored dimensions was tried first and rejected: it let a
    connector with zero live observations show composite 50/100, because
    Evidence Quality (a declared grade, not measured evidence) dominated the
    shrunken weight base. A connector with no live data earning a mid-range
    score is exactly the false confidence this feature exists to prevent.
    """
    sid = source["id"]
    dims = {}

    # Evidence Quality — the connector's declared source-type grade
    # (A=government primary ... E=synthetic). Always computable, but on its
    # own it describes the *source type*, not whether data has arrived —
    # that's why it can't be allowed to carry the composite alone.
    dims["evidence_quality"] = _GRADE_EVIDENCE_SCORE.get(source["quality_grade_default"], 10)

    # Coverage — real, measured: cities this specific source has actually
    # produced an observation for, divided by total tracked cities. Zero for
    # a connector that has never returned data — that is the honest number.
    dims["coverage"] = round(100 * cities_from_source / total_cities) if total_cities else 0

    # Freshness — only scored once the source has produced at least one real
    # observation. A connector that has only ever returned "no_data" has
    # nothing to be fresh or stale, so this is left unscored, not faked.
    if obs_count > 0:
        dims["freshness"] = 90 if is_live else 60  # live-today vs. has-history-but-not-today

    # Source Agreement — real, measured: for commodity/date/city combinations
    # where THIS source and at least one other live (non-manual) source both
    # reported a price, what fraction moved in the same WoW direction? This
    # replaces a flat 60 placeholder with an actual cross-check. Unscored
    # (not zero) when no overlapping observations exist yet to compare.
    cur = conn.cursor()
    cur.execute(
        """
        SELECT a.product_id, a.location_id, a.observation_date,
               a.normalized_unit_price as a_price, b.normalized_unit_price as b_price
        FROM price_observations a
        JOIN price_observations b
          ON a.product_id = b.product_id AND a.location_id = b.location_id
         AND a.observation_date = b.observation_date AND a.channel = b.channel
        JOIN data_sources ds_b ON ds_b.id = b.source_id
        WHERE a.source_id = ? AND b.source_id != ? AND ds_b.source_type != 'manual'
        LIMIT 500
        """,
        (sid, sid),
    )
    overlaps = cur.fetchall()
    if overlaps:
        # Simple, honest agreement metric: within-tolerance price match rate.
        within_tolerance = sum(
            1 for r in overlaps
            if r["a_price"] and r["b_price"] and abs(r["a_price"] - r["b_price"]) / r["b_price"] <= 0.08
        )
        dims["source_agreement"] = round(100 * within_tolerance / len(overlaps))
    # else: omitted — no overlapping observations yet to cross-check against

    # Historical Reliability — real, measured: pulled from the continuous
    # learning loop's tracked hit rate (scoring_weights.hit_rate), which is
    # only populated once backtest_history.py has run and insight_log calls
    # have been outcome-checked 14 days later. Zero rows today in this
    # database means this stays unscored — not assumed, not faked.
    cur.execute("SELECT hit_rate, sample_size FROM scoring_weights WHERE sample_size > 10")
    rows = cur.fetchall()
    if rows:
        avg_hit_rate = sum(r["hit_rate"] for r in rows if r["hit_rate"] is not None) / len(rows)
        dims["historical_reliability"] = round(avg_hit_rate * 100)
    # else: omitted — insufficient backtest sample size

    weights = {
        "evidence_quality": 0.25,
        "source_agreement": 0.20,
        "coverage": 0.20,
        "freshness": 0.15,
        "historical_reliability": 0.20,
    }
    composite = round(sum(dims.get(k, 0) * w for k, w in weights.items()))

    return {
        "dimensions": dims,
        "dimensions_scored": len(dims),
        "dimensions_total": len(weights),
        "composite": composite,
    }


def _data_status(source, obs_count, is_live):
    if source["source_type"] == "manual":
        return "SYNTHETIC"
    if is_live:
        return "LIVE"
    if obs_count > 0:
        return "PARTIAL"  # has historical live observations, but not from today's run
    return "CONFIGURED_NO_LIVE_DATA"


@app.get("/api/consumption/hces")
def api_consumption_hces(survey_round: str = Query("2023-24")):
    """
    Real MoSPI HCES data only -- see consumption_seed.py for source and
    verification notes. Returns [] rather than fabricated rows if that round
    hasn't been seeded.
    """
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "SELECT geography, item_group, mpce_rs, share_pct, source_url "
        "FROM consumption_hces WHERE survey_round=? ORDER BY item_group, geography",
        (survey_round,),
    )
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    if not rows:
        return {"survey_round": survey_round, "rows": [], "note": "No verified HCES data seeded for this round yet."}

    by_item = {}
    for r in rows:
        by_item.setdefault(r["item_group"], {})[r["geography"]] = {
            "mpce_rs": r["mpce_rs"], "share_pct": r["share_pct"]
        }
    food_total = by_item.get("food_total", {})
    rural_urban_gap_pts = None
    if food_total.get("rural") and food_total.get("urban"):
        rural_urban_gap_pts = round(food_total["rural"]["share_pct"] - food_total["urban"]["share_pct"], 2)

    return {
        "survey_round": survey_round,
        "source_url": rows[0]["source_url"],
        "by_item_group": by_item,
        "food_share_gap_rural_minus_urban_pts": rural_urban_gap_pts,
    }


@app.get("/api/export/dataset.xlsx")
def api_export_xlsx():
    """Full multi-sheet workbook -- price_observations, inflation_benchmarks,
    external_drivers, consumption_hces, a grade-distribution summary, and a
    README explaining the quality grades. Meant as a fallback for external
    AI tools or spreadsheets if this app's own pattern detection isn't
    enough -- every row keeps its data_quality_score so nothing downstream
    can mistake synthetic (grade E) rows for real data."""
    data = EX.build_xlsx_export()
    return StreamingResponse(
        io.BytesIO(data),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=hpi_dataset_export.xlsx"},
    )


@app.get("/api/export/price_observations.csv")
def api_export_price_csv(product_id: int = None, city: str = Query(None)):
    conn = get_conn()
    location_id = None
    if city:
        cur = conn.cursor()
        cur.execute("SELECT id FROM locations WHERE city=?", (city,))
        row = cur.fetchone()
        location_id = row["id"] if row else None
    rows = EX.export_price_observations_rows(conn, product_id=product_id, location_id=location_id)
    conn.close()
    if not rows:
        return PlainTextResponse("No rows match that filter.", status_code=404)
    csv_text = EX._dict_rows_to_csv(rows, list(rows[0].keys()))
    return StreamingResponse(
        io.StringIO(csv_text), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=price_observations.csv"},
    )


@app.get("/api/export/inflation_benchmarks.csv")
def api_export_inflation_csv():
    conn = get_conn()
    rows = EX.export_inflation_benchmarks_rows(conn)
    conn.close()
    if not rows:
        return PlainTextResponse("No inflation benchmark rows available yet.", status_code=404)
    csv_text = EX._dict_rows_to_csv(rows, list(rows[0].keys()))
    return StreamingResponse(
        io.StringIO(csv_text), media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=inflation_benchmarks.csv"},
    )


@app.get("/api/sources")
def api_sources():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM data_sources")
    sources = [dict(r) for r in cur.fetchall()]

    cur.execute("SELECT id FROM data_sources WHERE source_name='Manual / Seed Synthetic'")
    seed_source_row = cur.fetchone()
    seed_source_id = seed_source_row["id"] if seed_source_row else None

    cur.execute("SELECT COUNT(*) as n FROM price_observations WHERE source_id=?", (seed_source_id,))
    seed_obs = cur.fetchone()["n"] if seed_source_id else 0
    cur.execute("SELECT COUNT(*) as n FROM price_observations WHERE source_id IS NOT ? OR ? IS NULL", (seed_source_id, seed_source_id))
    live_obs = cur.fetchone()["n"] if seed_source_id else 0
    cur.execute("SELECT COUNT(*) as n FROM price_observations")
    total_obs = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(DISTINCT location_id) as n FROM price_observations")
    cities_covered = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) as n FROM locations")
    total_cities = cur.fetchone()["n"] or 1

    # Configured = real (non-manual) connectors that exist in code.
    # Healthy = last run actually returned real data ("ok (" prefix).
    # Pending = configured but not yet healthy (no key/network/no real data yet).
    configured = [s for s in sources if s["source_type"] != "manual"]
    healthy = [s for s in configured if s["last_status"] and s["last_status"].startswith("ok (")]
    pending = [s for s in configured if s not in healthy]

    for s in sources:
        # A source's real data may land in price_observations (AGMARKNET,
        # Consumer Affairs) OR inflation_benchmarks (CPI, WPI) -- combine
        # both so a live CPI/WPI connector isn't invisible to scoring just
        # because it doesn't report prices.
        cur.execute("SELECT COUNT(*) as n FROM price_observations WHERE source_id=?", (s["id"],))
        price_obs_count = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) as n FROM inflation_benchmarks WHERE source_id=?", (s["id"],))
        benchmark_obs_count = cur.fetchone()["n"]
        obs_count = price_obs_count + benchmark_obs_count
        # Coverage (cities) only has meaning for price_observations rows --
        # inflation_benchmarks are national/state aggregates, not per-city,
        # so this stays 0 for CPI/WPI rather than a fabricated substitute.
        cur.execute("SELECT COUNT(DISTINCT location_id) as n FROM price_observations WHERE source_id=?", (s["id"],))
        cities_from_source = cur.fetchone()["n"]
        is_live = bool(s["last_status"] and s["last_status"].startswith("ok ("))
        s["data_status"] = _data_status(s, obs_count, is_live)
        s["confidence"] = _compute_source_confidence(conn, s, total_cities, obs_count, cities_from_source, is_live)

    conn.close()
    return {
        "sources": sources,
        "seed_synthetic_observations": seed_obs,
        "live_observations": live_obs,
        "total_observations": total_obs,
        "cities_covered": cities_covered,
        "connectors_configured": len(configured),
        "connectors_healthy": len(healthy),
        "connectors_pending": len(pending),
    }


@app.get("/api/intelligence/commodity/{product_id}")
def api_intelligence_commodity(product_id: int, city: str = Query("Hyderabad")):
    conn = get_conn()
    location_id = loc_id(conn, city)
    anomaly = AI.anomaly_flag(conn, product_id, location_id, "local_retail")
    seasonal = AI.recurring_seasonal_pattern(conn, product_id, location_id, "local_retail")
    projection = AI.trend_projection(conn, product_id, location_id, "local_retail")

    driver_tests = [
        ("rainfall_mm", "Telangana", "Rainfall (Telangana)"),
        ("diesel_price", "national", "Diesel Price (national)"),
        ("import_cost_index", "international", "Global Edible Oil Import Cost"),
        ("global_export_price_onion", "international", "Global Onion Export Reference"),
        ("global_export_price_rice", "international", "Global Rice Export Reference"),
    ]
    drivers = []
    for driver_type, scope, label in driver_tests:
        result = AI.driver_correlation(conn, product_id, location_id, driver_type, scope=scope)
        if result.get("available"):
            drivers.append({"driver": label, **result})
    conn.close()
    return {"anomaly": anomaly, "seasonal_pattern": seasonal, "trend_projection": projection, "external_drivers": drivers}


@app.get("/api/intelligence/international-context")
def api_international_context(city: str = Query("Hyderabad")):
    conn = get_conn()
    location_id = loc_id(conn, city)
    cur = conn.cursor()

    def pid(name):
        cur.execute("SELECT id FROM product_master WHERE commodity=?", (name,))
        row = cur.fetchone()
        return row["id"] if row else None

    pairs = [
        ("Sunflower Oil", "import_cost_index", "international", "Global edible oil import cost (world price × INR/USD rate)"),
        ("Groundnut Oil", "import_cost_index", "international", "Global edible oil import cost (world price × INR/USD rate)"),
        ("Onion", "global_export_price_onion", "international", "Global onion export reference price"),
        ("Rice (regular)", "global_export_price_rice", "international", "Global rice export reference price"),
        ("Basmati Rice", "global_export_price_rice", "international", "Global rice export reference price"),
    ]
    results = []
    for commodity, driver_type, scope, description in pairs:
        product_id = pid(commodity)
        if not product_id:
            continue
        r = AI.driver_correlation(conn, product_id, location_id, driver_type, scope=scope)
        results.append({"commodity": commodity, "driver_description": description, **r})
    conn.close()
    return {"city": city, "results": results}


@app.get("/api/intelligence/publish-queue")
def api_publish_queue(city: str = Query("Hyderabad")):
    conn = get_conn()
    location_id = loc_id(conn, city)
    material = I.most_material_this_week(conn, location_id, "actual", top_n=12)
    cur = conn.cursor()
    queue = []
    for r in material:
        cur.execute("SELECT id FROM product_master WHERE commodity=?", (r["commodity"],))
        prow = cur.fetchone()
        anomaly = AI.anomaly_flag(conn, prow["id"], location_id, "local_retail") if prow else None
        rec = AI.publish_recommendation(r, anomaly)
        queue.append({**r, "anomaly": anomaly, "recommendation": rec})
    # persist today's calls so the learning loop can check them later
    AI.log_current_insights(conn, location_id, city, material)
    conn.close()
    return {"city": city, "queue": queue}


@app.get("/api/intelligence/learning-status")
def api_learning_status():
    conn = get_conn()
    status = AI.learning_status(conn)
    conn.close()
    return status


@app.post("/api/intelligence/evaluate-outcomes")
def api_evaluate_outcomes():
    conn = get_conn()
    result = AI.evaluate_outcomes(conn)
    conn.close()
    return result


@app.get("/api/intelligence/regional-snapshot/{product_id}")
def api_regional_snapshot(product_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT commodity FROM product_master WHERE id=?", (product_id,))
    row = cur.fetchone()
    if not row:
        conn.close()
        raise HTTPException(404, "Unknown product")
    result = AI.regional_supply_demand_snapshot(conn, product_id, row["commodity"])
    conn.close()
    return result


@app.get("/api/intelligence/evidence-trace/{product_id}")
def api_evidence_trace(product_id: int, city: str = Query("Hyderabad")):
    conn = get_conn()
    location_id = loc_id(conn, city)
    cur = conn.cursor()
    cur.execute("SELECT commodity FROM product_master WHERE id=?", (product_id,))
    row = cur.fetchone()
    commodity_name = row["commodity"] if row else None
    result = AI.evidence_trace(conn, product_id, location_id, commodity_name=commodity_name)
    conn.close()
    return result


@app.get("/api/intelligence/driver-correlation/{product_id}")
def api_driver_correlation(product_id: int, city: str = Query("Hyderabad"), driver_type: str = Query("diesel_price")):
    conn = get_conn()
    location_id = loc_id(conn, city)
    scope = "Telangana" if driver_type == "rainfall_mm" else "national"
    result = AI.driver_correlation(conn, product_id, location_id, driver_type, scope=scope)
    conn.close()
    return result


@app.get("/api/commodity/{product_id}/grades")
def api_commodity_grades(product_id: int, city: str = Query("Hyderabad")):
    conn = get_conn()
    location_id = loc_id(conn, city)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT grade FROM price_observations WHERE product_id=? AND location_id=? AND channel='local_retail'", (product_id, location_id))
    grades_present = [r["grade"] or "FAQ" for r in cur.fetchall()]
    conn.close()
    if len(grades_present) < 2:
        return {"available": False, "reason": "This commodity isn't tracked with separate grade tiers in the current data."}

    conn = get_conn()
    cur = conn.cursor()
    bands = {}
    for g in grades_present:
        cur.execute(
            """SELECT normalized_unit_price FROM price_observations
               WHERE product_id=? AND location_id=? AND channel='local_retail' AND grade=?
               ORDER BY observation_date DESC LIMIT 1""",
            (product_id, location_id, g),
        )
        row = cur.fetchone()
        if row:
            bands[g] = row["normalized_unit_price"]
    conn.close()

    faq_price = bands.get("FAQ")
    result = {"available": True, "bands": bands}
    if faq_price:
        result["spread_vs_faq"] = {g: round((p / faq_price - 1) * 100, 1) for g, p in bands.items() if g != "FAQ"}
    return result


# --- serve frontend ---
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")


@app.get("/")
def root():
    return FileResponse(os.path.join(FRONTEND_DIR, "index.html"))


@app.get("/{page_name}.html")
def page(page_name: str):
    path = os.path.join(FRONTEND_DIR, f"{page_name}.html")
    if os.path.exists(path):
        return FileResponse(path)
    raise HTTPException(404)
