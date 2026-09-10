"""
Basket aggregation and the weekly Regular/Shock/Unseen insight engine.
Scoring follows the frozen PRD section 4: Movement, Materiality, Breadth,
Persistence, Evidence -- computed BEFORE classification, so "Shock" emerges
from evidence rather than being a target.
"""
from database import get_conn
import metrics as M


def get_products(conn, basket_name=None):
    cur = conn.cursor()
    if basket_name:
        cur.execute(
            """SELECT pm.id, pm.commodity, pm.category, pm.structural_flag, pm.regional_relevance,
                      bd.weight, bd.purchase_frequency
               FROM product_master pm
               JOIN basket_definitions bd ON bd.product_id = pm.id
               WHERE bd.basket_name=? AND bd.active_status=1 AND pm.active_status=1""",
            (basket_name,),
        )
    else:
        cur.execute("SELECT id, commodity, category, structural_flag, regional_relevance FROM product_master WHERE active_status=1")
    return cur.fetchall()


def basket_movement(conn, location_id, basket_name, days_back, channel="local_retail"):
    rows = get_products(conn, basket_name)
    total_weight = sum(r["weight"] for r in rows)
    weighted_sum, covered_weight = 0.0, 0.0
    contributions = []
    for r in rows:
        pc = M.pct_change_over(conn, r["id"], location_id, channel, days_back)
        if pc is None:
            continue
        weighted_sum += pc * r["weight"]
        covered_weight += r["weight"]
        contributions.append({
            "commodity": r["commodity"], "pct_change": pc, "weight": r["weight"],
            "contribution_pts": round((r["weight"] / total_weight) * pc, 3) if total_weight else 0,
        })
    if covered_weight == 0:
        return None
    basket_pct = weighted_sum / covered_weight
    coverage_pct = round((covered_weight / total_weight) * 100, 1) if total_weight else 0
    return {
        "pct_change": round(basket_pct, 2),
        "coverage_pct": coverage_pct,
        "contributions": sorted(contributions, key=lambda c: -abs(c["contribution_pts"])),
    }


def structural_vs_volatile(conn, location_id, basket_name, days_back, channel="local_retail"):
    rows = get_products(conn, basket_name)
    buckets = {"structural": {"w": 0.0, "sum": 0.0}, "volatile": {"w": 0.0, "sum": 0.0}}
    for r in rows:
        pc = M.pct_change_over(conn, r["id"], location_id, channel, days_back)
        if pc is None:
            continue
        key = "structural" if r["structural_flag"] else "volatile"
        buckets[key]["w"] += r["weight"]
        buckets[key]["sum"] += pc * r["weight"]
    out = {}
    for key, b in buckets.items():
        out[key] = round(b["sum"] / b["w"], 2) if b["w"] else None
    return out


def latest_cpi(conn, benchmark_type="CPI"):
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM inflation_benchmarks WHERE benchmark_type=? ORDER BY year DESC, month DESC LIMIT 1",
        (benchmark_type,),
    )
    row = cur.fetchone()
    return dict(row) if row else None


def household_reality_gap(conn, location_id, basket_name="essential"):
    """Household Reality Gap = Observed basket MoM movement - CPI MoM movement.
    Both expressed on the SAME time basis (MoM) to keep the comparison honest --
    we do not have enough seed history for a true YoY basket figure yet."""
    basket = basket_movement(conn, location_id, basket_name, 30)
    cpi = latest_cpi(conn, "CPI")
    if not basket or not cpi or cpi.get("mom_change") is None:
        return None
    gap = round(basket["pct_change"] - cpi["mom_change"], 2)
    return {
        "observed_basket_mom": basket["pct_change"],
        "cpi_mom": cpi["mom_change"],
        "gap_pts": gap,
        "basket_coverage_pct": basket["coverage_pct"],
    }


def percentile_rank(values, value):
    """Percentile (0-100) of `value` within `values`. Used so Household
    Impact reflects standing relative to what's ACTUALLY moving this week,
    not a fixed magic-number threshold that lets one volatile vegetable
    hit 100 too easily while everything else also clusters near it."""
    if not values or value is None:
        return None
    below_or_equal = sum(1 for v in values if v is not None and v <= value)
    return round((below_or_equal / len(values)) * 100, 1)


def most_material_this_week(conn, location_id, basket_name="actual", top_n=8):
    rows = get_products(conn, basket_name)
    cur = conn.cursor()
    prelim = []
    for r in rows:
        wow = M.pct_change_over(conn, r["id"], location_id, "local_retail", 7)
        mom = M.pct_change_over(conn, r["id"], location_id, "local_retail", 30)
        if wow is None:
            continue
        confidence = M.data_confidence(conn, r["id"], location_id, "local_retail")

        # breadth: how many of the 8 cities show a same-direction move > 2%
        cur.execute("SELECT id FROM locations")
        all_locs = [l["id"] for l in cur.fetchall()]
        same_dir = 0
        checked = 0
        for loc_id in all_locs:
            other_wow = M.pct_change_over(conn, r["id"], loc_id, "local_retail", 7)
            if other_wow is None:
                continue
            checked += 1
            if (other_wow > 2 and wow > 2) or (other_wow < -2 and wow < -2):
                same_dir += 1
        breadth_score = round((same_dir / checked) * 100, 1) if checked else 0

        movement_score = min(100, abs(wow) * 8)
        materiality_score = min(100, r["weight"] * 11)
        persistence_score = 75 if (mom is not None and ((mom > 0) == (wow > 0))) else 35
        evidence_score = {"High": 90, "Medium": 65, "Low": 40}[confidence]

        impact_raw = M.material_household_impact_raw(wow, r["weight"], r["purchase_frequency"])
        composite = round(
            movement_score * 0.25 + materiality_score * 0.30 + breadth_score * 0.15
            + persistence_score * 0.15 + evidence_score * 0.15, 1
        )

        if composite >= 72 and movement_score >= 55 and evidence_score >= 65:
            classification = "Shock"
        else:
            classification = "Regular"

        prelim.append({
            "commodity": r["commodity"], "category": r["category"], "wow_pct": wow, "mom_pct": mom,
            "impact_raw": impact_raw, "composite_score": composite,
            "movement_score": round(movement_score, 1), "materiality_score": round(materiality_score, 1),
            "breadth_score": breadth_score, "persistence_score": persistence_score,
            "evidence_score": evidence_score, "signal_confidence": confidence,
            "classification": classification,
        })

    all_raw = [p["impact_raw"] for p in prelim if p["impact_raw"] is not None]
    results = []
    for p in prelim:
        p["household_impact_score"] = percentile_rank(all_raw, p["impact_raw"])
        del p["impact_raw"]
        results.append(p)

    results.sort(key=lambda x: -x["composite_score"])
    return results[:top_n]


def unseen_layer(conn, location_id, top_n=3):
    """Surfaces Consumer Benefit Pass-Through failures: commodities where
    upstream (mandi) fell but retail didn't follow proportionally."""
    rows = get_products(conn)
    findings = []
    for r in rows:
        pass_through = M.consumer_benefit_pass_through(conn, r["id"], location_id, "local_retail")
        if pass_through is None:
            continue
        lag = M.transmission_lag(conn, r["id"], location_id, "local_retail")
        if pass_through < 60:  # meaningful gap between upstream fall and retail response
            findings.append({
                "commodity": r["commodity"],
                "consumer_benefit_pass_through": pass_through,
                "retail_transmission_lag_days": lag["lag_days"] if lag else None,
            })
    findings.sort(key=lambda x: x["consumer_benefit_pass_through"])
    return findings[:top_n]
