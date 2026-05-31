"""
Stateful Session Memory
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Implements the two-tier memory model used by the autonomous agent loop:

  Short-Term Memory  — active working context kept in the prompt window
                       (current task tree, last N tool results, session vars)

  Long-Term Memory   — compressed summaries written to durable store when
                       the short-term buffer exceeds TOKEN_FLUSH_BUDGET
                       (GENCOST optimisation: avoids re-processing stale traces)

In production this maps to:
  Short-Term  → AgentCore in-session state (managed by Bedrock runtime)
  Long-Term   → S3 / DynamoDB session store, rehydrated on session resume

                    ┌───────────────────────────────────────────┐
                    │             SESSION MEMORY                 │
                    │                                            │
                    │  ┌─────────────────┐  ┌────────────────┐  │
                    │  │  Short-Term      │  │  Long-Term      │  │
                    │  │  (prompt window) │→ │  (S3 / dynamo) │  │
                    │  │  task_tree       │  │  prior_summaries│  │
                    │  │  tool_history    │  │  completed_tasks│  │
                    │  │  session_vars    │  │                 │  │
                    │  └─────────────────┘  └────────────────┘  │
                    └───────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class TaskNode:
    """A single node in the agent's dynamic task tree."""
    task_id:     str
    description: str
    status:      str      = "PENDING"   # PENDING | IN_PROGRESS | DONE | SKIPPED
    result:      Any      = None
    notes:       str      = ""

    def to_dict(self) -> dict:
        return {
            "task_id":     self.task_id,
            "description": self.description,
            "status":      self.status,
            "notes":       self.notes,
        }


@dataclass
class SessionMemory:
    """
    Two-tier stateful memory for the autonomous agent loop.

    Usage:
        mem = SessionMemory(session_id="sydney-client-901")
        mem.set("active_account", "Qantas-AU-Prod")

        # Agent plans tasks
        mem.add_task("T1", "Query deployment ledger for recent changes")
        mem.add_task("T2", "Retrieve S3 error logs for flagged service")
        mem.add_task("T3", "Formulate resolution strategy")

        # Agent marks progress
        mem.complete_task("T1", result={"deployment_id": "dep-88a"})

        # When buffer grows large, flush to long-term
        mem.flush_to_long_term(summary="Identified auth-gateway-service as root cause")
    """

    session_id:       str
    short_term:       dict  = field(default_factory=dict)
    task_tree:        list  = field(default_factory=list)   # List[TaskNode]
    tool_history:     list  = field(default_factory=list)   # raw tool results
    long_term:        list  = field(default_factory=list)   # compressed summaries
    iteration_count:  int   = 0
    _token_estimate:  int   = 0

    # ── Variable store ────────────────────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        self.short_term[key] = value
        logger.debug("[MEMORY] Set %s = %s", key, value)

    def get(self, key: str, default: Any = None) -> Any:
        return self.short_term.get(key, default)

    # ── Task tree ─────────────────────────────────────────────────────────────

    def add_task(self, task_id: str, description: str) -> TaskNode:
        node = TaskNode(task_id=task_id, description=description)
        self.task_tree.append(node)
        logger.info("[MEMORY] 📋 Task added: [%s] %s", task_id, description)
        return node

    def get_task(self, task_id: str) -> TaskNode | None:
        return next((t for t in self.task_tree if t.task_id == task_id), None)

    def next_pending_task(self) -> TaskNode | None:
        return next((t for t in self.task_tree if t.status == "PENDING"), None)

    def start_task(self, task_id: str) -> None:
        if task := self.get_task(task_id):
            task.status = "IN_PROGRESS"
            logger.info("[MEMORY] ▶️  Task started: [%s]", task_id)

    def complete_task(self, task_id: str, result: Any = None, notes: str = "") -> None:
        if task := self.get_task(task_id):
            task.status = "DONE"
            task.result = result
            task.notes  = notes
            logger.info("[MEMORY] ✅ Task complete: [%s] — %s", task_id, notes or "(no notes)")

    def all_done(self) -> bool:
        return all(t.status in ("DONE", "SKIPPED") for t in self.task_tree)

    def task_summary(self) -> str:
        lines = []
        for t in self.task_tree:
            icon = {"PENDING": "☐", "IN_PROGRESS": "▶", "DONE": "✅", "SKIPPED": "⏭"}.get(t.status, "?")
            lines.append(f"  {icon} [{t.task_id}] {t.description}")
            if t.notes:
                lines.append(f"       → {t.notes}")
        return "\n".join(lines)

    # ── Tool history ──────────────────────────────────────────────────────────

    def record_tool_call(self, tool_name: str, inputs: dict, result: Any) -> None:
        entry = {"tool": tool_name, "inputs": inputs, "result": result}
        self.tool_history.append(entry)
        self._token_estimate += len(json.dumps(entry)) // 4  # rough token estimate

    # ── Long-term memory (GENCOST token flush) ────────────────────────────────

    def should_flush(self, budget: int = 4_000) -> bool:
        return self._token_estimate > budget

    def flush_to_long_term(self, summary: str) -> None:
        """
        Compress and archive the current working tool history into long-term
        memory, then clear the short-term buffer.

        In production: write `summary` to S3 / DynamoDB keyed by session_id.
        """
        self.long_term.append({
            "iteration":   self.iteration_count,
            "summary":     summary,
            "tasks_done":  [t.task_id for t in self.task_tree if t.status == "DONE"],
        })
        archived_count = len(self.tool_history)
        self.tool_history.clear()
        self._token_estimate = 0
        logger.info(
            "[MEMORY] 🗜️  Flushed %d tool entries to long-term memory (GENCOST token reduction)",
            archived_count,
        )

    # ── Context builder for system prompt ─────────────────────────────────────

    def build_context_block(self) -> str:
        """Serialise memory into a compact string for injection into the system prompt."""
        parts = [
            f"SESSION: {self.session_id}",
            f"ITERATION: {self.iteration_count}",
        ]
        if self.short_term:
            parts.append("SHORT-TERM VARS:\n" + "\n".join(
                f"  {k}: {v}" for k, v in self.short_term.items()
            ))
        if self.task_tree:
            parts.append("TASK TREE:\n" + self.task_summary())
        if self.long_term:
            parts.append("PRIOR SUMMARIES:\n" + "\n".join(
                f"  [iter {e['iteration']}] {e['summary']}" for e in self.long_term
            ))
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {
            "session_id":      self.session_id,
            "iteration":       self.iteration_count,
            "short_term":      self.short_term,
            "task_tree":       [t.to_dict() for t in self.task_tree],
            "long_term":       self.long_term,
            "token_estimate":  self._token_estimate,
        }
