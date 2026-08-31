import sys
import asyncio
import aiohttp
import json
from tabulate import tabulate

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"

async def test_logical_spreads():
    params = {
        "closed": "false",
        "limit": 50,
        "order": "volume24hr",
        "ascending": "false"
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(GAMMA_EVENTS_URL, params=params) as resp:
            events = await resp.json()
            
    print("=" * 100)
    print(f"🔍 SCANSIONE ARBITRAGGI E SPREAD LOGICI SU {len(events)} EVENTI MULTI-OUTCOME:")
    print("=" * 100)

    opportunities = []
    for ev in events:
        title = ev.get("title", "")
        markets = ev.get("markets", [])
        if len(markets) < 2:
            continue

        # Calcoliamo la somma dei Best Ask per comprare tutti i YES (Paniere Completo)
        total_yes_ask = 0.0
        total_yes_bid = 0.0
        outcomes_data = []
        has_valid_prices = True

        for m in markets:
            q = m.get("question") or m.get("groupItemTitle") or "N/D"
            best_ask = m.get("bestAsk")
            best_bid = m.get("bestBid")
            if best_ask is None or best_ask <= 0:
                has_valid_prices = False
                break
            total_yes_ask += float(best_ask)
            if best_bid:
                total_yes_bid += float(best_bid)
            outcomes_data.append({
                "outcome": q,
                "ask": float(best_ask),
                "bid": float(best_bid) if best_bid else 0.0,
                "token_id": json.loads(m.get("clobTokenIds", "[]"))[0] if m.get("clobTokenIds") else ""
            })

        if has_valid_prices and len(outcomes_data) >= 2:
            # Se la somma di tutti gli Ask < 1.00$, c'è un arbitraggio matematico puro (compri tutto a meno di 1$ e incassi 1$ garantito!)
            # Se la somma di tutti i Bid > 1.00$, puoi vendere tutti i YES a più di 1$!
            # Oppure calcoliamo la deviazione logica
            dev_pct = round((1.00 - total_yes_ask) * 100.0, 2)
            
            opportunities.append({
                "event_title": title,
                "outcomes_count": len(outcomes_data),
                "sum_best_ask": round(total_yes_ask, 3),
                "sum_best_bid": round(total_yes_bid, 3),
                "arbitrage_profit_pct": dev_pct,
                "type": "ARBITRAGGIO LOGICO PURO (Sotto 1$)" if total_yes_ask < 1.00 else ("INEFFICIENZA LOGICA" if total_yes_ask > 1.05 else "EQUILIBRIO LOGICO"),
                "outcomes": outcomes_data
            })

    # Ordiniamo per migliore opportunità logica
    opportunities = sorted(opportunities, key=lambda x: x["arbitrage_profit_pct"], reverse=True)

    summary_table = []
    for op in opportunities[:10]:
        summary_table.append({
            "Evento": op["event_title"][:45],
            "Opzioni": op["outcomes_count"],
            "Somma ASK": f"{op['sum_best_ask']:.3f} $",
            "Somma BID": f"{op['sum_best_bid']:.3f} $",
            "Deviazione Logica": f"{op['arbitrage_profit_pct']:+.1f} %",
            "Tipo": op["type"]
        })
    print(tabulate(summary_table, headers="keys", tablefmt="fancy_grid"))

asyncio.run(test_logical_spreads())
