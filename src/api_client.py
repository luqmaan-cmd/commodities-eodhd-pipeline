"""EODHD API client with retry logic and rate limiting."""

import time
from datetime import date
from typing import Optional

import requests

from src.logger import get_logger
from src.models import CommodityConfig, CommodityPrice
from src.validator import validate_schema

logger = get_logger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
BASE_URL = "https://eodhistoricaldata.com/api/eod/{symbol}"
MAX_RETRIES = 3
INITIAL_BACKOFF = 1  # seconds (doubles each retry: 1, 2, 4)
REQUEST_DELAY = 0.5  # seconds between consecutive API calls
REQUEST_TIMEOUT = 30  # seconds

# HTTP status codes that should NOT be retried
NO_RETRY_STATUSES = {401, 403, 404}


class EODHDClient:
    """Client for the EODHD End-of-Day Historical Data API."""

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = requests.Session()

    # ── Public API ───────────────────────────────────────────────────────────

    def fetch_eod(
        self,
        commodity: CommodityConfig,
        from_date: Optional[date] = None,
        to_date: Optional[date] = None,
        skip_delay: bool = False,
    ) -> list[CommodityPrice]:
        """Fetch EOD data for a single commodity.

        Args:
            commodity: Commodity config with symbol, name, api_code, category.
            from_date: Optional start date (inclusive).
            to_date: Optional end date (inclusive).
            skip_delay: If True, skip the inter-request delay.
                        Use for backfill where rate-limit is not a concern
                        (we use 0.021% of the 200k/day limit).

        Returns:
            List of CommodityPrice objects (may be empty if no data).
        """
        url = BASE_URL.format(symbol=commodity.api_code)
        params: dict = {
            "api_token": self.api_key,
            "fmt": "json",
        }
        if from_date:
            params["from"] = from_date.isoformat()
        if to_date:
            params["to"] = to_date.isoformat()

        raw_rows = self._request_with_retry(url, params)

        # Rate-limit: small delay to stay well within 200k/day.
        # Skipped for backfill (skip_delay=True) since we only use
        # 42 calls = 0.021% of the daily limit.
        if not skip_delay:
            time.sleep(REQUEST_DELAY)

        # Validate schema of each raw row before standardization.
        # Rows with bad schemas are logged and skipped (they would crash
        # _standardize() anyway).
        prices: list[CommodityPrice] = []
        for raw in raw_rows:
            is_valid, error = validate_schema(raw)
            if is_valid:
                prices.append(self._standardize(raw, commodity))
            else:
                logger.warning(
                    f"Schema validation failed for {commodity.symbol}: "
                    f"{error} — raw={raw}"
                )

        return prices

    def close(self) -> None:
        """Close the underlying requests session."""
        self.session.close()

    # ── Private helpers ──────────────────────────────────────────────────────

    def _request_with_retry(
        self, url: str, params: dict
    ) -> list[dict]:
        """Execute an HTTP GET with exponential-backoff retry logic.

        Retries on: HTTP 429, 5xx, timeout, connection errors.
        Does NOT retry on: 401, 403, 404.
        """
        backoff = INITIAL_BACKOFF

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.session.get(
                    url, params=params, timeout=REQUEST_TIMEOUT
                )

                if response.status_code == 200:
                    data = response.json()
                    if not isinstance(data, list):
                        logger.warning(
                            f"Unexpected response format for {url}: "
                            f"expected list, got {type(data).__name__}"
                        )
                        return []
                    return data

                if response.status_code in NO_RETRY_STATUSES:
                    logger.error(
                        f"API returned {response.status_code} for {url} — "
                        f"not retrying. Body: {response.text[:200]}"
                    )
                    return []

                if response.status_code == 429 or response.status_code >= 500:
                    if attempt < MAX_RETRIES:
                        logger.warning(
                            f"API returned {response.status_code} for {url} — "
                            f"retry {attempt}/{MAX_RETRIES} in {backoff}s"
                        )
                        time.sleep(backoff)
                        backoff *= 2
                        continue
                    else:
                        logger.error(
                            f"API returned {response.status_code} for {url} — "
                            f"max retries exhausted"
                        )
                        return []

                # Other unexpected status codes
                logger.error(
                    f"API returned unexpected status {response.status_code} "
                    f"for {url}. Body: {response.text[:200]}"
                )
                return []

            except requests.exceptions.Timeout:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Timeout hitting {url} — "
                        f"retry {attempt}/{MAX_RETRIES} in {backoff}s"
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    logger.error(
                        f"Timeout hitting {url} — max retries exhausted"
                    )
                    return []

            except requests.exceptions.ConnectionError as exc:
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"Connection error hitting {url}: {exc} — "
                        f"retry {attempt}/{MAX_RETRIES} in {backoff}s"
                    )
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                else:
                    logger.error(
                        f"Connection error hitting {url}: {exc} — "
                        f"max retries exhausted"
                    )
                    return []

        return []  # should not reach here, but safety net

    @staticmethod
    def _standardize(raw: dict, commodity: CommodityConfig) -> CommodityPrice:
        """Convert a raw EODHD JSON row into a CommodityPrice model.

        The API returns: date, open, high, low, close, adjusted_close, volume.
        We add symbol and name from our config (not from the API response).
        """
        return CommodityPrice(
            date=date.fromisoformat(raw["date"]),
            symbol=commodity.symbol,
            name=commodity.name,
            open=raw.get("open"),
            high=raw.get("high"),
            low=raw.get("low"),
            close=raw.get("close"),
            adjusted_close=raw.get("adjusted_close"),
            volume=int(raw["volume"]) if raw.get("volume") is not None else None,
        )
