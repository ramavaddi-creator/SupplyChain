"""
Run this on your own machine (in India) on a schedule -- cron, launchd,
or just manually whenever you want fresh data. It writes real AGMARKNET
observations directly into Neon (the same database Render reads from),
completely independent of Render's own deploy lifecycle.

Why this exists: AGMARKNET's API is reachable and fast from your machine
(proven earlier this session -- real records back in under a second) but
times out from Render's US datacenter. Rather than fight that network
constraint, this script does the actual data collection from where it
already works, and Render just serves whatever's in the shared database.

SETUP (one time):
  1. Make sure DATABASE_URL and AGMARKNET_API_KEY are set in this shell
     before running -- see run_local_ingestion.sh below for the wrapper
     that sets them and calls this script.
  2. To schedule it: add a cron job, e.g. to run every morning at 8am IST:
       0 8 * * * cd /path/to/hpi/backend && ./run_local_ingestion.sh
     Edit with `crontab -e`.

This intentionally does NOT touch the seed/synthetic pipeline -- it only
ever calls real connectors' .run(), which is always safe to call repeatedly
(each real connector's .save() either inserts new real rows or is a no-op
for data that's already there, per each connector's own logic).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_conn, DATABASE_URL


def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set. This script is meant to write to Neon,")
        print("not your local SQLite file -- set DATABASE_URL first (see run_local_ingestion.sh).")
        sys.exit(1)

    if not os.environ.get("AGMARKNET_API_KEY"):
        print("ERROR: AGMARKNET_API_KEY is not set.")
        sys.exit(1)

    conn = get_conn()

    from connectors.agmarknet import AGMARKNETConnector
    agmarknet = AGMARKNETConnector(conn)
    count = agmarknet.run()
    print(f"AGMARKNET: {agmarknet._last_status}")

    # WPI has no key requirement and works from anywhere, but running it
    # here too means it also benefits from your machine's better network
    # path if Render's own attempt ever fails on a given day.
    from connectors.cpi_wpi import WPIConnector
    wpi = WPIConnector(conn)
    wpi.run()
    print(f"WPI: {wpi._last_status}")

    conn.close()
    print(f"Done. AGMARKNET saved {count} real rows to Neon.")


if __name__ == "__main__":
    main()
