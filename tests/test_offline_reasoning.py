"""A twin must be able to construct and run its reasoning tier with no provider
and no graph database.

The two pieces that make that possible are ``OfflineChatModel`` — a real chat
model that answers in-process — and ``NullGraphDriver``, which satisfies the
knowledge-graph driver contract while storing nothing. What matters here is that
they compose with the machinery that would otherwise demand a network: the
tool-calling agent builder, and ``KnowledgeGraph``'s schema setup and queries.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from dyon.core.config import LLMConfig, TwinConfig
from dyon.intelligent import KnowledgeGraph, NullGraphDriver, OfflineChatModel
from dyon.intelligent.agent import build_llm
from dyon.intelligent.knowledge_graph import (
    FailureMode,
    KnowledgeGraphSpec,
    SymptomMapping,
)
from dyon.intelligent.offline_llm import default_responder


@pytest.fixture
def config() -> TwinConfig:
    return TwinConfig(asset_id="pump_001", asset_type="pump", asset_name="Pump")


# --------------------------------------------------------------------------- #
# The offline chat model
# --------------------------------------------------------------------------- #


def test_build_llm_returns_offline_model_without_credentials():
    cfg = TwinConfig(llm=LLMConfig(provider="offline", model="ignored"))
    llm = build_llm(cfg)
    assert isinstance(llm, OfflineChatModel)


def test_default_answer_names_itself_as_offline():
    reply = OfflineChatModel().invoke([HumanMessage(content="Why is it hot?")])
    assert isinstance(reply, AIMessage)
    assert "offline model" in reply.content
    assert "Why is it hot?" in reply.content


def test_answers_are_deterministic():
    llm = OfflineChatModel()
    prompt = [HumanMessage(content="Status?")]
    assert llm.invoke(prompt).content == llm.invoke(prompt).content


def test_domain_responder_replaces_the_default():
    llm = OfflineChatModel(responder=lambda prompt: f"seen {len(prompt)} chars")
    assert llm.invoke([HumanMessage(content="abcde")]).content == "seen 5 chars"


def test_responder_sees_the_whole_conversation():
    seen: list[str] = []

    def responder(prompt: str) -> str:
        seen.append(prompt)
        return "ok"

    OfflineChatModel(responder=responder).invoke([
        SystemMessage(content="You are a pump twin."),
        HumanMessage(content="Diagnose."),
    ])
    assert "You are a pump twin." in seen[0]
    assert "Diagnose." in seen[0]


def test_multipart_content_is_flattened_to_text():
    flat = OfflineChatModel.flatten([
        HumanMessage(content=[
            {"type": "text", "text": "look at this"},
            {"type": "image_url", "image_url": "http://example.invalid/x.png"},
        ])
    ])
    assert flat == "look at this"


def test_long_prompts_are_truncated():
    reply = default_responder("word " * 500)
    assert reply.endswith("...")
    assert len(reply) < 500


@pytest.mark.asyncio
async def test_async_invocation_matches_sync():
    llm = OfflineChatModel(responder=lambda p: p.upper())
    reply = await llm.ainvoke([HumanMessage(content="quiet")])
    assert reply.content == "QUIET"


def test_bind_tools_accepts_and_ignores_tools():
    from langchain_core.tools import tool

    @tool
    def read_sensor(field: str) -> str:
        """Read a sensor."""
        return "42"

    bound = OfflineChatModel().bind_tools([read_sensor])
    reply = bound.invoke([HumanMessage(content="read the sensor")])
    assert reply.tool_calls == []


def test_tool_calling_agent_builds_against_the_offline_model():
    # The reasoning tier compiles its agents with create_tool_calling_agent, so
    # an offline twin must survive that call rather than fail at construction.
    from langchain_classic.agents import create_tool_calling_agent
    from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
    from langchain_core.tools import tool

    @tool
    def read_sensor(field: str) -> str:
        """Read a sensor."""
        return "42"

    prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a twin."),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(
        OfflineChatModel(responder=lambda p: "All nominal."), [read_sensor], prompt
    )
    assert agent is not None


def test_diagnostic_agent_answers_offline(config):
    from dyon.intelligent import DiagnosticAgent

    class _Store:
        def get_latest(self, field, measurement="asset_telemetry"):
            return 1.0

    agent = DiagnosticAgent(
        config,
        llm=OfflineChatModel(responder=lambda p: "Bearing temperature is nominal."),
        ts_store=_Store(),
    )
    import asyncio

    answer = asyncio.run(agent.ask("How is the bearing?"))
    assert answer == "Bearing temperature is nominal."


# --------------------------------------------------------------------------- #
# The null graph driver
# --------------------------------------------------------------------------- #


_SPEC = KnowledgeGraphSpec(
    components=["bearing"],
    failure_modes=[
        FailureMode(
            name="overheating",
            severity="high",
            maintenance_actions=["inspect"],
            affected_components=["bearing"],
        )
    ],
    symptom_mappings=[
        SymptomMapping("hot_bearing", "bearing_temp_c", 80.0, ["overheating"], "high")
    ],
)


def test_knowledge_graph_accepts_the_null_driver(config):
    kg = KnowledgeGraph(config, NullGraphDriver())
    kg.setup_from_spec(_SPEC)  # must not raise
    assert kg.get_components() == []
    assert kg.diagnose(["hot_bearing"]) == []


def test_threshold_diagnosis_still_works_without_a_graph(config):
    # Symptom detection is evaluated in Python off the spec, so it survives the
    # database being absent — this is what makes the null driver usable.
    kg = KnowledgeGraph(config, NullGraphDriver())
    kg.setup_from_spec(_SPEC)
    assert kg.diagnose_from_readings({"bearing_temp_c": 95.0}) == ["hot_bearing"]
    assert kg.diagnose_from_readings({"bearing_temp_c": 20.0}) == []


def test_recording_driver_captures_the_schema_it_was_given(config):
    driver = NullGraphDriver(record_statements=True)
    KnowledgeGraph(config, driver).setup_from_spec(_SPEC)
    statements = [stmt for stmt, _params in driver.statements()]
    assert any("MERGE (a:Asset" in s for s in statements)
    assert any("MERGE (f:FailureMode" in s for s in statements)


def test_session_is_a_context_manager_returning_empty_results():
    driver = NullGraphDriver()
    with driver.session() as session:
        result = session.run("MATCH (n) RETURN n")
        assert list(result) == []
        assert len(result) == 0
        assert result.single() is None
        assert result.data() == []


def test_driver_rejection_of_bad_arguments_still_applies(config):
    # KnowledgeGraph guards against being handed a spec instead of a driver; the
    # null driver must not weaken that.
    with pytest.raises(TypeError):
        KnowledgeGraph(config, _SPEC)
