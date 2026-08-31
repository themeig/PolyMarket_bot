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

token_id = "108718093338712731663126960153345881101729018265142989089542286947985469094294"
book = requests.get(f"https://clob.polymarket.com/book?token_id={token_id}").json()
bids = sorted(book.get("bids", []), key=lambda x: float(x["price"]), reverse=True)
best_bid = float(bids[0]["price"]) if bids else 0.70

print(f"Miglior BID attuale per Munar: {best_bid:.3f} $")
print(f"Vendita di 5.0 quote a {best_bid:.3f} $...")

args = OrderArgs(price=best_bid, size=5.0, side=SELL, token_id=token_id)
res = client.post_order(client.create_order(args), OrderType.GTC)
print("Risposta vendita:", res)
