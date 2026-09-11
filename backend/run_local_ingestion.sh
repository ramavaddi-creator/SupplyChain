#!/bin/bash
# Wrapper for run_local_ingestion.py -- keeps your actual key/connection
# string out of any file that could accidentally get committed to git.
#
# ONE-TIME SETUP: copy this file to run_local_ingestion.local.sh (which is
# gitignored) and fill in your real values below. Never edit THIS file
# with real secrets in it -- it's the template that DOES get committed.

export AGMARKNET_API_KEY="paste_your_real_key_here"
export DATABASE_URL="paste_your_real_neon_connection_string_here"

cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || true
python3 run_local_ingestion.py
