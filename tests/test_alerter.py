"""Tests for src.alerter — Slack alerting."""

import sys
from unittest.mock import MagicMock, patch

import requests

sys.path.insert(0, "/Users/luqmaan2000/EODHD - Commodities")

from src.alerter import Alerter


# ── Alerter.send() ────────────────────────────────────────────────────────────

class TestAlerterSend:
    """Tests for the base send() method."""

    @patch("src.alerter.requests.post")
    def test_sends_payload_to_webhook(self, mock_post):
        """send() should POST a JSON payload to the webhook URL."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.send("Test message", level="info")

        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args[1]
        assert call_kwargs["json"]["text"] == ":information_source: *EODHD Commodities Pipeline* — INFO\nTest message"

    @patch("src.alerter.requests.post")
    def test_critical_level_uses_red_circle_emoji(self, mock_post):
        """Critical severity should use the :red_circle: emoji."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.send("Critical issue", level="critical")

        call_kwargs = mock_post.call_args[1]
        assert ":red_circle:" in call_kwargs["json"]["text"]
        assert "CRITICAL" in call_kwargs["json"]["text"]

    def test_no_webhook_logs_only(self):
        """Without a webhook URL, send() should log but not raise."""
        alerter = Alerter(webhook_url=None)
        # Should not raise
        alerter.send("Test message", level="warning")

    @patch("src.alerter.requests.post")
    def test_non_200_response_logged(self, mock_post):
        """Non-200 response from Slack should be logged (not raised)."""
        mock_post.return_value = MagicMock(status_code=500, text="Server Error")

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        # Should not raise
        alerter.send("Test message", level="error")

    @patch("src.alerter.requests.post")
    def test_request_exception_logged(self, mock_post):
        """Network errors should be logged (not raised)."""
        mock_post.side_effect = requests.exceptions.ConnectionError("Timeout")

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        # Should not raise
        alerter.send("Test message", level="error")


# ── alert_pipeline_failure ───────────────────────────────────────────────────

class TestAlertPipelineFailure:
    """Tests for the pipeline failure alert (critical severity)."""

    @patch("src.alerter.requests.post")
    def test_uses_critical_level(self, mock_post):
        """Pipeline failure should use 'critical' severity."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.alert_pipeline_failure("Something broke")

        call_kwargs = mock_post.call_args[1]
        assert ":red_circle:" in call_kwargs["json"]["text"]
        assert "CRITICAL" in call_kwargs["json"]["text"]
        assert "Something broke" in call_kwargs["json"]["text"]


# ── alert_zero_data_ingested ─────────────────────────────────────────────────

class TestAlertZeroDataIngested:
    """Tests for the zero-data-ingested alert (warning severity)."""

    @patch("src.alerter.requests.post")
    def test_includes_target_date(self, mock_post):
        """Zero-data alert should include the target date."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.alert_zero_data_ingested("2026-04-20")

        call_kwargs = mock_post.call_args[1]
        assert "2026-04-20" in call_kwargs["json"]["text"]
        assert ":warning:" in call_kwargs["json"]["text"]

    @patch("src.alerter.requests.post")
    def test_mentions_trading_day(self, mock_post):
        """Zero-data alert should mention it's a trading day."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.alert_zero_data_ingested("2026-04-20")

        call_kwargs = mock_post.call_args[1]
        assert "trading day" in call_kwargs["json"]["text"].lower()


# ── alert_success_summary ────────────────────────────────────────────────────

class TestAlertSuccessSummary:
    """Tests for the success summary alert."""

    @patch("src.alerter.requests.post")
    def test_all_success_uses_info_level(self, mock_post):
        """When all commodities succeed, summary should use 'info' level."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.alert_success_summary(
            target_date="2026-04-20",
            total_symbols=42,
            successful=42,
            failed=0,
            failed_symbols=[],
            total_rows_upserted=40,
            total_rows_rejected=0,
            run_duration_seconds=12.5,
        )

        call_kwargs = mock_post.call_args[1]
        assert ":information_source:" in call_kwargs["json"]["text"]
        assert "42/42" in call_kwargs["json"]["text"]
        assert "40" in call_kwargs["json"]["text"]

    @patch("src.alerter.requests.post")
    def test_with_failures_uses_warning_level(self, mock_post):
        """When some commodities fail, summary should use 'warning' level."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.alert_success_summary(
            target_date="2026-04-20",
            total_symbols=42,
            successful=40,
            failed=2,
            failed_symbols=["CL", "NG"],
            total_rows_upserted=38,
            total_rows_rejected=1,
            run_duration_seconds=15.0,
        )

        call_kwargs = mock_post.call_args[1]
        assert ":warning:" in call_kwargs["json"]["text"]
        assert "40/42" in call_kwargs["json"]["text"]
        assert "CL" in call_kwargs["json"]["text"]
        assert "NG" in call_kwargs["json"]["text"]

    @patch("src.alerter.requests.post")
    def test_rejected_rows_included_when_nonzero(self, mock_post):
        """When rows are rejected, the count should appear in the summary."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.alert_success_summary(
            target_date="2026-04-20",
            total_symbols=42,
            successful=42,
            failed=0,
            failed_symbols=[],
            total_rows_upserted=40,
            total_rows_rejected=3,
            run_duration_seconds=10.0,
        )

        call_kwargs = mock_post.call_args[1]
        assert "rejected" in call_kwargs["json"]["text"].lower()
        assert "3" in call_kwargs["json"]["text"]

    @patch("src.alerter.requests.post")
    def test_duration_included(self, mock_post):
        """Duration should be included in the summary."""
        mock_post.return_value = MagicMock(status_code=200)

        alerter = Alerter(webhook_url="https://hooks.slack.com/test")
        alerter.alert_success_summary(
            target_date="2026-04-20",
            total_symbols=42,
            successful=42,
            failed=0,
            failed_symbols=[],
            total_rows_upserted=40,
            total_rows_rejected=0,
            run_duration_seconds=12.345,
        )

        call_kwargs = mock_post.call_args[1]
        assert "12.3s" in call_kwargs["json"]["text"]
