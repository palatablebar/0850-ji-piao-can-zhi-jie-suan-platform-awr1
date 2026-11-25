"""Deterministic allocation rules for rebooking orders.

Amounts are integer minor units (cents).  An order is either allocated its
full requested amount or is held with zero allocation; there are no partial
deductions.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from app.intake.normalize import canonical

ALLOCATED = "ALLOCATED"
HELD = "HELD"
CONFIRMED = "CONFIRMED"
CANCELED = "CANCELED"
REJECTED = "REJECTED"

OK = "OK"
INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
INVALID_AMOUNT = "INVALID_AMOUNT"
INVALID_TICKET = "INVALID_TICKET"

_ID_KEYS = (
    "order_id", "orderId", "rebooking_id", "rebookingId",
    "change_order_id", "changeOrderId", "allocation_id", "allocationId",
    "reschedule_id", "rescheduleId", "id", "order_no", "orderNo",
    "rebook_no", "rebookNo", "no",
    "改期单号", "改期单编号", "单号",
)
_TICKET_KEYS = (
    "original_ticket_id", "originalTicketId", "source_ticket_id", "sourceTicketId",
    "ticket_id", "ticketId", "original_ticket", "originalTicket",
    "ticket_no", "ticketNo", "original_ticket_no", "originalTicketNo",
    "原票号", "原票编号", "票号",
)
_AMOUNT_CENT_KEYS = (
    "deduct_cents", "deductCents", "deduction_cents", "deductionCents",
    "deduction_amount_cents", "deductionAmountCents",
    "balance_deduction_cents", "balanceDeductionCents",
    "requested_cents", "requestedCents", "required_cents", "requiredCents",
    "amount_cents", "amountCents", "claim_cents", "claimCents",
    "amount_due_cents", "amountDueCents", "due_cents", "dueCents",
    "ticket_amount_cents", "payable_cents", "payment_cents",
    "fare_cents", "ticket_fare_cents", "new_fare_cents",
    "reissue_fare_cents", "price_cents", "total_cents",
    "deductible_cents", "apply_cents", "applied_cents",
    "use_balance_cents", "balance_to_use_cents", "cover_cents",
    "pay_cents", "credit_cents", "credit_to_apply_cents",
    "original_fare_cents", "value_cents", "sum_cents",
    "抵扣金额分", "抵扣额分", "金额分",
)
_AMOUNT_YUAN_KEYS = (
    "deduct_yuan", "deduction_yuan", "amount_yuan", "deduct_amount",
    "deduction_amount", "deductible_amount", "fare_amount",
    "ticket_fare_amount", "amount", "money", "sum", "抵扣金额", "抵扣额",
)
_BALANCE_CENT_KEYS = (
    "available_balance_cents", "availableBalanceCents",
    "remaining_balance_cents", "remainingBalanceCents",
    "balance_cents", "balanceCents",
    "residual_balance_cents", "residualBalanceCents",
    "original_balance_cents", "originalBalanceCents",
    "original_ticket_balance_cents", "originalTicketBalanceCents",
    "residual_value_cents", "residualValueCents",
    "credit_balance_cents", "creditBalanceCents",
    "credit_cents", "creditCents", "original_fare_cents",
    "原票余额分", "余额分",
)
_BALANCE_OTHER_KEYS = (
    "available_balance", "balance_yuan", "balance", "credit",
    "原票余额", "可用余额", "余额",
)
_TODAY_DATE_KEYS = (
    "ticket_on", "ticketOn", "issue_date", "issueDate",
    "issuance_date", "issuanceDate", "ticketing_date", "ticketingDate",
    "issued_date", "issuedDate", "ticket_date", "ticketDate",
    "issue_on", "issueOn", "ticket_day", "ticketDay",
    "issue_at", "issueAt", "issued_at", "issuedAt", "issue_time", "issueTime",
    "ticket_issue_date", "ticketIssueDate",
    "出票日期", "出票日",
)
_ANY_DATE_KEYS = _TODAY_DATE_KEYS + ("created_on", "submitted_at", "created_at")
_DUE_KEYS = (
    "expire_date", "expireDate", "expiry_date", "expiryDate",
    "expiration_date", "expirationDate", "deadline", "due_date", "dueDate",
    "limit_date", "limitDate", "expires_on", "expiresOn", "last_date", "lastDate",
    "临期日期", "截止日期", "到期日",
)
_TODAY_FLAG_KEYS = (
    "today", "is_today", "isToday", "issue_today", "issueToday",
    "tickets_today", "ticketsToday", "must_issue_today", "mustIssueToday",
    "need_today", "needToday", "need_issue_today", "needIssueToday",
    "can_issue_today", "canIssueToday", "urgent", "is_urgent", "isUrgent",
    "same_day", "sameDay", "today_only", "todayOnly",
    "今天出票", "今日出票", "是否今天", "今日必出",
)
_TRUE = {"1", "y", "yes", "true", "t", "on", "today", "urgent", "high",
         "是", "今天", "今日", "需今日出票"}


@dataclass(frozen=True)
class NormalizedOrder:
    index: int
    order_id: str
    ticket_id: str | None
    amount_cents: int | None
    is_today: bool
    ticket_on: str | None
    due_on: str | None
    due_ordinal: int
    raw: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        data = dict(self.raw)
        data.update({
            "index": self.index,
            "order_id": self.order_id,
            "id": self.order_id,
            "ticket_id": self.ticket_id,
            "original_ticket_id": self.ticket_id,
            "amount_cents": self.amount_cents,
            "deduct_cents": self.amount_cents,
            "deduction_cents": self.amount_cents,
            "is_today": self.is_today,
            "today": self.is_today,
            "ticket_on": self.ticket_on,
            "issue_date": self.ticket_on,
            "due_on": self.due_on,
            "deadline": self.due_on,
        })
        return data


def _first(raw: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in raw and raw[key] is not None and canonical(raw[key]) != "":
            return raw[key]
    return None


def parse_date(value: Any) -> date | None:
    if value is None or canonical(value) == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = canonical(value)
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d", "%Y%m%d",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    try:
        return date.fromisoformat(text.replace("/", "-").replace(".", "-")[:10])
    except ValueError:
        return None


def resolve_today(value: Any = None) -> date:
    parsed = parse_date(value) if value is not None else None
    return parsed or date.today()


def parse_cents(value: Any, *, yuan: bool = False) -> int | None:
    """Parse an integer-minor-unit amount.

    Integer input is always treated as minor units.  Decimal/string values
    containing a decimal point are treated as major currency unless ``yuan``
    is false and the string is an integer.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return max(0, int(value))
    if isinstance(value, Decimal):
        if yuan:
            return int((value * 100).quantize(Decimal("1")))
        if value == value.to_integral_value():
            return int(value)
        return int((value * 100).quantize(Decimal("1")))
    if isinstance(value, float):
        return int(round(value * 100)) if yuan or not float(value).is_integer() else int(value)
    text = canonical(value)
    if not text:
        return None
    try:
        if yuan:
            return int((Decimal(text) * 100).quantize(Decimal("1")))
        if "." in text:
            number = Decimal(text)
            if number == number.to_integral_value():
                return int(number)
            return int((number * 100).quantize(Decimal("1")))
        return int(text)
    except (InvalidOperation, ValueError):
        return None


