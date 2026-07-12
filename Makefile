.PHONY: run dev sec-status audit lint docker smoke

run:
	./start.sh

dev:
	PYTHONPATH=backend uvicorn backend.main:app --reload --host 0.0.0.0 --port $${PORT:-7860}

sec-status:
	curl -s http://127.0.0.1:$${PORT:-7860}/api/security/status | python3 -m json.tool

audit:
	@echo "=== Recent security audit events ==="
	@tail -n 30 data/security_audit.jsonl 2>/dev/null || echo "(no audit log yet)"

lint:
	ruff check backend || true
	bandit -r backend -x backend/mcp_servers -ll || true

docker:
	docker compose build
	docker compose up -d

smoke:
	curl -fsS http://127.0.0.1:$${PORT:-7860}/api/health >/dev/null
	curl -fsS http://127.0.0.1:$${PORT:-7860}/api/security/status >/dev/null
	@echo "smoke ok"
