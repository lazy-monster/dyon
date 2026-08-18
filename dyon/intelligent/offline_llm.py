"""A chat model that answers without a provider.

Every reasoning path in the framework goes through a LangChain chat model, which
means a twin cannot be exercised end to end without an API key and a network —
not in a test, not in a demo on a train, not on an air-gapped machine. This
module supplies the missing piece: a real ``BaseChatModel`` that never leaves
the process.

It is deliberately not a mock. It is deterministic (the same prompt yields the
same answer), it is fast, and it composes with the rest of LangChain — including
:func:`create_tool_calling_agent`, which the reasoning tier builds its agents
with. What it cannot do is reason, so the default answer is an honest summary of
what was asked rather than an invention.

The interesting use is the second constructor argument. A twin that knows its
own domain can hand in a ``responder`` and get sensible offline copy in its own
voice:

::

    from dyon.intelligent.offline_llm import OfflineChatModel

    def responder(prompt: str) -> str:
        return "Bearing temperature is climbing; schedule an inspection."

    llm = OfflineChatModel(responder=responder)

Set ``DT_LLM__PROVIDER=offline`` to get the default responder from
:func:`~dyon.intelligent.agent.build_llm`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import Runnable

# Prompts arrive as prose paragraphs; this keeps the echoed excerpt to the first
# sentence or two rather than replaying an entire system prompt.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

MAX_ECHO_SENTENCES = 2


def default_responder(prompt: str) -> str:
    """Answer with a short, honest acknowledgement of the prompt.

    Used when no domain responder is supplied. It states plainly that no model
    is connected, so an offline reply is never mistaken for a real one in a
    transcript or a log.
    """
    excerpt = " ".join(_SENTENCE_SPLIT.split(prompt.strip())[:MAX_ECHO_SENTENCES])
    if len(excerpt) > 400:
        excerpt = excerpt[:397].rstrip() + "..."
    if not excerpt:
        return "[offline model] No prompt content to respond to."
    return f"[offline model] No language model is configured. Prompt received: {excerpt}"


class OfflineChatModel(BaseChatModel):
    """A ``BaseChatModel`` that answers from a local callable.

    ``responder`` receives the flattened prompt text and returns the reply. It
    may be synchronous or return a string directly; async responders are not
    supported because the point of this model is to have no I/O to await.
    """

    responder: Callable[[str], str] = default_responder
    model_name: str = "dyon-offline"

    # A plain callable is not a pydantic-friendly type, so the model config has
    # to allow it through untouched.
    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "dyon-offline"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_name": self.model_name}

    @staticmethod
    def flatten(messages: Sequence[BaseMessage]) -> str:
        """Render a message list as the plain prompt text the responder sees."""
        parts: list[str] = []
        for message in messages:
            content = message.content
            if isinstance(content, list):
                # Multi-part content (text + images); keep only the text parts.
                content = " ".join(
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict)
                )
            text = str(content).strip()
            if text:
                parts.append(text)
        return "\n\n".join(parts)

    def _result(self, messages: Sequence[BaseMessage]) -> ChatResult:
        reply = self.responder(self.flatten(messages))
        message = AIMessage(content=reply)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._result(messages)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        return self._result(messages)

    def bind_tools(
        self,
        tools: Sequence[Any],
        **kwargs: Any,
    ) -> Runnable[Any, BaseMessage]:
        """Accept tools and ignore them.

        ``create_tool_calling_agent`` refuses to build against a model without
        this method, so declining tools politely is what lets an offline twin
        construct the same agent graph as an online one. The model then simply
        never emits a tool call, and the executor treats the first reply as the
        final answer.
        """
        return self


__all__ = ["OfflineChatModel", "default_responder"]
