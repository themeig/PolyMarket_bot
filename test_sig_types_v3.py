import os
import sys
from dotenv import load_dotenv
from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import OrderArgs, OrderType
from py_clob_client_v2.order_builder.constants import BUY

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
private_key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")
if not private_key.startswith("0x") and len(private_key) == 64:
    private_key = "0x" + private_key

proxy_wallet = "0x117da19a541ba6d89ae52043a67dbc22572d1de8"

for sig_type in [3, 2, 1, 0]:
    print(f"\n=======================================================")
    print(f"Test con signature_type={sig_type} e funder={proxy_wallet}")
    print(f"=======================================================")
    try:
        client = ClobClient(
            host="https://clob.polymarket.com", 
            key=private_key, 
            chain_id=137,
            signature_type=sig_type,
            funder=proxy_wallet
        )
        api_creds = client.create_or_derive_api_key()
        client.set_api_creds(api_creds)
        print("API Creds:", api_creds)
        
        token_id = "101513571766435454355723114188696307527864518980689079866102837918467349506537"
        order_args = OrderArgs(price=0.11, size=15.0, side=BUY, token_id=token_id)
        signed_order = client.create_order(order_args)
        resp = client.post_order(signed_order, OrderType.GTC)
        print("\n" + "🎉" * 20)
        print(f"SUCCESSO ASSOLUTO CON SIGNATURE TYPE {sig_type}!")
        print("RISPOSTA POLYMARKET:", resp)
        print("🎉" * 20 + "\n")
        break
    except Exception as e:
        print(f"Risultato con sig_type={sig_type}:", e)
