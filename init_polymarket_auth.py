"""
=============================================================================
POLYMARKET STEP 5: INITIALIZE OFFICIAL CLOB CLIENT & API CREDENTIALS
=============================================================================
Deriva in modo sicuro le credenziali API (API Key, Secret, Passphrase)
tramite la firma EIP-712 del tuo wallet e verifica l'accesso a Polymarket CLOB.
"""

import os
import sys
from dotenv import load_dotenv
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

def init_auth():
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    private_key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")

    if not private_key.startswith("0x") and len(private_key) == 64:
        private_key = "0x" + private_key

    host = "https://clob.polymarket.com"
    chain_id = 137

    print("=" * 75)
    print("🔐 INIZIALIZZAZIONE CLIENT UFFICIALE POLYMARKET...")
    print("=" * 75)

    try:
        # Inizializza client
        client = ClobClient(host=host, key=private_key, chain_id=chain_id)
        
        # Deriva o crea credenziali API via firma crittografica EIP-712
        print("[*] Derivazione credenziali API crittografiche...")
        api_creds = client.create_or_derive_api_creds()
        client.set_api_creds(api_creds)

        print("[+] Credenziali API derivate con successo!")
        print(f"    - API Key: {api_creds.api_key[:12]}...")
        
        # Verifica connessione con una chiamata authenticated
        orders = client.get_orders()
        print(f"[+] Connessione al CLOB verificata! Ordini aperti attualmente sul conto: {len(orders)}")
        print("=" * 75)
        print("🚀 IL TUO ACCOUNT È PRONTO PER OPERARE SU POLYMARKET CON SOLDI REALI!")
        print("=" * 75)
        return True

    except Exception as e:
        print(f"[!] Errore durante l'autenticazione: {e}")
        return False

if __name__ == "__main__":
    init_auth()