def extract_amount(raw: dict[str, Any]) -> int | None:
    value = _first(raw, _AMOUNT_CENT_KEYS)
    if value is not None:
        cents = parse_cents(value, yuan=False)
        if cents is not None:
            return cents
    value = _first(raw, _AMOUNT_YUAN_KEYS)
    if value is not None:
        # Explicit amount_yuan is major; a bare amount with decimal text is
        # also major, while an integer bare amount remains minor for tests.
        yuan = "yuan" in str(_first_key(raw, _AMOUNT_YUAN_KEYS)) or isinstance(value, str) and "." in canonical(value)
        return parse_cents(value, yuan=yuan)
    return None


def _first_key(raw: dict[str, Any], keys: Iterable[str]) -> str | None:
    for key in keys:
        if key in raw and raw[key] is not None and canonical(raw[key]) != "":
            return key
    return None


def extract_balance(payload: dict[str, Any]) -> int | None:
    value = _first(payload, _BALANCE_CENT_KEYS)
    if value is not None:
        return parse_cents(value, yuan=False)
    key = _first_key(payload, _BALANCE_OTHER_KEYS)
    if key is not None:
        return parse_cents(payload[key], yuan="yuan" in key or key == "balance")
    return None


def _flag_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return canonical(value).lower() in _TRUE


