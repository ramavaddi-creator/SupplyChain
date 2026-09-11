"""
Intelligence layer. Three honestly-separated capabilities, per the frozen
PRD's evidence-first principle:

1. Pattern & anomaly detection -- real statistics on accumulated history.
2. Model Projection -- a simple trend model that must pass a backtest
   before it is ever shown. Fails the backtest -> returns None, not a
   fabricated number.
3. Publish-timing recommendation -- rule-based (Movement/Materiality/
   Breadth/Persistence/Evidence), with an optional natural-language
   reasoning layer via Claude (backend/claude_narrator.py), gated behind
   ANTHROPIC_API_KEY exactly like the AGMARKNET connector is gated behind
   its own key.

Continuous learning: insight_log records every "most material" call made;
evaluate_outcomes() checks, 14 days later, whether the call held up; the
resulting hit-rate nudges scoring_weights over time. This is a real,
transparent feedback loop -- not a black box.
"""
from datetime import date, timedelta
from database import get_conn
import metrics as M

DEFAULT_WEIGHTS = {"movement": 0.25, "materiality": 0.30, "breadth": 0.15, "persistence": 0.15, "evidence": 0.15}


def get_weights(conn):
    cur = conn.cursor()
    cur.execute("SELECT metric_name, weight FROM scoring_weights")
    rows = {r["metric_name"]: r["weight"] for r in cur.fetchall()}
    return {**DEFAULT_WEIGHTS, **rows}


def _daily_pct_series(conn, product_id, location_id, channel):
    """CRITICAL: excludes data_quality_score='E' (synthetic seed data).
    Every function that calls this -- recurring_seasonal_pattern,
    trend_projection, anomaly_flag -- produces a claimed pattern, trend, or
    anomaly. Per the frozen PRD's source hierarchy, E-grade observations
    must never feed public alerts, forecasts, or published findings. Before
    this fix, synthetic seed rows and real government rows were mixed
    together with no distinction, meaning every "pattern" this code could
    produce was potentially describing invented data, not reality. With this
    filter, until real (A-D grade) data actually accumulates over real
    calendar time, these functions correctly return "insufficient evidence"
    rather than a fabricated story."""
    cur = conn.cursor()
    cur.execute(
        """SELECT observation_date, normalized_unit_price FROM price_observations
           WHERE product_id=? AND location_id=? AND channel=? AND (grade='FAQ' OR grade IS NULL)
           AND data_quality_score IS NOT NULL AND data_quality_score != 'E'
           ORDER BY observation_date ASC""",
        (product_id, location_id, channel),
    )
    rows = cur.fetchall()
    dates = [r["observation_date"] for r in rows]
    prices = [r["normalized_unit_price"] for r in rows]
    pct = [None] + [(prices[i] / prices[i - 1] - 1) for i in range(1, len(prices))]
    return dates, prices, pct


def anomaly_flag(conn, product_id, location_id, channel="local_retail", window=60, z_threshold=2.3):
    """Flags today's move as anomalous relative to its own trailing baseline
    (not a fixed threshold) -- so what counts as 'unusual' is commodity- and
    city-specific, learned from its own history."""
    dates, prices, pct = _daily_pct_series(conn, product_id, location_id, channel)
    if len(pct) < window + 5:
        return None
    recent = [p for p in pct[-(window + 1):-1] if p is not None]
    if len(recent) < 20:
        return None
    mean = sum(recent) / len(recent)
    var = sum((x - mean) ** 2 for x in recent) / len(recent)
    std = var ** 0.5
    if std == 0:
        return None
    today_pct = pct[-1]
    z = (today_pct - mean) / std
    return {
        "z_score": round(z, 2),
        "flagged": abs(z) >= z_threshold,
        "direction": "up" if z > 0 else "down",
        "baseline_mean_daily_pct": round(mean * 100, 3),
        "baseline_days": len(recent),
    }


