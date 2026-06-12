"""Pipeline orchestrator — backfill and daily modes."""

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import yaml

from src.alerter import Alerter
from src.api_client import EODHDClient
from src.db import Database
from src.logger import get_logger, log_commodity_result, log_run_summary
from src.models import CommodityConfig, CommodityRunResult, PipelineRunSummary, RejectedRow
from src.validator import validate_row, validate_post_ingestion

logger = get_logger(__name__)

# Default path to the commodities config
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "commodities.yaml"

# Default number of parallel workers for ThreadPoolExecutor
DEFAULT_MAX_WORKERS = 6


def is_trading_day(d: date) -> bool:
    """Check whether a date is a trading day (Mon–Fri).

    Commodity markets are closed on weekends. This is a simple heuristic —
    it does not account for public holidays (e.g. Christmas, Thanksgiving),
    but those are rare enough that a "zero data on trading day" alert on a
    holiday is acceptable (it will be a false positive ~4–5 times per year).

    Args:
        d: The date to check.

    Returns:
        True if the date is Monday–Friday.
    """
    return d.weekday() < 5


def load_commodities(config_path: Optional[Path] = None) -> list[CommodityConfig]:
    """Load the commodity list from the YAML config file.

    Args:
        config_path: Path to commodities.yaml. Defaults to config/commodities.yaml.

    Returns:
        List of CommodityConfig objects.
    """
    path = config_path or DEFAULT_CONFIG_PATH
    with open(path, "r") as f:
        data = yaml.safe_load(f)

    commodities = []
    for entry in data.get("commodities", []):
        commodities.append(CommodityConfig(
            symbol=entry["symbol"],
            name=entry["name"],
            api_code=entry["api_code"],
            category=entry["category"],
            anomaly_threshold=entry.get("anomaly_threshold", 0.30),
        ))

    logger.info(f"Loaded {len(commodities)} commodities from {path}")
    return commodities


def _process_commodity_backfill(
    commodity: CommodityConfig,
    client: EODHDClient,
    db_url: str,
) -> CommodityRunResult:
    """Fetch, validate, and upsert data for a single commodity (backfill mode).

    Optimised for backfill:
    - Uses in-memory prev_close tracking instead of per-row DB queries.
      This eliminates ~5,000 DB round-trips per commodity.
    - Passes skip_delay=True to the API client (we use 0.021% of the
      200k/day rate limit, so the inter-request delay is unnecessary).
    - Creates its own Database connection (thread-safe for parallel workers).

    Args:
        commodity: The commodity to process.
        client: EODHD API client (shared across workers — thread-safe).
        db_url: PostgreSQL connection string (each worker creates its own conn).

    Returns:
        CommodityRunResult with stats.
    """
    result = CommodityRunResult(symbol=commodity.symbol, status="success")
    db = Database.create(db_url)

    try:
        # ── Fetch ─────────────────────────────────────────────────────────
        start_time = time.time()
        prices = client.fetch_eod(commodity, skip_delay=True)
        elapsed_ms = int((time.time() - start_time) * 1000)

        result.rows_fetched = len(prices)
        result.api_response_time_ms = elapsed_ms

        if not prices:
            logger.warning(f"No data returned for {commodity.symbol}")
            result.status = "no_data"
            return result

        # ── Sort by date ascending for in-memory prev_close tracking ──────
        prices.sort(key=lambda p: p.date)

        # ── Validate ──────────────────────────────────────────────────────
        valid_rows = []
        rejected_rows: list[RejectedRow] = []
        all_warnings: list[str] = []

        # For the very first row, query the DB for the previous close
        # (data that may already exist from a prior run or earlier commodity).
        # For all subsequent rows, we use the in-memory chain.
        prev_close: Optional[float] = db.get_previous_close(
            commodity.symbol, prices[0].date.isoformat()
        )

        for price in prices:
            validation = validate_row(
                price, prev_close=prev_close,
                anomaly_threshold=commodity.anomaly_threshold,
            )
            if validation.is_valid:
                valid_rows.append(price)
                if validation.warnings:
                    all_warnings.extend(validation.warnings)
                # Update in-memory prev_close for the next row
                prev_close = price.close
            else:
                result.rows_rejected += 1
                logger.warning(
                    f"Rejected row for {commodity.symbol} on {price.date}: "
                    f"{validation.rejected_reason}"
                )
                rejected_rows.append(RejectedRow(
                    date=price.date,
                    symbol=price.symbol,
                    name=price.name,
                    open=price.open,
                    high=price.high,
                    low=price.low,
                    close=price.close,
                    adjusted_close=price.adjusted_close,
                    volume=price.volume,
                    rejected_reason=validation.rejected_reason or "unknown",
                ))
                # Even rejected rows update prev_close if they have a close
                # value, so anomaly detection stays accurate for subsequent rows.
                if price.close is not None:
                    prev_close = price.close

        result.rows_valid = len(valid_rows)
        result.validation_warnings = all_warnings

        # ── Store rejected rows ──────────────────────────────────────────
        if rejected_rows:
            try:
                db.insert_rejected_rows(rejected_rows)
            except Exception as exc:
                logger.error(
                    f"Failed to insert rejected rows for {commodity.symbol}: {exc}"
                )

        # ── Upsert ────────────────────────────────────────────────────────
        if valid_rows:
            upserted = db.upsert_rows(valid_rows)
            result.rows_upserted = upserted

    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        logger.error(f"Failed processing {commodity.symbol}: {exc}")

    finally:
        db.close()

    return result


