"""
test_dev1_reads.py - Unit tests for the real GET /parts/{id}/availability
and GET /parts/{id}/ledger endpoints.

Plain Python – no pytest.  Prints PASS for every test that succeeds.
Run:  py -3 test_dev1_reads.py

Uses a fresh temporary SQLite database populated with seed.py so the checks
are independent of any previously running server.
Tests that insert extra rows use part_ids >= 9000 to avoid collisions.
"""
import os
import sys
import tempfile
import json

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ── 1. Boot the temp DB before importing db or main ─────────────────────────
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["INVENTORY_DB"] = _tmp.name

import db          # now connects to the temp file
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

def avail(part_id: int):
    r = client.get(f"/parts/{part_id}/availability")
    return r.status_code, r.json()

def ledger(part_id: int):
    r = client.get(f"/parts/{part_id}/ledger")
    return r.status_code, r.json()

def insert_part(c, part_id: int, name: str = "Test Part"):
    c.execute(
        "INSERT OR IGNORE INTO parts (part_id, sku, sku_norm, name, family, grp, subgroup, unit_name, active)"
        " VALUES (?, ?, ?, ?, 'TEST', 'TEST', 'TEST', 'PIEZA', 1)",
        (part_id, f"SKU-{part_id}", f"SKU-{part_id}", name),
    )

# ── Test 1: seed part 1001 availability ──────────────────────────────────────
print("\n[Test 1] Seed part 1001 – availability totals and locations")
st, data = avail(1001)
check("1001 status 200", st == 200, f"Got {st}")
check("1001 on_hand == 18", data["on_hand"] == 18, str(data))
check("1001 reserved == 3", data["reserved"] == 3, str(data))
check("1001 available == 15", data["available"] == 15, str(data))

locs = {l["location_id"]: l for l in data["locations"]}
check("1001 has locations 100 and 101", set(locs) == {100, 101}, str(locs))
check("1001 loc-100 code is U-100", locs[100].get("code") == "U-100", str(locs[100]))
check("1001 loc-101 code is U-101", locs[101].get("code") == "U-101", str(locs[101]))
check("1001 loc-100 on_hand == 10", locs[100]["on_hand"] == 10, str(locs[100]))
check("1001 loc-101 on_hand == 8",  locs[101]["on_hand"] == 8,  str(locs[101]))
check("1001 loc-100 reserved == 0", locs[100]["reserved"] == 0, str(locs[100]))
check("1001 loc-101 reserved == 0", locs[101]["reserved"] == 0, str(locs[101]))
check("1001 location sum equals root on_hand",
      sum(l["on_hand"] for l in data["locations"]) == data["on_hand"],
      str(data))

# ── Test 2: part 1004 – known, zero stock ────────────────────────────────────
print("\n[Test 2] Part 1004 – known part with zero stock")
st, data = avail(1004)
check("1004 status 200", st == 200, f"Got {st}")
check("1004 on_hand == 0",   data["on_hand"]   == 0, str(data))
check("1004 reserved == 0",  data["reserved"]  == 0, str(data))
check("1004 available == 0", data["available"] == 0, str(data))
check("1004 locations == []", data["locations"] == [], str(data))

# ── Test 3: unknown part 999 → 404 on both endpoints ─────────────────────────
print("\n[Test 3] Unknown part 999 – 404 on both endpoints")
st, data = avail(999)
check("999 availability status 404", st == 404, f"Got {st}")
check("999 availability detail is str",
      isinstance(data.get("detail"), str), str(data))

st, data = ledger(999)
check("999 ledger status 404", st == 404, f"Got {st}")
check("999 ledger detail is str",
      isinstance(data.get("detail"), str), str(data))

