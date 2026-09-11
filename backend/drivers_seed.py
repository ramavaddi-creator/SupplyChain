"""
Populates external_drivers from the shared external_reality module (see
that file for why it's shared with seed.py rather than independently
random). Clearly synthetic (data_quality_score='E') until
backend/connectors/external_drivers.py is pointed at real PPAC/IMD data.
"""
from datetime import timedelta
from database import get_conn, init_db
import external_reality as ER


def seed_drivers():
    init_db()
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT id FROM data_sources WHERE source_name='Manual / Seed Synthetic'")
    source_id = cur.fetchone()["id"]

    for name, connector, freq, grade in [
        ("PPAC Fuel Price", "PPACFuelConnector", "daily", "C"),
        ("IMD Rainfall", "IMDRainfallConnector", "monthly", "C"),
    ]:
        cur.execute(
            "INSERT INTO data_sources (source_name, source_type, connector_name, update_frequency, quality_grade_default, active_status) VALUES (?,?,?,?,?,1) ON CONFLICT (source_name) DO NOTHING",
            (name, "government", connector, freq, grade),
        )
    conn.commit()

    diesel_rows = [(d, v, source_id) for d, v in ER._DAILY_DIESEL.items()]
    cur.executemany(
        """INSERT INTO external_drivers (driver_type, scope, observation_date, value, unit, source_id, data_quality_score)
           VALUES ('diesel_price', 'national', ?, ?, 'INR/litre', ?, 'E')
           ON CONFLICT (driver_type, scope, observation_date) DO NOTHING""",
        diesel_rows,
    )

    rain_rows = [(f"{yy:04d}-{mm:02d}-01", ER.rainfall_actual(yy, mm), source_id) for (yy, mm) in ER.all_months()]
    cur.executemany(
        """INSERT INTO external_drivers (driver_type, scope, observation_date, value, unit, source_id, data_quality_score)
           VALUES ('rainfall_mm', 'Telangana', ?, ?, 'mm', ?, 'E')
           ON CONFLICT (driver_type, scope, observation_date) DO NOTHING""",
        rain_rows,
    )

    import_cost_rows = [(d, ER.import_cost_index(d), source_id) for d in ER._DAILY_DIESEL if ER.import_cost_index(d) is not None]
    cur.executemany(
        """INSERT INTO external_drivers (driver_type, scope, observation_date, value, unit, source_id, data_quality_score)
           VALUES ('import_cost_index', 'international', ?, ?, 'index (USD/tonne x FX, /1000)', ?, 'E')
           ON CONFLICT (driver_type, scope, observation_date) DO NOTHING""",
        import_cost_rows,
    )

    for key in ("onion", "rice"):
        export_rows = [(d, v, source_id) for d, v in ER._GLOBAL_EXPORT[key].items()]
        cur.executemany(
            f"""INSERT INTO external_drivers (driver_type, scope, observation_date, value, unit, source_id, data_quality_score)
               VALUES ('global_export_price_{key}', 'international', ?, ?, 'USD/tonne', ?, 'E')
               ON CONFLICT (driver_type, scope, observation_date) DO NOTHING""",
            export_rows,
        )

    conn.commit()
    cur.execute("SELECT COUNT(*) as n FROM external_drivers WHERE driver_type='diesel_price'")
    print(f"Diesel price rows: {cur.fetchone()['n']}")
    cur.execute("SELECT COUNT(*) as n FROM external_drivers WHERE driver_type='rainfall_mm'")
    print(f"Rainfall rows: {cur.fetchone()['n']}")
    conn.close()


if __name__ == "__main__":
    seed_drivers()
