"""
app/services/llm_service.py
────────────────────────────
LangChain + Groq agent factory and event-capture callback.

Architecture overview:
  ┌──────────────────────────────────────────────────────────────┐
  │  ExecutionService                                            │
  │    │                                                         │
  │    └─► build_agent_executor(tools, callbacks)               │  ← this file
  │              │                                               │
  │              ├─► ChatGroq(model=llama-3.3-70b-versatile)    │
  │              ├─► create_react_agent(llm, tools)  [LangGraph] │
  │              └─► ToolEventCallback  ← captures tool I/O     │
  └──────────────────────────────────────────────────────────────┘

Why LangGraph's create_react_agent instead of legacy AgentExecutor?
  LangChain 1.x / LangGraph is the current production API.
  The old langchain.agents.AgentExecutor is maintained for backwards
  compatibility but new projects should use LangGraph's prebuilt
  ReAct agent, which:
    • Uses native tool-calling (not XML/text parsing hacks).
    • Is more reliable with modern chat models.
    • Produces structured AIMessage.tool_calls lists we can inspect.
    • Supports streaming out of the box.

How tool event capture works:
  LangChain's callback system fires hooks at defined lifecycle points.
  We subclass BaseCallbackHandler and implement:
    on_tool_start(tool_name, input)  → fired before tool executes
    on_tool_end(output)              → fired after tool returns
    on_tool_error(error)             → fired if tool raises an exception

  The callback accumulates ToolCallRecord objects in a list.  After
  the agent finishes, the execution service reads this list to create
  AgentEvent rows in the database.

  This keeps the callback stateless between runs — each run gets a
  fresh ToolEventCallback instance.
"""

import logging
import time
from dataclasses import dataclass, field

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.tools import BaseTool
from langchain_groq import ChatGroq
from langgraph.prebuilt import create_react_agent

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Tool call record ──────────────────────────────────────────────────────────

@dataclass
class ToolCallRecord:
    """
    Immutable record of a single tool invocation captured by the callback.

    Populated in two phases:
      Phase 1 (on_tool_start): tool_name, input_data, start_time
      Phase 2 (on_tool_end):   output_data, latency_ms, error
    """
    tool_name:   str
    input_data:  dict
    output_data: dict | None  = None
    latency_ms:  float | None = None
    error:       str | None   = None   # set if the tool raised an exception
    start_time:  float        = field(default_factory=time.monotonic)


# ── Callback handler ──────────────────────────────────────────────────────────

class ToolEventCallback(BaseCallbackHandler):
    """
    LangChain callback that captures every tool invocation during a run.

    Lifecycle:
      1. Instantiate once per AgentRun (fresh state, no cross-run bleed).
      2. Pass to build_agent_executor() as part of the config dict.
      3. After agent.invoke() returns, read self.records for the full
         ordered list of tool calls.

    Thread safety:
      LangGraph runs synchronously within our async execution service
      (we use asyncio.to_thread).  The callback is called from the
      same thread as the agent, so no locking is needed.
    """

    def __init__(self) -> None:
        super().__init__()
        # Ordered list of completed tool call records.
        self.records: list[ToolCallRecord] = []
        # In-flight record while a tool is executing (between start and end).
        self._current: ToolCallRecord | None = None

    # ── LangChain callback hooks ──────────────────────────────────────

    def on_tool_start(
        self,
        serialized: dict,
        input_str: str,
        *,
        run_id,
        parent_run_id=None,
        tags=None,
        metadata=None,
        inputs=None,
        **kwargs,
    ) -> None:
        """
        Called by LangChain immediately before a tool function executes.

        `serialized` contains the tool's metadata dict including 'name'.
        `input_str` is the raw string argument passed to the tool.
        `inputs` (keyword, LangChain 1.x+) is the parsed dict of arguments.
        """
        tool_name = serialized.get("name", "unknown")

        # Prefer the structured `inputs` dict; fall back to wrapping
        # the raw string so input_data is always a dict, never a string.
        if inputs and isinstance(inputs, dict):
            input_data = inputs
        else:
            input_data = {"input": input_str}

        self._current = ToolCallRecord(
            tool_name=tool_name,
            input_data=input_data,
        )
        logger.debug("Tool start: %s | input: %s", tool_name, input_data)

    def on_tool_end(
        self,
        output,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """
        Called by LangChain immediately after a tool function returns.

        `output` may be a string, a ToolMessage, or another type
        depending on the LangChain version.  We normalise it to a dict.
        """
        if self._current is None:
            # Defensive — should not happen in normal flow.
            logger.warning("on_tool_end fired without a matching on_tool_start")
            return

        elapsed = time.monotonic() - self._current.start_time

        # Normalise the output to a dict for JSON storage.
        if hasattr(output, "content"):
            # ToolMessage or similar object with a .content attribute
            output_data = {"result": str(output.content)}
        elif isinstance(output, str):
            output_data = {"result": output}
        elif isinstance(output, dict):
            output_data = output
        else:
            output_data = {"result": str(output)}

        self._current.output_data = output_data
        self._current.latency_ms  = round(elapsed * 1000, 2)

        logger.debug(
            "Tool end: %s | latency=%.1fms | output: %s",
            self._current.tool_name,
            self._current.latency_ms,
            str(output_data)[:120],
        )

        self.records.append(self._current)
        self._current = None

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id,
        parent_run_id=None,
        **kwargs,
    ) -> None:
        """
        Called if a tool raises an unhandled exception.

        We still record the attempt so the audit trail is complete.
        The error text is stored in the record's `error` field.
        """
        if self._current is None:
            logger.warning("on_tool_error fired without a matching on_tool_start")
            return

        elapsed = time.monotonic() - self._current.start_time
        self._current.error      = str(error)
        self._current.output_data = {"error": str(error)}
        self._current.latency_ms  = round(elapsed * 1000, 2)

        logger.warning(
            "Tool error: %s | %s",
            self._current.tool_name, error
        )

        self.records.append(self._current)
        self._current = None