# ── Test 4: reservation statuses ─────────────────────────────────────────────
print("\n[Test 4] Reservation statuses – fulfilled/cancelled don't count, active does")
PART_4 = 9001
with db.tx() as c:
    insert_part(c, PART_4)
    # 10 units in loc 100
    db.record_movement(c, "receipt", PART_4, 100, 10, "2026-01-01T00:00:00Z")
    # active reservation: quantity=6, issued=2  →  reserved = 4
    c.execute("INSERT INTO reservations "
              "(work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
              "VALUES (3001, 601, ?, 6, 2, 'active')", (PART_4,))
    # fulfilled reservation: quantity=3, issued=3 → should NOT count
    c.execute("INSERT INTO reservations "
              "(work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
              "VALUES (3002, 602, ?, 3, 3, 'fulfilled')", (PART_4,))
    # cancelled reservation: quantity=5, issued=0 → should NOT count
    c.execute("INSERT INTO reservations "
              "(work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
              "VALUES (3003, 603, ?, 5, 0, 'cancelled')", (PART_4,))

st, data = avail(PART_4)
check("T4 status 200", st == 200, f"Got {st}")
check("T4 on_hand == 10",   data["on_hand"]   == 10, str(data))
check("T4 reserved == 4",   data["reserved"]  == 4,  str(data))  # only active: 6-2
check("T4 available == 6",  data["available"] == 6,  str(data))

# ── Test 5: available can be negative ────────────────────────────────────────
print("\n[Test 5] available is negative when reserved > on_hand")
PART_5 = 9002
with db.tx() as c:
    insert_part(c, PART_5)
    # 2 units
    db.record_movement(c, "receipt", PART_5, 100, 2, "2026-01-01T00:00:00Z")
    # reserve 5 (only 2 exist)
    c.execute("INSERT INTO reservations "
              "(work_order_id, bom_line_id, part_id, quantity, issued_quantity, status) "
              "VALUES (3010, 610, ?, 5, 0, 'active')", (PART_5,))

st, data = avail(PART_5)
check("T5 status 200", st == 200, f"Got {st}")
check("T5 on_hand == 2",   data["on_hand"]   == 2,  str(data))
check("T5 reserved == 5",  data["reserved"]  == 5,  str(data))
check("T5 available == -3", data["available"] == -3, str(data))

# ── Test 6: ledger for part 1001 ─────────────────────────────────────────────
print("\n[Test 6] Ledger for part 1001 – 4 entries, correct balances and order")
st, data = ledger(1001)
check("1001 ledger status 200", st == 200, f"Got {st}")
entries = data["entries"]
check("1001 has 4 ledger entries", len(entries) == 4, f"got {len(entries)}")

# Newest first: balances 18, 20, 10, 20  (issue, transfer_in, transfer_out, receipt)
expected_balances = [18, 20, 10, 20]
actual_balances   = [e["balance"] for e in entries]
check("1001 balances newest-first [18,20,10,20]",
      actual_balances == expected_balances, str(actual_balances))

check("1001 first entry type is 'issue'",
      entries[0]["type"] == "issue", entries[0]["type"])
check("1001 first entry quantity == -2",
      entries[0]["quantity"] == -2, str(entries[0]["quantity"]))
check("1001 last entry type is 'receipt'",
      entries[-1]["type"] == "receipt", entries[-1]["type"])
check("1001 last entry quantity == 20",
      entries[-1]["quantity"] == 20, str(entries[-1]["quantity"]))

check("1001 first balance equals availability on_hand",
      entries[0]["balance"] == 18, str(entries[0]["balance"]))
check("1001 quantities sum to on_hand",
      sum(e["quantity"] for e in entries) == 18,
      f"sum={sum(e['quantity'] for e in entries)}")

stamps = [e["occurred_at"] for e in entries]
check("1001 occurred_at newest first",
      stamps == sorted(stamps, reverse=True), str(stamps))

# ── Test 7: out-of-order movements (occurred_at older, id higher) ─────────────
print("\n[Test 7] Out-of-order insert – sort by occurred_at ASC, id ASC; balances cumulative")
PART_7 = 9003
EARLY_TS = "2026-02-01T06:00:00Z"
LATE_TS  = "2026-02-01T12:00:00Z"

with db.tx() as c:
    insert_part(c, PART_7)
    # Insert the LATER event first so it gets a lower id
    db.record_movement(c, "receipt",   PART_7, 100, 10, LATE_TS)   # id N
    # Insert the EARLIER event second so it gets a higher id
    db.record_movement(c, "adjustment", PART_7, 100, -3, EARLY_TS) # id N+1

