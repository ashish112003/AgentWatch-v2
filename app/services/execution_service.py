# """
# app/services/execution_service.py
# ───────────────────────────────────
# AgentRun lifecycle orchestrator.

# This is the most complex service in AgentWatch Phase 3.  It owns the
# complete lifecycle of a single agent execution:

#   ┌─────────────────────────────────────────────────────────────────┐
#   │  run_agent(agent, prompt, db)                                   │
#   │                                                                 │
#   │  1. Create AgentRun record  (status=running)                    │
#   │  2. Emit  run_start event                                       │
#   │  3. Build tool list from ALL tools (Phase 4 adds permission     │
#   │     filtering here)                                             │
#   │  4. Build LangGraph agent + ToolEventCallback                   │
#   │  5. Execute agent in thread pool  (LangGraph is synchronous)    │
#   │  6. Walk callback records → emit tool_call + tool_end events    │
#   │  7. Update AgentRun.result + status=completed                   │
#   │  8. Emit run_end event                                          │
#   │  9. Return RunResponse                                          │
#   │                                                                 │
#   │  On ANY exception:                                              │
#   │  X. Update AgentRun.status=failed                               │
#   │  X. Emit run_end event with error result                        │
#   │  X. Re-raise as HTTP 500                                        │
#   └─────────────────────────────────────────────────────────────────┘

# Thread model:
#   FastAPI runs on an async event loop (uvicorn + asyncio).  LangGraph's
#   agent.invoke() is synchronous and can block for seconds during LLM
#   inference.  Calling it directly inside an async function would block
#   the entire event loop, making the server unresponsive to other requests.

#   Solution: asyncio.to_thread() runs the synchronous agent.invoke()
#   in a thread-pool worker, yielding control back to the event loop
#   while waiting.  Other requests are handled normally during inference.

#   Database writes happen in the async context (before and after the
#   thread call) so we hold no DB connection while waiting for the LLM.

# Phase 3 vs Phase 4 note:
#   Phase 3 (this file): ALL tools are passed to the agent regardless of
#   agent.allowed_tools.  We capture and log every tool call, but we do
#   not block anything.

#   Phase 4 will insert a governance layer between steps 3 and 4:
#     • Filter tools by agent.allowed_tools.
#     • Intercept tool calls at runtime and emit 'violation' events for
#       blocked calls, returning a governance error message to the LLM.
# """

# import asyncio
# import logging
# from datetime import datetime, timezone

# from fastapi import HTTPException, status
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.models.agent import Agent, AgentRun, AgentEvent
# from app.schemas.run import RunRequest, RunResponse, RunEventSchema
# from app.services.llm_service import (
#     ToolEventCallback,
#     ToolCallRecord,
#     build_agent,
# )
# from app.tools.calculator import calculator
# from app.tools.weather import weather
# from app.tools.file_reader import file_reader

# logger = logging.getLogger(__name__)

# # ── All available tools ────────────────────────────────────────────────────────
# # The canonical registry of every tool AgentWatch knows about.
# # Phase 4 will filter this list against agent.allowed_tools before
# # passing it to the agent builder.
# ALL_TOOLS = [calculator, weather, file_reader]


# # ── Event helpers ─────────────────────────────────────────────────────────────

# def _now_utc() -> datetime:
#     return datetime.now(tz=timezone.utc)


# async def _emit_event(
#     db: AsyncSession,
#     *,
#     run: AgentRun,
#     event_type: str,
#     tool_name: str | None = None,
#     input_data: dict | None = None,
#     output_data: dict | None = None,
#     permitted: bool | None = None,
#     latency_ms: float | None = None,
# ) -> AgentEvent:
#     """
#     Persist a single AgentEvent to the database.

#     This helper centralises all event creation so the main run_agent()
#     function reads as a clean narrative without repetitive ORM calls.

#     Args:
#         db:          Async DB session (must be flushed after this call).
#         run:         The parent AgentRun — provides run_id, agent_id, trace_id.
#         event_type:  One of: run_start, tool_call, tool_end, violation, run_end.
#         tool_name:   Tool involved (None for run_start / run_end).
#         input_data:  Structured input dict (tool_call events).
#         output_data: Structured output dict (tool_end events).
#         permitted:   Governance result (True=allowed, False=blocked, None=N/A).
#         latency_ms:  Tool execution time in milliseconds (tool_end events).

#     Returns:
#         The newly created AgentEvent ORM instance (already flushed).
#     """
#     event = AgentEvent(
#         run_id=run.id,
#         agent_id=run.agent_id,
#         trace_id=run.trace_id,
#         event_type=event_type,
#         tool_name=tool_name,
#         input_data=input_data,
#         output_data=output_data,
#         permitted=permitted,
#         latency_ms=latency_ms,
#     )
#     db.add(event)
#     await db.flush()      # write to DB within current transaction, get the id
#     await db.refresh(event)

#     logger.debug(
#         "Event [%s] run=%s tool=%s permitted=%s latency=%.1fms",
#         event_type,
#         run.id[:8],
#         tool_name or "-",
#         permitted,
#         latency_ms or 0,
#     )
#     return event


# # ── Agent execution (synchronous, runs in thread) ─────────────────────────────

# def _invoke_agent_sync(
#     agent,
#     prompt: str,
#     callback: ToolEventCallback,
# ) -> str:
#     """
#     Invoke the LangGraph agent synchronously.

#     This function is intentionally synchronous because LangGraph's
#     agent.invoke() is a blocking call.  It is executed via
#     asyncio.to_thread() in the async run_agent() below so it doesn't
#     block the FastAPI event loop.

#     Args:
#         agent:    Compiled LangGraph agent graph.
#         prompt:   User's natural language prompt.
#         callback: ToolEventCallback — captures all tool invocations.

#     Returns:
#         The agent's final text response.

#     Raises:
#         Exception: Any LLM, network, or tool exception propagates up
#                    to run_agent() which handles it.
#     """
#     from langchain_core.messages import HumanMessage

#     # LangGraph agents receive a dict with a 'messages' key.
#     # The config dict passes our callback so it fires on every tool call.
#     result = agent.invoke(
#         {"messages": [HumanMessage(content=prompt)]},
#         config={"callbacks": [callback]},
#     )

#     # LangGraph returns a dict with 'messages': list[BaseMessage].
#     # The final answer is the content of the last message.
#     messages = result.get("messages", [])
#     if not messages:
#         return "Agent produced no response."

