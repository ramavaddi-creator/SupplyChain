"""
Analytics engine. Implements the frozen PRD's core calculations and six
locked intelligence metrics. Every function returns None (not a fabricated
number) when there isn't enough history/evidence to support a claim --
per the frozen PRD's "confidence must be visible, never claim causation
without evidence" principle.

NOTE ON TIME WINDOWS: this V1 seed covers ~70 days of daily history for
core commodities. That is enough for WoW and MoM. It is NOT enough for a
genuine YoY figure -- so YoY is deliberately reported as unavailable
rather than faked. Once real connectors accumulate 12+ months of history,
YoY becomes computable the same way MoM is now.
"""
from datetime import date, timedelta
from database import get_conn

GRADE_SCORE = {"A": 95, "B": 80, "C": 65, "D": 50, "E": 30}


def _series(conn, product_id, location_id, channel):
    cur = conn.cursor()
    cur.execute(
        """SELECT observation_date, normalized_unit_price, data_quality_score, provisional_flag
           FROM price_observations
           WHERE product_id=? AND location_id=? AND channel=? AND (grade='FAQ' OR grade IS NULL)
           ORDER BY observation_date ASC""",
        (product_id, location_id, channel),
    )
    return cur.fetchall()


def latest_price(conn, product_id, location_id, channel):
    rows = _series(conn, product_id, location_id, channel)
    return rows[-1] if rows else None


def pct_change_over(conn, product_id, location_id, channel, days_back):
    rows = _series(conn, product_id, location_id, channel)
    if len(rows) < 2:
        return None
    latest = rows[-1]
    latest_date = date.fromisoformat(latest["observation_date"])
    target_date = latest_date - timedelta(days=days_back)
    # find closest observation at/before target_date
    candidates = [r for r in rows if date.fromisoformat(r["observation_date"]) <= target_date]
    if not candidates:
        return None  # insufficient history -- do not fabricate
    base = candidates[-1]
    if base["normalized_unit_price"] in (None, 0):
        return None
    return round((latest["normalized_unit_price"] / base["normalized_unit_price"] - 1) * 100, 2)


def wow(conn, product_id, location_id, channel):
    return pct_change_over(conn, product_id, location_id, channel, 7)


def mom(conn, product_id, location_id, channel):
    return pct_change_over(conn, product_id, location_id, channel, 30)


def yoy(conn, product_id, location_id, channel):
    rows = _series(conn, product_id, location_id, channel)
    if not rows:
        return None
    span_days = (date.fromisoformat(rows[-1]["observation_date"]) - date.fromisoformat(rows[0]["observation_date"])).days
    if span_days < 300:
        return None  # honestly insufficient -- do not fabricate a YoY figure
    return pct_change_over(conn, product_id, location_id, channel, 365)


def wholesale_retail_spread(conn, product_id, location_id):
    """Wholesale-to-Retail Price Spread. Never called 'margin'."""
    mandi = latest_price(conn, product_id, location_id, "mandi")
    retail = latest_price(conn, product_id, location_id, "local_retail")
    if not mandi or not retail:
        return None
    spread_abs = retail["normalized_unit_price"] - mandi["normalized_unit_price"]
    spread_pct = (spread_abs / mandi["normalized_unit_price"]) * 100 if mandi["normalized_unit_price"] else None
    return {"spread_abs": round(spread_abs, 2), "spread_pct": round(spread_pct, 1) if spread_pct else None}


def quick_commerce_premium(conn, product_id, location_id):
    """Quick-commerce premium vs comparable SUPERMARKET price (not mandi)."""
    sm = latest_price(conn, product_id, location_id, "supermarket")
    qc = latest_price(conn, product_id, location_id, "quick_commerce")
    if not sm or not qc or not sm["normalized_unit_price"]:
        return None
    return round((qc["normalized_unit_price"] / sm["normalized_unit_price"] - 1) * 100, 1)


def mandi_to_household_spread(conn, product_id, location_id):
    """Mandi-to-Household Price Spread (renamed from 'Distortion Index')."""
    mandi = latest_price(conn, product_id, location_id, "mandi")
    qc = latest_price(conn, product_id, location_id, "quick_commerce") or latest_price(conn, product_id, location_id, "local_retail")
    if not mandi or not qc:
        return None
    return round((qc["normalized_unit_price"] / mandi["normalized_unit_price"] - 1) * 100, 1)


def data_confidence(conn, product_id, location_id, channel):
    """Price Signal Confidence: High/Medium/Low from source quality + coverage."""
    rows = _series(conn, product_id, location_id, channel)
    if not rows:
        return "Low"
    grades = [GRADE_SCORE.get(r["data_quality_score"], 50) for r in rows[-10:]]
    avg_grade = sum(grades) / len(grades)
    coverage_penalty = 0 if len(rows) >= 20 else (15 if len(rows) >= 8 else 30)
    provisional_penalty = 10 if any(r["provisional_flag"] for r in rows[-5:]) else 0
    score = avg_grade - coverage_penalty - provisional_penalty
    if score >= 80:
        return "High"
    elif score >= 60:
        return "Medium"
    return "Low"


