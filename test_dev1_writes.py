"""
test_dev1_writes.py - Comprehensive write tests for POST /transfers and POST /counts
(Backend Dev 1)

Plain Python – no pytest. Prints PASS for every test that succeeds.
Run: py -3 test_dev1_writes.py

Uses a fresh temporary SQLite database populated with seed.py.
Every test uses its own new parts with ids >= 9100.
"""
import json
import os
import re
import sys
import tempfile
import threading

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
        "INSERT OR IGNORE INTO parts (part_id, sku, sku_norm, name, family, grp, subgroup, unit_name, active)"
        " VALUES (?, ?, ?, ?, 'TEST', 'TEST', 'TEST', 'PIEZA', 1)",
        (part_id, f"SKU-{part_id}", f"SKU-{part_id}", name),
    )


# ══════════════════════════════════════════════════════════════════════════════
# TRANSFERS TESTS (1 - 9)
# ══════════════════════════════════════════════════════════════════════════════

# ── Test 1: Happy path transfer ──────────────────────────────────────────────
print("\n[Test 1] Happy path transfer: 201, transfer_out response, balances, reserved unchanged")
PART_1 = 9101
with db.tx() as c:
    insert_part(c, PART_1)
    db.record_movement(c, "receipt", PART_1, 100, 10, "2026-01-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status)"
        " VALUES (4001, 701, ?, 2, 0, 'active')",
        (PART_1,),
    )

res = client.post("/transfers", json={
    "part_id": PART_1,
    "from_location_id": 100,
    "to_location_id": 101,
    "quantity": 4,
})
check("T1 status 201", res.status_code == 201, f"Got {res.status_code}: {res.text}")
data = res.json()
check("T1 response has movement_id", isinstance(data.get("movement_id"), int), str(data))
check("T1 type is transfer_out", data.get("type") == "transfer_out", str(data))
check("T1 part_id matches", data.get("part_id") == PART_1, str(data))
check("T1 location_id is origin (100)", data.get("location_id") == 100, str(data))
check("T1 quantity is negative (-4)", data.get("quantity") == -4, str(data))
check("T1 occurred_at is non-empty", bool(data.get("occurred_at")), str(data))
check("T1 reference is non-empty", bool(data.get("reference")), str(data))

# Availability check
r_av = client.get(f"/parts/{PART_1}/availability").json()
check("T1 total on_hand unchanged (10)", r_av["on_hand"] == 10, str(r_av))
check("T1 reserved unchanged (2)", r_av["reserved"] == 2, str(r_av))
check("T1 available unchanged (8)", r_av["available"] == 8, str(r_av))
loc_map = {l["location_id"]: l["on_hand"] for l in r_av["locations"]}
check("T1 origin down to 6", loc_map.get(100) == 6, str(loc_map))
check("T1 destination up to 4", loc_map.get(101) == 4, str(loc_map))


# ── Test 2: Ledger records for transfer ──────────────────────────────────────
print("\n[Test 2] Ledger: transfer_out and transfer_in, same ref & timestamp, out has lower id")
PART_2 = 9102
with db.tx() as c:
    insert_part(c, PART_2)
    db.record_movement(c, "receipt", PART_2, 100, 10, "2026-01-01T00:00:00Z")

res = client.post("/transfers", json={
    "part_id": PART_2,
    "from_location_id": 100,
    "to_location_id": 101,
    "quantity": 3,
})
check("T2 transfer status 201", res.status_code == 201, f"Got {res.status_code}")

