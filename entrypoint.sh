#!/usr/bin/env bash
set -euo pipefail

# EODHD Commodities Pipeline — Entrypoint
# Selects backfill or daily mode based on PIPELINE_MODE env var.
#
# Usage:
#   PIPELINE_MODE=backfill  -> runs jobs/backfill.py
#   PIPELINE_MODE=daily     -> runs jobs/daily.py (default)
#
# Additional args can be passed via PIPELINE_ARGS env var.

# ── Activate GCP service account from mounted secret ──────────────────────────
CRED_FILE="${GOOGLE_APPLICATION_CREDENTIALS:-/app/secrets/gcp-key.json}"
if [ -f "$CRED_FILE" ]; then
    echo ">>> Activating GCP service account from $CRED_FILE ..."
    gcloud auth activate-service-account --key-file="$CRED_FILE" 2>/dev/null || {
        echo "WARNING: Failed to activate service account (non-fatal — continuing)"
    }
else
    echo ">>> No GCP credentials file found at $CRED_FILE — skipping auth"
fi

MODE="${PIPELINE_MODE:-daily}"
ARGS="${PIPELINE_ARGS:-}"

case "$MODE" in
    backfill)
        echo ">>> Running BACKFILL pipeline..."
        exec python jobs/backfill.py $ARGS
        ;;
    daily)
        echo ">>> Running DAILY pipeline..."
        exec python jobs/daily.py $ARGS
        ;;
    *)
        echo "ERROR: Unknown PIPELINE_MODE='$MODE'. Must be 'backfill' or 'daily'." >&2
        exit 1
        ;;
esac
