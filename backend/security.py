"""
Aurora DevSecOps security protocols.

Covers:
- Security response headers (CSP, HSTS, XFO, etc.)
- CORS policy from env
- Rate limiting (token bucket per IP + route class)
- Request size / content-type guards
- SSRF & local target blocking helpers
- Secret redaction for logs
- Optional app token gate for mutating APIs
- Audit event logger (local JSONL)
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
import uuid
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
AUDIT_LOG = DATA_DIR / "security_audit.jsonl"

# ---------- Config from env ----------
def env_bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except Exception:
        return default


SECURITY_ENABLED = env_bool("AURORA_SECURITY_ENABLED", True)
RATE_LIMIT_ENABLED = env_bool("AURORA_RATE_LIMIT", True)
RATE_LIMIT_CHAT = env_int("AURORA_RL_CHAT_PER_MIN", 30)
RATE_LIMIT_API = env_int("AURORA_RL_API_PER_MIN", 120)
RATE_LIMIT_MCP = env_int("AURORA_RL_MCP_PER_MIN", 40)
MAX_BODY_BYTES = env_int("AURORA_MAX_BODY_BYTES", 2_000_000)
MAX_MESSAGE_CHARS = env_int("AURORA_MAX_MESSAGE_CHARS", 100_000)
MAX_MESSAGES = env_int("AURORA_MAX_MESSAGES", 80)
REQUIRE_APP_TOKEN = env_bool("AURORA_REQUIRE_APP_TOKEN", False)
APP_TOKEN = (os.environ.get("AURORA_APP_TOKEN") or "").strip()
TRUST_PROXY = env_bool("AURORA_TRUST_PROXY", True)
HSTS_ENABLED = env_bool("AURORA_HSTS", True)
AUDIT_ENABLED = env_bool("AURORA_AUDIT_LOG", True)
BLOCK_PRIVATE_URLS = env_bool("AURORA_BLOCK_PRIVATE_URLS", True)

# Comma-separated origins; empty = same-origin friendly defaults for local + *
_cors = (os.environ.get("AURORA_CORS_ORIGINS") or "").strip()
if _cors:
    CORS_ORIGINS = [o.strip() for o in _cors.split(",") if o.strip()]
else:
    # Dev-friendly; production should set AURORA_CORS_ORIGINS explicitly
    CORS_ORIGINS = ["*"]

# Paths that never require app token
PUBLIC_PATHS = {
    "/",
    "/api/health",
    "/api/security/status",
    "/sw.js",
    "/manifest.webmanifest",
    "/favicon.ico",
}

# Mutating / sensitive path prefixes that can be gated
SENSITIVE_PREFIXES = (
    "/api/chat",
    "/api/settings",
    "/api/mcp/",
    "/api/pipelines",
)


# ---------- Secret redaction ----------
_SECRET_PATTERNS = [
    re.compile(r"(?i)\b(sk-[a-z0-9_\-]{10,})\b"),
    re.compile(r"(?i)\b(sk-or-v1-[a-z0-9]{10,})\b"),
    re.compile(r"(?i)\b(nvapi-[a-z0-9_\-]{10,})\b"),
    re.compile(r"(?i)\b(ghp_[a-z0-9]{20,})\b"),
    re.compile(r"(?i)\b(github_pat_[a-z0-9_]{20,})\b"),
    re.compile(r"(?i)\b(xox[baprs]-[a-z0-9\-]{10,})\b"),
    re.compile(r"(?i)\b(AIza[0-9A-Za-z\-_]{20,})\b"),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)\s*[:=]\s*([\"']?)([^\s\"']{8,})\2"),
    re.compile(r"(?i)bearer\s+([a-z0-9\-_\.]{12,})"),
]


def redact_secrets(text: str) -> str:
    if not text:
        return text
    out = str(text)
    for pat in _SECRET_PATTERNS:
        out = pat.sub(lambda m: (m.group(0)[:6] + "…REDACTED…") if m.lastindex is None else "…REDACTED…", out)
    return out


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) > 12:
        return key[:4] + "…" + key[-4:]
    return "••••••••"


# ---------- SSRF helpers ----------
_PRIVATE_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "::1",
    "metadata.google.internal",
    "metadata",
}


def is_blocked_url(url: str) -> tuple[bool, str]:
    """Return (blocked, reason). Used by fetch tools / MCP URL guards."""
    if not BLOCK_PRIVATE_URLS:
        return False, ""
    try:
        u = urlparse((url or "").strip())
    except Exception:
        return True, "invalid url"
    if u.scheme not in ("http", "https"):
        return True, "only http/https allowed"
    host = (u.hostname or "").lower()
    if not host:
        return True, "missing host"
    if host in _PRIVATE_HOSTS or host.endswith(".local") or host.endswith(".internal"):
        return True, "private/local host blocked"
    # Basic private IP ranges
    if re.match(r"^10\.\d+\.\d+\.\d+$", host):
        return True, "private ip blocked"
    if re.match(r"^192\.168\.\d+\.\d+$", host):
        return True, "private ip blocked"
    if re.match(r"^172\.(1[6-9]|2\d|3[0-1])\.\d+\.\d+$", host):
        return True, "private ip blocked"
    if host.startswith("169.254."):
        return True, "link-local blocked"
    if host.startswith("100.64."):  # CGNAT
        return True, "shared address space blocked"
    return False, ""


# ---------- Rate limiter ----------
class RateLimiter:
    """Simple sliding-window limiter: max N events per window_sec per key."""

    def __init__(self) -> None:
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window_sec: float = 60.0) -> tuple[bool, int, int]:
        """
        Returns (allowed, remaining, retry_after_sec).
        """
        if limit <= 0:
            return True, 999, 0
        now = time.time()
        with self._lock:
            q = self._events[key]
            while q and (now - q[0]) > window_sec:
                q.popleft()
            if len(q) >= limit:
                retry = int(max(1, window_sec - (now - q[0])))
                return False, 0, retry
            q.append(now)
            remaining = max(0, limit - len(q))
            return True, remaining, 0


rate_limiter = RateLimiter()


def client_ip(request: Request) -> str:
    if TRUST_PROXY:
        xff = request.headers.get("x-forwarded-for") or ""
        if xff:
            return xff.split(",")[0].strip()[:80]
        xri = request.headers.get("x-real-ip")
        if xri:
            return xri.strip()[:80]
    if request.client:
        return request.client.host or "unknown"
    return "unknown"


def route_limit(path: str) -> int:
    if path.startswith("/api/chat"):
        return RATE_LIMIT_CHAT
    if path.startswith("/api/mcp"):
        return RATE_LIMIT_MCP
    if path.startswith("/api/"):
        return RATE_LIMIT_API
    return RATE_LIMIT_API * 2


# ---------- Audit log ----------
_audit_lock = threading.Lock()


def audit(event: str, **fields: Any) -> None:
    if not AUDIT_ENABLED:
        return
    rec = {
        "ts": time.time(),
        "event": event,
        "id": uuid.uuid4().hex[:12],
        **{k: redact_secrets(str(v)) if isinstance(v, str) else v for k, v in fields.items()},
    }
    line = json.dumps(rec, ensure_ascii=False)
    try:
        with _audit_lock:
            with AUDIT_LOG.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass


# ---------- Input validation ----------
def validate_chat_payload(messages: list[Any], max_tokens: Optional[int] = None) -> Optional[str]:
    if not isinstance(messages, list):
        return "messages must be a list"
    if len(messages) > MAX_MESSAGES:
        return f"too many messages (max {MAX_MESSAGES})"
    total = 0
    for m in messages:
        content = getattr(m, "content", None)
        if content is None and isinstance(m, dict):
            content = m.get("content")
        if content is None:
            continue
        if not isinstance(content, str):
            try:
                content = json.dumps(content)
            except Exception:
                content = str(content)
        total += len(content)
        if len(content) > MAX_MESSAGE_CHARS:
            return f"message too large (max {MAX_MESSAGE_CHARS} chars)"
    if total > MAX_MESSAGE_CHARS * 2:
        return "conversation payload too large"
    if max_tokens is not None and (max_tokens < 1 or max_tokens > 128_000):
        return "max_tokens out of range"
    return None


def sanitize_model_id(model: Optional[str]) -> Optional[str]:
    if model is None:
        return None
    m = str(model).strip()[:120]
    if not re.match(r"^[A-Za-z0-9_.:/@+\-]+$", m):
        return None
    return m


# ---------- Security middleware ----------
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        if not SECURITY_ENABLED:
            return await call_next(request)

        # Request ID
        req_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        request.state.request_id = req_id
        request.state.client_ip = client_ip(request)

        path = request.url.path

        # Body size guard for non-GET
        if request.method in {"POST", "PUT", "PATCH"}:
            cl = request.headers.get("content-length")
            if cl:
                try:
                    if int(cl) > MAX_BODY_BYTES:
                        audit("body_too_large", ip=request.state.client_ip, path=path, size=cl)
                        return JSONResponse(
                            {"detail": "request body too large", "request_id": req_id},
                            status_code=413,
                        )
                except ValueError:
                    pass

        # Optional app token gate
        if REQUIRE_APP_TOKEN and APP_TOKEN and path.startswith("/api/"):
            if path not in PUBLIC_PATHS and not path.startswith("/assets"):
                token = (
                    request.headers.get("x-aurora-token")
                    or request.headers.get("x-api-key")
                    or ""
                ).strip()
                # Also accept Authorization: Bearer
                auth = request.headers.get("authorization") or ""
                if auth.lower().startswith("bearer "):
                    token = token or auth[7:].strip()
                if token != APP_TOKEN:
                    audit("auth_failed", ip=request.state.client_ip, path=path)
                    return JSONResponse(
                        {"detail": "unauthorized", "request_id": req_id},
                        status_code=401,
                    )

        # Rate limit API routes
        if RATE_LIMIT_ENABLED and path.startswith("/api/"):
            limit = route_limit(path)
            key = f"{request.state.client_ip}:{path.split('?')[0].rsplit('/', 1)[0]}"
            # coarser key for chat
            if path.startswith("/api/chat"):
                key = f"{request.state.client_ip}:chat"
            elif path.startswith("/api/mcp"):
                key = f"{request.state.client_ip}:mcp"
            else:
                key = f"{request.state.client_ip}:api"
            allowed, remaining, retry = rate_limiter.allow(key, limit, 60.0)
            if not allowed:
                audit("rate_limited", ip=request.state.client_ip, path=path)
                return JSONResponse(
                    {
                        "detail": "rate limit exceeded",
                        "retry_after": retry,
                        "request_id": req_id,
                    },
                    status_code=429,
                    headers={"Retry-After": str(retry), "X-RateLimit-Limit": str(limit)},
                )
            request.state.rate_remaining = remaining
            request.state.rate_limit = limit

        response = await call_next(request)

        # Security headers
        response.headers["X-Request-ID"] = req_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = (
            "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
        )
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        # CSP: allow self + inline for SPA; block framing & mixed content
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com data:; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-src 'self' blob: data:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers["Content-Security-Policy"] = csp
        if HSTS_ENABLED and request.url.scheme == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if getattr(request.state, "rate_remaining", None) is not None:
            response.headers["X-RateLimit-Limit"] = str(getattr(request.state, "rate_limit", ""))
            response.headers["X-RateLimit-Remaining"] = str(request.state.rate_remaining)
        # Don't leak server banner
        if "server" in response.headers:
            del response.headers["server"]
        return response


def security_status() -> dict[str, Any]:
    return {
        "enabled": SECURITY_ENABLED,
        "rate_limit": RATE_LIMIT_ENABLED,
        "limits": {
            "chat_per_min": RATE_LIMIT_CHAT,
            "api_per_min": RATE_LIMIT_API,
            "mcp_per_min": RATE_LIMIT_MCP,
            "max_body_bytes": MAX_BODY_BYTES,
            "max_message_chars": MAX_MESSAGE_CHARS,
        },
        "require_app_token": REQUIRE_APP_TOKEN and bool(APP_TOKEN),
        "cors_origins": CORS_ORIGINS if CORS_ORIGINS != ["*"] else ["* (dev default — set AURORA_CORS_ORIGINS in prod)"],
        "hsts": HSTS_ENABLED,
        "audit_log": AUDIT_ENABLED,
        "block_private_urls": BLOCK_PRIVATE_URLS,
        "audit_path": str(AUDIT_LOG.name),
    }


def fingerprint_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
