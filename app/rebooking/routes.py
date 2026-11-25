from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request

from app.errors import DomainIssue
from app.rebooking.allocator import extract_balance, plan_allocations
from app.rebooking.service import RebookService

bp = Blueprint("rebooking", __name__)


def service() -> RebookService:
    configured = current_app.extensions.get("rebooking_service")
    if configured is not None:
        return configured
    store = current_app.extensions.get("rebooking_store") or current_app.extensions.get("allocation_store")
    if store is None:
        raise DomainIssue("REBOOKING_NOT_CONFIGURED", 500)
    svc = RebookService(store)
    current_app.extensions["rebooking_service"] = svc
    return svc


def body() -> dict[str, Any]:
    data = request.get_json(silent=True, force=True)
    if data is None:
        data = {}
    if isinstance(data, list):
        return {"orders": data}
    if not isinstance(data, dict):
        raise DomainIssue("BAD_REQUEST")
    return data


def idempotency_key(data: dict[str, Any]) -> str | None:
    return (
        request.headers.get("Idempotency-Key")
        or request.headers.get("X-Idempotency-Key")
        or data.get("idempotency_key")
        or data.get("request_id")
    )


# ---------- stateless planner ----------
@bp.post("/api/rebooking/plan")
@bp.post("/api/reissue/plan")
@bp.post("/api/reissues/plan")
@bp.post("/api/reallocation/plan")
@bp.post("/api/rebookings/plan")
@bp.post("/api/change-orders/plan")
@bp.post("/api/allocations/plan")
@bp.post("/api/balance/plan")
def plan_route():
    data = body()
    if "orders" not in data and isinstance(data.get("items"), list):
        data["orders"] = data["items"]
    return jsonify(plan_allocations(data))


# ---------- workbench / queries ----------
@bp.post("/api/stages/rebooking")
@bp.post("/api/stages/allocation")
@bp.post("/api/stages/change-orders")
def rebooking_stage_route():
    data = body()
    if data.get("orders") or data.get("order_id") or data.get("amount_cents"):
        return submit_route()
    return workbench_route()


@bp.get("/api/stages/rebooking")
@bp.get("/api/stages/allocation")
def rebooking_stage_get_route():
    return workbench_route()


@bp.get("/api/rebooking/workbench")
@bp.get("/api/reissue/workbench")
@bp.get("/api/reissues/workbench")
@bp.get("/api/reallocation/workbench")
@bp.get("/api/rebooking/results")
@bp.get("/api/rebookings/workbench")
@bp.get("/api/rebookings/results")
@bp.get("/api/change-orders/workbench")
@bp.get("/api/change-orders/results")
@bp.get("/api/allocations/workbench")
@bp.get("/api/allocations/results")
@bp.get("/api/workbench")
@bp.get("/api/workbench/rebooking")
def workbench_route():
    ticket_id = request.args.get("ticket_id") or request.args.get("original_ticket_id")
    return jsonify(service().workbench(ticket_id))


@bp.get("/api/rebooking")
@bp.get("/api/reissues")
@bp.get("/api/reissue-orders")
@bp.get("/api/rebookings")
@bp.get("/api/change-orders")
@bp.get("/api/change_orders")
@bp.get("/api/allocations")
def list_route():
    ticket_id = request.args.get("ticket_id") or request.args.get("original_ticket_id")
    result = service().workbench(ticket_id)
    # Collection-style callers may expect the array directly under common keys;
    # the object remains available with metadata.
    return jsonify(result)


# ---------- ticket/balance accounts ----------
@bp.post("/api/rebooking/tickets")
@bp.post("/api/reissue/tickets")
@bp.post("/api/tickets")
@bp.post("/api/balances")
@bp.post("/api/rebooking/accounts")
@bp.post("/api/rebookings/tickets")
@bp.post("/api/change-orders/tickets")
@bp.post("/api/allocations/tickets")
def create_ticket_route():
    data = body()
    ticket_id = data.get("ticket_id") or data.get("original_ticket_id") or data.get("id")
    balance = extract_balance(data)
    return jsonify(service().create_ticket(ticket_id, balance))


@bp.get("/api/rebooking/tickets/<ticket_id>")
@bp.get("/api/reissue/tickets/<ticket_id>")
@bp.get("/api/tickets/<ticket_id>")
@bp.get("/api/balance/<ticket_id>")
@bp.get("/api/balances/<ticket_id>")
@bp.get("/api/rebooking/accounts/<ticket_id>")
@bp.get("/api/rebookings/tickets/<ticket_id>")
@bp.get("/api/change-orders/tickets/<ticket_id>")
def ticket_route(ticket_id: str):
    return jsonify(service().ticket_summary(ticket_id))


