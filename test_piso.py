"""
test_piso.py - Pruebas de los endpoints del escáner (piso_api.py). Python plano. Correr:
    python test_piso.py
"""
import os
import tempfile

os.environ["INVENTORY_DB"] = os.path.join(tempfile.mkdtemp(), "t_piso.db")

import db
import inventory_handlers
import piso_api
from fastapi import HTTPException

db.init_db()
inventory_handlers.init_dev2_schema()
T = "2026-10-10T16:00:00Z"

with db.tx() as c:
    for lid, code in [(100, "U-100"), (101, "U-101")]:
        c.execute("INSERT INTO locations (location_id, code, name) VALUES (?,?,?)", (lid, code, code))
    for pid, sku in [(1, "TORNILLO 6X12"), (2, "7014 CTA/P4"), (3, "TOR, CAB, BAJA"),
                     (4, "DUP"), (5, "DUP"), (24, None)]:
        c.execute("INSERT INTO parts (part_id, sku, sku_norm, name) VALUES (?,?,?,?)",
                  (pid, sku, sku.strip().upper() if sku else None, f"Pieza {pid}"))
    c.execute("INSERT INTO work_orders (work_order_id, code, model_id, received_at) VALUES (1203,'R061026012',4,?)", (T,))
    c.execute("INSERT INTO work_orders (work_order_id, code, model_id, received_at, deleted) "
              "VALUES (1204,'R061026013',4,'2026-10-10T17:00:00Z',1)")
    c.execute("INSERT INTO work_orders (work_order_id, code, model_id, received_at) "
              "VALUES (1205,'R061026014',4,'2026-10-10T18:00:00Z')")


def ok(name, cond, extra=""):
    assert cond, f"FAIL {name} {extra}"
    print("PASS", name)


def ids(r):
    return [m["part_id"] for m in r["matches"]]


r = piso_api.scan("U-100")
ok("ubicación U-100", r["type"] == "location" and r["location_id"] == 100)
r = piso_api.scan(" u-101 ")
ok("ubicación con espacios y minúsculas", r["type"] == "location" and r["location_id"] == 101)
r = piso_api.scan("R061026012")
ok("orden de trabajo", r["type"] == "work_order" and r["work_order_id"] == 1203)
r = piso_api.scan("TORNILLO 6X12")
ok("pieza por sku tal cual", r["type"] == "part" and ids(r) == [1])
r = piso_api.scan("7014 CTA/P4")
ok("sku con diagonal y espacio (no lo toma como 7014)", ids(r) == [2])
r = piso_api.scan("TOR, CAB, BAJA")
ok("sku con comas", ids(r) == [3])
r = piso_api.scan("P-24")
ok("pieza sin sku: P-24", ids(r) == [24])
r = piso_api.scan("tornillo 6x12")
ok("sku en minúsculas (normalizado)", ids(r) == [1])
r = piso_api.scan("DUP")
ok("sku de dos piezas regresa las dos", ids(r) == [4, 5])
for code in ["NADA-123", "R061026013"]:
    try:
        piso_api.scan(code)
        ok(f"{code} -> 404", False)
    except HTTPException as e:
        ok(f"{code} -> 404 (desconocido u orden borrada)", e.status_code == 404)

wo = piso_api.list_work_orders(30)["items"]
ok("lista de órdenes: vivas y la más nueva primero", [w["code"] for w in wo] == ["R061026014", "R061026012"], wo)

print("\nTODAS LAS PRUEBAS DE PISO PASARON")