def movement_plausibility(pct_change, period="WoW"):
    """Flags an implausibly large percentage move for human review before
    it's presented as a confirmed finding. This is deliberately separate
    from anomaly_flag() -- that checks a single day's move against a
    60-day baseline; this checks the ACCUMULATED WoW/MoM/YoY figure
    against a generic plausibility ceiling, since a slow but steady drift
    can produce an implausible cumulative number without ever tripping a
    single-day anomaly."""
    if pct_change is None:
        return {"flagged": False}
    thresholds = {"WoW": 20, "MoM": 35, "YoY": 50}
    threshold = thresholds.get(period, 50)
    if abs(pct_change) > threshold:
        return {
            "flagged": True,
            "period": period,
            "threshold_pct": threshold,
            "observed_pct": pct_change,
            "possible_reasons": [
                "Unit conversion error",
                "Different SKU/variety/grade being compared across dates",
                "Synthetic/seed data artifact (if applicable)",
                "Missing or thin historical baseline",
                "Genuine market shock -- verify against a second source before treating as confirmed",
            ],
        }
    return {"flagged": False, "period": period, "threshold_pct": threshold}


def recurring_seasonal_pattern(conn, product_id, location_id, channel="local_retail"):
    """Detects a genuine recurring monthly pattern. A single long-term trend
    line is fit across the FULL price history first (removing secular
    drift/inflation), then month-by-month residual ratios are compared
    across years. Fitting the trend globally -- rather than within each
    comparison window -- matters here: if you only have a partial-year
    window to compare, a within-window trend fit can absorb real
    seasonality as if it were drift. Returns unavailable if there isn't
    enough history or the years don't actually agree."""
    dates, prices, pct = _daily_pct_series(conn, product_id, location_id, channel)
    if len(dates) < 500:
        return {"available": False, "reason": "Requires 16+ months of history; insufficient evidence."}

    n = len(prices)
    xs = list(range(n))
    mx, my = sum(xs) / n, sum(prices) / n
    num = sum((xs[i] - mx) * (prices[i] - my) for i in range(n))
    den = sum((xs[i] - mx) ** 2 for i in range(n)) or 1e-9
    slope = num / den
    intercept = my - slope * mx
    residual_ratio = [prices[i] / (intercept + slope * xs[i]) for i in range(n)]

    by_year_month = {}
    for d_str, r in zip(dates, residual_ratio):
        d = date.fromisoformat(d_str)
        by_year_month.setdefault((d.year, d.month), []).append(r)
    monthly_avg = {k: sum(v) / len(v) for k, v in by_year_month.items()}

    years = sorted(set(y for y, m in monthly_avg))
    if len(years) < 2:
        return {"available": False, "reason": "Requires at least two distinct years of history."}

    def profile(year):
        present = {m: monthly_avg.get((year, m)) for m in range(1, 13) if monthly_avg.get((year, m)) is not None}
        return present if len(present) >= 5 else None

    candidate_profiles = [(yr, profile(yr)) for yr in years]
    candidate_profiles = [(yr, p) for yr, p in candidate_profiles if p is not None]
    if len(candidate_profiles) < 2:
        return {"available": False, "reason": "Fewer than two years have enough month coverage to compare."}

    best_pair, best_overlap = None, 0
    for i in range(len(candidate_profiles)):
        for j in range(i + 1, len(candidate_profiles)):
            yr_a, p_a = candidate_profiles[i]
            yr_b, p_b = candidate_profiles[j]
            overlap = len(set(p_a) & set(p_b))
            if overlap > best_overlap:
                best_overlap = overlap
                best_pair = ((yr_a, p_a), (yr_b, p_b))
    if not best_pair or best_overlap < 5:
        return {"available": False, "reason": "No two years share enough overlapping months for a fair comparison."}
    (y1, p1), (y2, p2) = best_pair

    common_months = sorted(set(p1) & set(p2))
    if len(common_months) < 5:
        return {"available": False, "reason": "Not enough overlapping months between years."}

    x = [p1[m] for m in common_months]
    y = [p2[m] for m in common_months]
    mx, my = sum(x) / len(x), sum(y) / len(y)
    cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
    vx = sum((xi - mx) ** 2 for xi in x)
    vy = sum((yi - my) ** 2 for yi in y)
    if vx <= 0 or vy <= 0:
        return {"available": False, "reason": "No monthly variation detected."}
    corr = cov / ((vx ** 0.5) * (vy ** 0.5))

    peak_month = max(common_months, key=lambda m: (p1[m] + p2[m]) / 2)
    trough_month = min(common_months, key=lambda m: (p1[m] + p2[m]) / 2)
    peak_avg_deviation = round((((p1[peak_month] + p2[peak_month]) / 2) - 1) * 100, 1)
    trough_avg_deviation = round((((p1[trough_month] + p2[trough_month]) / 2) - 1) * 100, 1)

    if corr >= 0.6:
        confidence = "High"
    elif corr >= 0.35:
        confidence = "Medium"
    else:
        return {"available": False, "reason": f"Year-over-year correlation too low ({round(corr,2)}) to call this a recurring pattern rather than noise."}

    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    monthly_profile = [
        {"month": month_names[m], "avg_deviation_pct": round((((p1[m] + p2[m]) / 2) - 1) * 100, 1)}
        for m in common_months
    ]
    return {
        "available": True,
        "confidence": confidence,
        "year_over_year_correlation": round(corr, 2),
        "months_compared": len(common_months),
        "peak_month": month_names[peak_month],
        "peak_avg_deviation_pct": peak_avg_deviation,
        "trough_month": month_names[trough_month],
        "trough_avg_deviation_pct": trough_avg_deviation,
        "years_compared": [y1, y2],
        "monthly_profile": monthly_profile,
    }


