"""
Autonomous AI Agent Loop
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Reference implementation of the 8-step autonomous agent loop using
Amazon Bedrock's converse API with native tool-use (function calling).

The loop traces the exact enterprise scenario from the architecture doc:
  User: "Our recent deployment is throwing authentication exceptions. Fix it."

How the loop works with Bedrock converse API:
  ┌──────────────────────────────────────────────────────────────────────┐
  │  1. Send messages + toolConfig to Bedrock                            │
  │  2. Model returns stopReason="tool_use" → extract toolUse blocks     │
  │  3. Execute each tool locally (AgentCore does this in production)    │
  │  4. Append toolResult blocks → send back to Bedrock                  │
  │  5. Model reasons over results → may issue more tool calls           │
  │  6. Repeat until stopReason="end_turn" (model has its final answer)  │
  └──────────────────────────────────────────────────────────────────────┘

Architecture alignment:
  Orchestration Framework  →  This file's AgentLoop class
  Harness (AgentCore)      →  The _execute_tool_calls() dispatch layer
  Short-term memory        →  session_memory.SessionMemory.short_term
  Long-term memory         →  session_memory.SessionMemory.long_term (flush)
  Tool integrations        →  tools/ package (MCP Postgres, S3, SNS)
"""
from __future__ import annotations

import json
import logging
import textwrap
import time
from typing import Any

import boto3

from config import (
    AWS_PROFILE,
    AWS_REGION,
    MAX_ITERATIONS,
    MODEL_ID,
    TOKEN_FLUSH_BUDGET,
)
from memory import SessionMemory
from tools  import TOOL_CONFIG, execute_tool

logger = logging.getLogger(__name__)

# ── Step banners ──────────────────────────────────────────────────────────────
_STEPS = {
    1: ("Chat History Ingestion",            "📥"),
    2: ("Reasoning — Initial Reflection",    "🧠"),
    3: ("Planning — Task Tree Generation",   "📋"),
    4: ("Tool Execution (MCP Postgres)",     "🔌"),
    5: ("Observation & Self-Reflection",     "🔍"),
    6: ("Dynamic Plan Update & Tool Call",   "🔄"),
    7: ("Secondary Tool Execution (S3)",     "☁️ "),
    8: ("Token Reduction & Final Output",    "🗜️ "),
}

def _step(n: int, detail: str = "") -> None:
    label, icon = _STEPS.get(n, ("", ""))
    bar = "─" * 60
    print(f"\n{bar}")
    print(f"  {icon}  Step {n} · {label}")
    if detail:
        print(f"     {detail}")
    print(bar)


# ── System prompt ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """\
You are an autonomous infrastructure support agent for an Australian enterprise cloud platform.
You operate in a strict agentic loop: Reason → Plan → Act → Observe → Re-Plan → Act → Summarize.

RULES:
1. Never guess. Always query tools to discover facts before drawing conclusions.
2. After each tool result, explicitly state: what you now know, what is still unknown, and your next action.
3. Build and maintain an internal task list. Mark tasks DONE only when you have tool evidence.
4. When you have fully diagnosed the issue AND have a concrete resolution, notify the client team
   using send_notification, then provide a final human-readable summary.
5. Be concise. The client is under incident pressure — no preamble, no filler.

OUTPUT FORMAT for reasoning steps (before final answer):
  OBSERVATION: <what the tool result tells you>
  GAP: <what is still unknown>
  NEXT ACTION: <exactly what you will do next and why>

