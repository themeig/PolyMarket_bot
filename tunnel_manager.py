import subprocess
import re
import sys
import os
import time
import httpx

BOT_TOKEN = "8622241080:AAGZO-lRfXPtjsFj3GsuSNyicSz3pLT_zBQ"
CHAT_ID = "8184115780"
URL_FILE = os.path.join(os.path.dirname(__file__), "cloudflared_url.txt")
EXE = os.path.join(os.path.dirname(__file__), "cloudflared.exe")

def update_telegram_menu_button(url: str):
    miniapp_url = f"{url}/miniapp"
    try:
        r = httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/setChatMenuButton",
            json={
                "chat_id": CHAT_ID,
                "menu_button": {
                    "type": "web_app",
                    "text": "📊 Dashboard",
                    "web_app": {"url": miniapp_url}
                }
            },
            timeout=10.0
        )
        print(f"[Tunnel] setChatMenuButton status: {r.status_code}, resp: {r.text}")
    except Exception as e:
        print(f"[Tunnel] Errore setChatMenuButton: {e}")

def run():
    while True:
        print("[Tunnel] Avvio cloudflared tunnel verso http://localhost:8080...")
        proc = subprocess.Popen(
            [EXE, "tunnel", "--url", "http://localhost:8080"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        tunnel_url = None
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            if not tunnel_url:
                m = re.search(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com", line)
                if m:
                    tunnel_url = m.group(0)
                    print(f"\n==========================================")
                    print(f"[Tunnel] NUOVO URL CLOUDFLARE TROVATO:")
                    print(f"  {tunnel_url}")
                    print(f"==========================================\n")
                    with open(URL_FILE, "w") as f:
                        f.write(tunnel_url)
                    update_telegram_menu_button(tunnel_url)

        proc.wait()
        print("[Tunnel] cloudflared si è interrotto. Riavvio tra 5 secondi...")
        time.sleep(5)

if __name__ == "__main__":
    run()
