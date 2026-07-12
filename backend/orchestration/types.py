"""Shared types for Aurora orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StepSpec:
    id: str
    module: str
    title: str
    instruction: str
    depends_on: list[str] = field(default_factory=list)
    parallel_group: Optional[str] = None
    use_tools: bool = False
    max_tokens: int = 2048


@dataclass
class PipelineSpec:
    id: str
    name: str
    description: str
    steps: list[StepSpec] = field(default_factory=list)
    dynamic: bool = False  # planner fills steps


@dataclass
class ModuleResult:
    step_id: str
    module: str
    title: str
    ok: bool
    content: str = ""
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    model: str = ""
    label: str = ""
    error: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestrationEvent:
    type: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"aurora_event": self.type, **self.data}


@dataclass
class RunContext:
    goal: str
    history: list[dict[str, Any]]
    system: str
    temperature: float
    max_tokens: int
    settings: dict[str, Any]
    routes: list[Any]
    workspace_notes: list[str] = field(default_factory=list)
    results: dict[str, ModuleResult] = field(default_factory=dict)

    def prior_text(self, step_ids: Optional[list[str]] = None, limit: int = 6000) -> str:
        ids = step_ids or list(self.results.keys())
        chunks = []
        for sid in ids:
            r = self.results.get(sid)
            if not r or not r.content:
                continue
            body = r.content.strip()
            if len(body) > 2500:
                body = body[:2500] + "…"
            chunks.append(f"### {r.title} ({r.module})\n{body}")
        text = "\n\n".join(chunks)
        return text[:limit]
