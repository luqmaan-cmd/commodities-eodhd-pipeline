#!/usr/bin/env bash
set -euo pipefail

# EODHD Commodities Pipeline — Deploy script
# Builds, pushes, and updates Cloud Run jobs.
#
# Usage:
#   ./deploy.sh v1.0.6
#   ./deploy.sh v1.0.6 --region=europe-west2 --project=my-project
#
# Required env vars (or pass via flags):
#   GCP_PROJECT   — GCP project ID
#   GCP_REGION    — GCP region (default: europe-west2)

# ── Defaults ────────────────────────────────────────────────────────────────
REGION="${GCP_REGION:-europe-west2}"
PROJECT="${GCP_PROJECT:-}"
REPO="commodities-pipeline"
IMAGE_NAME="eodhd-commodities"

# ── Parse args ──────────────────────────────────────────────────────────────
VERSION=""

for arg in "$@"; do
    case "$arg" in
        --region=*)  REGION="${arg#--region=}" ;;
        --project=*) PROJECT="${arg#--project=}" ;;
        v*)          VERSION="$arg" ;;
        *)           echo "Unknown argument: $arg"; exit 1 ;;
    esac
done

if [ -z "$VERSION" ]; then
    echo "ERROR: Version tag required (e.g. ./deploy.sh v1.0.6)"
    exit 1
fi

if [ -z "$PROJECT" ]; then
    echo "ERROR: GCP project not set. Use --project=... or set GCP_PROJECT env var"
    exit 1
fi

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${IMAGE_NAME}:${VERSION}"

echo "========================================"
echo " EODHD Commodities Pipeline Deploy"
echo "========================================"
echo " Version : ${VERSION}"
echo " Project : ${PROJECT}"
echo " Region  : ${REGION}"
echo " Image   : ${IMAGE}"
echo "========================================"
echo ""
read -p "Continue? [y/N] " confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "Y" ]; then
    echo "Aborted."
    exit 0
fi

# ── Build ───────────────────────────────────────────────────────────────────
echo ""
echo ">>> Building Docker image..."
docker build --platform linux/amd64 -t "${IMAGE}" .

# ── Push ────────────────────────────────────────────────────────────────────
echo ""
echo ">>> Pushing to Artifact Registry..."
docker push "${IMAGE}"

# ── Update Cloud Run Jobs ──────────────────────────────────────────────────
echo ""
echo ">>> Updating commodities-daily..."
gcloud run jobs update commodities-daily \
    --region="${REGION}" \
    --image="${IMAGE}"

echo ""
echo ">>> Updating commodities-backfill..."
gcloud run jobs update commodities-backfill \
    --region="${REGION}" \
    --image="${IMAGE}"

echo ""
echo "========================================"
echo " Deploy complete: ${VERSION}"
echo "========================================"