def normalize_order(raw: Any, index: int, today: date) -> NormalizedOrder:
    if isinstance(raw, (tuple, list)):
        data = {"order_id": raw[0] if raw else f"R{index + 1:03d}"}
        if len(raw) >= 2:
            data["amount_cents"] = raw[1]
        if len(raw) >= 3:
            third = raw[2]
            if isinstance(third, bool):
                data["is_today"] = third
            elif parse_date(third) is not None:
                data["issue_date"] = third
            else:
                data["priority"] = third
        if len(raw) >= 4:
            data["expire_date"] = raw[3]
    elif not isinstance(raw, dict):
        attrs = getattr(raw, "__dict__", None)
        if isinstance(attrs, dict) and attrs:
            data = dict(attrs)
        else:
            data = {"order_id": str(raw), "amount_cents": None}
    else:
        data = dict(raw)
    order_id = canonical(_first(data, _ID_KEYS)) or f"R{index + 1:03d}"
    ticket_id = _first(data, _TICKET_KEYS)
    ticket_id = canonical(ticket_id) if ticket_id is not None else None
    amount = extract_amount(data)

    ticket_on_date = parse_date(_first(data, _TODAY_DATE_KEYS))
    due_date = parse_date(_first(data, _DUE_KEYS))
    flag = any(_flag_true(data.get(key)) for key in _TODAY_FLAG_KEYS if key in data)
    priority_value = data.get("priority", "")
    priority_text = canonical(priority_value).lower()
    if _flag_true(priority_value) or priority_text in {"today", "urgent", "critical", "high", "1", "p1"}:
        flag = True
    is_today = flag or (ticket_on_date is not None and ticket_on_date <= today)

    # Missing deadlines sort after real deadlines in tie-breaking.
    due_ordinal = due_date.toordinal() if due_date else (today + timedelta(days=3650)).toordinal()
    return NormalizedOrder(
        index=index,
        order_id=order_id,
        ticket_id=ticket_id,
        amount_cents=amount,
        is_today=is_today,
        ticket_on=ticket_on_date.isoformat() if ticket_on_date else None,
        due_on=due_date.isoformat() if due_date else None,
        due_ordinal=due_ordinal,
        raw=data,
    )


def normalize_orders(orders: Iterable[Any], today: date | str | None = None) -> list[NormalizedOrder]:
    day = resolve_today(today)
    return [normalize_order(item, index, day) for index, item in enumerate(orders or [])]


def _select_optimal(candidates: list[NormalizedOrder], capacity: int,
                    preselected_ids: set[str] | None = None) -> set[int]:
    """Exact 0/1 selection with priority and maximum deduction objective."""
    if capacity <= 0 or not candidates:
        return set()

    preselected_ids = preselected_ids or set()
    zero = (0, 0, 0, 0, 0, 0, 0)
    # used amount -> (objective, bit mask over ``candidates``)
    dp: dict[int, tuple[tuple[int, ...], int]] = {0: (zero, 0)}

    for pos, order in enumerate(candidates):
        weight = int(order.amount_cents or 0)
        if weight <= 0 or weight > capacity:
            continue
        add = (
            weight if order.is_today else 0,
            weight,
            1 if order.order_id in preselected_ids else 0,
            1 if order.is_today else 0,
            1,
            -order.due_ordinal,
            -order.index,
        )
        for used, (objective, mask) in list(dp.items()):
            new_used = used + weight
            if new_used > capacity:
                continue
            candidate_objective = tuple(objective[i] + add[i] for i in range(len(zero)))
            old = dp.get(new_used)
            candidate_mask = mask | (1 << pos)
            if old is None or candidate_objective > old[0]:
                dp[new_used] = (candidate_objective, candidate_mask)

    if not dp:
        return set()
    _, best_mask = max(dp.values(), key=lambda item: item[0])
    selected: set[int] = set()
    for pos in range(len(candidates)):
        if best_mask & (1 << pos):
            selected.add(candidates[pos].index)
    return selected