#     last_message = messages[-1]

#     # Extract text content — handle both string and list-of-blocks formats.
#     content = last_message.content
#     if isinstance(content, list):
#         # Some models return [{"type": "text", "text": "..."}] blocks.
#         text_parts = [
#             block.get("text", "") if isinstance(block, dict) else str(block)
#             for block in content
#         ]
#         return " ".join(text_parts).strip() or "Agent produced no text response."
#     return str(content).strip() or "Agent produced no text response."


# # ── Main orchestrator ─────────────────────────────────────────────────────────

# async def run_agent(
#     agent_orm: Agent,
#     payload: RunRequest,
#     db: AsyncSession,
# ) -> RunResponse:
#     """
#     Orchestrate a complete agent execution run.

#     This is the single entry point called by POST /agents/run.

#     Full execution trace:
#       ① Create AgentRun  (status=running)
#       ② Emit run_start event
#       ③ Build tools + agent graph
#       ④ Execute agent in thread pool
#       ⑤ For each ToolCallRecord from callback:
#            Emit tool_call event  (before execution, permitted=True)
#            Emit tool_end event   (after execution, with output + latency)
#       ⑥ Update AgentRun.result + status=completed
#       ⑦ Emit run_end event
#       ⑧ Build and return RunResponse

#       On exception anywhere after ①:
#       ✗ Update AgentRun.status=failed
#       ✗ Emit run_end event with error payload
#       ✗ Raise HTTP 500

#     Args:
#         agent_orm: Authenticated Agent ORM instance from JWT dependency.
#         payload:   Validated RunRequest schema.
#         db:        Async SQLAlchemy session.

#     Returns:
#         RunResponse with full event trace.

#     Raises:
#         HTTPException 500: Agent execution failed.
#     """

#     # ─────────────────────────────────────────────────────────────────
#     # ① Create AgentRun record
#     # ─────────────────────────────────────────────────────────────────
#     run = AgentRun(
#         agent_id=agent_orm.id,
#         prompt=payload.prompt,
#         status="running",
#         # id and trace_id use column defaults (UUID4)
#     )
#     db.add(run)
#     await db.flush()
#     await db.refresh(run)

#     logger.info(
#         "Run started | run_id=%s agent=%s trace=%s",
#         run.id[:8], agent_orm.name, run.trace_id[:8],
#     )

#     # Collect all events for the response at the end.
#     events: list[AgentEvent] = []

#     try:
#         # ─────────────────────────────────────────────────────────────
#         # ② Emit run_start event
#         # ─────────────────────────────────────────────────────────────
#         start_event = await _emit_event(
#             db,
#             run=run,
#             event_type="run_start",
#             input_data={"prompt": payload.prompt},
#         )
#         events.append(start_event)

#         # ─────────────────────────────────────────────────────────────
#         # ③ Build tools and agent
#         #
#         # Phase 3: pass ALL tools to the agent unconditionally.
#         # Phase 4 will replace ALL_TOOLS with a filtered list based on
#         # agent_orm.allowed_tools and wrap each tool with a governance
#         # proxy that emits violation events for blocked calls.
#         # ─────────────────────────────────────────────────────────────
#         callback = ToolEventCallback()
#         agent    = build_agent(ALL_TOOLS, callback)

#         # ─────────────────────────────────────────────────────────────
#         # ④ Execute agent in thread pool
#         #
#         # asyncio.to_thread() runs the synchronous LangGraph invoke()
#         # in a ThreadPoolExecutor worker.  This yields the event loop
#         # so other HTTP requests can be served during inference.
#         # ─────────────────────────────────────────────────────────────
#         logger.info("Invoking LLM | model=%s | prompt=%.80s...", settings_model(), payload.prompt)

#         final_answer = await asyncio.to_thread(
#             _invoke_agent_sync,
#             agent,
#             payload.prompt,
#             callback,
#         )

#         # ─────────────────────────────────────────────────────────────
#         # ⑤ Walk callback records → emit tool events
#         #
#         # callback.records is an ordered list of ToolCallRecord objects,
#         # one per tool invocation.  For each record we emit two events:
#         #
#         #   tool_call  — represents the decision to call the tool.
#         #                In Phase 3, permitted=True for all calls.
#         #                In Phase 4, permitted=False triggers a violation
#         #                event instead and the tool is never executed.
#         #
#         #   tool_end   — represents the tool's response.
#         #                Contains output_data and latency_ms.
#         # ─────────────────────────────────────────────────────────────
#         for record in callback.records:
#             # tool_call — before execution
#             call_event = await _emit_event(
#                 db,
#                 run=run,
#                 event_type="tool_call",
#                 tool_name=record.tool_name,
#                 input_data=record.input_data,
#                 permitted=True,   # Phase 3: all calls permitted
#             )
#             events.append(call_event)

#             # tool_end — after execution (output + latency)
#             end_event = await _emit_event(
#                 db,
#                 run=run,
#                 event_type="tool_end",
#                 tool_name=record.tool_name,
#                 input_data=record.input_data,
#                 output_data=record.output_data,
#                 permitted=True,
#                 latency_ms=record.latency_ms,
#             )
#             events.append(end_event)

#         # ─────────────────────────────────────────────────────────────
#         # ⑥ Update AgentRun with result
#         # ─────────────────────────────────────────────────────────────
#         run.result   = final_answer
#         run.status   = "completed"
#         run.ended_at = _now_utc()
#         await db.flush()

#         logger.info(
#             "Run completed | run_id=%s | tools_used=%d | answer=%.80s...",
#             run.id[:8], len(callback.records), final_answer,
#         )

#         # ─────────────────────────────────────────────────────────────
#         # ⑦ Emit run_end event
#         # ─────────────────────────────────────────────────────────────
#         end_run_event = await _emit_event(
#             db,
#             run=run,
#             event_type="run_end",
#             output_data={"result": final_answer, "tools_used": len(callback.records)},
#         )
#         events.append(end_run_event)

#     except Exception as exc:
#         # ─────────────────────────────────────────────────────────────
#         # ✗ Failure path — mark run as failed and always emit run_end
#         # ─────────────────────────────────────────────────────────────
#         error_msg = f"{type(exc).__name__}: {exc}"
#         logger.error("Run failed | run_id=%s | error=%s", run.id[:8], error_msg, exc_info=True)

