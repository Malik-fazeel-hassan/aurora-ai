#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

export PYTHONUNBUFFERED=1
export PYTHONPATH="$(pwd)/backend:${PYTHONPATH:-}"
export HOST="${HOST:-0.0.0.0}"
export PORT="${PORT:-7860}"

mkdir -p data workspace

# Bootstrap settings from example if missing
if [[ ! -f data/settings.json && -f data/settings.example.json ]]; then
  cp data/settings.example.json data/settings.json
fi
if [[ ! -f data/mcp_servers.json && -f data/mcp_servers.example.json ]]; then
  cp data/mcp_servers.example.json data/mcp_servers.json
fi

# Inject env key into settings if provided and settings key empty
python3 - <<'PY' || true
import json, os
from pathlib import Path
p = Path("data/settings.json")
if not p.exists():
    raise SystemExit(0)
try:
    s = json.loads(p.read_text())
except Exception:
    raise SystemExit(0)
key = os.environ.get("AURORA_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
if key and not (s.get("api_key") or "").strip():
    s["api_key"] = key
    profiles = s.get("profiles") or {}
    if "openrouter" in profiles and not profiles["openrouter"].get("api_key"):
        profiles["openrouter"]["api_key"] = key
        s["profiles"] = profiles
    p.write_text(json.dumps(s, indent=2))
    print("Bootstrapped API key from environment")
PY

exec python3 -m uvicorn backend.main:app --host "$HOST" --port "$PORT" --proxy-headers --forwarded-allow-ips='*'
