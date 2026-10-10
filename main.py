"""
main.py - Inventory Service API
Real endpoints: GET /parts/{id}/availability, GET /parts/{id}/ledger,
                POST /transfers, POST /counts, POST /issues  (dev1_api.py)
Fake endpoints: GET+POST for Dev 2 and Dev 3 (stubs, replaced later)
"""
from typing import Optional
from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

import db
from dev1_api import router as dev1_real_router

# ---------- app ---------------------------------------------------------------
app = FastAPI(title="Servicio de inventario del taller", version="1.0.0")

# Ensure tables exist both at import time (in-process tests) and at ASGI startup
db.init_db()

@app.on_event("startup")
def _startup():
    db.init_db()

# ---------- CORS --------------------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- error handlers ({"detail": "<plain string>"} everywhere) -----------
@app.exception_handler(RequestValidationError)
async def _validation_handler(request: Request, exc: RequestValidationError):
    errors = [
        f"{' -> '.join(str(x) for x in e.get('loc', []))}: {e.get('msg', 'error')}"
        for e in exc.errors()
    ]
    return JSONResponse(status_code=422, content={"detail": "; ".join(errors)})

@app.exception_handler(StarletteHTTPException)
async def _http_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})

# ==================== DEV 1 — real routes (dev1_api.py) =======================
app.include_router(dev1_real_router)

# ==================== DEV 2 — fake stubs =====================================
dev2_router = APIRouter(tags=["Dev 2 - Materials & Shortages (Fake stubs)"])

@dev2_router.get("/work-orders/{code}/materials")
def get_materials(code: str):
    if code == "UNKNOWN":
        raise HTTPException(404, "Orden de trabajo no encontrada")
    return {
        "work_order_id": 2001, "code": code,
        "lines": [
            {"inspection_item_id": 701, "bom_line_id": 501, "part_id": 1001,
             "name": "Rodamiento rígido 6204-2RS", "required": 5, "reserved": 3, "issued": 2, "missing": 0},
            {"inspection_item_id": 702, "bom_line_id": 502, "part_id": 1004,
             "name": "Tornillo Allen M8x25 Grado 12.9", "required": 4, "reserved": 0, "issued": 0, "missing": 4},
        ],
    }

@dev2_router.get("/shortages")
def get_shortages(work_order_code: Optional[str] = None, part_id: Optional[int] = None):
    all_shortages = [
        {"work_order_id": 2002, "work_order_code": "R061026002", "part_id": 1004,
         "name": "Tornillo Allen M8x25 Grado 12.9", "missing_quantity": 4,
         "inspection_item_id": 702, "opened_at": "2026-10-06T11:00:00Z"},
        {"work_order_id": 2002, "work_order_code": "R061026002", "part_id": None,
         "name": "Kit especial sin identificar", "missing_quantity": None,
         "inspection_item_id": 703, "opened_at": "2026-10-06T11:05:00Z"},
    ]
    items = [
        s for s in all_shortages
        if (work_order_code is None or s["work_order_code"] == work_order_code)
        and (part_id is None or s["part_id"] == part_id)
    ]
    return {"items": items}

app.include_router(dev2_router)

# ==================== DEV 3 — fake stubs =====================================
dev3_router = APIRouter(tags=["Dev 3 - Purchasing & Matching (Fake stubs)"])

class ResolveRequest(BaseModel):
    part_id: int
    location_id: Optional[int] = None

@dev3_router.get("/reorder-suggestions")
def get_reorder_suggestions():
    return {
        "items": [
            {"part_id": 1004, "sku": "TOR-M8-25", "name": "Tornillo Allen M8x25 Grado 12.9",
             "suggested_quantity": 10, "work_order_ids": [2002]}
        ]
    }

@dev3_router.get("/unmatched-receipts")
def get_unmatched_receipts():
    return {
        "items": [
            {
                "id": 1, "purchase_line_id": 801, "part_number": "UNKNOWN-BEARING-44",
                "description": "Rodamiento desconocido recibido", "quantity": 3,
                "unit_price": "250.00", "currency": "MXN",
                "received_at": "2026-10-07T12:00:00Z", "work_order_code": "R061026001",
            }
        ]
    }

@dev3_router.post("/unmatched-receipts/{id}/resolve", status_code=200)
def post_resolve(id: str, req: ResolveRequest):
    if str(id) == "999" or req.part_id == 999:
        raise HTTPException(404, "Recepción o pieza no encontrada")
    if str(id) == "998":
        raise HTTPException(409, "La recepción ya fue resuelta")
    return {
        "movement_id": 103, "type": "receipt", "part_id": req.part_id,
        "location_id": req.location_id or 100, "quantity": 3,
        "occurred_at": "2026-10-07T14:00:00Z", "reference": f"RESOLVED-{id}",
    }

app.include_router(dev3_router)