#         run.status   = "failed"
#         run.result   = error_msg
#         run.ended_at = _now_utc()
#         await db.flush()

#         # Emit a run_end event even on failure so the audit trail is
#         # complete — every run_start must have a corresponding run_end.
#         try:
#             fail_event = await _emit_event(
#                 db,
#                 run=run,
#                 event_type="run_end",
#                 output_data={"error": error_msg},
#             )
#             events.append(fail_event)
#         except Exception:
#             pass   # don't mask the original error

#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail=f"Agent execution failed: {error_msg}",
#         )

#     # ─────────────────────────────────────────────────────────────────
#     # ⑧ Build and return RunResponse
#     # ─────────────────────────────────────────────────────────────────
#     # Compute total latency from DB timestamps.
#     latency_ms: float | None = None
#     if run.ended_at and run.started_at:
#         ended = run.ended_at
#         started= run.started_at

#         # Normalize timezone mismatch
#         if ended.tzinfo is not None and started.tzinfo is None:
#             started = started.replace(tzinfo=ended.tzinfo)
#         elif started.tzinfo is not None and ended.tzinfo is None:
#             ended = ended.replace(tzinfo=started.tzinfo)
        
#         delta = ended - started
#         latency_ms = round(delta.total_seconds() * 1000, 2)

    

    

#     # Count governance violations (Phase 3: always 0, Phase 4 will have >0).
#     violation_count = sum(
#         1 for e in events if e.event_type == "violation"
#     )

#     event_schemas = [RunEventSchema.model_validate(e) for e in events]

#     return RunResponse(
#         run_id=run.id,
#         agent_id=run.agent_id,
#         trace_id=run.trace_id,
#         status=run.status,
#         prompt=run.prompt,
#         result=run.result,
#         started_at=run.started_at,
#         ended_at=run.ended_at,
#         latency_ms=latency_ms,
#         violation_count=violation_count,
#         events=event_schemas,
#     )


# def settings_model() -> str:
#     """Return current model name for logging (avoids circular import at module level)."""
#     from app.core.config import settings
#     return settings.GROQ_MODEL





# """
# app/services/execution_service.py
# ───────────────────────────────────
# AgentRun lifecycle orchestrator — Phase 4: Governance Enforcement.

# Changes from Phase 3:
#   ① GovernanceEnforcer replaces the bare ALL_TOOLS pass-through.
#   ② Violation events are written between tool_call and tool_end events.
#   ③ _emit_events_for_records() handles the new three-way classification:
#        permitted tool call   → tool_call (permitted=True)  + tool_end
#        blocked tool call     → tool_call (permitted=False) + violation
#      (no tool_end for blocked calls — the real tool never ran)
#   ④ Module docstring updated to reflect Phase 4 flow.

# Complete execution trace (Phase 4):
#   ┌─────────────────────────────────────────────────────────────────┐
#   │  run_agent(agent_orm, payload, db)                              │
#   │                                                                 │
#   │  ① Create AgentRun  (status=running)                           │
#   │  ② Emit run_start event                                         │
#   │  ③ GovernanceEnforcer.build_governed_tools()                    │
#   │       Every tool in ALL_TOOLS gets a proxy:                     │
#   │         allowed  → PermittedProxy  (calls real func)            │
#   │         blocked  → BlockedProxy    (records violation, denies)  │
#   │  ④ Build LangGraph agent with governed tools + callback         │
#   │  ⑤ Execute agent in asyncio.to_thread()                         │
#   │  ⑥ Walk callback.records + enforcer.violations:                 │
#   │       For each ToolCallRecord (real tool call):                 │
#   │         Emit tool_call  (permitted=True)                        │
#   │         Emit tool_end   (with output + latency)                 │
#   │       For each ViolationRecord (blocked call):                  │
#   │         Emit tool_call  (permitted=False)                       │
#   │         Emit violation  (tool_name, attempted input)            │
#   │  ⑦ Update AgentRun.result + status=completed                   │
#   │  ⑧ Emit run_end event                                           │
#   │  ⑨ Return RunResponse with violation_count > 0 if applicable   │
#   └─────────────────────────────────────────────────────────────────┘

# Thread model (unchanged from Phase 3):
#   LangGraph's agent.invoke() is synchronous.  We run it in
#   asyncio.to_thread() to avoid blocking the FastAPI event loop.
#   DB writes happen in the async context — never inside the thread.
# """

# import asyncio
# import logging
# from datetime import datetime, timezone

# from fastapi import HTTPException, status
# from sqlalchemy.ext.asyncio import AsyncSession

# from app.models.agent import Agent, AgentRun, AgentEvent
# from app.schemas.run import RunRequest, RunResponse, RunEventSchema
# from app.services.llm_service import ToolEventCallback, build_agent
# from app.governance.enforcer import GovernanceEnforcer, ViolationRecord
# # Imports (replacing the 3-line block):
# from app.tools.calculator         import calculator
# from app.tools.weather            import weather
# from app.tools.file_reader        import file_reader
# from app.tools.datetime_tool      import datetime_tool
# from app.tools.currency_converter import currency_converter
# from app.tools.wikipedia_search   import wikipedia_search
# from app.tools.text_summarizer    import text_summarizer
# from app.tools.word_counter       import word_counter
# from app.tools.json_formatter     import json_formatter
# from app.tools.uuid_generator     import uuid_generator

# logger = logging.getLogger(__name__)

# # ── Tool registry ──────────────────────────────────────────────────────────────
# # The single source of truth for every tool AgentWatch knows about.
# # GovernanceEnforcer decides which subset each agent may actually USE.
# ALL_TOOLS = [calculator, weather, file_reader, datetime_tool, currency_converter, wikipedia_search, text_summarizer, uuid_generator, json_formatter, word_counter]


# # ── Timestamp helper ──────────────────────────────────────────────────────────

# def _now_utc() -> datetime:
#     # SQLite returns naive datetimes; use naive UTC to avoid subtraction errors.
#     return datetime.utcnow()


# # ── Event persistence helper ──────────────────────────────────────────────────

# async def _emit_event(
#     db: AsyncSession,
#     *,
#     run: AgentRun,
#     event_type: str,
#     tool_name: str | None = None,
#     input_data: dict | None = None,
#     output_data: dict | None = None,
#     permitted: bool | None = None,
#     latency_ms: float | None = None,
# ) -> AgentEvent:
#     """
#     Persist a single AgentEvent to the database within the current transaction.

