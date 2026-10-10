"""
test_dev1_issues.py - Comprehensive tests for POST /issues (Backend Dev 1)

Plain Python - no pytest. Prints PASS for every test that succeeds.
Run: py -3 test_dev1_issues.py

Uses a fresh temporary SQLite database populated with seed.py.
Every test uses its own new parts with ids >= 9300 and its own work orders
with ids >= 9300.
"""
import json
import os
import re
import sys
import tempfile
import threading
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── 1. Boot the temp DB before importing db or main ─────────────────────────
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["INVENTORY_DB"] = _tmp.name

import db
import seed
seed.seed()

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)

# ── helpers ──────────────────────────────────────────────────────────────────
_passed = 0
_total  = 0

def ok(name: str):
    global _passed, _total
    _passed += 1
    _total  += 1
    print(f"  [PASS] {name}")

def fail(name: str, detail: str):
    global _total
    _total += 1
    print(f"  [FAIL] {name}: {detail}", file=sys.stderr)
    raise AssertionError(f"{name}: {detail}")

def check(name: str, condition: bool, detail: str = ""):
    if condition:
        ok(name)
    else:
        fail(name, detail or "condition is False")

def insert_part(c, part_id: int, name: str = "Test Part"):
    c.execute(
        "INSERT OR IGNORE INTO parts (part_id, sku, sku_norm, name, family, grp, subgroup, unit_name, active) "
        "VALUES (?, ?, ?, ?, 'TEST', 'TEST', 'TEST', 'PIEZA', 1)",
        (part_id, f"SKU-{part_id}", f"SKU-{part_id}", name),
    )

def insert_work_order(c, work_order_id: int, code: str, deleted: int = 0):
    c.execute(
        "INSERT OR IGNORE INTO work_orders (work_order_id, code, model_id, department, stage, received_at, deleted) "
        "VALUES (?, ?, 301, 'test_dept', 'assembly', '2026-10-01T08:00:00Z', ?)",
        (work_order_id, code, deleted),
    )


# ══════════════════════════════════════════════════════════════════════════════
# TESTS (1 - 19)
# ══════════════════════════════════════════════════════════════════════════════

# ── Test 1: Official scenario "salida sin negativos" ─────────────────────────
print("\n[Test 1] Official scenario 'salida sin negativos'")
PART_1 = 9301
WO_ID_1 = 9301
WO_CODE_1 = "R999999901"
with db.tx() as c:
    insert_part(c, PART_1)
    insert_work_order(c, WO_ID_1, WO_CODE_1)
    db.record_movement(c, "receipt", PART_1, 100, 2, "2026-10-01T00:00:00Z")

# Issue 3 gives 409 and writes nothing
res_fail = client.post("/issues", json={
    "work_order_code": WO_CODE_1,
    "part_id": PART_1,
    "location_id": 100,
    "quantity": 3,
    "issued_by": "op-1",
})
check("T1 issue 3 gives 409", res_fail.status_code == 409, f"Got {res_fail.status_code}: {res_fail.text}")
check("T1 409 detail is str", isinstance(res_fail.json().get("detail"), str))
check("T1 no extra ledger row on 409", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_1,))) == 1)
check("T1 balance unchanged at 2", db.on_hand_at(PART_1, 100) == 2)
check("T1 no outbox event on 409", len(db.query("SELECT * FROM outbox WHERE key = ?", (WO_CODE_1,))) == 0)

# Issue 2 gives 201
res_ok = client.post("/issues", json={
    "work_order_code": WO_CODE_1,
    "part_id": PART_1,
    "location_id": 100,
    "quantity": 2,
    "issued_by": "op-1",
})
check("T1 issue 2 gives 201", res_ok.status_code == 201, f"Got {res_ok.status_code}: {res_ok.text}")
data1 = res_ok.json()
check("T1 quantity is negative (-2)", data1.get("quantity") == -2, str(data1))
check("T1 location_id is 100", data1.get("location_id") == 100, str(data1))
check("T1 reference is order code", data1.get("reference") == WO_CODE_1, str(data1))
check("T1 type is 'issue'", data1.get("type") == "issue", str(data1))
check("T1 on_hand ends at 0", db.on_hand_at(PART_1, 100) == 0)


# ── Test 2: Outbox ───────────────────────────────────────────────────────────
print("\n[Test 2] Outbox: exactly one stock.issued, exact keys, published = 0")
PART_2 = 9302
WO_ID_2 = 9302
WO_CODE_2 = "R999999902"
with db.tx() as c:
    insert_part(c, PART_2)
    insert_work_order(c, WO_ID_2, WO_CODE_2)
    db.record_movement(c, "receipt", PART_2, 100, 5, "2026-10-01T00:00:00Z")

