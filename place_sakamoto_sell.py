import os
import sys
import requests
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgs, OrderType
from py_clob_client_v2.order_builder.constants import SELL

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

token_id = "99721182329744494555632841441611271032828473278477610243523849964590225809396"
book = requests.get(f"https://clob.polymarket.com/book?token_id={token_id}").json()
asks = sorted(book.get("asks", []), key=lambda x: float(x["price"]))
top_ask = float(asks[0]["price"]) if asks else 0.350
sell_target = max(0.295, round(0.25 * 1.18, 3)) # +18% margine (0.295$)

print(f"Piazzamento ordine di VENDITA (ASK) per 6 quote di Sakamoto a {sell_target:.3f} $ (Target Margine)...")
args = OrderArgs(price=sell_target, size=6.0, side=SELL, token_id=token_id)
res = client.post_order(client.create_order(args), OrderType.GTC)
print("Risposta piazzamento vendita:", res)