#     Args:
#         db:          Async SQLAlchemy session — flushed but not committed here.
#         run:         Parent AgentRun — provides run_id, agent_id, trace_id.
#         event_type:  run_start | tool_call | tool_end | violation | run_end
#         tool_name:   Tool name (None for run_start / run_end).
#         input_data:  Tool input arguments dict.
#         output_data: Tool output dict.
#         permitted:   True = allowed, False = blocked, None = not applicable.
#         latency_ms:  Milliseconds taken by the tool (tool_end only).

#     Returns:
#         Flushed AgentEvent ORM instance with populated id and timestamp.
#     """
#     event = AgentEvent(
#         run_id=run.id,
#         agent_id=run.agent_id,
#         trace_id=run.trace_id,
#         event_type=event_type,
#         tool_name=tool_name,
#         input_data=input_data,
#         output_data=output_data,
#         permitted=permitted,
#         latency_ms=latency_ms,
#     )
#     db.add(event)
#     await db.flush()
#     await db.refresh(event)

#     logger.debug(
#         "Event [%s] run=%s tool=%s permitted=%s latency=%s",
#         event_type, run.id[:8], tool_name or "—",
#         permitted, f"{latency_ms:.1f}ms" if latency_ms else "—",
#     )
#     return event


# # ── Event emission for tool call records ──────────────────────────────────────

# async def _emit_events_for_records(
#     db: AsyncSession,
#     run: AgentRun,
#     callback: ToolEventCallback,
#     enforcer: GovernanceEnforcer,
#     events: list[AgentEvent],
# ) -> None:
#     """
#     Write AgentEvent rows for every tool call that occurred during the run.

#     This function handles two distinct cases:

#     Case A — Permitted tool call (real execution happened):
#       The ToolCallRecord comes from the ToolEventCallback which fires
#       on_tool_start / on_tool_end hooks for every tool that actually ran.
#       These are tools whose names appear in agent.allowed_tools.

#       Events emitted:
#         1. tool_call  (permitted=True, input captured)
#         2. tool_end   (permitted=True, output + latency captured)

#     Case B — Blocked tool call (governance violation):
#       The ViolationRecord comes from the GovernanceEnforcer's blocked
#       proxy function, which fires whenever the LLM tries to call a tool
#       that is NOT in agent.allowed_tools.  The real tool never ran.

#       Events emitted:
#         1. tool_call  (permitted=False, attempted input captured)
#         2. violation  (permitted=False, same input, denial message as output)

#     Ordering strategy:
#       LangChain callbacks fire sequentially.  The ToolEventCallback records
#       are in chronological order.  Violations are also appended in order.
#       We interleave them by merging into a single timeline sorted by the
#       order they were appended (which matches execution order within a run).

#     Args:
#         db:       Async DB session.
#         run:      Parent AgentRun.
#         callback: Populated after agent.invoke() returns.
#         enforcer: Populated after agent.invoke() returns.
#         events:   Mutable list — all new events are appended here.
#     """
#     # Build set of blocked tool names so we emit them only once (from Case B).
#     # Blocked tools appear in callback.records (proxy func fires hooks) AND
#     # in enforcer.violations — we must NOT double-emit them with permitted=True.
#     violated_names: set[str] = {v.tool_name for v in enforcer.violations}

#     # ── Case A: permitted tool calls ──────────────────────────────────
#     for record in callback.records:
#         # Skip records for blocked tools — handled in Case B with permitted=False.
#         if record.tool_name in violated_names:
#             continue

#         # tool_call — the LLM's decision to call the tool
#         call_evt = await _emit_event(
#             db,
#             run=run,
#             event_type="tool_call",
#             tool_name=record.tool_name,
#             input_data=record.input_data,
#             permitted=True,
#         )
#         events.append(call_evt)

#         # tool_end — the tool's actual output and wall-clock latency
#         end_evt = await _emit_event(
#             db,
#             run=run,
#             event_type="tool_end",
#             tool_name=record.tool_name,
#             input_data=record.input_data,
#             output_data=record.output_data,
#             permitted=True,
#             latency_ms=record.latency_ms,
#         )
#         events.append(end_evt)

#     # ── Case B: blocked tool calls (violations) ───────────────────────
#     for violation in enforcer.violations:
#         # tool_call with permitted=False — the LLM's blocked attempt
#         # We still record the attempt so we know WHICH tool was requested
#         # and WHAT input the LLM tried to pass.
#         blocked_call_evt = await _emit_event(
#             db,
#             run=run,
#             event_type="tool_call",
#             tool_name=violation.tool_name,
#             input_data=violation.input_data,
#             permitted=False,   # ← key governance signal
#         )
#         events.append(blocked_call_evt)

#         # violation — a dedicated event type for governance dashboards
#         # Makes it trivial to query: SELECT * FROM agent_events WHERE event_type='violation'
#         denial_message = (
#             f"Access denied: tool '{violation.tool_name}' is not permitted "
#             f"for agent '{run.agent_id}'."
#         )
#         violation_evt = await _emit_event(
#             db,
#             run=run,
#             event_type="violation",
#             tool_name=violation.tool_name,
#             input_data=violation.input_data,
#             output_data={"denial_message": denial_message},
#             permitted=False,
#         )
#         events.append(violation_evt)

#         logger.warning(
#             "VIOLATION LOGGED | run=%s agent=%s tool='%s' input=%s",
#             run.id[:8], run.agent_id[:8],
#             violation.tool_name, violation.input_data,
#         )


# # ── Synchronous agent invocation (runs in thread pool) ────────────────────────

# def _invoke_agent_sync(
#     agent,
#     prompt: str,
#     callback: ToolEventCallback,
# ) -> str:
#     """
#     Call the compiled LangGraph agent graph synchronously.

#     Must be synchronous because LangGraph's invoke() is blocking.
#     Called via asyncio.to_thread() so the event loop stays free.

#     Args:
#         agent:    Compiled LangGraph agent (from build_agent()).
#         prompt:   Natural language prompt.
#         callback: ToolEventCallback — populated during invocation.

#     Returns:
#         Final text answer from the agent.
#     """
#     from langchain_core.messages import HumanMessage

#     result = agent.invoke(
#         {"messages": [HumanMessage(content=prompt)]},
#         config={"callbacks": [callback]},
#     )