res2 = client.post("/issues", json={
    "work_order_code": WO_CODE_2,
    "part_id": PART_2,
    "location_id": 100,
    "quantity": 2,
    "issued_by": "operador-custom",
})
check("T2 issue gives 201", res2.status_code == 201, f"Got {res2.status_code}: {res2.text}")
outbox_rows = db.query("SELECT * FROM outbox WHERE key = ?", (WO_CODE_2,))
check("T2 exactly one outbox row for order", len(outbox_rows) == 1, f"Got {len(outbox_rows)}")
row2 = outbox_rows[0]
check("T2 event_type is stock.issued", row2["event_type"] == "stock.issued")
check("T2 key is order code", row2["key"] == WO_CODE_2)
check("T2 published == 0", row2["published"] == 0)
data_parsed = json.loads(row2["data"])
check("T2 exact data keys", set(data_parsed.keys()) == {"work_order_id", "part_id", "location_id", "quantity", "issued_by"}, str(data_parsed))
check("T2 data work_order_id matches", data_parsed["work_order_id"] == WO_ID_2)
check("T2 data part_id matches", data_parsed["part_id"] == PART_2)
check("T2 data location_id matches", data_parsed["location_id"] == 100)
check("T2 data quantity is positive 2", data_parsed["quantity"] == 2)
check("T2 data issued_by matches", data_parsed["issued_by"] == "operador-custom")


# ── Test 3: Official scenario "reserva y salida" ─────────────────────────────
print("\n[Test 3] Official scenario 'reserva y salida': stock 8, reservation 8, issue 8 -> fulfilled")
PART_3 = 9303
WO_ID_3 = 9303
WO_CODE_3 = "R999999903"
with db.tx() as c:
    insert_part(c, PART_3)
    insert_work_order(c, WO_ID_3, WO_CODE_3)
    db.record_movement(c, "receipt", PART_3, 100, 8, "2026-10-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 8, 0, 'active')",
        (WO_ID_3, PART_3),
    )

res3 = client.post("/issues", json={
    "work_order_code": WO_CODE_3,
    "part_id": PART_3,
    "location_id": 100,
    "quantity": 8,
    "issued_by": "op-3",
})
check("T3 issue 8 gives 201", res3.status_code == 201, f"Got {res3.status_code}: {res3.text}")
res_row3 = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ?", (WO_ID_3, PART_3))
check("T3 reservation issued_quantity is 8", res_row3["issued_quantity"] == 8)
check("T3 reservation status is 'fulfilled'", res_row3["status"] == "fulfilled")
r_av3 = client.get(f"/parts/{PART_3}/availability").json()
check("T3 availability on_hand == 0", r_av3["on_hand"] == 0, str(r_av3))
check("T3 availability reserved == 0", r_av3["reserved"] == 0, str(r_av3))
check("T3 availability available == 0", r_av3["available"] == 0, str(r_av3))


# ── Test 4: Partial issue ────────────────────────────────────────────────────
print("\n[Test 4] Partial issue: reservation 8, issue 3 -> issued 3, active, reserved 5, available unchanged")
PART_4 = 9304
WO_ID_4 = 9304
WO_CODE_4 = "R999999904"
with db.tx() as c:
    insert_part(c, PART_4)
    insert_work_order(c, WO_ID_4, WO_CODE_4)
    db.record_movement(c, "receipt", PART_4, 100, 10, "2026-10-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 8, 0, 'active')",
        (WO_ID_4, PART_4),
    )

# Before issue: on_hand 10, reserved 8, available 2
avail_before4 = client.get(f"/parts/{PART_4}/availability").json()
check("T4 available before issue is 2", avail_before4["available"] == 2, str(avail_before4))

res4 = client.post("/issues", json={
    "work_order_code": WO_CODE_4,
    "part_id": PART_4,
    "location_id": 100,
    "quantity": 3,
    "issued_by": "op-4",
})
check("T4 issue 3 gives 201", res4.status_code == 201, f"Got {res4.status_code}: {res4.text}")
res_row4 = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ?", (WO_ID_4, PART_4))
check("T4 reservation issued_quantity is 3", res_row4["issued_quantity"] == 3)
check("T4 reservation status is still 'active'", res_row4["status"] == "active")

avail_after4 = client.get(f"/parts/{PART_4}/availability").json()
check("T4 availability on_hand is 7", avail_after4["on_hand"] == 7, str(avail_after4))
check("T4 availability reserved is 5", avail_after4["reserved"] == 5, str(avail_after4))
check("T4 availability available is still 2", avail_after4["available"] == 2, str(avail_after4))


# ── Test 5: Protection of other orders' stock ────────────────────────────────
print("\n[Test 5] Protection of other orders' stock: stock 5, B holds 5, A has none")
PART_5 = 9305
WO_A_ID_5 = 9305
WO_A_CODE_5 = "R999999905A"
WO_B_ID_5 = 9306
WO_B_CODE_5 = "R999999905B"
with db.tx() as c:
    insert_part(c, PART_5)
    insert_work_order(c, WO_A_ID_5, WO_A_CODE_5)
    insert_work_order(c, WO_B_ID_5, WO_B_CODE_5)
    db.record_movement(c, "receipt", PART_5, 100, 5, "2026-10-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 5, 0, 'active')",
        (WO_B_ID_5, PART_5),
    )