def _process_commodity_daily(
    commodity: CommodityConfig,
    client: EODHDClient,
    db_url: str,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
) -> CommodityRunResult:
    """Fetch, validate, and upsert data for a single commodity (daily mode).

    Uses DB-based prev_close lookup (only 1 row per commodity, so the
    per-row DB query overhead is negligible for daily runs).

    Creates its own Database connection (thread-safe for parallel workers).

    Args:
        commodity: The commodity to process.
        client: EODHD API client (shared across workers — thread-safe).
        db_url: PostgreSQL connection string (each worker creates its own conn).
        from_date: Optional start date.
        to_date: Optional end date.

    Returns:
        CommodityRunResult with stats.
    """
    result = CommodityRunResult(symbol=commodity.symbol, status="success")
    db = Database.create(db_url)

    try:
        # ── Fetch ─────────────────────────────────────────────────────────
        start_time = time.time()
        prices = client.fetch_eod(commodity, from_date=from_date, to_date=to_date)
        elapsed_ms = int((time.time() - start_time) * 1000)

        result.rows_fetched = len(prices)
        result.api_response_time_ms = elapsed_ms

        if not prices:
            logger.warning(f"No data returned for {commodity.symbol}")
            result.status = "no_data"
            return result

        # ── Validate ──────────────────────────────────────────────────────
        valid_rows = []
        rejected_rows: list[RejectedRow] = []
        all_warnings: list[str] = []

        for price in prices:
            # Daily mode: only 1 row per commodity, so DB lookup is fine
            prev_close = db.get_previous_close(commodity.symbol, price.date.isoformat())

            validation = validate_row(
                price, prev_close=prev_close,
                anomaly_threshold=commodity.anomaly_threshold,
            )
            if validation.is_valid:
                valid_rows.append(price)
                if validation.warnings:
                    all_warnings.extend(validation.warnings)
            else:
                result.rows_rejected += 1
                logger.warning(
                    f"Rejected row for {commodity.symbol} on {price.date}: "
                    f"{validation.rejected_reason}"
                )
                rejected_rows.append(RejectedRow(
                    date=price.date,
                    symbol=price.symbol,
                    name=price.name,
                    open=price.open,
                    high=price.high,
                    low=price.low,
                    close=price.close,
                    adjusted_close=price.adjusted_close,
                    volume=price.volume,
                    rejected_reason=validation.rejected_reason or "unknown",
                ))

        result.rows_valid = len(valid_rows)
        result.validation_warnings = all_warnings

        # ── Store rejected rows ──────────────────────────────────────────
        if rejected_rows:
            try:
                db.insert_rejected_rows(rejected_rows)
            except Exception as exc:
                logger.error(
                    f"Failed to insert rejected rows for {commodity.symbol}: {exc}"
                )

        # ── Upsert ────────────────────────────────────────────────────────
        if valid_rows:
            upserted = db.upsert_rows(valid_rows)
            result.rows_upserted = upserted

    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        logger.error(f"Failed processing {commodity.symbol}: {exc}")

    finally:
        db.close()

    return result


