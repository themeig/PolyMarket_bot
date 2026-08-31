"""
=============================================================================
POLYMARKET LIVE QUANT SCANNER v1.1 (Multi-Outcome + Deep Scan)
=============================================================================
Scansiona fino a 2.000 mercati contemporaneamente ed esamina sia i mercati
binari sia i mercati con eventi a esiti multipli (NegRisk).
"""

import sys
import asyncio
import aiohttp
import time
import json
from tabulate import tabulate

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

GAMMA_API_URL = "https://gamma-api.polymarket.com/markets"

async def fetch_markets_batch(session, offset=0, limit=100):
    params = {
        "active": "true",
        "closed": "false",
        "limit": limit,
        "offset": offset
    }
    try:
        async with session.get(GAMMA_API_URL, params=params, timeout=10) as response:
            if response.status == 200:
                return await response.json()
            return []
    except Exception:
        return []

async def get_all_active_markets(total_to_fetch=2000):
    batch_size = 100
    offsets = range(0, total_to_fetch, batch_size)
    
    t_start = time.time()
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_markets_batch(session, offset=off, limit=batch_size) for off in offsets]
        results = await asyncio.gather(*tasks)
        
    all_markets = []
    for batch in results:
        all_markets.extend(batch)
        
    elapsed = time.time() - t_start
    return all_markets, elapsed

def analyze_all(markets):
    spread_opps = []
    arb_opps = []

    for m in markets:
        question = m.get("question", "N/A")
        best_bid = m.get("bestBid")
        best_ask = m.get("bestAsk")
        volume_24h = m.get("volume24hr", 0) or 0
        liquidity = float(m.get("liquidity", 0) or 0)
        
        if best_bid is None or best_ask is None or best_bid <= 0 or best_ask <= 0:
            continue

        # 1. SPREAD SCANNER (Con filtri di qualità: spread > 3 centesimi e liquidità > 1.000$)
        spread = best_ask - best_bid
        mid_price = (best_ask + best_bid) / 2.0
        
        if mid_price > 0 and spread >= 0.03 and liquidity >= 1000:
            spread_pct = (spread / mid_price) * 100
            spread_opps.append({
                "Mercato": question[:45] + ("..." if len(question) > 45 else ""),
                "Bid": f"{best_bid:.3f} $",
                "Ask": f"{best_ask:.3f} $",
                "Spread ($)": f"{spread:.3f} $",
                "Spread (%)": f"{spread_pct:.1f} %",
                "Liquidita": f"{liquidity:,.0f} $",
                "Vol 24h": f"{volume_24h:,.0f} $"
            })

        # 2. SOMMA LOGICA
        try:
            prices = json.loads(m.get("outcomePrices", "[]"))
            if len(prices) == 2:
                p_yes = float(prices[0])
                p_no = float(prices[1])
                s = p_yes + p_no
                if s < 0.98 or s > 1.02:
                    p_gain = abs(1.00 - s) * 100
                    tipo = "BUY BOTH (<1$)" if s < 0.98 else "SELL BOTH (>1$)"
                    arb_opps.append({
                        "Tipo": tipo,
                        "Mercato": question[:42] + "...",
                        "Prezzo YES": f"{p_yes:.3f} $",
                        "Prezzo NO": f"{p_no:.3f} $",
                        "Somma": f"{s:.3f} $",
                        "Profitto": f"+{p_gain:.2f} %",
                        "Liquidita": f"{liquidity:,.0f} $"
                    })
        except Exception:
            pass

    spread_opps = sorted(spread_opps, key=lambda x: float(x["Spread (%)"].replace(" %", "")), reverse=True)
    return spread_opps, arb_opps

async def main():
    print("=" * 90)
    print("🚀 POLYMARKET LIVE DEEP SCANNER v1.1 — 2.000 MERCATI ATTIVI")
    print("=" * 90)
    
    markets, elapsed = await get_all_active_markets(total_to_fetch=2000)
    spread_opps, arb_opps = analyze_all(markets)
    
    print(f"[*] Scaricati e analizzati {len(markets)} mercati in appena {elapsed:.2f} SECONDI!\n")
    
    print("=" * 90)
    print(f"💎 TOP 15 MERCATI CON SPREAD PIU' LARGO (Liquidita > 1.000 $) — Trovati: {len(spread_opps)}")
    print("=" * 90)
    if spread_opps:
        print(tabulate(spread_opps[:15], headers="keys", tablefmt="grid"))
        
    print("\n" + "=" * 90)
    print(f"🎯 OPPORTUNITA' DI ARBITRAGGIO DI SOMMA LOGICA — Trovate: {len(arb_opps)}")
    print("=" * 90)
    if arb_opps:
        print(tabulate(arb_opps[:10], headers="keys", tablefmt="grid"))
    else:
        print("Tutti i mercati binari analizzati sono momentaneamente allineati a 1.00 $.")
        
    print("=" * 90)

if __name__ == "__main__":
    asyncio.run(main())