def trend_projection(conn, product_id, location_id, channel="local_retail", horizon_days=14, fit_window=60, backtest_mape_threshold=9.0):
    """Simple linear-trend projection over the recent fit window, but only
    returned if it passes a genuine backtest (predict the last known
    `horizon_days` using the window before them, measure MAPE). If it fails,
    returns why -- never a silently-fabricated number."""
    dates, prices, _ = _daily_pct_series(conn, product_id, location_id, channel)
    if len(prices) < fit_window + horizon_days + 30:
        return {"available": False, "reason": "Insufficient history for a backtested projection."}

    def linfit_predict(y_train, n_predict):
        n = len(y_train)
        xs = list(range(n))
        mx = sum(xs) / n
        my = sum(y_train) / n
        num = sum((xs[i] - mx) * (y_train[i] - my) for i in range(n))
        den = sum((xs[i] - mx) ** 2 for i in range(n)) or 1e-9
        slope = num / den
        intercept = my - slope * mx
        return [intercept + slope * (n - 1 + k) for k in range(1, n_predict + 1)]

    # backtest: hold out the last horizon_days of KNOWN data
    train_bt = prices[-(fit_window + horizon_days):-horizon_days]
    actual_bt = prices[-horizon_days:]
    pred_bt = linfit_predict(train_bt, horizon_days)
    mape = sum(abs(a - p) / a for a, p in zip(actual_bt, pred_bt)) / len(actual_bt) * 100

    result = {
        "available": mape <= backtest_mape_threshold,
        "backtest_mape_pct": round(mape, 1),
        "backtest_window_days": fit_window,
        "method": "Linear trend (local window), backtested",
    }
    if not result["available"]:
        result["reason"] = f"Backtest error ({round(mape,1)}%) exceeds the {backtest_mape_threshold}% threshold for this commodity -- not shown as a projection."
        return result

    train_live = prices[-fit_window:]
    pred_live = linfit_predict(train_live, horizon_days)
    current_price = prices[-1]
    projected_price = pred_live[-1]
    result["projected_pct_change"] = round((projected_price / current_price - 1) * 100, 2)
    result["horizon_days"] = horizon_days
    return result


