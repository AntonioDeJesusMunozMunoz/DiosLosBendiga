import json, os, uuid
os.environ["INVENTORY_DB"] = "/tmp/t1.db"
import db, inventory_handlers
from processor import Processor
from fastapi import FastAPI
from fastapi.testclient import TestClient
import dev2_api

db.init_db(); inventory_handlers.init_dev2_schema()
dlq = []
P = Processor(lambda key, raw, kind, detail: dlq.append((kind, detail)) or True)
T = "2026-10-06T16:00:00Z"

def ev(etype, key, data, eid=None):
    return {"event_id": eid or str(uuid.uuid4()), "event_type": etype, "event_version": 1,
            "occurred_at": T, "source": "shop-floor", "key": key, "data": data}
TOPIC = {"part": "shop.catalog", "bom": "shop.catalog", "location": "shop.catalog",
         "work_order": "shop.work_orders", "inspection": "shop.inspections"}
def send(e):
    out = P.process(TOPIC[e["event_type"].split(".")[0]], json.dumps(e).encode(), e["key"].encode())
    if P.retry_requested and P.has_pending(): P.retry_pending()
    return out
def outbox():
    return [(r["event_type"], json.loads(r["data"])) for r in db.query("SELECT * FROM outbox ORDER BY seq")]
def stock(part, qty):
    with db.tx() as c: db.record_movement(c, "receipt", part, 100, qty, T)
def item(iid, bl, part, name, cond="damaged", action="buy", qty=None):
    return {"inspection_item_id": iid, "bom_line_id": bl, "part_id": part, "name": name, "group_name": None,
            "condition": cond, "action": action, "quantity": qty, "note": None}
def approve(code, wo, insp, items):
    return ev("inspection.approved", code, {"inspection_id": insp, "work_order_id": wo, "kind": "full",
              "items": items, "approved_by": 1, "approved_at": T})
def part(pid, sku, family="REFACCION"):
    return ev("part.upserted", f"part:{pid}", {"part_id": pid, "sku": sku, "name": f"P{pid}", "family": family,
              "group": None, "subgroup": None, "unit": None, "image_url": None, "active": True})
def opened(wo, code, model=4):
    return ev("work_order.opened", code, {"work_order_id": wo, "code": code, "model_id": model,
              "department": "spindles", "received_at": T})
client = TestClient((lambda a: (a.include_router(dev2_api.router), a)[1])(FastAPI()))

# --- catalog, bom, order
assert send(part(10, " tor-1 ")) == "ok"
assert send(part(11, "ARA")) == "ok"
assert send(part(12, "SRV", family="SERVICIO")) == "ok"
assert db.query_one("SELECT sku_norm FROM parts WHERE part_id=10")["sku_norm"] == "TOR-1"
send(ev("bom.upserted", "model:4", {"model_id": 4, "model_name": "M", "brand": "B", "lines": [
    {"bom_line_id": 1, "part_id": 10, "name": "tor", "group_name": None, "unit": None, "qty_per_unit": 3, "sort_order": 1},
    {"bom_line_id": 2, "part_id": 11, "name": "ara", "group_name": None, "unit": None, "qty_per_unit": None, "sort_order": 2}]}))
assert send(opened(100, "R061026001")) == "ok"
stock(10, 5)

# --- 1. approval: need 8 of part 10 with stock 5 -> reserve 5, shortage 3; bom fallback; repair/ok/servicio/null part
a = approve("R061026001", 100, 900, [
    item(1, 1, 10, "tor", qty=8),
    item(2, 2, 11, "ara", qty=None),          # bom qty null too -> shortage without quantity
    item(3, 3, None, "mystery", qty=2),       # part null -> shortage, never reserve
    item(4, 4, 10, "cust", cond="ok", action=None),
    item(5, 5, 10, "rep", action="repair"),
    item(6, 6, 12, "chrome", qty=1)])         # SERVICIO family: ignored
# line 3 / 4,5,6 have no bom entries; qty given so fine
assert send(a) == "ok"
ob = outbox(); types = [t for t, _ in ob]
print(types)
assert types.count("stock.reserved") == 1 and types.count("stock.shortage_detected") == 3
res = [d for t, d in ob if t == "stock.reserved"][0]
assert res["quantity"] == 5 and res["part_id"] == 10
short = {d["name"]: d["missing_quantity"] for t, d in ob if t == "stock.shortage_detected"}
assert short == {"tor": 3, "ara": None, "mystery": 2}, short
assert db.available_for_part(10) == 0

# --- 2. duplicate: no change, no new outputs
n = len(outbox()); assert send(a) == "duplicate" and len(outbox()) == n

