"""SQLite persistence for rebooking balances, orders and balance events."""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import DB_PATH


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RebookStore:
    def __init__(self, path: str | Path | None = None):
        self.path_text = str(path or __import__("os").getenv("WORKBENCH_DB", str(DB_PATH)))
        self.memory = self.path_text == ":memory:"
        self.path = None if self.memory else Path(path)
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._memory_db: sqlite3.Connection | None = None
        if self.memory:
            self._memory_db = sqlite3.connect(
                ":memory:", check_same_thread=False, isolation_level=None
            )
            self._memory_db.row_factory = sqlite3.Row
        self.init()

    def connect(self) -> sqlite3.Connection:
        if self.memory:
            assert self._memory_db is not None
            return self._memory_db
        connection = sqlite3.connect(self.path_text, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        return connection

    def close(self, connection: sqlite3.Connection) -> None:
        if not self.memory:
            connection.close()

    def init(self) -> None:
        sql = """
        CREATE TABLE IF NOT EXISTS rebooking_tickets(
          ticket_id TEXT PRIMARY KEY,
          balance_cents INTEGER NOT NULL CHECK(balance_cents >= 0),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rebooking_orders(
          order_id TEXT PRIMARY KEY,
          ticket_id TEXT NOT NULL,
          amount_cents INTEGER NOT NULL,
          status TEXT NOT NULL,
          allocated_cents INTEGER NOT NULL DEFAULT 0,
          refunded_cents INTEGER NOT NULL DEFAULT 0,
          is_today INTEGER NOT NULL DEFAULT 0,
          ticket_on TEXT,
          due_on TEXT,
          reason_code TEXT,
          idempotency_key TEXT,
          revision INTEGER NOT NULL DEFAULT 0,
          payload TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rebooking_orders_ticket
          ON rebooking_orders(ticket_id, created_at);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_rebooking_order_idem
          ON rebooking_orders(idempotency_key)
          WHERE idempotency_key IS NOT NULL;
        CREATE TABLE IF NOT EXISTS balance_events(
          event_id TEXT PRIMARY KEY,
          idempotency_key TEXT UNIQUE,
          ticket_id TEXT NOT NULL,
          order_id TEXT,
          event_type TEXT NOT NULL,
          amount_cents INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_balance_events_ticket
          ON balance_events(ticket_id, created_at);
        """
        with self._lock:
            db = self.connect()
            try:
                db.executescript(sql)
            finally:
                self.close(db)

    @staticmethod
    def new_id(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:12]}"

    def upsert_ticket(self, ticket_id: str, balance_cents: int) -> dict[str, Any]:
        stamp = now_iso()
        with self._lock:
            db = self.connect()
            try:
                db.execute(
                    """
                    INSERT INTO rebooking_tickets(ticket_id, balance_cents, created_at, updated_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(ticket_id) DO UPDATE SET
                      balance_cents=excluded.balance_cents,
                      updated_at=excluded.updated_at
                    """,
                    (ticket_id, int(balance_cents), stamp, stamp),
                )
                db.commit()
                row = self.get_ticket(ticket_id)
                assert row is not None
                return row
            finally:
                self.close(db)

    def get_ticket(self, ticket_id: str) -> dict[str, Any] | None:
        db = self.connect()
        try:
            row = db.execute(
                "SELECT * FROM rebooking_tickets WHERE ticket_id=?", (ticket_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            self.close(db)

    def list_tickets(self) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            rows = db.execute(
                "SELECT * FROM rebooking_tickets ORDER BY created_at, ticket_id"
            ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self.close(db)

    def get_order(self, order_id: str) -> dict[str, Any] | None:
        db = self.connect()
        try:
            row = db.execute(
                "SELECT * FROM rebooking_orders WHERE order_id=?", (order_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            self.close(db)

    def find_by_idempotency_key(self, key: str) -> dict[str, Any] | None:
        if not key:
            return None
        db = self.connect()
        try:
            row = db.execute(
                "SELECT * FROM rebooking_orders WHERE idempotency_key=?", (key,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            self.close(db)

    def list_orders(self, ticket_id: str | None = None) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            if ticket_id:
                rows = db.execute(
                    "SELECT * FROM rebooking_orders WHERE ticket_id=? ORDER BY created_at, rowid",
                    (ticket_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM rebooking_orders ORDER BY created_at, rowid"
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self.close(db)

    def list_events(self, ticket_id: str | None = None) -> list[dict[str, Any]]:
        db = self.connect()
        try:
            if ticket_id:
                rows = db.execute(
                    "SELECT * FROM balance_events WHERE ticket_id=? ORDER BY created_at, rowid",
                    (ticket_id,),
                ).fetchall()
            else:
                rows = db.execute(
                    "SELECT * FROM balance_events ORDER BY created_at, rowid"
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            self.close(db)

    def reset(self) -> None:
        with self._lock:
            db = self.connect()
            try:
                db.execute("DELETE FROM balance_events")
                db.execute("DELETE FROM rebooking_orders")
                db.execute("DELETE FROM rebooking_tickets")
                db.commit()
            finally:
                self.close(db)
