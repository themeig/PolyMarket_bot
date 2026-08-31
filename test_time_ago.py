import time
from datetime import datetime

def format_time_ago(ts):
    if not ts:
        return "N/D"
    try:
        now = time.time()
        diff = now - float(ts)
        if diff < 0:
            return "Adesso"
        if diff < 60:
            return f"{int(diff)}s fa"
        elif diff < 3600:
            mins = int(diff // 60)
            return f"{mins}m fa"
        elif diff < 86400:
            hours = int(diff // 3600)
            return f"{hours}h fa"
        else:
            days = int(diff // 86400)
            return f"{days}g fa"
    except Exception:
        return "N/D"

# Test
print("60s ago:", format_time_ago(time.time() - 60))
print("300s ago:", format_time_ago(time.time() - 300))
print("7200s ago:", format_time_ago(time.time() - 7200))
