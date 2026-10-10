"""
main.py - Inventory Service API
Real endpoints: Dev 1 (availability, ledger, transfers, counts, issues in dev1_api.py)
                Dev 2 (materials, shortages in dev2_api.py)
Fake endpoints: Dev 3 stubs (replaced later)
"""
from contextlib import asynccontextmanager
from typing import Optional
from fastapi import FastAPI, APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

import db
import inventory_handlers
import kafka_consumer
from dev1_api import router as dev1_real_router
from dev2_api import router as real_dev2_router
from dev3_api import router as dev3_real_router

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
inventory_handlers.init_dev2_schema()

@app.on_event("startup")
def _startup():
    db.init_db()
    inventory_handlers.init_dev2_schema()

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

# ==================== DEV 2 — real routes (dev2_api.py) =======================
app.include_router(real_dev2_router)

# ==================== DEV 3 fake stubs =====================================
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
app.include_router(dev3_real_router)