st, data = ledger(PART_7)
check("T7 status 200", st == 200, f"Got {st}")
entries = data["entries"]
check("T7 has 2 entries", len(entries) == 2, f"got {len(entries)}")

# Newest first → LATE_TS first
check("T7 first entry is LATE_TS",  entries[0]["occurred_at"] == LATE_TS,  entries[0]["occurred_at"])
check("T7 second entry is EARLY_TS", entries[1]["occurred_at"] == EARLY_TS, entries[1]["occurred_at"])

# Chronological running balance: -3 → 7  (adjustment first, then receipt)
# Reversed: entry[0] = receipt → balance 7; entry[1] = adjustment → balance -3
check("T7 entry[0] balance == 7",  entries[0]["balance"] == 7,  str(entries[0]))
check("T7 entry[1] balance == -3", entries[1]["balance"] == -3, str(entries[1]))

check("T7 quantities sum to on_hand",
      sum(e["quantity"] for e in entries) == 7,
      f"sum={sum(e['quantity'] for e in entries)}")

# ── Test 8: known part with no movements ─────────────────────────────────────
print("\n[Test 8] Known part with no movements → 200, entries = []")
PART_8 = 9004
with db.tx() as c:
    insert_part(c, PART_8)

st, data = ledger(PART_8)
check("T8 status 200", st == 200, f"Got {st}")
check("T8 entries is []", data["entries"] == [], str(data))

# ── Test 9: required fields and types in every ledger entry ──────────────────
print("\n[Test 9] Every ledger entry has all openapi.yaml required fields with correct types")
st, data = ledger(1001)
for i, e in enumerate(data["entries"]):
    check(f"T9 entry[{i}] has movement_id (int)", isinstance(e["movement_id"], int), str(e))
    check(f"T9 entry[{i}] has type (str)",        isinstance(e["type"], str),        str(e))
    check(f"T9 entry[{i}] has part_id (int)",     isinstance(e["part_id"], int),     str(e))
    check(f"T9 entry[{i}] has location_id (int)", isinstance(e["location_id"], int), str(e))
    check(f"T9 entry[{i}] has quantity (int)",     isinstance(e["quantity"], int),    str(e))
    check(f"T9 entry[{i}] has occurred_at (str)", isinstance(e["occurred_at"], str), str(e))
    check(f"T9 entry[{i}] has 'reference' key",   "reference" in e,                  str(e))
    check(f"T9 entry[{i}] has balance (int)",      isinstance(e["balance"], int),     str(e))

# ── Test 10: stock_balances.on_hand == sum(ledger_movements.quantity) ─────────
print("\n[Test 10] stock_balances.on_hand == SUM(ledger_movements.quantity) for every part/loc")
balance_rows = db.query("SELECT part_id, location_id, on_hand FROM stock_balances")
for row in balance_rows:
    pid, lid, balance = row["part_id"], row["location_id"], row["on_hand"]
    total = db.query_one(
        "SELECT COALESCE(SUM(quantity),0) AS s FROM ledger_movements "
        "WHERE part_id=? AND location_id=?", (pid, lid)
    )["s"]
    check(f"T10 part={pid} loc={lid}: balance({balance}) == sum_qty({total})",
          balance == total, f"balance={balance} sum={total}")

# ── Test 11: both endpoints are read-only (no row changes) ───────────────────
print("\n[Test 11] Both endpoints are read-only – row counts unchanged after calling them")

def row_counts():
    counts = {}
    for tbl in ["ledger_movements", "stock_balances", "reservations",
                "shortages", "parts", "locations", "outbox"]:
        r = db.query_one(f"SELECT COUNT(*) AS n FROM {tbl}")
        counts[tbl] = r["n"]
    return counts

before = row_counts()
avail(1001)
avail(1004)
ledger(1001)
ledger(1004)
after = row_counts()
check("T11 no row counts changed after reads", before == after,
      f"before={before}\nafter={after}")

# ── Summary ──────────────────────────────────────────────────────────────────
print(f"\n==========================================")
print(f"ALL TESTS DONE: {_passed}/{_total} passed")
print(f"==========================================")
if _passed < _total:
    sys.exit(1)

