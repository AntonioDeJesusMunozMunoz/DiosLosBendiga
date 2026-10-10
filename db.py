"""
db.py - Database layer (Backend Dev 1)

Everything that touches SQLite goes through this file.
  - SCHEMA        : all tables (ours + the ones shared with Dev 2 and Dev 3)
  - tx()          : the ONLY way to write. One writer at a time, all-or-nothing.
  - query()       : the way to read.
  - record_movement / reserved_for_part / available_for_part : ledger helpers
  - enqueue_event : put an output event in the outbox (published to Kafka later)
"""
import json
import os
import sqlite3
import threading
import uuid
from contextlib import contextmanager

DB_PATH = os.environ.get("INVENTORY_DB", "inventory.db")

# isolation_level=None -> we control BEGIN/COMMIT ourselves (see tx()).
_conn = sqlite3.connect(DB_PATH, check_same_thread=False, isolation_level=None)
_conn.row_factory = sqlite3.Row          # rows behave like dicts: row["on_hand"]
_conn.execute("PRAGMA journal_mode=WAL")  # readers don't block the writer
_conn.execute("PRAGMA synchronous=NORMAL")
_conn.execute("PRAGMA busy_timeout=5000")
_lock = threading.RLock()                 # one thread at a time touches the connection

SCHEMA = """
-- ============ REFERENCE DATA (filled from shop.catalog events) ============
CREATE TABLE IF NOT EXISTS parts (
  part_id   INTEGER PRIMARY KEY,
  sku       TEXT,                 -- may be NULL
  sku_norm  TEXT,                 -- trimmed + uppercase, for matching purchases
  name      TEXT NOT NULL,
  family    TEXT, grp TEXT, subgroup TEXT,
  unit_name TEXT,
  active    INTEGER NOT NULL DEFAULT 1
);
-- NOT unique: the catalog has different parts sharing the same sku
CREATE INDEX IF NOT EXISTS idx_parts_sku_norm ON parts(sku_norm);

CREATE TABLE IF NOT EXISTS locations (
  location_id  INTEGER PRIMARY KEY,
  code         TEXT NOT NULL,     -- barcode, e.g. 'U-101'
  name         TEXT,
  is_workbench INTEGER NOT NULL DEFAULT 0,
  active       INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_locations_code ON locations(code);

-- Bill of materials: parts list per model (needed when quantity is null)
CREATE TABLE IF NOT EXISTS bom_lines (
  model_id     INTEGER NOT NULL,
  bom_line_id  INTEGER NOT NULL,
  part_id      INTEGER,
  name         TEXT NOT NULL,
  group_name   TEXT,
  qty_per_unit INTEGER,
  sort_order   INTEGER,
  PRIMARY KEY (model_id, bom_line_id)
);

CREATE TABLE IF NOT EXISTS work_orders (
  work_order_id INTEGER PRIMARY KEY,
  code          TEXT NOT NULL UNIQUE,
  model_id      INTEGER,
  department    TEXT,
  stage         TEXT,
  received_at   TEXT,
  deleted       INTEGER NOT NULL DEFAULT 0
);

-- ============ THE CORE (our responsibility) ============
-- The ledger: append-only. Never UPDATE or DELETE rows here.
CREATE TABLE IF NOT EXISTS ledger_movements (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  type        TEXT NOT NULL CHECK (type IN
              ('receipt','issue','transfer_out','transfer_in','adjustment')),
  part_id     INTEGER NOT NULL,
  location_id INTEGER NOT NULL,
  quantity    INTEGER NOT NULL,       -- signed: negative when stock leaves
  occurred_at TEXT NOT NULL,          -- from the event/request, never "now" on replay
  reference   TEXT,                   -- order code, purchase line, or count reason
  unit_cost   TEXT,                   -- decimal as text
  currency    TEXT
);
CREATE INDEX IF NOT EXISTS idx_ledger_part ON ledger_movements(part_id, id);

-- Projection of the ledger: on_hand per part and shelf. Rebuildable from the ledger.
CREATE TABLE IF NOT EXISTS stock_balances (
  part_id     INTEGER NOT NULL,
  location_id INTEGER NOT NULL,
  on_hand     INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (part_id, location_id)
);

-- ============ SHARED WITH DEV 2 / DEV 3 ============
CREATE TABLE IF NOT EXISTS processed_events (
  event_id TEXT PRIMARY KEY           -- idempotency: insert in the same tx as the effects
);

-- Reservations are per PART (not per shelf). reserved(part) is computed from here.
CREATE TABLE IF NOT EXISTS reservations (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  work_order_id      INTEGER NOT NULL,
  bom_line_id        INTEGER NOT NULL,
  inspection_item_id INTEGER,
  source_inspection_id INTEGER,       -- which approval set the current need
  part_id            INTEGER NOT NULL,
  quantity           INTEGER NOT NULL,          -- amount reserved
  issued_quantity    INTEGER NOT NULL DEFAULT 0,
  status             TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active','fulfilled','cancelled')),
  UNIQUE (work_order_id, bom_line_id)           -- no double reservations
);
CREATE INDEX IF NOT EXISTS idx_res_part ON reservations(part_id, status);

CREATE TABLE IF NOT EXISTS shortages (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  work_order_id      INTEGER NOT NULL,
  work_order_code    TEXT,                      -- from key/event, needed by OpenAPI /shortages
  bom_line_id        INTEGER NOT NULL,
  inspection_item_id INTEGER,
  source_inspection_id INTEGER,
  part_id            INTEGER,                   -- NULL = unidentified part
  name               TEXT,
  missing_quantity   INTEGER,                   -- NULL allowed
  status             TEXT NOT NULL DEFAULT 'open'
                     CHECK (status IN ('open','resolved','cancelled')),
  opened_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_short_open ON shortages(status, part_id);
CREATE INDEX IF NOT EXISTS idx_short_wo_code ON shortages(work_order_code);

CREATE TABLE IF NOT EXISTS unmatched_receipts (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  purchase_line_id  INTEGER NOT NULL,
  part_number       TEXT,
  description       TEXT,
  quantity          INTEGER,
  unit_price        TEXT,
  currency          TEXT,
  received_at       TEXT,
  work_order_code   TEXT,
  status            TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved'))
);

CREATE TABLE IF NOT EXISTS part_policies (
  part_id INTEGER PRIMARY KEY,
  min_qty INTEGER, max_qty INTEGER, reorder_qty INTEGER, lead_time_days INTEGER
);

CREATE TABLE IF NOT EXISTS reorder_suggestions (
  part_id            INTEGER PRIMARY KEY,
  suggested_quantity INTEGER NOT NULL,
  work_order_ids     TEXT NOT NULL            -- JSON list, e.g. "[1203,1210]"
);

-- Events that arrived too early (missing part/order/line). Retried later.
CREATE TABLE IF NOT EXISTS pending_events (
  event_id TEXT PRIMARY KEY,
  topic    TEXT,
  payload  TEXT NOT NULL,                     -- original event JSON
  attempts INTEGER NOT NULL DEFAULT 0
);

-- Outbox: output events are saved in the SAME transaction as the ledger change,
-- then a publisher sends them to Kafka (inventory.events) and marks them sent.
CREATE TABLE IF NOT EXISTS outbox (
  seq        INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id   TEXT NOT NULL UNIQUE,
  event_type TEXT NOT NULL,
  key        TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  data       TEXT NOT NULL,                   -- JSON
  published  INTEGER NOT NULL DEFAULT 0
);
"""