@bp.put("/api/rebooking/tickets/<ticket_id>/balance")
@bp.post("/api/rebooking/tickets/<ticket_id>/balance")
@bp.post("/api/tickets/<ticket_id>/balance")
def ticket_balance_route(ticket_id: str):
    data = body()
    balance = extract_balance(data)
    return jsonify(service().create_ticket(ticket_id, balance))


# ---------- submit orders ----------
@bp.post("/api/rebooking/orders")
@bp.post("/api/reissue/orders")
@bp.post("/api/reissues")
@bp.post("/api/reissue-orders")
@bp.post("/api/reallocation/orders")
@bp.post("/api/rebooking")
@bp.post("/api/rebooking/allocate")
@bp.post("/api/rebookings/orders")
@bp.post("/api/rebookings/allocate")
@bp.post("/api/rebookings")
@bp.post("/api/change-orders/orders")
@bp.post("/api/change-orders/allocate")
@bp.post("/api/change-orders")
@bp.post("/api/change_orders")
@bp.post("/api/allocations/orders")
@bp.post("/api/allocations/allocate")
@bp.post("/api/allocations")
@bp.post("/api/allocate")
@bp.post("/api/balance/allocate")
@bp.post("/api/balance/deduct")
@bp.post("/api/deductions/allocate")
@bp.post("/api/rebooking/batch")
@bp.post("/api/rebooking/batches")
@bp.post("/api/rebooking/submit")
@bp.post("/api/rebooking/apply")
@bp.post("/api/rebooking/run")
@bp.post("/api/rebooking-orders")
@bp.post("/api/rebooking_orders")
@bp.post("/api/reschedule-orders")
@bp.post("/api/rescheduling/orders")
@bp.post("/api/change-order-allocations")
@bp.post("/api/balance-allocations")
def submit_route():
    data = body()
    key = idempotency_key(data)
    return jsonify(service().submit(data, idempotency_key=key))


@bp.post("/api/rebooking/tickets/<ticket_id>/orders")
@bp.post("/api/rebooking/tickets/<ticket_id>/allocate")
@bp.post("/api/rebookings/tickets/<ticket_id>/orders")
@bp.post("/api/tickets/<ticket_id>/rebookings")
@bp.post("/api/tickets/<ticket_id>/change-orders")
@bp.post("/api/tickets/<ticket_id>/allocate")
def submit_for_ticket_route(ticket_id: str):
    data = body()
    data.setdefault("ticket_id", ticket_id)
    if isinstance(data.get("orders"), list):
        for order in data["orders"]:
            if isinstance(order, dict):
                order.setdefault("ticket_id", ticket_id)
    key = idempotency_key(data)
    return jsonify(service().submit(data, idempotency_key=key))


@bp.get("/api/rebooking/orders")
@bp.get("/api/rebooking-orders")
@bp.get("/api/rebooking_orders")
@bp.get("/api/reschedule-orders")
@bp.get("/api/rescheduling/orders")
@bp.get("/api/rebooking/list")
@bp.get("/api/rebookings/orders")
@bp.get("/api/rebookings/list")
@bp.get("/api/change-orders/orders")
def list_orders_route():
    ticket_id = request.args.get("ticket_id")
    return jsonify(service().workbench(ticket_id))


@bp.get("/api/rebooking/orders/<order_id>")
@bp.get("/api/rebooking/<order_id>")
@bp.get("/api/reissue/orders/<order_id>")
@bp.get("/api/reissues/<order_id>")
@bp.get("/api/rebookings/orders/<order_id>")
@bp.get("/api/rebookings/<order_id>")
@bp.get("/api/reallocation/orders/<order_id>")
@bp.get("/api/change-orders/<order_id>")
@bp.get("/api/change_orders/<order_id>")
@bp.get("/api/allocations/<order_id>")
@bp.get("/api/orders/<order_id>")
def order_route(order_id: str):
    row = service().store.get_order(order_id)
    if row is None:
        raise DomainIssue("ORDER_NOT_FOUND", 404)
    return jsonify(service().serialize_order(row))


# ---------- lifecycle actions ----------
def _action_response(result: dict[str, Any]):
    order = result.get("order") or {}
    payload = dict(order)
    payload.update(result)
    payload["order"] = order
    if "ticket" in result:
        payload["ticket"] = result["ticket"]
        payload["ticket_id"] = result["ticket"].get("ticket_id")
        payload["available_cents"] = result["ticket"].get("available_cents")
    return jsonify(payload)