# ── LLM factory ───────────────────────────────────────────────────────────────

def build_llm() -> ChatGroq:
    """
    Construct a ChatGroq LLM instance from application settings.

    Why ChatGroq?
      Groq runs open-weight models (Llama 3.3 70B) on custom LPU hardware
      and is dramatically faster than most hosted LLM APIs.  Low latency
      is important for agent loops that may make multiple tool calls.

    Model choice — llama-3.3-70b-versatile:
      • 70B parameters: strong instruction-following and tool-use ability.
      • "versatile" variant: optimised for chat + function calling.
      • Supports native tool-calling (structured JSON, not text hacks).

    temperature=0:
      Deterministic outputs for tool calls.  We want the agent to make
      reliable, predictable tool calls — not creative ones.

    Returns:
        A configured ChatGroq instance ready for tool binding.

    Raises:
        ValueError: GROQ_API_KEY is not set in environment.
    """
    if not settings.GROQ_API_KEY or settings.GROQ_API_KEY == "not_set":
        raise ValueError(
            "GROQ_API_KEY is not configured. "
            "Add it to your .env file: GROQ_API_KEY=your_key_here"
        )

    return ChatGroq(
        model=settings.GROQ_MODEL,          # "llama-3.3-70b-versatile"
        api_key=settings.GROQ_API_KEY,
        temperature=0,                       # deterministic tool calls
        max_tokens=2048,                     # plenty for tool-augmented answers
    )


# ── Agent factory ─────────────────────────────────────────────────────────────

def build_agent(
    tools: list[BaseTool],
    callback: ToolEventCallback,
) -> object:
    """
    Build a LangGraph ReAct agent bound to the given tools.

    ReAct (Reason + Act) loop:
      1. LLM receives the user prompt.
      2. LLM decides whether to call a tool.  If yes → emits a
         structured tool_call in the AIMessage.
      3. LangGraph executes the tool and feeds the result back as
         a ToolMessage.
      4. LLM sees the tool result and either calls another tool
         or produces the final answer.
      Repeat until the LLM produces a plain text response.

    System prompt:
      The system message shapes agent behaviour.  We instruct it to:
        • Always use tools when needed (not guess answers).
        • Produce clear, structured final answers.
        • State when it cannot answer due to tool limitations.

    Args:
        tools:    List of LangChain BaseTool instances the agent may call.
        callback: ToolEventCallback instance for this run.

    Returns:
        A compiled LangGraph agent graph (CompiledStateGraph).
        Call .invoke({"messages": [HumanMessage(content=prompt)]},
                     config={"callbacks": [callback]})
        to execute it.
    """
    llm = build_llm()

    system_prompt = (
        "You are AgentWatch, a precise and helpful AI assistant with access to tools. "
        "Always use the available tools to answer questions — do not guess or make up data. "
        "\n\nTool usage guide:\n"
        "- calculator: mathematical expressions and arithmetic (e.g. sqrt(144), 2**10, 100/3). "
        "- weather: current weather conditions for any city. "
        "- file_reader: read files from the sandbox directory; use 'list' to see available files. "
        "- datetime_tool: current date, time, UTC time, day of week, Unix timestamp. "
        "  Queries: date | time | utc | day | datetime | timestamp | all. "
        "- currency_converter: convert between currencies using static rates. "
        "  Format: '<amount> <FROM> to <TO>' e.g. '100 USD to EUR'. "
        "- wikipedia_search: short summary of any topic from Wikipedia. "
        "- text_summarizer: extractive summary of long text (no extra LLM calls). "
        "- word_counter: word count, character count, sentence count for text. "
        "- json_formatter: validate, pretty-print, minify, or analyse JSON strings. "
        "  Operations: format | validate | minify | keys | stats. "
        "- uuid_generator: generate UUID v4 values. Format: '<count> [uppercase|no-hyphens|braces]'. "
        "\n"
        "Provide clear, concise answers based on tool results. "
        "If a tool returns an error, explain what went wrong to the user."
    )

    

    # create_react_agent is LangGraph's prebuilt ReAct agent.
    # It handles the full reason→act→observe loop automatically.
    # `prompt` here becomes the system message prepended to every conversation.
    agent = create_react_agent(
        model=llm,
        tools=tools,
        prompt=system_prompt,
    )

    return agent