"""
Retroactively walks the 24 months of seed history, simulating what the
insight engine would have flagged at each biweekly checkpoint, and checks
whether that call actually held up 14 days later. This is what lets the
Continuous Learning page show a real (if simplified) hit-rate on day one,
using the accumulated history you asked to seed, rather than starting from
an empty log. Going forward, log_current_insights() + evaluate_outcomes()
in intelligence.py take over with real day-by-day logging.
"""
from datetime import date, timedelta
from database import get_conn
import intelligence as I

CORE_COMMODITIES = [
    "Rice (regular)", "Atta", "Toor Dal", "Moong Dal", "Milk", "Curd", "Ghee",
    "Sunflower Oil", "Groundnut Oil", "Sugar", "Salt", "Tea (mid-market)",
    "Filter Coffee", "Jeera", "Turmeric", "Red Chilli", "Peanuts / Groundnuts",
    "Tomato", "Onion", "Potato",
]


def backtest():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM locations")
    all_locations = [r["id"] for r in cur.fetchall()]

    rows_to_insert = []
    for commodity in CORE_COMMODITIES:
        cur.execute("SELECT pm.id as id, bd.weight as weight FROM product_master pm JOIN basket_definitions bd ON bd.product_id=pm.id WHERE pm.commodity=? AND bd.basket_name='actual' LIMIT 1", (commodity,))
        prow = cur.fetchone()
        if not prow:
            continue
        product_id, weight = prow["id"], prow["weight"]

        for location_id in all_locations:
            cur.execute(
                "SELECT observation_date, normalized_unit_price FROM price_observations WHERE product_id=? AND location_id=? AND channel='local_retail' AND (grade='FAQ' OR grade IS NULL) ORDER BY observation_date ASC",
                (product_id, location_id),
            )
            series = cur.fetchall()
            if len(series) < 60:
                continue
            dates = [r["observation_date"] for r in series]
            prices = [r["normalized_unit_price"] for r in series]

            for i in range(30, len(prices) - 15, 14):
                p_now, p_week_ago, p_14d_later = prices[i], prices[i - 7], prices[i + 14]
                wow_pct = round((p_now / p_week_ago - 1) * 100, 2)
                actual_change = round((p_14d_later / p_now - 1) * 100, 2)
                movement_score = min(100, abs(wow_pct) * 8)
                materiality_score = min(100, weight * 11)
                composite = round(movement_score * 0.25 + materiality_score * 0.30 + 50 * 0.40, 1)
                classification = "Shock" if (movement_score >= 55 and materiality_score >= 30) else "Regular"
                persisted = 1 if (wow_pct != 0 and (actual_change > 0) == (wow_pct > 0) and abs(actual_change) >= abs(wow_pct) * 0.5) else 0

                rows_to_insert.append((
                    dates[i], product_id, location_id, commodity, None, classification, composite,
                    wow_pct, p_now, 1, actual_change, persisted,
                ))

    cur.executemany(
        """INSERT INTO insight_log
           (logged_at, product_id, location_id, commodity, city, classification, composite_score,
            wow_pct_at_log_time, price_at_log_time, outcome_checked, outcome_pct_change_14d, outcome_persisted)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        rows_to_insert,
    )
    conn.commit()
    print(f"Backtested {len(rows_to_insert)} historical insight checkpoints across {len(CORE_COMMODITIES)} commodities")

    result = I.evaluate_outcomes(conn)
    print("Initial calibration from historical backtest:", result)
    conn.close()


if __name__ == "__main__":
    backtest()
