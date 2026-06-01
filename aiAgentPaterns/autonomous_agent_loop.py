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
  Short-term memory        →  memory.SessionMemory.short_term
  Long-term memory         →  memory.SessionMemory.long_term (flush)
  Tool integrations        →  tools/ package (MCP Postgres, S3, SNS)

Design decisions — hardcoded vs dynamic:
  ┌──────────────────────────────────────────────┬────────────────────┐
  │  Element                                     │  Who decides?      │
  ├──────────────────────────────────────────────┼────────────────────┤
  │  Step banner labels                          │  Runtime context   │
  │    (derived from actual tool name + iter)    │  (not a dict)      │
  ├──────────────────────────────────────────────┼────────────────────┤
  │  Task tree (T1, T2, … Tn)                   │  LLM Planner       │
  │    (Plan-and-Execute pattern, Nova Micro)    │  (not hardcoded)   │
  ├──────────────────────────────────────────────┼────────────────────┤
  │  Which tool to call next                     │  Executor LLM      │
  ├──────────────────────────────────────────────┼────────────────────┤
  │  Tool arguments                              │  Executor LLM      │
  ├──────────────────────────────────────────────┼────────────────────┤
  │  When to stop looping (end_turn)             │  Executor LLM      │
  └──────────────────────────────────────────────┴────────────────────┘
"""
from __future__ import annotations

import json
import logging
import textwrap
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

from config import (
    AWS_PROFILE,
    AWS_REGION,
    MAX_ITERATIONS,
    MAX_RETRIES,
    MODEL_FALLBACK,
    MODEL_ID,
    RETRY_BASE_DELAY_S,
    TOKEN_FLUSH_BUDGET,
)
from memory import SessionMemory, TaskNode
from tools  import TOOL_CONFIG, execute_tool

logger = logging.getLogger(__name__)

# Errors that are worth retrying (transient capacity issues)
_RETRYABLE    = {"ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException"}


# ── Step banner helper (dynamic, not a lookup dict) ───────────────────────────

def _step_banner(
    number:    int,
    label:     str,
    icon:      str = "▶",
    detail:    str = "",
) -> None:
    """
    Print a step banner.

    Labels are passed in at call time, derived from runtime context
    (the actual tool name, iteration count, stopReason, etc.) rather
    than looked up from a static dictionary.  This means the banner
    accurately reflects WHAT IS ACTUALLY HAPPENING, not a pre-written
    script — e.g. "Tool Call 3 · send_notification" rather than
    the generic "Step 7 · Secondary Tool Execution".
    """
    bar = "─" * 60
    print(f"\n{bar}")
    print(f"  {icon}  Step {number} · {label}")
    if detail:
        print(f"     {detail}")
    print(bar)


# ── System prompts ────────────────────────────────────────────────────────────

_PLANNER_PROMPT = """\
You are a task planning assistant for an enterprise infrastructure support system.

Available tools:
{tool_names}

A support agent has received this request:
"{user_input}"

Context about the client session:
{memory_context}

Your job: produce a concise, ordered task list the agent must complete to fully
resolve this request. Each task should map to one or two tool calls.

Respond ONLY with a JSON array. No explanation, no markdown fences.
Format:
[
  {{"id": "T1", "description": "...", "tool_hint": "<tool_name_or_null>"}},
  {{"id": "T2", "description": "...", "tool_hint": "<tool_name_or_null>"}},
  ...
]
"""

_EXECUTOR_SYSTEM_PROMPT = """\
You are an autonomous infrastructure support agent for an Australian enterprise cloud platform.
You operate in a strict agentic loop: Reason → Plan → Act → Observe → Re-Plan → Act → Summarize.

RULES:
1. Never guess. Always query tools to discover facts before drawing conclusions.
2. After each tool result, explicitly state: what you now know, what is still unknown,
   and your next action.
3. Work through the task list below in order. Mark each task complete only when you
   have tool evidence — not before.
4. When you have fully diagnosed the issue AND have a concrete resolution, notify the
   client team using send_notification, then provide a final human-readable summary.
5. Be concise. The client is under incident pressure — no preamble, no filler.

OUTPUT FORMAT for reasoning steps (before final answer):
  OBSERVATION: <what the tool result tells you>
  GAP:         <what is still unknown>
  NEXT ACTION: <exactly what you will do next and why>