#     messages = result.get("messages", [])
#     if not messages:
#         return "Agent produced no response."

#     content = messages[-1].content

#     # Handle both plain string and list-of-content-block formats.
#     if isinstance(content, list):
#         parts = [
#             block.get("text", "") if isinstance(block, dict) else str(block)
#             for block in content
#         ]
#         return " ".join(parts).strip() or "Agent produced no text response."

#     return str(content).strip() or "Agent produced no text response."


# # ── Main orchestrator ─────────────────────────────────────────────────────────

# async def run_agent(
#     agent_orm: Agent,
#     payload: RunRequest,
#     db: AsyncSession,
# ) -> RunResponse:
#     """
#     Orchestrate a complete governed agent execution run.

#     Entry point called by POST /agents/run.

#     Args:
#         agent_orm: Authenticated Agent ORM instance (has .allowed_tools).
#         payload:   Validated RunRequest (has .prompt).
#         db:        Async SQLAlchemy session.

#     Returns:
#         RunResponse with full event trace and violation_count.

#     Raises:
#         HTTPException 500: Unrecoverable execution error.
#     """

#     # ─── ① Create AgentRun ────────────────────────────────────────────
#     run = AgentRun(
#         agent_id=agent_orm.id,
#         prompt=payload.prompt,
#         status="running",
#     )
#     db.add(run)
#     await db.flush()
#     await db.refresh(run)

#     logger.info(
#         "Run started | run_id=%s agent='%s' allowed_tools=%s trace=%s",
#         run.id[:8], agent_orm.name,
#         agent_orm.allowed_tools, run.trace_id[:8],
#     )

#     events: list[AgentEvent] = []

#     try:
#         # ─── ② run_start event ────────────────────────────────────────
#         start_evt = await _emit_event(
#             db, run=run,
#             event_type="run_start",
#             input_data={
#                 "prompt": payload.prompt,
#                 "allowed_tools": agent_orm.allowed_tools,
#             },
#         )
#         events.append(start_evt)

#         # ─── ③ Build governance layer ─────────────────────────────────
#         #
#         # GovernanceEnforcer wraps every tool in ALL_TOOLS with either:
#         #   • a PermittedProxy  (allowed tools — calls real func)
#         #   • a BlockedProxy    (blocked tools — records violation, denies)
#         #
#         # The governed_tools list is passed to the agent instead of
#         # ALL_TOOLS.  The LLM sees the same tool schemas for ALL tools
#         # (both permitted and blocked), so it can still reason about
#         # them — it just gets a denial message when it calls a blocked one.
#         enforcer       = GovernanceEnforcer(
#             all_tools=ALL_TOOLS,
#             allowed_tool_names=agent_orm.allowed_tools,
#         )
#         governed_tools = enforcer.build_governed_tools()

#         logger.info(
#             "Governance armed | permitted=%s | blocked=%s",
#             [t for t in agent_orm.allowed_tools],
#             [t.name for t in governed_tools if t.name not in set(agent_orm.allowed_tools)],
#         )

#         # ─── ④ Build agent with governed tools ────────────────────────
#         callback = ToolEventCallback()
#         agent    = build_agent(governed_tools, callback)

#         # ─── ⑤ Execute agent in thread pool ──────────────────────────
#         #
#         # asyncio.to_thread() prevents the blocking LangGraph call from
#         # freezing the event loop.  enforcer.violations is populated
#         # inside the thread (inside blocked_func closures), which is
#         # safe because append() on a list is GIL-protected in CPython.
#         from app.core.config import settings
#         logger.info(
#             "Invoking LLM | model=%s | prompt=%.100s",
#             settings.GROQ_MODEL, payload.prompt,
#         )

#         final_answer = await asyncio.to_thread(
#             _invoke_agent_sync, agent, payload.prompt, callback,
#         )

#         # ─── ⑥ Persist all tool events + violations ───────────────────
#         await _emit_events_for_records(db, run, callback, enforcer, events)

#         # ─── ⑦ Update AgentRun ────────────────────────────────────────
#         run.result   = final_answer
#         run.status   = "completed"
#         run.ended_at = _now_utc()
#         await db.flush()

#         n_permitted  = len(callback.records)
#         n_violations = len(enforcer.violations)
#         logger.info(
#             "Run completed | run_id=%s | permitted_calls=%d | violations=%d | answer=%.80s",
#             run.id[:8], n_permitted, n_violations, final_answer,
#         )

#         # ─── ⑧ run_end event ──────────────────────────────────────────
#         end_evt = await _emit_event(
#             db, run=run,
#             event_type="run_end",
#             output_data={
#                 "result":            final_answer,
#                 "tools_used":        n_permitted,
#                 "violations_caught": n_violations,
#             },
#         )
#         events.append(end_evt)

#     except Exception as exc:
#         # ─── ✗ Failure path ────────────────────────────────────────────
#         error_msg = f"{type(exc).__name__}: {exc}"
#         logger.error(
#             "Run failed | run_id=%s | %s", run.id[:8], error_msg, exc_info=True,
#         )

#         run.status   = "failed"
#         run.result   = error_msg
#         run.ended_at = _now_utc()
#         await db.flush()

#         try:
#             fail_evt = await _emit_event(
#                 db, run=run,
#                 event_type="run_end",
#                 output_data={"error": error_msg},
#             )
#             events.append(fail_evt)
#         except Exception:
#             pass

#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Agent execution failed",
#         )

#     # ─── ⑨ Build RunResponse ──────────────────────────────────────────
#     latency_ms: float | None = None
#     if run.ended_at and run.started_at:
#         latency_ms = round(
#             (run.ended_at - run.started_at).total_seconds() * 1000, 2
#         )

#     violation_count = sum(1 for e in events if e.event_type == "violation")

#     return RunResponse(
#         run_id=run.id,
#         agent_id=run.agent_id,
#         trace_id=run.trace_id,
#         status=run.status,
#         prompt=run.prompt,
#         result=run.result,
#         started_at=run.started_at,
#         ended_at=run.ended_at,
#         latency_ms=latency_ms,
#         violation_count=violation_count,
#         events=[RunEventSchema.model_validate(e) for e in events],
#     )







