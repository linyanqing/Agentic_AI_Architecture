"""
Base Sub-Agent
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Common invoke pattern shared by all specialist sub-agents.

Each sub-agent:
  - Runs inside its own AgentCore microVM session (isolated by session_id)
  - Is protected by the dual-sided GENSEC guardrail perimeter
  - Reports a structured SubAgentResult with quality self-score
  - Handles its own circuit-breaking fallback (PT → serverless)
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from config import AWS_REGION, MODEL_FRONTIER, MODEL_LIGHTWEIGHT, GUARDRAIL_ID, GUARDRAIL_VERSION

logger = logging.getLogger(__name__)

_CIRCUIT_BREAKER_FAULTS = {"ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException"}


@dataclass
class SubAgentResult:
    """Structured output from a single specialist sub-agent invocation."""
    agent_name:    str
    sub_task:      str
    response:      str
    session_id:    str
    model_used:    str
    latency_ms:    float
    self_score:    float        # 0.0–1.0 confidence the agent assigns its own output
    error:         Optional[str] = None
    success:       bool = True

    def to_dict(self) -> dict:
        return {
            "agent":       self.agent_name,
            "session_id":  self.session_id,
            "response":    self.response,
            "model":       self.model_used,
            "latency_ms":  round(self.latency_ms, 1),
            "self_score":  self.self_score,
            "success":     self.success,
            "error":       self.error,
        }


class BaseSubAgent:
    """
    Specialist sub-agent base class.

    Each sub-class provides:
      - agent_name  : unique identifier (e.g. "fraud", "compliance", "risk")
      - system_prompt: domain-specific instruction set
      - output_schema: expected JSON structure for the response

    Invocation pattern:
      1. Build a domain-scoped prompt (system_prompt + sub_task)
      2. Call Bedrock with GENSEC guardrail and isolated session_id
      3. Parse structured JSON response
      4. Return SubAgentResult with self-scored confidence
    """

    agent_name:    str = "base"
    system_prompt: str = "You are a specialist AI agent."
    output_schema: str = '{"result": "...", "confidence": 0.0}'

    def __init__(
        self,
        primary_model:  str = MODEL_FRONTIER,
        fallback_model: str = MODEL_FRONTIER,
    ) -> None:
        self._primary_model  = primary_model
        self._fallback_model = fallback_model
        self._bedrock_rt     = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    # ── Public API ────────────────────────────────────────────────────────────

    def invoke(self, sub_task: str, session_id: str | None = None) -> SubAgentResult:
        """
        Execute this specialist agent on the given sub-task.

        Each call uses a unique session_id to enforce microVM boundary isolation.
        Falls back to serverless pool on capacity faults (circuit breaker).
        """
        session_id = session_id or f"{self.agent_name}-{uuid.uuid4().hex[:8]}"
        logger.info("[%s] Invoking — session=%s", self.agent_name.upper(), session_id)

        prompt = self._build_prompt(sub_task)
        t0     = time.time()

        try:
            response_text, model_used = self._invoke_with_fallback(prompt)
        except Exception as exc:  # noqa: BLE001
            latency = (time.time() - t0) * 1000
            logger.error("[%s] Failed: %s", self.agent_name.upper(), exc)
            return SubAgentResult(
                agent_name=self.agent_name, sub_task=sub_task, response="",
                session_id=session_id, model_used="N/A", latency_ms=latency,
                self_score=0.0, error=str(exc), success=False,
            )

        latency = (time.time() - t0) * 1000
        parsed  = self._parse_response(response_text)
        score   = float(parsed.get("confidence", 0.8))

        logger.info(
            "[%s] ✅ Done — session=%s latency=%.0fms confidence=%.2f",
            self.agent_name.upper(), session_id, latency, score,
        )

        return SubAgentResult(
            agent_name=self.agent_name,
            sub_task=sub_task,
            response=json.dumps(parsed),
            session_id=session_id,
            model_used=model_used,
            latency_ms=latency,
            self_score=score,
            success=True,
        )

    # ── Private helpers ───────────────────────────────────────────────────────

    def _build_prompt(self, sub_task: str) -> str:
        return (
            f"{self.system_prompt}\n\n"
            f"Sub-task assigned by Supervisor Agent:\n{sub_task}\n\n"
            f"Return your analysis as valid JSON matching this schema:\n{self.output_schema}"
        )

    def _invoke_with_fallback(self, prompt: str) -> tuple[str, str]:
        """Try primary model; circuit-break to fallback on capacity faults."""
        for model_id in (self._primary_model, self._fallback_model):
            try:
                resp = self._bedrock_rt.converse(
                    modelId=model_id,
                    messages=[{"role": "user", "content": [{"text": prompt}]}],
                    guardrailConfig={
                        "guardrailIdentifier": GUARDRAIL_ID,
                        "guardrailVersion":    GUARDRAIL_VERSION,
                    },
                )
                return resp["output"]["message"]["content"][0]["text"], model_id
            except ClientError as exc:
                if exc.response["Error"]["Code"] in _CIRCUIT_BREAKER_FAULTS \
                        and model_id == self._primary_model:
                    logger.warning(
                        "[%s] 🚨 Circuit breaker tripped — failing over to %s",
                        self.agent_name.upper(), self._fallback_model,
                    )
                    continue
                raise
        raise RuntimeError(f"{self.agent_name}: both primary and fallback models failed")

    def _parse_response(self, text: str) -> dict:
        """Extract JSON from response text; fall back to raw string on parse error."""
        # Strip markdown code fences if present
        clean = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            return {"result": text, "confidence": 0.7}
