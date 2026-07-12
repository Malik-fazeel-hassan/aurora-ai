"""Built-in orchestration pipelines."""

from __future__ import annotations

from .types import PipelineSpec, StepSpec


def research_pipeline() -> PipelineSpec:
    return PipelineSpec(
        id="research",
        name="Research",
        description="Plan → research (tools) → analyze → critique → refine → summarize",
        steps=[
            StepSpec("plan", "planner", "Plan", "Create a research plan for the goal.", max_tokens=1200),
            StepSpec(
                "research",
                "researcher",
                "Research",
                "Investigate using tools; collect key facts and sources.",
                depends_on=["plan"],
                use_tools=True,
                max_tokens=2500,
            ),
            StepSpec(
                "analyze",
                "analyst",
                "Analyze",
                "Analyze findings and answer the core questions.",
                depends_on=["research"],
                max_tokens=2000,
            ),
            StepSpec(
                "critique",
                "critic",
                "Critique",
                "Review the analysis for gaps and errors.",
                depends_on=["analyze"],
                max_tokens=1200,
            ),
            StepSpec(
                "refine",
                "refiner",
                "Refine",
                "Produce the improved final research answer.",
                depends_on=["analyze", "critique"],
                max_tokens=2500,
            ),
            StepSpec(
                "summary",
                "summarizer",
                "Final answer",
                "Deliver the final user-facing answer only.",
                depends_on=["refine"],
                max_tokens=2000,
            ),
        ],
    )


def build_pipeline() -> PipelineSpec:
    return PipelineSpec(
        id="build",
        name="Build",
        description="Plan → architect → code (tools) → critique → refine",
        steps=[
            StepSpec("plan", "planner", "Plan", "Break the build goal into implementation steps.", max_tokens=1200),
            StepSpec(
                "architect",
                "architect",
                "Architecture",
                "Design structure, files, and interfaces.",
                depends_on=["plan"],
                max_tokens=1800,
            ),
            StepSpec(
                "code",
                "coder",
                "Implement",
                "Write the code/artifacts. Use write_file for multi-file work when helpful.",
                depends_on=["architect"],
                use_tools=True,
                max_tokens=3500,
            ),
            StepSpec(
                "critique",
                "critic",
                "Code review",
                "Review code quality, bugs, and missing pieces.",
                depends_on=["code"],
                max_tokens=1500,
            ),
            StepSpec(
                "refine",
                "refiner",
                "Polish",
                "Apply fixes and produce the final deliverable with code blocks.",
                depends_on=["code", "critique"],
                use_tools=True,
                max_tokens=3500,
            ),
        ],
    )


def analysis_pipeline() -> PipelineSpec:
    return PipelineSpec(
        id="analysis",
        name="Analysis",
        description="Plan → parallel research+analysis angles → critique → synthesize",
        steps=[
            StepSpec("plan", "planner", "Plan", "Frame the analysis and criteria.", max_tokens=1000),
            StepSpec(
                "research",
                "researcher",
                "Background",
                "Collect relevant background facts (tools OK).",
                depends_on=["plan"],
                use_tools=True,
                parallel_group="fanout",
                max_tokens=2000,
            ),
            StepSpec(
                "pros",
                "analyst",
                "Pros / opportunities",
                "Argue the strongest positive case or opportunities.",
                depends_on=["plan"],
                parallel_group="fanout",
                max_tokens=1600,
            ),
            StepSpec(
                "cons",
                "analyst",
                "Cons / risks",
                "Argue risks, downsides, and failure modes.",
                depends_on=["plan"],
                parallel_group="fanout",
                max_tokens=1600,
            ),
            StepSpec(
                "critique",
                "critic",
                "Challenge",
                "Stress-test the arguments for bias and missing evidence.",
                depends_on=["research", "pros", "cons"],
                max_tokens=1400,
            ),
            StepSpec(
                "summary",
                "summarizer",
                "Recommendation",
                "Give a decisive, balanced recommendation with rationale.",
                depends_on=["research", "pros", "cons", "critique"],
                max_tokens=2200,
            ),
        ],
    )


def write_pipeline() -> PipelineSpec:
    return PipelineSpec(
        id="write",
        name="Write",
        description="Outline → draft → critique → polish",
        steps=[
            StepSpec("plan", "planner", "Outline", "Create a tight outline for the piece.", max_tokens=1000),
            StepSpec(
                "draft",
                "writer",
                "Draft",
                "Write a full draft following the outline.",
                depends_on=["plan"],
                max_tokens=3000,
            ),
            StepSpec(
                "critique",
                "critic",
                "Editorial review",
                "Edit for clarity, structure, and impact.",
                depends_on=["draft"],
                max_tokens=1200,
            ),
            StepSpec(
                "refine",
                "refiner",
                "Final draft",
                "Produce the polished final version.",
                depends_on=["draft", "critique"],
                max_tokens=3000,
            ),
        ],
    )


def quick_pipeline() -> PipelineSpec:
    return PipelineSpec(
        id="quick",
        name="Quick",
        description="Fast plan → execute (tools) → answer",
        steps=[
            StepSpec("plan", "planner", "Quick plan", "List 2-4 actions max.", max_tokens=800),
            StepSpec(
                "exec",
                "executor",
                "Execute",
                "Do the work with tools if needed, then answer.",
                depends_on=["plan"],
                use_tools=True,
                max_tokens=2500,
            ),
            StepSpec(
                "summary",
                "summarizer",
                "Answer",
                "Final concise answer for the user.",
                depends_on=["exec"],
                max_tokens=1800,
            ),
        ],
    )


def custom_dynamic_pipeline() -> PipelineSpec:
    """Planner-first; orchestrator expands steps dynamically."""
    return PipelineSpec(
        id="auto",
        name="Auto orchestrate",
        description="Planner chooses modules dynamically, then executes a tailored pipeline",
        dynamic=True,
        steps=[
            StepSpec(
                "route",
                "router_note",
                "Route",
                "Classify the goal and pick the best pipeline style.",
                max_tokens=400,
            ),
            StepSpec(
                "plan",
                "planner",
                "Dynamic plan",
                "Create a concrete multi-step plan the team will execute.",
                depends_on=["route"],
                max_tokens=1200,
            ),
        ],
    )


PIPELINES: dict[str, callable] = {
    "research": research_pipeline,
    "build": build_pipeline,
    "analysis": analysis_pipeline,
    "write": write_pipeline,
    "quick": quick_pipeline,
    "auto": custom_dynamic_pipeline,
}


def get_pipeline(pipeline_id: str) -> PipelineSpec:
    key = (pipeline_id or "auto").lower().strip()
    factory = PIPELINES.get(key) or PIPELINES["auto"]
    return factory()


def list_pipeline_info() -> list[dict]:
    out = []
    for pid, factory in PIPELINES.items():
        p = factory()
        out.append(
            {
                "id": p.id,
                "name": p.name,
                "description": p.description,
                "steps": len(p.steps),
                "dynamic": p.dynamic,
                "modules": [s.module for s in p.steps],
            }
        )
    return out