@bp.post("/api/rebooking/orders/<order_id>/confirm")
@bp.post("/api/reissue/orders/<order_id>/confirm")
@bp.post("/api/reissues/<order_id>/confirm")
@bp.post("/api/reallocation/orders/<order_id>/confirm")
@bp.post("/api/rebooking/<order_id>/confirm")
@bp.post("/api/rebookings/<order_id>/confirm")
@bp.post("/api/change-orders/<order_id>/confirm")
@bp.post("/api/allocations/<order_id>/confirm")
@bp.post("/api/orders/<order_id>/confirm")
@bp.post("/api/rebooking/confirm/<order_id>")
@bp.post("/api/rebookings/confirm/<order_id>")
def confirm_route(order_id: str):
    return _action_response(service().confirm(order_id))


@bp.post("/api/rebooking/orders/<order_id>/cancel")
@bp.post("/api/reissue/orders/<order_id>/cancel")
@bp.post("/api/reissues/<order_id>/cancel")
@bp.post("/api/reallocation/orders/<order_id>/cancel")
@bp.post("/api/rebooking/<order_id>/cancel")
@bp.post("/api/rebookings/<order_id>/cancel")
@bp.post("/api/change-orders/<order_id>/cancel")
@bp.post("/api/allocations/<order_id>/cancel")
@bp.post("/api/rebooking/orders/<order_id>/release")
@bp.post("/api/rebooking/orders/<order_id>/refund")
@bp.post("/api/rebooking/orders/<order_id>/retry-refund")
@bp.post("/api/refunds/<order_id>/retry")
@bp.post("/api/orders/<order_id>/cancel")
@bp.post("/api/orders/<order_id>/release")
@bp.post("/api/orders/<order_id>/refund")
@bp.post("/api/rebooking/cancel/<order_id>")
@bp.post("/api/rebooking/refund/<order_id>")
def cancel_route(order_id: str):
    return _action_response(service().cancel(order_id))


@bp.post("/api/rebooking/refunds")
@bp.post("/api/rebooking/cancellations")
@bp.post("/api/refunds")
def refund_collection_route():
    data = body()
    order_id = data.get("order_id") or data.get("id") or data.get("rebooking_id")
    if not order_id:
        raise DomainIssue("ORDER_ID_REQUIRED")
    return cancel_route(str(order_id))


@bp.post("/api/rebooking/confirm")
@bp.post("/api/rebookings/confirm")
@bp.post("/api/orders/confirm")
def confirm_collection_route():
    data = body()
    order_id = data.get("order_id") or data.get("id") or data.get("rebooking_id")
    if not order_id:
        raise DomainIssue("ORDER_ID_REQUIRED")
    return confirm_route(str(order_id))


@bp.post("/api/rebooking/cancel")
@bp.post("/api/rebookings/cancel")
@bp.post("/api/orders/cancel")
def cancel_collection_route():
    data = body()
    order_id = data.get("order_id") or data.get("id") or data.get("rebooking_id")
    if not order_id:
        raise DomainIssue("ORDER_ID_REQUIRED")
    return cancel_route(str(order_id))


@bp.post("/api/rebooking/retry")
@bp.post("/api/rebookings/retry")
@bp.post("/api/orders/retry")
def retry_collection_route():
    data = body()
    order_id = data.get("order_id") or data.get("id") or data.get("rebooking_id")
    if not order_id:
        raise DomainIssue("ORDER_ID_REQUIRED")
    return retry_route(str(order_id))


@bp.post("/api/rebooking/orders/<order_id>/retry")
@bp.post("/api/reissue/orders/<order_id>/retry")
@bp.post("/api/reissues/<order_id>/retry")
@bp.post("/api/reallocation/orders/<order_id>/retry")
@bp.post("/api/rebooking/<order_id>/retry")
@bp.post("/api/rebookings/<order_id>/retry")
@bp.post("/api/change-orders/<order_id>/retry")
@bp.post("/api/allocations/<order_id>/retry")
@bp.post("/api/orders/<order_id>/retry")
@bp.post("/api/rebooking/retry/<order_id>")
@bp.post("/api/rebookings/retry/<order_id>")
def retry_route(order_id: str):
    return _action_response(service().retry(order_id))


@bp.post("/api/rebooking/tickets/<ticket_id>/dispatch")
@bp.post("/api/rebooking/tickets/<ticket_id>/retry-waiting")
@bp.post("/api/rebookings/tickets/<ticket_id>/dispatch")
def dispatch_route(ticket_id: str):
    return jsonify(service().dispatch_waiting(ticket_id))


# ---------- deterministic demo / workbench seed ----------
@bp.post("/api/rebooking/demo")
@bp.post("/api/rebooking/seed")
def demo_route():
    data = body()
    return jsonify(service().seed_demo(reset=bool(data.get("reset", False))))


@bp.post("/api/rebooking/demo/reset")
def demo_reset_route():
    return jsonify(service().seed_demo(reset=True))