# A issuing 1 gives 409
res5_fail = client.post("/issues", json={
    "work_order_code": WO_A_CODE_5,
    "part_id": PART_5,
    "location_id": 100,
    "quantity": 1,
    "issued_by": "op-a",
})
check("T5 A issue 1 gives 409", res5_fail.status_code == 409, f"Got {res5_fail.status_code}: {res5_fail.text}")
check("T5 nothing written on 409 (ledger count == 1)", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_5,))) == 1)
check("T5 stock balance untouched at 5", db.on_hand_at(PART_5, 100) == 5)
res_b5 = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ?", (WO_B_ID_5, PART_5))
check("T5 B reservation untouched on 409", res_b5["issued_quantity"] == 0 and res_b5["status"] == "active")

# Increase stock to 6 (available becomes 1)
with db.tx() as c:
    db.record_movement(c, "receipt", PART_5, 100, 1, "2026-10-01T01:00:00Z")

res5_ok = client.post("/issues", json={
    "work_order_code": WO_A_CODE_5,
    "part_id": PART_5,
    "location_id": 100,
    "quantity": 1,
    "issued_by": "op-a",
})
check("T5 A issue 1 gives 201 with stock 6", res5_ok.status_code == 201, f"Got {res5_ok.status_code}: {res5_ok.text}")
res_b5_after = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ?", (WO_B_ID_5, PART_5))
check("T5 B reservation untouched after A issue", res_b5_after["issued_quantity"] == 0 and res_b5_after["status"] == "active")


# ── Test 6: Mixed case ───────────────────────────────────────────────────────
print("\n[Test 6] Mixed case: A reserved 3, B reserved 2, stock 5 -> A issuing 4 gives 409; with stock 6 gives 201")
PART_6 = 9306
WO_A_ID_6 = 9307
WO_A_CODE_6 = "R999999906A"
WO_B_ID_6 = 9308
WO_B_CODE_6 = "R999999906B"
with db.tx() as c:
    insert_part(c, PART_6)
    insert_work_order(c, WO_A_ID_6, WO_A_CODE_6)
    insert_work_order(c, WO_B_ID_6, WO_B_CODE_6)
    db.record_movement(c, "receipt", PART_6, 100, 5, "2026-10-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 3, 0, 'active')",
        (WO_A_ID_6, PART_6),
    )
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 2, ?, 2, 0, 'active')",
        (WO_B_ID_6, PART_6),
    )

# A issuing 4 gives 409 (extra 1, available 0)
res6_fail = client.post("/issues", json={
    "work_order_code": WO_A_CODE_6,
    "part_id": PART_6,
    "location_id": 100,
    "quantity": 4,
    "issued_by": "op-a",
})
check("T6 A issue 4 gives 409", res6_fail.status_code == 409, f"Got {res6_fail.status_code}: {res6_fail.text}")

# Add 1 to stock -> total 6, available 1
with db.tx() as c:
    db.record_movement(c, "receipt", PART_6, 100, 1, "2026-10-01T01:00:00Z")

res6_ok = client.post("/issues", json={
    "work_order_code": WO_A_CODE_6,
    "part_id": PART_6,
    "location_id": 100,
    "quantity": 4,
    "issued_by": "op-a",
})
check("T6 A issue 4 gives 201 with stock 6", res6_ok.status_code == 201, f"Got {res6_ok.status_code}: {res6_ok.text}")
res_a6 = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ?", (WO_A_ID_6, PART_6))
check("T6 A row fulfilled", res_a6["issued_quantity"] == 3 and res_a6["status"] == "fulfilled")
res_b6 = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ?", (WO_B_ID_6, PART_6))
check("T6 B row untouched", res_b6["issued_quantity"] == 0 and res_b6["status"] == "active")


# ── Test 7: Several rows for the same order and part ─────────────────────────
print("\n[Test 7] Several reservation rows for same order and part")
PART_7 = 9307
WO_ID_7 = 9309
WO_CODE_7 = "R999999907"
with db.tx() as c:
    insert_part(c, PART_7)
    insert_work_order(c, WO_ID_7, WO_CODE_7)
    db.record_movement(c, "receipt", PART_7, 100, 10, "2026-10-01T00:00:00Z")
    # Two BOM lines: row 1 (qty 2), row 2 (qty 4)
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 2, 0, 'active')",
        (WO_ID_7, PART_7),
    )
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 2, ?, 4, 0, 'active')",
        (WO_ID_7, PART_7),
    )