def init_db():
    """Create all tables (safe to call every startup)."""
    with _lock:
        _conn.executescript(SCHEMA)


@contextmanager
def tx():
    """The ONLY way to write.

        with tx() as c:
            c.execute(...)
            record_movement(c, ...)

    - The lock means one writer at a time (no two threads grabbing the last unit).
    - BEGIN IMMEDIATE locks the DB for writing from the start.
    - If anything raises, ROLLBACK undoes everything (all-or-nothing).
    Never call executescript() inside a tx(); it commits the transaction early.
    """
    with _lock:
        _conn.execute("BEGIN IMMEDIATE")
        try:
            yield _conn
            if _conn.in_transaction:
                _conn.execute("COMMIT")
        except BaseException:
            if _conn.in_transaction:
                _conn.execute("ROLLBACK")
            raise


def query(sql, params=()):
    """Read helper. Returns a list of rows (dict-like)."""
    with _lock:
        return _conn.execute(sql, params).fetchall()


def query_one(sql, params=()):
    with _lock:
        return _conn.execute(sql, params).fetchone()


# ---------------------------------------------------------------- ledger helpers
def record_movement(c, type, part_id, location_id, quantity, occurred_at,
                    reference=None, unit_cost=None, currency=None):
    """Append a movement AND update on_hand. Call inside `with tx() as c`.
    Does NOT check for negative stock: callers (issues/transfers) check first."""
    cur = c.execute(
        "INSERT INTO ledger_movements "
        "(type, part_id, location_id, quantity, occurred_at, reference, unit_cost, currency) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (type, part_id, location_id, quantity, occurred_at, reference, unit_cost, currency))
    c.execute(
        "INSERT INTO stock_balances (part_id, location_id, on_hand) VALUES (?,?,?) "
        "ON CONFLICT(part_id, location_id) DO UPDATE SET on_hand = on_hand + excluded.on_hand",
        (part_id, location_id, quantity))
    return cur.lastrowid


def on_hand_at(c_or_part_id, part_id=None, location_id=None):
    """Get on_hand for part at location.
    Usage: on_hand_at(c, part_id, location_id) or on_hand_at(part_id, location_id)"""
    if location_id is None:
        c, p_id, loc_id = _conn, c_or_part_id, part_id
    else:
        c, p_id, loc_id = c_or_part_id, part_id, location_id
    with _lock:
        row = c.execute("SELECT on_hand FROM stock_balances WHERE part_id=? AND location_id=?",
                        (p_id, loc_id)).fetchone()
        return row["on_hand"] if row else 0


def on_hand_total(c_or_part_id, part_id=None):
    """Get total on_hand for part across all locations.
    Usage: on_hand_total(c, part_id) or on_hand_total(part_id)"""
    if part_id is None:
        c, p_id = _conn, c_or_part_id
    else:
        c, p_id = c_or_part_id, part_id
    with _lock:
        row = c.execute("SELECT COALESCE(SUM(on_hand),0) AS n FROM stock_balances WHERE part_id=?",
                        (p_id,)).fetchone()
        return row["n"]


def reserved_for_part(c_or_part_id, part_id=None):
    """Units promised to orders but not yet taken out.
    Usage: reserved_for_part(c, part_id) or reserved_for_part(part_id)"""
    if part_id is None:
        c, p_id = _conn, c_or_part_id
    else:
        c, p_id = c_or_part_id, part_id
    with _lock:
        row = c.execute(
            "SELECT COALESCE(SUM(quantity - issued_quantity),0) AS n FROM reservations "
            "WHERE part_id=? AND status='active'", (p_id,)).fetchone()
        return row["n"]


def available_for_part(c_or_part_id, part_id=None):
    """Available = on_hand - reserved.
    Usage: available_for_part(c, part_id) or available_for_part(part_id)"""
    if part_id is None:
        c, p_id = _conn, c_or_part_id
    else:
        c, p_id = c_or_part_id, part_id
    return on_hand_total(c, p_id) - reserved_for_part(c, p_id)


def enqueue_event(c, event_type, key, data, occurred_at):
    """Save an output event in the outbox. A publisher sends it to Kafka later."""
    event_id = str(uuid.uuid4())
    c.execute("INSERT INTO outbox (event_id, event_type, key, occurred_at, data) "
              "VALUES (?,?,?,?,?)",
              (event_id, event_type, key, occurred_at, json.dumps(data)))
    return event_id
