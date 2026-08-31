import os
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient

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
orders = client.get_open_orders()
print("=" * 60)
print(f"ORDINI APERTI SUL TUO CONTO: {len(orders)}")
for o in orders:
    print(f" - ID: {o.get('id') or o.get('orderID')}")
    print(f"   Prezzo: {o.get('price')} $ | Size: {o.get('original_size') or o.get('size')} quote | Side: {o.get('side')}")
    print(f"   Asset/Token ID: {o.get('asset_id')}")
print("=" * 60)