tr_rows = db.query(
    "SELECT * FROM ledger_movements WHERE part_id = ? AND type IN ('transfer_out', 'transfer_in') ORDER BY id ASC",
    (PART_2,),
)
check("T2 exactly 2 transfer rows in ledger", len(tr_rows) == 2, f"Got {len(tr_rows)}")
out_row, in_row = tr_rows[0], tr_rows[1]
check("T2 first row is transfer_out", out_row["type"] == "transfer_out", str(out_row))
check("T2 second row is transfer_in", in_row["type"] == "transfer_in", str(in_row))
check("T2 out has lower id than in", out_row["id"] < in_row["id"], f"out={out_row['id']} in={in_row['id']}")
check("T2 same reference", out_row["reference"] == in_row["reference"], f"{out_row['reference']} vs {in_row['reference']}")
check("T2 same occurred_at", out_row["occurred_at"] == in_row["occurred_at"], f"{out_row['occurred_at']} vs {in_row['occurred_at']}")
check(
    "T2 occurred_at matches ISO format second precision",
    bool(re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", out_row["occurred_at"])),
    out_row["occurred_at"],
)


# ── Test 3: Outbox for transfer ──────────────────────────────────────────────
print("\n[Test 3] Outbox: exactly one stock.transferred, published=0, exact data keys")
PART_3 = 9103
with db.tx() as c:
    insert_part(c, PART_3)
    db.record_movement(c, "receipt", PART_3, 100, 10, "2026-01-01T00:00:00Z")

res = client.post("/transfers", json={
    "part_id": PART_3,
    "from_location_id": 100,
    "to_location_id": 101,
    "quantity": 2,
})
check("T3 transfer status 201", res.status_code == 201, f"Got {res.status_code}")

events = db.query("SELECT * FROM outbox WHERE key = ?", (f"part:{PART_3}",))
check("T3 exactly 1 outbox event", len(events) == 1, f"Got {len(events)}")
evt = events[0]
check("T3 event_type is stock.transferred", evt["event_type"] == "stock.transferred", evt["event_type"])
check("T3 published == 0", evt["published"] == 0, str(evt["published"]))
data = json.loads(evt["data"])
check(
    "T3 exact data keys",
    set(data.keys()) == {"part_id", "from_location_id", "to_location_id", "quantity"},
    str(data.keys()),
)
check("T3 data values match", data == {"part_id": PART_3, "from_location_id": 100, "to_location_id": 101, "quantity": 2}, str(data))


# ── Test 4: 409 when origin has less than requested ──────────────────────────
print("\n[Test 4] 409 when origin has less than requested: nothing written")
PART_4 = 9104
with db.tx() as c:
    insert_part(c, PART_4)
    db.record_movement(c, "receipt", PART_4, 100, 5, "2026-01-01T00:00:00Z")

ledger_before = db.query("SELECT COUNT(*) AS n FROM ledger_movements WHERE part_id = ?", (PART_4,))[0]["n"]
outbox_before = db.query("SELECT COUNT(*) AS n FROM outbox WHERE key = ?", (f"part:{PART_4}",))[0]["n"]

res = client.post("/transfers", json={
    "part_id": PART_4,
    "from_location_id": 100,
    "to_location_id": 101,
    "quantity": 6,
})
check("T4 status 409", res.status_code == 409, f"Got {res.status_code}: {res.text}")
check("T4 detail is str", isinstance(res.json().get("detail"), str), str(res.json()))
check("T4 no new ledger rows", db.query("SELECT COUNT(*) AS n FROM ledger_movements WHERE part_id = ?", (PART_4,))[0]["n"] == ledger_before)
check("T4 balance unchanged at 5", db.on_hand_at(PART_4, 100) == 5)
check("T4 no outbox event", db.query("SELECT COUNT(*) AS n FROM outbox WHERE key = ?", (f"part:{PART_4}",))[0]["n"] == outbox_before)


# ── Test 5: 404 for unknown part, unknown origin, unknown destination ────────
print("\n[Test 5] 404 for unknown part, unknown origin, unknown destination: nothing written")
PART_5 = 9105
with db.tx() as c:
    insert_part(c, PART_5)
    db.record_movement(c, "receipt", PART_5, 100, 5, "2026-01-01T00:00:00Z")

# Unknown part
r_part = client.post("/transfers", json={"part_id": 99991, "from_location_id": 100, "to_location_id": 101, "quantity": 1})
check("T5 unknown part 404", r_part.status_code == 404, str(r_part.status_code))
check("T5 unknown part detail str", isinstance(r_part.json().get("detail"), str))

# Unknown origin
r_orig = client.post("/transfers", json={"part_id": PART_5, "from_location_id": 99991, "to_location_id": 101, "quantity": 1})
check("T5 unknown origin 404", r_orig.status_code == 404, str(r_orig.status_code))
check("T5 unknown origin detail str", isinstance(r_orig.json().get("detail"), str))

# Unknown destination
r_dest = client.post("/transfers", json={"part_id": PART_5, "from_location_id": 100, "to_location_id": 99991, "quantity": 1})
check("T5 unknown dest 404", r_dest.status_code == 404, str(r_dest.status_code))
check("T5 unknown dest detail str", isinstance(r_dest.json().get("detail"), str))

check("T5 origin balance still 5", db.on_hand_at(PART_5, 100) == 5)
check("T5 only 1 ledger movement (receipt)", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_5,))) == 1)


