"""PostgreSQL database connection and upsert logic."""

from typing import Optional

import psycopg2
import psycopg2.extras

from src.logger import get_logger
from src.models import CommodityPrice, RejectedRow

logger = get_logger(__name__)

# ── Upsert query (idempotent) ─────────────────────────────────────────────────
UPSERT_SQL = """
INSERT INTO commodities_eod (date, symbol, name, open, high, low, close,
                             adjusted_close, volume, ingestion_ts)
VALUES %s
ON CONFLICT (date, symbol)
DO UPDATE SET
    open           = EXCLUDED.open,
    high           = EXCLUDED.high,
    low            = EXCLUDED.low,
    close          = EXCLUDED.close,
    adjusted_close = EXCLUDED.adjusted_close,
    volume         = EXCLUDED.volume,
    name           = EXCLUDED.name,
    ingestion_ts   = EXCLUDED.ingestion_ts;
"""

# ── Rejected rows insert ──────────────────────────────────────────────────────
INSERT_REJECTED_SQL = """
INSERT INTO commodities_eod_rejected (date, symbol, name, open, high, low, close,
                                       adjusted_close, volume, rejected_reason)
VALUES %s
"""


class Database:
    """Manages PostgreSQL connection and batch upserts.

    IMPORTANT: psycopg2 connections are NOT thread-safe. Each worker thread
    must create its own Database instance via the create() class method.
    """

    def __init__(self, connection_string: str) -> None:
        """Initialize with a PostgreSQL connection string.

        Args:
            connection_string: psycopg2-compatible connection string,
                e.g. "postgresql://user:pass@host:5432/dbname"
        """
        self.connection_string = connection_string
        self._conn: Optional[psycopg2.extensions.connection] = None

    @classmethod
    def create(cls, connection_string: str) -> "Database":
        """Factory method: create a new Database instance and open the connection.

        Use this in worker threads to get a thread-safe, dedicated connection.

        Args:
            connection_string: psycopg2-compatible connection string.

        Returns:
            A connected Database instance.
        """
        db = cls(connection_string)
        db.connect()
        return db

    # ── Connection management ─────────────────────────────────────────────

    def connect(self) -> None:
        """Open a connection to the database."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self.connection_string)
            self._conn.autocommit = False
            logger.info("Database connection established")

    def close(self) -> None:
        """Close the database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            logger.info("Database connection closed")

    @property
    def connection(self) -> psycopg2.extensions.connection:
        """Return the active connection, connecting if needed."""
        if self._conn is None or self._conn.closed:
            self.connect()
        return self._conn

    # ── Write operations ───────────────────────────────────────────────────

    def upsert_rows(self, rows: list[CommodityPrice]) -> int:
        """Batch-upsert a list of CommodityPrice rows.

        Uses psycopg2.execute_values for efficient batch insertion.

        Args:
            rows: List of CommodityPrice objects to upsert.

        Returns:
            Number of rows upserted.
        """
        if not rows:
            return 0

        # Convert each row to a tuple matching the UPSERT_SQL column order
        values = [row.as_tuple() for row in rows]

        cursor = self.connection.cursor()
        try:
            psycopg2.extras.execute_values(
                cursor,
                UPSERT_SQL,
                values,
                page_size=500,
            )
            self.connection.commit()
            logger.info(f"Upserted {len(rows)} rows")
            return len(rows)
        except Exception as exc:
            self.connection.rollback()
            logger.error(f"Upsert failed, rolled back: {exc}")
            raise
        finally:
            cursor.close()

    def insert_rejected_rows(self, rows: list[RejectedRow]) -> int:
        """Batch-insert rejected rows into the commodities_eod_rejected table.

        Args:
            rows: List of RejectedRow objects to insert.

        Returns:
            Number of rejected rows inserted.
        """
        if not rows:
            return 0

        values = [row.as_tuple() for row in rows]

        cursor = self.connection.cursor()
        try:
            psycopg2.extras.execute_values(
                cursor,
                INSERT_REJECTED_SQL,
                values,
                page_size=500,
            )
            self.connection.commit()
            logger.info(f"Inserted {len(rows)} rejected rows")
            return len(rows)
        except Exception as exc:
            self.connection.rollback()
            logger.error(f"Rejected rows insert failed, rolled back: {exc}")
            raise
        finally:
            cursor.close()

    # ── Read operations (for validation) ──────────────────────────────────

    def get_latest_date(self) -> Optional[str]:
        """Return the most recent date in the table, or None if empty."""
        cursor = self.connection.cursor()
        try:
            cursor.execute("SELECT MAX(date) FROM commodities_eod")
            result = cursor.fetchone()[0]
            return result.isoformat() if result else None
        finally:
            cursor.close()

    def get_previous_close(self, symbol: str, before_date: str) -> Optional[float]:
        """Return the close price for a symbol on the most recent date before the given date."""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                """
                SELECT close FROM commodities_eod
                WHERE symbol = %s AND date < %s
                ORDER BY date DESC
                LIMIT 1
                """,
                (symbol, before_date),
            )
            row = cursor.fetchone()
            return float(row[0]) if row else None
        finally:
            cursor.close()
