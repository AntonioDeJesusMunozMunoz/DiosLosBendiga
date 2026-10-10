import json
from confluent_kafka import Consumer

kafkaServer = "localhost:19092"

consumer = Consumer({
    "bootstrap.servers": kafkaServer,
    "group.id": "inventory-service",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
})
consumer.subscribe(["work_order.opened", "work_order.stage_changed"])

while True:
    msg = consumer.poll(1.0)              # wait up to 1s for a message
    if msg is None:
        continue
    if msg.error():
        print(msg.error())
        continue

    event = json.loads(msg.value())  #leer : print(msg.topic(), msg.partition(), msg.offset(), event)

    # 1. open DB transaction


    # 4. commit DB transaction
    consumer.commit(msg)                  # only now tell Redpanda you finished it







# 2. insert event_id into processed_events (skip if duplicate)
# 3. apply reservation / ledger changes