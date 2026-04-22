"""Structured JSON logging for the EODHD Commodities Pipeline."""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Optional


class StructuredFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }

        # Merge any extra fields passed via the `extra` kwarg
        if hasattr(record, "extra_fields"):
            log_entry.update(record.extra_fields)

        # Include exception info if present
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry, default=str)


def get_logger(name: str = "eodhd_commodities") -> logging.Logger:
    """Return a logger that outputs structured JSON to stdout.

    Safe to call multiple times — handlers are only added once.
    """
    logger = logging.getLogger(name)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(StructuredFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)

    return logger


def log_commodity_result(
    logger: logging.Logger,
    pipeline_type: str,
    target_date: Optional[str],
    result: Any,  # CommodityRunResult — imported at runtime to avoid circular imports
) -> None:
    """Log a per-commodity result as structured JSON."""
    logger.info(
        f"Commodity result: {result.symbol}",
        extra={
            "extra_fields": {
                "pipeline_type": pipeline_type,
                "target_date": target_date,
                "symbol": result.symbol,
                "status": result.status,
                "rows_fetched": result.rows_fetched,
                "rows_valid": result.rows_valid,
                "rows_rejected": result.rows_rejected,
                "api_response_time_ms": result.api_response_time_ms,
                "validation_warnings": result.validation_warnings,
                "error": result.error,
            }
        },
    )


def log_run_summary(
    logger: logging.Logger,
    summary: Any,  # PipelineRunSummary
) -> None:
    """Log a pipeline run summary as structured JSON."""
    logger.info(
        f"Pipeline run complete: {summary.pipeline_type}",
        extra={
            "extra_fields": {
                "pipeline_type": summary.pipeline_type,
                "target_date": summary.target_date,
                "total_symbols": summary.total_symbols,
                "successful": summary.successful,
                "failed": summary.failed,
                "failed_symbols": summary.failed_symbols,
                "total_rows_upserted": summary.total_rows_upserted,
                "total_rows_rejected": summary.total_rows_rejected,
                "run_duration_seconds": round(summary.run_duration_seconds, 2),
            }
        },
    )
