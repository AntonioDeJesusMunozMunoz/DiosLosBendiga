"""
test_dev3.py - Pruebas de Backend Dev 3 (compras, matching, surtido, reabasto, endpoints).
Python plano, sin pytest ni Kafka. Usa una base temporal. Correr:
    py -3 test_dev3.py
"""
import json
import os
import tempfile
import uuid

os.environ["INVENTORY_DB"] = os.path.join(tempfile.mkdtemp(), "t_dev3.db")

import db
import dev3_api
import inventory_handlers
from fastapi import HTTPException
from processor import Processor

db.init_db()
inventory_handlers.init_dev2_schema()
DLQ = []
P = Processor(lambda key, raw, kind, detail: DLQ.append((kind, detail)) or True)
T = "2026-10-10T16:00:00Z"
TOPIC = {"part": "shop.catalog", "bom": "shop.catalog", "location": "shop.catalog",
         "work_order": "shop.work_orders", "inspection": "shop.inspections",
         "purchase": "shop.purchasing"}
_ids = iter(range(5000, 99999))


def ev(etype, key, data, eid=None):
    return {"event_id": eid or str(uuid.uuid4()), "event_type": etype, "event_version": 1,
            "occurred_at": T, "source": "shop-floor", "key": key, "data": data}


def send(e):
    out = P.process(TOPIC[e["event_type"].split(".")[0]], json.dumps(e).encode(), e["key"].encode())
    if P.retry_requested and P.has_pending():
        P.retry_pending()
    return out


def outbox(etype=None):
    rows = [(r["event_type"], r["key"], json.loads(r["data"])) for r in db.query("SELECT * FROM outbox ORDER BY seq")]
    return [r for r in rows if etype is None or r[0] == etype]


def part(pid, sku):
    return ev("part.upserted", f"part:{pid}", {"part_id": pid, "sku": sku, "name": f"P{pid}", "family": "REFACCIÓN",
              "group": None, "subgroup": None, "unit": None, "image_url": None, "active": True})


def bom(model, lines):
    return ev("bom.upserted", f"model:{model}", {"model_id": model, "model_name": "M", "brand": "B", "lines": [
        {"bom_line_id": bl, "part_id": pid, "name": f"L{bl}", "group_name": None, "unit": None,
         "qty_per_unit": q, "sort_order": bl} for bl, pid, q in lines]})


def opened(wo, code, model):
    return ev("work_order.opened", code, {"work_order_id": wo, "code": code, "model_id": model,
              "department": "spindles", "received_at": T})


def approve(code, wo, insp, items):
    return ev("inspection.approved", code, {"inspection_id": insp, "work_order_id": wo, "kind": "full",
              "items": [{"inspection_item_id": iid, "bom_line_id": bl, "part_id": pid, "name": f"L{bl}",
                         "group_name": None, "condition": "damaged", "action": "buy", "quantity": q, "note": None}
                        for iid, bl, pid, q in items],
              "approved_by": 1, "approved_at": T})


def purchase(part_number, qty, purpose="restock", wo_code=None, line_id=None):
    line_id = line_id or next(_ids)
    return ev("purchase.item_received", f"PR-{line_id}", {
        "purchase_request_id": next(_ids), "request_number": str(line_id), "line_id": line_id,
        "part_number": part_number, "description": f"compra {part_number}", "quantity": qty,
        "unit_price": "100.00", "currency": "MXN", "purpose": purpose, "received_at": T,
        "work_order_code": wo_code})


def totals(pid):
    return db.on_hand_total(pid), db.reserved_for_part(pid)


def ok(name, cond, extra=""):
    assert cond, f"FAIL {name} {extra}"
    print("PASS", name)


# ---------------------------------------------------------------- catálogo
send(ev("location.upserted", "location:100", {"location_id": 100, "code": "U-100", "name": "Recepción",
                                              "is_workbench": False, "active": True}))
send(part(1, "CICLO-1"))
send(part(2, "CLI-1"))
send(part(3, "MANUAL-1"))
send(part(4, "DUP"))
send(part(5, "DUP"))
send(part(6, "TOR, CAB, BAJA"))
send(part(7, "PRIO-1"))
send(bom(10, [(1, 1, 8), (2, 7, 2)]))

# ------------------------------------------- 1. ciclo: reserva 5, falta 3, compra surte
send(purchase("CICLO-1", 5))
ok("restock suma existencia en U-100", totals(1) == (5, 0) and db.on_hand_at(1, 100) == 5)
send(opened(100, "R100", 10))
send(approve("R100", 100, 900, [(11, 1, 1, 8)]))
ok("aprobación reserva 5 y deja faltante 3", totals(1) == (5, 5)
   and db.query_one("SELECT missing_quantity FROM shortages WHERE work_order_id=100")["missing_quantity"] == 3)
