"""Tests for src.pipeline — orchestrator logic."""

import sys
from datetime import date
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, "/Users/luqmaan2000/EODHD - Commodities")

from src.models import (
    CommodityConfig,
    CommodityPrice,
    CommodityRunResult,
    PipelineRunSummary,
    RejectedRow,
)
from src.pipeline import (
    load_commodities,
    _process_commodity_backfill,
    _process_commodity_daily,
    run_backfill,
    run_daily,
    is_trading_day,
)


# ── load_commodities ──────────────────────────────────────────────────────────

class TestLoadCommodities:
    def test_loads_from_yaml(self):
        commodities = load_commodities()
        assert len(commodities) == 42
        assert commodities[0].symbol == "CL"
        assert commodities[0].api_code == "CL.COMM"
        assert commodities[0].category == "Energy"

    def test_all_have_required_fields(self):
        commodities = load_commodities()
        for c in commodities:
            assert c.symbol
            assert c.name
            assert c.api_code
            assert c.category

    def test_default_anomaly_threshold(self):
        """Commodities without explicit anomaly_threshold should default to 0.30."""
        commodities = load_commodities()
        cl = next(c for c in commodities if c.symbol == "CL")
        assert cl.anomaly_threshold == 0.30

    def test_custom_anomaly_threshold_loaded(self):
        """Commodities with explicit anomaly_threshold in YAML should use that value."""
        commodities = load_commodities()
        ebm = next(c for c in commodities if c.symbol == "EBM")
        hg = next(c for c in commodities if c.symbol == "HG")
        assert ebm.anomaly_threshold == 500.0
        assert hg.anomaly_threshold == 500.0


# ── _process_commodity_backfill ───────────────────────────────────────────────

class TestProcessCommodityBackfill:
    """Tests for the backfill-optimised processing function."""

    def _make_commodity(self):
        return CommodityConfig(
            symbol="GC", name="Gold", api_code="GC.COMM", category="Precious Metals"
        )

    def _make_prices(self):
        """Create 3 chronological price rows."""
        return [
            CommodityPrice(
                date=date(2026, 4, 19), symbol="GC", name="Gold",
                open=3100, high=3150, low=3090, close=3120,
                adjusted_close=3120, volume=1000,
            ),
            CommodityPrice(
                date=date(2026, 4, 20), symbol="GC", name="Gold",
                open=3120, high=3180, low=3110, close=3160,
                adjusted_close=3160, volume=1200,
            ),
            CommodityPrice(
                date=date(2026, 4, 21), symbol="GC", name="Gold",
                open=3160, high=3200, low=3150, close=3190,
                adjusted_close=3190, volume=1100,
            ),
        ]

    @patch("src.pipeline.Database")
    def test_happy_path(self, MockDatabase):
        commodity = self._make_commodity()
        prices = self._make_prices()

        mock_client = MagicMock()
        mock_client.fetch_eod.return_value = prices

        mock_db_instance = MagicMock()
        mock_db_instance.get_previous_close.return_value = None  # No prior data
        mock_db_instance.upsert_rows.return_value = 3
        MockDatabase.create.return_value = mock_db_instance

        result = _process_commodity_backfill(commodity, mock_client, "postgresql://test")

        assert result.status == "success"
        assert result.rows_fetched == 3
        assert result.rows_valid == 3
        assert result.rows_rejected == 0
        mock_db_instance.insert_rejected_rows.assert_not_called()


# ── is_trading_day ────────────────────────────────────────────────────────────

class TestIsTradingDay:
    """Tests for the is_trading_day helper."""

    def test_monday_is_trading_day(self):
        assert is_trading_day(date(2026, 4, 20)) is True  # Monday

    def test_friday_is_trading_day(self):
        assert is_trading_day(date(2026, 4, 24)) is True  # Friday

    def test_saturday_is_not_trading_day(self):
        assert is_trading_day(date(2026, 4, 25)) is False  # Saturday

    def test_sunday_is_not_trading_day(self):
        assert is_trading_day(date(2026, 4, 26)) is False  # Sunday