def run_backfill(
    api_key: str,
    db_url: str,
    slack_webhook_url: Optional[str] = None,
    config_path: Optional[Path] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
    symbol_filter: Optional[str] = None,
) -> PipelineRunSummary:
    """Run the one-time backfill for all commodities.

    Uses ThreadPoolExecutor for parallel API fetches + DB upserts.
    Each worker gets its own Database connection (psycopg2 is not thread-safe).

    Args:
        api_key: EODHD API key.
        db_url: PostgreSQL connection string.
        slack_webhook_url: Optional Slack webhook for alerts.
        config_path: Path to commodities.yaml.
        max_workers: Number of parallel workers (default 6).
        symbol_filter: If set, only process this single symbol (for test runs).

    Returns:
        PipelineRunSummary with run statistics.
    """
    start_time = time.time()
    commodities = load_commodities(config_path)

    # Apply symbol filter for test runs
    if symbol_filter:
        commodities = [c for c in commodities if c.symbol == symbol_filter]
        if not commodities:
            logger.error(f"Symbol '{symbol_filter}' not found in config")
            return PipelineRunSummary(
                pipeline_type="backfill",
                total_symbols=0,
                failed=1,
                failed_symbols=[symbol_filter],
            )
        logger.info(f"Filtered to single symbol: {symbol_filter}")

    client = EODHDClient(api_key)
    alerter = Alerter(slack_webhook_url)

    summary = PipelineRunSummary(
        pipeline_type="backfill",
        total_symbols=len(commodities),
    )

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all commodities as parallel tasks
            future_to_symbol = {
                executor.submit(
                    _process_commodity_backfill, commodity, client, db_url
                ): commodity.symbol
                for commodity in commodities
            }

            # Collect results as they complete
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    result = future.result()
                except Exception as exc:
                    # Shouldn't happen since _process_commodity_backfill
                    # catches its own exceptions, but safety net
                    result = CommodityRunResult(
                        symbol=symbol, status="failed", error=str(exc)
                    )

                log_commodity_result(logger, "backfill", None, result)

                if result.status == "success":
                    summary.successful += 1
                    summary.successful_symbols.append(symbol)
                elif result.status == "no_data":
                    summary.no_data += 1
                    summary.no_data_symbols.append(symbol)
                else:
                    summary.failed += 1
                    summary.failed_symbols.append(symbol)
                    alerter.alert_api_errors(symbol, result.error or "unknown error")

                summary.total_rows_upserted += result.rows_upserted
                summary.total_rows_rejected += result.rows_rejected

    finally:
        client.close()

    summary.run_duration_seconds = time.time() - start_time
    log_run_summary(logger, summary)

    if summary.failed > 0:
        alerter.alert_pipeline_failure(
            f"Backfill completed with {summary.failed} failures: {summary.failed_symbols}"
        )

    return summary


