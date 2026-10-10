"""
main.py - Fake Inventory Service API (Temporary scaffolding for frontend team)
Implements all 10 paths from openapi.yaml with fixed, deterministic demo responses.
"""
from contextlib import asynccontextmanager
from dev2_api import router as real_dev2_router
import db, inventory_handlers, kafka_consumer

from typing import Optional, Union
from fastapi import FastAPI, APIRouter, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    inventory_handlers.init_dev2_schema()   # creates my needs table
    kafka_consumer.start()
    yield
    kafka_consumer.stop()

app = FastAPI(title="Servicio de inventario del taller (Fake API)", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom exception handlers ensuring {"detail": "<plain string>"} format
@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    errors = [f"{' -> '.join(str(x) for x in e.get('loc', []))}: {e.get('msg', 'error')}" for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": "; ".join(errors)})

@app.exception_handler(StarletteHTTPException)
async def http_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc.detail)})

# ==================== DEV 1 ROUTER (Ledger & Stock) ====================
dev1_router = APIRouter(tags=["Dev 1 - Ledger & Stock"])

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

@dev1_router.get("/parts/{part_id}/availability")
def get_availability(part_id: int):
    if part_id == 999:
        raise HTTPException(404, "Pieza no encontrada")
    if part_id == 1004:  # Awkward case: zero stock
        return {
            "part_id": 1004, "on_hand": 0, "reserved": 0, "available": 0,
            "locations": [{"location_id": 100, "code": "U-100", "on_hand": 0, "reserved": 0, "available": 0}],
        }
    return {
        "part_id": part_id, "on_hand": 18, "reserved": 3, "available": 15,
        "locations": [
            {"location_id": 100, "code": "U-100", "on_hand": 10, "reserved": 0, "available": 10},
            {"location_id": 101, "code": "U-101", "on_hand": 8, "reserved": 0, "available": 8},
        ],
    }

@dev1_router.get("/parts/{part_id}/ledger")
def get_ledger(part_id: int):
    if part_id == 999:
        raise HTTPException(404, "Pieza no encontrada")
    return {
        "part_id": part_id,
        "entries": [
            {"movement_id": 4, "type": "issue", "part_id": part_id, "location_id": 101, "quantity": -2, "occurred_at": "2026-10-05T14:00:00Z", "reference": "R061026001", "balance": 18},
            {"movement_id": 3, "type": "transfer_in", "part_id": part_id, "location_id": 101, "quantity": 10, "occurred_at": "2026-10-03T11:00:00Z", "reference": "TR-001", "balance": 20},
            {"movement_id": 2, "type": "transfer_out", "part_id": part_id, "location_id": 100, "quantity": -10, "occurred_at": "2026-10-03T11:00:00Z", "reference": "TR-001", "balance": 10},
            {"movement_id": 1, "type": "receipt", "part_id": part_id, "location_id": 100, "quantity": 20, "occurred_at": "2026-10-03T10:00:00Z", "reference": "PR-1001", "balance": 20},
        ],
    }

@dev1_router.post("/issues", status_code=201)
def post_issue(req: IssueRequest):
    if req.part_id == 999 or req.work_order_code == "UNKNOWN":
        raise HTTPException(404, "Pieza u orden no encontrada")
    if req.quantity > 50:
        raise HTTPException(409, "Saldo insuficiente en la ubicación")
    return {
        "movement_id": 101, "type": "issue", "part_id": req.part_id, "location_id": req.location_id,
        "quantity": -req.quantity, "occurred_at": "2026-10-06T12:00:00Z", "reference": req.work_order_code,
    }

@dev1_router.post("/transfers", status_code=201)
def post_transfer(req: TransferRequest):
    if req.part_id == 999 or req.from_location_id == 999:
        raise HTTPException(404, "Pieza o ubicación no encontrada")
    if req.quantity > 50:
        raise HTTPException(409, "Saldo insuficiente en ubicación origen")
    return {
        "movement_id": 102, "type": "transfer_out", "part_id": req.part_id, "location_id": req.from_location_id,
        "quantity": -req.quantity, "occurred_at": "2026-10-06T12:05:00Z", "reference": f"TR-{req.from_location_id}-{req.to_location_id}",
    }

@dev1_router.post("/counts", status_code=201)
def post_count(req: CountRequest):
    if not req.reason or not req.reason.strip():
        raise HTTPException(422, "El motivo del conteo es obligatorio")
    if req.location_id == 999:
        raise HTTPException(404, "Ubicación no encontrada")
    adjustments = [{"part_id": line.part_id, "delta": line.counted_quantity - 10} for line in req.lines if line.counted_quantity != 10]
    return {"adjustments": adjustments}

# ==================== DEV 2 ROUTER (Materials & Shortages) ====================
dev2_router = APIRouter(tags=["Dev 2 - Materials & Shortages"])

@dev2_router.get("/work-orders/{code}/materials")
def get_materials(code: str):
    if code == "UNKNOWN":
        raise HTTPException(404, "Orden de trabajo no encontrada")
    return {
        "work_order_id": 2001, "code": code,
        "lines": [
            {"inspection_item_id": 701, "bom_line_id": 501, "part_id": 1001, "name": "Rodamiento rígido 6204-2RS", "required": 5, "reserved": 3, "issued": 2, "missing": 0},
            {"inspection_item_id": 702, "bom_line_id": 502, "part_id": 1004, "name": "Tornillo Allen M8x25 Grado 12.9", "required": 4, "reserved": 0, "issued": 0, "missing": 4},
        ],
    }

@dev2_router.get("/shortages")
def get_shortages(work_order_code: Optional[str] = None, part_id: Optional[int] = None):
    all_shortages = [
        # Normal open shortage (on part 1004 with zero stock)
        {"work_order_id": 2002, "work_order_code": "R061026002", "part_id": 1004, "name": "Tornillo Allen M8x25 Grado 12.9", "missing_quantity": 4, "inspection_item_id": 702, "opened_at": "2026-10-06T11:00:00Z"},
        # Awkward case: part_id null and missing_quantity null
        {"work_order_id": 2002, "work_order_code": "R061026002", "part_id": None, "name": "Kit especial sin identificar", "missing_quantity": None, "inspection_item_id": 703, "opened_at": "2026-10-06T11:05:00Z"},
    ]
    items = [
        s for s in all_shortages
        if (work_order_code is None or s["work_order_code"] == work_order_code)
        and (part_id is None or s["part_id"] == part_id)
    ]
    return {"items": items}

# ==================== DEV 3 ROUTER (Purchases & Matching) ====================
dev3_router = APIRouter(tags=["Dev 3 - Purchasing & Matching"])

class ResolveRequest(BaseModel):
    part_id: int
    location_id: Optional[int] = None

@dev3_router.get("/reorder-suggestions")
def get_reorder_suggestions():
    # Awkward case: one reorder suggestion (on part 1004 with zero stock)
    return {
        "items": [
            {"part_id": 1004, "sku": "TOR-M8-25", "name": "Tornillo Allen M8x25 Grado 12.9", "suggested_quantity": 10, "work_order_ids": [2002]}
        ]
    }

@dev3_router.get("/unmatched-receipts")
def get_unmatched_receipts():
    # Awkward case: one unmatched receipt (id 1, purchase_line_id 801 consistent with seed)
    return {
        "items": [
            {
                "id": 1, "purchase_line_id": 801, "part_number": "UNKNOWN-BEARING-44",
                "description": "Rodamiento desconocido recibido", "quantity": 3,
                "unit_price": "250.00", "currency": "MXN", "received_at": "2026-10-07T12:00:00Z",
                "work_order_code": "R061026001",
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