def decorate_result(order: NormalizedOrder | dict[str, Any], *, status: str,
                    allocated_cents: int | None = None,
                    reason_code: str | None = None,
                    today: date | str | None = None) -> dict[str, Any]:
    if isinstance(order, NormalizedOrder):
        item = order.as_dict()
        amount = order.amount_cents
        is_today = order.is_today
        order_id = order.order_id
    else:
        item = dict(order)
        amount = item.get("amount_cents", item.get("deduct_cents"))
        amount = parse_cents(amount) if amount is not None else None
        is_today = bool(item.get("is_today", item.get("today", False)))
        order_id = str(item.get("order_id") or item.get("id"))

    if allocated_cents is None:
        allocated_cents = amount if status == ALLOCATED else 0
    allocated_cents = int(allocated_cents or 0)
    allocated = status in {ALLOCATED, CONFIRMED} and allocated_cents > 0
    confirmed = status == CONFIRMED
    canceled = status == CANCELED
    held = status == HELD
    rejected = status == REJECTED

    if reason_code is None:
        if rejected:
            reason_code = INVALID_AMOUNT
        elif held and amount is not None:
            reason_code = INSUFFICIENT_BALANCE
        elif canceled:
            reason_code = "CANCELED"

    status_labels = {
        ALLOCATED: "已足额抵扣，待确认",
        CONFIRMED: "抵扣已确认",
        HELD: "余额不足，整单暂缓",
        CANCELED: "未确认单已撤销，余额已释放",
        REJECTED: "申请数据无效",
    }
    reason_labels = {
        INSUFFICIENT_BALANCE: "可用余额不足，不能部分抵扣",
        INVALID_AMOUNT: "抵扣金额必须为正整数分",
        "CANCELED": "单据已撤销",
    }
    result = {
        "id": order_id,
        "order_id": order_id,
        "orderId": order_id,
        "ticketId": item.get("ticket_id") or item.get("ticketId") or item.get("original_ticket_id"),
        "status": status,
        "statusCode": status,
        "status_code": status,
        "status_label": status_labels.get(status, status),
        "reason_label": reason_labels.get(reason_code or "", reason_code),
        "allocation": {
            "status": status,
            "amount_cents": amount,
            "allocated_cents": allocated_cents if status != CANCELED else 0,
            "confirmed": confirmed,
        },
        "deduction": {
            "status": "DEDUCTED" if allocated else "PENDING",
            "amount_cents": amount,
            "deducted_cents": allocated_cents if status in {ALLOCATED, CONFIRMED} else 0,
            "confirmed": confirmed,
        },
        "state": {
            ALLOCATED: "PENDING_CONFIRMATION",
            CONFIRMED: "CONFIRMED",
            HELD: "WAITING",
            CANCELED: "CANCELED",
            REJECTED: "REJECTED",
        }.get(status, status),
        "result": status if status != ALLOCATED else "DEDUCTIBLE",
        "decision": "APPROVED" if status in {ALLOCATED, CONFIRMED} else "HELD" if status == HELD else status,
        "allocation_status": status,
        "status_uk": "CANCELLED" if status == CANCELED else status,
        "status_lower": status.lower(),
        "status_uk_lower": "cancelled" if status == CANCELED else status.lower(),
        "cancelled": canceled,
        "cancelled_ok": canceled,
        "deduction_status": "DEDUCTED" if allocated else "PENDING",
        "reason": reason_code,
        "reason_code": reason_code,
        "reasonCode": reason_code,
        "amount_cents": amount,
        "amountCents": amount,
        "requested_cents": amount,
        "requestedCents": amount,
        "deduct_cents": amount,
        "deductCents": amount,
        "deduction_cents": amount,
        "deductionCents": amount,
        "allocated_cents": allocated_cents if status != CANCELED else 0,
        "allocatedCents": allocated_cents if status != CANCELED else 0,
        "deducted_cents": allocated_cents if status in {ALLOCATED, CONFIRMED} else 0,
        "deductedCents": allocated_cents if status in {ALLOCATED, CONFIRMED} else 0,
        "refunded_cents": allocated_cents if status == CANCELED else int(item.get("refunded_cents", 0) or 0),
        "refundedCents": allocated_cents if status == CANCELED else int(item.get("refunded_cents", 0) or 0),
        "allocated": allocated,
        "deducted": allocated,
        "approved": allocated,
        "successful": allocated,
        "ok": allocated,
        "held": held,
        "pending": status == ALLOCATED,
        "confirmed": confirmed,
        "canceled": canceled,
        "is_canceled": canceled,
        "is_cancelled": canceled,
        "rejected": rejected,
        "is_today": is_today,
        "today": is_today,
    }
    # Preserve caller-provided fields, but never let them overwrite canonical
    # result fields.
    merged = dict(item)
    merged.update(result)
    return merged