# ── Test 6: 422 for same origin and destination, quantity 0, missing field ────
print("\n[Test 6] 422 for same origin/destination, quantity 0, missing field")
PART_6 = 9106
with db.tx() as c:
    insert_part(c, PART_6)
    db.record_movement(c, "receipt", PART_6, 100, 5, "2026-01-01T00:00:00Z")

# Same origin and destination
r_same = client.post("/transfers", json={"part_id": PART_6, "from_location_id": 100, "to_location_id": 100, "quantity": 1})
check("T6 same origin/dest 422", r_same.status_code == 422, f"Got {r_same.status_code}: {r_same.text}")
check("T6 same origin/dest detail str", isinstance(r_same.json().get("detail"), str))

# Quantity 0
r_zero = client.post("/transfers", json={"part_id": PART_6, "from_location_id": 100, "to_location_id": 101, "quantity": 0})
check("T6 quantity 0 422", r_zero.status_code == 422, f"Got {r_zero.status_code}: {r_zero.text}")
check("T6 quantity 0 detail str", isinstance(r_zero.json().get("detail"), str))

# Missing field
r_miss = client.post("/transfers", json={"part_id": PART_6, "from_location_id": 100, "to_location_id": 101})
check("T6 missing field 422", r_miss.status_code == 422, f"Got {r_miss.status_code}: {r_miss.text}")
check("T6 missing field detail str", isinstance(r_miss.json().get("detail"), str))

check("T6 origin balance untouched", db.on_hand_at(PART_6, 100) == 5)


# ── Test 7: Moving ALL the stock leaves origin at 0 and disappears from list ──
print("\n[Test 7] Moving ALL stock leaves origin at 0, disappears from availability locations")
PART_7 = 9107
with db.tx() as c:
    insert_part(c, PART_7)
    db.record_movement(c, "receipt", PART_7, 100, 5, "2026-01-01T00:00:00Z")

res = client.post("/transfers", json={
    "part_id": PART_7,
    "from_location_id": 100,
    "to_location_id": 101,
    "quantity": 5,
})
check("T7 transfer all status 201", res.status_code == 201, f"Got {res.status_code}")
check("T7 origin balance is exactly 0", db.on_hand_at(PART_7, 100) == 0)
check("T7 destination balance is 5", db.on_hand_at(PART_7, 101) == 5)

r_av = client.get(f"/parts/{PART_7}/availability").json()
loc_ids = [l["location_id"] for l in r_av["locations"]]
check("T7 origin 100 disappeared from locations list", 100 not in loc_ids, str(loc_ids))
check("T7 destination 101 in locations list", 101 in loc_ids, str(loc_ids))
check("T7 total on_hand is 5", r_av["on_hand"] == 5, str(r_av))


# ── Test 8: Transfer allowed even when whole stock is reserved ────────────────
print("\n[Test 8] Transfer allowed when whole stock is reserved (on_hand 5, reserved 5)")
PART_8 = 9108
with db.tx() as c:
    insert_part(c, PART_8)
    db.record_movement(c, "receipt", PART_8, 100, 5, "2026-01-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status)"
        " VALUES (4008, 708, ?, 5, 0, 'active')",
        (PART_8,),
    )

res = client.post("/transfers", json={
    "part_id": PART_8,
    "from_location_id": 100,
    "to_location_id": 101,
    "quantity": 5,
})
check("T8 transfer status 201", res.status_code == 201, f"Got {res.status_code}: {res.text}")
r_av = client.get(f"/parts/{PART_8}/availability").json()
check("T8 total on_hand is 5", r_av["on_hand"] == 5, str(r_av))
check("T8 reserved is 5", r_av["reserved"] == 5, str(r_av))
check("T8 available is 0", r_av["available"] == 0, str(r_av))


