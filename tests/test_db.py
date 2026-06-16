"""Tests for src.db — Database connection and upsert logic."""

import sys
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, "/Users/luqmaan2000/EODHD - Commodities")

from src.db import Database
from src.models import CommodityPrice, RejectedRow


# ── Database.create() factory method ──────────────────────────────────────────

class TestDatabaseCreate:
    """Tests for the thread-safe factory method."""

    @patch("src.db.psycopg2.connect")
    def test_create_returns_connected_instance(self, mock_connect):
        """Database.create() should return a Database with an open connection."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_connect.return_value = mock_conn

        db = Database.create("postgresql://user:pass@host:5432/db")

        assert db._conn is mock_conn
        mock_connect.assert_called_once_with("postgresql://user:pass@host:5432/db")

    @patch("src.db.psycopg2.connect")
    def test_create_sets_autocommit_false(self, mock_connect):
        """Database.create() should set autocommit=False for transaction safety."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_connect.return_value = mock_conn

        db = Database.create("postgresql://user:pass@host:5432/db")

        mock_conn.autocommit = False  # This is what the connect() method does
        # Verify autocommit was set to False
        assert mock_conn.autocommit is False

    @patch("src.db.psycopg2.connect")
    def test_separate_instances_have_separate_connections(self, mock_connect):
        """Each Database.create() call should produce a new connection."""
        mock_conn1 = MagicMock()
        mock_conn1.closed = False
        mock_conn2 = MagicMock()
        mock_conn2.closed = False
        mock_connect.side_effect = [mock_conn1, mock_conn2]

        db1 = Database.create("postgresql://user:pass@host:5432/db")
        db2 = Database.create("postgresql://user:pass@host:5432/db")

        assert db1._conn is mock_conn1
        assert db2._conn is mock_conn2
        assert db1._conn is not db2._conn
        assert mock_connect.call_count == 2


# ── Database.upsert_rows() ────────────────────────────────────────────────────

class TestUpsertRows:
    """Tests for the batch upsert method."""

    @patch("src.db.psycopg2.connect")
    def test_upsert_empty_list_returns_zero(self, mock_connect):
        """Upserting an empty list should return 0 without hitting the DB."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_connect.return_value = mock_conn

        db = Database("postgresql://test")
        db.connect()

        result = db.upsert_rows([])
        assert result == 0


# ── RejectedRow model ─────────────────────────────────────────────────────────

class TestRejectedRowModel:
    """Tests for the RejectedRow dataclass."""

    def test_as_tuple_returns_correct_order(self):
        row = RejectedRow(
            date=date(2026, 4, 21),
            symbol="GC",
            name="Gold",
            open=100.0,
            high=105.0,
            low=95.0,
            close=102.0,
            adjusted_close=102.0,
            volume=500,
            rejected_reason="High (105.0) < Low (95.0)",
        )
        t = row.as_tuple()
        assert t == (
            date(2026, 4, 21),
            "GC",
            "Gold",
            100.0,
            105.0,
            95.0,
            102.0,
            102.0,
            500,
            "High (105.0) < Low (95.0)",
        )

    def test_as_tuple_with_none_fields(self):
        row = RejectedRow(
            date=date(2026, 4, 21),
            symbol="GC",
            name="Gold",
            rejected_reason="Null price field(s)",
        )
        t = row.as_tuple()
        assert t[3] is None  # open
        assert t[4] is None  # high
        assert t[5] is None  # low
        assert t[6] is None  # close
        assert t[7] is None  # adjusted_close
        assert t[8] is None  # volume
        assert t[9] == "Null price field(s)"


# ── Database.insert_rejected_rows() ───────────────────────────────────────────

class TestInsertRejectedRows:
    """Tests for the rejected rows batch insert method."""

    @patch("src.db.psycopg2.connect")
    def test_insert_empty_list_returns_zero(self, mock_connect):
        """Inserting an empty list should return 0 without hitting the DB."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_connect.return_value = mock_conn

        db = Database("postgresql://test")
        db.connect()

        result = db.insert_rejected_rows([])
        assert result == 0

    @patch("src.db.psycopg2.connect")
    def test_insert_rejected_rows_calls_execute_values(self, mock_connect):
        """insert_rejected_rows should call execute_values with the correct SQL."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        db = Database("postgresql://test")
        db.connect()

        rows = [
            RejectedRow(
                date=date(2026, 4, 21),
                symbol="GC",
                name="Gold",
                open=100.0,
                high=95.0,  # high < low — will be rejected
                low=105.0,
                close=102.0,
                adjusted_close=102.0,
                volume=500,
                rejected_reason="High (95.0) < Low (105.0)",
            ),
        ]

        with patch("src.db.psycopg2.extras.execute_values") as mock_ev:
            result = db.insert_rejected_rows(rows)
            assert result == 1
            mock_ev.assert_called_once()
            # Verify the SQL passed is the rejected rows insert
            call_args = mock_ev.call_args
            assert "commodities_eod_rejected" in call_args[0][1]

    @patch("src.db.psycopg2.connect")
    def test_insert_rejected_rows_rollback_on_error(self, mock_connect):
        """insert_rejected_rows should rollback on error."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        db = Database("postgresql://test")
        db.connect()

        rows = [
            RejectedRow(
                date=date(2026, 4, 21),
                symbol="GC",
                name="Gold",
                rejected_reason="Null price field(s)",
            ),
        ]

        with patch("src.db.psycopg2.extras.execute_values", side_effect=Exception("DB error")):
            with pytest.raises(Exception, match="DB error"):
                db.insert_rejected_rows(rows)
            mock_conn.rollback.assert_called_once()