{memory_context}
"""


class AgentLoop:
    """
    Autonomous agent loop orchestrator — Plan-and-Execute pattern.

    Two-model architecture:
      Planner  (Nova Micro / lightweight) — Step 3: generates the task tree as JSON
      Executor (Nova Pro  / frontier)     — Steps 4–7: works through each task via tools

    This separates WHAT to do (cheap, fast planning) from HOW to do it
    (expensive, careful execution), which is the production best practice for
    cost-efficient agentic systems.

    Step labels: derived from runtime context (actual tool name, iteration,
    stopReason) — not from a hardcoded lookup dict.

    Task tree: generated by the Planner LLM from the user's input and session
    memory — not hardcoded — so the same loop handles any request type.
    """

    def __init__(
        self,
        session_id:     str = "sydney-client-901",
        model_id:       str = MODEL_ID,
        fallback_model: str = MODEL_FALLBACK,
        planner_model:  str = "amazon.nova-lite-v1:0",   # lightweight for planning
        aws_profile:    str = AWS_PROFILE,
        aws_region:     str = AWS_REGION,
    ) -> None:
        self.session_id      = session_id
        self.model_id        = model_id
        self._active_model   = model_id
        self._fallback_model = fallback_model
        self._planner_model  = planner_model
        self.memory          = SessionMemory(session_id=session_id)

        sess = boto3.Session(profile_name=aws_profile, region_name=aws_region)
        self._bedrock = sess.client("bedrock-runtime")

        logger.info(
            "[LOOP] Initialized — executor=%s planner=%s fallback=%s session=%s",
            model_id, planner_model, fallback_model, session_id,
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
        _step_banner(
            number=1, icon="📥", label="Chat History Ingestion",
            detail=f'User: "{textwrap.shorten(user_input, 65)}"',
        )
        self.memory.iteration_count = 0

        # Pre-load session context (in production: pulled from DynamoDB/S3
        # keyed by the authenticated user's session token)
        self.memory.set("active_account", "Qantas-AU-Prod")
        self.memory.set("client_id",      "QANTAS-AU")
        self.memory.set("contact_email",  "oncall@qantas.com.au")
        self.memory.set("incident_id",    "INC-2026-001")

        print(f"\n  Session ID      : {self.session_id}")
        print(f"  Active account  : {self.memory.get('active_account')}")
        print(f"  Incident        : {self.memory.get('incident_id')}")

        # ─────────────────────────────────────────────────────────────────────
        # STEP 2 · Reasoning — Initial Reflection
        # ─────────────────────────────────────────────────────────────────────
        _step_banner(
            number=2, icon="🧠", label="Reasoning — Initial Reflection",
            detail="Executor LLM assesses the request before deciding any action",
        )

        system_prompt = _EXECUTOR_SYSTEM_PROMPT.format(
            memory_context=self.memory.build_context_block(),
        )
        messages: list[dict] = [
            {"role": "user", "content": [{"text": user_input}]},
        ]

        response      = self._call_executor(messages, system_prompt)
        stop_reason   = response["stopReason"]
        assistant_msg = response["output"]["message"]
        messages.append({"role": "assistant", "content": assistant_msg["content"]})

        initial_text = self._extract_text(assistant_msg["content"])
        if initial_text:
            print(f"\n  Model's initial reflection:\n")
            for line in initial_text.strip().splitlines()[:12]:
                print(f"    {line}")

        # ─────────────────────────────────────────────────────────────────────
        # STEP 3 · Planning — LLM-Generated Task Tree
        #
        # The Planner LLM (Nova Micro / Nova Lite — cheap) receives the user
        # request + available tools and produces a JSON task list.
        #
        # WHY separate from the executor?
        #   • Planning is a structured, low-complexity call → use a smaller model
        #   • Decouples WHAT to do from HOW to do it (Plan-and-Execute pattern)
        #   • Task list adapts to any request — no hardcoded T1/T2/T3
        #   • Enables replanning mid-loop if the situation changes
        # ─────────────────────────────────────────────────────────────────────
        _step_banner(
            number=3, icon="📋", label="Planning — LLM-Generated Task Tree",
            detail=f"Planner model: {self._planner_model}",
        )

        tasks = self._llm_plan(user_input)
        if not tasks:
            # Planner failed or returned nothing — executor will self-plan
            logger.warning("[LOOP] Planner returned no tasks; executor will self-direct")
            print("  ⚠️   Planner returned no tasks — executor will self-direct via tool calls")
        else:
            for t in tasks:
                self.memory.add_task(t["id"], t["description"])
            print(f"\n  LLM-generated task tree ({len(tasks)} tasks):")
            print(self.memory.task_summary())

        # Build a tool→task mapping from the planner's tool_hint annotations.
        # This replaces the hardcoded {"query_deployment_ledger": "T1", ...} dict.
        tool_task_map: dict[str, str] = {
            t["tool_hint"]: t["id"]
            for t in tasks
            if t.get("tool_hint")
        }
        logger.info("[LOOP] Tool→task map from planner: %s", tool_task_map)

        # ─────────────────────────────────────────────────────────────────────
        # MAIN AGENTIC LOOP  (Steps 4 → 7, dynamic iterations)
        #
        # The loop drives itself entirely from the model's stopReason:
        #   "tool_use" → executor wants to call a tool → execute → loop back
        #   "end_turn" → executor has its final answer → exit
        #
        # Step banner labels are generated from the ACTUAL tool name and
        # iteration count at runtime — not from a pre-written script.
        # ─────────────────────────────────────────────────────────────────────
        iteration    = 0
        final_answer = ""

        while stop_reason == "tool_use" and iteration < MAX_ITERATIONS:
            iteration += 1
            self.memory.iteration_count = iteration

            tool_use_blocks = [
                block for block in assistant_msg["content"]
                if "toolUse" in block
            ]

            # ── Dynamic step banner ───────────────────────────────────────────
            # Label is built from what is ACTUALLY happening this iteration,
            # not from a hardcoded position in a numbered list.
            tool_names_called = [b["toolUse"]["name"] for b in tool_use_blocks]
            banner_label = (
                f"Tool Call (iter {iteration}) · {' + '.join(tool_names_called)}"
            )
            banner_icon = "🔌" if iteration == 1 else "🔄"
            _step_banner(
                number=3 + iteration,   # Steps 4, 5, 6, … as iterations grow
                icon=banner_icon,
                label=banner_label,
                detail=f"{len(tool_use_blocks)} tool call(s) requested by executor",
            )

            # ── Execute each tool call ────────────────────────────────────────
            tool_result_blocks = []
            for block in tool_use_blocks:
                tool_use    = block["toolUse"]
                tool_name   = tool_use["name"]
                tool_input  = tool_use["input"]
                tool_use_id = tool_use["toolUseId"]

                print(f"\n  Tool            : {tool_name}")
                print(f"  Arguments       : {json.dumps(tool_input, indent=4)}")

                # Mark the associated planned task as in-progress
                if tid := tool_task_map.get(tool_name):
                    self.memory.start_task(tid)

                tool_result = execute_tool(tool_name, tool_input)
                self.memory.record_tool_call(tool_name, tool_input, tool_result)

                print(f"\n  Result          :")
                for line in json.dumps(tool_result, indent=4).splitlines()[:20]:
                    print(f"    {line}")

                tool_result_blocks.append({
                    "toolResult": {
                        "toolUseId": tool_use_id,
                        "content":   [{"json": tool_result}],
                    }
                })

            # ── Observation & Re-Plan ────────────────────────────────────────
            # The banner label dynamically reflects what the model observed
            # (derived from the tool names just executed, not a fixed label).
            obs_label = f"Observation — After {' + '.join(tool_names_called)}"
            _step_banner(
                number=3 + iteration + 1,
                icon="🔍",
                label=obs_label,
                detail="Executor loops results back into reasoning engine",
            )

            # Update the system prompt so the executor sees the latest task state
            system_prompt = _EXECUTOR_SYSTEM_PROMPT.format(
                memory_context=self.memory.build_context_block(),
            )
            messages.append({"role": "user", "content": tool_result_blocks})
            response      = self._call_executor(messages, system_prompt)
            stop_reason   = response["stopReason"]
            assistant_msg = response["output"]["message"]
            messages.append({"role": "assistant", "content": assistant_msg["content"]})

            obs_text = self._extract_text(assistant_msg["content"])
            if obs_text:
                print(f"\n  Model observation:\n")
                for line in obs_text.strip().splitlines()[:15]:
                    print(f"    {line}")

            # Mark associated planned tasks as complete
            for tool_name in tool_names_called:
                if tid := tool_task_map.get(tool_name):
                    self.memory.complete_task(tid, notes=f"Evidence from {tool_name}")

            # GENCOST: flush intermediate traces if buffer exceeds budget
            if self.memory.should_flush(TOKEN_FLUSH_BUDGET):
                self.memory.flush_to_long_term(
                    summary=(
                        f"Iteration {iteration}: called {tool_names_called}. "
                        f"Tasks done: {[t.task_id for t in self.memory.task_tree if t.status == 'DONE']}"
                    )
                )

        # ─────────────────────────────────────────────────────────────────────
        # FINAL STEP · Token Reduction & Output
        # Dynamic number: one beyond the last observation step
        # ─────────────────────────────────────────────────────────────────────
        final_step_n = 3 + (iteration * 2) + 1
        _step_banner(
            number=final_step_n,
            icon="🗜️ ",
            label="Token Reduction & Final Output (GENCOST)",
            detail=f"Completed in {iteration} iteration(s) — flushing intermediate traces",
        )

        final_answer = self._extract_text(assistant_msg["content"])

        if self.memory.tool_history:
            self.memory.flush_to_long_term(
                summary=f"Diagnosis complete after {iteration} loop iterations. "
                        f"All tasks: {[f'{t.task_id}={t.status}' for t in self.memory.task_tree]}."
            )

        elapsed = (time.time() - t_start) * 1000
        print(f"\n  Loop iterations : {iteration}")
        print(f"  Total latency   : {elapsed:.0f}ms")
        print(f"  Active model    : {self._active_model}")
        print(f"\n  Final task tree:")
        print(self.memory.task_summary())

        return final_answer

    # ── Planner (Step 3): LLM-generated task tree ─────────────────────────────

    def _llm_plan(self, user_input: str) -> list[dict]:
        """
        Call the lightweight Planner model to generate a structured task list.

        Uses Nova Lite (cheap, fast) — not the frontier executor model.
        Returns a list of dicts: [{"id": "T1", "description": "...", "tool_hint": "..."}]

        Falls back to an empty list on any failure; the executor LLM will
        self-direct via tool calls in that case.
        """
        tool_names = "\n".join(
            f"  - {t['toolSpec']['name']}: {t['toolSpec']['description'][:80]}"
            for t in TOOL_CONFIG["tools"]
        )
        prompt = _PLANNER_PROMPT.format(
            tool_names=tool_names,
            user_input=user_input,
            memory_context=self.memory.build_context_block(),
        )
        logger.info("[PLANNER] Generating task tree with %s …", self._planner_model)
        try:
            resp = self._bedrock.converse(
                modelId=self._planner_model,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": 512, "temperature": 0.0},
            )
            raw = resp["output"]["message"]["content"][0]["text"]
            # Strip markdown fences if the model wraps the JSON
            clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            tasks = json.loads(clean)
            logger.info("[PLANNER] Generated %d task(s): %s", len(tasks), [t["id"] for t in tasks])
            print(f"\n  Planner model   : {self._planner_model}")
            print(f"  Tasks generated : {len(tasks)}")
            return tasks
        except Exception as exc:  # noqa: BLE001
            logger.warning("[PLANNER] Task generation failed: %s", exc)
            print(f"  ⚠️   Planner failed ({exc.__class__.__name__}) — executor will self-direct")
            return []

    # ── Executor: Bedrock converse with retry + model fallback ────────────────

    def _call_executor(self, messages: list[dict], system_prompt: str) -> dict:
        """
        Invoke the executor model (Bedrock converse) with exponential backoff
        retry and automatic fallback to the lighter model on quota exhaustion.
        """
        logger.debug("[EXECUTOR] Calling %s — %d messages", self._active_model, len(messages))
        last_exc = None

        for attempt in range(1, MAX_RETRIES + 2):
            try:
                return self._bedrock.converse(
                    modelId=self._active_model,
                    messages=messages,
                    system=[{"text": system_prompt}],
                    toolConfig=TOOL_CONFIG,
                    inferenceConfig={"maxTokens": 1_024, "temperature": 0.1},
                )
            except ClientError as exc:
                code = exc.response["Error"]["Code"]
                if code not in _RETRYABLE:
                    raise
                last_exc = exc

                if attempt > MAX_RETRIES:
                    if self._active_model != self._fallback_model:
                        logger.warning(
                            "[EXECUTOR] 🚨 Quota exhausted on %s — switching to %s",
                            self._active_model, self._fallback_model,
                        )
                        print(
                            f"\n  ⚠️   Throttled on {self._active_model} (daily quota)."
                            f"\n  🔄  Switching to fallback: {self._fallback_model}\n"
                        )
                        self._active_model = self._fallback_model
                        try:
                            return self._bedrock.converse(
                                modelId=self._active_model,
                                messages=messages,
                                system=[{"text": system_prompt}],
                                toolConfig=TOOL_CONFIG,
                                inferenceConfig={"maxTokens": 1_024, "temperature": 0.1},
                            )
                        except ClientError as fb_exc:
                            raise fb_exc
                    raise last_exc

                wait = RETRY_BASE_DELAY_S * (2 ** (attempt - 1))
                logger.warning("[EXECUTOR] %s (attempt %d/%d) — retrying in %ds", code, attempt, MAX_RETRIES, wait)
                print(f"  ⏳  {code} (attempt {attempt}/{MAX_RETRIES}) — retrying in {wait}s …")
                time.sleep(wait)

        raise last_exc

    @staticmethod
    def _extract_text(content_blocks: list[dict]) -> str:
        return "".join(b["text"] for b in content_blocks if "text" in b)
