"""
Seeds consumption_hces with REAL published figures from the MoSPI
Household Consumption Expenditure Survey (HCES) 2023-24 Fact Sheet
(https://www.mospi.gov.in/sites/default/files/publication_reports/HCES%20FactSheet%202023-24.pdf).

Every row below is a verified published figure -- nothing here is synthetic
or estimated. Per the frozen PRD, do not add rows to this table that are not
traceable to a specific government release. If a figure is needed but not
yet verified, leave it out rather than approximate it.
"""
from database import get_conn

SOURCE_URL = "https://www.mospi.gov.in/sites/default/files/publication_reports/HCES%20FactSheet%202023-24.pdf"
ROUND = "2023-24"

# (item_group, rural_mpce_rs, rural_share_pct, urban_mpce_rs, urban_share_pct)
ROWS = [
    ("cereals_and_substitutes", 206, 4.99, 263, 3.76),
    ("pulses_and_products", 84, 2.04, 98, 1.40),
    ("sugar_and_salt", 37, 0.89, 40, 0.57),
    ("milk_and_products", 348, 8.44, 503, 7.19),
    ("vegetables", 248, 6.03, 288, 4.12),
    ("fruits", 158, 3.85, 271, 3.87),
    ("egg_fish_meat", 203, 4.92, 249, 3.56),
    ("edible_oil", 114, 2.77, 127, 1.82),
    ("spices", 135, 3.27, 161, 2.30),
    ("beverages_refreshments_processed_food", 406, 9.84, 776, 11.09),
    ("food_total", 1939, 47.04, 2776, 39.68),
    ("non_food_total", 2183, 52.96, 4220, 60.32),
]


def seed_consumption_hces():
    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    for item_group, r_mpce, r_share, u_mpce, u_share in ROWS:
        cur.execute(
            "INSERT OR REPLACE INTO consumption_hces "
            "(survey_round, geography, item_group, mpce_rs, share_pct, source_url) "
            "VALUES (?, 'rural', ?, ?, ?, ?)",
            (ROUND, item_group, r_mpce, r_share, SOURCE_URL),
        )
        cur.execute(
            "INSERT OR REPLACE INTO consumption_hces "
            "(survey_round, geography, item_group, mpce_rs, share_pct, source_url) "
            "VALUES (?, 'urban', ?, ?, ?, ?)",
            (ROUND, item_group, u_mpce, u_share, SOURCE_URL),
        )
        inserted += 2
    conn.commit()
    conn.close()
    return inserted


if __name__ == "__main__":
    n = seed_consumption_hces()
    print(f"Seeded {n} real HCES {ROUND} rows into consumption_hces.")