# ── Test 9: Concurrency: 10 threads transfer 1 unit from location holding 5 ───
print("\n[Test 9] Concurrency: 10 threads transfer 1 unit from origin holding 5 (5 get 201, 5 get 409)")
PART_9 = 9109
with db.tx() as c:
    insert_part(c, PART_9)
    db.record_movement(c, "receipt", PART_9, 100, 5, "2026-01-01T00:00:00Z")

status_codes = []
lock = threading.Lock()

def transfer_worker():
    t_client = TestClient(app)
    resp = t_client.post("/transfers", json={
        "part_id": PART_9,
        "from_location_id": 100,
        "to_location_id": 101,
        "quantity": 1,
    })
    with lock:
        status_codes.append(resp.status_code)

threads = [threading.Thread(target=transfer_worker) for _ in range(10)]
for t in threads:
    t.start()
for t in threads:
    t.join()

check("T9 exactly 5 threads got 201", status_codes.count(201) == 5, f"Got {status_codes}")
check("T9 exactly 5 threads got 409", status_codes.count(409) == 5, f"Got {status_codes}")
check("T9 origin ended at 0", db.on_hand_at(PART_9, 100) == 0)
check("T9 destination ended at 5", db.on_hand_at(PART_9, 101) == 5)


# ══════════════════════════════════════════════════════════════════════════════
# COUNTS TESTS (10 - 17)
# ══════════════════════════════════════════════════════════════════════════════

# ── Test 10: Counted below system ────────────────────────────────────────────
print("\n[Test 10] Counted below system: delta negative, balance matches, ledger adjustment")
PART_10 = 9110
with db.tx() as c:
    insert_part(c, PART_10)
    db.record_movement(c, "receipt", PART_10, 100, 10, "2026-01-01T00:00:00Z")

res = client.post("/counts", json={
    "location_id": 100,
    "reason": "conteo mensual",
    "lines": [{"part_id": PART_10, "counted_quantity": 8}],
})
check("T10 count status 201", res.status_code == 201, f"Got {res.status_code}: {res.text}")
data = res.json()
check(
    "T10 response exact adjustments",
    data == {"adjustments": [{"part_id": PART_10, "delta": -2}]},
    str(data),
)
check("T10 balance matches (8)", db.on_hand_at(PART_10, 100) == 8)
latest_entry = db.query_one(
    "SELECT * FROM ledger_movements WHERE part_id = ? ORDER BY id DESC LIMIT 1",
    (PART_10,),
)
check("T10 latest entry is adjustment", latest_entry["type"] == "adjustment", str(latest_entry))
check("T10 latest entry quantity is -2", latest_entry["quantity"] == -2, str(latest_entry))
check("T10 latest entry reference is reason", latest_entry["reference"] == "conteo mensual", str(latest_entry))


# ── Test 11: Counted above system, equal, no balance, counted 0 ──────────────
print("\n[Test 11] Counted above, equal (no op), no balance (+delta), counted 0 with 0 (no op)")
PART_11A = 9111
PART_11B = 9112
PART_11C = 9113
with db.tx() as c:
    insert_part(c, PART_11A)
    insert_part(c, PART_11B)
    insert_part(c, PART_11C)
    db.record_movement(c, "receipt", PART_11A, 100, 5, "2026-01-01T00:00:00Z")

# 11a: Counted above (5 -> 7 => +2)
r_above = client.post("/counts", json={
    "location_id": 100,
    "reason": "sobrante",
    "lines": [{"part_id": PART_11A, "counted_quantity": 7}],
})
check("T11a status 201", r_above.status_code == 201)
check("T11a delta +2", r_above.json() == {"adjustments": [{"part_id": PART_11A, "delta": 2}]}, str(r_above.json()))
check("T11a balance is 7", db.on_hand_at(PART_11A, 100) == 7)

# 11b: Counted equal (7 -> 7 => delta 0, no ledger row, no outbox event)
ledger_cnt_before = db.query("SELECT COUNT(*) AS n FROM ledger_movements WHERE part_id = ?", (PART_11A,))[0]["n"]
outbox_cnt_before = db.query("SELECT COUNT(*) AS n FROM outbox WHERE key = ?", (f"part:{PART_11A}",))[0]["n"]

