"""Daily ingestion entry point — fetches yesterday's commodity data."""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from src.pipeline import run_daily


def _build_db_url() -> str:
    """Construct DB_URL from individual components if not already set.

    Supports two patterns:
      1. DB_URL set directly (local dev with .env)
      2. Individual DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD vars
         (Cloud Run with Secret Manager)
    """
    db_url = os.environ.get("DB_URL")
    if db_url:
        return db_url

    # Build from individual components (Cloud Run pattern)
    host = os.environ.get("DB_HOST")
    port = os.environ.get("DB_PORT", "5432")
    name = os.environ.get("DB_NAME")
    user = os.environ.get("DB_USER")
    password = os.environ.get("DB_PASSWORD")

    if not all([host, name, user, password]):
        missing = [k for k, v in [
            ("DB_HOST", host), ("DB_NAME", name),
            ("DB_USER", user), ("DB_PASSWORD", password)
        ] if not v]
        print(f"ERROR: DB_URL not set and missing DB components: {missing}",
              file=sys.stderr)
        sys.exit(1)

    from urllib.parse import quote_plus
    return (
        f"postgresql://{user}:{quote_plus(password)}"
        f"@{host}:{port}/{name}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="EODHD Commodities Pipeline — Daily Ingestion"
    )
    parser.add_argument(
        "target_date",
        nargs="?",
        default=None,
        type=lambda s: date.fromisoformat(s),
        help="Target date in YYYY-MM-DD format (default: yesterday).",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="Number of parallel workers (default: 6).",
    )
    return parser.parse_args()


def main() -> None:
    # Load .env from project root if it exists
    env_path = Path(__file__).resolve().parent.parent / ".env"
    load_dotenv(env_path)

    # Support both EODHD_API_KEY (local) and EODHD_API_TOKEN (Cloud Run / Secret Manager)
    api_key = os.environ.get("EODHD_API_KEY") or os.environ.get("EODHD_API_TOKEN")
    db_url = _build_db_url()
    slack_webhook = os.environ.get("SLACK_WEBHOOK_URL")

    if not api_key:
        print("ERROR: EODHD_API_KEY or EODHD_API_TOKEN environment variable is required", file=sys.stderr)
        sys.exit(1)

    args = parse_args()

    summary = run_daily(
        api_key=api_key,
        db_url=db_url,
        target_date=args.target_date,
        slack_webhook_url=slack_webhook,
        max_workers=args.workers,
    )

    # Exit with non-zero if any commodities failed
    if summary.failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
