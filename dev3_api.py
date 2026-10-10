"""
dev3_api.py - Endpoints de Backend Dev 3 (formas de contract/openapi.yaml):
    GET  /unmatched-receipts
    POST /unmatched-receipts/{id}/resolve
    GET  /reorder-suggestions
Se incluye en la app con:  app.include_router(dev3_api.router)
"""
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import db
import dev3_hooks

router = APIRouter(tags=["Dev 3 - Purchasing & Matching"])


class ResolveRequest(BaseModel):
    part_id: int
    location_id: Optional[int] = None


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@router.get("/unmatched-receipts")
def list_unmatched_receipts():
    rows = db.query(
        "SELECT id, purchase_line_id, part_number, description, quantity, unit_price, currency, "
        "received_at, work_order_code FROM unmatched_receipts WHERE status='open' ORDER BY id")
    return {"items": [dict(r) for r in rows]}


@router.post("/unmatched-receipts/{id}/resolve", status_code=200)
def resolve_unmatched_receipt(id: str, req: ResolveRequest):
    try:
        receipt_id = int(id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Recepción no encontrada")

    with db.tx() as c:
        r = c.execute("SELECT * FROM unmatched_receipts WHERE id=?", (receipt_id,)).fetchone()
        if r is None:
            raise HTTPException(status_code=404, detail="Recepción no encontrada")
        if r["status"] == "resolved":
            raise HTTPException(status_code=409, detail="La recepción ya fue resuelta")
        if c.execute("SELECT 1 FROM parts WHERE part_id=?", (req.part_id,)).fetchone() is None:
            raise HTTPException(status_code=404, detail="Pieza no encontrada")

        if req.location_id is not None:
            if c.execute("SELECT 1 FROM locations WHERE location_id=?", (req.location_id,)).fetchone() is None:
                raise HTTPException(status_code=404, detail="Ubicación no encontrada")
            location_id = req.location_id
        else:
            location_id = dev3_hooks.receiving_location_id(c)

        ts = _now_iso()
        movement_id = dev3_hooks.receive_into(
            c, part_id=req.part_id, location_id=location_id, quantity=r["quantity"],
            purchase_line_id=r["purchase_line_id"], unit_cost=r["unit_price"],
            currency=r["currency"], work_order_code=r["work_order_code"], occurred_at=ts)
        c.execute("UPDATE unmatched_receipts SET status='resolved' WHERE id=?", (receipt_id,))

        return {
            "movement_id": movement_id,
            "type": "receipt",
            "part_id": req.part_id,
            "location_id": location_id,
            "quantity": r["quantity"],
            "occurred_at": ts,
            "reference": f"purchase_line:{r['purchase_line_id']}",
        }


@router.get("/reorder-suggestions")
def list_reorder_suggestions():
    rows = db.query(
        "SELECT rs.part_id, p.sku, COALESCE(p.name, '') AS name, rs.suggested_quantity, rs.work_order_ids "
        "FROM reorder_suggestions rs LEFT JOIN parts p ON p.part_id = rs.part_id "
        "ORDER BY rs.part_id")
    return {"items": [{
        "part_id": r["part_id"],
        "sku": r["sku"],
        "name": r["name"],
        "suggested_quantity": r["suggested_quantity"],
        "work_order_ids": json.loads(r["work_order_ids"]),
    } for r in rows]}