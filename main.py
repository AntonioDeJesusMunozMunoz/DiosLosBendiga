"""
main.py - Inventory Service API
Real endpoints: Dev 1 (availability, ledger, transfers, counts, issues in dev1_api.py)
                Dev 2 (materials, shortages in dev2_api.py)
                Dev 3 (unmatched-receipts, resolve, reorder-suggestions in dev3_api.py)
Extras piso:    GET /scan, GET /work-orders (piso_api.py) para el escáner del celular
Frontend:       carpeta frontend/ servida en /app  (ej. https://<IP>:8000/app/salida.html)
"""
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

import db
import inventory_handlers
import kafka_consumer
from dev1_api import router as dev1_real_router
from dev2_api import router as real_dev2_router
from dev3_api import router as dev3_real_router
from piso_api import router as piso_router

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

# ==================== DEV 3 — real routes (dev3_api.py) =======================
app.include_router(dev3_real_router)

# ==================== PISO — escáner del celular (piso_api.py) ================
app.include_router(piso_router)

# ==================== FRONTEND — páginas en /app ===============================
FRONTEND_DIR = Path(__file__).parent / "frontend"
app.mount("/app", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


@app.get("/", include_in_schema=False)
def _home():
    return RedirectResponse("/app/salida.html")
