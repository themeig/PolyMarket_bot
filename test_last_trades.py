import requests
import json
from datetime import datetime

# Token ID OpenAI
token_id = "108052633825118494550832240247980096965299835115818656939682823516952479310001"

endpoints = [
    f"https://data-api.polymarket.com/trades?asset_id={token_id}&limit=5",
    f"https://clob.polymarket.com/trades?token_id={token_id}&limit=5",
    f"https://gamma-api.polymarket.com/markets?clob_token_ids={token_id}"
]

for url in endpoints:
    try:
        r = requests.get(url, timeout=5)
        print(f"\n--- URL: {url} ---")
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list) and len(data) > 0:
                print(f"Trovati {len(data)} record. Primo record:")
                print(json.dumps(data[0], indent=2))
            elif isinstance(data, dict):
                print(json.dumps(data, indent=2)[:300])
    except Exception as e:
        print(f"Errore su {url}: {e}")
