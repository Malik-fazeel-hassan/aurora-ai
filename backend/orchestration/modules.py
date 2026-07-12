"""
Orchestration modules — discrete agent roles used by pipelines.

Each module receives a RunContext + StepSpec and returns ModuleResult.
LLM calls go through a injected completer for failover/routing.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Optional

from tools import TOOL_SPECS, get_all_tool_specs, run_tool
try:
    from tools import run_tool_async
except Exception:
    run_tool_async = None

from .types import ModuleResult, RunContext, StepSpec

Completer = Callable[..., Awaitable[tuple[Optional[str], Optional[Any], str]]]
ToolRunner = Callable[..., Awaitable[tuple[Optional[str], Optional[Any], str, list[dict]]]]


MODULE_PROMPTS: dict[str, str] = {
    "planner": (
        "You are the Planner module. Break the user goal into a clear, ordered plan.\n"
        "Output markdown with:\n"
        "1. Goal restatement (1 line)\n"
        "2. Assumptions\n"
        "3. Numbered steps (actionable)\n"
        "4. Risks / open questions\n"
        "Be concise and practical."
    ),
    "researcher": (
        "You are the Researcher module. Gather facts needed for the goal.\n"
        "If tools are available, use web_search / fetch_url for current info.\n"
        "Output: key findings as bullets with sources/URLs when possible, then a short synthesis."
    ),
    "analyst": (
        "You are the Analyst module. Reason carefully over prior findings.\n"
        "Compare options, trade-offs, and implications. Use structured markdown."
    ),
    "architect": (
        "You are the Architect module. Design a solution structure.\n"
        "Include components, interfaces, data flow, and a minimal implementation outline."
    ),
    "coder": (
        "You are the Coder module. Produce working code or concrete artifacts.\n"
        "Prefer complete, copy-pasteable code blocks. Use write_file for multi-file deliverables when tools allow."
    ),
    "writer": (
        "You are the Writer module. Produce polished prose for the user.\n"
        "Clear structure, strong opening, no fluff."
    ),
    "critic": (
        "You are the Critic module. Review prior outputs for correctness, gaps, risks, and quality.\n"
        "Output:\n"
        "- Verdict: pass | revise\n"
        "- Issues (bullets)\n"
        "- Required fixes (bullets)\n"
        "- Score 1-10"
    ),
    "refiner": (
        "You are the Refiner module. Apply the critic's required fixes and produce the improved final deliverable.\n"
        "If the critic said pass, polish lightly and return the best version."
    ),
    "executor": (
        "You are the Executor module. Carry out concrete tool-backed actions for the goal.\n"
        "Use calculator/python_eval/files/search as needed. Report what you did and results."
    ),
    "summarizer": (
        "You are the Summarizer module. Produce the final user-facing answer from all prior module outputs.\n"
        "Be complete but tight. Use markdown. Do not mention internal module names unless useful."
    ),
    "router_note": (
        "You classify the task and recommend the best orchestration style in JSON only:\n"
        '{"pipeline":"research|build|analysis|write|quick","reason":"...","complexity":1-5}'
    ),
}


def _build_messages(ctx: RunContext, step: StepSpec, role_prompt: str, extra: str = "") -> list[dict]:
    prior = ctx.prior_text(step.depends_on or None)
    user_parts = [
        f"## User goal\n{ctx.goal}",
        f"## Your assignment\n{step.instruction or step.title}",
    ]
    if prior:
        user_parts.append(f"## Inputs from previous modules\n{prior}")
    if extra:
        user_parts.append(extra)
    if ctx.workspace_notes:
        user_parts.append("## Workspace notes\n" + "\n".join(ctx.workspace_notes[-8:]))

    return [
        {"role": "system", "content": f"{ctx.system}\n\n{role_prompt}"},
        {"role": "user", "content": "\n\n".join(user_parts)},
    ]


async def run_module(
    step: StepSpec,
    ctx: RunContext,
    complete: Completer,
    complete_with_tools: Optional[ToolRunner] = None,
) -> ModuleResult:
    module = (step.module or "analyst").lower().strip()
    role = MODULE_PROMPTS.get(module, MODULE_PROMPTS["analyst"])
    messages = _build_messages(ctx, step, role)
    tokens = min(int(step.max_tokens or 2048), int(ctx.max_tokens or 4096))

    content = None
    route = None
    err = ""
    tool_trace: list[dict] = []

    if step.use_tools and complete_with_tools is not None:
        content, route, err, tool_trace = await complete_with_tools(
            ctx.routes, messages, ctx.temperature, tokens
        )
    else:
        content, route, err = await complete(ctx.routes, messages, ctx.temperature, tokens)

    if not content:
        return ModuleResult(
            step_id=step.id,
            module=module,
            title=step.title,
            ok=False,
            error=err or "module failed",
            meta={"tool_trace": tool_trace},
        )

    artifacts = _extract_artifacts(content)
    # Optional: parse planner dynamic steps
    meta: dict[str, Any] = {"tool_trace": tool_trace}
    if module == "planner":
        meta["plan_steps"] = _parse_numbered_steps(content)
    if module == "critic":
        meta["verdict"] = _parse_critic_verdict(content)

    return ModuleResult(
        step_id=step.id,
        module=module,
        title=step.title,
        ok=True,
        content=content,
        artifacts=artifacts,
        model=getattr(route, "model", "") if route else "",
        label=getattr(route, "label", "") if route else "",
        meta=meta,
    )


def _extract_artifacts(content: str) -> list[dict[str, Any]]:
    arts = []
    for m in re.finditer(r"```(\w+)?\n([\s\S]*?)```", content or ""):
        lang = (m.group(1) or "text").lower()
        code = m.group(2)
        if len(code.strip()) < 40:
            continue
        arts.append({"type": "code", "lang": lang, "chars": len(code), "preview": code[:120]})
    return arts[:8]


def _parse_numbered_steps(text: str) -> list[str]:
    steps = []
    for line in (text or "").splitlines():
        m = re.match(r"\s*(?:\d+[\).]|[-*])\s+(.+)", line)
        if m:
            steps.append(m.group(1).strip()[:200])
    return steps[:12]


def _parse_critic_verdict(text: str) -> str:
    t = (text or "").lower()
    if re.search(r"verdict\s*:\s*pass", t) or "verdict: pass" in t:
        return "pass"
    if re.search(r"verdict\s*:\s*revise", t) or "needs revision" in t:
        return "revise"
    if "pass" in t[:200] and "revise" not in t[:200]:
        return "pass"
    return "revise"


async def tool_enabled_complete(
    routes,
    messages: list[dict],
    temperature: float,
    max_tokens: int,
    try_completion_fn,
    is_retriable_fn,
    max_tool_steps: int = 4,
) -> tuple[Optional[str], Optional[Any], str, list[dict]]:
    """
    Lightweight tool loop for research/executor/coder modules.
    Uses OpenAI-style tool calling when available.
    """
    working = list(messages)
    trace: list[dict] = []
    used_route = None
    last_err = ""

    for step in range(max_tool_steps):
        data = None
        for r in routes:
            data, status, err = await try_completion_fn(
                r.api_base,
                r.api_key,
                r.model,
                working,
                temperature,
                max_tokens,
                tools=get_all_tool_specs(),
                tool_choice="auto",
            )
            if not data and (is_retriable_fn(status, err) or "tool" in (err or "").lower()):
                data, status, err = await try_completion_fn(
                    r.api_base, r.api_key, r.model, working, temperature, max_tokens
                )
            if data:
                used_route = r
                break
            last_err = f"{getattr(r, 'label', r)}: {status} {(err or '')[:120]}"
        if not data:
            return None, used_route, last_err, trace

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        tool_calls = message.get("tool_calls") or []

        # XML fallback
        if not tool_calls and isinstance(content, str) and "<tool_call>" in content:
            for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, flags=re.S):
                try:
                    obj = json.loads(m.group(1))
                    tool_calls.append(
                        {
                            "id": f"call_{step}_{obj.get('name','t')}",
                            "type": "function",
                            "function": {
                                "name": obj.get("name"),
                                "arguments": json.dumps(obj.get("arguments") or {}),
                            },
                        }
                    )
                except Exception:
                    pass

        if not tool_calls:
            if isinstance(content, str):
                content = re.sub(r"<tool_call>[\s\S]*?</tool_call>", "", content).strip()
            return content or None, used_route, "", trace

        working.append({"role": "assistant", "content": content or "", "tool_calls": tool_calls})
        for call in tool_calls:
            fn = call.get("function") or {}
            name = fn.get("name") or ""
            raw_args = fn.get("arguments") or "{}"
            if run_tool_async is not None:
                result = await run_tool_async(name, raw_args)
            else:
                result = run_tool(name, raw_args)
            trace.append({"name": name, "ok": bool(result.get("ok", True)), "preview": json.dumps(result)[:180]})
            working.append(
                {
                    "role": "tool",
                    "tool_call_id": call.get("id") or f"call_{step}",
                    "name": name,
                    "content": json.dumps(result, ensure_ascii=False)[:12000],
                }
            )

    return "Module stopped after max tool steps without a final answer.", used_route, "", trace
