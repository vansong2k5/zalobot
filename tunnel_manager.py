import re
import subprocess
import time
import httpx

ZALO_BOT_TOKEN = "3671575949239713075:GmyzxMeqxNNGDPvdKdWPKcbaVzYkrETItqRVsqdAIribgFUOJQzRPlPdLxtwvufc"

def sync_webhook(tunnel_url: str):
    webhook_url = f"{tunnel_url.rstrip('/')}/webhooks/zalo"
    print(f"[TunnelManager] Dong bo Webhook Zalo: {webhook_url}")
    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"https://bot-api.zaloplatforms.com/bot{ZALO_BOT_TOKEN}/setWebhook",
                json={"url": webhook_url, "secret_token": "songadmin2026"}
            )
            print(f"[TunnelManager] setWebhook result: {resp.status_code} {resp.text}")
    except Exception as e:
        print(f"[TunnelManager] Loi dong bo webhook: {e}")

def run():
    cmd = ["/usr/local/bin/cloudflared", "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8000"]
    while True:
        print("[TunnelManager] Dang khoi chay cloudflared tunnel...")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
        current_url = None
        for line in proc.stdout:
            print(line, end="")
            match = re.search(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com", line)
            if match:
                found_url = match.group(0)
                if found_url != current_url:
                    current_url = found_url
                    sync_webhook(current_url)
        proc.wait()
        print(f"[TunnelManager] cloudflared dung (code {proc.returncode}). Khoi dong lai sau 3s...")
        time.sleep(3)

if __name__ == "__main__":
    run()
