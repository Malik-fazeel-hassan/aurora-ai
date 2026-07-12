# Aurora DevSecOps & Security Protocols

This document describes the security controls built into Aurora Live and how to operate them in production.

## 1. Security principles

1. **Secrets never in git** — `data/settings.json`, `data/mcp_servers.json`, `.env` are gitignored  
2. **Defense in depth** — headers + rate limits + input validation + SSRF guards + optional app token  
3. **Least privilege** — MCP/workspace sandboxes; private URL blocking  
4. **Observable** — request IDs, rate-limit headers, local audit log  
5. **Shift-left CI** — gitleaks, pip-audit, bandit, trivy, smoke tests  

## 2. Runtime controls (enabled by default)

| Control | What it does | Env var |
|--------|----------------|---------|
| Security headers | CSP, XFO DENY, nosniff, Referrer-Policy, Permissions-Policy, COOP/CORP | `AURORA_SECURITY_ENABLED=true` |
| HSTS | Strict-Transport-Security on HTTPS | `AURORA_HSTS=true` |
| Rate limiting | Per-IP limits for chat / API / MCP | `AURORA_RATE_LIMIT=true` |
| Body size cap | Rejects oversized POSTs | `AURORA_MAX_BODY_BYTES` |
| Chat payload guards | Max messages / chars / model id charset | `AURORA_MAX_MESSAGE_CHARS`, `AURORA_MAX_MESSAGES` |
| App token gate | Optional shared secret for `/api/*` | `AURORA_REQUIRE_APP_TOKEN`, `AURORA_APP_TOKEN` |
| CORS lockdown | Explicit origins in prod | `AURORA_CORS_ORIGINS` |
| SSRF guard | Blocks localhost/private IPs in fetch tools | `AURORA_BLOCK_PRIVATE_URLS=true` |
| Audit log | JSONL security events in `data/security_audit.jsonl` | `AURORA_AUDIT_LOG=true` |
| Secret redaction | Redacts keys in audit/log helpers | always on |

### Default rate limits

- Chat: **30 / min / IP** (`AURORA_RL_CHAT_PER_MIN`)  
- General API: **120 / min / IP** (`AURORA_RL_API_PER_MIN`)  
- MCP: **40 / min / IP** (`AURORA_RL_MCP_PER_MIN`)  

Exceeding limits returns **HTTP 429** with `Retry-After`.

### Optional app token

```bash
export AURORA_REQUIRE_APP_TOKEN=true
export AURORA_APP_TOKEN='long-random-string'
```

Clients must send:

```
X-Aurora-Token: long-random-string
# or
Authorization: Bearer long-random-string
```

Public: `/`, `/api/health`, `/api/security/status`, static assets.

### Production CORS

```bash
export AURORA_CORS_ORIGINS=https://your-domain.com,https://www.your-domain.com
```

Do **not** leave `*` on a public multi-tenant deployment with credentials.

## 3. Application hardening

- **Settings keys masked** in UI/API responses  
- **MCP tools** namespaced `mcp__server__tool`; workspace FS is sandboxed  
- **Fetch / web tools** block private/metadata hosts  
- **Model ids** sanitized (charset allow-list)  
- **Proxy headers** supported (`--proxy-headers`) for correct client IP rate limiting  

## 4. CI / DevSecOps pipeline

Workflow: `.github/workflows/devsecops.yml`

1. **Gitleaks** — secret scanning  
2. **pip-audit** — vulnerable dependencies  
3. **Ruff + Bandit** — lint / SAST  
4. **Tracked-secret guard** — fails if `.env` / settings with keys are committed  
5. **Docker build + Trivy** — image CVEs  
6. **Smoke test** — boots app, checks `/api/health` + `/api/security/status`  

## 5. Container / deploy checklist

- [ ] Set `AURORA_API_KEY` via host secrets (not baked into image)  
- [ ] Set `AURORA_CORS_ORIGINS` to your HTTPS origin  
- [ ] Consider `AURORA_REQUIRE_APP_TOKEN=true` if the UI is not the only client  
- [ ] Terminate TLS at reverse proxy / platform  
- [ ] Persist `data/` volume privately (contains keys + audit log)  
- [ ] Rotate keys that ever appeared in chat/logs  
- [ ] Keep image updated; re-run Trivy on deploy  

## 6. Incident response (lightweight)

1. **Revoke** exposed API keys (OpenRouter / NVIDIA / OpenAI)  
2. Inspect `data/security_audit.jsonl` for `auth_failed`, `rate_limited`, `chat_rejected`  
3. Temporarily tighten: lower RL limits, enable app token, restrict CORS  
4. Redeploy from clean git tag  

## 7. What this does *not* replace

- WAF / DDoS at CDN edge  
- Full SSO / per-user OAuth (can be added later)  
- Guaranteed prevention of all prompt-injection against tools  
- Vendor-side rate limits on LLM providers  

## 8. Security contact

Open a private security advisory on the GitHub repo for vulnerabilities.  
Do not file public issues with live secrets.

---

**Status endpoint:** `GET /api/security/status`  
**Health (includes security summary):** `GET /api/health`