def evidence_trace(conn, product_id, location_id, channel="local_retail", commodity_name=None):
    """'Why are we saying this?' -- the observation counts, date range,
    city breakdown, sources, and calculation method behind a WoW/breadth
    finding for one commodity. Built so every insight is defensible, not
    just asserted."""
    cur = conn.cursor()
    cur.execute("SELECT MAX(observation_date) as d FROM price_observations WHERE product_id=?", (product_id,))
    latest_date_row = cur.fetchone()
    latest_date = latest_date_row["d"] if latest_date_row else None

    cur.execute("SELECT id, city FROM locations")
    all_locs = cur.fetchall()
    city_breakdown = {}
    total_count = 0
    min_date, max_date = None, None
    for loc in all_locs:
        cur.execute(
            """SELECT COUNT(*) as n, MIN(observation_date) as mind, MAX(observation_date) as maxd
               FROM price_observations
               WHERE product_id=? AND location_id=? AND channel=? AND observation_date >= date(?, '-30 days')""",
            (product_id, loc["id"], channel, latest_date),
        )
        row = cur.fetchone()
        if row["n"]:
            city_breakdown[loc["city"]] = row["n"]
            total_count += row["n"]
            if min_date is None or (row["mind"] and row["mind"] < min_date):
                min_date = row["mind"]
            if max_date is None or (row["maxd"] and row["maxd"] > max_date):
                max_date = row["maxd"]

    cur.execute(
        """SELECT DISTINCT ds.source_name FROM price_observations po
           JOIN data_sources ds ON ds.id = po.source_id
           WHERE po.product_id=? AND po.channel=?""",
        (product_id, channel),
    )
    sources = [r["source_name"] for r in cur.fetchall()]

    cur.execute(
        """SELECT data_quality_score, COUNT(*) as n FROM price_observations
           WHERE product_id=? AND location_id=? AND channel=? GROUP BY data_quality_score""",
        (product_id, location_id, channel),
    )
    grade_dist = {r["data_quality_score"]: r["n"] for r in cur.fetchall()}

    from datetime import datetime, timedelta
    cutoff_date = (datetime.strptime(latest_date, "%Y-%m-%d") - timedelta(days=30)).strftime("%Y-%m-%d")
    cur.execute(
        "SELECT COUNT(*) as n FROM price_observations WHERE product_id=? AND location_id=? AND channel=? AND provisional_flag=1 AND observation_date >= ?",
        (product_id, location_id, channel, cutoff_date),
    )
    provisional_count = cur.fetchone()["n"]

    return {
        "commodity": commodity_name,
        "as_of_date": latest_date,
        "total_observations_all_cities_30d": total_count,
        "city_breakdown_30d": city_breakdown,
        "date_range": [min_date, max_date],
        "sources": sources,
        "data_quality_grade_distribution_this_city": grade_dist,
        "provisional_observations_this_city_30d": provisional_count,
        "calculation_method": (
            "WoW = latest normalized_unit_price vs the closest observation at least 7 days prior, same channel (Local Retail). "
            "Breadth = share of tracked cities (see breakdown above) showing the same direction move (>2%) in the same window. "
            "Confidence = source quality grade + observation coverage + freshness + provisional-flag penalty."
        ),
    }


def seasonal_multiplier_lookup(commodity_name, month):
    import commodities as C
    return C.seasonal_multiplier(commodity_name, month)