# --- 3. replacement lowers 8 -> 6: shortage shrinks first (3 -> 1), reservation stays 5
assert send(approve("R061026001", 100, 901, [item(1, 1, 10, "tor", qty=6)])) == "ok"
r = db.query_one("SELECT quantity, status FROM reservations WHERE bom_line_id=1")
s = db.query_one("SELECT missing_quantity, status FROM shortages WHERE bom_line_id=1")
assert (r["quantity"], r["status"]) == (5, "active") and s["missing_quantity"] == 1
# lower to 4: shortage cancelled, reservation 5 -> 4
assert send(approve("R061026001", 100, 902, [item(1, 1, 10, "tor", qty=4)])) == "ok"
r = db.query_one("SELECT quantity FROM reservations WHERE bom_line_id=1")
s = db.query_one("SELECT status FROM shortages WHERE bom_line_id=1")
assert r["quantity"] == 4 and s["status"] == "cancelled" and db.available_for_part(10) == 1
# raise to 7 with 1 available: reserve 1, shortage 2
assert send(approve("R061026001", 100, 903, [item(1, 1, 10, "tor", qty=7)])) == "ok"
assert db.query_one("SELECT quantity FROM reservations WHERE bom_line_id=1")["quantity"] == 5
assert db.query_one("SELECT missing_quantity FROM shortages WHERE bom_line_id=1 AND status='open'")["missing_quantity"] == 2
# unmentioned lines keep their need (full inspection with items: [])
assert send(approve("R061026001", 100, 904, [])) == "ok"
assert db.query_one("SELECT status FROM needs WHERE bom_line_id=2")["status"] == "active"

# --- 4. materials / shortages endpoints
m = client.get("/work-orders/R061026001/materials").json()
by = {l["bom_line_id"]: l for l in m["lines"]}
print(m)
assert by[1]["required"] == 7 and by[1]["reserved"] == 5 and by[1]["missing"] == 2 and by[1]["issued"] == 0
assert by[2]["required"] is None and by[2]["missing"] is None
assert client.get("/work-orders/NOPE/materials").status_code == 404
sh = client.get("/shortages", params={"work_order_code": "R061026001"}).json()["items"]
assert {x["name"] for x in sh} == {"tor", "ara", "mystery"}
assert len(client.get("/shortages", params={"part_id": 10}).json()["items"]) == 1

# --- 5. voided: only lines whose current need came from that inspection
send(approve("R061026001", 100, 905, [item(3, 3, None, "mystery", qty=2)]))   # line 3 now from 905
send(ev("inspection.voided", "R061026001", {"inspection_id": 905, "work_order_id": 100, "reason": "x"}))
assert db.query_one("SELECT status FROM needs WHERE bom_line_id=3")["status"] == "cancelled"
assert db.query_one("SELECT status FROM needs WHERE bom_line_id=1")["status"] == "active"

# --- 6. work_order.deleted releases everything and closes shortages
send(ev("work_order.deleted", "R061026001", {"work_order_id": 100, "code": "R061026001"}))
assert db.reserved_for_part(10) == 0 and db.available_for_part(10) == 5
assert client.get("/shortages", params={"work_order_code": "R061026001"}).json()["items"] == []
n = len(outbox()); assert send(approve("R061026001", 100, 906, [item(1, 1, 10, "tor", qty=2)])) == "ok"
assert len(outbox()) == n  # approvals for a deleted order are ignored

# --- 7. out of order: inspection before work order and before part -> parked, then processed
send(approve("R061026002", 200, 910, [item(1, 1, 20, "late", qty=2)]))
assert db.query_one("SELECT COUNT(*) n FROM pending_events")["n"] == 1
# same topic+key later event parks behind it (keeps order)
assert send(ev("inspection.voided", "R061026002", {"inspection_id": 910, "work_order_id": 200, "reason": "x"})) == "parked"
send(opened(200, "R061026002"))                       # trigger: still missing part 20
assert db.query_one("SELECT COUNT(*) n FROM pending_events")["n"] == 2
stock(20, 5)
send(part(20, "LATE"))                                # trigger: approved runs, then voided in order
assert db.query_one("SELECT COUNT(*) n FROM pending_events")["n"] == 0
assert db.query_one("SELECT status FROM needs WHERE work_order_id=200")["status"] == "cancelled"
assert db.reserved_for_part(20) == 0

# --- 8. DLQ: handler blows up -> sink gets it, recorded as processed (no second DLQ on replay)
bad = ev("part.upserted", "part:1", {"part_id": 1})   # missing fields -> KeyError
assert send(bad) == "dlq" and dlq[-1][0] == "unexpected"
assert send(bad) == "duplicate"
assert P.process("shop.catalog", b"not json", b"k") == "dlq" and dlq[-1][0] == "invalid_json"

# --- 9. transaction is all-or-nothing: unknown part in 2nd item leaves nothing from the 1st
before = (len(outbox()), db.query_one("SELECT COUNT(*) n FROM reservations")["n"])
send(opened(300, "R061026003")); stock(10, 5)
send(approve("R061026003", 300, 920, [item(1, 1, 10, "ok-item", qty=1), item(2, 2, 999, "ghost", qty=1)]))
assert db.query_one("SELECT COUNT(*) n FROM reservations WHERE work_order_id=300")["n"] == 0
assert db.query_one("SELECT COUNT(*) n FROM pending_events")["n"] == 1
print("ALL LOGIC TESTS PASSED")
