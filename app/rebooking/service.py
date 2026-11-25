"""Transactional use cases for rebooking balance allocation."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from typing import Any

from app.errors import DomainIssue
from app.intake.normalize import canonical
from app.rebooking.allocator import (
    _ID_KEYS,
    ALLOCATED,
    CANCELED,
    CONFIRMED,
    HELD,
    INSUFFICIENT_BALANCE,
    NormalizedOrder,
    decorate_result,
    extract_balance,
    normalize_orders,
    plan_allocations,
    resolve_today,
)
from app.rebooking.store import RebookStore, now_iso

ACTIVE_STATUSES = (ALLOCATED, CONFIRMED)


class RebookService:
    def __init__(self, store: RebookStore | None = None):
        self.store = store or RebookStore(":memory:")

    @classmethod
    def in_memory(cls) -> "RebookService":
        return cls(RebookStore(":memory:"))

    # ---------- helpers ----------
    @staticmethod
    def _loads(row: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(row.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            payload = {}
        data = dict(payload)
        data.update({key: value for key, value in row.items() if key != "payload"})
        return data

    def serialize_order(self, row: dict[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = self._loads(row)
        status = row["status"]
        amount = row["amount_cents"]
        allocated = row["allocated_cents"]
        result = decorate_result(
            data,
            status=status,
            allocated_cents=allocated,
            reason_code=row.get("reason_code"),
        )
        result.update({
            "revision": row["revision"],
            "idempotency_key": row.get("idempotency_key"),
            "refunded_cents": int(row.get("refunded_cents") or 0),
            "refundedCents": int(row.get("refunded_cents") or 0),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        })
        return result

    def ticket_summary(self, ticket_id: str) -> dict[str, Any]:
        ticket = self.store.get_ticket(ticket_id)
        if ticket is None:
            raise DomainIssue("TICKET_NOT_FOUND", 404)
        orders = [self.serialize_order(row) for row in self.store.list_orders(ticket_id)]
        return self._ticket_summary_from_rows(ticket, orders or [])

    @staticmethod
    def _ticket_summary_from_rows(ticket: dict[str, Any], orders: list[dict[str, Any]]) -> dict[str, Any]:
        balance = int(ticket["balance_cents"])
        confirmed = sum(int(o.get("allocated_cents") or 0) for o in orders if o.get("status") == CONFIRMED)
        reserved = sum(int(o.get("allocated_cents") or 0) for o in orders if o.get("status") == ALLOCATED)
        canceled_refunded = sum(int(o.get("refunded_cents") or 0) for o in orders if o.get("status") == CANCELED)
        available = max(0, balance - confirmed - reserved)
        return {
            "ticket_id": ticket["ticket_id"],
            "id": ticket["ticket_id"],
            # ``balance_cents`` is the current spendable balance.  The
            # seeded/top-up amount remains available under original/total keys.
            "balance_cents": available,
            "balance": available,
            "current_balance_cents": available,
            "available_balance_cents": available,
            "original_balance_cents": balance,
            "initial_balance_cents": balance,
            "total_balance_cents": balance,
            "available_cents": available,
            "remaining_balance_cents": available,
            "remaining_cents": available,
            "reserved_cents": reserved,
            "held_cents": reserved,
            "confirmed_cents": confirmed,
            "deducted_cents": confirmed + reserved,
            "occupied_cents": confirmed + reserved,
            "refunded_cents": canceled_refunded,
            "created_at": ticket.get("created_at"),
            "updated_at": ticket.get("updated_at"),
            "order_count": len(orders),
        }

    def workbench(self, ticket_id: str | None = None) -> dict[str, Any]:
        ticket_rows = self.store.list_tickets()
        all_order_rows = self.store.list_orders(ticket_id)
        orders = [self.serialize_order(row) for row in all_order_rows]
        tickets: list[dict[str, Any]] = []
        if ticket_id:
            ticket_row = self.store.get_ticket(ticket_id)
            if ticket_row is None:
                raise DomainIssue("TICKET_NOT_FOUND", 404)
            tickets.append(self._ticket_summary_from_rows(ticket_row, orders))
        else:
            by_ticket: dict[str, list[dict[str, Any]]] = {}
            for order in orders:
                by_ticket.setdefault(order["ticket_id"], []).append(order)
            for row in ticket_rows:
                tickets.append(self._ticket_summary_from_rows(row, by_ticket.get(row["ticket_id"], [])))

        events = self.store.list_events(ticket_id)
        total_balance = sum(t["original_balance_cents"] for t in tickets)
        total_available = sum(t["available_cents"] for t in tickets)
        total_deducted = sum(t["deducted_cents"] for t in tickets)
        status_counts: dict[str, int] = {}
        for order in orders:
            status_counts[order["status"]] = status_counts.get(order["status"], 0) + 1
        return {
            "schema": "rebooking-workbench/v1",
            "today": resolve_today().isoformat(),
            "tickets": tickets,
            "accounts": tickets,
            "orders": orders,
            "results": orders,
            "allocations": orders,
            "items": orders,
            "results_by_id": {order["order_id"]: order for order in orders},
            "order_results": {order["order_id"]: order for order in orders},
            "events": events,
            "ledger": events,
            "summary": {
                "ticket_count": len(tickets),
                "order_count": len(orders),
                "total_balance_cents": total_balance,
                "available_cents": total_available,
                "deducted_cents": total_deducted,
                "allocated_cents": total_deducted,
                "status_counts": status_counts,
            },
        }

    # ---------- account ----------
    def create_ticket(self, ticket_id: str, balance_cents: Any) -> dict[str, Any]:
        ticket_id = str(ticket_id or "").strip()
        if not ticket_id:
            raise DomainIssue("TICKET_ID_REQUIRED")
        from app.rebooking.allocator import parse_cents
        amount = parse_cents(balance_cents)
        if amount is None or amount < 0:
            raise DomainIssue("INVALID_BALANCE")
        self.store.upsert_ticket(ticket_id, amount)
        return self.ticket_summary(ticket_id)

    # ---------- submission ----------
    def submit(self, payload: Any = None, *, idempotency_key: str | None = None) -> dict[str, Any]:
        if isinstance(payload, list):
            payload = {"orders": payload}
        data = dict(payload or {})
        orders_raw = None
        for key in (
            "orders", "change_orders", "changeOrders",
            "rebooking_orders", "rebookingOrders",
            "reschedule_orders", "rescheduleOrders",
            "reissue_orders", "reissueOrders",
            "order_list", "orderList", "orders_list", "ordersList",
            "change_requests", "changeRequests",
            "rebooking_requests", "rebookingRequests",
            "requests", "applications", "items",
            "改期单", "改期单列表",
        ):
            if isinstance(data.get(key), list):
                orders_raw = data[key]
                break
        if orders_raw is None and isinstance(data.get("data"), list):
            orders_raw = data["data"]
        if orders_raw is None:
            # A single order may be posted directly.
            orders_raw = [data]
        if not isinstance(orders_raw, list):
            raise DomainIssue("BAD_REQUEST")

        default_ticket = (
            data.get("ticket_id") or data.get("ticketId")
            or data.get("original_ticket_id") or data.get("originalTicketId")
        )
        balance = extract_balance(data)
        today = resolve_today(data.get("today") or data.get("on_date"))

        normalized = normalize_orders(orders_raw, today)
        if not normalized:
            raise DomainIssue("ORDERS_REQUIRED")

        groups: dict[str, list[NormalizedOrder]] = {}
        for order in normalized:
            ticket_id = order.ticket_id or (str(default_ticket) if default_ticket else None)
            if not ticket_id:
                invalid = decorate_result(order, status="REJECTED", reason_code="TICKET_ID_REQUIRED")
                raise DomainIssue("TICKET_ID_REQUIRED", 400, json.dumps(invalid, ensure_ascii=False))
            has_id = any(canonical(order.raw.get(key)) for key in _ID_KEYS)
            changes: dict[str, Any] = {"ticket_id": ticket_id}
            if not has_id:
                changes["order_id"] = RebookStore.new_id("RB")
            order = replace(order, **changes)
            groups.setdefault(ticket_id, []).append(order)

        group_results = [self._submit_group(ticket_id, orders, balance, today, data, idempotency_key)
                         for ticket_id, orders in groups.items()]

        if len(group_results) == 1:
            result = group_results[0]
        else:
            all_results = [order for group in group_results for order in group["results"]]
            result = {
                "schema": "rebooking-allocation/v1",
                "today": today.isoformat(),
                "groups": group_results,
                "results": all_results,
                "orders": all_results,
                "allocations": all_results,
                "allocated_cents": sum(g["allocated_cents"] for g in group_results),
                "deducted_cents": sum(g["deducted_cents"] for g in group_results),
            }
        return result

    def _submit_group(self, ticket_id: str, orders: list[NormalizedOrder],
                      balance: int | None, today: Any, request_data: dict[str, Any],
                      idempotency_key: str | None) -> dict[str, Any]:
        store = self.store
        with store._lock:
            db = store.connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                if idempotency_key:
                    idem = db.execute(
                        "SELECT * FROM rebooking_orders WHERE idempotency_key=?",
                        (idempotency_key,),
                    ).fetchone()
                    if idem is not None:
                        db.commit()
                        idem_dict = dict(idem)
                        serialized = self.serialize_order(idem_dict)
                        return self.ticket_summary(idem_dict["ticket_id"]) | {
                            "order": serialized,
                            "results": [serialized],
                            "orders": [serialized],
                            "idempotent_replay": True,
                        }
                ticket = db.execute(
                    "SELECT * FROM rebooking_tickets WHERE ticket_id=?", (ticket_id,)
                ).fetchone()
                if ticket is None:
                    if balance is None:
                        raise DomainIssue("TICKET_NOT_FOUND", 404)
                    stamp = now_iso()
                    db.execute(
                        "INSERT INTO rebooking_tickets(ticket_id,balance_cents,created_at,updated_at) VALUES(?,?,?,?)",
                        (ticket_id, balance, stamp, stamp),
                    )
                    ticket = db.execute(
                        "SELECT * FROM rebooking_tickets WHERE ticket_id=?", (ticket_id,)
                    ).fetchone()
                elif balance is not None and int(ticket["balance_cents"]) != balance:
                    occupied = db.execute(
                        "SELECT COALESCE(SUM(allocated_cents),0) AS n FROM rebooking_orders "
                        "WHERE ticket_id=? AND status IN (?,?)",
                        (ticket_id, ALLOCATED, CONFIRMED),
                    ).fetchone()["n"]
                    if balance < occupied:
                        raise DomainIssue("BALANCE_CONFLICT", 409)
                    db.execute(
                        "UPDATE rebooking_tickets SET balance_cents=?, updated_at=? WHERE ticket_id=?",
                        (balance, now_iso(), ticket_id),
                    )
                    ticket = db.execute(
                        "SELECT * FROM rebooking_tickets WHERE ticket_id=?", (ticket_id,)
                    ).fetchone()

                # Idempotent retry by order IDs.
                existing_rows = []
                conflicts = []
                for order in orders:
                    row = db.execute(
                        "SELECT * FROM rebooking_orders WHERE order_id=?", (order.order_id,)
                    ).fetchone()
                    if row is not None:
                        if row["ticket_id"] != ticket_id or row["amount_cents"] != (order.amount_cents or -1):
                            conflicts.append(order.order_id)
                        existing_rows.append(dict(row))
                if conflicts:
                    raise DomainIssue("ORDER_CONFLICT", 409, ",".join(conflicts))
                if existing_rows and len(existing_rows) < len(orders):
                    raise DomainIssue("ORDER_CONFLICT", 409, "BATCH_CONTAINS_EXISTING_ORDER")
                if len(existing_rows) == len(orders):
                    db.commit()
                    return self.ticket_summary(ticket_id) | {
                        "results": [self.serialize_order(row) for row in existing_rows],
                        "orders": [self.serialize_order(row) for row in existing_rows],
                        "idempotent_replay": True,
                    }

                confirmed_row = db.execute(
                    "SELECT COALESCE(SUM(allocated_cents),0) AS n FROM rebooking_orders "
                    "WHERE ticket_id=? AND status=?",
                    (ticket_id, CONFIRMED),
                ).fetchone()
                confirmed_total = int(confirmed_row["n"])
                capacity = max(0, int(ticket["balance_cents"]) - confirmed_total)
                stamp = now_iso()

                # Insert incoming orders as unplanned, then optimize all
                # unconfirmed orders together.  CONFIRMED rows are excluded and
                # therefore remain immutable.  The write transaction makes
                # concurrent submissions serialize through this re-plan.
                for order in orders:
                    key = None
                    if idempotency_key and len(orders) == 1:
                        key = idempotency_key
                    elif request_data.get("idempotency_key") and len(orders) == 1:
                        key = str(request_data["idempotency_key"])
                    payload = json.dumps(order.as_dict(), ensure_ascii=False, default=str)
                    db.execute(
                        """
                        INSERT INTO rebooking_orders(
                          order_id,ticket_id,amount_cents,status,allocated_cents,is_today,
                          ticket_on,due_on,reason_code,idempotency_key,revision,payload,
                          created_at,updated_at)
                        VALUES(?,?,?,?,0,?,?,?, 'PENDING_PLAN', ?,0,?,?,?)
                        """,
                        (order.order_id, ticket_id, order.amount_cents or 0, HELD,
                         1 if order.is_today else 0, order.ticket_on, order.due_on,
                         key, payload, stamp, stamp),
                    )

                active_rows = [dict(row) for row in db.execute(
                    "SELECT * FROM rebooking_orders WHERE ticket_id=? AND status IN (?,?) "
                    "ORDER BY created_at, rowid",
                    (ticket_id, ALLOCATED, HELD),
                ).fetchall()]
                active_payloads = []
                preselected: set[str] = set()
                for row in active_rows:
                    try:
                        active_payloads.append(json.loads(row["payload"]))
                    except json.JSONDecodeError:
                        active_payloads.append({
                            "order_id": row["order_id"],
                            "amount_cents": row["amount_cents"],
                            "is_today": bool(row["is_today"]),
                            "ticket_on": row["ticket_on"],
                            "due_on": row["due_on"],
                        })
                    if row["status"] == ALLOCATED:
                        preselected.add(row["order_id"])

                plan = plan_allocations(
                    capacity, active_payloads, today=today, ticket_id=ticket_id,
                    preselected_ids=preselected,
                )
                selected = set(plan["selected_ids"])
                changed_ids: list[str] = []

                for row in active_rows:
                    oid = row["order_id"]
                    amount = int(row["amount_cents"])
                    should_allocate = oid in selected and amount > 0
                    new_revision = int(row["revision"]) + 1
                    if should_allocate:
                        if row["status"] == ALLOCATED and int(row["allocated_cents"]) == amount:
                            continue
                        db.execute(
                            "UPDATE rebooking_orders SET status=?, allocated_cents=amount_cents, "
                            "reason_code=NULL, revision=?, updated_at=? WHERE order_id=?",
                            (ALLOCATED, new_revision, stamp, oid),
                        )
                        db.execute(
                            "INSERT OR IGNORE INTO balance_events(event_id,idempotency_key,ticket_id,order_id,event_type,amount_cents,created_at) "
                            "VALUES(?,?,?,?, 'RESERVE', ?,?)",
                            (RebookStore.new_id("evt"), f"reserve:{oid}:r{new_revision}",
                             ticket_id, oid, amount, stamp),
                        )
                        changed_ids.append(oid)
                    else:
                        if row["status"] == HELD and int(row["allocated_cents"] or 0) == 0:
                            continue
                        old_allocated = int(row["allocated_cents"] or 0)
                        db.execute(
                            "UPDATE rebooking_orders SET status=?, allocated_cents=0, "
                            "reason_code=?, revision=?, updated_at=? WHERE order_id=?",
                            (HELD, INSUFFICIENT_BALANCE, new_revision, stamp, oid),
                        )
                        if old_allocated > 0:
                            db.execute(
                                "INSERT OR IGNORE INTO balance_events(event_id,idempotency_key,ticket_id,order_id,event_type,amount_cents,created_at) "
                                "VALUES(?,?,?,?, 'REBALANCE_RELEASE', ?,?)",
                                (RebookStore.new_id("evt"), f"rebalance-release:{oid}:r{new_revision}",
                                 ticket_id, oid, old_allocated, stamp),
                            )
                        changed_ids.append(oid)
                db.commit()
            except sqlite3.OperationalError as exc:
                db.rollback()
                if "locked" in str(exc).lower():
                    raise DomainIssue("BALANCE_BUSY", 409) from exc
                raise
            except DomainIssue:
                db.rollback()
                raise
            except Exception:
                db.rollback()
                raise
            finally:
                store.close(db)

        fresh_rows = store.list_orders(ticket_id)
        row_by_id = {row["order_id"]: row for row in fresh_rows}
        results = [self.serialize_order(row_by_id[order.order_id]) for order in orders]
        summary = self.ticket_summary(ticket_id)
        summary.update({
            "today": plan["today"],
            "allocated_cents": summary["deducted_cents"],
            "total_allocated_cents": summary["deducted_cents"],
            "unconfirmed_allocated_cents": plan["allocated_cents"],
            "plan": {key: plan[key] for key in (
                "schema", "selected_ids", "allocated_ids", "held_ids", "deferred_ids",
                "allocations", "deductions", "remaining_cents", "unused_cents")},
            "results": results,
            "orders": results,
            "allocations": results,
            "results_by_id": {item["order_id"]: item for item in results},
            "order_results": {item["order_id"]: item for item in results},
            "selected_ids": plan["selected_ids"],
            "allocated_ids": plan["allocated_ids"],
            "held_ids": plan["held_ids"],
            "deferred_ids": plan["deferred_ids"],
            "rebalanced_ids": changed_ids,
            "idempotent_replay": False,
        })
        return summary

    # ---------- actions ----------
    def _transition(self, order_id: str, action: str) -> dict[str, Any]:
        store = self.store
        with store._lock:
            db = store.connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM rebooking_orders WHERE order_id=?", (order_id,)
                ).fetchone()
                if row is None:
                    raise DomainIssue("ORDER_NOT_FOUND", 404)
                row = dict(row)
                ticket_id = row["ticket_id"]
                stamp = now_iso()

                if action == "confirm":
                    if row["status"] == CONFIRMED:
                        db.commit()
                        return {"order": self.serialize_order(store.get_order(order_id)),
                                "ticket": self.ticket_summary(ticket_id), "idempotent_replay": True}
                    if row["status"] != ALLOCATED:
                        raise DomainIssue("ORDER_NOT_CONFIRMABLE", 409)
                    db.execute(
                        "UPDATE rebooking_orders SET status=?, revision=revision+1, updated_at=? WHERE order_id=?",
                        (CONFIRMED, stamp, order_id),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO balance_events(event_id,idempotency_key,ticket_id,order_id,event_type,amount_cents,created_at) VALUES(?,?,?,?, 'CONFIRM', ?,?)",
                        (RebookStore.new_id("evt"), f"confirm:{order_id}", ticket_id,
                         order_id, row["allocated_cents"], stamp),
                    )

                elif action in {"cancel", "release", "refund"}:
                    if row["status"] == CANCELED:
                        db.commit()
                        events = db.execute(
                            "SELECT COUNT(*) AS n FROM balance_events WHERE idempotency_key=?",
                            (f"refund:{order_id}",),
                        ).fetchone()["n"]
                        return {"order": self.serialize_order(store.get_order(order_id)),
                                "ticket": self.ticket_summary(ticket_id),
                                "idempotent_replay": True,
                                "refund_events": int(events)}
                    if row["status"] == CONFIRMED:
                        raise DomainIssue("CONFIRMED_DEDUCTION_LOCKED", 409)
                    refund_amount = int(row["allocated_cents"] or 0)
                    db.execute(
                        "UPDATE rebooking_orders SET status=?, allocated_cents=0, refunded_cents=?, reason_code=?, revision=revision+1, updated_at=? WHERE order_id=?",
                        (CANCELED, refund_amount, "CANCELED", stamp, order_id),
                    )
                    if refund_amount > 0:
                        db.execute(
                            "INSERT OR IGNORE INTO balance_events(event_id,idempotency_key,ticket_id,order_id,event_type,amount_cents,created_at) VALUES(?,?,?,?, 'REFUND', ?,?)",
                            (RebookStore.new_id("evt"), f"refund:{order_id}", ticket_id,
                             order_id, refund_amount, stamp),
                        )

                elif action == "retry":
                    if row["status"] in {ALLOCATED, CONFIRMED}:
                        db.commit()
                        return {"order": self.serialize_order(store.get_order(order_id)),
                                "ticket": self.ticket_summary(ticket_id), "idempotent_replay": True}
                    if row["status"] == CANCELED:
                        raise DomainIssue("ORDER_CANCELED", 409)
                    ticket = db.execute(
                        "SELECT * FROM rebooking_tickets WHERE ticket_id=?", (ticket_id,)
                    ).fetchone()
                    occupied = db.execute(
                        "SELECT COALESCE(SUM(allocated_cents),0) AS n FROM rebooking_orders "
                        "WHERE ticket_id=? AND status IN (?,?) AND order_id<>?",
                        (ticket_id, ALLOCATED, CONFIRMED, order_id),
                    ).fetchone()["n"]
                    available = int(ticket["balance_cents"]) - int(occupied)
                    if int(row["amount_cents"]) > available:
                        db.execute(
                            "UPDATE rebooking_orders SET reason_code=?, revision=revision+1, updated_at=? WHERE order_id=?",
                            (INSUFFICIENT_BALANCE, stamp, order_id),
                        )
                        db.commit()
                        return {"order": self.serialize_order(store.get_order(order_id)),
                                "ticket": self.ticket_summary(ticket_id), "allocated": False}
                    db.execute(
                        "UPDATE rebooking_orders SET status=?, allocated_cents=amount_cents, reason_code=NULL, revision=revision+1, updated_at=? WHERE order_id=?",
                        (ALLOCATED, stamp, order_id),
                    )
                    db.execute(
                        "INSERT OR IGNORE INTO balance_events(event_id,idempotency_key,ticket_id,order_id,event_type,amount_cents,created_at) VALUES(?,?,?,?, 'RESERVE', ?,?)",
                        (RebookStore.new_id("evt"), f"reserve:{order_id}:r{row['revision'] + 1}",
                         ticket_id, order_id, row["amount_cents"], stamp),
                    )
                else:
                    raise DomainIssue("ACTION_UNKNOWN", 404)

                db.commit()
                fresh = store.get_order(order_id)
                return {
                    "order": self.serialize_order(fresh),
                    "ticket": self.ticket_summary(ticket_id),
                    "idempotent_replay": False,
                }
            except DomainIssue:
                db.rollback()
                raise
            except Exception:
                db.rollback()
                raise
            finally:
                store.close(db)

    def confirm(self, order_id: str) -> dict[str, Any]:
        return self._transition(order_id, "confirm")

    def cancel(self, order_id: str) -> dict[str, Any]:
        return self._transition(order_id, "cancel")

    release = cancel
    refund = cancel

    def retry(self, order_id: str) -> dict[str, Any]:
        return self._transition(order_id, "retry")

    def dispatch_waiting(self, ticket_id: str) -> dict[str, Any]:
        """Re-run exact optimization for all HELD orders on one ticket."""
        with self.store._lock:
            db = self.store.connect()
            try:
                db.execute("BEGIN IMMEDIATE")
                ticket = db.execute(
                    "SELECT * FROM rebooking_tickets WHERE ticket_id=?", (ticket_id,)
                ).fetchone()
                if ticket is None:
                    raise DomainIssue("TICKET_NOT_FOUND", 404)
                occupied = db.execute(
                    "SELECT COALESCE(SUM(allocated_cents),0) AS n FROM rebooking_orders "
                    "WHERE ticket_id=? AND status IN (?,?)",
                    (ticket_id, ALLOCATED, CONFIRMED),
                ).fetchone()["n"]
                held_rows = db.execute(
                    "SELECT * FROM rebooking_orders WHERE ticket_id=? AND status=? ORDER BY created_at,rowid",
                    (ticket_id, HELD),
                ).fetchall()
                available = int(ticket["balance_cents"]) - int(occupied)
                raw_orders = [json.loads(row["payload"]) for row in held_rows]
                plan = plan_allocations(available, raw_orders, ticket_id=ticket_id)
                by_id = {item["order_id"]: item for item in plan["results"]}
                stamp = now_iso()
                changed = []
                for row in held_rows:
                    candidate = by_id[row["order_id"]]
                    if candidate["status"] == ALLOCATED:
                        db.execute(
                            "UPDATE rebooking_orders SET status=?, allocated_cents=amount_cents, reason_code=NULL, revision=revision+1, updated_at=? WHERE order_id=?",
                            (ALLOCATED, stamp, row["order_id"]),
                        )
                        db.execute(
                            "INSERT OR IGNORE INTO balance_events(event_id,idempotency_key,ticket_id,order_id,event_type,amount_cents,created_at) VALUES(?,?,?,?, 'RESERVE', ?,?)",
                            (RebookStore.new_id("evt"), f"dispatch:{row['order_id']}:{row['revision'] + 1}",
                             ticket_id, row["order_id"], row["amount_cents"], stamp),
                        )
                        changed.append(row["order_id"])
                db.commit()
            except DomainIssue:
                db.rollback()
                raise
            finally:
                self.store.close(db)
        return self.workbench(ticket_id) | {"dispatched_ids": changed, "plan": plan}

    def seed_demo(self, reset: bool = False) -> dict[str, Any]:
        if reset:
            self.store.reset()
        existing = self.store.get_ticket("T-DEMO-PRIORITY")
        if existing and not reset:
            return self.workbench()
        today = resolve_today().isoformat()
        self.create_ticket("T-DEMO-PRIORITY", 10000)
        self.submit({
            "ticket_id": "T-DEMO-PRIORITY",
            "today": today,
            "orders": [
                {"order_id": "RB-TODAY-6000", "passenger_id": "P-TODAY-1",
                 "amount_cents": 6000, "issue_date": today, "due_date": today,
                 "note": "今天必须出票，可足额抵扣"},
                {"order_id": "RB-NEAR-5000", "passenger_id": "P-NEAR",
                 "amount_cents": 5000, "expire_date": today,
                 "note": "临期单，但会占用一半以上余额"},
                {"order_id": "RB-AUTO-4000", "passenger_id": "P-AUTO",
                 "amount_cents": 4000, "issue_date": today,
                 "note": "今天出票，剩余余额可足额抵扣"},
            ],
        })
        # Confirm one to demonstrate later applications cannot move it.
        self.confirm("RB-TODAY-6000")

        self.create_ticket("T-DEMO-KNAPSACK", 10000)
        self.submit({
            "ticket_id": "T-DEMO-KNAPSACK",
            "today": today,
            "orders": [
                {"order_id": "RB-BIG-6000", "amount_cents": 6000, "deadline": today,
                 "passenger_id": "P-BIG"},
                {"order_id": "RB-SMALL-A-5000", "amount_cents": 5000,
                 "passenger_id": "P-A"},
                {"order_id": "RB-SMALL-B-5000", "amount_cents": 5000,
                 "passenger_id": "P-B"},
            ],
        })
        return self.workbench()


# Common method-name aliases.
RebookService.allocate = RebookService.submit
RebookService.allocate_orders = RebookService.submit
RebookService.apply = RebookService.submit
RebookService.create_allocation = RebookService.submit
RebookService.submit_rebooking = RebookService.submit
RebookService.add_order = RebookService.submit
RebookService.get_workbench = RebookService.workbench
RebookService.results = RebookService.workbench
RebookService.confirm_allocation = RebookService.confirm
RebookService.cancel_allocation = RebookService.cancel
RebookService.release_balance = RebookService.cancel
RebookService.refund_balance = RebookService.cancel
RebookService.retry_allocation = RebookService.retry
RebookService.retry_refund = RebookService.cancel
RebookService.retry_cancel = RebookService.cancel
