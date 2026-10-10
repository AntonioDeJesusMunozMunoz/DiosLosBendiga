"""
verify.py - Contract verification for the Inventory Service API
Checks every path in openapi.yaml: status codes, required fields, error shapes.

In-process mode (no argument): creates a temporary SQLite DB, runs seed.py,
then imports main.  Run:
    py -3 verify.py

Live mode: pass the server URL. seed.py must have been run against the same DB
first.  Run:
    py -3 seed.py
    py -3 -m uvicorn main:app --port 8000
    py -3 verify.py http://localhost:8000
"""
import sys
from typing import Any


def run_tests():
    url_base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else None

    # ---- transport layer ----
    if url_base:
        import urllib.request
        import json

        print(f"--> Testing live server at {url_base}")
        print("    (reminder: transfers and counts now change the database, so seed.py must be run again before each live run)")

        def request(method: str, path: str, body: dict = None) -> tuple[int, Any]:
            req = urllib.request.Request(
                f"{url_base}{path}",
                data=json.dumps(body).encode() if body is not None else None,
                headers={"Content-Type": "application/json"},
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    raw = resp.read().decode()
                    return resp.status, json.loads(raw) if raw else None
            except urllib.error.HTTPError as err:
                raw = err.read().decode()
                return err.code, json.loads(raw) if raw else None

    else:
        import os, tempfile

        # Must set INVENTORY_DB before importing db or main
        _tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        _tmp.close()
        os.environ["INVENTORY_DB"] = _tmp.name

        import db            # now picks up the temp path
        import seed
        seed.seed()          # populate demo data

        from fastapi.testclient import TestClient
        from main import app  # imports db again but module is already cached

        print(f"--> Testing in-process with FastAPI TestClient (db: {_tmp.name})")
        client = TestClient(app)

        def request(method: str, path: str, body: dict = None) -> tuple[int, Any]:
            resp = client.request(method, path, json=body)
            try:
                return resp.status_code, resp.json()
            except Exception:
                return resp.status_code, resp.text

    # ---- helpers ----
    passed = 0
    total = 0

    def check(name: str, condition: bool, err_msg: str):
        nonlocal passed, total
        total += 1
        if condition:
            passed += 1
            print(f"  [PASS] {name}")
        else:
            print(f"  [FAIL] {name}: {err_msg}")
            raise AssertionError(f"{name}: {err_msg}")

    def assert_error_detail(status: int, body: Any, expected_status: int, name: str):
        check(f"{name} status is {expected_status}", status == expected_status,
              f"Got status {status}, body: {body}")
        check(f"{name} has detail",
              isinstance(body, dict) and "detail" in body, f"Body missing detail: {body}")
        check(f"{name} detail is plain string",
              isinstance(body.get("detail"), str), f"detail is not str: {type(body.get('detail'))}")

    # ---- 1. Availability (now real) ----
    print("\n--- 1. Availability Endpoints ---")
    st, data = request("GET", "/parts/1001/availability")
    check("GET /parts/{id}/availability 200", st == 200, f"Got {st}")
    req_avail = {"part_id", "on_hand", "reserved", "available", "locations"}
    check("Availability required fields", req_avail.issubset(data.keys()),
          f"Missing keys in {data.keys()}")
    check("Root balance math available = on_hand - reserved",
          data["available"] == data["on_hand"] - data["reserved"], f"{data}")
    check("on_hand is 18 from seed", data["on_hand"] == 18, f"on_hand={data['on_hand']}")
    check("reserved is 3 from seed", data["reserved"] == 3, f"reserved={data['reserved']}")
    check("available is 15 from seed", data["available"] == 15, f"available={data['available']}")
    check("Locations list has items", len(data["locations"]) > 0, "locations is empty")
    loc0 = data["locations"][0]
    check("Location entry required fields",
          {"location_id", "on_hand", "reserved", "available"}.issubset(loc0.keys()), f"{loc0}")
    check("Location reserved is 0", all(l["reserved"] == 0 for l in data["locations"]),
          "some location has reserved != 0")
    loc_sum = sum(l["on_hand"] for l in data["locations"])
    check("Location on_hand sums to root on_hand", loc_sum == data["on_hand"],
          f"sum={loc_sum} root={data['on_hand']}")

    # part 1004: known, zero stock → 200 with zeros and empty locations
    st, data = request("GET", "/parts/1004/availability")
    check("Zero-stock part (1004) returns 200", st == 200, f"Got {st}")
    check("Zero-stock has on_hand==0", data["on_hand"] == 0, f"{data}")
    check("Zero-stock has reserved==0", data["reserved"] == 0, f"{data}")
    check("Zero-stock has available==0", data["available"] == 0, f"{data}")
    check("Zero-stock locations is empty list", data["locations"] == [], f"{data['locations']}")

    # unknown part → 404
    st, data = request("GET", "/parts/999/availability")
    assert_error_detail(st, data, 404, "Unknown part availability 404")

    # ---- 2. Ledger (now real) ----
    print("\n--- 2. Ledger Endpoints ---")
    st, data = request("GET", "/parts/1001/ledger")
    check("GET /parts/{id}/ledger 200", st == 200, f"Got {st}")
    check("Ledger required fields", {"part_id", "entries"}.issubset(data.keys()),
          f"{data.keys()}")
    entries = data["entries"]
    check("Ledger has 4 entries from seed", len(entries) == 4, f"Got {len(entries)}")
    req_entry = {"movement_id", "type", "part_id", "location_id",
                 "quantity", "occurred_at", "reference", "balance"}
    check("Ledger entry required fields", req_entry.issubset(entries[0].keys()),
          f"{entries[0].keys()}")
    stamps = [e["occurred_at"] for e in entries]
    check("Ledger newest first", stamps == sorted(stamps, reverse=True), f"Stamps: {stamps}")
    check("First entry balance equals on_hand", entries[0]["balance"] == 18,
          f"balance={entries[0]['balance']}")
    check("Quantities sum to on_hand",
          sum(e["quantity"] for e in entries) == 18, f"sum={sum(e['quantity'] for e in entries)}")

    # unknown part → 404
    st, data = request("GET", "/parts/999/ledger")
    assert_error_detail(st, data, 404, "Unknown part ledger 404")

    # ---- 3. Issues (fake) ----
    print("\n--- 3. Issues Endpoints ---")
    issue_payload = {"work_order_code": "R061026001", "part_id": 1001,
                     "location_id": 101, "quantity": 2, "issued_by": "operador-1"}
    st, data = request("POST", "/issues", issue_payload)
    check("POST /issues returns 201", st == 201, f"Got {st}: {data}")
    req_move = {"movement_id", "type", "part_id", "location_id", "quantity", "occurred_at", "reference"}
    check("Movement required fields in issue", req_move.issubset(data.keys()), f"{data.keys()}")
    check("Quantity is negative in movement", data["quantity"] == -2, f"{data['quantity']}")

    st, data = request("POST", "/issues", {**issue_payload, "part_id": 999})
    assert_error_detail(st, data, 404, "POST /issues unknown part 404")

    st, data = request("POST", "/issues", {**issue_payload, "quantity": 99})
    assert_error_detail(st, data, 409, "POST /issues excess quantity 409")

    st, data = request("POST", "/issues", {"part_id": 1001})
    assert_error_detail(st, data, 422, "POST /issues invalid body 422")

    # ---- 4. Transfers (real) ----
    print("\n--- 4. Transfers Endpoints ---")
    tr_payload = {"part_id": 1001, "from_location_id": 100, "to_location_id": 101, "quantity": 5}
    st, data = request("POST", "/transfers", tr_payload)
    check("POST /transfers returns 201", st == 201, f"Got {st}: {data}")
    check("Movement required fields in transfer", req_move.issubset(data.keys()), f"{data.keys()}")

    st, data = request("POST", "/transfers", {**tr_payload, "part_id": 999})
    assert_error_detail(st, data, 404, "POST /transfers unknown part 404")

    st, data = request("POST", "/transfers", {**tr_payload, "quantity": 99})
    assert_error_detail(st, data, 409, "POST /transfers excess quantity 409")

    # ---- 5. Counts (real) ----
    print("\n--- 5. Counts Endpoints ---")
    count_payload = {
        "location_id": 101, "reason": "conteo ciclico mensual",
        "lines": [{"part_id": 1001, "counted_quantity": 8}],
    }
    st, data = request("POST", "/counts", count_payload)
    check("POST /counts returns 201", st == 201, f"Got {st}: {data}")
    check("POST /counts response has adjustments", "adjustments" in data, f"{data}")
    check("Adjustments item has part_id and delta",
          {"part_id", "delta"}.issubset(data["adjustments"][0].keys()), f"{data}")
    check("POST /counts adjustments == [{'part_id': 1001, 'delta': -5}]",
          data.get("adjustments") == [{"part_id": 1001, "delta": -5}], f"{data}")

    st, data = request("POST", "/counts", {**count_payload, "reason": "   "})
    assert_error_detail(st, data, 422, "POST /counts empty reason 422")

    st, data = request("POST", "/counts", {**count_payload, "location_id": 999})
    assert_error_detail(st, data, 404, "POST /counts unknown location 404")

    # ---- 6. Materials (fake Dev 2) ----
    print("\n--- 6. Materials Endpoints ---")
    st, data = request("GET", "/work-orders/R061026001/materials")
    check("GET /work-orders/{code}/materials 200", st == 200, f"Got {st}")
    check("WorkOrderMaterials required fields",
          {"work_order_id", "code", "lines"}.issubset(data.keys()), f"{data.keys()}")
    line0 = data["lines"][0]
    req_lines = {"inspection_item_id", "bom_line_id", "part_id", "name",
                 "required", "reserved", "issued", "missing"}
    check("Materials line required fields", req_lines.issubset(line0.keys()), f"{line0.keys()}")
    check("Materials line 2 is part 1004 with missing 4",
          data["lines"][1]["part_id"] == 1004 and data["lines"][1]["missing"] == 4,
          f"{data['lines']}")

    st, data = request("GET", "/work-orders/UNKNOWN/materials")
    assert_error_detail(st, data, 404, "Unknown order materials 404")

    # ---- 7. Shortages (fake Dev 2) ----
    print("\n--- 7. Shortages Endpoints ---")
    st, data = request("GET", "/shortages")
    check("GET /shortages 200", st == 200, f"Got {st}")
    check("Shortages returns items list",
          "items" in data and isinstance(data["items"], list), f"{data}")
    req_shortage = {"work_order_id", "work_order_code", "part_id", "name",
                    "missing_quantity", "inspection_item_id"}
    check("Shortage item required fields",
          req_shortage.issubset(data["items"][0].keys()), f"{data['items'][0].keys()}")

    part_1004_shortages = [s for s in data["items"]
                           if s["part_id"] == 1004 and s["missing_quantity"] == 4]
    check("Open shortage for part 1004 exists", len(part_1004_shortages) > 0, f"{data['items']}")

    null_shortages = [s for s in data["items"]
                      if s["part_id"] is None and s["missing_quantity"] is None]
    check("Awkward shortage with null part_id and null missing_quantity",
          len(null_shortages) > 0, f"{data['items']}")

    st, data = request("GET", "/shortages?work_order_code=R061026002")
    check("GET /shortages with work_order_code filter 200",
          st == 200 and len(data["items"]) > 0, f"{data}")

    # ---- 8. Reorder Suggestions (fake Dev 3) ----
    print("\n--- 8. Reorder Suggestions Endpoints ---")
    st, data = request("GET", "/reorder-suggestions")
    check("GET /reorder-suggestions 200", st == 200, f"Got {st}")
    check("Reorder suggestions returns items list",
          "items" in data and isinstance(data["items"], list), f"{data}")
    req_sugg = {"part_id", "suggested_quantity", "work_order_ids"}
    check("Reorder suggestion required fields",
          req_sugg.issubset(data["items"][0].keys()), f"{data['items'][0].keys()}")
    check("Reorder suggestion points to part 1004",
          data["items"][0]["part_id"] == 1004, f"{data['items']}")

    # ---- 9. Unmatched Receipts (fake Dev 3) ----
    print("\n--- 9. Unmatched Receipts Endpoints ---")
    st, data = request("GET", "/unmatched-receipts")
    check("GET /unmatched-receipts 200", st == 200, f"Got {st}")
    check("Unmatched receipts returns items list",
          "items" in data and isinstance(data["items"], list), f"{data}")
    req_unmatched = {"id", "purchase_line_id", "part_number", "description", "quantity", "received_at"}
    check("Unmatched receipt required fields",
          req_unmatched.issubset(data["items"][0].keys()), f"{data['items'][0].keys()}")
    check("Unmatched receipt has id=1 and purchase_line_id=801",
          data["items"][0]["id"] == 1 and data["items"][0]["purchase_line_id"] == 801,
          f"{data['items'][0]}")

    # ---- 10. Resolve (fake Dev 3) ----
    print("\n--- 10. Resolve Unmatched Receipt Endpoints ---")
    resolve_payload = {"part_id": 1001, "location_id": 100}
    st, data = request("POST", "/unmatched-receipts/1/resolve", resolve_payload)
    check("POST /unmatched-receipts/1/resolve 200", st == 200, f"Got {st}: {data}")
    check("Movement required fields in resolve", req_move.issubset(data.keys()), f"{data.keys()}")

    st, data = request("POST", "/unmatched-receipts/999/resolve", resolve_payload)
    assert_error_detail(st, data, 404, "Resolve unknown receipt 404")

    st, data = request("POST", "/unmatched-receipts/998/resolve", resolve_payload)
    assert_error_detail(st, data, 409, "Resolve already resolved receipt 409")

    # ---- 11. Unknown route → 404 with {"detail": str} ----
    print("\n--- 11. Unknown Route Contract Error Format ---")
    st, data = request("GET", "/does-not-exist")
    assert_error_detail(st, data, 404, "GET /does-not-exist returns 404 with string detail")

    print(f"\n==========================================")
    print(f"VERIFICATION COMPLETE: {passed}/{total} checks PASSED successfully!")
    print(f"==========================================")


if __name__ == "__main__":
    run_tests()