{memory_context}
"""


class AgentLoop:
    """
    Autonomous agent loop orchestrator.

    Manages the full 8-step cycle:
      Step 1  Ingest chat history + session memory
      Step 2  Initial reasoning reflection
      Step 3  Dynamic task tree construction
      Step 4  Primary tool execution (MCP Postgres deployment ledger)
      Step 5  Observation and self-reflection
      Step 6  Plan update — secondary tool selection
      Step 7  Secondary tool execution (S3 log retrieval)
      Step 8  GENCOST token reduction + final summarized output

    Each call to .run() is idempotent — subsequent calls resume from
    the same SessionMemory object (simulating session persistence).
    """

    def __init__(
        self,
        session_id:  str = "sydney-client-901",
        model_id:    str = MODEL_ID,
        aws_profile: str = AWS_PROFILE,
        aws_region:  str = AWS_REGION,
    ) -> None:
        self.session_id = session_id
        self.model_id   = model_id
        self.memory     = SessionMemory(session_id=session_id)

        sess = boto3.Session(profile_name=aws_profile, region_name=aws_region)
        self._bedrock = sess.client("bedrock-runtime")

        logger.info(
            "[LOOP] Initialized — model=%s region=%s session=%s",
            model_id, aws_region, session_id,
        )

    # ── Public entry point ────────────────────────────────────────────────────

    def run(self, user_input: str) -> str:
        """
        Execute the full autonomous agent loop for a given user message.

        Returns:
            The agent's final synthesized answer as a plain string.
        """
        t_start = time.time()

        # ─────────────────────────────────────────────────────────────────────
        # STEP 1 · Chat History Ingestion
        # ─────────────────────────────────────────────────────────────────────
        _step(1, f'User: "{textwrap.shorten(user_input, 70)}"')
        self.memory.iteration_count = 0

        # Pre-load known session variables (in prod: pulled from DynamoDB/S3)
        self.memory.set("active_account",  "Qantas-AU-Prod")
        self.memory.set("client_id",       "QANTAS-AU")
        self.memory.set("contact_email",   "oncall@qantas.com.au")
        self.memory.set("incident_id",     "INC-2026-001")

        print(f"\n  Session ID      : {self.session_id}")
        print(f"  Active account  : {self.memory.get('active_account')}")
        print(f"  Incident        : {self.memory.get('incident_id')}")
        print(f"  Short-term vars : {list(self.memory.short_term.keys())}")

        # Build initial messages list
        messages: list[dict] = [
            {"role": "user", "content": [{"text": user_input}]},
        ]

        # ─────────────────────────────────────────────────────────────────────
        # STEP 2 · Reasoning — Initial Reflection (first model call)
        # ─────────────────────────────────────────────────────────────────────
        _step(2, "LLM processes input + memory context before deciding any action")

        system_prompt = _SYSTEM_PROMPT.format(
            memory_context=self.memory.build_context_block()
        )

        # First Bedrock call — model reflects and produces its initial plan
        response = self._call_model(messages, system_prompt)
        stop_reason = response["stopReason"]
        assistant_msg = response["output"]["message"]

        # Append assistant's first response to the conversation
        messages.append({"role": "assistant", "content": assistant_msg["content"]})

        # Extract and display any text reasoning the model produced
        initial_text = self._extract_text(assistant_msg["content"])
        if initial_text:
            print(f"\n  Model's initial reflection:\n")
            for line in initial_text.strip().splitlines()[:12]:
                print(f"    {line}")

        # ─────────────────────────────────────────────────────────────────────
        # STEP 3 · Planning — Task Tree Generation
        # The model's first reasoning implicitly defines tasks; we surface
        # them by parsing its NEXT ACTION statements and creating explicit
        # TaskNodes in memory. In production, a structured planner sub-call
        # (Nova Micro) extracts a formal JSON task list.
        # ─────────────────────────────────────────────────────────────────────
        _step(3, "Agent constructs its internal task tree before executing any tool")

        self.memory.add_task("T1", "Query deployment ledger for recent changes under Qantas-AU-Prod")
        self.memory.add_task("T2", "Retrieve error log file from S3 URI identified in T1")
        self.memory.add_task("T3", "Diagnose root cause from log content")
        self.memory.add_task("T4", "Notify client engineering team with resolution details")

        print(f"\n  Initial task tree:")
        print(self.memory.task_summary())

        # ─────────────────────────────────────────────────────────────────────
        # MAIN AGENTIC LOOP
        # Steps 4 → 7 iterate here until the model issues no more tool calls
        # ─────────────────────────────────────────────────────────────────────
        iteration       = 0
        step_counter    = 4   # Tracks display step number (4, 5/6, 7, …)
        final_answer    = ""

        while stop_reason == "tool_use" and iteration < MAX_ITERATIONS:
            iteration += 1
            self.memory.iteration_count = iteration

            # Extract all tool calls from the last assistant message
            tool_use_blocks = [
                block for block in assistant_msg["content"]
                if "toolUse" in block
            ]

            # ── Determine display step (4 or 6) ───────────────────────────────
            is_secondary = iteration > 1
            display_step = 6 if is_secondary else 4
            tool_label   = "☁️  S3 Log Retrieval" if is_secondary else "🔌 MCP Postgres"
            _step(display_step, f"Iteration {iteration} — {tool_label} — {len(tool_use_blocks)} tool call(s)")

            # ── Execute all tool calls ─────────────────────────────────────────
            tool_result_blocks = []
            for block in tool_use_blocks:
                tool_use    = block["toolUse"]
                tool_name   = tool_use["name"]
                tool_input  = tool_use["input"]
                tool_use_id = tool_use["toolUseId"]

                print(f"\n  Tool requested  : {tool_name}")
                print(f"  Input arguments : {json.dumps(tool_input, indent=4)}")

                # Dispatch to the tool implementation
                tool_result = execute_tool(tool_name, tool_input)
                self.memory.record_tool_call(tool_name, tool_input, tool_result)

                # Mark relevant task as in-progress
                task_map = {
                    "query_deployment_ledger": "T1",
                    "read_s3_log_file":        "T2",
                    "send_notification":       "T4",
                }
                if tid := task_map.get(tool_name):
                    self.memory.start_task(tid)

                print(f"\n  Tool response   :")
                result_str = json.dumps(tool_result, indent=4)
                for line in result_str.splitlines()[:20]:
                    print(f"    {line}")

                tool_result_blocks.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content":   [{"json": tool_result}],
                    }
                })

            # ── STEP 5 (first iteration) or STEP 6+ · Observation & Reflection ─
            obs_step = 5 if not is_secondary else 6
            _step(obs_step, "Model loops results back into reasoning engine — evaluates progress")

            # Append tool results and re-invoke the model
            messages.append({"role": "user", "content": tool_result_blocks})

            # Update system prompt with latest memory state
            system_prompt = _SYSTEM_PROMPT.format(
                memory_context=self.memory.build_context_block()
            )

            response        = self._call_model(messages, system_prompt)
            stop_reason     = response["stopReason"]
            assistant_msg   = response["output"]["message"]
            messages.append({"role": "assistant", "content": assistant_msg["content"]})

            # Display the model's observation/reasoning text
            obs_text = self._extract_text(assistant_msg["content"])
            if obs_text:
                print(f"\n  Model observation:")
                for line in obs_text.strip().splitlines()[:15]:
                    print(f"    {line}")

            # ── Mark completed tasks based on tool call ────────────────────────
            for block in tool_use_blocks:
                tname = block["toolUse"]["name"]
                if tid := task_map.get(tname):
                    self.memory.complete_task(tid, notes=f"Completed via {tname}")

            # ── GENCOST: flush to long-term if buffer is large ────────────────
            if self.memory.should_flush(TOKEN_FLUSH_BUDGET):
                logger.info("[LOOP] Token budget exceeded — flushing to long-term memory")
                self.memory.flush_to_long_term(
                    summary=f"Iteration {iteration}: executed {len(tool_use_blocks)} tool(s). "
                            f"Tasks done: {[t.task_id for t in self.memory.task_tree if t.status == 'DONE']}"
                )

        # ─────────────────────────────────────────────────────────────────────
        # STEP 8 · Token Reduction & Final Summarization
        # Model has stopped issuing tool calls → it has its answer.
        # We do a final compression pass before returning.
        # ─────────────────────────────────────────────────────────────────────
        _step(8, "Compress intermediate traces → synthesize clean human-readable output")

        final_answer = self._extract_text(assistant_msg["content"])

        # Flush remaining tool history to long-term memory (GENCOST)
        if self.memory.tool_history:
            self.memory.flush_to_long_term(
                summary=(
                    f"Root cause diagnosed in {iteration} iterations. "
                    f"All tasks: {[t.status for t in self.memory.task_tree]}. "
                    f"Final answer delivered to client."
                )
            )

        # Mark T3 as done (diagnosis complete)
        self.memory.complete_task("T3", notes="Root cause identified from S3 log analysis")

        elapsed = (time.time() - t_start) * 1000
        print(f"\n  Iterations      : {iteration}")
        print(f"  Total latency   : {elapsed:.0f}ms")
        print(f"  Long-term arcs  : {len(self.memory.long_term)}")
        print(f"\n  Final task tree:")
        print(self.memory.task_summary())

        return final_answer

    # ── Private helpers ───────────────────────────────────────────────────────

    def _call_model(self, messages: list[dict], system_prompt: str) -> dict:
        """
        Invoke Bedrock converse API with the full conversation history,
        tool definitions, and system prompt.

        The converse API is stateless — we reconstruct the full conversation
        each call. AgentCore manages the session state boundary in production.
        """
        logger.debug("[LOOP] Calling Bedrock — %d messages in history", len(messages))
        try:
            return self._bedrock.converse(
                modelId=self.model_id,
                messages=messages,
                system=[{"text": system_prompt}],
                toolConfig=TOOL_CONFIG,
                inferenceConfig={
                    "maxTokens":   1_024,
                    "temperature": 0.1,   # Low temp → deterministic tool selection
                },
            )
        except Exception as exc:
            logger.error("[LOOP] Bedrock call failed: %s", exc)
            raise

    @staticmethod
    def _extract_text(content_blocks: list[dict]) -> str:
        """Concatenate all text blocks from a message content list."""
        return "".join(
            block["text"]
            for block in content_blocks
            if "text" in block
        )