r_equal = client.post("/counts", json={
    "location_id": 100,
    "reason": "igual",
    "lines": [{"part_id": PART_11A, "counted_quantity": 7}],
})
check("T11b status 201", r_equal.status_code == 201)
check("T11b adjustments is empty list", r_equal.json() == {"adjustments": []}, str(r_equal.json()))
check("T11b no new ledger rows", db.query("SELECT COUNT(*) AS n FROM ledger_movements WHERE part_id = ?", (PART_11A,))[0]["n"] == ledger_cnt_before)
check("T11b no new outbox events", db.query("SELECT COUNT(*) AS n FROM outbox WHERE key = ?", (f"part:{PART_11A}",))[0]["n"] == outbox_cnt_before)

# 11c: No balance at that location, counted 4 => delta +4
r_nobal = client.post("/counts", json={
    "location_id": 100,
    "reason": "hallazgo",
    "lines": [{"part_id": PART_11B, "counted_quantity": 4}],
})
check("T11c status 201", r_nobal.status_code == 201)
check("T11c delta +4", r_nobal.json() == {"adjustments": [{"part_id": PART_11B, "delta": 4}]}, str(r_nobal.json()))
check("T11c balance is 4", db.on_hand_at(PART_11B, 100) == 4)

# 11d: Counted 0 with nothing there => writes nothing
r_zero = client.post("/counts", json={
    "location_id": 100,
    "reason": "nada",
    "lines": [{"part_id": PART_11C, "counted_quantity": 0}],
})
check("T11d status 201", r_zero.status_code == 201)
check("T11d adjustments is empty list", r_zero.json() == {"adjustments": []}, str(r_zero.json()))
check("T11d no ledger entries", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_11C,))) == 0)
check("T11d no outbox entries", len(db.query("SELECT * FROM outbox WHERE key = ?", (f"part:{PART_11C}",))) == 0)


# ── Test 12: A part that is not listed is untouched ──────────────────────────
print("\n[Test 12] A part that is not listed is untouched")
PART_12A = 9114
PART_12B = 9115
with db.tx() as c:
    insert_part(c, PART_12A)
    insert_part(c, PART_12B)
    db.record_movement(c, "receipt", PART_12A, 100, 10, "2026-01-01T00:00:00Z")
    db.record_movement(c, "receipt", PART_12B, 100, 10, "2026-01-01T00:00:00Z")

res = client.post("/counts", json={
    "location_id": 100,
    "reason": "conteo parcial",
    "lines": [{"part_id": PART_12A, "counted_quantity": 8}],
})
check("T12 status 201", res.status_code == 201)
check("T12 PART_12A balance is 8", db.on_hand_at(PART_12A, 100) == 8)
check("T12 PART_12B balance is untouched at 10", db.on_hand_at(PART_12B, 100) == 10)
check("T12 no extra ledger rows for PART_12B", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_12B,))) == 1)


# ── Test 13: 422 for missing reason, "", "   ", duplicate part_id, negative counted_qty ─
print("\n[Test 13] 422 for missing reason, '', '   ', duplicate part_id, negative counted_quantity")
PART_13 = 9116
with db.tx() as c:
    insert_part(c, PART_13)
    db.record_movement(c, "receipt", PART_13, 100, 10, "2026-01-01T00:00:00Z")

# Missing reason key
r_miss_reason = client.post("/counts", json={"location_id": 100, "lines": [{"part_id": PART_13, "counted_quantity": 5}]})
check("T13 missing reason 422", r_miss_reason.status_code == 422, str(r_miss_reason.status_code))
check("T13 missing reason detail str", isinstance(r_miss_reason.json().get("detail"), str))

# Empty string reason ""
r_empty_reason = client.post("/counts", json={"location_id": 100, "reason": "", "lines": [{"part_id": PART_13, "counted_quantity": 5}]})
check("T13 empty reason 422", r_empty_reason.status_code == 422, str(r_empty_reason.status_code))
check("T13 empty reason detail str", isinstance(r_empty_reason.json().get("detail"), str))

# Whitespace reason "   "
r_ws_reason = client.post("/counts", json={"location_id": 100, "reason": "   ", "lines": [{"part_id": PART_13, "counted_quantity": 5}]})
check("T13 whitespace reason 422", r_ws_reason.status_code == 422, str(r_ws_reason.status_code))
check("T13 whitespace reason detail str", isinstance(r_ws_reason.json().get("detail"), str))