# ── Alerting in run_daily ─────────────────────────────────────────────────────

class TestRunDailyAlerts:
    """Tests for the 3 alert types wired into run_daily()."""

    def _make_commodities(self):
        return [
            CommodityConfig(symbol="GC", name="Gold", api_code="GC.COMM", category="Precious Metals"),
            CommodityConfig(symbol="CL", name="Crude Oil", api_code="CL.COMM", category="Energy"),
        ]

    @patch("src.pipeline.Alerter")
    @patch("src.pipeline.EODHDClient")
    @patch("src.pipeline._process_commodity_daily")
    @patch("src.pipeline.load_commodities")
    @patch("src.pipeline.Database")
    def test_success_summary_always_fires(
        self, MockDB, mock_load, mock_process, MockClient, MockAlerter
    ):
        """Success summary alert should always fire at the end of a daily run."""
        mock_load.return_value = self._make_commodities()

        mock_process.side_effect = [
            CommodityRunResult(symbol="GC", status="success", rows_upserted=1),
            CommodityRunResult(symbol="CL", status="success", rows_upserted=1),
        ]

        mock_db_instance = MagicMock()
        mock_db_instance.get_latest_date.return_value = "2026-04-20"
        MockDB.return_value = mock_db_instance
        MockDB.create.return_value = mock_db_instance

        mock_alerter_instance = MagicMock()
        MockAlerter.return_value = mock_alerter_instance

        run_daily(
            api_key="test-key",
            db_url="postgresql://test",
            target_date=date(2026, 4, 20),  # Monday
        )

        mock_alerter_instance.alert_success_summary.assert_called_once()
        call_kwargs = mock_alerter_instance.alert_success_summary.call_args[1]
        assert call_kwargs["total_symbols"] == 2
        assert call_kwargs["successful"] == 2
        assert call_kwargs["failed"] == 0
        assert call_kwargs["total_rows_upserted"] == 2

    @patch("src.pipeline.Alerter")
    @patch("src.pipeline.EODHDClient")
    @patch("src.pipeline._process_commodity_daily")
    @patch("src.pipeline.load_commodities")
    @patch("src.pipeline.Database")
    def test_zero_data_alert_on_trading_day(
        self, MockDB, mock_load, mock_process, MockClient, MockAlerter
    ):
        """Zero-data alert should fire when 0 rows upserted on a trading day."""
        mock_load.return_value = self._make_commodities()

        # Both commodities return 0 rows upserted
        mock_process.side_effect = [
            CommodityRunResult(symbol="GC", status="success", rows_upserted=0),
            CommodityRunResult(symbol="CL", status="success", rows_upserted=0),
        ]

        mock_db_instance = MagicMock()
        mock_db_instance.get_latest_date.return_value = "2026-04-17"
        MockDB.return_value = mock_db_instance
        MockDB.create.return_value = mock_db_instance

        mock_alerter_instance = MagicMock()
        MockAlerter.return_value = mock_alerter_instance

        run_daily(
            api_key="test-key",
            db_url="postgresql://test",
            target_date=date(2026, 4, 20),  # Monday — trading day
        )

        mock_alerter_instance.alert_zero_data_ingested.assert_called_once_with("2026-04-20")

    @patch("src.pipeline.Alerter")
    @patch("src.pipeline.EODHDClient")
    @patch("src.pipeline._process_commodity_daily")
    @patch("src.pipeline.load_commodities")
    @patch("src.pipeline.Database")
    def test_no_zero_data_alert_on_weekend(
        self, MockDB, mock_load, mock_process, MockClient, MockAlerter
    ):
        """Zero-data alert should NOT fire on a weekend (not a trading day)."""
        mock_load.return_value = self._make_commodities()

        mock_process.side_effect = [
            CommodityRunResult(symbol="GC", status="success", rows_upserted=0),
            CommodityRunResult(symbol="CL", status="success", rows_upserted=0),
        ]

        mock_db_instance = MagicMock()
        mock_db_instance.get_latest_date.return_value = "2026-04-17"
        MockDB.return_value = mock_db_instance
        MockDB.create.return_value = mock_db_instance

        mock_alerter_instance = MagicMock()
        MockAlerter.return_value = mock_alerter_instance

        run_daily(
            api_key="test-key",
            db_url="postgresql://test",
            target_date=date(2026, 4, 25),  # Saturday — not a trading day
        )

        mock_alerter_instance.alert_zero_data_ingested.assert_not_called()

    @patch("src.pipeline.Alerter")
    @patch("src.pipeline.EODHDClient")
    @patch("src.pipeline._process_commodity_daily")
    @patch("src.pipeline.load_commodities")
    @patch("src.pipeline.Database")
    def test_pipeline_failure_alert_on_failures(
        self, MockDB, mock_load, mock_process, MockClient, MockAlerter
    ):
        """Pipeline failure alert should fire when commodities fail."""
        mock_load.return_value = self._make_commodities()

        mock_process.side_effect = [
            CommodityRunResult(symbol="GC", status="success", rows_upserted=1),
            CommodityRunResult(symbol="CL", status="failed", error="API timeout"),
        ]

        mock_db_instance = MagicMock()
        mock_db_instance.get_latest_date.return_value = "2026-04-20"
        MockDB.return_value = mock_db_instance
        MockDB.create.return_value = mock_db_instance

        mock_alerter_instance = MagicMock()
        MockAlerter.return_value = mock_alerter_instance

        run_daily(
            api_key="test-key",
            db_url="postgresql://test",
            target_date=date(2026, 4, 20),
        )

        mock_alerter_instance.alert_pipeline_failure.assert_called_once()
        call_args = mock_alerter_instance.alert_pipeline_failure.call_args[0][0]
        assert "CL" in call_args

    @patch("src.pipeline.Alerter")
    @patch("src.pipeline.EODHDClient")
    @patch("src.pipeline._process_commodity_daily")
    @patch("src.pipeline.load_commodities")
    @patch("src.pipeline.Database")
    def test_no_pipeline_failure_alert_when_all_succeed(
        self, MockDB, mock_load, mock_process, MockClient, MockAlerter
    ):
        """Pipeline failure alert should NOT fire when all commodities succeed."""
        mock_load.return_value = self._make_commodities()

        mock_process.side_effect = [
            CommodityRunResult(symbol="GC", status="success", rows_upserted=1),
            CommodityRunResult(symbol="CL", status="success", rows_upserted=1),
        ]

        mock_db_instance = MagicMock()
        mock_db_instance.get_latest_date.return_value = "2026-04-20"
        MockDB.return_value = mock_db_instance
        MockDB.create.return_value = mock_db_instance

        mock_alerter_instance = MagicMock()
        MockAlerter.return_value = mock_alerter_instance

        run_daily(
            api_key="test-key",
            db_url="postgresql://test",
            target_date=date(2026, 4, 20),
        )

        mock_alerter_instance.alert_pipeline_failure.assert_not_called()

    @patch("src.pipeline.Alerter")
    @patch("src.pipeline.EODHDClient")
    @patch("src.pipeline._process_commodity_daily")
    @patch("src.pipeline.load_commodities")
    @patch("src.pipeline.Database")
    def test_successful_symbols_tracked(
        self, MockDB, mock_load, mock_process, MockClient, MockAlerter
    ):
        """successful_symbols should be populated in the summary."""
        mock_load.return_value = self._make_commodities()

        mock_process.side_effect = [
            CommodityRunResult(symbol="GC", status="success", rows_upserted=1),
            CommodityRunResult(symbol="CL", status="failed", error="API timeout"),
        ]

        mock_db_instance = MagicMock()
        mock_db_instance.get_latest_date.return_value = "2026-04-20"
        MockDB.return_value = mock_db_instance
        MockDB.create.return_value = mock_db_instance

        MockAlerter.return_value = MagicMock()

        summary = run_daily(
            api_key="test-key",
            db_url="postgresql://test",
            target_date=date(2026, 4, 20),
        )

        assert "GC" in summary.successful_symbols
        assert "CL" not in summary.successful_symbols
        assert "CL" in summary.failed_symbols


