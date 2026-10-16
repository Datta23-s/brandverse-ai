"""The 15-step campaign pipeline (Architecture.md §5.1), as data.

The single source of step numbers and names for anything that reports run progress: the fake run
now (Phases.md Phase 1), the LangGraph pipeline from Phase 6. The UI renders these 15 steps.

Two steps cover more than one graph node; `nodes` records which:
- step 6 runs `focused_research` (D1) or `deep_research` (D2) and is skipped at D0;
- step 7 is `select_agents` (7a) followed by the parallel `run_agents` fan-out (7b).
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PipelineStep:
    number: int
    name: str
    title: str
    nodes: tuple[str, ...]


PIPELINE_STEPS: tuple[PipelineStep, ...] = (
    PipelineStep(1, "normalise_request", "Normalise request", ("normalise_request",)),
    PipelineStep(2, "load_product_context", "Load product context", ("load_product_context",)),
    PipelineStep(
        3, "validate_product_truth", "Validate product truth", ("validate_product_truth",)
    ),
    PipelineStep(4, "retrieve_memory", "Retrieve memory", ("retrieve_memory",)),
    PipelineStep(
        5,
        "assess_research_necessity",
        "Assess research necessity",
        ("assess_research_necessity",),
    ),
    PipelineStep(6, "research", "Research", ("focused_research", "deep_research")),
    PipelineStep(7, "run_agents", "Run specialist agents", ("select_agents", "run_agents")),
    PipelineStep(8, "personalise", "Personalise", ("personalise",)),
    PipelineStep(9, "build_strategy", "Build strategy", ("build_strategy",)),
    PipelineStep(10, "generate_creative", "Generate creative", ("generate_creative",)),
    PipelineStep(11, "validate_evidence", "Validate evidence", ("validate_evidence",)),
    PipelineStep(12, "await_human_approval", "Await human approval", ("await_human_approval",)),
    PipelineStep(13, "assemble_campaign", "Assemble campaign", ("assemble_campaign",)),
    PipelineStep(14, "record_feedback", "Record feedback", ("record_feedback",)),
    PipelineStep(15, "write_memory", "Write memory", ("write_memory",)),
)

TOTAL_STEPS = len(PIPELINE_STEPS)
