"""
Runs once a day (registered in app.py's startup event). For each real
source (AGMARKNET, Consumer Affairs, CPI, WPI), tries the actual connector
first. If it returns 0 rows -- no API key set, no network, source
temporarily down -- falls back to the synthetic daily append so the app
still "moves forward" for demo/testing purposes, with the fallback clearly
logged so it's never confused with a real data day.

This is the mechanism that answers "does this update every day": today,
it does via synthetic continuation; the moment you set AGMARKNET_API_KEY /
CA_CSV_PATH / CPI_CSV_PATH / WPI_CSV_PATH on your own machine, real data
takes over automatically without any other code change.
"""
from datetime import date, datetime
from database import get_conn
from connectors.agmarknet import AGMARKNETConnector
from connectors.consumer_affairs import ConsumerAffairsConnector
from connectors.cpi_wpi import CPIConnector, WPIConnector
from connectors.external_drivers import PPACFuelConnector, IMDRainfallConnector
import synthetic_daily as SD
import intelligence as I

REAL_CONNECTORS = [AGMARKNETConnector, ConsumerAffairsConnector, CPIConnector, WPIConnector,
                    PPACFuelConnector, IMDRainfallConnector]


def run_daily_ingestion(force=False):
    conn = get_conn()
    cur = conn.cursor()
    today_str = date.today().isoformat()

    if not force:
        cur.execute("SELECT last_run_at FROM data_sources WHERE source_name='Manual / Seed Synthetic'")
        row = cur.fetchone()
        if row and row["last_run_at"] and row["last_run_at"].startswith(today_str):
            conn.close()
            return {"date": today_str, "skipped": "already ran today"}

    summary = {"date": today_str, "sources": []}

    any_real_data = False
    for connector_cls in REAL_CONNECTORS:
        connector = connector_cls(conn)
        count = connector.run()
        summary["sources"].append({"source": connector.source_name, "rows": count})
        if count > 0:
            any_real_data = True

    # Fallback: keep the synthetic dataset moving forward for whichever
    # portion of the pipeline doesn't yet have real data flowing.
    if not any_real_data:
        fallback = SD.append_one_day()
        summary["synthetic_fallback"] = fallback
        status_text = f"synthetic fallback: {fallback.get('series_appended', 0)} series appended for {fallback.get('date')}"
    else:
        status_text = "real connector data ingested today -- no synthetic fallback needed"

    cur.execute(
        "UPDATE data_sources SET last_run_at=?, last_status=? WHERE source_name='Manual / Seed Synthetic'",
        (datetime.utcnow().isoformat(), status_text),
    )
    conn.commit()

    # Continuous learning: check any insights that have aged past 14 days
    learning_result = I.evaluate_outcomes(conn)
    summary["learning_evaluation"] = learning_result

    conn.close()
    return summary


if __name__ == "__main__":
    import json
    print(json.dumps(run_daily_ingestion(), indent=2))
