"""Wait for the Ollama server and make sure the configured Qwen model is downloaded.

Exit code 0: ready. Exit code 1: no Ollama server answered (the caller falls back to the offline stub).
The first download of qwen2.5:7b-instruct is about 4.7 GB; later starts reuse the stored copy.
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

base = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b-instruct")
wait_s = float(os.getenv("AGRICREDIT_OLLAMA_WAIT_S", "120"))


def models() -> list[str]:
    r = httpx.get(f"{base}/api/tags", timeout=5)
    r.raise_for_status()
    return [m["name"] for m in r.json().get("models", [])]


deadline = time.time() + wait_s
while True:
    try:
        have = models()
        break
    except Exception:
        if time.time() > deadline:
            print(f"[agricredit] no Ollama server answered at {base} after {wait_s:.0f} s", flush=True)
            sys.exit(1)
        time.sleep(2)

wanted = model if ":" in model else f"{model}:latest"
if wanted in have:
    print(f"[agricredit] model {wanted} is ready", flush=True)
    sys.exit(0)

print(f"[agricredit] downloading {wanted} (first start only, about 4.7 GB; this can take a while)", flush=True)
last = -10.0
with httpx.stream("POST", f"{base}/api/pull", json={"model": wanted, "stream": True}, timeout=None) as r:
    r.raise_for_status()
    for line in r.iter_lines():
        if not line:
            continue
        msg = json.loads(line)
        if msg.get("error"):
            print(f"[agricredit] model download failed: {msg['error']}", flush=True)
            sys.exit(1)
        total, done = msg.get("total"), msg.get("completed")
        if total and done:
            pct = 100 * done / total
            if pct - last >= 10:
                print(f"[agricredit]   {pct:.0f}%", flush=True)
                last = pct
print(f"[agricredit] model {wanted} downloaded", flush=True)
