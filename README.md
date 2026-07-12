# Aurora AI — Live multi-model assistant

Gemini-style UI · Claude-class routing · Agents · Debate · Orchestration · MCP  
Works on **phones, tablets, laptops, and desktops**.

![Aurora](static/assets/icon-512.png)

## Features

- **Beautiful responsive UI** (dark/light, PWA installable)
- **⚡ Auto model routing** + credit-aware multi-key failover
- **Agent mode** with tools
- **Debate mode** (3 models → merge)
- **Orchestration pipelines** (research / build / analysis / write / quick / auto)
- **MCP connectors** + **50 free open-source MCP presets**
- Artifacts panel, streaming, multi-chat, export

## Quick start (local)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# optional: export AURORA_API_KEY=sk-or-v1-...
./start.sh
```

Open http://127.0.0.1:7860

## Go live (Git + deploy)

See **[DEPLOY.md](./DEPLOY.md)** for Railway / Render / Fly / Docker.

```bash
git init
git add .
git commit -m "Aurora Live"
git remote add origin https://github.com/<you>/aurora-ai.git
git push -u origin main
```

Then connect the repo to Railway or Render and set:

```
AURORA_API_KEY=your_openrouter_or_openai_key
```

## Docker

```bash
docker compose up -d --build
```

## Security notes

- Do **not** commit `data/settings.json` (contains API keys)
- Use environment variables in production
- Rotate keys if they were shared in chat/logs

## Project layout

```
aurora/
  backend/           # FastAPI, router, tools, orchestration, MCP
  static/            # Gemini-style responsive frontend + PWA
  data/*.example.json
  workspace/         # agent/MCP file sandbox
  Dockerfile
  docker-compose.yml
  DEPLOY.md
```

## License

MIT — use and ship freely.
