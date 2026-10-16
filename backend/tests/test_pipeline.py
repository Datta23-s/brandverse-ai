"""The 15-step pipeline definition matches Architecture.md §5.1 (Rules.md §8)."""

import re
from pathlib import Path

from app.core.pipeline import PIPELINE_STEPS, TOTAL_STEPS

ARCHITECTURE = Path(__file__).resolve().parents[2] / "Architecture.md"


def _graph_block() -> str:
    text = ARCHITECTURE.read_text(encoding="utf-8")
    section = text.split("### 5.1 Graph shape", 1)[1].split("### 5.2", 1)[0]
    return section.split("```", 2)[1]


def test_fifteen_steps_numbered_in_order() -> None:
    assert TOTAL_STEPS == 15
    assert [s.number for s in PIPELINE_STEPS] == list(range(1, 16))


def test_names_are_unique() -> None:
    assert len({s.name for s in PIPELINE_STEPS}) == TOTAL_STEPS


def test_every_node_is_in_the_architecture_graph_at_its_step() -> None:
    graph = _graph_block()
    for step in PIPELINE_STEPS:
        for node in step.nodes:
            line = next((ln for ln in graph.splitlines() if re.search(rf"\b{node}\b", ln)), None)
            assert line is not None, f"{node} (step {step.number}) not in Architecture.md §5.1"
            if "(step" in line:
                assert re.search(rf"\(step {step.number}[ab]?\)", line), (
                    f"{node} is step {step.number} here but not in Architecture.md: {line.strip()}"
                )


def test_every_graph_node_is_covered() -> None:
    # Nodes are the lowercase names after a branch (├─, └─) or an edge (──►).
    nodes_in_doc = set(re.findall(r"(?:├─|└─|──►)\s+([a-z_]+)", _graph_block()))
    covered = {node for step in PIPELINE_STEPS for node in step.nodes}
    assert nodes_in_doc == covered
