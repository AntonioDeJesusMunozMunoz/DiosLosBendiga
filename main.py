"""
main.py - Inventory Service API
Real endpoints: GET /parts/{id}/availability, GET /parts/{id}/ledger  (dev1_api.py)
Fake  endpoints: POST /issues, /transfers, /counts  (Dev 1 stubs)
                 GET+POST for Dev 2 and Dev 3 (stubs, replaced later)
"""
from contextlib import asynccontextmanager
from dev2_api import router as real_dev2_router
import db, inventory_handlers, kafka_consumer

from typing import Optional, Union
from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from dev1_api import router as dev1_real_router

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    inventory_handlers.init_dev2_schema()   # creates my needs table
    kafka_consumer.start()
    yield
    kafka_consumer.stop()

# ---------- app ---------------------------------------------------------------
app = FastAPI(title="Servicio de inventario del taller", version="1.0.0", lifespan=lifespan)

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

# ==================== DEV 1 — real GET routes (dev1_api.py) ===================
app.include_router(dev1_real_router)

# ==================== DEV 1 — fake POST stubs =================================
dev1_router = APIRouter(tags=["Dev 1 - Ledger & Stock (Fake stubs)"])

class IssueRequest(BaseModel):
    work_order_code: str
    part_id: int
    location_id: int
    quantity: int = Field(ge=1)
    issued_by: Union[int, str]

class TransferRequest(BaseModel):
    part_id: int
    from_location_id: int
    to_location_id: int
    quantity: int = Field(ge=1)

class CountLine(BaseModel):
    part_id: int
    counted_quantity: int = Field(ge=0)

class CountRequest(BaseModel):
    location_id: int
    reason: str
    lines: list[CountLine]

@dev1_router.post("/issues", status_code=201)
def post_issue(req: IssueRequest):
    if req.part_id == 999 or req.work_order_code == "UNKNOWN":
        raise HTTPException(404, "Pieza u orden no encontrada")
    if req.quantity > 50:
        raise HTTPException(409, "Saldo insuficiente en la ubicación")
    return {
        "movement_id": 101, "type": "issue", "part_id": req.part_id,
        "location_id": req.location_id, "quantity": -req.quantity,
        "occurred_at": "2026-10-06T12:00:00Z", "reference": req.work_order_code,
    }

@dev1_router.post("/transfers", status_code=201)
def post_transfer(req: TransferRequest):
    if req.part_id == 999 or req.from_location_id == 999:
        raise HTTPException(404, "Pieza o ubicación no encontrada")
    if req.quantity > 50:
        raise HTTPException(409, "Saldo insuficiente en ubicación origen")
    return {
        "movement_id": 102, "type": "transfer_out", "part_id": req.part_id,
        "location_id": req.from_location_id, "quantity": -req.quantity,
        "occurred_at": "2026-10-06T12:05:00Z",
        "reference": f"TR-{req.from_location_id}-{req.to_location_id}",
    }

@dev1_router.post("/counts", status_code=201)
def post_count(req: CountRequest):
    if not req.reason or not req.reason.strip():
        raise HTTPException(422, "El motivo del conteo es obligatorio")
    if req.location_id == 999:
        raise HTTPException(404, "Ubicación no encontrada")
    adjustments = [
        {"part_id": line.part_id, "delta": line.counted_quantity - 10}
        for line in req.lines if line.counted_quantity != 10
    ]
    return {"adjustments": adjustments}

app.include_router(dev1_router)

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

app.include_router(dev1_router)
app.include_router(real_dev2_router)
app.include_router(dev3_router)

