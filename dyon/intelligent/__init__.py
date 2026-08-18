from dyon.intelligent.agent import DiagnosticAgent
from dyon.intelligent.base import AgentRegistry, TwinAgent
from dyon.intelligent.graph_drivers import NullGraphDriver
from dyon.intelligent.knowledge_graph import (
    FailureMode,
    KnowledgeGraph,
    KnowledgeGraphSpec,
    SymptomMapping,
)
from dyon.intelligent.mas import MultiAgentSystem

# Re-exported lazily: the offline chat model pulls in langchain_core, which code
# that only wants the knowledge-graph or agent-registry types should not pay for.
_LAZY = ("OfflineChatModel", "default_responder")

__all__ = [
    "AgentRegistry",
    "DiagnosticAgent",
    "FailureMode",
    "KnowledgeGraph",
    "KnowledgeGraphSpec",
    "MultiAgentSystem",
    "NullGraphDriver",
    "OfflineChatModel",
    "SymptomMapping",
    "TwinAgent",
    "default_responder",
]


def __getattr__(name: str):
    if name in _LAZY:
        from dyon.intelligent import offline_llm

        return getattr(offline_llm, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
