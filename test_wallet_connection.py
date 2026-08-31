"""
=============================================================================
POLYMARKET STEP 4: WALLET CONNECTION & ON-CHAIN BALANCE CHECK
=============================================================================
Verifica la connessione sicura del tuo Account 2 e legge il saldo reale
di POL e USDC direttamente dalla blockchain di Polygon.
"""

import os
import sys
from dotenv import load_dotenv
from web3 import Web3

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# Indirizzo ufficiale del contratto USDC su rete Polygon
USDC_POLYGON_CONTRACT = "0x3c499c542cEF5E3811e1192ce70d8cC03d5c3359"
USDC_ABI = [
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "type": "function"
    }
]

def check_connection():
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    private_key = os.getenv("PRIVATE_KEY", "").strip().strip('"').strip("'")

    if not private_key or "INCOLLA_QUI" in private_key:
        print("\n" + "=" * 75)
        print("⚠️ CHIAVE NON ANCORA INSERITA!")
        print("Apri il file '.env' sul tuo computer e incolla la chiave privata di Account 2.")
        print("Percorso file: polymarket_bot/.env")
        print("=" * 75 + "\n")
        return False

    # Assicurati che abbia il prefisso 0x
    if not private_key.startswith("0x") and len(private_key) == 64:
        private_key = "0x" + private_key

    rpc_url = os.getenv("RPC_URL", "https://polygon.drpc.org")
    w3 = Web3(Web3.HTTPProvider(rpc_url))

    if not w3.is_connected():
        print("[!] Impossibile connettersi al nodo Polygon. Controllo connessione...")
        return False

    try:
        account = w3.eth.account.from_key(private_key)
        wallet_address = account.address

        # 1. Saldo POL nativo (per il gas)
        pol_balance_wei = w3.eth.get_balance(wallet_address)
        pol_balance = w3.from_wei(pol_balance_wei, 'ether')

        # 2. Saldo USDC (token per il trading)
        usdc_contract = w3.eth.contract(address=w3.to_checksum_address(USDC_POLYGON_CONTRACT), abi=USDC_ABI)
        usdc_raw = usdc_contract.functions.balanceOf(wallet_address).call()
        usdc_decimals = usdc_contract.functions.decimals().call()
        usdc_balance = usdc_raw / (10 ** usdc_decimals)

        print("\n" + "=" * 75)
        print("🎉 CONNESSIONE AL WALLET REALE AVVENUTA CON SUCCESSO!")
        print("=" * 75)
        print(f"📌 Indirizzo Wallet Account 2: {wallet_address}")
        print(f"⛽ Saldo POL (Gas per commissioni): {pol_balance:.4f} POL")
        print(f"💵 Saldo USDC (Capitale di Trading): {usdc_balance:.2f} USDC ($)")
        print("=" * 75)

        if usdc_balance > 0 and pol_balance > 0:
            print("✅ TUTTO PRONTO! Il conto ha sia i fondi per il gas sia il capitale di trading.")
            print("Possiamo procedere con l'avvio del bot reale con micro-trade da 1.50 $!")
            print("=" * 75 + "\n")
            return True
        else:
            print("⚠️ ATTENZIONE: Assicurati di avere almeno 0.5 POL e qualche USDC sul conto.")
            print("=" * 75 + "\n")
            return False

    except Exception as e:
        print(f"[!] Errore di decodifica della chiave privata: {e}")
        return False

if __name__ == "__main__":
    check_connection()
