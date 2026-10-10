"""
verify.py - Verification script for Fake Inventory API (Backend Dev 1)
Calls every endpoint in openapi.yaml, validates status codes, required fields,
error responses ({"detail": "<plain string>"}), and awkward test cases.
Can run in-process or against a live URL:
  py -3 verify.py
  py -3 verify.py http://localhost:8000
"""
import sys
from typing import Any, Dict

def run_tests():
    url_base = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else None

    if url_base:
        import urllib.request
        import json

        print(f"--> Testing live server at {url_base}")
        def request(method: str, path: str, body: dict = None) -> tuple[int, Any]:
            req = urllib.request.Request(
                f"{url_base}{path}",
                data=json.dumps(body).encode("utf-8") if body is not None else None,
                headers={"Content-Type": "application/json"},
                method=method,
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    raw = resp.read().decode("utf-8")
                    return resp.status, json.loads(raw) if raw else None
            except urllib.error.HTTPError as err:
                raw = err.read().decode("utf-8")
                return err.code, json.loads(raw) if raw else None
    else:
        from fastapi.testclient import TestClient
        from main import app
        print("--> Testing in-process with FastAPI TestClient")
        client = TestClient(app)

        def request(method: str, path: str, body: dict = None) -> tuple[int, Any]:
            resp = client.request(method, path, json=body)
            try:
                return resp.status_code, resp.json()
            except Exception:
                return resp.status_code, resp.text

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
        check(f"{name} status is {expected_status}", status == expected_status, f"Got status {status}, body: {body}")
        check(f"{name} has detail", isinstance(body, dict) and "detail" in body, f"Body missing detail: {body}")
        check(f"{name} detail is plain string", isinstance(body.get("detail"), str), f"detail is not str: {type(body.get('detail'))}")

    print("\n--- 1. Availability Endpoints ---")
    st, data = request("GET", "/parts/1001/availability")
    check("GET /parts/{id}/availability 200", st == 200, f"Got {st}")
    req_avail = {"part_id", "on_hand", "reserved", "available", "locations"}
    check("Availability required fields", req_avail.issubset(data.keys()), f"Missing keys in {data.keys()}")
    check("Root balance math available = on_hand - reserved", data["available"] == data["on_hand"] - data["reserved"], f"{data}")
    check("Locations list has items", len(data["locations"]) > 0, "locations is empty")
    loc0 = data["locations"][0]
    check("Location entry schema", {"location_id", "on_hand", "reserved", "available"}.issubset(loc0.keys()), f"{loc0}")

    # Awkward case: zero stock
    st, data = request("GET", "/parts/1004/availability")
    check("Zero stock part (1004) returns 200", st == 200, f"Got {st}")
    check("Zero stock has on_hand == 0", data["on_hand"] == 0 and data["available"] == 0, f"{data}")

    # 404 test
    st, data = request("GET", "/parts/999/availability")
    assert_error_detail(st, data, 404, "Unknown part availability 404")

    print("\n--- 2. Ledger Endpoints ---")
    st, data = request("GET", "/parts/1001/ledger")
    check("GET /parts/{id}/ledger 200", st == 200, f"Got {st}")
    check("Ledger required fields", {"part_id", "entries"}.issubset(data.keys()), f"{data.keys()}")
    entries = data["entries"]
    check("Ledger has entries", len(entries) >= 4, f"Got {len(entries)}")
    entry0 = entries[0]
    req_entry = {"movement_id", "type", "part_id", "location_id", "quantity", "occurred_at", "reference", "balance"}
    check("Ledger entry required fields", req_entry.issubset(entry0.keys()), f"{entry0.keys()}")
    stamps = [e["occurred_at"] for e in entries]
    check("Ledger newest first", stamps == sorted(stamps, reverse=True), f"Stamps: {stamps}")

    # 404 test
    st, data = request("GET", "/parts/999/ledger")
    assert_error_detail(st, data, 404, "Unknown part ledger 404")

    print("\n--- 3. Issues Endpoints ---")
    issue_payload = {"work_order_code": "R061026001", "part_id": 1001, "location_id": 101, "quantity": 2, "issued_by": "operador-1"}
    st, data = request("POST", "/issues", issue_payload)
    check("POST /issues returns 201", st == 201, f"Got {st}: {data}")
    req_move = {"movement_id", "type", "part_id", "location_id", "quantity", "occurred_at", "reference"}
    check("Movement required fields in issue", req_move.issubset(data.keys()), f"{data.keys()}")
    check("Quantity is negative in movement", data["quantity"] == -2, f"{data['quantity']}")

    # 404 on unknown part/order
    st, data = request("POST", "/issues", {**issue_payload, "part_id": 999})
    assert_error_detail(st, data, 404, "POST /issues unknown part 404")

    # 409 when quantity exceeds stock
    st, data = request("POST", "/issues", {**issue_payload, "quantity": 99})
    assert_error_detail(st, data, 409, "POST /issues excess quantity 409")

    # 422 validation error
    st, data = request("POST", "/issues", {"part_id": 1001})  # missing required fields
    assert_error_detail(st, data, 422, "POST /issues invalid body 422")

    print("\n--- 4. Transfers Endpoints ---")
    tr_payload = {"part_id": 1001, "from_location_id": 100, "to_location_id": 101, "quantity": 5}
    st, data = request("POST", "/transfers", tr_payload)
    check("POST /transfers returns 201", st == 201, f"Got {st}: {data}")
    check("Movement required fields in transfer", req_move.issubset(data.keys()), f"{data.keys()}")

    # 404 on unknown part
    st, data = request("POST", "/transfers", {**tr_payload, "part_id": 999})
    assert_error_detail(st, data, 404, "POST /transfers unknown part 404")

    # 409 when quantity exceeds stock
    st, data = request("POST", "/transfers", {**tr_payload, "quantity": 99})
    assert_error_detail(st, data, 409, "POST /transfers excess quantity 409")

    print("\n--- 5. Counts Endpoints ---")
    count_payload = {
        "location_id": 101,
        "reason": "conteo ciclico mensual",
        "lines": [{"part_id": 1001, "counted_quantity": 8}],
    }
    st, data = request("POST", "/counts", count_payload)
    check("POST /counts returns 201", st == 201, f"Got {st}: {data}")
    check("POST /counts response has adjustments", "adjustments" in data, f"{data}")
    check("Adjustments item has part_id and delta", {"part_id", "delta"}.issubset(data["adjustments"][0].keys()), f"{data}")

    # 422 on missing/empty reason
    st, data = request("POST", "/counts", {**count_payload, "reason": "   "})
    assert_error_detail(st, data, 422, "POST /counts empty reason 422")

    print("\n--- 6. Materials Endpoints ---")
    st, data = request("GET", "/work-orders/R061026001/materials")
    check("GET /work-orders/{code}/materials 200", st == 200, f"Got {st}")
    check("WorkOrderMaterials required fields", {"work_order_id", "code", "lines"}.issubset(data.keys()), f"{data.keys()}")
    line0 = data["lines"][0]
    req_lines = {"inspection_item_id", "bom_line_id", "part_id", "name", "required", "reserved", "issued", "missing"}
    check("Materials line required fields", req_lines.issubset(line0.keys()), f"{line0.keys()}")
    check("Materials line 2 is part 1004 with missing 4", data["lines"][1]["part_id"] == 1004 and data["lines"][1]["missing"] == 4, f"{data['lines']}")

    # 404 on unknown work order
    st, data = request("GET", "/work-orders/UNKNOWN/materials")
    assert_error_detail(st, data, 404, "Unknown order materials 404")

    print("\n--- 7. Shortages Endpoints ---")
    st, data = request("GET", "/shortages")
    check("GET /shortages 200", st == 200, f"Got {st}")
    check("Shortages returns items list", "items" in data and isinstance(data["items"], list), f"{data}")
    req_shortage = {"work_order_id", "work_order_code", "part_id", "name", "missing_quantity", "inspection_item_id"}
    check("Shortage item required fields", req_shortage.issubset(data["items"][0].keys()), f"{data['items'][0].keys()}")

    # Normal open shortage is for part 1004 (zero stock)
    part_1004_shortages = [s for s in data["items"] if s["part_id"] == 1004 and s["missing_quantity"] == 4]
    check("Open shortage for part 1004 exists", len(part_1004_shortages) > 0, f"{data['items']}")

    # Awkward case: one shortage with part_id null and missing_quantity null
    null_shortages = [s for s in data["items"] if s["part_id"] is None and s["missing_quantity"] is None]
    check("Awkward shortage with null part_id and null missing_quantity exists", len(null_shortages) > 0, f"{data['items']}")

    # Filtering by work_order_code
    st, data = request("GET", "/shortages?work_order_code=R061026002")
    check("GET /shortages with work_order_code filter 200", st == 200 and len(data["items"]) > 0, f"{data}")

    print("\n--- 8. Reorder Suggestions Endpoints ---")
    st, data = request("GET", "/reorder-suggestions")
    check("GET /reorder-suggestions 200", st == 200, f"Got {st}")
    check("Reorder suggestions returns items list", "items" in data and isinstance(data["items"], list), f"{data}")
    req_sugg = {"part_id", "suggested_quantity", "work_order_ids"}
    check("Reorder suggestion required fields", req_sugg.issubset(data["items"][0].keys()), f"{data['items'][0].keys()}")
    check("Reorder suggestion points to part 1004", data["items"][0]["part_id"] == 1004, f"{data['items']}")

    print("\n--- 9. Unmatched Receipts Endpoints ---")
    st, data = request("GET", "/unmatched-receipts")
    check("GET /unmatched-receipts 200", st == 200, f"Got {st}")
    check("Unmatched receipts returns items list", "items" in data and isinstance(data["items"], list), f"{data}")
    req_unmatched = {"id", "purchase_line_id", "part_number", "description", "quantity", "received_at"}
    check("Unmatched receipt required fields", req_unmatched.issubset(data["items"][0].keys()), f"{data['items'][0].keys()}")
    check("Unmatched receipt has id=1 and purchase_line_id=801", data["items"][0]["id"] == 1 and data["items"][0]["purchase_line_id"] == 801, f"{data['items'][0]}")

    print("\n--- 10. Resolve Unmatched Receipt Endpoints ---")
    resolve_payload = {"part_id": 1001, "location_id": 100}
    st, data = request("POST", "/unmatched-receipts/1/resolve", resolve_payload)
    check("POST /unmatched-receipts/1/resolve 200", st == 200, f"Got {st}: {data}")
    check("Movement required fields in resolve", req_move.issubset(data.keys()), f"{data.keys()}")

    # 404 on unknown receipt
    st, data = request("POST", "/unmatched-receipts/999/resolve", resolve_payload)
    assert_error_detail(st, data, 404, "Resolve unknown receipt 404")

    # 409 on already resolved receipt
    st, data = request("POST", "/unmatched-receipts/998/resolve", resolve_payload)
    assert_error_detail(st, data, 409, "Resolve already resolved receipt 409")

    print("\n--- 11. Unknown Route Contract Error Format ---")
    st, data = request("GET", "/does-not-exist")
    assert_error_detail(st, data, 404, "GET /does-not-exist returns 404 with string detail")

    print(f"\n==========================================")
    print(f"VERIFICATION COMPLETE: {passed}/{total} checks PASSED successfully!")
    print(f"==========================================")

if __name__ == "__main__":
    run_tests()