def regional_supply_demand_snapshot(conn, product_id, commodity_name):
    """V1 Regional Supply & Demand snapshot. Honest about scope: consumption
    is a synthetic per-capita index (not NSSO survey data), supply is real
    AGMARKNET-style mandi arrivals (seeded here, real once connectors run),
    and price sensitivity is a genuine lagged correlation between arrivals
    and price -- not a fabricated elasticity number. Stock levels, imports,
    and inter-state flows are NOT modeled yet (would need FCI/DGCIS data)
    and are explicitly flagged as a gap rather than guessed at.
    """
    import commodities as C
    cur = conn.cursor()
    per_capita = C.PER_CAPITA_MONTHLY_CONSUMPTION.get(commodity_name)
    typical_arrivals = C.TYPICAL_DAILY_ARRIVALS_TONNES.get(commodity_name)

    cur.execute("SELECT id, city, tier FROM locations")
    city_rows = cur.fetchall()

    current_month = date.today().month
    seasonal_factor = seasonal_multiplier_lookup(commodity_name, current_month)

    snapshot = []
    for loc in city_rows:
        city, tier = loc["city"], loc["tier"]
        pop_millions = C.CITY_POPULATION_MILLIONS.get(city)

        consumption_index = None
        if per_capita and pop_millions:
            consumption_index = round(per_capita * pop_millions * 1_000_000 / 1000, 1)  # tonnes/month, illustrative

        cur.execute(
            """SELECT observation_date, arrivals, normalized_unit_price FROM price_observations
               WHERE product_id=? AND location_id=? AND channel='mandi' AND arrivals IS NOT NULL
               ORDER BY observation_date DESC LIMIT 30""",
            (product_id, loc["id"]),
        )
        arrivals_rows = cur.fetchall()
        supply_index = None
        if arrivals_rows:
            supply_index = round(sum(r["arrivals"] for r in arrivals_rows) / len(arrivals_rows), 1)

        deficit_surplus = None
        if consumption_index is not None and supply_index is not None:
            monthly_supply = supply_index * 30
            deficit_surplus = round(monthly_supply - consumption_index, 1)

        demand_intensity = round(seasonal_factor * 100, 1) if seasonal_factor else None

        snapshot.append({
            "city": city, "tier": tier,
            "consumption_index_tonnes_month": consumption_index,
            "supply_index_avg_daily_arrivals_tonnes": supply_index,
            "deficit_surplus_tonnes_month": deficit_surplus,
            "demand_intensity_index": demand_intensity,
        })

    dependency = None
    hyd = next((s for s in snapshot if s["city"] == "Hyderabad"), None)
    regional_towns = [s for s in snapshot if s["tier"] == "telangana_regional" and s["supply_index_avg_daily_arrivals_tonnes"]]
    if hyd and hyd["supply_index_avg_daily_arrivals_tonnes"] and regional_towns:
        avg_regional_supply = sum(s["supply_index_avg_daily_arrivals_tonnes"] for s in regional_towns) / len(regional_towns)
        if avg_regional_supply > 0:
            ratio = hyd["supply_index_avg_daily_arrivals_tonnes"] / avg_regional_supply
            dependency = {
                "hyderabad_vs_regional_supply_ratio": round(ratio, 2),
                "interpretation": (
                    "Hyderabad's own mandi arrivals are lower than production-adjacent towns -- "
                    "consistent with net inbound dependency, though inter-state inflow is not directly measured yet."
                    if ratio < 0.8 else
                    "Hyderabad's mandi arrivals are comparable to or exceed production-adjacent towns."
                ),
            }

    price_sensitivity = None
    if typical_arrivals and hyd:
        cur.execute(
            """SELECT observation_date, arrivals, normalized_unit_price FROM price_observations
               WHERE product_id=? AND location_id=(SELECT id FROM locations WHERE city='Hyderabad')
               AND channel='mandi' AND arrivals IS NOT NULL ORDER BY observation_date ASC""",
            (product_id,),
        )
        rows = cur.fetchall()
        if len(rows) > 60:
            arr = [r["arrivals"] for r in rows]
            prc = [r["normalized_unit_price"] for r in rows]
            arr_pct = [None] + [(arr[i] / arr[i - 1] - 1) if arr[i - 1] else None for i in range(1, len(arr))]
            prc_pct = [None] + [(prc[i] / prc[i - 1] - 1) if prc[i - 1] else None for i in range(1, len(prc))]
            pairs = [(a, p) for a, p in zip(arr_pct, prc_pct) if a is not None and p is not None]
            if len(pairs) > 30:
                xs, ys = zip(*pairs)
                n = len(xs)
                mx, my = sum(xs) / n, sum(ys) / n
                cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
                vx = sum((x - mx) ** 2 for x in xs)
                vy = sum((y - my) ** 2 for y in ys)
                if vx > 0 and vy > 0:
                    corr = cov / ((vx ** 0.5) * (vy ** 0.5))
                    price_sensitivity = {
                        "arrivals_to_price_correlation": round(corr, 2),
                        "interpretation": (
                            "Negative correlation confirms the expected relationship: when arrivals fall, price rises."
                            if corr < -0.2 else
                            "Correlation too weak to confirm a clear arrivals-price relationship from this data."
                        ),
                    }

    return {
        "commodity": commodity_name,
        "current_month_seasonal_demand_factor": demand_intensity,
        "by_city": snapshot,
        "dependency": dependency,
        "price_sensitivity": price_sensitivity,
        "scope_note": (
            "V1 scope: consumption is a synthetic per-capita index, supply is mandi arrivals "
            "(seeded here, real once AGMARKNET is connected). Production estimates, stock levels "
            "(FCI/state warehousing), and inter-state inflow data are NOT modeled yet -- "
            "dependency above is inferred only from relative arrivals, not actual trade-flow data."
        ),
    }


