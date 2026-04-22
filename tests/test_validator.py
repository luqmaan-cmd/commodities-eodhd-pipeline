"""Tests for src.validator — per-row and post-ingestion validation checks."""

import sys
from datetime import date
from unittest.mock import MagicMock

import pytest

# Ensure project root is on the path so `src.*` imports resolve
sys.path.insert(0, "/Users/luqmaan2000/EODHD - Commodities")

from src.models import CommodityPrice
from src.validator import validate_row, validate_post_ingestion, validate_schema


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_row(
    open_: float = 100.0,
    high: float = 105.0,
    low: float = 95.0,
    close: float = 102.0,
    volume: int = 1000,
) -> CommodityPrice:
    """Create a valid CommodityPrice row with sensible defaults."""
    return CommodityPrice(
        date=date(2026, 4, 21),
        symbol="GC",
        name="Gold (COMEX)",
        open=open_,
        high=high,
        low=low,
        close=close,
        adjusted_close=close,
        volume=volume,
    )


# ── Check 1: Null prices ─────────────────────────────────────────────────────

class TestNullPrices:
    def test_valid_row_passes(self):
        row = _make_row()
        result = validate_row(row)
        assert result.is_valid is True
        assert result.rejected_reason is None

    def test_null_open_rejected(self):
        row = _make_row()
        row.open = None
        result = validate_row(row)
        assert result.is_valid is False
        assert "Null price" in result.rejected_reason

    def test_null_high_rejected(self):
        row = _make_row()
        row.high = None
        result = validate_row(row)
        assert result.is_valid is False

    def test_null_low_rejected(self):
        row = _make_row()
        row.low = None
        result = validate_row(row)
        assert result.is_valid is False

    def test_null_close_rejected(self):
        row = _make_row()
        row.close = None
        result = validate_row(row)
        assert result.is_valid is False


# ── Check 2: High >= Low ─────────────────────────────────────────────────────

class TestHighLow:
    def test_high_less_than_low_rejected(self):
        row = _make_row(high=90.0, low=100.0)
        result = validate_row(row)
        assert result.is_valid is False
        assert "High" in result.rejected_reason and "Low" in result.rejected_reason

    def test_high_equals_low_valid(self):
        row = _make_row(high=100.0, low=100.0)
        result = validate_row(row)
        assert result.is_valid is True


# ── Check 3: High >= Open/Close ──────────────────────────────────────────────

class TestHighVsOpenClose:
    def test_high_below_open_warning(self):
        row = _make_row(high=99.0, open_=100.0, low=95.0, close=98.0)
        result = validate_row(row)
        assert result.is_valid is True
        assert any("High" in w and "Open" in w for w in result.warnings)

    def test_high_below_close_warning(self):
        row = _make_row(high=99.0, open_=95.0, low=95.0, close=100.0)
        result = validate_row(row)
        assert result.is_valid is True
        assert any("High" in w and "Close" in w for w in result.warnings)


# ── Check 4: Low <= Open/Close ───────────────────────────────────────────────

class TestLowVsOpenClose:
    def test_low_above_open_warning(self):
        row = _make_row(low=101.0, open_=100.0, high=110.0, close=102.0)
        result = validate_row(row)
        assert result.is_valid is True
        assert any("Low" in w and "Open" in w for w in result.warnings)

    def test_low_above_close_warning(self):
        row = _make_row(low=103.0, open_=95.0, high=110.0, close=102.0)
        result = validate_row(row)
        assert result.is_valid is True
        assert any("Low" in w and "Close" in w for w in result.warnings)


# ── Check 5: Volume >= 0 ─────────────────────────────────────────────────────

class TestVolume:
    def test_negative_volume_rejected(self):
        row = _make_row(volume=-1)
        result = validate_row(row)
        assert result.is_valid is False
        assert "Negative volume" in result.rejected_reason

    def test_zero_volume_valid(self):
        row = _make_row(volume=0)
        result = validate_row(row)
        assert result.is_valid is True

    def test_null_volume_valid(self):
        row = _make_row()
        row.volume = None
        result = validate_row(row)
        assert result.is_valid is True


# ── Check 6: Price anomaly ───────────────────────────────────────────────────