# Issue 4 fulfills row 1 and leaves row 2 at issued 2 (active)
res7_1 = client.post("/issues", json={
    "work_order_code": WO_CODE_7,
    "part_id": PART_7,
    "location_id": 100,
    "quantity": 4,
    "issued_by": "op-7",
})
check("T7 issue 4 gives 201", res7_1.status_code == 201, f"Got {res7_1.status_code}: {res7_1.text}")
rows7_1 = db.query("SELECT bom_line_id, issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ? ORDER BY id ASC", (WO_ID_7, PART_7))
check("T7 first row fulfilled", rows7_1[0]["issued_quantity"] == 2 and rows7_1[0]["status"] == "fulfilled")
check("T7 second row issued 2 (active)", rows7_1[1]["issued_quantity"] == 2 and rows7_1[1]["status"] == "active")

# Then issue 1 makes second row issued 3 and still active
res7_2 = client.post("/issues", json={
    "work_order_code": WO_CODE_7,
    "part_id": PART_7,
    "location_id": 100,
    "quantity": 1,
    "issued_by": "op-7",
})
check("T7 issue 1 gives 201", res7_2.status_code == 201, f"Got {res7_2.status_code}: {res7_2.text}")
rows7_2 = db.query("SELECT bom_line_id, issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ? ORDER BY id ASC", (WO_ID_7, PART_7))
check("T7 second row issued 3 (still active)", rows7_2[1]["issued_quantity"] == 3 and rows7_2[1]["status"] == "active")

# Then issue 1 fulfills it
res7_3 = client.post("/issues", json={
    "work_order_code": WO_CODE_7,
    "part_id": PART_7,
    "location_id": 100,
    "quantity": 1,
    "issued_by": "op-7",
})
check("T7 final issue 1 gives 201", res7_3.status_code == 201, f"Got {res7_3.status_code}: {res7_3.text}")
rows7_3 = db.query("SELECT bom_line_id, issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ? ORDER BY id ASC", (WO_ID_7, PART_7))
check("T7 second row fulfilled", rows7_3[1]["issued_quantity"] == 4 and rows7_3[1]["status"] == "fulfilled")


# ── Test 8: Shelf-level 409 ──────────────────────────────────────────────────
print("\n[Test 8] Shelf-level 409: loc 100 holds 3, loc 101 holds 3, reservation 5")
PART_8 = 9308
WO_ID_8 = 9310
WO_CODE_8 = "R999999908"
with db.tx() as c:
    insert_part(c, PART_8)
    insert_work_order(c, WO_ID_8, WO_CODE_8)
    db.record_movement(c, "receipt", PART_8, 100, 3, "2026-10-01T00:00:00Z")
    db.record_movement(c, "receipt", PART_8, 101, 3, "2026-10-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 5, 0, 'active')",
        (WO_ID_8, PART_8),
    )

# Issue 4 at loc 100 gives 409 (shelf balance is 3)
res8_fail = client.post("/issues", json={
    "work_order_code": WO_CODE_8,
    "part_id": PART_8,
    "location_id": 100,
    "quantity": 4,
    "issued_by": "op-8",
})
check("T8 issue 4 at loc 100 gives 409", res8_fail.status_code == 409, f"Got {res8_fail.status_code}: {res8_fail.text}")

# Issue 3 at loc 100 gives 201
res8_ok = client.post("/issues", json={
    "work_order_code": WO_CODE_8,
    "part_id": PART_8,
    "location_id": 100,
    "quantity": 3,
    "issued_by": "op-8",
})
check("T8 issue 3 at loc 100 gives 201", res8_ok.status_code == 201, f"Got {res8_ok.status_code}: {res8_ok.text}")
check("T8 loc 100 balance is 0", db.on_hand_at(PART_8, 100) == 0)
check("T8 loc 101 balance is still 3", db.on_hand_at(PART_8, 101) == 3)


# ── Test 9: 404 for unknown order, deleted order, unknown part, unknown location
print("\n[Test 9] 404 for unknown order, deleted order, unknown part, unknown location")
PART_9 = 9309
WO_ID_9 = 9311
WO_CODE_9 = "R999999909"
WO_DEL_ID = 9312
WO_DEL_CODE = "R999999909DEL"
with db.tx() as c:
    insert_part(c, PART_9)
    insert_work_order(c, WO_ID_9, WO_CODE_9, deleted=0)
    insert_work_order(c, WO_DEL_ID, WO_DEL_CODE, deleted=1)
    db.record_movement(c, "receipt", PART_9, 100, 10, "2026-10-01T00:00:00Z")

# Unknown order
r_no_order = client.post("/issues", json={"work_order_code": "R_NONEXISTENT", "part_id": PART_9, "location_id": 100, "quantity": 1, "issued_by": "op"})
check("T9 unknown order 404", r_no_order.status_code == 404, str(r_no_order.status_code))
check("T9 unknown order detail str", isinstance(r_no_order.json().get("detail"), str))

