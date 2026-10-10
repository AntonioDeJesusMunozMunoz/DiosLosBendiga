"""
kafka_consumer.py - Dev 2: the Kafka side. One consumer thread living in the FastAPI lifespan.

Startup:
  1. wait (with timeout) for shop.catalog to exist
  2. read shop.catalog from offset 0 up to the end offset it had at that moment (rule 22)
  3. assign all four input topics: catalog continues where it stopped, the rest start at 0
Offsets are NEVER committed: every start reads from zero and processed_events turns
already-handled events into cheap silent skips. Deleting the DB therefore rebuilds
everything with no group.id tricks.

Per message: processor.Processor does dedup / handler / park / DLQ. Output events are in
the outbox (written in the handler's transaction); _drain_outbox publishes them to
inventory.events and marks them published, so a crash can never lose an event.

Config (env): KAFKA_BOOTSTRAP (localhost:19092), KAFKA_GROUP_ID (inventory-service),
CATALOG_WAIT_SECONDS (30), PARK_TIMEOUT_SECONDS (60).
"""
import json
import logging
import os
import threading
import time

from confluent_kafka import (OFFSET_BEGINNING, Consumer, KafkaError, KafkaException,
                             Producer, TopicPartition)

from db import query, tx
from processor import Processor

log = logging.getLogger("kafka_consumer")

BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:19092")
GROUP_ID = os.environ.get("KAFKA_GROUP_ID", "inventory-service")  # required by the client, value irrelevant
CATALOG = "shop.catalog"
OTHERS = ["shop.work_orders", "shop.inspections", "shop.purchasing"]
EVENTS_TOPIC = "inventory.events"
DLQ_TOPIC = "inventory.dlq"
CATALOG_WAIT = float(os.environ.get("CATALOG_WAIT_SECONDS", "30"))
CATALOG_IDLE_DONE = 3.0   # no catalog message for this long -> consider it fully read
RETRY_EVERY = 1.0         # seconds between retries of parked events
REFRESH_EVERY = 5.0       # seconds between checks for topics that did not exist yet


