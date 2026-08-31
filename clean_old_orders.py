import os
import sys
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

load_dotenv("polymarket_bot/.env")
key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")
if not key.startswith("0x") and len(key) == 64:
    key = "0x" + key

client = ClobClient(
    host="https://clob.polymarket.com",
    key=key,
    chain_id=137,
    signature_type=3,
    funder="0x117da19a541ba6d89ae52043a67dbc22572d1de8"
)
client.set_api_creds(client.create_or_derive_api_key())

open_orders = client.get_open_orders()
hashes = [o.get("id") or o.get("orderID") for o in open_orders if o.get("id") or o.get("orderID")]
print(f"Cancellazione di {len(hashes)} ordini tramite cancel_orders...")

if hashes:
    res = client.cancel_orders(hashes)
    print("Risposta cancellazione:", res)

remaining = client.get_open_orders()
print(f"Ordini aperti rimasti dopo la pulizia: {len(remaining)}")