# Deleted order
r_del_order = client.post("/issues", json={"work_order_code": WO_DEL_CODE, "part_id": PART_9, "location_id": 100, "quantity": 1, "issued_by": "op"})
check("T9 deleted order 404", r_del_order.status_code == 404, str(r_del_order.status_code))
check("T9 deleted order detail str", isinstance(r_del_order.json().get("detail"), str))

# Unknown part
r_no_part = client.post("/issues", json={"work_order_code": WO_CODE_9, "part_id": 99999, "location_id": 100, "quantity": 1, "issued_by": "op"})
check("T9 unknown part 404", r_no_part.status_code == 404, str(r_no_part.status_code))
check("T9 unknown part detail str", isinstance(r_no_part.json().get("detail"), str))

# Unknown location
r_no_loc = client.post("/issues", json={"work_order_code": WO_CODE_9, "part_id": PART_9, "location_id": 99999, "quantity": 1, "issued_by": "op"})
check("T9 unknown location 404", r_no_loc.status_code == 404, str(r_no_loc.status_code))
check("T9 unknown location detail str", isinstance(r_no_loc.json().get("detail"), str))

check("T9 nothing written (ledger movements == 1)", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_9,))) == 1)
check("T9 balance still 10", db.on_hand_at(PART_9, 100) == 10)


# ── Test 10: 422 validations ─────────────────────────────────────────────────
print("\n[Test 10] 422 for quantity 0, missing field, empty/whitespace issued_by")
PART_10 = 9310
WO_ID_10 = 9313
WO_CODE_10 = "R999999910"
with db.tx() as c:
    insert_part(c, PART_10)
    insert_work_order(c, WO_ID_10, WO_CODE_10)
    db.record_movement(c, "receipt", PART_10, 100, 10, "2026-10-01T00:00:00Z")

# Quantity 0
r_q0 = client.post("/issues", json={"work_order_code": WO_CODE_10, "part_id": PART_10, "location_id": 100, "quantity": 0, "issued_by": "op"})
check("T10 quantity 0 gives 422", r_q0.status_code == 422, str(r_q0.status_code))
check("T10 quantity 0 detail is str", isinstance(r_q0.json().get("detail"), str))

# Missing field
r_mf = client.post("/issues", json={"work_order_code": WO_CODE_10, "part_id": PART_10, "quantity": 1, "issued_by": "op"})
check("T10 missing field gives 422", r_mf.status_code == 422, str(r_mf.status_code))
check("T10 missing field detail is str", isinstance(r_mf.json().get("detail"), str))

# Empty issued_by
r_empty = client.post("/issues", json={"work_order_code": WO_CODE_10, "part_id": PART_10, "location_id": 100, "quantity": 1, "issued_by": ""})
check("T10 empty issued_by gives 422", r_empty.status_code == 422, str(r_empty.status_code))
check("T10 empty issued_by detail is str", isinstance(r_empty.json().get("detail"), str))

# Whitespace issued_by
r_ws = client.post("/issues", json={"work_order_code": WO_CODE_10, "part_id": PART_10, "location_id": 100, "quantity": 1, "issued_by": "   "})
check("T10 whitespace issued_by gives 422", r_ws.status_code == 422, str(r_ws.status_code))
check("T10 whitespace issued_by detail is str", isinstance(r_ws.json().get("detail"), str))


# ── Test 11: issued_by type preservation ────────────────────────────────────
print("\n[Test 11] issued_by as int and str both give 201, outbox preserves type")
PART_11 = 9311
WO_ID_11 = 9314
WO_CODE_11 = "R999999911"
with db.tx() as c:
    insert_part(c, PART_11)
    insert_work_order(c, WO_ID_11, WO_CODE_11)
    db.record_movement(c, "receipt", PART_11, 100, 10, "2026-10-01T00:00:00Z")

# Int issued_by
r_int = client.post("/issues", json={"work_order_code": WO_CODE_11, "part_id": PART_11, "location_id": 100, "quantity": 1, "issued_by": 888})
check("T11 int issued_by gives 201", r_int.status_code == 201, str(r_int.status_code))
ev_int_row = db.query("SELECT data FROM outbox WHERE event_type = 'stock.issued' AND key = ? ORDER BY seq DESC", (WO_CODE_11,))[0]
ev_int_data = json.loads(ev_int_row["data"])
check("T11 outbox data issued_by is int", ev_int_data["issued_by"] == 888 and type(ev_int_data["issued_by"]) is int)

# Str issued_by
r_str = client.post("/issues", json={"work_order_code": WO_CODE_11, "part_id": PART_11, "location_id": 100, "quantity": 1, "issued_by": "operador-xyz"})
check("T11 str issued_by gives 201", r_str.status_code == 201, str(r_str.status_code))
ev_str_row = db.query("SELECT data FROM outbox WHERE event_type = 'stock.issued' AND key = ? ORDER BY seq DESC", (WO_CODE_11,))[0]
ev_str_data = json.loads(ev_str_row["data"])
check("T11 outbox data issued_by is str", ev_str_data["issued_by"] == "operador-xyz" and type(ev_str_data["issued_by"]) is str)


