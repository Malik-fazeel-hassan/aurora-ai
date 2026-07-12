# Deploy Aurora Live

Aurora is a single Docker/Python app: FastAPI backend + static frontend.

## 1) Push to GitHub

```bash
cd aurora
git init
git add .
git commit -m "Aurora Live v2.5 — multi-model AI with agents, MCP, mobile UI"
git branch -M main
git remote add origin https://github.com/<you>/aurora-ai.git
git push -u origin main
```

> Never commit `data/settings.json` (API keys). Use env vars.

## 2) One-click hosts

### Railway
1. New project → Deploy from GitHub
2. Root = this repo (Dockerfile auto-detected)
3. Set env: `AURORA_API_KEY=sk-or-...` (OpenRouter recommended)
4. Generate domain → open HTTPS URL on phone/PC

### Render
1. New → Web Service → connect repo
2. Runtime: Docker
3. Health check: `/api/health`
4. Env: `AURORA_API_KEY`

### Fly.io
```bash
fly launch --dockerfile Dockerfile
fly secrets set AURORA_API_KEY=sk-or-...
fly deploy
```

### Docker anywhere (VPS)
```bash
cp .env.example .env   # add AURORA_API_KEY
docker compose up -d --build
# http://YOUR_IP:7860
```

## 3) Mobile / desktop

- Responsive Gemini-style UI (phone, tablet, laptop)
- Installable PWA (Add to Home Screen)
- Touch-friendly controls + safe-area padding

## 4) First-run checklist

1. Open site → Settings
2. Confirm API key (or set `AURORA_API_KEY` env)
3. Model: **⚡ Auto**
4. Optional: Install free MCP catalog
5. Try Agent / Debate / Orch modes

## Security

- Put HTTPS in front (Railway/Render do this)
- Rotate any keys that were ever pasted in chat
- Keep `data/` volume private
