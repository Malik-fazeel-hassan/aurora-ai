"""
Aurora multi-model router + credit-aware failover.

We cannot remove provider billing, but we can:
- pick the best model for the task
- fail over across profiles/keys when rate-limited or out of credits
- prefer free/open models when premium routes fail
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Route:
    profile: str
    api_base: str
    api_key: str
    model: str
    label: str
    tier: str  # premium | standard | free | nvidia
    capabilities: set[str] = field(default_factory=set)


# Task → preferred capability tags
TASK_TAGS = {
    "chat": {"general", "chat"},
    "code": {"code", "general"},
    "reason": {"reason", "general"},
    "agent": {"agent", "code", "reason", "general"},
    "fast": {"fast", "general"},
    "long": {"long", "general"},
    "vision": {"vision", "general"},
    "write": {"write", "general"},
}


# Ordered candidate catalog. Actual availability depends on keys in settings.
CATALOG: list[dict[str, Any]] = [
    # Premium Claude (OpenRouter)
    {
        "profile": "openrouter",
        "model": "anthropic/claude-sonnet-4.6",
        "label": "Claude Sonnet 4.6",
        "tier": "premium",
        "capabilities": {"general", "chat", "code", "reason", "agent", "write", "long"},
    },
    {
        "profile": "openrouter",
        "model": "anthropic/claude-opus-4.6",
        "label": "Claude Opus 4.6",
        "tier": "premium",
        "capabilities": {"general", "chat", "code", "reason", "agent", "write", "long"},
    },
    {
        "profile": "openrouter",
        "model": "anthropic/claude-haiku-4.5",
        "label": "Claude Haiku 4.5",
        "tier": "standard",
        "capabilities": {"general", "chat", "code", "fast", "agent"},
    },
    {
        "profile": "openrouter",
        "model": "anthropic/claude-3-haiku",
        "label": "Claude 3 Haiku",
        "tier": "standard",
        "capabilities": {"general", "chat", "fast"},
    },
    # Strong non-Claude on OpenRouter
    {
        "profile": "openrouter",
        "model": "openai/gpt-4o",
        "label": "GPT-4o",
        "tier": "premium",
        "capabilities": {"general", "chat", "code", "reason", "vision", "agent"},
    },
    {
        "profile": "openrouter",
        "model": "openai/gpt-4o-mini",
        "label": "GPT-4o Mini",
        "tier": "standard",
        "capabilities": {"general", "chat", "code", "fast", "vision"},
    },
    {
        "profile": "openrouter",
        "model": "google/gemini-2.5-pro",
        "label": "Gemini 2.5 Pro",
        "tier": "premium",
        "capabilities": {"general", "chat", "code", "reason", "long", "vision"},
    },
    {
        "profile": "openrouter",
        "model": "google/gemini-2.5-flash",
        "label": "Gemini 2.5 Flash",
        "tier": "standard",
        "capabilities": {"general", "chat", "fast", "long", "vision"},
    },
    {
        "profile": "openrouter",
        "model": "deepseek/deepseek-r1",
        "label": "DeepSeek R1",
        "tier": "standard",
        "capabilities": {"reason", "code", "agent", "general"},
    },
    {
        "profile": "openrouter",
        "model": "deepseek/deepseek-chat",
        "label": "DeepSeek Chat",
        "tier": "standard",
        "capabilities": {"general", "chat", "code", "agent"},
    },
    {
        "profile": "openrouter",
        "model": "qwen/qwen3-235b-a22b",
        "label": "Qwen3 235B",
        "tier": "standard",
        "capabilities": {"general", "chat", "code", "reason", "agent", "long"},
    },
    {
        "profile": "openrouter",
        "model": "meta-llama/llama-3.3-70b-instruct",
        "label": "Llama 3.3 70B",
        "tier": "standard",
        "capabilities": {"general", "chat", "code", "agent"},
    },
    # Free OpenRouter routes (no credit burn when available)
    {
        "profile": "openrouter",
        "model": "openrouter/free",
        "label": "OpenRouter Free Router",
        "tier": "free",
        "capabilities": {"general", "chat", "fast"},
    },
    {
        "profile": "openrouter",
        "model": "qwen/qwen3-coder:free",
        "label": "Qwen3 Coder (free)",
        "tier": "free",
        "capabilities": {"code", "agent", "general"},
    },
    {
        "profile": "openrouter",
        "model": "qwen/qwen3-next-80b-a3b-instruct:free",
        "label": "Qwen3 Next 80B (free)",
        "tier": "free",
        "capabilities": {"general", "chat", "code", "agent", "long"},
    },
    {
        "profile": "openrouter",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "label": "Nemotron Super 120B (free)",
        "tier": "free",
        "capabilities": {"general", "reason", "agent", "long"},
    },
    {
        "profile": "openrouter",
        "model": "nvidia/nemotron-3-nano-30b-a3b:free",
        "label": "Nemotron Nano (free)",
        "tier": "free",
        "capabilities": {"general", "fast", "agent"},
    },
    {
        "profile": "openrouter",
        "model": "meta-llama/llama-3.2-3b-instruct:free",
        "label": "Llama 3.2 3B (free)",
        "tier": "free",
        "capabilities": {"general", "fast", "chat"},
    },
    {
        "profile": "openrouter",
        "model": "google/gemma-4-31b-it:free",
        "label": "Gemma 4 31B (free)",
        "tier": "free",
        "capabilities": {"general", "chat", "vision"},
    },
    # NVIDIA NIM profiles (separate credits/quotas)
    {
        "profile": "nvidia_minimax",
        "model": "minimaxai/minimax-m3",
        "label": "MiniMax M3 (NVIDIA)",
        "tier": "nvidia",
        "capabilities": {"general", "chat", "code", "agent", "vision", "reason"},
    },
    {
        "profile": "nvidia",
        "model": "meta/llama-3.1-70b-instruct",
        "label": "Llama 3.1 70B (NVIDIA)",
        "tier": "nvidia",
        "capabilities": {"general", "chat", "code", "agent"},
    },
    {
        "profile": "nvidia",
        "model": "meta/llama-3.3-70b-instruct",
        "label": "Llama 3.3 70B (NVIDIA)",
        "tier": "nvidia",
        "capabilities": {"general", "chat", "code", "agent"},
    },
    {
        "profile": "nvidia_qwen",
        "model": "qwen/qwen2.5-72b-instruct",
        "label": "Qwen2.5 72B (NVIDIA)",
        "tier": "nvidia",
        "capabilities": {"general", "chat", "code", "agent", "reason"},
    },
    {
        "profile": "nvidia_qwen",
        "model": "qwen/qwen2.5-coder-32b-instruct",
        "label": "Qwen2.5 Coder 32B (NVIDIA)",
        "tier": "nvidia",
        "capabilities": {"code", "agent", "general"},
    },
    {
        "profile": "nvidia_cosmos",
        "model": "meta/llama-3.1-8b-instruct",
        "label": "Llama 3.1 8B (NVIDIA)",
        "tier": "nvidia",
        "capabilities": {"general", "chat", "fast"},
    },
    {
        "profile": "nvidia",
        "model": "meta/llama-3.1-8b-instruct",
        "label": "Llama 3.1 8B (NVIDIA general)",
        "tier": "nvidia",
        "capabilities": {"general", "chat", "fast"},
    },
]

DEFAULT_BASES = {
    "openrouter": "https://openrouter.ai/api/v1",
    "nvidia": "https://integrate.api.nvidia.com/v1",
    "nvidia_qwen": "https://integrate.api.nvidia.com/v1",
    "nvidia_minimax": "https://integrate.api.nvidia.com/v1",
    "nvidia_cosmos": "https://integrate.api.nvidia.com/v1",
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}


def detect_task(text: str, agent_mode: bool = False) -> str:
    if agent_mode:
        return "agent"
    t = (text or "").lower()
    if any(k in t for k in ("image", "screenshot", "describe this photo", "vision")):
        return "vision"
    if any(
        k in t
        for k in (
            "write code",
            "implement",
            "refactor",
            "debug",
            "python",
            "javascript",
            "typescript",
            "bug",
            "function",
            "class ",
            "api endpoint",
            "sql",
            "regex",
        )
    ):
        return "code"
    if any(
        k in t
        for k in (
            "prove",
            "reason step",
            "chain of thought",
            "analyze deeply",
            "trade-off",
            "compare carefully",
            "math",
            "derive",
            "optimize algorithm",
        )
    ):
        return "reason"
    if any(k in t for k in ("long report", "research", "comprehensive", "detailed essay", "whitepaper")):
        return "long"
    if any(k in t for k in ("blog", "rewrite", "email", "story", "poem", "copywriting", "tone")):
        return "write"
    if len(t) < 40 and any(k in t for k in ("hi", "hello", "hey", "thanks", "ok", "yes", "no")):
        return "fast"
    return "chat"


def build_routes(settings: dict, task: str = "chat", prefer_free: bool = False) -> list[Route]:
    profiles = settings.get("profiles") or {}
    tags = TASK_TAGS.get(task, TASK_TAGS["chat"])

    # Also allow top-level key as openrouter fallback
    if "openrouter" not in profiles and settings.get("api_key"):
        profiles = {
            **profiles,
            "openrouter": {
                "api_base": settings.get("api_base") or DEFAULT_BASES["openrouter"],
                "api_key": settings.get("api_key"),
                "default_model": settings.get("default_model"),
            },
        }

    candidates: list[tuple[int, Route]] = []
    for entry in CATALOG:
        pid = entry["profile"]
        prof = profiles.get(pid) or {}
        key = (prof.get("api_key") or "").strip()
        if not key:
            continue
        base = (prof.get("api_base") or DEFAULT_BASES.get(pid) or "").rstrip("/")
        if not base:
            continue

        caps = set(entry.get("capabilities") or [])
        overlap = len(caps & tags)
        tier = entry["tier"]
        # Scoring: capability match first, then tier preference
        score = overlap * 10
        if prefer_free:
            score += {"free": 30, "nvidia": 12, "standard": 6, "premium": 0}.get(tier, 0)
        else:
            # Prefer premium quality, but keep free as deep fallback
            score += {"premium": 20, "standard": 12, "nvidia": 10, "free": 4}.get(tier, 0)
        # Slight boost if profile default matches
        if prof.get("default_model") == entry["model"]:
            score += 3

        route = Route(
            profile=pid,
            api_base=base,
            api_key=key,
            model=entry["model"],
            label=entry["label"],
            tier=tier,
            capabilities=caps,
        )
        candidates.append((score, route))

    candidates.sort(key=lambda x: x[0], reverse=True)

    # De-dupe identical (profile, model)
    seen: set[tuple[str, str]] = set()
    routes: list[Route] = []
    for _, r in candidates:
        k = (r.profile, r.model)
        if k in seen:
            continue
        seen.add(k)
        routes.append(r)
    return routes


def is_retriable_error(status: Optional[int], body: str) -> bool:
    b = (body or "").lower()
    if status in (402, 429, 503, 502, 500, 404, 401, 403):
        # 401/403/404 often mean model not available on this key — try next
        return True
    needles = (
        "rate limit",
        "rate_limit",
        "quota",
        "credit",
        "insufficient",
        "payment",
        "billing",
        "overloaded",
        "capacity",
        "not found",
        "no endpoints",
        "model not",
        "does not exist",
        "unsupported",
        "timeout",
        "temporarily",
        "try again",
        "provider returned error",
    )
    return any(n in b for n in needles)


def route_summary(routes: list[Route], limit: int = 8) -> list[dict[str, str]]:
    out = []
    for r in routes[:limit]:
        out.append(
            {
                "profile": r.profile,
                "model": r.model,
                "label": r.label,
                "tier": r.tier,
            }
        )
    return out
