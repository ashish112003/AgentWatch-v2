"""
app/governance/enforcer.py
───────────────────────────
Tool-level permission enforcement for AgentWatch.

Architecture overview:
  ┌─────────────────────────────────────────────────────────────────┐
  │  GovernanceEnforcer                                             │
  │                                                                 │
  │  Input:  ALL_TOOLS (3 tools)  +  agent.allowed_tools (list)    │
  │                                                                 │
  │  Output: governed_tools (3 tools, same names, different funcs) │
  │                                                                 │
  │  For each tool:                                                 │
  │    ├─ PERMITTED  → wrap with PermittedToolProxy                 │
  │    │               (calls the real tool, records the event)     │
  │    └─ BLOCKED    → wrap with BlockedToolProxy                   │
  │                    (never calls the real tool, records          │
  │                     violation, returns governance message)      │
  └─────────────────────────────────────────────────────────────────┘

Why pass ALL tools to the agent (as proxies) rather than just the
permitted subset?

  If we omit blocked tools from the agent's tool list entirely, the
  LLM cannot see them in its tool schema.  Two problems arise:

  1. We can't detect and log the *intent* to call a blocked tool —
     the LLM simply won't try.  The governance violation is invisible.

  2. If a user prompt explicitly mentions a tool by name ("use the
     weather tool"), the LLM will hallucinate an answer because it
     has no tool schema to reason about.

  By replacing blocked tools with proxy functions that share the
  same name and schema, we:
    • Preserve the LLM's full tool awareness.
    • Intercept the call before the real tool executes.
    • Log the violation with the attempted input.
    • Return a structured denial message the LLM can relay to the user.

Proxy pattern:
  Both proxy types are created via StructuredTool.from_function()
  with the original tool's name, description, and args_schema.
  This ensures the LLM sees an identical schema for permitted and
  blocked tools — the only difference is what happens at execution time.

Async note:
  The proxy callback functions are synchronous because StructuredTool
  with func= is the synchronous path.  The async governance violation
  write to the database happens AFTER the agent finishes, by reading
  ViolationRecord objects accumulated during the run.
  (We cannot do async DB writes inside the sync tool callback.)
"""

import logging
from dataclasses import dataclass, field as dc_field
from typing import Callable

from langchain_core.tools import BaseTool, StructuredTool

logger = logging.getLogger(__name__)

# Governance denial message template.
# Kept as a module constant so it's easy to find and customise.
_DENIAL_MESSAGE = (
    "Access denied: tool '{tool_name}' is not permitted for this agent. "
    "This violation has been logged."
)


# ── Violation record ──────────────────────────────────────────────────────────

@dataclass
class ViolationRecord:
    """
    Captures a single governance violation for later DB persistence.

    Populated entirely inside the blocked-tool proxy function (synchronous).
    The execution service reads this list after agent.invoke() returns
    and writes AgentEvent rows for each violation.

    Note: we cannot write to the DB directly inside the proxy because the
    proxy is called synchronously inside LangGraph's thread-pool execution,
    while the DB session lives on the async event loop.  The decoupled
    "record now, write later" pattern keeps the proxy simple and safe.
    """
    tool_name:  str
    input_data: dict   # the args the LLM tried to pass to the blocked tool


# ── Enforcer ──────────────────────────────────────────────────────────────────

