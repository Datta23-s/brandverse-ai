"""The graph (Architecture.md §5.1, §5.3): 15 steps, a parallel fan-out at step 7, a revision loop
between steps 10 and 11, and an interrupt before step 12.

Compiled with a checkpointer and `interrupt_before=["await_human_approval"]`: the run suspends
before that node, nothing is held in memory, and approval resumes it from the checkpoint
(`runner.py`).
"""

from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.orchestrator.deps import GraphDeps
from app.orchestrator.nodes import Nodes
from app.orchestrator.state import AgentTask, RunState

APPROVAL_NODE = "await_human_approval"


def build_graph(
    deps: GraphDeps, checkpointer: BaseCheckpointSaver[Any]
) -> CompiledStateGraph[RunState, Any, RunState, RunState]:
    n = Nodes(deps)
    g: StateGraph[RunState, Any, RunState, RunState] = StateGraph(RunState)

    g.add_node("normalise_request", n.stepped(1, n.normalise_request))
    g.add_node("load_product_context", n.stepped(2, n.load_product_context))
    g.add_node("validate_product_truth", n.stepped(3, n.validate_product_truth))
    g.add_node("retrieve_memory", n.stepped(4, n.retrieve_memory))
    g.add_node("assess_research_necessity", n.stepped(5, n.assess_research_necessity))
    g.add_node("focused_research", n.research_node("D1"))
    g.add_node("deep_research", n.research_node("D2"))
    g.add_node("select_agents", n.stepped(7, n.select_agents, complete=False))
    g.add_node("run_agent", n.run_agent, input_schema=AgentTask)
    g.add_node("agents_join", n.stepped(7, n.agents_join, start=False))
    g.add_node("personalise", n.stepped(8, n.personalise))
    g.add_node("build_strategy", n.stepped(9, n.build_strategy))
    g.add_node("generate_creative", n.stepped(10, n.generate_creative))
    g.add_node("validate_evidence", n.stepped(11, n.validate_evidence))
    g.add_node(APPROVAL_NODE, n.stepped(12, n.await_human_approval, start=False))
    g.add_node("assemble_campaign", n.stepped(13, n.assemble_campaign))
    g.add_node("record_feedback", n.stepped(14, n.record_feedback))
    g.add_node("write_memory", n.write_memory_node())

    g.add_edge(START, "normalise_request")
    g.add_edge("normalise_request", "load_product_context")
    g.add_edge("load_product_context", "validate_product_truth")
    g.add_conditional_edges(
        "validate_product_truth",
        n.route_after_truth,
        {"blocked": END, "ok": "retrieve_memory"},
    )
    g.add_edge("retrieve_memory", "assess_research_necessity")
    g.add_conditional_edges(
        "assess_research_necessity",
        n.route_depth,
        {"D0": "select_agents", "D1": "focused_research", "D2": "deep_research"},
    )
    g.add_edge("focused_research", "select_agents")
    g.add_edge("deep_research", "select_agents")
    g.add_conditional_edges("select_agents", n.fan_out, ["run_agent"])
    g.add_edge("run_agent", "agents_join")
    g.add_edge("agents_join", "personalise")
    g.add_edge("personalise", "build_strategy")
    g.add_edge("build_strategy", "generate_creative")
    g.add_edge("generate_creative", "validate_evidence")
    g.add_conditional_edges(
        "validate_evidence",
        n.route_after_validation,
        {"generate_creative": "generate_creative", APPROVAL_NODE: APPROVAL_NODE},
    )
    g.add_conditional_edges(
        APPROVAL_NODE,
        n.route_after_approval,
        {"generate_creative": "generate_creative", "assemble_campaign": "assemble_campaign"},
    )
    g.add_edge("assemble_campaign", "record_feedback")
    g.add_edge("record_feedback", "write_memory")
    g.add_edge("write_memory", END)

    return g.compile(checkpointer=checkpointer, interrupt_before=[APPROVAL_NODE])
