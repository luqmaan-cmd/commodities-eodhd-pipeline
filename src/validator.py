"""Data validation for the EODHD Commodities Pipeline.

Implements the 6 per-row validation checks defined in PIPELINE_LOGIC.md:
  1. Null prices        — reject if open/high/low/close is null
  2. High >= Low        — reject if violated
  3. High >= Open/Close — accept with warning
  4. Low <= Open/Close  — accept with warning
  5. Volume >= 0        — reject if negative
  6. Price anomaly      — accept with warning if daily % change > 30%

Also provides schema validation for raw API responses before standardization.
"""

from typing import Optional

from src.models import CommodityPrice, ValidationResult


# ── Schema validation (raw API response) ─────────────────────────────────────

# Required fields that must be present in every API response row
REQUIRED_FIELDS = {"date", "open", "high", "low", "close"}

# Expected types for fields that we type-check (date is str, prices are int/float)
EXPECTED_TYPES = {
    "date": str,
    "open": (int, float),
    "high": (int, float),
    "low": (int, float),
    "close": (int, float),
}


def validate_schema(raw: dict) -> tuple[bool, Optional[str]]:
    """Validate the schema of a raw API response row before standardization.

    Checks that:
    - The input is a dict
    - All required fields are present
    - Fields have the expected types (date=str, open/high/low/close=numeric)

    Args:
        raw: A single row from the EODHD API JSON response.

    Returns:
        Tuple of (is_valid, error_message). If is_valid is True, error_message
        is None. If is_valid is False, error_message describes the problem.
    """
    if not isinstance(raw, dict):
        return False, f"Expected dict, got {type(raw).__name__}"

    # Check for missing required fields
    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        return False, f"Missing required field(s): {sorted(missing)}"

    # Check types of key fields
    for field_name, expected_type in EXPECTED_TYPES.items():
        value = raw.get(field_name)
        if value is not None and not isinstance(value, expected_type):
            return False, (
                f"Field '{field_name}' has wrong type: "
                f"expected {expected_type}, got {type(value).__name__}"
            )

    return True, None


def validate_row(
    row: CommodityPrice,
    prev_close: Optional[float] = None,
    anomaly_threshold: float = 0.30,
) -> ValidationResult:
    """Validate a single CommodityPrice row.

    Args:
        row: The price row to validate.
        prev_close: Previous day's close price (for anomaly detection).
                    None means no comparison is possible (first row).
        anomaly_threshold: Fractional threshold for flagging price anomalies
                           (default 0.30 = 30%).

    Returns:
        ValidationResult with is_valid, warnings list, and optional rejected_reason.
    """
    warnings: list[str] = []

    # ── Check 1: Null prices ──────────────────────────────────────────────
    if any(v is None for v in (row.open, row.high, row.low, row.close)):
        return ValidationResult(
            is_valid=False,
            warnings=warnings,
            rejected_reason="Null price field(s): "
            f"open={row.open}, high={row.high}, low={row.low}, close={row.close}",
        )

    # After check 1, we know open/high/low/close are all not-None
    open_price: float = row.open  # type: ignore[assignment]
    high_price: float = row.high  # type: ignore[assignment]
    low_price: float = row.low    # type: ignore[assignment]
    close_price: float = row.close  # type: ignore[assignment]

    # ── Check 2: High >= Low ──────────────────────────────────────────────
    if high_price < low_price:
        return ValidationResult(
            is_valid=False,
            warnings=warnings,
            rejected_reason=f"High ({high_price}) < Low ({low_price})",
        )

    # ── Check 3: High >= Open and High >= Close ──────────────────────────
    if high_price < open_price:
        warnings.append(f"High ({high_price}) < Open ({open_price})")
    if high_price < close_price:
        warnings.append(f"High ({high_price}) < Close ({close_price})")

    # ── Check 4: Low <= Open and Low <= Close ─────────────────────────────
    if low_price > open_price:
        warnings.append(f"Low ({low_price}) > Open ({open_price})")
    if low_price > close_price:
        warnings.append(f"Low ({low_price}) > Close ({close_price})")

    # ── Check 5: Volume >= 0 ──────────────────────────────────────────────
    if row.volume is not None and row.volume < 0:
        return ValidationResult(
            is_valid=False,
            warnings=warnings,
            rejected_reason=f"Negative volume: {row.volume}",
        )

    # ── Check 6: Price anomaly (> 30% daily change) ──────────────────────
    if prev_close is not None and prev_close != 0:
        pct_change = abs(close_price - prev_close) / prev_close
        if pct_change > anomaly_threshold:
            warnings.append(
                f"Price anomaly: {pct_change:.1%} change from "
                f"prev close {prev_close} to close {close_price}"
            )

    return ValidationResult(
        is_valid=True,
        warnings=warnings,
        rejected_reason=None,
    )


def validate_post_ingestion(
    db_connection,
    target_date: str,
    expected_symbols: set[str],
) -> list[str]:
    """Run post-ingestion validation checks (checks 7-10 from PIPELINE_LOGIC.md).

    Args:
        db_connection: An open psycopg2 connection.
        target_date: The date that was ingested (YYYY-MM-DD).
        expected_symbols: Set of all 42 commodity symbols.

    Returns:
        List of alert messages (empty if everything is fine).
    """
    alerts: list[str] = []
    cursor = db_connection.cursor()

    # ── Check 7: Row count for target date ────────────────────────────────
    cursor.execute(
        "SELECT COUNT(*) FROM commodities_eod WHERE date = %s",
        (target_date,),
    )
    row_count = cursor.fetchone()[0]
    if row_count != len(expected_symbols):
        alerts.append(
            f"Row count for {target_date}: expected {len(expected_symbols)}, "
            f"got {row_count}"
        )

    # ── Check 9: Missing symbols for target date ──────────────────────────
    cursor.execute(
        "SELECT symbol FROM commodities_eod WHERE date = %s",
        (target_date,),
    )
    found_symbols = {row[0] for row in cursor.fetchall()}
    missing = expected_symbols - found_symbols
    if missing:
        alerts.append(
            f"Missing symbols for {target_date}: {sorted(missing)}"
        )

    # ── Check 10: Null values in required fields ──────────────────────────
    cursor.execute(
        """
        SELECT symbol
        FROM commodities_eod
        WHERE date = %s
          AND (open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL)
        """,
        (target_date,),
    )
    null_rows = cursor.fetchall()
    if null_rows:
        null_symbols = [row[0] for row in null_rows]
        alerts.append(
            f"Null price fields for {target_date}: {null_symbols}"
        )

    cursor.close()
    return alerts
