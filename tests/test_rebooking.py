import threading
from concurrent.futures import ThreadPoolExecutor

from app.rebooking.allocator import plan_allocations


def test_today_fully_payable_orders_have_priority_over_near_expiry():
    result = plan_allocations(
        10_000,
        [
            {"order_id": "today-6000", "amount_cents": 6_000, "issue_date": "2026-11-25"},
            {"order_id": "near-5000", "amount_cents": 5_000, "expire_date": "2026-11-25"},
            {"order_id": "today-4000", "amount_cents": 4_000, "issue_date": "2026-11-25"},
        ],
        today="2026-11-25",
    )

    assert result["allocations"]["today-6000"] == 6_000
    assert result["allocations"]["today-4000"] == 4_000
    assert "near-5000" not in result["allocations"]
    assert next(x for x in result["results"] if x["order_id"] == "near-5000")["status"] == "HELD"


def test_remaining_balance_selects_maximum_whole_order_deduction():
    result = plan_allocations(
        10_000,
        [
            {"order_id": "greedy-6000", "amount_cents": 6_000},
            {"order_id": "pair-a-5000", "amount_cents": 5_000},
            {"order_id": "pair-b-5000", "amount_cents": 5_000},
        ],
        today="2026-11-25",
    )

    assert result["allocated_cents"] == 10_000
    assert set(result["selected_ids"]) == {"pair-a-5000", "pair-b-5000"}
    assert "greedy-6000" in result["held_ids"]


def test_incomplete_order_is_held_without_partial_deduction():
    result = plan_allocations(
        10_000,
        [
            {"order_id": "short-big", "amount_cents": 12_000, "issue_date": "2026-11-25"},
            {"order_id": "left-6000", "amount_cents": 6_000},
            {"order_id": "left-4000", "amount_cents": 4_000},
        ],
        today="2026-11-25",
    )

    big = next(x for x in result["results"] if x["order_id"] == "short-big")
    assert big["status"] == "HELD"
    assert big["allocated_cents"] == 0
    assert result["allocations"]["left-6000"] == 6_000
    assert result["allocations"]["left-4000"] == 4_000
    assert result["remaining_cents"] == 0


def test_confirmed_deduction_is_stable_and_cancel_releases_once_with_retry(client):
    create = client.post("/api/rebooking/tickets", json={
        "ticket_id": "T-1", "balance_cents": 10_000
    })
    assert create.status_code == 200

    submit = client.post("/api/rebooking/orders", json={
        "ticket_id": "T-1",
        "orders": [
            {"order_id": "A", "amount_cents": 6_000, "issue_date": "2026-11-25"},
            {"order_id": "B", "amount_cents": 4_000},
            {"order_id": "C", "amount_cents": 3_000},
        ],
        "today": "2026-11-25",
    })
    assert submit.status_code == 200
    body = submit.get_json()
    assert {x["order_id"] for x in body["results"] if x["allocated"]} == {"A", "B"}

    confirm = client.post("/api/rebooking/orders/A/confirm")
    assert confirm.status_code == 200
    assert confirm.get_json()["status"] == "CONFIRMED"

    later = client.post("/api/rebooking/orders", json={
        "ticket_id": "T-1", "order_id": "LATER", "amount_cents": 1_000
    })
    assert later.get_json()["results"][0]["status"] == "HELD"

    cancel = client.post("/api/rebooking/orders/B/cancel")
    assert cancel.status_code == 200
    assert cancel.get_json()["ticket"]["available_cents"] == 4_000
    assert cancel.get_json()["refunded_cents"] == 4_000

    cancel_retry = client.post("/api/rebooking/orders/B/cancel")
    payload = cancel_retry.get_json()
    assert cancel_retry.status_code == 200
    assert payload["idempotent_replay"] is True
    assert payload["ticket"]["available_cents"] == 4_000
    assert payload["refund_events"] == 1

    retry_c = client.post("/api/rebooking/orders/C/retry")
    assert retry_c.status_code == 200
    assert retry_c.get_json()["status"] == "ALLOCATED"
    assert retry_c.get_json()["allocated_cents"] == 3_000

    retry_c_again = client.post("/api/rebooking/orders/C/retry")
    assert retry_c_again.get_json()["idempotent_replay"] is True
    assert retry_c_again.get_json()["ticket"]["available_cents"] == 1_000

    wb = client.get("/api/rebooking/workbench?ticket_id=T-1").get_json()
    by_id = {x["order_id"]: x for x in wb["orders"]}
    assert by_id["A"]["status"] == "CONFIRMED"
    assert by_id["A"]["allocated_cents"] == 6_000
    assert by_id["B"]["status"] == "CANCELED"
    assert by_id["B"]["refunded_cents"] == 4_000
    assert by_id["C"]["status"] == "ALLOCATED"
    assert by_id["LATER"]["status"] == "HELD"
    refunds = [e for e in wb["events"] if e["event_type"] == "REFUND" and e["order_id"] == "B"]
    assert len(refunds) == 1


def test_concurrent_submissions_never_overuse_balance(client):
    client.post("/api/rebooking/tickets", json={"ticket_id": "T-CONC", "balance_cents": 10_000})

    def submit(i):
        return client.post("/api/rebooking/orders", json={
            "ticket_id": "T-CONC",
            "order_id": f"O-{i:02d}",
            "amount_cents": 1_000,
        })

    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(submit, range(20)))

    assert all(r.status_code == 200 for r in responses)
    wb = client.get("/api/rebooking/workbench?ticket_id=T-CONC").get_json()
    assert wb["tickets"][0]["available_cents"] == 0
    assert wb["summary"]["allocated_cents"] == 10_000
    allocated = [o for o in wb["orders"] if o["status"] in {"ALLOCATED", "CONFIRMED"}]
    assert len(allocated) == 10
