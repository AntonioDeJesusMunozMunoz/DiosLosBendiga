"""
dev2_api.py - Dev 2 endpoints (read only), shapes from contract/openapi.yaml:
    GET /work-orders/{code}/materials
    GET /shortages
Include in the app with:  app.include_router(dev2_api.router)
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from db import query, query_one

router = APIRouter()


class MaterialLine(BaseModel):
    inspection_item_id: int
    bom_line_id: int
    part_id: Optional[int]
    name: str
    required: Optional[int]
    reserved: int
    issued: int
    missing: Optional[int]


class WorkOrderMaterials(BaseModel):
    work_order_id: int
    code: str
    lines: list[MaterialLine]


class Shortage(BaseModel):
    work_order_id: int
    work_order_code: Optional[str]
    part_id: Optional[int]
    name: str
    missing_quantity: Optional[int]
    inspection_item_id: Optional[int]
    opened_at: Optional[str] = None


class ShortageList(BaseModel):
    items: list[Shortage]


@router.get("/work-orders/{code}/materials", response_model=WorkOrderMaterials)
def work_order_materials(code: str):
    wo = query_one("SELECT work_order_id, code FROM work_orders WHERE code = ?", (code,))
    if wo is None:
        raise HTTPException(status_code=404, detail=f"work order {code} not found")

    # One line per active need. reserved = units still reserved (not yet issued), so
    # required = reserved + issued + missing.
    rows = query(
        """
        SELECT n.inspection_item_id, n.bom_line_id, n.part_id, n.name,
               n.required_quantity AS required,
               CASE WHEN r.status = 'active' THEN r.quantity - r.issued_quantity ELSE 0 END AS reserved,
               COALESCE(r.issued_quantity, 0) AS issued,
               s.id AS shortage_id, s.missing_quantity AS missing
        FROM needs n
        LEFT JOIN reservations r ON r.work_order_id = n.work_order_id AND r.bom_line_id = n.bom_line_id
        LEFT JOIN shortages s ON s.work_order_id = n.work_order_id AND s.bom_line_id = n.bom_line_id
                             AND s.status = 'open'
        WHERE n.work_order_id = ? AND n.status = 'active'
        ORDER BY n.bom_line_id
        """,
        (wo["work_order_id"],),
    )
    lines = [
        MaterialLine(
            inspection_item_id=r["inspection_item_id"], bom_line_id=r["bom_line_id"],
            part_id=r["part_id"], name=r["name"], required=r["required"],
            reserved=r["reserved"] or 0, issued=r["issued"],
            missing=0 if r["shortage_id"] is None else r["missing"],
        )
        for r in rows
    ]
    return WorkOrderMaterials(work_order_id=wo["work_order_id"], code=wo["code"], lines=lines)


@router.get("/shortages", response_model=ShortageList)
def list_shortages(work_order_code: Optional[str] = Query(None), part_id: Optional[int] = Query(None)):
    sql = (
        "SELECT s.work_order_id, COALESCE(s.work_order_code, w.code) AS work_order_code, s.part_id, "
        "COALESCE(s.name, '') AS name, s.missing_quantity, s.inspection_item_id, s.opened_at "
        "FROM shortages s LEFT JOIN work_orders w ON w.work_order_id = s.work_order_id "
        "WHERE s.status = 'open'"
    )
    params = []
    if work_order_code is not None:
        sql += " AND COALESCE(s.work_order_code, w.code) = ?"
        params.append(work_order_code)
    if part_id is not None:
        sql += " AND s.part_id = ?"
        params.append(part_id)
    sql += " ORDER BY s.id"
    return ShortageList(items=[Shortage(**dict(r)) for r in query(sql, tuple(params))])