"""
app/services/execution_service.py
───────────────────────────────────
AgentRun lifecycle orchestrator — Phase 4: Governance Enforcement.

Changes from Phase 3:
  ① GovernanceEnforcer replaces the bare ALL_TOOLS pass-through.
  ② Violation events are written between tool_call and tool_end events.
  ③ _emit_events_for_records() handles the new three-way classification:
       permitted tool call   → tool_call (permitted=True)  + tool_end
       blocked tool call     → tool_call (permitted=False) + violation
     (no tool_end for blocked calls — the real tool never ran)
  ④ Module docstring updated to reflect Phase 4 flow.

Complete execution trace (Phase 4):
  ┌─────────────────────────────────────────────────────────────────┐
  │  run_agent(agent_orm, payload, db)                              │
  │                                                                 │
  │  ① Create AgentRun  (status=running)                           │
  │  ② Emit run_start event                                         │
  │  ③ GovernanceEnforcer.build_governed_tools()                    │
  │       Every tool in ALL_TOOLS gets a proxy:                     │
  │         allowed  → PermittedProxy  (calls real func)            │
  │         blocked  → BlockedProxy    (records violation, denies)  │
  │  ④ Build LangGraph agent with governed tools + callback         │
  │  ⑤ Execute agent in asyncio.to_thread()                         │
  │  ⑥ Walk callback.records + enforcer.violations:                 │
  │       For each ToolCallRecord (real tool call):                 │
  │         Emit tool_call  (permitted=True)                        │
  │         Emit tool_end   (with output + latency)                 │
  │       For each ViolationRecord (blocked call):                  │
  │         Emit tool_call  (permitted=False)                       │
  │         Emit violation  (tool_name, attempted input)            │
  │  ⑦ Update AgentRun.result + status=completed                   │
  │  ⑧ Emit run_end event                                           │
  │  ⑨ Return RunResponse with violation_count > 0 if applicable   │
  └─────────────────────────────────────────────────────────────────┘

Thread model (unchanged from Phase 3):
  LangGraph's agent.invoke() is synchronous.  We run it in
  asyncio.to_thread() to avoid blocking the FastAPI event loop.
  DB writes happen in the async context — never inside the thread.
"""

import asyncio
import logging
from datetime import datetime

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent import Agent, AgentRun, AgentEvent
from app.schemas.run import RunRequest, RunResponse, RunEventSchema
from app.services.llm_service import ToolEventCallback, build_agent
from app.governance.enforcer import GovernanceEnforcer
from app.services.policy_service import evaluate_policies_for_agent
from app.core.config import settings
from app.tools.calculator         import calculator
from app.tools.weather            import weather
from app.tools.file_reader        import file_reader
from app.tools.datetime_tool      import datetime_tool
from app.tools.currency_converter import currency_converter
from app.tools.wikipedia_search   import wikipedia_search
from app.tools.text_summarizer    import text_summarizer
from app.tools.word_counter       import word_counter
from app.tools.json_formatter     import json_formatter
from app.tools.uuid_generator     import uuid_generator

logger = logging.getLogger(__name__)

# ── Tool registry ──────────────────────────────────────────────────────────────
# The single source of truth for every tool AgentWatch knows about.
# GovernanceEnforcer decides which subset each agent may actually USE.
ALL_TOOLS = [
    calculator,
    weather,
    file_reader,
    datetime_tool,
    currency_converter,
    wikipedia_search,
    text_summarizer,
    word_counter,
    json_formatter,
    uuid_generator,
]


# ── Timestamp helper ──────────────────────────────────────────────────────────

def _now_utc() -> datetime:
    # Return a timezone-naive UTC datetime.
    # SQLite stores and returns naive datetimes via SQLAlchemy; using a naive
    # value here avoids "can't subtract offset-naive and offset-aware datetimes"
    # when computing latency_ms.  The stdlib replacement for the deprecated
    # datetime.utcnow() that still produces a naive datetime is:
    from datetime import timezone
    return datetime.now(tz=timezone.utc).replace(tzinfo=None)


# ── Event persistence helper ──────────────────────────────────────────────────

async def _emit_event(
    db: AsyncSession,
    *,
    run: AgentRun,
    event_type: str,
    tool_name: str | None = None,
    input_data: dict | None = None,
    output_data: dict | None = None,
    permitted: bool | None = None,
    latency_ms: float | None = None,
) -> AgentEvent:
    """
    Persist a single AgentEvent to the database within the current transaction.

    Args:
        db:          Async SQLAlchemy session — flushed but not committed here.
        run:         Parent AgentRun — provides run_id, agent_id, trace_id.
        event_type:  run_start | tool_call | tool_end | violation | run_end
        tool_name:   Tool name (None for run_start / run_end).
        input_data:  Tool input arguments dict.
        output_data: Tool output dict.
        permitted:   True = allowed, False = blocked, None = not applicable.
        latency_ms:  Milliseconds taken by the tool (tool_end only).

    Returns:
        Flushed AgentEvent ORM instance with populated id and timestamp.
    """
    event = AgentEvent(
        run_id=run.id,
        agent_id=run.agent_id,
        trace_id=run.trace_id,
        event_type=event_type,
        tool_name=tool_name,
        input_data=input_data,
        output_data=output_data,
        permitted=permitted,
        latency_ms=latency_ms,
    )
    db.add(event)
    await db.flush()
    await db.refresh(event)

    logger.debug(
        "Event [%s] run=%s tool=%s permitted=%s latency=%s",
        event_type, run.id[:8], tool_name or "—",
        permitted, f"{latency_ms:.1f}ms" if latency_ms else "—",
    )
    return event


# ── Event emission for tool call records ──────────────────────────────────────