# ── Test 12: Cancelled and fulfilled reservations ignored ───────────────────
print("\n[Test 12] Cancelled and fulfilled reservations are ignored when consuming")
PART_12 = 9312
WO_ID_12 = 9315
WO_CODE_12 = "R999999912"
with db.tx() as c:
    insert_part(c, PART_12)
    insert_work_order(c, WO_ID_12, WO_CODE_12)
    db.record_movement(c, "receipt", PART_12, 100, 5, "2026-10-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 5, 0, 'cancelled')",
        (WO_ID_12, PART_12),
    )
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 2, ?, 5, 5, 'fulfilled')",
        (WO_ID_12, PART_12),
    )

res12 = client.post("/issues", json={
    "work_order_code": WO_CODE_12,
    "part_id": PART_12,
    "location_id": 100,
    "quantity": 2,
    "issued_by": "op-12",
})
check("T12 issue 2 gives 201 (from available stock)", res12.status_code == 201, str(res12.status_code))
row_canc = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND bom_line_id = 1", (WO_ID_12,))
check("T12 cancelled row still cancelled and issued 0", row_canc["issued_quantity"] == 0 and row_canc["status"] == "cancelled")
row_fulf = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND bom_line_id = 2", (WO_ID_12,))
check("T12 fulfilled row still fulfilled and issued 5", row_fulf["issued_quantity"] == 5 and row_fulf["status"] == "fulfilled")


# ── Test 13: Concurrency A ──────────────────────────────────────────────────
print("\n[Test 13] Concurrency A: 10 threads issue 1 from shelf holding 3 (3 get 201, 7 get 409)")
PART_13 = 9313
WO_ID_13 = 9316
WO_CODE_13 = "R999999913"
with db.tx() as c:
    insert_part(c, PART_13)
    insert_work_order(c, WO_ID_13, WO_CODE_13)
    db.record_movement(c, "receipt", PART_13, 100, 3, "2026-10-01T00:00:00Z")

status_codes_13 = []
lock_13 = threading.Lock()

def worker_13():
    th_client = TestClient(app)
    resp = th_client.post("/issues", json={
        "work_order_code": WO_CODE_13,
        "part_id": PART_13,
        "location_id": 100,
        "quantity": 1,
        "issued_by": "thread-user",
    })
    with lock_13:
        status_codes_13.append(resp.status_code)

threads_13 = [threading.Thread(target=worker_13) for _ in range(10)]
for t in threads_13:
    t.start()
for t in threads_13:
    t.join()

check("T13 exactly 3 threads got 201", status_codes_13.count(201) == 3, f"Got {status_codes_13}")
check("T13 exactly 7 threads got 409", status_codes_13.count(409) == 7, f"Got {status_codes_13}")
check("T13 stock ends at 0", db.on_hand_at(PART_13, 100) == 0)


# ── Test 14: Concurrency B ──────────────────────────────────────────────────
print("\n[Test 14] Concurrency B: stock 1, order A has reservation 1, order B has none -> A gets 201, B gets 409")
PART_14 = 9314
WO_A_ID_14 = 9317
WO_A_CODE_14 = "R999999914A"
WO_B_ID_14 = 9318
WO_B_CODE_14 = "R999999914B"
with db.tx() as c:
    insert_part(c, PART_14)
    insert_work_order(c, WO_A_ID_14, WO_A_CODE_14)
    insert_work_order(c, WO_B_ID_14, WO_B_CODE_14)
    db.record_movement(c, "receipt", PART_14, 100, 1, "2026-10-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 1, 0, 'active')",
        (WO_A_ID_14, PART_14),
    )

results_14 = {}
lock_14 = threading.Lock()

def worker_14_a():
    th_client = TestClient(app)
    resp = th_client.post("/issues", json={
        "work_order_code": WO_A_CODE_14,
        "part_id": PART_14,
        "location_id": 100,
        "quantity": 1,
        "issued_by": "op-a",
    })
    with lock_14:
        results_14["A"] = resp.status_code

def worker_14_b():
    th_client = TestClient(app)
    resp = th_client.post("/issues", json={
        "work_order_code": WO_B_CODE_14,
        "part_id": PART_14,
        "location_id": 100,
        "quantity": 1,
        "issued_by": "op-b",
    })
    with lock_14:
        results_14["B"] = resp.status_code

t_a = threading.Thread(target=worker_14_a)
t_b = threading.Thread(target=worker_14_b)
t_a.start()
t_b.start()
t_a.join()
t_b.join()

check("T14 order A got 201", results_14.get("A") == 201, f"Results: {results_14}")
check("T14 order B got 409", results_14.get("B") == 409, f"Results: {results_14}")
check("T14 stock ends at 0", db.on_hand_at(PART_14, 100) == 0)


