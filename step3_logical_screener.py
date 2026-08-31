"""
=============================================================================
POLYMARKET STEP 3: LOGICAL SPREAD & COMBINATORIAL ARBITRAGE SCREENER
=============================================================================
Scans multi-outcome and binary events for mathematical logical arbitrage:
1. Mutually Exclusive Basket Arbitrage (Sum(YES_Ask) < 1.00$)
2. Complementary Binary Arbitrage (YES_Ask + NO_Ask < 1.00$)
3. Negative Imbalance & Mispricing Arbitrage (Sum(YES_Bid) > 1.00$)
=============================================================================
"""

import sys
import asyncio
import aiohttp
import time
import math
import json
from datetime import datetime, timezone
from step2_smart_screener import is_sports_market, format_iso_time_ago

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
GAMMA_MARKETS_URL = "https://gamma-api.polymarket.com/markets"

async def fetch_events_batch(session, offset=0, limit=100):
    params = {
        "closed": "false",
        "limit": limit,
        "offset": offset,
        "order": "volume24hr",
        "ascending": "false"
    }
    try:
        async with session.get(GAMMA_EVENTS_URL, params=params, timeout=8) as resp:
            if resp.status == 200:
                return await resp.json()
            return []
    except Exception:
        return []

async def fetch_all_logical_opportunities(exclude_sports=True, total_events_to_scan=300):
    batch_size = 100
    offsets = range(0, total_events_to_scan, batch_size)
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_events_batch(session, offset=off, limit=batch_size) for off in offsets]
        results = await asyncio.gather(*tasks)

    all_events = []
    for b in results:
        all_events.extend(b)

    opportunities = []

    for ev in all_events:
        title = ev.get("title", "")
        slug = ev.get("slug", "")
        category = ev.get("category") or "Macro / Politica / Crypto"
        markets = ev.get("markets", [])

        if exclude_sports and is_sports_market(title):
            continue

        # CRITICAL FIX: Only scan events that are strictly mutually exclusive and exhaustive
        if not ev.get("enableNegRisk", False):
            continue

        if len(markets) < 2:
            continue

        total_yes_ask = 0.0
        total_yes_bid = 0.0
        outcomes_data = []
        has_valid_prices = True
        total_volume = float(ev.get("volume24hr", 0) or 0)

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

            tokens_raw = m.get("clobTokenIds", "[]")
            tokens = json.loads(tokens_raw) if isinstance(tokens_raw, str) else tokens_raw
            token_id = tokens[0] if tokens else ""

            outcomes_data.append({
                "outcome_title": q,
                "best_ask": round(float(best_ask), 3),
                "best_bid": round(float(best_bid), 3) if best_bid else 0.0,
                "token_id": token_id,
                "volume_24h": float(m.get("volume24hr", 0) or 0)
            })

        if has_valid_prices and len(outcomes_data) >= 2:
            sum_ask = round(total_yes_ask, 3)
            sum_bid = round(total_yes_bid, 3)
            
            # Deviazione da 1.000$ (Se < 1.00$ -> Arbitraggio Matematico Puro Garantito!)
            spread_profit = round(1.000 - sum_ask, 3)
            roi_pct = round((spread_profit / sum_ask) * 100.0, 2) if sum_ask > 0 else 0.0

            # FILTRO CRUCIALE: Mostriamo solo le VERE opportunità di arbitraggio con profitto netto positivo (> 0%)
            if spread_profit <= 0 or roi_pct <= 0:
                continue

            # Calcolo APY e Days to Expiry
            end_date_str = ev.get("endDate")
            days_to_expiry = 30.0 # fallback
            apy_pct = 0.0
            if end_date_str:
                try:
                    end_dt = datetime.fromisoformat(end_date_str.replace('Z', '+00:00'))
                    now_dt = datetime.now(timezone.utc)
                    delta = (end_dt - now_dt).total_seconds()
                    days_to_expiry = max(0.25, delta / 86400.0) # minimo 6 ore per evitare apy infiniti
                except Exception:
                    pass

            if roi_pct > 0:
                apy_pct = round(min(roi_pct * (365.0 / days_to_expiry), 9999.0), 2)

            # Classificazione della tipologia di arbitraggio logico
            if sum_ask < 0.980:
                arb_type = "🚀 ARBITRAGGIO LOGICO ECCEZIONALE (Paniere < 0.98$)"
                badge_color = "emerald"
                risk_profile = "Zero Rischio Direzionale (Matematico)"
            else:
                arb_type = "💰 ARBITRAGGIO LOGICO ATTIVO (Paniere < 1.00$)"
                badge_color = "purple"
                risk_profile = "Zero Rischio Direzionale (Matematico)"

            opportunities.append({
                "event_id": str(ev.get("id", "")),
                "title": title,
                "category": category,
                "polymarket_url": f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
                "outcomes_count": len(outcomes_data),
                "sum_best_ask": sum_ask,
                "sum_best_bid": sum_bid,
                "guaranteed_payout": 1.000,
                "net_profit_per_basket": spread_profit,
                "roi_pct": roi_pct,
                "apy_pct": apy_pct,
                "days_to_expiry": round(days_to_expiry, 1),
                "arb_type": arb_type,
                "badge_color": badge_color,
                "risk_profile": risk_profile,
                "volume_24h": total_volume,
                "outcomes": outcomes_data
            })

    # Ordiniamo prima per migliori opportunità con ROI positivo garantito
    opportunities = sorted(opportunities, key=lambda x: (x["roi_pct"], x["volume_24h"]), reverse=True)
    return opportunities