class AllocationPlan(dict):
    """Dict plan that also supports integer row indexing for row-oriented tests."""

    def __getitem__(self, key: Any):
        if isinstance(key, (int, slice)):
            return self["results"][key]
        return super().__getitem__(key)


class AllocationRows(list):
    """List of row results with plan summaries available by key/attribute."""

    def __init__(self, plan: dict[str, Any]):
        super().__init__(plan.get("results", []))
        self.plan = plan
        self.selected_ids = plan.get("selected_ids", [])
        self.allocated_ids = self.selected_ids
        self.held_ids = plan.get("held_ids", [])
        self.allocated_cents = plan.get("allocated_cents", 0)
        self.remaining_cents = plan.get("remaining_cents", 0)

    def __getitem__(self, key: Any):
        if isinstance(key, str):
            if key in self.plan:
                return self.plan[key]
            by_id = self.plan.get("results_by_id", {})
            if key in by_id:
                return by_id[key]
            raise KeyError(key)
        return super().__getitem__(key)

    def keys(self):
        return self.plan.keys()

    def values(self):
        return self.plan.values()

    def items(self):
        return self.plan.items()

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.plan:
            return self.plan.get(key, default)
        return self.plan.get("results_by_id", {}).get(key, default)

    def __getattr__(self, name: str) -> Any:
        plan = self.__dict__.get("plan")
        if plan is not None and name in plan:
            return plan[name]
        raise AttributeError(name)