# ── Test 15: Atomicity ───────────────────────────────────────────────────────
print("\n[Test 15] Atomicity: patch db.enqueue_event to raise -> 500, nothing written")
PART_15 = 9315
WO_ID_15 = 9319
WO_CODE_15 = "R999999915"
with db.tx() as c:
    insert_part(c, PART_15)
    insert_work_order(c, WO_ID_15, WO_CODE_15)
    db.record_movement(c, "receipt", PART_15, 100, 5, "2026-10-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
        "VALUES (?, 1, ?, 5, 0, 'active')",
        (WO_ID_15, PART_15),
    )

orig_enqueue = db.enqueue_event
def faulty_enqueue(*args, **kwargs):
    raise RuntimeError("Simulated failure in enqueue_event")

db.enqueue_event = faulty_enqueue
try:
    c_500 = TestClient(app, raise_server_exceptions=False)
    resp_500 = c_500.post("/issues", json={
        "work_order_code": WO_CODE_15,
        "part_id": PART_15,
        "location_id": 100,
        "quantity": 2,
        "issued_by": "atom-op",
    })
    check("T15 returns 500 on internal exception", resp_500.status_code == 500, f"Got {resp_500.status_code}")
finally:
    db.enqueue_event = orig_enqueue

check("T15 no new ledger movement written", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_15,))) == 1)
check("T15 balance still 5", db.on_hand_at(PART_15, 100) == 5)
res_row_15 = db.query_one("SELECT issued_quantity, status FROM reservations WHERE work_order_id = ? AND part_id = ?", (WO_ID_15, PART_15))
check("T15 reservation issued_quantity unchanged (0)", res_row_15["issued_quantity"] == 0)
check("T15 reservation status unchanged ('active')", res_row_15["status"] == "active")


# ── Test 16: Ledger and invariants ──────────────────────────────────────────
print("\n[Test 16] Ledger entry format and database-wide invariants")
PART_16 = 9316
WO_ID_16 = 9320
WO_CODE_16 = "R999999916"
with db.tx() as c:
    insert_part(c, PART_16)
    insert_work_order(c, WO_ID_16, WO_CODE_16)
    db.record_movement(c, "receipt", PART_16, 100, 10, "2026-10-01T00:00:00Z")

res16 = client.post("/issues", json={
    "work_order_code": WO_CODE_16,
    "part_id": PART_16,
    "location_id": 100,
    "quantity": 3,
    "issued_by": "inv-op",
})
check("T16 issue status 201", res16.status_code == 201)
ledger_issue = db.query_one("SELECT * FROM ledger_movements WHERE part_id = ? AND type = 'issue'", (PART_16,))
check("T16 ledger type is 'issue'", ledger_issue["type"] == "issue")
check("T16 ledger quantity is negative (-3)", ledger_issue["quantity"] == -3)
iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
check("T16 occurred_at has second-precision Z", bool(iso_re.match(ledger_issue["occurred_at"])), ledger_issue["occurred_at"])

# Invariant: every stock_balances.on_hand equals SUM(ledger quantities), none is negative
all_balances = db.query("SELECT part_id, location_id, on_hand FROM stock_balances")
check("T16 have balances to check", len(all_balances) > 0)
for b in all_balances:
    pid = b["part_id"]
    locid = b["location_id"]
    on_h = b["on_hand"]
    check(f"T16 part {pid} loc {locid} on_hand >= 0", on_h >= 0, f"on_hand={on_h}")
    sum_q = db.query_one("SELECT COALESCE(SUM(quantity), 0) FROM ledger_movements WHERE part_id = ? AND location_id = ?", (pid, locid))[0]
    check(f"T16 part {pid} loc {locid} balance == sum(movements)", on_h == sum_q, f"on_h={on_h} sum={sum_q}")


# ── Test 17: Table row count invariants ─────────────────────────────────────
print("\n[Test 17] shortages, work_orders and needs row counts unchanged by issues")
PART_17 = 9317
WO_ID_17 = 9321
WO_CODE_17 = "R999999917"
with db.tx() as c:
    insert_part(c, PART_17)
    insert_work_order(c, WO_ID_17, WO_CODE_17)
    db.record_movement(c, "receipt", PART_17, 100, 10, "2026-10-01T00:00:00Z")

