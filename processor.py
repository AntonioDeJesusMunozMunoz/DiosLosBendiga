"""
processor.py - Dev 2: the per-message pipeline (no Kafka in here).

    process(topic, raw, key):
        duplicate?            -> skip silently
        key has parked events -> park behind them (keeps order per topic + key)
        otherwise: BEGIN IMMEDIATE -> insert event_id -> handler -> COMMIT
            MissingDependency -> rollback, park (retried later, DLQ after PARK_TIMEOUT)
            DB error          -> retry a few times if locked, then DLQ ("db_error")
            anything else     -> rollback, DLQ ("unexpected")

Output events are written to the outbox inside the same transaction by the handlers;
publishing them is kafka_consumer's job. Because there is no Kafka here, replay_events.py
can drive the same code from data/events.jsonl.
"""
import json
import logging
import os
import sqlite3
import time

from db import query, query_one, tx
from errors import MissingDependency
from inventory_handlers import HANDLERS, TRIGGER_TYPES

log = logging.getLogger("processor")

PARK_TIMEOUT = float(os.environ.get("PARK_TIMEOUT_SECONDS", "60"))
DB_RETRIES = 5


class Processor:
    def __init__(self, dlq_sink):
        """dlq_sink(key_bytes, raw_bytes, kind, detail) -> bool (True = delivered)."""
        self.dlq_sink = dlq_sink
        self.retry_requested = False
        self._parked_since = {}   # event_id -> monotonic time of first park (in memory)
        self._reasons = {}        # event_id -> last MissingDependency message

    # ------------------------------------------------------------- entry point
    def process(self, topic, raw, key=None):
        """Returns 'ok' | 'duplicate' | 'parked' | 'dlq'."""
        try:
            ev = json.loads(raw)
            
            # TEMPORARY: skip purchasing events without processing or DLQ.
            # They will be replayed after Dev 3's real handler is integrated.
            if topic == "shop.purchasing":
                return "skipped"

        except (ValueError, TypeError) as e:
            self.dlq_sink(key, raw, "invalid_json", str(e))
            return "dlq"
        if (not isinstance(ev, dict) or "event_id" not in ev or "event_type" not in ev
                or topic not in HANDLERS):
            self.dlq_sink(key, raw, "invalid_envelope", f"topic={topic}")
            return "dlq"

        if self._already_processed(ev["event_id"]):
            return "duplicate"
        if self._blocked_by_parked(topic, ev):
            self._park(topic, ev, raw)
            return "parked"
        return self._attempt(topic, ev, raw, key)

    # --------------------------------------------------------------- one attempt
    def _attempt(self, topic, ev, raw, key):
        handler = HANDLERS[topic]
        event_id = ev["event_id"]
        for attempt in range(DB_RETRIES):
            try:
                with tx() as c:
                    cur = c.execute("INSERT OR IGNORE INTO processed_events (event_id) VALUES (?)",
                                    (event_id,))
                    if cur.rowcount == 0:
                        c.execute("DELETE FROM pending_events WHERE event_id=?", (event_id,))
                        return "duplicate"
                    handler(c, ev)
                    c.execute("DELETE FROM pending_events WHERE event_id=?", (event_id,))
                self._parked_since.pop(event_id, None)
                self._reasons.pop(event_id, None)
                if ev["event_type"] in TRIGGER_TYPES:
                    self.retry_requested = True
                return "ok"
            except MissingDependency as e:
                self._reasons[event_id] = str(e)
                self._park(topic, ev, raw)
                return "parked"
            except sqlite3.OperationalError as e:      # locked / busy: transient, retry
                if attempt < DB_RETRIES - 1:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                return self._fail(key, raw, event_id, "db_error", e)
            except sqlite3.Error as e:
                return self._fail(key, raw, event_id, "db_error", e)
            except Exception as e:
                return self._fail(key, raw, event_id, "unexpected", e)

    # --------------------------------------------------------------- parked events
    def has_pending(self):
        return query_one("SELECT 1 FROM pending_events LIMIT 1") is not None

    def retry_pending(self):
        """Retry every parked event in arrival order. Events of the same topic + key
        stay in order: if one is still blocked, the later ones wait too."""
        self.retry_requested = False
        for _ in range(10):                         # repeat while a pass makes progress
            if self._pass() == 0:
                break

    def _pass(self):
        rows = query("SELECT event_id, topic, payload FROM pending_events ORDER BY rowid")
        blocked, resolved, now = set(), 0, time.monotonic()
        for r in rows:
            ev = json.loads(r["payload"])
            group = (r["topic"], ev.get("key"))
            if group in blocked:
                continue
            raw = r["payload"].encode()
            key = str(ev.get("key", "")).encode()
            outcome = self._attempt(r["topic"], ev, raw, key)
            if outcome != "parked":
                resolved += 1
                continue
            since = self._parked_since.setdefault(r["event_id"], now)
            if now - since > PARK_TIMEOUT:
                reason = self._reasons.get(r["event_id"], "dependency never arrived")
                self._fail(key, raw, r["event_id"], "missing_dependency", RuntimeError(reason))
                resolved += 1
            else:
                blocked.add(group)
        return resolved

    # --------------------------------------------------------------------- helpers
    def _already_processed(self, event_id):
        return query_one("SELECT 1 FROM processed_events WHERE event_id=?", (event_id,)) is not None

    def _blocked_by_parked(self, topic, ev):
        return query_one(
            "SELECT 1 FROM pending_events WHERE topic=? AND json_extract(payload,'$.key')=? LIMIT 1",
            (topic, ev.get("key"))) is not None

    def _park(self, topic, ev, raw):
        payload = raw.decode() if isinstance(raw, (bytes, bytearray)) else raw
        with tx() as c:
            c.execute("INSERT OR IGNORE INTO pending_events (event_id, topic, payload, attempts) "
                      "VALUES (?,?,?,0)", (ev["event_id"], topic, payload))
        self._parked_since.setdefault(ev["event_id"], time.monotonic())
        log.info("parked %s (%s): %s", ev["event_id"], ev["event_type"], self._reasons.get(ev["event_id"], "behind older event"))

    def _fail(self, key, raw, event_id, kind, exc):
        """Send the ORIGINAL event to the DLQ and remember it so a restart does not resend it."""
        log.error("event %s -> DLQ (%s): %s", event_id, kind, exc)
        delivered = self.dlq_sink(key, raw, kind, f"{type(exc).__name__}: {exc}")
        if delivered:
            try:
                with tx() as c:
                    c.execute("INSERT OR IGNORE INTO processed_events (event_id) VALUES (?)", (event_id,))
                    c.execute("DELETE FROM pending_events WHERE event_id=?", (event_id,))
            except sqlite3.Error:
                log.exception("could not record DLQ'd event %s", event_id)
            self._parked_since.pop(event_id, None)
            self._reasons.pop(event_id, None)
        return "dlq"