# ── Rejected row storage (continued) ─────────────────────────────────────────

class TestRejectedRowStorageDaily:
    """Tests for rejected row storage in daily mode (separate from alert tests)."""

    def _make_commodity(self):
        return CommodityConfig(
            symbol="GC", name="Gold", api_code="GC.COMM", category="Precious Metals"
        )

    @patch("src.pipeline.Database")
    def test_backfill_insert_rejected_failure_does_not_crash(self, MockDatabase):
        """If insert_rejected_rows fails, the pipeline should continue (not crash)."""
        commodity = self._make_commodity()
        prices = [
            CommodityPrice(
                date=date(2026, 4, 21), symbol="GC", name="Gold",
                open=100, high=95, low=105, close=102,  # high < low
                adjusted_close=102, volume=1000,
            ),
        ]

        mock_client = MagicMock()
        mock_client.fetch_eod.return_value = prices

        mock_db_instance = MagicMock()
        mock_db_instance.get_previous_close.return_value = None
        mock_db_instance.insert_rejected_rows.side_effect = Exception("DB error")
        MockDatabase.create.return_value = mock_db_instance

        result = _process_commodity_backfill(commodity, mock_client, "postgresql://test")

        # Should still succeed (the rejected row insert failure is logged, not raised)
        assert result.status == "success"
        assert result.rows_rejected == 1

    @patch("src.pipeline.Database")
    def test_daily_stores_rejected_rows(self, MockDatabase):
        """Daily mode should call db.insert_rejected_rows() for rejected rows."""
        commodity = self._make_commodity()
        prices = [
            CommodityPrice(
                date=date(2026, 4, 21), symbol="GC", name="Gold",
                open=100, high=95, low=105, close=102,  # high < low
                adjusted_close=102, volume=1000,
            ),
        ]

        mock_client = MagicMock()
        mock_client.fetch_eod.return_value = prices

        mock_db_instance = MagicMock()
        mock_db_instance.get_previous_close.return_value = None
        MockDatabase.create.return_value = mock_db_instance

        result = _process_commodity_daily(
            commodity, mock_client, "postgresql://test",
            from_date=date(2026, 4, 21), to_date=date(2026, 4, 21),
        )

        assert result.rows_rejected == 1
        mock_db_instance.insert_rejected_rows.assert_called_once()

    @patch("src.pipeline.Database")
    def test_daily_no_rejected_rows_no_insert_call(self, MockDatabase):
        """If no rows are rejected in daily mode, insert_rejected_rows should NOT be called."""
        commodity = self._make_commodity()
        prices = [
            CommodityPrice(
                date=date(2026, 4, 21), symbol="GC", name="Gold",
                open=100, high=105, low=95, close=102,
                adjusted_close=102, volume=1000,
            ),
        ]

        mock_client = MagicMock()
        mock_client.fetch_eod.return_value = prices

        mock_db_instance = MagicMock()
        mock_db_instance.get_previous_close.return_value = None
        mock_db_instance.upsert_rows.return_value = 1
        MockDatabase.create.return_value = mock_db_instance

        result = _process_commodity_daily(
            commodity, mock_client, "postgresql://test",
            from_date=date(2026, 4, 21), to_date=date(2026, 4, 21),
        )

        assert result.rows_rejected == 0
        mock_db_instance.insert_rejected_rows.assert_not_called()