class GovernanceEnforcer:
    """
    Produces a governed tool list from the full tool registry.

    Usage (inside execution_service.py):

        enforcer = GovernanceEnforcer(
            all_tools=ALL_TOOLS,
            allowed_tool_names=agent_orm.allowed_tools,
        )
        governed_tools = enforcer.build_governed_tools()

        # ... run agent with governed_tools ...

        violations = enforcer.violations   # list[ViolationRecord]

    The violations list is populated in real time as the agent executes.
    Read it after agent.invoke() returns to write AgentEvent rows.
    """

    def __init__(
        self,
        all_tools: list[BaseTool],
        allowed_tool_names: list[str],
    ) -> None:
        """
        Args:
            all_tools:          The complete tool registry (ALL_TOOLS).
            allowed_tool_names: Names from agent.allowed_tools, e.g.
                                ["calculator", "weather"].
        """
        self._all_tools          = all_tools
        self._allowed_tool_names = set(allowed_tool_names)

        # Accumulated in real time during agent execution.
        # Each blocked tool call appends one ViolationRecord here.
        self.violations: list[ViolationRecord] = []

    # ── Public API ────────────────────────────────────────────────────

    def is_permitted(self, tool_name: str) -> bool:
        """Return True if `tool_name` is in the agent's allowed list."""
        return tool_name in self._allowed_tool_names

    def build_governed_tools(self) -> list[BaseTool]:
        """
        Return a list of proxy tools covering every tool in ALL_TOOLS.

        Every tool is replaced with a StructuredTool proxy that shares
        the original tool's name, description, and args_schema.  The
        proxy function is either:
          • _make_permitted_proxy() — calls the real tool function
          • _make_blocked_proxy()   — records a violation and returns
                                      a denial message

        Returns:
            List of governed StructuredTool instances, same length as
            all_tools.  Safe to pass directly to build_agent().
        """
        governed: list[BaseTool] = []

        for tool in self._all_tools:
            permitted = self.is_permitted(tool.name)

            if permitted:
                proxy = self._make_permitted_proxy(tool)
                logger.debug("Tool '%s' → PERMITTED", tool.name)
            else:
                proxy = self._make_blocked_proxy(tool)
                logger.debug("Tool '%s' → BLOCKED", tool.name)

            governed.append(proxy)

        allowed_names = sorted(self._allowed_tool_names)
        all_names     = sorted(t.name for t in self._all_tools)
        logger.info(
            "Governance: allowed=%s | registry=%s",
            allowed_names, all_names,
        )
        return governed

    # ── Private proxy builders ────────────────────────────────────────

    def _make_permitted_proxy(self, original_tool: BaseTool) -> StructuredTool:
        """
        Wrap a permitted tool in a pass-through proxy.

        The proxy calls the original tool's underlying function directly
        (bypassing the LangChain BaseTool.run() overhead for simplicity).
        The ToolEventCallback in llm_service.py still fires its
        on_tool_start / on_tool_end hooks through LangChain's machinery,
        so permitted tool calls are still audited.

        Args:
            original_tool: The real BaseTool to wrap.

        Returns:
            A StructuredTool proxy with the same schema that delegates
            to the original tool's function.
        """
        # Capture the original func in the closure.
        original_func = original_tool.func  # type: ignore[attr-defined]

        def permitted_func(**kwargs) -> str:  # type: ignore[return]
            return original_func(**kwargs)

        return StructuredTool.from_function(
            func=permitted_func,
            name=original_tool.name,
            description=original_tool.description,
            args_schema=original_tool.args_schema,  # type: ignore[attr-defined]
        )

    def _make_blocked_proxy(self, original_tool: BaseTool) -> StructuredTool:
        """
        Wrap a blocked tool in a governance-enforcement proxy.

        When the LLM calls this proxy:
          1. The kwargs (attempted inputs) are captured.
          2. A ViolationRecord is appended to self.violations.
          3. A human-readable denial message is returned to the LLM
             instead of a real tool result.

        The LLM receives the denial message as a ToolMessage and
        typically relays it to the user in its final answer.

        Args:
            original_tool: The real BaseTool being blocked.

        Returns:
            A StructuredTool proxy that never executes the original tool.
        """
        tool_name  = original_tool.name
        violations = self.violations   # bind to the same list instance

        def blocked_func(**kwargs) -> str:
            """
            Governance interception function.

            This runs synchronously inside LangGraph's tool execution node.
            No async operations allowed here — accumulate the record and
            let the async execution service write it to the DB afterwards.
            """
            logger.warning(
                "GOVERNANCE VIOLATION | tool='%s' | attempted_input=%s",
                tool_name, kwargs,
            )
            violations.append(
                ViolationRecord(
                    tool_name=tool_name,
                    input_data=kwargs,
                )
            )
            return _DENIAL_MESSAGE.format(tool_name=tool_name)

        return StructuredTool.from_function(
            func=blocked_func,
            name=original_tool.name,
            description=original_tool.description,
            args_schema=original_tool.args_schema,  # type: ignore[attr-defined]
        )