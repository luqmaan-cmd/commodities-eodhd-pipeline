"""Tests for src.api_client — EODHD API client with retry logic."""

import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "/Users/luqmaan2000/EODHD - Commodities")

from src.api_client import EODHDClient
from src.models import CommodityConfig


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def commodity():
    return CommodityConfig(
        symbol="GC",
        name="Gold (COMEX)",
        api_code="GC.COMM",
        category="Precious Metals",
    )


@pytest.fixture
def client():
    return EODHDClient(api_key="test-key")


# ── Standardization ───────────────────────────────────────────────────────────

class TestStandardize:
    def test_standardize_valid_row(self, client, commodity):
        raw = {
            "date": "2026-04-21",
            "open": 3129.7,
            "high": 3149.5,
            "low": 3104.0,
            "close": 3118.9,
            "adjusted_close": 3118.9,
            "volume": 1721,
        }
        result = EODHDClient._standardize(raw, commodity)

        assert result.date == date(2026, 4, 21)
        assert result.symbol == "GC"
        assert result.name == "Gold (COMEX)"
        assert result.open == 3129.7
        assert result.high == 3149.5
        assert result.low == 3104.0
        assert result.close == 3118.9
        assert result.adjusted_close == 3118.9
        assert result.volume == 1721

    def test_standardize_null_volume(self, client, commodity):
        raw = {
            "date": "2026-04-21",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "adjusted_close": 102.0,
            "volume": None,
        }
        result = EODHDClient._standardize(raw, commodity)
        assert result.volume is None

    def test_symbol_and_name_from_config(self, client, commodity):
        raw = {
            "date": "2026-04-21",
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 102.0,
            "adjusted_close": 102.0,
            "volume": 500,
        }
        result = EODHDClient._standardize(raw, commodity)
        # Symbol and name come from config, not from the API response
        assert result.symbol == "GC"
        assert result.name == "Gold (COMEX)"


# ── Retry logic ───────────────────────────────────────────────────────────────

class TestRetryLogic:
    def test_404_not_retried(self, client, commodity):
        """A 404 should return empty list immediately, no retries."""
        mock_response = MagicMock()
        mock_response.status_code = 404
        mock_response.text = "Not Found"

        with patch.object(client.session, "get", return_value=mock_response):
            result = client._request_with_retry("http://test", {})
            assert result == []

    def test_200_returns_data(self, client, commodity):
        """A 200 should return the parsed JSON list."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"date": "2026-04-21", "open": 100, "high": 105,
             "low": 95, "close": 102, "adjusted_close": 102, "volume": 500}
        ]

        with patch.object(client.session, "get", return_value=mock_response):
            result = client._request_with_retry("http://test", {})
            assert len(result) == 1
            assert result[0]["date"] == "2026-04-21"

    def test_500_retried_then_succeeds(self, client, commodity):
        """A 500 should retry, and succeed if the next call returns 200."""
        mock_500 = MagicMock()
        mock_500.status_code = 500
        mock_500.text = "Internal Server Error"

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.json.return_value = [{"date": "2026-04-21", "open": 100,
                                        "high": 105, "low": 95, "close": 102,
                                        "adjusted_close": 102, "volume": 500}]

        with patch.object(client.session, "get", side_effect=[mock_500, mock_200]):
            with patch("src.api_client.time.sleep"):  # skip actual sleep
                result = client._request_with_retry("http://test", {})
                assert len(result) == 1


# ── skip_delay parameter ──────────────────────────────────────────────────────

class TestSkipDelay:
    def test_skip_delay_true_skips_sleep(self, client, commodity):
        """When skip_delay=True, time.sleep should NOT be called."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"date": "2026-04-21", "open": 100, "high": 105,
             "low": 95, "close": 102, "adjusted_close": 102, "volume": 500}
        ]

        with patch.object(client.session, "get", return_value=mock_response):
            with patch("src.api_client.time.sleep") as mock_sleep:
                client.fetch_eod(commodity, skip_delay=True)
                mock_sleep.assert_not_called()

    def test_skip_delay_false_calls_sleep(self, client, commodity):
        """When skip_delay=False (default), time.sleep should be called."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"date": "2026-04-21", "open": 100, "high": 105,
             "low": 95, "close": 102, "adjusted_close": 102, "volume": 500}
        ]

        with patch.object(client.session, "get", return_value=mock_response):
            with patch("src.api_client.time.sleep") as mock_sleep:
                client.fetch_eod(commodity, skip_delay=False)
                mock_sleep.assert_called_once_with(0.5)

    def test_default_skip_delay_is_false(self, client, commodity):
        """By default (no skip_delay arg), time.sleep should be called."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"date": "2026-04-21", "open": 100, "high": 105,
             "low": 95, "close": 102, "adjusted_close": 102, "volume": 500}
        ]

        with patch.object(client.session, "get", return_value=mock_response):
            with patch("src.api_client.time.sleep") as mock_sleep:
                client.fetch_eod(commodity)
                mock_sleep.assert_called_once_with(0.5)


# ── Schema validation in fetch_eod ────────────────────────────────────────────

class TestSchemaValidationInFetchEod:
    """Tests that fetch_eod validates raw API response schemas before standardizing."""

    def test_bad_schema_row_skipped(self, client, commodity):
        """A row with missing required fields should be skipped, not crash."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            # Good row
            {"date": "2026-04-21", "open": 100, "high": 105,
             "low": 95, "close": 102, "adjusted_close": 102, "volume": 500},
            # Bad row — missing 'close'
            {"date": "2026-04-22", "open": 101, "high": 106,
             "low": 96, "adjusted_close": 103, "volume": 600},
        ]

        with patch.object(client.session, "get", return_value=mock_response):
            with patch("src.api_client.time.sleep"):
                result = client.fetch_eod(commodity, skip_delay=True)

        # Only the good row should be returned
        assert len(result) == 1
        assert result[0].date == date(2026, 4, 21)

    def test_all_bad_schema_rows_returns_empty(self, client, commodity):
        """If all rows have bad schemas, fetch_eod should return an empty list."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"date": "2026-04-21"},  # Missing open/high/low/close
            {"open": 100},  # Missing date
        ]

        with patch.object(client.session, "get", return_value=mock_response):
            with patch("src.api_client.time.sleep"):
                result = client.fetch_eod(commodity, skip_delay=True)

        assert result == []

    def test_mixed_good_and_bad_rows(self, client, commodity):
        """Good rows should pass through; bad rows should be skipped."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"date": "2026-04-21", "open": 100, "high": 105,
             "low": 95, "close": 102, "adjusted_close": 102, "volume": 500},
            {"date": "2026-04-22", "open": "bad", "high": 106,  # 'open' is a string
             "low": 96, "close": 103, "adjusted_close": 103, "volume": 600},
            {"date": "2026-04-23", "open": 102, "high": 107,
             "low": 97, "close": 104, "adjusted_close": 104, "volume": 700},
        ]

        with patch.object(client.session, "get", return_value=mock_response):
            with patch("src.api_client.time.sleep"):
                result = client.fetch_eod(commodity, skip_delay=True)

        assert len(result) == 2
        assert result[0].date == date(2026, 4, 21)
        assert result[1].date == date(2026, 4, 23)