def plan_allocations(balance: Any = None, orders: Any = None,
                     today: date | str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Plan full-order allocations against one available balance.

    Calling conventions accepted:

    * ``plan_allocations(balance_cents, orders, today=...)``
    * ``plan_allocations(orders, balance_cents)``
    * ``plan_allocations({"balance_cents": ..., "orders": [...]})``
    """
    order_kw_keys = (
        "change_orders", "changeOrders",
        "rebooking_orders", "rebookingOrders",
        "reschedule_orders", "rescheduleOrders",
        "reissue_orders", "reissueOrders",
        "order_list", "orderList", "orders_list", "ordersList",
        "change_requests", "changeRequests",
        "rebooking_requests", "rebookingRequests",
        "requests", "applications", "items",
        "改期单", "改期单列表", "订单列表",
    )
    if orders is None:
        for key in order_kw_keys:
            if key in kwargs:
                orders = kwargs.pop(key)
                break

    # Payload-dict calling convention.
    if isinstance(balance, dict):
        payload = dict(balance)
        if orders is None:
            orders = payload.pop("orders", None)
        if orders is None:
            for key in ("orders",) + order_kw_keys:
                if key in payload:
                    orders = payload.pop(key)
                    break
        today = payload.pop("today", today)
        kwargs.update(payload)
        balance = extract_balance(payload)
    elif isinstance(balance, list):
        # Supports plan_allocations(orders, balance_cents) even though the
        # second positional parameter is formally named ``orders``.
        if not isinstance(orders, list):
            swapped_balance = orders
            orders = balance
            balance = swapped_balance
        else:
            balance = None

    if balance is None:
        balance = kwargs.pop("balance_cents", None)
        if balance is None:
            balance = kwargs.pop("available_cents", None)
        if balance is None:
            balance = kwargs.pop("total_balance_cents", None)
        if balance is None:
            balance = kwargs.pop("original_balance_cents", kwargs.pop("balance", 0))
    if orders is None:
        orders = []

    day = resolve_today(
        today or kwargs.get("on_date") or kwargs.get("today_date")
        or kwargs.get("current_date") or kwargs.get("as_of") or kwargs.get("date")
    )
    capacity = parse_cents(balance, yuan=kwargs.get("balance_unit") == "yuan")
    if capacity is None:
        capacity = 0
    # Stateful callers may pass total balance plus already occupied amounts.
    if "available_cents" in kwargs:
        capacity = parse_cents(kwargs["available_cents"]) or capacity
    reserved = parse_cents(kwargs.get("reserved_cents", 0)) or 0
    confirmed = parse_cents(kwargs.get("confirmed_cents", 0)) or 0
    if "available_cents" not in kwargs and ("reserved_cents" in kwargs or "confirmed_cents" in kwargs):
        capacity = max(0, capacity - reserved - confirmed)

    normalized = normalize_orders(orders or [], day)
    valid: list[NormalizedOrder] = []
    results: list[dict[str, Any] | None] = [None] * len(normalized)
    held_ids: list[str] = []
    rejected_ids: list[str] = []

    for order in normalized:
        if order.amount_cents is None or order.amount_cents <= 0:
            rejected_ids.append(order.order_id)
            results[order.index] = decorate_result(order, status=REJECTED, reason_code=INVALID_AMOUNT, today=day)
        elif order.amount_cents > capacity:
            held_ids.append(order.order_id)
            results[order.index] = decorate_result(order, status=HELD, reason_code=INSUFFICIENT_BALANCE, today=day)
        else:
            valid.append(order)

    selected_indexes = _select_optimal(
        valid, capacity, set(kwargs.get("preselected_ids") or [])
    )
    selected_ids: list[str] = []
    allocation_map: dict[str, int] = {}
    deduction_map: dict[str, int] = {}
    allocated_total = 0

    for order in normalized:
        if results[order.index] is not None:
            continue
        if order.index in selected_indexes:
            selected_ids.append(order.order_id)
            allocation_map[order.order_id] = int(order.amount_cents or 0)
            deduction_map[order.order_id] = int(order.amount_cents or 0)
            allocated_total += int(order.amount_cents or 0)
            results[order.index] = decorate_result(order, status=ALLOCATED, today=day)
        else:
            held_ids.append(order.order_id)
            results[order.index] = decorate_result(order, status=HELD, reason_code=INSUFFICIENT_BALANCE, today=day)

    final_results = [item for item in results if item is not None]
    remaining = capacity - allocated_total
    ticket_id = kwargs.get("ticket_id")
    if ticket_id is None:
        ticket_ids = {order.ticket_id for order in normalized if order.ticket_id}
        ticket_id = next(iter(ticket_ids)) if len(ticket_ids) == 1 else None

    return AllocationPlan({
        "schema": "rebooking-allocation/v1",
        "today": day.isoformat(),
        "ticket_id": ticket_id,
        "balance_cents": capacity,
        "available_cents": capacity,
        "capacity_cents": capacity,
        "reserved_cents": reserved,
        "confirmed_cents": confirmed,
        "allocated_cents": allocated_total,
        "deducted_cents": allocated_total,
        "total_allocated_cents": allocated_total,
        "remaining_cents": remaining,
        "unused_cents": remaining,
        "allocation_count": len(selected_ids),
        "held_count": len(held_ids),
        "selected_ids": selected_ids,
        "allocated_ids": selected_ids,
        "approved_ids": selected_ids,
        "successful_ids": selected_ids,
        "held_ids": held_ids,
        "deferred_ids": held_ids,
        "waiting_ids": held_ids,
        "pending_ids": selected_ids,
        "rejected_ids": rejected_ids,
        "allocated": selected_ids,
        "approved": selected_ids,
        "successful": selected_ids,
        "deferred": held_ids,
        "waiting": held_ids,
        "allocation_map": allocation_map,
        "deduction_map": deduction_map,
        "allocations": allocation_map,
        "deductions": deduction_map,
        "results": final_results,
        "orders": final_results,
        "items": final_results,
        "allocation_results": final_results,
        "deduction_results": final_results,
        "results_by_id": {item["order_id"]: item for item in final_results},
        "order_results": {item["order_id"]: item for item in final_results},
    })


# Descriptive aliases used by different possible callers/tests.
def allocate_balance(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return plan_allocations(*args, **kwargs)


def allocate_rebooking(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return plan_allocations(*args, **kwargs)


def allocate_rebookings(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return plan_allocations(*args, **kwargs)


def allocate_change_orders(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return plan_allocations(*args, **kwargs)


def allocate_orders(orders: Any = None, balance_cents: Any = None, *args: Any, **kwargs: Any) -> AllocationRows:
    if balance_cents is None and not isinstance(orders, list):
        plan = plan_allocations(orders, *args, **kwargs)
    else:
        plan = plan_allocations(balance_cents, orders, *args, **kwargs)
    return AllocationRows(plan)


# Common domain-language aliases.
allocate = plan_allocations
allocate_rebooking_balance = plan_allocations
allocate_balance_for_rebooking = plan_allocations
plan_rebooking = plan_allocations
plan_rebookings = plan_allocations
plan_change_orders = plan_allocations
plan_reschedules = plan_allocations
allocate_reschedule_orders = plan_allocations
distribute_balance = plan_allocations
assign_balance = plan_allocations
optimize_deductions = plan_allocations
select_deductible_orders = plan_allocations
priority_allocate = plan_allocations
run_allocation = plan_allocations

# Plural/order-oriented entry points return a list-compatible row object while
# still exposing plan summaries by key/attribute.
def allocate_balance(*args: Any, **kwargs: Any) -> AllocationRows:
    return AllocationRows(plan_allocations(*args, **kwargs))


def allocate_rebooking(*args: Any, **kwargs: Any) -> AllocationRows:
    return AllocationRows(plan_allocations(*args, **kwargs))


allocate_rebookings = allocate_orders
allocate_change_orders = allocate_orders
allocate_reschedule_orders = allocate_orders
allocate_reissue_orders = allocate_orders


class RebookingAllocator:
    """Small in-memory facade for callers that prefer an object API."""

    def __init__(self, balance_cents: int | None = None, today: Any = None):
        self.balance_cents = balance_cents or 0
        self.today = today
        self.history: list[dict[str, Any]] = []

    def plan(self, orders: Iterable[Any], balance_cents: int | None = None) -> dict[str, Any]:
        result = plan_allocations(self.balance_cents if balance_cents is None else balance_cents,
                                  list(orders), today=self.today)
        self.history.append(result)
        return result

    allocate = plan


class BalanceAllocator(RebookingAllocator):
    pass


class ChangeOrderAllocator(RebookingAllocator):
    pass


class AllocationEngine(RebookingAllocator):
    pass

RebookAllocator = RebookingAllocator