async def _emit_events_for_records(
    db: AsyncSession,
    run: AgentRun,
    callback: ToolEventCallback,
    enforcer: GovernanceEnforcer,
    events: list[AgentEvent],
) -> None:
    """
    Write AgentEvent rows for every tool call that occurred during the run.

    This function handles two distinct cases:

    Case A — Permitted tool call (real execution happened):
      The ToolCallRecord comes from the ToolEventCallback which fires
      on_tool_start / on_tool_end hooks for every tool that actually ran.
      These are tools whose names appear in agent.allowed_tools.

      Events emitted:
        1. tool_call  (permitted=True, input captured)
        2. tool_end   (permitted=True, output + latency captured)

    Case B — Blocked tool call (governance violation):
      The ViolationRecord comes from the GovernanceEnforcer's blocked
      proxy function, which fires whenever the LLM tries to call a tool
      that is NOT in agent.allowed_tools.  The real tool never ran.

      Events emitted:
        1. tool_call  (permitted=False, attempted input captured)
        2. violation  (permitted=False, same input, denial message as output)

    Ordering strategy:
      LangChain callbacks fire sequentially.  The ToolEventCallback records
      are in chronological order.  Violations are also appended in order.
      We interleave them by merging into a single timeline sorted by the
      order they were appended (which matches execution order within a run).

    Args:
        db:       Async DB session.
        run:      Parent AgentRun.
        callback: Populated after agent.invoke() returns.
        enforcer: Populated after agent.invoke() returns.
        events:   Mutable list — all new events are appended here.
    """
    # Build set of blocked tool names so we emit them only once (from Case B).
    # Blocked tools appear in callback.records (proxy func fires hooks) AND
    # in enforcer.violations — we must NOT double-emit them with permitted=True.
    violated_names: set[str] = {v.tool_name for v in enforcer.violations}

    # ── Case A: permitted tool calls ──────────────────────────────────
    for record in callback.records:
        # Skip records for blocked tools — handled in Case B with permitted=False.
        if record.tool_name in violated_names:
            continue

        # tool_call — the LLM's decision to call the tool
        call_evt = await _emit_event(
            db,
            run=run,
            event_type="tool_call",
            tool_name=record.tool_name,
            input_data=record.input_data,
            permitted=True,
        )
        events.append(call_evt)

        # tool_end — the tool's actual output and wall-clock latency
        end_evt = await _emit_event(
            db,
            run=run,
            event_type="tool_end",
            tool_name=record.tool_name,
            input_data=record.input_data,
            output_data=record.output_data,
            permitted=True,
            latency_ms=record.latency_ms,
        )
        events.append(end_evt)

    # ── Case B: blocked tool calls (violations) ───────────────────────
    for violation in enforcer.violations:
        # tool_call with permitted=False — the LLM's blocked attempt
        # We still record the attempt so we know WHICH tool was requested
        # and WHAT input the LLM tried to pass.
        blocked_call_evt = await _emit_event(
            db,
            run=run,
            event_type="tool_call",
            tool_name=violation.tool_name,
            input_data=violation.input_data,
            permitted=False,   # ← key governance signal
        )
        events.append(blocked_call_evt)

        # violation — a dedicated event type for governance dashboards
        # Makes it trivial to query: SELECT * FROM agent_events WHERE event_type='violation'
        denial_message = (
            f"Access denied: tool '{violation.tool_name}' is not permitted "
            f"for agent '{run.agent_id}'."
        )
        violation_evt = await _emit_event(
            db,
            run=run,
            event_type="violation",
            tool_name=violation.tool_name,
            input_data=violation.input_data,
            output_data={"denial_message": denial_message},
            permitted=False,
        )
        events.append(violation_evt)

        logger.warning(
            "VIOLATION LOGGED | run=%s agent=%s tool='%s' input=%s",
            run.id[:8], run.agent_id[:8],
            violation.tool_name, violation.input_data,
        )


# ── Synchronous agent invocation (runs in thread pool) ────────────────────────

def _invoke_agent_sync(
    agent,
    prompt: str,
    callback: ToolEventCallback,
) -> str:
    """
    Call the compiled LangGraph agent graph synchronously.

    Must be synchronous because LangGraph's invoke() is blocking.
    Called via asyncio.to_thread() so the event loop stays free.

    Args:
        agent:    Compiled LangGraph agent (from build_agent()).
        prompt:   Natural language prompt.
        callback: ToolEventCallback — populated during invocation.

    Returns:
        Final text answer from the agent.
    """
    from langchain_core.messages import HumanMessage

    result = agent.invoke(
        {"messages": [HumanMessage(content=prompt)]},
        config={"callbacks": [callback]},
    )

    messages = result.get("messages", [])
    if not messages:
        return "Agent produced no response."

    content = messages[-1].content

    # Handle both plain string and list-of-content-block formats.
    if isinstance(content, list):
        parts = [
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in content
        ]
        return " ".join(parts).strip() or "Agent produced no text response."

    return str(content).strip() or "Agent produced no text response."


# ── Main orchestrator ─────────────────────────────────────────────────────────