class TestPriceAnomaly:
    def test_large_price_change_warning(self):
        row = _make_row(close=150.0)
        result = validate_row(row, prev_close=100.0)
        assert result.is_valid is True
        assert any("anomaly" in w.lower() for w in result.warnings)

    def test_small_price_change_no_warning(self):
        row = _make_row(close=102.0)
        result = validate_row(row, prev_close=100.0)
        assert result.is_valid is True
        assert not any("anomaly" in w.lower() for w in result.warnings)

    def test_no_prev_close_no_anomaly_check(self):
        row = _make_row(close=500.0)
        result = validate_row(row, prev_close=None)
        assert result.is_valid is True
        assert not any("anomaly" in w.lower() for w in result.warnings)

    def test_custom_anomaly_threshold_higher(self):
        """A high anomaly_threshold should suppress warnings for large changes."""
        row = _make_row(close=150.0)
        # 50% change — default threshold (0.30) would flag it, but 1.0 should not
        result = validate_row(row, prev_close=100.0, anomaly_threshold=1.0)
        assert result.is_valid is True
        assert not any("anomaly" in w.lower() for w in result.warnings)

    def test_custom_anomaly_threshold_lower(self):
        """A low anomaly_threshold should flag even small changes."""
        row = _make_row(close=105.0)
        # 5% change — default threshold (0.30) would not flag it, but 0.03 should
        result = validate_row(row, prev_close=100.0, anomaly_threshold=0.03)
        assert result.is_valid is True
        assert any("anomaly" in w.lower() for w in result.warnings)


# ── Post-ingestion validation ────────────────────────────────────────────────

class TestPostIngestion:
    def test_all_good_no_alerts(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (2,)  # Check 7: row count
        # Check 9: found symbols; Check 10: no null rows (empty)
        mock_cursor.fetchall.side_effect = [
            [("GC",), ("CL",)],  # Check 9 — all symbols present
            [],                   # Check 10 — no null price rows
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        alerts = validate_post_ingestion(
            mock_conn,
            "2026-04-21",
            {"GC", "CL"},
        )
        assert alerts == []

    def test_missing_symbols_alert(self):
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)  # Check 7: row count != 2
        # Check 9: only GC found; Check 10: no null rows
        mock_cursor.fetchall.side_effect = [
            [("GC",)],  # Check 9 — CL missing
            [],          # Check 10 — no null price rows
        ]

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        alerts = validate_post_ingestion(
            mock_conn,
            "2026-04-21",
            {"GC", "CL"},
        )
        assert len(alerts) >= 1
        assert any("Missing" in a or "Row count" in a for a in alerts)


# ── Schema validation ────────────────────────────────────────────────────────

class TestValidateSchema:
    """Tests for validate_schema — raw API response schema checks."""

    def test_valid_row_passes(self):
        raw = {
            "date": "2026-04-21",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "adjusted_close": 102.0,
            "volume": 500,
        }
        is_valid, error = validate_schema(raw)
        assert is_valid is True
        assert error is None

    def test_missing_date_rejected(self):
        raw = {"open": 100.0, "high": 105.0, "low": 95.0, "close": 102.0}
        is_valid, error = validate_schema(raw)
        assert is_valid is False
        assert "date" in error

    def test_missing_close_rejected(self):
        raw = {"date": "2026-04-21", "open": 100.0, "high": 105.0, "low": 95.0}
        is_valid, error = validate_schema(raw)
        assert is_valid is False
        assert "close" in error

    def test_missing_multiple_fields_rejected(self):
        raw = {"date": "2026-04-21"}
        is_valid, error = validate_schema(raw)
        assert is_valid is False
        assert "open" in error
        assert "high" in error
        assert "low" in error
        assert "close" in error

    def test_wrong_type_for_date_rejected(self):
        raw = {
            "date": 20260421,  # int instead of str
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
        }
        is_valid, error = validate_schema(raw)
        assert is_valid is False
        assert "date" in error
        assert "type" in error.lower()

    def test_wrong_type_for_close_rejected(self):
        raw = {
            "date": "2026-04-21",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": "102.0",  # str instead of numeric
        }
        is_valid, error = validate_schema(raw)
        assert is_valid is False
        assert "close" in error
        assert "type" in error.lower()

    def test_non_dict_input_rejected(self):
        is_valid, error = validate_schema([1, 2, 3])
        assert is_valid is False
        assert "dict" in error.lower()

    def test_extra_fields_are_ok(self):
        """Extra fields beyond the required ones should not cause rejection."""
        raw = {
            "date": "2026-04-21",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "adjusted_close": 102.0,
            "volume": 500,
            "extra_field": "should be fine",
        }
        is_valid, error = validate_schema(raw)
        assert is_valid is True

    def test_int_prices_are_valid(self):
        """API sometimes returns prices as ints (e.g. 100 instead of 100.0)."""
        raw = {
            "date": "2026-04-21",
            "open": 100,
            "high": 105,
            "low": 95,
            "close": 102,
        }
        is_valid, error = validate_schema(raw)
        assert is_valid is True
