"""
piso_api.py - Endpoints de apoyo para la app de piso (Frontend 1, escáner del celular).
No están en el openapi del contrato: son extras de solo lectura.

    GET /scan?code=<lo que leyó la cámara>
        Dice qué es la etiqueta escaneada:
          {"type": "location",   "location_id": 100, "code": "U-100", "name": ...}
          {"type": "work_order", "work_order_id": 1203, "code": "R061026012"}
          {"type": "part",       "matches": [{"part_id": 24, "sku": "7014 CTA/P4", "name": ...}, ...]}
        Pieza (estándar de etiquetas, sección 5 del contrato):
          1. el sku tal cual          ("7014 CTA/P4", "TOR, CAB, BAJA")
          2. "P-" + part_id           ("P-24", pieza sin sku)
          3. el sku sin espacios y en mayúsculas (por si la etiqueta viene distinta)
        Si un sku es de dos piezas, "matches" trae las dos y la app pide elegir.
        404 si no es nada conocido.

    GET /work-orders
        Órdenes vivas (no borradas), la más nueva primero, para elegirla de una lista.
"""
import re

from fastapi import APIRouter, HTTPException, Query

import db
from dev3_hooks import normalizar

router = APIRouter(tags=["Piso - Escáner (extras)"])

_P_ID = re.compile(r"^P-(\d+)$", re.IGNORECASE)


def _part_rows(rows):
    return [{"part_id": r["part_id"], "sku": r["sku"], "name": r["name"]} for r in rows]


@router.get("/scan")
def scan(code: str = Query(..., min_length=1)):
    raw = code.strip()
    if not raw:
        raise HTTPException(status_code=422, detail="Código vacío")

    loc = db.query_one(
        "SELECT location_id, code, name FROM locations WHERE UPPER(code) = UPPER(?) AND active = 1 LIMIT 1",
        (raw,))
    if loc:
        return {"type": "location", "location_id": loc["location_id"], "code": loc["code"], "name": loc["name"]}

    wo = db.query_one(
        "SELECT work_order_id, code FROM work_orders WHERE UPPER(code) = UPPER(?) AND deleted = 0 LIMIT 1",
        (raw,))
    if wo:
        return {"type": "work_order", "work_order_id": wo["work_order_id"], "code": wo["code"]}

    # 1. sku tal cual
    rows = db.query("SELECT part_id, sku, name FROM parts WHERE sku = ? AND active = 1 ORDER BY part_id", (raw,))
    # 2. P-<part_id>
    if not rows:
        m = _P_ID.match(raw)
        if m:
            rows = db.query("SELECT part_id, sku, name FROM parts WHERE part_id = ?", (int(m.group(1)),))
    # 3. sku normalizado (sin espacios, mayúsculas)
    if not rows:
        target = normalizar(raw)
        rows = [r for r in db.query("SELECT part_id, sku, name FROM parts WHERE sku IS NOT NULL AND active = 1 "
                                    "ORDER BY part_id")
                if normalizar(r["sku"]) == target]
    if rows:
        return {"type": "part", "matches": _part_rows(rows)}

    raise HTTPException(status_code=404, detail=f"Código no reconocido: {raw}")


@router.get("/work-orders")
def list_work_orders(limit: int = Query(30, ge=1, le=200)):
    rows = db.query(
        "SELECT work_order_id, code, stage, received_at FROM work_orders WHERE deleted = 0 "
        "ORDER BY received_at DESC, work_order_id DESC LIMIT ?", (limit,))
    return {"items": [dict(r) for r in rows]}