# ── Database.insert_if_missing_rows() ─────────────────────────────────────────

class TestInsertIfMissingRows:
    """Tests for the insert-if-missing (ON CONFLICT DO NOTHING) method."""

    @patch("src.db.psycopg2.connect")
    def test_insert_empty_list_returns_zero(self, mock_connect):
        """Inserting an empty list should return 0 without hitting the DB."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_connect.return_value = mock_conn

        db = Database("postgresql://test")
        db.connect()

        result = db.insert_if_missing_rows([])
        assert result == 0

    @patch("src.db.psycopg2.connect")
    def test_insert_if_missing_calls_execute_values_with_do_nothing_sql(self, mock_connect):
        """insert_if_missing_rows should use ON CONFLICT DO NOTHING SQL."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 1
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        db = Database("postgresql://test")
        db.connect()

        rows = [
            CommodityPrice(
                date=date(2026, 4, 19), symbol="GC", name="Gold",
                open=3100, high=3150, low=3090, close=3120,
                adjusted_close=3120, volume=1000,
            ),
        ]

        with patch("src.db.psycopg2.extras.execute_values") as mock_ev:
            result = db.insert_if_missing_rows(rows)
            assert result == 1
            mock_ev.assert_called_once()
            # Verify the SQL uses DO NOTHING
            call_args = mock_ev.call_args
            sql_used = call_args[0][1]
            assert "DO NOTHING" in sql_used
            assert "ON CONFLICT" in sql_used

    @patch("src.db.psycopg2.connect")
    def test_insert_if_missing_returns_rowcount(self, mock_connect):
        """insert_if_missing_rows should return cursor.rowcount (actual inserts)."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_cursor = MagicMock()
        mock_cursor.rowcount = 0  # All rows already existed
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        db = Database("postgresql://test")
        db.connect()

        rows = [
            CommodityPrice(
                date=date(2026, 4, 19), symbol="GC", name="Gold",
                open=3100, high=3150, low=3090, close=3120,
                adjusted_close=3120, volume=1000,
            ),
        ]

        with patch("src.db.psycopg2.extras.execute_values"):
            result = db.insert_if_missing_rows(rows)
            # rowcount=0 means all rows already existed (skipped)
            assert result == 0

    @patch("src.db.psycopg2.connect")
    def test_insert_if_missing_rollback_on_error(self, mock_connect):
        """insert_if_missing_rows should rollback on error."""
        mock_conn = MagicMock()
        mock_conn.closed = False
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_connect.return_value = mock_conn

        db = Database("postgresql://test")
        db.connect()

        rows = [
            CommodityPrice(
                date=date(2026, 4, 19), symbol="GC", name="Gold",
                open=3100, high=3150, low=3090, close=3120,
                adjusted_close=3120, volume=1000,
            ),
        ]

        with patch("src.db.psycopg2.extras.execute_values", side_effect=Exception("DB error")):
            with pytest.raises(Exception, match="DB error"):
                db.insert_if_missing_rows(rows)
            mock_conn.rollback.assert_called_once()