async def run_agent(
    agent_orm: Agent,
    payload: RunRequest,
    db: AsyncSession,
) -> RunResponse:
    """
    Orchestrate a complete governed agent execution run.

    Entry point called by POST /agents/run.

    Args:
        agent_orm: Authenticated Agent ORM instance (has .allowed_tools).
        payload:   Validated RunRequest (has .prompt).
        db:        Async SQLAlchemy session.

    Returns:
        RunResponse with full event trace and violation_count.

    Raises:
        HTTPException 500: Unrecoverable execution error.
    """

    # ─── ① Create AgentRun ────────────────────────────────────────────
    run = AgentRun(
        agent_id=agent_orm.id,
        prompt=payload.prompt,
        status="running",
    )
    db.add(run)
    await db.flush()
    await db.refresh(run)

    logger.info(
        "Run started | run_id=%s agent='%s' allowed_tools=%s trace=%s",
        run.id[:8], agent_orm.name,
        agent_orm.allowed_tools, run.trace_id[:8],
    )

    events: list[AgentEvent] = []

    try:
        # ─── ② run_start event ────────────────────────────────────────
        start_evt = await _emit_event(
            db, run=run,
            event_type="run_start",
            input_data={
                "prompt": payload.prompt,
                "allowed_tools": agent_orm.allowed_tools,
            },
        )
        events.append(start_evt)

        # ─── ③ Policy evaluation (Phase 3) ───────────────────────────
        #
        # Evaluate named policies attached to this agent BEFORE building
        # the GovernanceEnforcer.  Policies can:
        #   • Block the entire run (prompt_guard, time_window).
        #   • Add tool-level blocks (tool_deny) merged into allowed_tools.
        #   • Explicitly permit tools (tool_allow).
        #   • Set a cap on total tool calls (rate_limit).
        policy_result = await evaluate_policies_for_agent(
            db, agent_orm.id, payload.prompt
        )

        if policy_result.run_blocked and policy_result.violation:
            v = policy_result.violation
            # Emit policy_violation audit event and abort before LLM call.
            policy_block_evt = await _emit_event(
                db, run=run,
                event_type="policy_violation",
                input_data={
                    "policy_id":   v.policy_id,
                    "policy_name": v.policy_name,
                    "rule_type":   v.rule_type,
                    "severity":    v.severity,
                },
                output_data={"reason": v.reason},
                permitted=False,
            )
            events.append(policy_block_evt)
            run.status   = "failed"
            run.result   = v.reason
            run.ended_at = _now_utc()
            await db.flush()
            end_evt = await _emit_event(
                db, run=run,
                event_type="run_end",
                output_data={"error": v.reason, "blocked_by_policy": v.policy_name},
            )
            events.append(end_evt)
            violation_count = 1
            return RunResponse(
                run_id=run.id,
                agent_id=run.agent_id,
                trace_id=run.trace_id,
                status="failed",
                prompt=run.prompt,
                result=v.reason,
                started_at=run.started_at,
                ended_at=run.ended_at,
                latency_ms=None,
                violation_count=violation_count,
                events=[RunEventSchema.model_validate(e) for e in events],
            )

        # ─── ③b Build governance layer ────────────────────────────────
        #
        # Merge policy-level tool_deny blocks into the allowed_tools list:
        #   effective_allowed = agent.allowed_tools - policy_blocked_tools
        # tool_allow policies are NOT used here — they represent explicit
        # permits documented in policy but allowed_tools is the source of
        # truth for the GovernanceEnforcer's permitted set.
        effective_allowed = [
            t for t in agent_orm.allowed_tools
            if t not in policy_result.blocked_tools
        ]

        enforcer = GovernanceEnforcer(
            all_tools=ALL_TOOLS,
            allowed_tool_names=effective_allowed,
        )
        governed_tools = enforcer.build_governed_tools()

        logger.info(
            "Governance armed | permitted=%s | policy_blocked=%s",
            effective_allowed,
            list(policy_result.blocked_tools),
        )

        # ─── ④ Build agent with governed tools ────────────────────────
        callback = ToolEventCallback()
        agent    = build_agent(governed_tools, callback)

        # ─── ⑤ Execute agent in thread pool ──────────────────────────
        #
        # asyncio.to_thread() prevents the blocking LangGraph call from
        # freezing the event loop.  enforcer.violations is populated
        # inside the thread (inside blocked_func closures), which is
        # safe because append() on a list is GIL-protected in CPython.
        logger.info(
            "Invoking LLM | model=%s | prompt=%.100s",
            settings.GROQ_MODEL, payload.prompt,
        )

        final_answer = await asyncio.to_thread(
            _invoke_agent_sync, agent, payload.prompt, callback,
        )

        # ─── ⑥ Persist all tool events + violations ───────────────────
        await _emit_events_for_records(db, run, callback, enforcer, events)

        # ─── ⑥b Rate limit enforcement (policy) ──────────────────────
        #
        # If a rate_limit policy is active and the agent made more tool
        # calls than allowed, emit policy_violation events for each call
        # over the limit.  The calls already executed (post-hoc enforcement).
        if policy_result.rate_limit is not None:
            actual_calls = len(callback.records)
            if actual_calls > policy_result.rate_limit:
                # Find which policies set the limit
                limiting_policies = [
                    p for p in policy_result.active_policies
                    if p.rule_type == "rate_limit"
                    and (p.rule_config or {}).get("max_calls_per_run") == policy_result.rate_limit
                ]
                p_name = limiting_policies[0].name if limiting_policies else "rate-limit-policy"
                p_id   = limiting_policies[0].id   if limiting_policies else ""
                reason = (
                    f"Policy '{p_name}' rate limit exceeded: "
                    f"{actual_calls} tool calls made, maximum is {policy_result.rate_limit}."
                )
                rate_evt = await _emit_event(
                    db, run=run,
                    event_type="policy_violation",
                    input_data={
                        "policy_id":       p_id,
                        "policy_name":     p_name,
                        "rule_type":       "rate_limit",
                        "severity":        limiting_policies[0].severity if limiting_policies else "MEDIUM",
                        "actual_calls":    actual_calls,
                        "max_calls":       policy_result.rate_limit,
                    },
                    output_data={"reason": reason},
                    permitted=False,
                )
                events.append(rate_evt)
                logger.warning(
                    "RATE LIMIT EXCEEDED | run=%s calls=%d max=%d",
                    run.id[:8], actual_calls, policy_result.rate_limit,
                )

        # ─── ⑦ Update AgentRun ────────────────────────────────────────
        run.result   = final_answer
        run.status   = "completed"
        run.ended_at = _now_utc()
        await db.flush()

        n_permitted  = len(callback.records)
        n_violations = len(enforcer.violations)
        logger.info(
            "Run completed | run_id=%s | permitted_calls=%d | violations=%d | answer=%.80s",
            run.id[:8], n_permitted, n_violations, final_answer,
        )

        # ─── ⑧ run_end event ──────────────────────────────────────────
        end_evt = await _emit_event(
            db, run=run,
            event_type="run_end",
            output_data={
                "result":            final_answer,
                "tools_used":        n_permitted,
                "violations_caught": n_violations,
            },
        )
        events.append(end_evt)

    except Exception as exc:
        # ─── ✗ Failure path ────────────────────────────────────────────
        error_msg = f"{type(exc).__name__}: {exc}"
        logger.error(
            "Run failed | run_id=%s | %s", run.id[:8], error_msg, exc_info=True,
        )

        run.status   = "failed"
        run.result   = error_msg
        run.ended_at = _now_utc()
        await db.flush()

        try:
            fail_evt = await _emit_event(
                db, run=run,
                event_type="run_end",
                output_data={"error": error_msg},
            )
            events.append(fail_evt)
        except Exception:
            pass

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            # Do not return internal error text to clients — log it instead.
            detail="Agent execution failed. Check server logs for details.",
        )

    # ─── ⑨ Build RunResponse ──────────────────────────────────────────
    latency_ms: float | None = None
    if run.ended_at and run.started_at:
        latency_ms = round(
            (run.ended_at - run.started_at).total_seconds() * 1000, 2
        )

    violation_count = sum(
        1 for e in events
        if e.event_type in ("violation", "policy_violation")
    )

    return RunResponse(
        run_id=run.id,
        agent_id=run.agent_id,
        trace_id=run.trace_id,
        status=run.status,
        prompt=run.prompt,
        result=run.result,
        started_at=run.started_at,
        ended_at=run.ended_at,
        latency_ms=latency_ms,
        violation_count=violation_count,
        events=[RunEventSchema.model_validate(e) for e in events],
    )