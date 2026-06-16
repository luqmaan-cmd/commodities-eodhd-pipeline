"""Data models for the EODHD Commodities Pipeline."""

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional


@dataclass
class CommodityConfig:
    """Represents a single commodity from the YAML config."""

    symbol: str
    name: str
    api_code: str
    category: str
    anomaly_threshold: float = 0.30  # Fractional threshold for price anomaly warnings


@dataclass
class CommodityPrice:
    """A single standardized price row ready for database insertion."""

    date: date
    symbol: str
    name: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    adjusted_close: Optional[float] = None
    volume: Optional[int] = None
    ingestion_ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def as_tuple(self) -> tuple:
        """Return values as a tuple for database insertion."""
        return (
            self.date,
            self.symbol,
            self.name,
            self.open,
            self.high,
            self.low,
            self.close,
            self.adjusted_close,
            self.volume,
            self.ingestion_ts,
        )


@dataclass
class ValidationResult:
    """Result of validating a single price row."""

    is_valid: bool
    warnings: list[str] = field(default_factory=list)
    rejected_reason: Optional[str] = None


@dataclass
class RejectedRow:
    """A price row that failed validation, stored for audit/reprocessing."""

    date: date
    symbol: str
    name: str
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = None
    adjusted_close: Optional[float] = None
    volume: Optional[int] = None
    rejected_reason: str = ""

    def as_tuple(self) -> tuple:
        """Return values as a tuple for database insertion."""
        return (
            self.date,
            self.symbol,
            self.name,
            self.open,
            self.high,
            self.low,
            self.close,
            self.adjusted_close,
            self.volume,
            self.rejected_reason,
        )


@dataclass
class CommodityRunResult:
    """Result of processing a single commodity in a pipeline run."""

    symbol: str
    status: str  # "success", "no_data", or "failed"
    rows_fetched: int = 0
    rows_valid: int = 0
    rows_rejected: int = 0
    rows_upserted: int = 0
    rows_backfilled: int = 0
    api_response_time_ms: Optional[int] = None
    validation_warnings: list[str] = field(default_factory=list)
    error: Optional[str] = None


@dataclass
class PipelineRunSummary:
    """Summary of an entire pipeline run (backfill or daily)."""

    pipeline_type: str  # "backfill" or "daily"
    target_date: Optional[str] = None
    total_symbols: int = 0
    successful: int = 0
    no_data: int = 0
    failed: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    no_data_symbols: list[str] = field(default_factory=list)
    successful_symbols: list[str] = field(default_factory=list)
    total_rows_upserted: int = 0
    total_rows_backfilled: int = 0
    total_rows_rejected: int = 0
    run_duration_seconds: float = 0.0
