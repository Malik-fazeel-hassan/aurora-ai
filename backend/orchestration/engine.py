"""
Aurora Orchestrator — runs modular multi-agent pipelines.

Supports:
- sequential steps with dependencies
- parallel groups (fan-out)
- tool-enabled modules
- dynamic auto pipeline expansion
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any, AsyncGenerator, Awaitable, Callable, Optional

from .modules import MODULE_PROMPTS, run_module, tool_enabled_complete
from .pipelines import get_pipeline, list_pipeline_info
from .types import ModuleResult, OrchestrationEvent, PipelineSpec, RunContext, StepSpec

Completer = Callable[..., Awaitable[tuple[Optional[str], Optional[Any], str]]]


def list_pipelines() -> list[dict]:
    return list_pipeline_info()


class Orchestrator:
    def __init__(
        self,
        routes: list[Any],
        settings: dict[str, Any],
        complete: Completer,
        try_completion_fn,
        is_retriable_fn,
    ):
        self.routes = routes
        self.settings = settings
        self.complete = complete
        self.try_completion_fn = try_completion_fn
        self.is_retriable_fn = is_retriable_fn

    async def _complete_with_tools(self, routes, messages, temperature, max_tokens):
        return await tool_enabled_complete(
            routes,
            messages,
            temperature,
            max_tokens,
            self.try_completion_fn,
            self.is_retriable_fn,
            max_tool_steps=int(self.settings.get("agent_max_steps") or 6),
        )

    def _expand_auto(self, route_text: str, plan_text: str, base: PipelineSpec) -> list[StepSpec]:
        """Map router classification + plan into a concrete step list."""
        chosen = "quick"
        m = re.search(r"\{[\s\S]*\}", route_text or "")
        if m:
            try:
                obj = json.loads(m.group(0))
                cand = str(obj.get("pipeline") or "").lower()
                if cand in ("research", "build", "analysis", "write", "quick"):
                    chosen = cand
            except Exception:
                pass
        # keyword fallback
        blob = f"{route_text}\n{plan_text}".lower()
        if chosen == "quick":
            if any(k in blob for k in ("code", "implement", "build", "api", "function", "app")):
                chosen = "build"
            elif any(k in blob for k in ("research", "latest", "sources", "news", "compare market")):
                chosen = "research"
            elif any(k in blob for k in ("pros", "cons", "should we", "decision", "trade-off", "analyze")):
                chosen = "analysis"
            elif any(k in blob for k in ("write", "blog", "essay", "email", "copy", "article")):
                chosen = "write"

        template = get_pipeline(chosen)
        # Keep the dynamic plan result as first dependency input by rewriting depends
        steps = []
        for s in template.steps:
            dep = list(s.depends_on)
            # attach plan output from auto pipeline id "plan" if first real step
            if not dep and s.id != "plan":
                dep = ["plan"]
            elif "plan" not in dep and s.id not in ("plan", "route"):
                # ensure first-level template steps can see the auto plan
                if not dep:
                    dep = ["plan"]
            steps.append(
                StepSpec(
                    id=s.id if s.id not in ("plan", "route") else f"exec_{s.id}",
                    module=s.module,
                    title=s.title,
                    instruction=s.instruction,
                    depends_on=dep if s.id != "plan" else ["plan"],
                    parallel_group=s.parallel_group,
                    use_tools=s.use_tools,
                    max_tokens=s.max_tokens,
                )
            )
        # Avoid colliding with existing plan/route ids
        fixed = []
        for s in steps:
            sid = s.id
            if sid in ("plan", "route"):
                sid = f"t_{sid}"
            # map depends plan -> existing dynamic plan id
            deps = [("plan" if d == "plan" else d) for d in s.depends_on]
            # rewrite internal template plan dependency names
            deps2 = []
            for d in deps:
                if d == "plan":
                    deps2.append("plan")
                elif d in ("research", "analyze", "architect", "code", "critique", "refine", "summary", "pros", "cons", "draft", "exec"):
                    # keep as-is if present later
                    deps2.append(d if d != "plan" else "plan")
                else:
                    deps2.append(d)
            fixed.append(
                StepSpec(
                    id=s.id,
                    module=s.module,
                    title=s.title,
                    instruction=s.instruction,
                    depends_on=deps2,
                    parallel_group=s.parallel_group,
                    use_tools=s.use_tools,
                    max_tokens=s.max_tokens,
                )
            )
        # Prefer clean template ids; dependency on dynamic "plan" is enough
        clean = []
        for s in template.steps:
            deps = list(s.depends_on)
            if s.id != "plan" and "plan" not in deps and not deps:
                deps = ["plan"]
            elif s.id != "plan" and s.depends_on == ["plan"]:
                deps = ["plan"]
            # If template has its own plan, rename to tplan and depend on auto plan
            sid = s.id
            deps_out = []
            for d in deps:
                if d == "plan":
                    deps_out.append("plan")  # the dynamic planner output already in ctx
                else:
                    deps_out.append(d)
            if sid == "plan":
                # skip duplicate planner; dynamic plan already done
                continue
            clean.append(
                StepSpec(
                    id=sid,
                    module=s.module,
                    title=s.title,
                    instruction=s.instruction,
                    depends_on=deps_out,
                    parallel_group=s.parallel_group,
                    use_tools=s.use_tools,
                    max_tokens=s.max_tokens,
                )
            )
        return clean

    async def run(
        self,
        pipeline_id: str,
        goal: str,
        history: list[dict],
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> tuple[str, list[ModuleResult], str]:
        """Non-stream run. Returns final_text, all results, pipeline_id used."""
        events: list[str] = []

        async def collect():
            final = ""
            results: list[ModuleResult] = []
            pid = pipeline_id
            async for ev in self.stream(pipeline_id, goal, history, system, temperature, max_tokens):
                et = ev.get("aurora_event")
                if et == "orch_step_done":
                    results.append(
                        ModuleResult(
                            step_id=ev.get("step_id", ""),
                            module=ev.get("module", ""),
                            title=ev.get("title", ""),
                            ok=bool(ev.get("ok")),
                            content=ev.get("content", ""),
                            model=ev.get("model", ""),
                            label=ev.get("label", ""),
                            error=ev.get("error", ""),
                        )
                    )
                if et == "orch_final":
                    final = ev.get("content") or final
                    pid = ev.get("pipeline") or pid
                if et == "orch_done" and ev.get("final"):
                    final = ev.get("final") or final
            return final, results, pid

        return await collect()

    async def stream(
        self,
        pipeline_id: str,
        goal: str,
        history: list[dict],
        system: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncGenerator[dict[str, Any], None]:
        pipe = get_pipeline(pipeline_id)
        ctx = RunContext(
            goal=goal,
            history=history,
            system=system,
            temperature=temperature,
            max_tokens=max_tokens,
            settings=self.settings,
            routes=self.routes,
        )

        yield OrchestrationEvent(
            "orch_start",
            {
                "pipeline": pipe.id,
                "name": pipe.name,
                "description": pipe.description,
                "steps_planned": [{"id": s.id, "module": s.module, "title": s.title} for s in pipe.steps],
                "dynamic": pipe.dynamic,
            },
        ).to_dict()

        steps = list(pipe.steps)

        # Execute in waves respecting dependencies + parallel groups
        completed: set[str] = set()
        # For dynamic auto: run route+plan first, then expand
        if pipe.dynamic:
            for s in steps:
                async for ev in self._run_one(s, ctx):
                    yield ev
                completed.add(s.id)
            route_txt = ctx.results.get("route").content if ctx.results.get("route") else ""
            plan_txt = ctx.results.get("plan").content if ctx.results.get("plan") else ""
            expanded = self._expand_auto(route_txt, plan_txt, pipe)
            yield OrchestrationEvent(
                "orch_expanded",
                {
                    "from": "auto",
                    "steps": [{"id": s.id, "module": s.module, "title": s.title, "tools": s.use_tools} for s in expanded],
                },
            ).to_dict()
            steps = expanded

        # Wave scheduler
        pending = {s.id: s for s in steps if s.id not in completed}
        safety = 0
        while pending and safety < 30:
            safety += 1
            ready = [
                s
                for s in pending.values()
                if all(d in completed or d in ctx.results for d in (s.depends_on or []))
            ]
            if not ready:
                # break dependency deadlock by running any remaining
                ready = list(pending.values())[:1]

            # Group by parallel_group
            groups: dict[str, list[StepSpec]] = {}
            serial: list[StepSpec] = []
            for s in ready:
                if s.parallel_group:
                    groups.setdefault(s.parallel_group, []).append(s)
                else:
                    serial.append(s)

            # Run each parallel group concurrently, serial steps one-by-one
            for gid, members in groups.items():
                yield OrchestrationEvent(
                    "orch_parallel",
                    {"group": gid, "steps": [m.id for m in members]},
                ).to_dict()
                async for ev in self._run_many(members, ctx):
                    yield ev
                for m in members:
                    completed.add(m.id)
                    pending.pop(m.id, None)

            for s in serial:
                async for ev in self._run_one(s, ctx):
                    yield ev
                completed.add(s.id)
                pending.pop(s.id, None)

        final = self._compose_final(ctx)
        yield OrchestrationEvent(
            "orch_final",
            {
                "pipeline": pipe.id,
                "content": final,
                "modules_run": len(ctx.results),
                "ok_count": sum(1 for r in ctx.results.values() if r.ok),
            },
        ).to_dict()
        yield OrchestrationEvent(
            "orch_done",
            {"pipeline": pipe.id, "final": final, "steps": list(ctx.results.keys())},
        ).to_dict()

    async def _run_one(self, step: StepSpec, ctx: RunContext) -> AsyncGenerator[dict, None]:
        yield OrchestrationEvent(
            "orch_step_start",
            {
                "step_id": step.id,
                "module": step.module,
                "title": step.title,
                "use_tools": step.use_tools,
                "depends_on": step.depends_on,
            },
        ).to_dict()

        result = await run_module(
            step,
            ctx,
            complete=self.complete,
            complete_with_tools=self._complete_with_tools if step.use_tools else None,
        )
        ctx.results[step.id] = result

        preview = (result.content or result.error or "").replace("\n", " ")
        if len(preview) > 180:
            preview = preview[:180] + "…"

        yield OrchestrationEvent(
            "orch_step_done",
            {
                "step_id": result.step_id,
                "module": result.module,
                "title": result.title,
                "ok": result.ok,
                "model": result.model,
                "label": result.label,
                "error": result.error,
                "preview": preview,
                "content": result.content if result.ok else "",
                "artifacts": result.artifacts,
                "meta": {k: v for k, v in (result.meta or {}).items() if k != "tool_trace"},
                "tool_trace": (result.meta or {}).get("tool_trace") or [],
            },
        ).to_dict()

    async def _run_many(self, steps: list[StepSpec], ctx: RunContext) -> AsyncGenerator[dict, None]:
        # concurrent execution; emit start events first
        for s in steps:
            yield OrchestrationEvent(
                "orch_step_start",
                {
                    "step_id": s.id,
                    "module": s.module,
                    "title": s.title,
                    "use_tools": s.use_tools,
                    "depends_on": s.depends_on,
                    "parallel": True,
                },
            ).to_dict()

        async def run(s: StepSpec) -> ModuleResult:
            return await run_module(
                s,
                ctx,
                complete=self.complete,
                complete_with_tools=self._complete_with_tools if s.use_tools else None,
            )

        results = await asyncio.gather(*[run(s) for s in steps])
        for result in results:
            ctx.results[result.step_id] = result
            preview = (result.content or result.error or "").replace("\n", " ")
            if len(preview) > 180:
                preview = preview[:180] + "…"
            yield OrchestrationEvent(
                "orch_step_done",
                {
                    "step_id": result.step_id,
                    "module": result.module,
                    "title": result.title,
                    "ok": result.ok,
                    "model": result.model,
                    "label": result.label,
                    "error": result.error,
                    "preview": preview,
                    "content": result.content if result.ok else "",
                    "artifacts": result.artifacts,
                    "meta": {k: v for k, v in (result.meta or {}).items() if k != "tool_trace"},
                    "tool_trace": (result.meta or {}).get("tool_trace") or [],
                    "parallel": True,
                },
            ).to_dict()

    def _compose_final(self, ctx: RunContext) -> str:
        # Prefer last summarizer/refiner/writer/coder success
        priority = ["summary", "refine", "draft", "code", "analyze", "exec", "research", "plan"]
        for key in priority:
            r = ctx.results.get(key)
            if r and r.ok and r.content.strip():
                return r.content.strip()
        # fallback any ok result in reverse insertion order
        for sid, r in reversed(list(ctx.results.items())):
            if r.ok and r.content.strip() and r.module not in ("critic", "router_note"):
                return r.content.strip()
        # last resort stitch
        parts = []
        for r in ctx.results.values():
            if r.content:
                parts.append(f"## {r.title}\n\n{r.content}")
        return "\n\n".join(parts) if parts else "Orchestration produced no content."
