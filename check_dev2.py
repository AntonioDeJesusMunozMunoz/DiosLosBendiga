"""check_dev2.py - proof that the Dev 2 part works. Run from the folder that has inventory.db:

    python check_dev2.py

Env (optional): INVENTORY_DB, API (http://localhost:8000), SIM (http://localhost:8090)
"""
import json
import os
import sqlite3
import urllib.request

DB = os.environ.get("INVENTORY_DB", "inventory.db")
API = os.environ.get("API", "http://localhost:8000")
SIM = os.environ.get("SIM", "http://localhost:8090")


def get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return json.load(r)
    except Exception as e:
        return {"_error": str(e)}


con = sqlite3.connect(DB)
q = lambda sql: con.execute(sql).fetchall()

print("simulator replay :", get(SIM + "/api/state").get("replay", "simulator not reachable"))
print("processed_events :", q("select count(*) from processed_events")[0][0])
print("parked events    :", q("select count(*) from pending_events")[0][0], "(must end at 0)")
print("outbox           :")
for t, n, unpub in q("select event_type, count(*), sum(published=0) from outbox group by 1"):
    print(f"   {t}: {n} (unpublished: {unpub})")
print("work orders      :", q("select count(*), coalesce(sum(deleted),0) from work_orders")[0], "(total, deleted)")
print("needs            :", q("select status, count(*) from needs group by 1"))
print("shortages        :", q("select status, count(*) from shortages group by 1"))
print("reservations     :", q("select status, count(*) from reservations group by 1"))
print("orders with open shortages (sample):",
      [r[0] for r in q("select distinct work_order_code from shortages "
                       "where status='open' and work_order_code is not null limit 3")])
s = get(API + "/shortages")
items = s.get("items")
print("GET /shortages   :", f"{len(items)} items" if items is not None else s)
if items and any(i.get("name") == "Kit especial sin identificar" for i in items):
    print("WARNING: the FAKE dev2 router is still included in main.py")