def table_count(name: str):
    exists = db.query_one("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,))
    if exists:
        return db.query_one(f"SELECT COUNT(*) FROM {name}")[0]
    return None

cnt_short_before = table_count("shortages")
cnt_wo_before = table_count("work_orders")
cnt_needs_before = table_count("needs")

res17 = client.post("/issues", json={
    "work_order_code": WO_CODE_17,
    "part_id": PART_17,
    "location_id": 100,
    "quantity": 2,
    "issued_by": "test-user",
})
check("T17 issue status 201", res17.status_code == 201)

cnt_short_after = table_count("shortages")
cnt_wo_after = table_count("work_orders")
cnt_needs_after = table_count("needs")

check("T17 shortages row count unchanged", cnt_short_after == cnt_short_before)
check("T17 work_orders row count unchanged", cnt_wo_after == cnt_wo_before)
if cnt_needs_before is not None:
    check("T17 needs row count unchanged", cnt_needs_after == cnt_needs_before)


# ── Test 18: Official schema validation ──────────────────────────────────────
print("\n[Test 18] Official schema validation with jsonschema")
PART_18_ISSUE = 9318
WO_ID_18 = 9322
WO_CODE_18 = "R999999918"
PART_18_TR = 9319
PART_18_COUNT = 9320

with db.tx() as c:
    insert_part(c, PART_18_ISSUE)
    insert_work_order(c, WO_ID_18, WO_CODE_18)
    db.record_movement(c, "receipt", PART_18_ISSUE, 100, 10, "2026-10-01T00:00:00Z")
    insert_part(c, PART_18_TR)
    db.record_movement(c, "receipt", PART_18_TR, 100, 10, "2026-10-01T00:00:00Z")
    insert_part(c, PART_18_COUNT)
    db.record_movement(c, "receipt", PART_18_COUNT, 100, 10, "2026-10-01T00:00:00Z")

# Produce one event of each kind using real endpoints
r_iss = client.post("/issues", json={
    "work_order_code": WO_CODE_18,
    "part_id": PART_18_ISSUE,
    "location_id": 100,
    "quantity": 3,
    "issued_by": "validator-user",
})
assert r_iss.status_code == 201

r_tr = client.post("/transfers", json={
    "part_id": PART_18_TR,
    "from_location_id": 100,
    "to_location_id": 101,
    "quantity": 2,
})
assert r_tr.status_code == 201

r_cnt = client.post("/counts", json={
    "location_id": 100,
    "reason": "schema verification count",
    "lines": [{"part_id": PART_18_COUNT, "counted_quantity": 7}],
})
assert r_cnt.status_code == 201

row_ev_issued = db.query_one("SELECT * FROM outbox WHERE event_type = 'stock.issued' AND key = ?", (WO_CODE_18,))
row_ev_transferred = db.query_one("SELECT * FROM outbox WHERE event_type = 'stock.transferred' AND key = ?", (f"part:{PART_18_TR}",))
row_ev_adjusted = db.query_one("SELECT * FROM outbox WHERE event_type = 'stock.adjusted' AND key = ?", (f"part:{PART_18_COUNT}",))

try:
    import jsonschema
    from jsonschema import Draft202012Validator
    from referencing import Registry, Resource
    JSONSCHEMA_AVAILABLE = True
except ImportError:
    JSONSCHEMA_AVAILABLE = False

if not JSONSCHEMA_AVAILABLE:
    print("  SKIP: pip install jsonschema")
else:
    # Assert contract keys
    check("T18 stock.issued key is order code", row_ev_issued["key"] == WO_CODE_18)
    check("T18 stock.transferred key is part:<part_id>", row_ev_transferred["key"] == f"part:{PART_18_TR}")
    check("T18 stock.adjusted key is part:<part_id>", row_ev_adjusted["key"] == f"part:{PART_18_COUNT}")
    KIT = Path(__file__).resolve().parent / "hackathon-inventario-kit"
    SCHEMAS = KIT / "contract" / "schemas"
    resources = []
    for p in SCHEMAS.rglob("*.schema.json"):
        s_data = json.loads(p.read_text(encoding="utf-8"))
        resources.append((s_data["$id"], Resource.from_contents(s_data)))
    registry = Registry().with_resources(resources)
    index = json.loads((SCHEMAS / "index.json").read_text(encoding="utf-8"))
    validators = {
        name: Draft202012Validator(json.loads((SCHEMAS / e["schema"]).read_text(encoding="utf-8")), registry=registry)
        for name, e in index["output"].items()
    }

    for ev_row in [row_ev_issued, row_ev_transferred, row_ev_adjusted]:
        envelope = {
            "event_id": ev_row["event_id"],
            "event_type": ev_row["event_type"],
            "event_version": 1,
            "occurred_at": ev_row["occurred_at"],
            "source": "inventory",
            "key": ev_row["key"],
            "data": json.loads(ev_row["data"]),
        }
        val = validators[ev_row["event_type"]]
        errs = list(val.iter_errors(envelope))
        check(f"T18 schema validation for {ev_row['event_type']}", len(errs) == 0, f"Errors: {[e.message for e in errs]}")


# ── Test 19: PRAGMA check ───────────────────────────────────────────────────
print("\n[Test 19] PRAGMA synchronous check (NORMAL == 1)")
pragma_val = db.query_one("PRAGMA synchronous")[0]
check("T19 db PRAGMA synchronous is 1 (NORMAL)", pragma_val == 1, f"Got {pragma_val}")


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n==========================================")
print(f"ALL ISSUES TESTS DONE: {_passed}/{_total} passed")
print(f"==========================================")
