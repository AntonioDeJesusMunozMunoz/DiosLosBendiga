"""
replay_events.py - run data/events.jsonl through the SAME processor, no Kafka needed.

    INVENTORY_DB=scratch.db python replay_events.py data/events.jsonl

Run it twice on the same DB: the second run must report everything as duplicate
(idempotency). Catalog events are processed first, like the real startup gate.
Accepts lines that are the event envelope itself, or {"topic":..., "value": {...}}.
"""
import json
import sys
from collections import Counter

import db
import inventory_handlers
from processor import Processor

TOPIC_OF = {
    "part.upserted": "shop.catalog", "location.upserted": "shop.catalog", "bom.upserted": "shop.catalog",
    "work_order.opened": "shop.work_orders", "work_order.stage_changed": "shop.work_orders",
    "work_order.deleted": "shop.work_orders",
    "inspection.submitted": "shop.inspections", "inspection.approved": "shop.inspections",
    "inspection.rejected": "shop.inspections", "inspection.discarded": "shop.inspections",
    "inspection.voided": "shop.inspections",
    "purchase.item_received": "shop.purchasing",
}


def load(path):
    events = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if "event_type" not in obj and "value" in obj:
                obj = obj["value"] if isinstance(obj["value"], dict) else json.loads(obj["value"])
            events.append((TOPIC_OF[obj["event_type"]], obj))
    return events


def main(path):
    db.init_db()
    inventory_handlers.init_dev2_schema()
    dlq = []
    proc = Processor(lambda key, raw, kind, detail: dlq.append((kind, detail, raw)) or True)

    events = load(path)
    ordered = [e for e in events if e[0] == "shop.catalog"] + [e for e in events if e[0] != "shop.catalog"]
    outcomes = Counter()
    for topic, ev in ordered:
        outcomes[proc.process(topic, json.dumps(ev).encode(), str(ev.get("key", "")).encode())] += 1
        if proc.retry_requested and proc.has_pending():
            proc.retry_pending()
    proc.retry_pending()

    print("events:", len(ordered), dict(outcomes))
    print("parked still:", len(db.query("SELECT 1 FROM pending_events")))
    print("outbox rows:", db.query_one("SELECT COUNT(*) AS n FROM outbox")["n"])
    for kind, detail, _ in dlq[:10]:
        print("DLQ:", kind, detail)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "data/events.jsonl")