def run_daily(
    api_key: str,
    db_url: str,
    target_date: Optional[date] = None,
    slack_webhook_url: Optional[str] = None,
    config_path: Optional[Path] = None,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> PipelineRunSummary:
    """Run the daily ingestion for all commodities.

    Uses ThreadPoolExecutor for parallel API fetches + DB upserts.
    Each worker gets its own Database connection (psycopg2 is not thread-safe).

    Args:
        api_key: EODHD API key.
        db_url: PostgreSQL connection string.
        target_date: The date to fetch data for. Defaults to yesterday.
        slack_webhook_url: Optional Slack webhook for alerts.
        config_path: Path to commodities.yaml.
        max_workers: Number of parallel workers (default 6).

    Returns:
        PipelineRunSummary with run statistics.
    """
    start_time = time.time()

    if target_date is None:
        target_date = date.today() - timedelta(days=1)

    target_date_str = target_date.isoformat()

    commodities = load_commodities(config_path)
    client = EODHDClient(api_key)
    alerter = Alerter(slack_webhook_url)

    # We need a shared DB connection for post-ingestion validation
    # (which runs after all workers complete)
    shared_db = Database(db_url)
    shared_db.connect()

    summary = PipelineRunSummary(
        pipeline_type="daily",
        target_date=target_date_str,
        total_symbols=len(commodities),
    )

    try:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all commodities as parallel tasks
            future_to_symbol = {
                executor.submit(
                    _process_commodity_daily,
                    commodity, client, db_url,
                    from_date=target_date, to_date=target_date,
                ): commodity.symbol
                for commodity in commodities
            }

            # Collect results as they complete
            for future in as_completed(future_to_symbol):
                symbol = future_to_symbol[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = CommodityRunResult(
                        symbol=symbol, status="failed", error=str(exc)
                    )

                log_commodity_result(logger, "daily", target_date_str, result)

                if result.status == "success":
                    summary.successful += 1
                    summary.successful_symbols.append(symbol)
                elif result.status == "no_data":
                    summary.no_data += 1
                    summary.no_data_symbols.append(symbol)
                else:
                    summary.failed += 1
                    summary.failed_symbols.append(symbol)
                    alerter.alert_api_errors(symbol, result.error or "unknown error")

                summary.total_rows_upserted += result.rows_upserted
                summary.total_rows_rejected += result.rows_rejected

        # ── Post-ingestion validation (sequential, after all workers done) ──
        expected_symbols = {c.symbol for c in commodities}
        alerts = validate_post_ingestion(
            shared_db.connection, target_date_str, expected_symbols,
            known_no_data_symbols=set(summary.no_data_symbols),
        )

        if alerts:
            alerter.alert_validation_failures(alerts)

        # ── Check 8: Data freshness ───────────────────────────────────────
        latest = shared_db.get_latest_date()
        if latest and latest < target_date_str:
            alerter.alert_stale_data(latest, target_date_str)

    finally:
        client.close()
        shared_db.close()

    summary.run_duration_seconds = time.time() - start_time
    log_run_summary(logger, summary)

    # ── Alert: Pipeline failure (critical) ────────────────────────────────
    if summary.failed > 0:
        alerter.alert_pipeline_failure(
            f"Daily run for {target_date_str} completed with "
            f"{summary.failed} failures: {summary.failed_symbols}"
        )

    # ── Alert: Zero data ingested on a trading day (silent failure) ───────
    if summary.total_rows_upserted == 0 and is_trading_day(target_date):
        alerter.alert_zero_data_ingested(target_date_str)

    # ── Alert: Success summary (always fires) ─────────────────────────────
    alerter.alert_success_summary(
        target_date=target_date_str,
        total_symbols=summary.total_symbols,
        successful=summary.successful,
        no_data=summary.no_data,
        no_data_symbols=summary.no_data_symbols,
        failed=summary.failed,
        failed_symbols=summary.failed_symbols,
        total_rows_upserted=summary.total_rows_upserted,
        total_rows_rejected=summary.total_rows_rejected,
        run_duration_seconds=summary.run_duration_seconds,
    )

    return summary