# Duplicate part_id in lines
r_dup = client.post("/counts", json={
    "location_id": 100,
    "reason": "duplicado",
    "lines": [
        {"part_id": PART_13, "counted_quantity": 5},
        {"part_id": PART_13, "counted_quantity": 6},
    ],
})
check("T13 duplicate part_id 422", r_dup.status_code == 422, str(r_dup.status_code))
check("T13 duplicate part_id detail str", isinstance(r_dup.json().get("detail"), str))

# Negative counted_quantity
r_neg = client.post("/counts", json={
    "location_id": 100,
    "reason": "negativo",
    "lines": [{"part_id": PART_13, "counted_quantity": -1}],
})
check("T13 negative counted_qty 422", r_neg.status_code == 422, str(r_neg.status_code))
check("T13 negative counted_qty detail str", isinstance(r_neg.json().get("detail"), str))

check("T13 balance untouched at 10", db.on_hand_at(PART_13, 100) == 10)


# ── Test 14: 404 for unknown location & unknown part; all-or-nothing check ─────
print("\n[Test 14] 404 for unknown location and unknown part; multi-line failure applies nothing")
PART_14A = 9117
PART_14B = 9118
with db.tx() as c:
    insert_part(c, PART_14A)
    insert_part(c, PART_14B)
    db.record_movement(c, "receipt", PART_14A, 100, 10, "2026-01-01T00:00:00Z")
    db.record_movement(c, "receipt", PART_14B, 100, 10, "2026-01-01T00:00:00Z")

# Unknown location
r_unk_loc = client.post("/counts", json={
    "location_id": 99991,
    "reason": "test",
    "lines": [{"part_id": PART_14A, "counted_quantity": 5}],
})
check("T14 unknown loc 404", r_unk_loc.status_code == 404, str(r_unk_loc.status_code))
check("T14 unknown loc detail str", isinstance(r_unk_loc.json().get("detail"), str))

# Unknown part in lines
r_unk_part = client.post("/counts", json={
    "location_id": 100,
    "reason": "test",
    "lines": [{"part_id": 99991, "counted_quantity": 5}],
})
check("T14 unknown part 404", r_unk_part.status_code == 404, str(r_unk_part.status_code))
check("T14 unknown part detail str", isinstance(r_unk_part.json().get("detail"), str))

# Two lines: line 1 valid, line 2 invalid part -> 404 and line 1 is NOT applied!
r_partial = client.post("/counts", json={
    "location_id": 100,
    "reason": "test",
    "lines": [
        {"part_id": PART_14A, "counted_quantity": 5},
        {"part_id": 99991, "counted_quantity": 5},
    ],
})
check("T14 second line invalid returns 404", r_partial.status_code == 404, str(r_partial.status_code))
check("T14 first line was NOT applied (balance still 10)", db.on_hand_at(PART_14A, 100) == 10)
check("T14 no ledger rows written for PART_14A", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_14A,))) == 1)


# ── Test 15: Outbox for counts ───────────────────────────────────────────────
print("\n[Test 15] Outbox: one stock.adjusted per adjustment, key 'part:<id>', exact keys")
PART_15 = 9119
with db.tx() as c:
    insert_part(c, PART_15)
    db.record_movement(c, "receipt", PART_15, 100, 10, "2026-01-01T00:00:00Z")

res = client.post("/counts", json={
    "location_id": 100,
    "reason": "ajuste anual",
    "lines": [{"part_id": PART_15, "counted_quantity": 7}],
})
check("T15 status 201", res.status_code == 201)

events = db.query("SELECT * FROM outbox WHERE key = ?", (f"part:{PART_15}",))
check("T15 exactly 1 outbox event", len(events) == 1, f"Got {len(events)}")
evt = events[0]
check("T15 event_type is stock.adjusted", evt["event_type"] == "stock.adjusted", evt["event_type"])
check("T15 published == 0", evt["published"] == 0, str(evt["published"]))
data = json.loads(evt["data"])
check(
    "T15 exact data keys {part_id, location_id, delta, reason}",
    set(data.keys()) == {"part_id", "location_id", "delta", "reason"},
    str(data.keys()),
)
check("T15 data delta is -3 (not 0)", data["delta"] == -3, str(data))
check("T15 data reason is non-empty", data["reason"] == "ajuste anual", str(data))
check("T15 data part_id matches", data["part_id"] == PART_15, str(data))
check("T15 data location_id matches", data["location_id"] == 100, str(data))