def transmission_lag(conn, product_id, location_id, target_channel, max_lag=18):
    """Estimate days between a mandi price movement and a downstream channel's
    response, via lagged correlation of daily % changes. Returns None if the
    commodity has no mandi/daily history (e.g. FMCG, or non-core weekly data)."""
    mandi_rows = _series(conn, product_id, location_id, "mandi")
    target_rows = _series(conn, product_id, location_id, target_channel)
    if len(mandi_rows) < max_lag + 10 or len(target_rows) < max_lag + 10:
        return None

    mandi_by_date = {r["observation_date"]: r["normalized_unit_price"] for r in mandi_rows}
    target_by_date = {r["observation_date"]: r["normalized_unit_price"] for r in target_rows}
    dates = sorted(set(mandi_by_date) & set(target_by_date))
    if len(dates) < max_lag + 10:
        return None

    def pct_series(by_date, ds):
        vals = []
        for i in range(1, len(ds)):
            p0, p1 = by_date.get(ds[i - 1]), by_date.get(ds[i])
            vals.append((p1 / p0 - 1) if p0 else 0.0)
        return vals

    mandi_pct = pct_series(mandi_by_date, dates)
    target_pct = pct_series(target_by_date, dates)

    best_lag, best_corr = 0, -2
    for lag in range(0, max_lag + 1):
        if lag >= len(mandi_pct):
            break
        x = mandi_pct[: len(mandi_pct) - lag]
        y = target_pct[lag:]
        n = min(len(x), len(y))
        if n < 15:
            continue
        x, y = x[:n], y[:n]
        mx, my = sum(x) / n, sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        vx = sum((xi - mx) ** 2 for xi in x)
        vy = sum((yi - my) ** 2 for yi in y)
        if vx <= 0 or vy <= 0:
            continue
        corr = cov / ((vx ** 0.5) * (vy ** 0.5))
        if corr > best_corr:
            best_corr, best_lag = corr, lag
    if best_corr < 0.15:
        return None  # not enough evidence of a transmission relationship
    return {"lag_days": best_lag, "correlation": round(best_corr, 2)}


def price_stickiness(conn, product_id, location_id, target_channel):
    """Price Stickiness Index: how much of an upstream rise passes through vs
    how much of an upstream fall passes through. Returns None if insufficient
    daily history exists."""
    mandi_rows = _series(conn, product_id, location_id, "mandi")
    target_rows = _series(conn, product_id, location_id, target_channel)
    if len(mandi_rows) < 25 or len(target_rows) < 25:
        return None

    mandi_by_date = {r["observation_date"]: r["normalized_unit_price"] for r in mandi_rows}
    target_by_date = {r["observation_date"]: r["normalized_unit_price"] for r in target_rows}
    dates = sorted(set(mandi_by_date) & set(target_by_date))
    if len(dates) < 25:
        return None

    lag_info = transmission_lag(conn, product_id, location_id, target_channel)
    lag = lag_info["lag_days"] if lag_info else 5

    up_upstream, up_downstream, down_upstream, down_downstream = 0.0, 0.0, 0.0, 0.0
    for i in range(lag + 1, len(dates)):
        d_now, d_prev = dates[i], dates[i - 1]
        d_src, d_src_prev = dates[i - lag], dates[i - lag - 1] if (i - lag - 1) >= 0 else None
        if d_src_prev is None:
            continue
        p0, p1 = mandi_by_date.get(d_src_prev), mandi_by_date.get(d_src)
        q0, q1 = target_by_date.get(d_prev), target_by_date.get(d_now)
        if not all([p0, p1, q0, q1]):
            continue
        u_pct = p1 / p0 - 1
        d_pct = q1 / q0 - 1
        if u_pct > 0.001:
            up_upstream += u_pct
            up_downstream += d_pct
        elif u_pct < -0.001:
            down_upstream += u_pct
            down_downstream += d_pct

    pass_up = round((up_downstream / up_upstream) * 100, 1) if up_upstream else None
    pass_down = round((down_downstream / down_upstream) * 100, 1) if down_upstream else None
    return {"pass_through_on_rise_pct": pass_up, "pass_through_on_fall_pct": pass_down}


def consumer_benefit_pass_through(conn, product_id, location_id, target_channel="local_retail"):
    """0-100: how much of an upstream PRICE FALL reached the household."""
    stickiness = price_stickiness(conn, product_id, location_id, target_channel)
    if not stickiness or stickiness["pass_through_on_fall_pct"] is None:
        return None
    val = stickiness["pass_through_on_fall_pct"]
    return max(0, min(100, round(val, 1)))


def material_household_impact_raw(pct_change, basket_weight, purchase_frequency):
    """Uncapped raw Household Impact magnitude = |price change| x basket
    weight x purchase frequency. NOT the final 0-100 score -- see
    insights.percentile_rank(), which converts a population of these raw
    scores into percentiles so one volatile commodity can't cheaply hit
    100 just by exceeding a fixed magic-number threshold. Ranking is always
    relative to the OTHER commodities being scored the same week."""
    if pct_change is None or basket_weight is None:
        return None
    freq = purchase_frequency or 1
    return abs(pct_change) * basket_weight * (freq ** 0.5)


def confidence_band_to_causal_label(evidence_strength):
    if evidence_strength >= 0.6:
        return "Likely Driver"
    elif evidence_strength >= 0.3:
        return "Possible Driver"
    return "Insufficient Evidence"
