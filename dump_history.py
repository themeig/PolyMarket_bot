import sys
import requests

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

r = requests.get('http://localhost:8080/api/status')
data = r.json()

print(f"Patrimonio Netto Attuale: {data.get('net_worth')} $")
print(f"Liquidita Libera: {data.get('free_cash')} $")
print(f"In Ordini: {data.get('in_orders')} $")
print(f"Rewards: {data.get('rewards')} $")
print("=" * 75)
print("CRONOLOGIA PUNTO PER PUNTO DEI PALLINI DEL GRAFICO:")
print("=" * 75)

trades = [t for t in data.get('trades', []) if t.get('action') == 'VENDITA']
curr_bal = 1000.0

for i, t in enumerate(trades, 1):
    pnl = t.get('pnl', 0.0)
    curr_bal += pnl
    print(f"[*] Step #{i:02d} [{t.get('time')}]")
    print(f"    Mercato: {t.get('market')}")
    print(f"    Venduto a: {t.get('price'):.3f} $ (Durata: {t.get('duration')})")
    print(f"    Guadagno: +{pnl:.2f} $ ──► Saldo Sale a: {curr_bal:.2f} $")
    print("-" * 75)
