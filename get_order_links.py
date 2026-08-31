import os
import requests
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

print("=" * 70)
print(f"VERIFICA LINK PROFILO E ORDINI POLYMARKET:")
print(f"Link Profilo Proxy: https://polymarket.com/profile/0x117da19a541ba6d89ae52043a67dbc22572d1de8")
print(f"Link Profilo EOA:   https://polymarket.com/profile/0xb41EfF43D6cB0952313fE4d181453676f68a7CAf")
print("=" * 70)

for idx, o in enumerate(orders, 1):
    asset_id = o.get("asset_id")
    # Trova mercato su Gamma
    res = requests.get(f"https://gamma-api.polymarket.com/markets?clob_token_ids={asset_id}").json()
    m_title = res[0].get("question") if res and isinstance(res, list) and len(res) > 0 else "N/A"
    slug = res[0].get("slug") if res and isinstance(res, list) and len(res) > 0 else ""
    event_slug = res[0].get("events", [{}])[0].get("slug", "") if res and isinstance(res, list) and len(res) > 0 and res[0].get("events") else ""
    print(f"\n[{idx}] Mercato: {m_title}")
    print(f"    - Prezzo: {o.get('price')} $ | Size: {o.get('original_size')} quote")
    if event_slug:
        print(f"    - Link Diretto Mercato: https://polymarket.com/event/{event_slug}")
    elif slug:
        print(f"    - Link Diretto Mercato: https://polymarket.com/market/{slug}")

print("=" * 70)