def publish_recommendation(insight_row, anomaly=None):
    """Rule-based decision on WHEN to surface an insight, using the same
    five PRD dimensions already computed for classification. Five possible
    actions, ordered from most to least urgent -- this is the 'should this
    post now or later' capability: deterministic and explainable, not a
    black box."""
    breadth = insight_row["breadth_score"]
    persistence = insight_row["persistence_score"]
    evidence = insight_row["evidence_score"]
    composite = insight_row["composite_score"]
    classification = insight_row["classification"]

    reasons = []
    if classification == "Shock" and evidence >= 65 and breadth >= 30:
        action = "Publish Now"
        reasons.append(f"Classified {classification} with breadth {breadth}% and evidence score {evidence} -- cross-city and well-evidenced.")
    elif composite >= 55 and (breadth < 30 or persistence < 55):
        action = "Hold — Recheck in 3-5 days"
        if breadth < 30:
            reasons.append(f"Breadth is only {breadth}% -- move may be city-specific rather than a genuine trend.")
        if persistence < 55:
            reasons.append("WoW and MoM directions don't agree yet -- could be a short-lived spike.")
    elif evidence < 55 and composite >= 40:
        action = "Watch Only"
        reasons.append(f"Evidence score {evidence} is too thin to act on yet -- worth tracking, not yet worth surfacing.")
    elif composite < 25:
        action = "Discard — Weak Signal"
        reasons.append(f"Composite score {composite} doesn't clear the minimum bar to be worth a reader's attention.")
    else:
        action = "Bundle into Weekly Report only"
        reasons.append("Meets baseline materiality but not urgent enough for a standalone alert.")

    if anomaly and anomaly.get("flagged"):
        reasons.append(f"Also flagged as a statistical anomaly (z={anomaly['z_score']}) vs its own {anomaly['baseline_days']}-day baseline.")

    return {"action": action, "reasoning": " ".join(reasons)}


def _driver_series(conn, driver_type, scope=None):
    """Same E-grade exclusion as _daily_pct_series -- a correlation between
    a real price series and a synthetic driver series would be meaningless,
    so synthetic driver rows are excluded here too."""
    cur = conn.cursor()
    base = "SELECT observation_date, value FROM external_drivers WHERE driver_type=? AND (data_quality_score IS NULL OR data_quality_score != 'E')"
    if scope:
        cur.execute(base + " AND scope=? ORDER BY observation_date ASC", (driver_type, scope))
    else:
        cur.execute(base + " ORDER BY observation_date ASC", (driver_type,))
    rows = cur.fetchall()
    return [r["observation_date"] for r in rows], [r["value"] for r in rows]


def driver_correlation(conn, product_id, location_id, driver_type, scope=None, channel="local_retail", max_lag=35):
    """Correlational context only -- per the frozen PRD, this NEVER becomes
    a causal claim on its own. Returns Likely/Possible/Insufficient Evidence
    exactly like the transmission-lag driver labels elsewhere. Daily drivers
    (diesel, import cost, export price) are compared via daily pct-change
    lag correlation; sparse/monthly drivers (rainfall) are aggregated to
    monthly first -- forcing a monthly series through a daily pct-change
    comparison mostly measures forward-fill artifacts, not a real relationship."""
    price_dates, _, price_pct_full = _daily_pct_series(conn, product_id, location_id, channel)
    driver_dates, driver_vals = _driver_series(conn, driver_type, scope)
    if len(price_dates) < 60 or len(driver_dates) < 8:
        return {"available": False, "reason": "Insufficient history for either the commodity or the driver series."}

    is_sparse_driver = len(driver_dates) < 60  # e.g. ~25 monthly points over 24 months

    if is_sparse_driver:
        return _driver_correlation_monthly(price_dates, price_pct_full, driver_dates, driver_vals)
    return _driver_correlation_daily(price_dates, price_pct_full, driver_dates, driver_vals, max_lag)