class ConsumerService:
    def __init__(self):
        self._stop = threading.Event()
        self._thread = None
        self._consumer = None
        self._producer = None
        self._next = {}               # (topic, partition) -> next offset to read
        self._assigned = set()        # topics already assigned
        self._outbox_upto = 0         # every outbox seq <= this is published
        self.catalog_ready = False
        self.processor = Processor(self._send_dlq)

    # ------------------------------------------------------------ lifecycle
    def start(self):
        self._thread = threading.Thread(target=self._supervise, name="kafka-consumer", daemon=True)
        self._thread.start()

    def stop(self, timeout=15):
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    def _supervise(self):
        """If the loop dies for any reason, log it and start again (resumes from _next)."""
        while not self._stop.is_set():
            try:
                self._run_once()
            except Exception:
                log.exception("consumer loop crashed, restarting in 3s")
                self._stop.wait(3)

    # ------------------------------------------------------------- main loop
    def _run_once(self):
        self._consumer = Consumer({
            "bootstrap.servers": BOOTSTRAP,
            "group.id": GROUP_ID,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        })
        self._producer = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all"})
        try:
            self._drain_outbox()                       # leftovers from a previous crash
            if not self.catalog_ready:
                self._load_catalog()
                self.catalog_ready = True
            self._assign_all()
            last_retry = last_refresh = time.monotonic()
            while not self._stop.is_set():
                msg = self._consumer.poll(0.5)
                if msg is not None:
                    self._handle_message(msg)
                now = time.monotonic()
                if self.processor.retry_requested or now - last_retry >= RETRY_EVERY:
                    last_retry = now
                    if self.processor.has_pending():
                        self.processor.retry_pending()
                        self._drain_outbox()
                    else:
                        self.processor.retry_requested = False
                if now - last_refresh >= REFRESH_EVERY:
                    last_refresh = now
                    self._refresh_assignment()
                if msg is None:
                    self._drain_outbox()
        finally:
            try:
                self._consumer.close()
            except Exception:
                pass
            try:
                self._producer.flush(5)
            except Exception:
                pass

    def _handle_message(self, msg):
        if msg.error():
            code = msg.error().code()
            if code not in (KafkaError._PARTITION_EOF, KafkaError.UNKNOWN_TOPIC_OR_PART):
                log.error("kafka error: %s", msg.error())
            return
        outcome = self.processor.process(msg.topic(), msg.value(), msg.key())
        self._next[(msg.topic(), msg.partition())] = msg.offset() + 1
        if outcome in ("ok", "dlq"):
            self._drain_outbox()

    # -------------------------------------------------------- catalog gate
    def _partitions(self, topic):
        """Sorted partition ids, or None if the topic does not exist yet."""
        try:
            md = self._consumer.list_topics(topic, timeout=5)
        except KafkaException:
            return None
        t = md.topics.get(topic)
        if t is None or t.error is not None or not t.partitions:
            return None
        return sorted(t.partitions)

    def _load_catalog(self):
        deadline = time.monotonic() + CATALOG_WAIT
        parts = None
        while not self._stop.is_set():
            parts = self._partitions(CATALOG)
            if parts:
                break
            if time.monotonic() > deadline:
                log.warning("%s not found after %.0fs: continuing without the catalog gate "
                            "(it will be read as soon as it appears)", CATALOG, CATALOG_WAIT)
                return
            self._stop.wait(1)
        if not parts:
            return

        ends, tps = {}, []
        for p in parts:
            lo, hi = self._consumer.get_watermark_offsets(TopicPartition(CATALOG, p), timeout=10)
            ends[p] = hi
            self._next[(CATALOG, p)] = lo
            tps.append(TopicPartition(CATALOG, p, OFFSET_BEGINNING))
        self._consumer.assign(tps)

        last_msg = time.monotonic()
        while not self._stop.is_set():
            if all(self._next[(CATALOG, p)] >= ends[p] for p in parts):
                break
            msg = self._consumer.poll(0.5)
            if msg is None:
                if time.monotonic() - last_msg > CATALOG_IDLE_DONE:
                    log.warning("catalog idle for %.0fs before reaching its end offset; going on",
                                CATALOG_IDLE_DONE)
                    break
                continue
            last_msg = time.monotonic()
            self._handle_message(msg)
        log.info("catalog loaded, subscribing to the other topics")

    # ------------------------------------------------------------ assignment
    def _assign_all(self):
        tps = []
        self._assigned = set()
        for topic in [CATALOG] + OTHERS:
            parts = self._partitions(topic)
            if not parts:
                continue
            self._assigned.add(topic)
            for p in parts:
                tps.append(TopicPartition(topic, p, self._next.get((topic, p), OFFSET_BEGINNING)))
        self._consumer.assign(tps)

    def _refresh_assignment(self):
        """Topics created later by the simulator join from the beginning."""
        missing = [t for t in [CATALOG] + OTHERS if t not in self._assigned]
        if missing and any(self._partitions(t) for t in missing):
            self._assign_all()

    # ---------------------------------------------------------------- output
    def _send_dlq(self, key, raw, kind, detail):
        """The DLQ message is the ORIGINAL event with the original key; error info in headers."""
        result = []
        try:
            self._producer.produce(
                DLQ_TOPIC, key=key, value=raw,
                headers=[("error_kind", kind.encode()), ("error_detail", detail[:500].encode())],
                on_delivery=lambda err, m: result.append(err is None))
            self._producer.flush(10)
        except Exception:
            log.exception("DLQ publish failed")
            return False
        return bool(result) and result[0]

    def _drain_outbox(self):
        """Publish unpublished outbox rows to inventory.events, then mark them published."""
        while True:
            rows = query("SELECT seq, event_id, event_type, key, occurred_at, data FROM outbox "
                         "WHERE seq > ? AND published = 0 ORDER BY seq LIMIT 500", (self._outbox_upto,))
            if not rows:
                return
            ok = set()
            for r in rows:
                envelope = {
                    "event_id": r["event_id"], "event_type": r["event_type"], "event_version": 1,
                    "occurred_at": r["occurred_at"], "source": "inventory", "key": r["key"],
                    "data": json.loads(r["data"]),
                }
                self._producer.produce(
                    EVENTS_TOPIC, key=r["key"], value=json.dumps(envelope),
                    on_delivery=lambda err, m, s=r["seq"]: ok.add(s) if err is None else log.error(
                        "publish failed for outbox seq %s: %s", s, err))
            self._producer.flush(10)
            if ok:
                with tx() as c:
                    c.executemany("UPDATE outbox SET published=1 WHERE seq=?", [(s,) for s in sorted(ok)])
            first_fail = next((r["seq"] for r in rows if r["seq"] not in ok), None)
            if first_fail is not None:
                self._outbox_upto = first_fail - 1   # retry from the first failure next time
                return
            self._outbox_upto = rows[-1]["seq"]
            if len(rows) < 500:
                return


service = ConsumerService()


def start():
    service.start()


def stop():
    service.stop()
