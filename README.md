# EODHD Commodities ETL Pipeline

A production-grade ETL pipeline that fetches end-of-day commodity prices from the [EODHD API](https://eodhistoricaldata.com/), validates them, and upserts them into PostgreSQL. Deployed as Cloud Run Jobs on Google Cloud Platform with Cloud Scheduler for daily automation.

## Architecture

```
Cloud Scheduler (6 AM London)
        |
        v
Cloud Run Job (commodities-daily)
        |
        +---> EODHD API (42 commodities, parallel fetch)
        |         |
        |         v
        |     Schema Validation
        |         |
        |         v
        |     Row Validation (6 checks)
        |         |                    \
        |         v                     v
        |   Valid Rows              Rejected Rows
        |         |                     |
        v         v                     v
    PostgreSQL (upsert)     PostgreSQL (audit table)
        |
        v
    Post-ingestion Validation
        |
        v
    Slack Alerts (3 types)
```

## Features

- **42 commodities** across 7 categories: Energy, Precious Metals, Industrial Metals, Grains, Softs, Livestock & Dairy, Other
- **Parallel fetching** with `ThreadPoolExecutor` (6 workers by default)
- **6 per-row validation checks**: null prices, high >= low, high >= open/close, low <= open/close, negative volume, price anomaly detection
- **Schema validation** on raw API responses before standardization
- **Rejected rows table** for audit and reprocessing of invalid data
- **Post-ingestion validation**: row count, missing symbols, null fields, data freshness
- **3 Slack alert types**: pipeline failure (critical), zero-data on trading day (warning), success summary (info)
- **Idempotent upserts** via `ON CONFLICT (date, symbol) DO UPDATE`
- **Exponential-backoff retry** on API failures (429, 5xx, timeout, connection errors)
- **Structured JSON logging** for Cloud Logging integration
- **VPC connector** for private DB access from Cloud Run

## Project Structure

```
.
├── config/
│   └── commodities.yaml          # Central commodity config (42 symbols)
├── jobs/
│   ├── daily.py                  # Daily ingestion entry point
│   └── backfill.py               # One-time historical backfill entry point
├── sql/
│   └── create_table.sql          # DDL for commodities_eod + rejected tables
├── src/
│   ├── __init__.py
│   ├── api_client.py             # EODHD API client with retry + rate limiting
│   ├── alerter.py                # Slack webhook alerting
│   ├── db.py                     # PostgreSQL connection + batch upsert
│   ├── logger.py                 # Structured JSON logging
│   ├── models.py                 # Dataclasses: CommodityPrice, ValidationResult, etc.
│   ├── pipeline.py               # Pipeline orchestrator (daily + backfill)
│   └── validator.py              # Per-row + post-ingestion + schema validation
├── tests/
│   ├── test_alerter.py
│   ├── test_api_client.py
│   ├── test_db.py
│   ├── test_pipeline.py
│   └── test_validator.py
├── .env.example                  # Template for environment variables
├── .gitignore
├── Dockerfile                    # Multi-stage build (Python 3.12-slim)
├── docker-compose.yml            # Local dev: daily + backfill services
├── entrypoint.sh                 # Selects daily/backfill via PIPELINE_MODE
├── requirements.txt              # psycopg2-binary, requests, PyYAML, python-dotenv
└── PIPELINE_LOGIC.md             # Detailed validation logic documentation
```

## Quick Start

### Prerequisites

- Python 3.12+
- PostgreSQL 14+
- EODHD API key
- (Optional) Slack webhook URL for alerts

### Local Development

1. **Clone and install dependencies:**

   ```bash
   git clone https://github.com/luqmaan-cmd/commodities-eodhd-pipeline.git
   cd commodities-eodhd-pipeline
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. **Configure environment:**

   ```bash
   cp .env.example .env
   # Edit .env with your actual values:
   #   EODHD_API_KEY=your_key
   #   DB_URL=postgresql://user:pass@host:5432/dbname
   #   SLACK_WEBHOOK_URL=https://hooks.slack.com/... (optional)
   ```

3. **Create the database tables:**

   ```bash
   psql -h <host> -U <user> -d <dbname> -f sql/create_table.sql
   ```

4. **Run the pipeline:**

   ```bash
   # Daily ingestion (yesterday's data)
   python jobs/daily.py

   # Daily ingestion for a specific date
   python jobs/daily.py 2025-01-15

   # Full historical backfill
   python jobs/backfill.py

   # Backfill a single commodity
   python jobs/backfill.py --symbol GC
   ```

### Docker

```bash
# Build
docker build -t eodhd-commodities .

# Daily run
docker compose run --rm daily

# Backfill
docker compose run --rm backfill

# Backfill a single symbol
docker compose run --rm -e PIPELINE_ARGS="--symbol GC" backfill
```

## GCP Deployment

### Infrastructure

| Resource | Details |
|----------|---------|
| **Artifact Registry** | `europe-west2-docker.pkg.dev/<PROJECT>/commodities-pipeline/eodhd-commodities` |
| **Cloud Run Jobs** | `commodities-daily` (10 min timeout, 1 retry), `commodities-backfill` (30 min timeout, 0 retries) |
| **Cloud Scheduler** | `commodities-daily-scheduler` — `0 6 * * *` Europe/London |
| **VPC Connector** | `bls-connector` — routes private-range traffic to VPC |
| **Secret Manager** | Stores API key, DB credentials, Slack webhook, GCP SA key |

### Deploy Steps

1. **Build and push the Docker image:**

   ```bash
   docker build --platform linux/amd64 -t europe-west2-docker.pkg.dev/<PROJECT>/commodities-pipeline/eodhd-commodities:v1.0.5 .
   docker push europe-west2-docker.pkg.dev/<PROJECT>/commodities-pipeline/eodhd-commodities:v1.0.5
   ```

2. **Create Secret Manager secrets** (one-time setup):

   ```bash
   printf '%s' 'your_eodhd_api_key' | gcloud secrets create eodhd-api-token --data-file=-
   printf '%s' '10.x.x.x'            | gcloud secrets create db-host --data-file=-
   printf '%s' '5432'                | gcloud secrets create db-port --data-file=-
   printf '%s' 'your_db_name'         | gcloud secrets create db-name --data-file=-
   printf '%s' 'your_db_user'         | gcloud secrets create db-user --data-file=-
   printf '%s' 'your_db_password'     | gcloud secrets create db-password --data-file=-
   printf '%s' 'https://hooks.slack.com/services/...' | gcloud secrets create slack-webhook-url --data-file=-
   ```

3. **Create Cloud Run Jobs:**

   ```bash
   # Daily job
   gcloud run jobs create commodities-daily \
     --region=europe-west2 \
     --image=europe-west2-docker.pkg.dev/<PROJECT>/commodities-pipeline/eodhd-commodities:v1.0.5 \
     --task-timeout=10m \
     --max-retries=1 \
     --memory=512Mi \
     --cpu=1 \
     --set-env-vars="PIPELINE_MODE=daily,GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/gcp-key.json" \
     --set-secrets="EODHD_API_TOKEN=eodhd-api-token:latest,DB_HOST=db-host:latest,DB_PORT=db-port:latest,DB_NAME=db-name:latest,DB_USER=db-user:latest,DB_PASSWORD=db-password:latest,SLACK_WEBHOOK_URL=slack-webhook-url:latest" \
     --add-volume=name=gcp-sa-key,type=secret,secret=gcp-sa-key \
     --vpc-connector=bls-connector \
     --vpc-egress=private-ranges-only \
     --service-account=<SA_EMAIL>

   # Backfill job
   gcloud run jobs create commodities-backfill \
     --region=europe-west2 \
     --image=europe-west2-docker.pkg.dev/<PROJECT>/commodities-pipeline/eodhd-commodities:v1.0.5 \
     --task-timeout=30m \
     --max-retries=0 \
     --memory=512Mi \
     --cpu=1 \
     --set-env-vars="PIPELINE_MODE=backfill,GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/gcp-key.json" \
     --set-secrets="EODHD_API_TOKEN=eodhd-api-token:latest,DB_HOST=db-host:latest,DB_PORT=db-port:latest,DB_NAME=db-name:latest,DB_USER=db-user:latest,DB_PASSWORD=db-password:latest,SLACK_WEBHOOK_URL=slack-webhook-url:latest" \
     --add-volume=name=gcp-sa-key,type=secret,secret=gcp-sa-key \
     --vpc-connector=bls-connector \
     --vpc-egress=private-ranges-only \
     --service-account=<SA_EMAIL>
   ```

4. **Set up Cloud Scheduler:**

   ```bash
   gcloud scheduler jobs create http commodities-daily-scheduler \
     --location=europe-west2 \
     --schedule="0 6 * * *" \
     --time-zone="Europe/London" \
     --uri="https://europe-west2-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/<PROJECT>/jobs/commodities-daily:run" \
     --http-method=POST \
     --oauth-service-account-email=<SA_EMAIL>
   ```

### Updating an Existing Job

> **Important:** `gcloud run jobs update` with `--set-secrets` or `--set-env-vars` **replaces** the entire list, not appends. To preserve all existing secrets and volume mounts, either:
>
> - Use `gcloud run jobs update` with **all** flags included, or
> - Export the full spec with `gcloud run jobs describe --format=json`, modify it, and use `gcloud run jobs replace`

To update just the image:

```bash
gcloud run jobs update commodities-daily \
  --region=europe-west2 \
  --image=europe-west2-docker.pkg.dev/<PROJECT>/commodities-pipeline/eodhd-commodities:v1.0.5
```

To add a VPC connector without losing other config:

```bash
gcloud run jobs update commodities-daily \
  --region=europe-west2 \
  --vpc-connector=bls-connector \
  --vpc-egress=private-ranges-only
```

## Validation

### Per-Row Checks (before upsert)

| # | Check | Action |
|---|-------|--------|
| 1 | Null open/high/low/close | **Reject** |
| 2 | High < Low | **Reject** |
| 3 | High < Open or Close | **Accept** with warning |
| 4 | Low > Open or Close | **Accept** with warning |
| 5 | Negative volume | **Reject** |
| 6 | Price anomaly (>30% daily change) | **Accept** with warning |

### Post-Ingestion Checks (after all upserts)

| # | Check | Alert Level |
|---|-------|-------------|
| 7 | Row count != expected | Warning |
| 8 | Data freshness (latest date < target) | Warning |
| 9 | Missing symbols for target date | Warning |
| 10 | Null price fields in inserted rows | Warning |

### Schema Validation

Raw API responses are validated before standardization:
- Must be a dict
- Must contain required fields: `date`, `open`, `high`, `low`, `close`
- Fields must have expected types (date=str, prices=numeric)

Invalid schema rows are logged and skipped (they would crash standardization anyway).

## Slack Alerts

| Alert | Severity | Trigger |
|-------|----------|---------|
| Pipeline failure | Critical | Any commodity fails during processing |
| Zero data on trading day | Warning | Zero rows upserted on a weekday |
| Validation failures | Warning | Post-ingestion checks find issues |
| Success summary | Info | Always fires at end of daily run |

## Database Schema

### `commodities_eod` (main table)

| Column | Type | Notes |
|--------|------|-------|
| date | DATE | PK (with symbol) |
| symbol | VARCHAR(20) | PK (with date) |
| name | VARCHAR(100) | |
| open | NUMERIC | |
| high | NUMERIC | |
| low | NUMERIC | |
| close | NUMERIC | |
| adjusted_close | NUMERIC | |
| volume | BIGINT | |
| ingestion_ts | TIMESTAMP | Default NOW() |

### `commodities_eod_rejected` (audit table)

Same columns as above, plus `rejected_reason` (TEXT) and `rejected_at` (TIMESTAMP), with an auto-incrementing `id` as PK.

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run a specific test module
python -m pytest tests/test_validator.py -v
```

81 tests covering all modules: validator, API client, database, pipeline, and alerter.

## Configuration

Commodities are defined in `config/commodities.yaml`. Each entry specifies:

```yaml
- symbol: GC              # Internal symbol (used as DB key)
  name: Gold (COMEX)      # Human-readable name
  api_code: GC.COMM        # EODHD API code
  category: Precious Metals # Category grouping
  anomaly_threshold: 0.30  # Optional: fractional threshold for anomaly detection (default 0.30)
```

To add a new commodity, simply add an entry to the YAML file. The pipeline will pick it up on the next run.

## Key Design Decisions

- **Internal IP + VPC Connector**: Cloud Run connects to PostgreSQL via the VPC connector (`bls-connector`) using the DB's internal IP. This keeps database traffic off the public internet.
- **Thread-safe DB connections**: Each worker thread creates its own `Database` instance (psycopg2 connections are not thread-safe).
- **Backfill optimization**: Uses in-memory `prev_close` tracking instead of per-row DB queries, eliminating ~5,000 round-trips per commodity.
- **Idempotent upserts**: `ON CONFLICT (date, symbol) DO UPDATE` means re-running for the same date is safe.
- **Secrets via Secret Manager**: All sensitive values (API key, DB credentials, Slack webhook) are stored in GCP Secret Manager and mounted as env vars at runtime. The GCP SA key is mounted as a volume.