def _driver_correlation_daily(price_dates, price_pct_full, driver_dates, driver_vals, max_lag):
    price_pct_by_date = dict(zip(price_dates, price_pct_full))
    driver_by_date = dict(zip(driver_dates, driver_vals))
    sorted_driver_dates = sorted(driver_by_date)

    def ffill(target_date):
        val = None
        for dd in sorted_driver_dates:
            if dd <= target_date:
                val = driver_by_date[dd]
            else:
                break
        return val

    common_dates = sorted(price_pct_by_date)
    driver_on_price_dates = [ffill(d) for d in common_dates]
    if any(v is None for v in driver_on_price_dates[:10]):
        return {"available": False, "reason": "Driver series doesn't cover the start of the price history."}

    driver_pct = [None] + [
        (driver_on_price_dates[i] / driver_on_price_dates[i - 1] - 1) if driver_on_price_dates[i - 1] not in (None, 0) else None
        for i in range(1, len(driver_on_price_dates))
    ]
    price_pct = [price_pct_by_date[d] for d in common_dates]

    best_lag, best_corr = 0, 0
    for lag in range(0, max_lag + 1):
        pairs = [(driver_pct[i - lag], price_pct[i]) for i in range(lag + 1, len(price_pct))
                 if i - lag >= 1 and driver_pct[i - lag] is not None and price_pct[i] is not None]
        if len(pairs) < 30:
            continue
        corr = _pearson(pairs)
        if corr is not None and abs(corr) > abs(best_corr):
            best_corr, best_lag = corr, lag

    return _label_result(best_corr, best_lag, unit="days")


def _driver_correlation_monthly(price_dates, price_pct_full, driver_dates, driver_vals):
    # aggregate daily price pct-changes to a monthly average movement
    from collections import defaultdict
    monthly_prices = defaultdict(list)
    for d, v in zip(price_dates, price_pct_full):
        if v is not None:
            monthly_prices[d[:7]].append(v)
    monthly_price_pct = {ym: sum(vals) / len(vals) for ym, vals in monthly_prices.items() if vals}

    driver_by_month = {d[:7]: v for d, v in zip(driver_dates, driver_vals)}
    sorted_months = sorted(set(monthly_price_pct) & set(driver_by_month))
    if len(sorted_months) < 8:
        return {"available": False, "reason": "Not enough overlapping months between the commodity and driver series."}

    driver_month_pct = {}
    sorted_driver_months = sorted(driver_by_month)
    for i in range(1, len(sorted_driver_months)):
        m0, m1 = sorted_driver_months[i - 1], sorted_driver_months[i]
        if driver_by_month[m0]:
            driver_month_pct[m1] = driver_by_month[m1] / driver_by_month[m0] - 1

    best_lag, best_corr = 0, 0
    for lag in range(0, 4):  # 0-3 month lag, appropriate for a monthly-resolution driver
        pairs = []
        for i, m in enumerate(sorted_driver_months):
            if i - lag < 0:
                continue
            src_month = sorted_driver_months[i - lag]
            if src_month in driver_month_pct and m in monthly_price_pct:
                pairs.append((driver_month_pct[src_month], monthly_price_pct[m]))
        if len(pairs) < 8:
            continue
        corr = _pearson(pairs)
        if corr is not None and abs(corr) > abs(best_corr):
            best_corr, best_lag = corr, lag

    return _label_result(best_corr, best_lag, unit="months")


def _pearson(pairs):
    xs, ys = zip(*pairs)
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / ((vx ** 0.5) * (vy ** 0.5))


def _label_result(corr, lag, unit):
    if abs(corr) < 0.35:
        return {"available": False, "reason": f"Correlation too weak ({round(corr,2)}) to call this a driver rather than coincidence.",
                "correlation": round(corr, 2)}
    label = "Likely Driver" if abs(corr) >= 0.55 else "Possible Driver"
    return {
        "available": True,
        "correlation": round(corr, 2),
        "lag": lag,
        "lag_unit": unit,
        "label": label,
        "direction": "same direction" if corr > 0 else "opposite direction",
    }


# ---------------- continuous learning loop ----------------