# ── Test 16: Count can make available negative ───────────────────────────────
print("\n[Test 16] Count can make available negative: on_hand 5, reserved 5, count 2 -> available -3")
PART_16 = 9120
with db.tx() as c:
    insert_part(c, PART_16)
    db.record_movement(c, "receipt", PART_16, 100, 5, "2026-01-01T00:00:00Z")
    c.execute(
        "INSERT INTO reservations (work_order_id, bom_line_id, part_id, quantity, issued_quantity, status)"
        " VALUES (4016, 716, ?, 5, 0, 'active')",
        (PART_16,),
    )

res = client.post("/counts", json={
    "location_id": 100,
    "reason": "auditoria",
    "lines": [{"part_id": PART_16, "counted_quantity": 2}],
})
check("T16 status 201", res.status_code == 201, f"Got {res.status_code}: {res.text}")

r_av = client.get(f"/parts/{PART_16}/availability").json()
check("T16 on_hand is 2", r_av["on_hand"] == 2, str(r_av))
check("T16 reserved is 5", r_av["reserved"] == 5, str(r_av))
check("T16 available is -3", r_av["available"] == -3, str(r_av))


# ── Test 17: Atomicity on outbox failure ─────────────────────────────────────
print("\n[Test 17] Atomicity: enqueue_event failure on 2nd line rolls back everything, returns 500")
PART_17A = 9121
PART_17B = 9122
with db.tx() as c:
    insert_part(c, PART_17A)
    insert_part(c, PART_17B)
    db.record_movement(c, "receipt", PART_17A, 100, 10, "2026-01-01T00:00:00Z")
    db.record_movement(c, "receipt", PART_17B, 100, 10, "2026-01-01T00:00:00Z")

orig_enqueue = db.enqueue_event
call_count = 0

def patched_enqueue(*args, **kwargs):
    global call_count
    call_count += 1
    if call_count >= 2:
        raise RuntimeError("Simulated failure on 2nd enqueue")
    return orig_enqueue(*args, **kwargs)

db.enqueue_event = patched_enqueue
try:
    c_err = TestClient(app, raise_server_exceptions=False)
    r_fail = c_err.post("/counts", json={
        "location_id": 100,
        "reason": "atomicity test",
        "lines": [
            {"part_id": PART_17A, "counted_quantity": 8},
            {"part_id": PART_17B, "counted_quantity": 7},
        ],
    })
    check("T17 returns 500 on internal exception", r_fail.status_code == 500, f"Got {r_fail.status_code}")
finally:
    db.enqueue_event = orig_enqueue

check("T17 PART_17A balance still 10", db.on_hand_at(PART_17A, 100) == 10)
check("T17 PART_17B balance still 10", db.on_hand_at(PART_17B, 100) == 10)
check("T17 no new ledger rows for 17A", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_17A,))) == 1)
check("T17 no new ledger rows for 17B", len(db.query("SELECT * FROM ledger_movements WHERE part_id = ?", (PART_17B,))) == 1)
check("T17 no outbox events for 17A", len(db.query("SELECT * FROM outbox WHERE key = ?", (f"part:{PART_17A}",))) == 0)
check("T17 no outbox events for 17B", len(db.query("SELECT * FROM outbox WHERE key = ?", (f"part:{PART_17B}",))) == 0)


# ══════════════════════════════════════════════════════════════════════════════
# CONTRACT REPLAY & INVARIANTS (18 - 19)
# ══════════════════════════════════════════════════════════════════════════════

# ── Test 18: Official-scenario replay ────────────────────────────────────────
print("\n[Test 18] Official scenario: shelf insert, receipt 4, transfer 5 (409), transfer 3 (201), count (201), ledger check")
PART_18 = 9123
SHELF_LOC = 150
with db.tx() as c:
    c.execute(
        "INSERT OR IGNORE INTO locations (location_id, code, name, is_workbench, active) VALUES (?, 'U-150', 'Estante 150', 0, 1)",
        (SHELF_LOC,),
    )
    insert_part(c, PART_18)
    db.record_movement(c, "receipt", PART_18, 100, 4, "2020-01-01T00:00:00Z")