sug = [d for _, _, d in outbox("stock.reorder_suggested") if d["part_id"] == 1]
ok("stock.reorder_suggested con la orden", sug and sug[-1]["suggested_quantity"] >= 3 and 100 in sug[-1]["work_order_ids"])
items = dev3_api.list_reorder_suggestions()["items"]
ok("GET /reorder-suggestions", any(s["part_id"] == 1 and s["suggested_quantity"] >= 3 and s["work_order_ids"] == [100]
                                   and s["sku"] == "CICLO-1" for s in items), items)

send(purchase("  ciclo-1 ", 3, wo_code="R100"))     # minúsculas y espacios: debe coincidir
res = [(k, d) for _, k, d in outbox("stock.shortage_resolved") if d["work_order_id"] == 100]
ok("compra surte el faltante (shortage_resolved 3, key = código de orden)",
   res and res[-1][1]["quantity"] == 3 and res[-1][0] == "R100")
rsv = [d for _, _, d in outbox("stock.reserved") if d["work_order_id"] == 100]
ok("stock.reserved trae inspection_item_id", rsv[-1]["quantity"] == 3 and rsv[-1]["inspection_item_id"] == 11)
ok("todo reservado tras la compra (8, 8)", totals(1) == (8, 8))
ok("sin faltantes abiertos", db.query("SELECT 1 FROM shortages WHERE work_order_id=100 AND status='open'") == [])
ok("la sugerencia desaparece", all(s["part_id"] != 1 for s in dev3_api.list_reorder_suggestions()["items"]))

# ------------------------------------------- 2. compra para cliente
n = len(outbox())
send(purchase("CLI-1", 6, purpose="customer_order"))
ok("customer_order no entra ni emite nada", totals(2) == (0, 0) and len(outbox()) == n)

# ------------------------------------------- 3. recepción sin relacionar + resolver
e = purchase("PROV-XYZ", 2)
send(e)
line = e["data"]["line_id"]
ok("número desconocido -> stock.unmatched_receipt",
   any(d["purchase_line_id"] == line for _, _, d in outbox("stock.unmatched_receipt")))
pend = [r for r in dev3_api.list_unmatched_receipts()["items"] if r["purchase_line_id"] == line]
ok("GET /unmatched-receipts la muestra", len(pend) == 1)
mov = dev3_api.resolve_unmatched_receipt(str(pend[0]["id"]), dev3_api.ResolveRequest(part_id=3))
ok("resolve responde movimiento receipt en U-100", mov["type"] == "receipt" and mov["location_id"] == 100
   and mov["quantity"] == 2)
ok("resolve emite stock.received y suma existencia",
   any(d["purchase_line_id"] == line and d["part_id"] == 3 for _, _, d in outbox("stock.received")) and totals(3) == (2, 0))
for rid, body, code in [(pend[0]["id"], 3, 409), (99999, 3, 404), ("abc", 3, 404)]:
    try:
        dev3_api.resolve_unmatched_receipt(str(rid), dev3_api.ResolveRequest(part_id=body))
        ok(f"resolve {rid} -> {code}", False)
    except HTTPException as ex:
        ok(f"resolve {rid} -> {code}", ex.status_code == code, ex.status_code)
ok("ya no aparece en pendientes", all(r["purchase_line_id"] != line for r in dev3_api.list_unmatched_receipts()["items"]))

# ------------------------------------------- 4. sku ambiguo y etiquetas con comas
e = purchase("dup", 1)
send(e)
ok("sku ambiguo no entra solo", totals(4) == (0, 0) and totals(5) == (0, 0)
   and not any(d["purchase_line_id"] == e["data"]["line_id"] for _, _, d in outbox("stock.received")))
send(purchase("tor, cab, baja", 4))
ok("acepta 'TOR, CAB, BAJA'", totals(6) == (4, 0))

# ------------------------------------------- 5. prioridad: primero la orden de la compra
send(opened(200, "R200", 10))
send(opened(300, "R300", 10))
send(approve("R200", 200, 901, [(21, 2, 7, 2)]))   # orden vieja: faltan 2
send(approve("R300", 300, 902, [(31, 2, 7, 2)]))   # orden nueva: faltan 2
send(purchase("PRIO-1", 2, wo_code="R300"))
o = {r["work_order_id"]: r["missing_quantity"] for r in
     db.query("SELECT work_order_id, missing_quantity FROM shortages WHERE part_id=7 AND status='open'")}
ok("surte primero la orden de work_order_code (R300)", o == {200: 2}, o)
send(purchase("PRIO-1", 1))
o = {r["work_order_id"]: r["missing_quantity"] for r in
     db.query("SELECT work_order_id, missing_quantity FROM shortages WHERE part_id=7 AND status='open'")}
ok("sin código, surte la más antigua (R200)", o == {200: 1}, o)

# ------------------------------------------- 6. idempotencia
e = purchase("CLI-1", 5)                      # restock de la pieza 2
before = totals(2)
ok("primera vez ok", send(e) == "ok")
ok("duplicado se ignora", send(e) == "duplicate" and totals(2) == (before[0] + 5, 0))

ok("nada en DLQ", DLQ == [], DLQ)
print("\nTODAS LAS PRUEBAS DE DEV 3 PASARON")