def log_current_insights(conn, location_id, city, insights):
    """Persist today's 'most material' calls so they can be checked later."""
    cur = conn.cursor()
    today = date.today().isoformat()
    for r in insights:
        cur.execute("SELECT id FROM product_master WHERE commodity=?", (r["commodity"],))
        row = cur.fetchone()
        if not row:
            continue
        product_id = row["id"]
        price_row = M.latest_price(conn, product_id, location_id, "local_retail")
        cur.execute(
            """INSERT INTO insight_log
               (logged_at, product_id, location_id, commodity, city, classification, composite_score, wow_pct_at_log_time, price_at_log_time)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (today, product_id, location_id, r["commodity"], city, r["classification"], r["composite_score"],
             r["wow_pct"], price_row["normalized_unit_price"] if price_row else None),
        )
    conn.commit()


def evaluate_outcomes(conn):
    """For logged insights >=14 days old with no outcome yet, check whether
    the move actually persisted, then recalibrate scoring_weights from the
    accumulated hit rate. This is the 'keeps learning' loop -- simple,
    transparent, and only as good as the history it has seen so far."""
    cur = conn.cursor()
    cutoff = (date.today() - timedelta(days=14)).isoformat()
    cur.execute("SELECT * FROM insight_log WHERE outcome_checked=0 AND logged_at<=?", (cutoff,))
    pending = cur.fetchall()
    checked = 0
    for row in pending:
        price_now = M.latest_price(conn, row["product_id"], row["location_id"], "local_retail")
        if not price_now or not row["price_at_log_time"]:
            continue
        actual_change = (price_now["normalized_unit_price"] / row["price_at_log_time"] - 1) * 100
        logged_wow = row["wow_pct_at_log_time"] or 0
        persisted = 1 if (logged_wow != 0 and (actual_change > 0) == (logged_wow > 0) and abs(actual_change) >= abs(logged_wow) * 0.5) else 0
        cur.execute(
            "UPDATE insight_log SET outcome_checked=1, outcome_pct_change_14d=?, outcome_persisted=? WHERE id=?",
            (round(actual_change, 2), persisted, row["id"]),
        )
        checked += 1
    conn.commit()

    cur.execute("SELECT classification, outcome_persisted FROM insight_log WHERE outcome_checked=1")
    rows = cur.fetchall()
    if not rows:
        return {"checked_this_run": checked, "total_evaluated": 0, "hit_rate": None}
    hit_rate = sum(r["outcome_persisted"] for r in rows) / len(rows)

    weights = get_weights(conn)
    if hit_rate >= 0.6:
        weights["persistence"] = min(0.25, weights["persistence"] + 0.01)
    elif hit_rate < 0.4:
        weights["persistence"] = max(0.08, weights["persistence"] - 0.01)
        weights["evidence"] = min(0.25, weights["evidence"] + 0.01)
    total = sum(weights.values())
    weights = {k: round(v / total, 3) for k, v in weights.items()}

    for name, w in weights.items():
        cur.execute(
            """INSERT INTO scoring_weights (metric_name, weight, sample_size, hit_rate, last_recalibrated)
               VALUES (?,?,?,?,?)
               ON CONFLICT(metric_name) DO UPDATE SET weight=excluded.weight, sample_size=excluded.sample_size,
                 hit_rate=excluded.hit_rate, last_recalibrated=excluded.last_recalibrated""",
            (name, w, len(rows), round(hit_rate, 3), date.today().isoformat()),
        )
    conn.commit()
    return {"checked_this_run": checked, "total_evaluated": len(rows), "hit_rate": round(hit_rate, 3), "weights": weights}


def learning_status(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as n FROM insight_log")
    total_logged = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) as n FROM insight_log WHERE outcome_checked=1")
    total_evaluated = cur.fetchone()["n"]
    cur.execute("SELECT COUNT(*) as n FROM insight_log WHERE outcome_checked=1 AND outcome_persisted=1")
    total_hit = cur.fetchone()["n"]
    cur.execute("SELECT * FROM scoring_weights ORDER BY metric_name")
    weights = [dict(r) for r in cur.fetchall()]
    return {
        "total_insights_logged": total_logged,
        "total_evaluated": total_evaluated,
        "hit_rate": round(total_hit / total_evaluated, 3) if total_evaluated else None,
        "current_weights": weights if weights else [{"metric_name": k, "weight": v, "sample_size": 0, "hit_rate": None, "last_recalibrated": None} for k, v in DEFAULT_WEIGHTS.items()],
    }
