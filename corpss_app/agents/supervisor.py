"""
Supervisor Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Top-level orchestrator that decomposes incoming loan application
queries into specialist sub-tasks, fans them out in parallel to
isolated sub-agents, then aggregates their outputs into a single
coherent decision.

Macro Orchestration Layer (CORPSEE framework):
  ┌──────────────────────────────────────────────┐
  │  Supervisor Agent (Nova Micro — task routing) │
  │    ├─ Sub-Agent 1: Fraud Detection            │
  │    ├─ Sub-Agent 2: Compliance Check           │
  │    └─ Sub-Agent 3: Risk Scoring               │
  │  Aggregator (Claude 3.5 Sonnet — synthesis)   │
  └──────────────────────────────────────────────┘

Each sub-agent runs in an isolated AgentCore microVM session
identified by a unique session_id.  Parallel execution is
achieved with ThreadPoolExecutor (simulating separate microVMs
in this reference implementation).
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional

import boto3

from agents.base_agent import SubAgentResult
from agents.compliance_agent import ComplianceAgent
from agents.fraud_agent import FraudDetectionAgent
from agents.risk_agent import RiskScoringAgent
from config import AWS_REGION, GUARDRAIL_ID, GUARDRAIL_VERSION, MODEL_FRONTIER, MODEL_LIGHTWEIGHT

logger = logging.getLogger(__name__)

# ── Data structures ────────────────────────────────────────────────────────────

@dataclass
class SupervisorDecision:
    """Aggregated final decision from Supervisor + all sub-agents."""
    session_id:      str
    query:           str
    final_decision:  str          # APPROVE / MANUAL_REVIEW / DECLINE
    overall_risk:    str          # LOW / MEDIUM / HIGH / CRITICAL
    summary:         str
    sub_results:     list[SubAgentResult] = field(default_factory=list)
    latency_ms:      float = 0.0
    error:           Optional[str] = None
    success:         bool = True

    def to_dict(self) -> dict:
        return {
            "session_id":     self.session_id,
            "final_decision": self.final_decision,
            "overall_risk":   self.overall_risk,
            "summary":        self.summary,
            "sub_agents":     [r.to_dict() for r in self.sub_results],
            "latency_ms":     round(self.latency_ms, 1),
            "success":        self.success,
            "error":          self.error,
        }


# ── Supervisor Agent ───────────────────────────────────────────────────────────

class SupervisorAgent:
    """
    Macro-orchestrator for multi-agent loan application assessment.

    Step 1 — Task Decomposition  (Nova Micro, lightweight)
        Breaks the incoming query into targeted sub-tasks for each specialist.

    Step 2 — Parallel Delegation  (ThreadPoolExecutor)
        Fires all three sub-agents concurrently, each with a unique session_id.
        Mirrors the AgentCore microVM isolation boundary.

    Step 3 — Result Aggregation  (Claude 3.5 Sonnet, frontier)
        Synthesises sub-agent outputs into a single final decision with
        executive summary and audit trail.
    """

    _DECOMPOSE_SCHEMA = """{
  "fraud_task": "focused sub-task description for fraud detection agent",
  "compliance_task": "focused sub-task description for compliance agent",
  "risk_task": "focused sub-task description for risk scoring agent"
}"""

    _AGGREGATE_SCHEMA = """{
  "final_decision": "APPROVE | MANUAL_REVIEW | REFER_COMPLIANCE | DECLINE",
  "overall_risk": "LOW | MEDIUM | HIGH | CRITICAL",
  "summary": "executive summary of the assessment (2-3 sentences)",
  "key_issues": ["top issues identified across all agents"],
  "next_steps": ["recommended next steps for the lending officer"]
}"""

    def __init__(self) -> None:
        self._bedrock_rt    = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        self._fraud_agent   = FraudDetectionAgent()
        self._compliance    = ComplianceAgent()
        self._risk_agent    = RiskScoringAgent()

    # ── Public API ─────────────────────────────────────────────────────────────

    def assess(self, query: str, session_id: str | None = None) -> SupervisorDecision:
        """
        Full multi-agent assessment pipeline for a loan application query.

        Args:
            query:      Raw loan application details or transaction description.
            session_id: Parent session ID; sub-agents get child IDs derived from it.

        Returns:
            SupervisorDecision with aggregated result from all three sub-agents.
        """
        session_id = session_id or f"sup-{uuid.uuid4().hex[:8]}"
        t0 = time.time()

        logger.info("[SUPERVISOR] Starting assessment — session=%s", session_id)

        # ── Step 1: Task decomposition ─────────────────────────────────────────
        try:
            sub_tasks = self._decompose_query(query, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("[SUPERVISOR] Decomposition failed: %s", exc)
            # Fall back: use the full query as each sub-task
            sub_tasks = {
                "fraud_task":      query,
                "compliance_task": query,
                "risk_task":       query,
            }

        logger.info("[SUPERVISOR] Sub-tasks decomposed: %s", list(sub_tasks.keys()))

        # ── Step 2: Parallel sub-agent execution ───────────────────────────────
        sub_results = self._run_parallel(sub_tasks, session_id)

        # ── Step 3: Aggregation ────────────────────────────────────────────────
        try:
            aggregated = self._aggregate_results(query, sub_results, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.error("[SUPERVISOR] Aggregation failed: %s", exc)
            aggregated = {
                "final_decision": "MANUAL_REVIEW",
                "overall_risk":   "HIGH",
                "summary":        "Aggregation model unavailable — manual review required.",
                "key_issues":     ["Aggregation error: " + str(exc)],
                "next_steps":     ["Escalate to senior lending officer for manual assessment"],
            }

        latency = (time.time() - t0) * 1000
        logger.info(
            "[SUPERVISOR] ✅ Assessment complete — session=%s latency=%.0fms decision=%s",
            session_id, latency, aggregated.get("final_decision"),
        )

        return SupervisorDecision(
            session_id=session_id,
            query=query,
            final_decision=aggregated.get("final_decision", "MANUAL_REVIEW"),
            overall_risk=aggregated.get("overall_risk", "HIGH"),
            summary=aggregated.get("summary", ""),
            sub_results=sub_results,
            latency_ms=latency,
            success=True,
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _decompose_query(self, query: str, parent_session: str) -> dict:
        """
        Use Nova Micro to break the query into targeted per-specialist sub-tasks.
        Lightweight model keeps decomposition cost near zero.
        """
        prompt = (
            "You are a loan application triage supervisor.\n\n"
            "Decompose the following loan application into three focused sub-tasks:\n"
            "1. A fraud detection sub-task (focus on identity, income, LVR anomalies)\n"
            "2. A compliance sub-task (focus on NCCP, AML/CTF, APRA obligations)\n"
            "3. A risk scoring sub-task (focus on DTI, LVR, serviceability buffer)\n\n"
            f"Application:\n{query}\n\n"
            f"Return valid JSON matching this schema:\n{self._DECOMPOSE_SCHEMA}"
        )
        resp = self._bedrock_rt.converse(
            modelId=MODEL_LIGHTWEIGHT,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            guardrailConfig={
                "guardrailIdentifier": GUARDRAIL_ID,
                "guardrailVersion":    GUARDRAIL_VERSION,
            },
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)

    def _run_parallel(
        self,
        sub_tasks: dict,
        parent_session: str,
        max_workers: int = 3,
    ) -> list[SubAgentResult]:
        """
        Execute all sub-agents concurrently via ThreadPoolExecutor.

        Each sub-agent receives a child session_id derived from the parent,
        enforcing AgentCore microVM boundary isolation semantics.
        """
        jobs = {
            self._fraud_agent:   (sub_tasks.get("fraud_task", ""),      f"{parent_session}-fraud"),
            self._compliance:    (sub_tasks.get("compliance_task", ""), f"{parent_session}-compliance"),
            self._risk_agent:    (sub_tasks.get("risk_task", ""),       f"{parent_session}-risk"),
        }

        results: list[SubAgentResult] = []
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(agent.invoke, task, sid): agent.agent_name
                for agent, (task, sid) in jobs.items()
            }
            for future in as_completed(futures):
                agent_name = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    logger.info(
                        "[SUPERVISOR] Sub-agent '%s' completed — score=%.2f",
                        agent_name, result.self_score,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error("[SUPERVISOR] Sub-agent '%s' raised: %s", agent_name, exc)

        return results

    def _aggregate_results(
        self,
        original_query: str,
        sub_results: list[SubAgentResult],
        parent_session: str,
    ) -> dict:
        """
        Use Claude 3.5 Sonnet to synthesise sub-agent outputs into a final decision.
        The frontier model is used here for nuanced multi-factor reasoning.
        """
        sub_summaries = "\n\n".join(
            f"--- {r.agent_name.upper()} AGENT (confidence={r.self_score:.2f}) ---\n{r.response}"
            for r in sub_results
        )

        prompt = (
            "You are a senior lending decision officer at an Australian bank.\n\n"
            "You have received assessments from three specialist AI agents for the "
            "following loan application:\n\n"
            f"ORIGINAL APPLICATION:\n{original_query}\n\n"
            f"SPECIALIST ASSESSMENTS:\n{sub_summaries}\n\n"
            "Synthesise these assessments into a single final lending decision.\n"
            "Consider all risk factors holistically. If any agent flags CRITICAL or HIGH risk, "
            "escalate the overall decision accordingly.\n\n"
            f"Return valid JSON matching this schema:\n{self._AGGREGATE_SCHEMA}"
        )

        resp = self._bedrock_rt.converse(
            modelId=MODEL_FRONTIER,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            guardrailConfig={
                "guardrailIdentifier": GUARDRAIL_ID,
                "guardrailVersion":    GUARDRAIL_VERSION,
            },
        )
        raw = resp["output"]["message"]["content"][0]["text"]
        clean = raw.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        return json.loads(clean)
