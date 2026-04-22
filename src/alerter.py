"""Alerting via Slack webhook for the EODHD Commodities Pipeline."""

import json
from typing import Optional

import requests

from src.logger import get_logger

logger = get_logger(__name__)


class Alerter:
    """Sends alerts to a Slack channel via incoming webhook."""

    def __init__(self, webhook_url: Optional[str] = None) -> None:
        """Initialize the alerter.

        Args:
            webhook_url: Slack incoming webhook URL.
                         If None, alerts are only logged (no-op mode).
        """
        self.webhook_url = webhook_url

    def send(self, message: str, level: str = "warning") -> None:
        """Send an alert message.

        Args:
            message: The alert text.
            level: Severity level — "info", "warning", "error", or "critical".
        """
        emoji = {
            "info": ":information_source:",
            "warning": ":warning:",
            "error": ":rotating_light:",
            "critical": ":red_circle:",
        }
        prefix = emoji.get(level, ":bell:")

        logger.info(f"Alert [{level}]: {message}")

        if not self.webhook_url:
            logger.info("No Slack webhook configured — alert logged only")
            return

        payload = {
            "text": f"{prefix} *EODHD Commodities Pipeline* — {level.upper()}\n{message}",
        }

        try:
            response = requests.post(
                self.webhook_url,
                json=payload,
                timeout=10,
            )
            if response.status_code != 200:
                logger.error(
                    f"Slack webhook returned {response.status_code}: {response.text[:200]}"
                )
        except requests.exceptions.RequestException as exc:
            logger.error(f"Failed to send Slack alert: {exc}")

    def alert_pipeline_failure(self, error: str) -> None:
        """Alert that the pipeline has failed entirely (critical severity)."""
        self.send(f"Pipeline failed: {error}", level="critical")

    def alert_validation_failures(self, failures: list[str]) -> None:
        """Alert about validation failures."""
        if not failures:
            return
        msg = "Validation failures:\n" + "\n".join(f"• {f}" for f in failures)
        self.send(msg, level="warning")

    def alert_missing_commodities(self, missing: list[str], target_date: str) -> None:
        """Alert about commodities missing for a target date."""
        msg = (
            f"Missing {len(missing)} commodity(s) for {target_date}: "
            f"{', '.join(missing)}"
        )
        self.send(msg, level="warning")

    def alert_stale_data(self, latest_date: str, expected_date: str) -> None:
        """Alert that data in the table is stale."""
        msg = f"Stale data: latest date is {latest_date}, expected {expected_date}"
        self.send(msg, level="warning")

    def alert_api_errors(self, symbol: str, error: str) -> None:
        """Alert about repeated API failures for a symbol."""
        msg = f"API error for {symbol}: {error}"
        self.send(msg, level="error")

    def alert_zero_data_ingested(self, target_date: str) -> None:
        """Alert that zero rows were ingested on what should be a trading day.

        This indicates a silent failure — the pipeline succeeded but produced
        no data, which likely means the API returned nothing or all rows were
        rejected.
        """
        msg = (
            f"Zero data ingested for {target_date} — a trading day. "
            f"This may indicate an API issue or all rows were rejected."
        )
        self.send(msg, level="warning")

    def alert_success_summary(
        self,
        target_date: str,
        total_symbols: int,
        successful: int,
        failed: int,
        failed_symbols: list[str],
        total_rows_upserted: int,
        total_rows_rejected: int,
        run_duration_seconds: float,
    ) -> None:
        """Send a success summary alert after a daily pipeline run.

        Always fires at the end of a daily run (even if some commodities failed),
        providing visibility into what happened.
        """
        duration_str = f"{run_duration_seconds:.1f}s"
        commodity_str = f"{successful}/{total_symbols}"

        lines = [
            f"*Daily Run — {target_date}*",
            f"Commodities: {commodity_str} succeeded",
            f"Rows upserted: {total_rows_upserted}",
        ]

        if total_rows_rejected > 0:
            lines.append(f"Rows rejected: {total_rows_rejected}")

        if failed > 0:
            lines.append(f"Failed: {failed} ({', '.join(failed_symbols)})")

        lines.append(f"Duration: {duration_str}")

        # Use "warning" level if there are failures, otherwise "info"
        level = "warning" if failed > 0 else "info"
        self.send("\n".join(lines), level=level)
