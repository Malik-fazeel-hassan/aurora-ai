FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    HOST=0.0.0.0 \
    PYTHONPATH=/app/backend

WORKDIR /app

# System deps for some MCP/node optional tools (node for npx MCPs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git \
    && rm -rf /var/lib/apt/lists/*

# Optional Node for MCP npx servers (kept slim; comment out if not needed)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt \
    && pip install mcp-server-time mcp-server-git mcp-server-fetch mcp-server-sqlite || true

COPY backend ./backend
COPY static ./static
COPY start.sh ./start.sh
COPY data/settings.example.json ./data/settings.example.json
COPY data/mcp_servers.example.json ./data/mcp_servers.example.json
COPY workspace/README.md ./workspace/README.md

RUN chmod +x /app/start.sh \
    && mkdir -p /app/data /app/workspace \
    && useradd -m -u 10001 aurora \
    && chown -R aurora:aurora /app

USER aurora

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

CMD ["./start.sh"]