# transfer 5 gives 409
r_tr5 = client.post("/transfers", json={
    "part_id": PART_18,
    "from_location_id": 100,
    "to_location_id": SHELF_LOC,
    "quantity": 5,
})
check("T18 transfer 5 gives 409", r_tr5.status_code == 409, str(r_tr5.status_code))

# transfer 3 gives 201
r_tr3 = client.post("/transfers", json={
    "part_id": PART_18,
    "from_location_id": 100,
    "to_location_id": SHELF_LOC,
    "quantity": 3,
})
check("T18 transfer 3 gives 201", r_tr3.status_code == 201, str(r_tr3.status_code))

# availability shows 100 = 1, shelf = 3, total 4
r_av = client.get(f"/parts/{PART_18}/availability").json()
loc_map = {l["location_id"]: l["on_hand"] for l in r_av["locations"]}
check("T18 availability total is 4", r_av["on_hand"] == 4, str(r_av))
check("T18 location 100 is 1", loc_map.get(100) == 1, str(loc_map))
check("T18 shelf 150 is 3", loc_map.get(SHELF_LOC) == 3, str(loc_map))

# count with reason "" gives 422
r_c_empty = client.post("/counts", json={
    "location_id": SHELF_LOC,
    "reason": "",
    "lines": [{"part_id": PART_18, "counted_quantity": 2}],
})
check("T18 count reason '' gives 422", r_c_empty.status_code == 422, str(r_c_empty.status_code))

# count of the shelf with counted_quantity 2 and reason "conteo cíclico" gives 201 with adjustments == [{"part_id": X, "delta": -1}]
r_c = client.post("/counts", json={
    "location_id": SHELF_LOC,
    "reason": "conteo cíclico",
    "lines": [{"part_id": PART_18, "counted_quantity": 2}],
})
check("T18 count status 201", r_c.status_code == 201, str(r_c.status_code))
check(
    "T18 adjustments == [{'part_id': 9123, 'delta': -1}]",
    r_c.json().get("adjustments") == [{"part_id": PART_18, "delta": -1}],
    str(r_c.json()),
)

# total is 3
r_av2 = client.get(f"/parts/{PART_18}/availability").json()
check("T18 total is now 3", r_av2["on_hand"] == 3, str(r_av2))

# ledger check: at least 4 entries, first has balance 3, last has balance 4, quantities sum to 3, sorted newest to oldest
r_led = client.get(f"/parts/{PART_18}/ledger").json()
entries = r_led["entries"]
check("T18 ledger has at least 4 entries", len(entries) >= 4, f"Got {len(entries)}")
check("T18 first (newest) entry balance is 3", entries[0]["balance"] == 3, str(entries[0]))
check("T18 last entry is receipt with balance 4", entries[-1]["type"] == "receipt" and entries[-1]["balance"] == 4, str(entries[-1]))
check("T18 quantities sum to 3", sum(e["quantity"] for e in entries) == 3, f"Sum={sum(e['quantity'] for e in entries)}")
timestamps = [e["occurred_at"] for e in entries]
check("T18 occurred_at sorted newest to oldest", timestamps == sorted(timestamps, reverse=True), str(timestamps))


# ── Test 19: Global ledger and balance invariants ────────────────────────────
print("\n[Test 19] Invariants: stock_balances.on_hand == SUM(ledger_movements.quantity), no on_hand is negative")
balance_rows = db.query("SELECT part_id, location_id, on_hand FROM stock_balances")
check("T19 have balance rows", len(balance_rows) > 0, f"Got {len(balance_rows)}")

for b_row in balance_rows:
    p_id = b_row["part_id"]
    l_id = b_row["location_id"]
    oh = b_row["on_hand"]
    check(f"T19 part={p_id} loc={l_id} on_hand >= 0", oh >= 0, f"on_hand={oh}")

    sum_qty = db.query_one(
        "SELECT COALESCE(SUM(quantity), 0) AS s FROM ledger_movements WHERE part_id = ? AND location_id = ?",
        (p_id, l_id),
    )["s"]
    check(
        f"T19 part={p_id} loc={l_id} balance matches sum of movements",
        oh == sum_qty,
        f"balance={oh} sum={sum_qty}",
    )


# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n==========================================")
print(f"ALL WRITE TESTS DONE: {_passed}/{_total} passed")
print(f"==========================================")
if _passed < _total:
    sys.exit(1)
