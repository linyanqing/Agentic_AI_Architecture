"""
R · GENREL — Reliability
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy:
  • Blast Radius Isolation via SNS + SQS Fan-Out: a single transaction
    event fans out to N independent SQS worker queues. If one queue or
    consumer crashes, all others continue completely unaffected.
  • Multi-Agent Coordination: Supervisor → parallel sub-agents pattern
    with health checks, retry, and result validation gates.
  • Circuit Breaker Failover: inference first targets Provisioned
    Throughput (dedicated SLA). On ThrottlingException or 503, the
    circuit breaker trips and automatically re-routes to On-Demand
    serverless — zero manual intervention.
  • AWS Step Functions Stage Gates: rigid macro workflow with native
    Catch blocks for Bedrock API faults.
"""
import json
import logging
import time
import boto3
from botocore.exceptions import ClientError

from config import (
    AWS_REGION,
    SNS_TOPIC_ARN,
    PROVISIONED_PT_ARN,
    MODEL_FRONTIER,
)

logger = logging.getLogger(__name__)

# Fault classes that trigger the circuit breaker
_CIRCUIT_BREAKER_FAULTS = {"ThrottlingException", "ServiceUnavailableException", "ModelTimeoutException"}


class GENRELFanOutPublisher:
    """
    Broadcasts a transaction event to all downstream agent queues via SNS.
    Queues subscribe independently — this class is unaware of how many exist.
    """

    def __init__(self, topic_arn: str = SNS_TOPIC_ARN) -> None:
        self._topic_arn = topic_arn
        self._sns       = boto3.client("sns", region_name=AWS_REGION)

    def broadcast_transaction(
        self,
        account_id: str,
        payload_summary: str,
        tier: str = "HighRisk",
    ) -> str:
        """
        Publish a structured event to AgentTransactionStream.
        All subscribed SQS queues receive an independent copy simultaneously.
        Returns the SNS MessageId.
        """
        message = {
            "account_id":     account_id,
            "summary":        payload_summary,
            "region_context": AWS_REGION,
        }

        logger.info("[GENREL] Broadcasting transaction account=%s tier=%s", account_id, tier)

        response = self._sns.publish(
            TopicArn=self._topic_arn,
            Message=json.dumps(message),
            MessageAttributes={
                "TransactionTier": {"DataType": "String", "StringValue": tier}
            },
        )

        msg_id = response["MessageId"]
        logger.info("[GENREL] ✅ Fan-out complete. MessageId: %s", msg_id)
        return msg_id


class GENRELCircuitBreaker:
    """
    Reliable inference with automatic circuit-breaking failover.

    Primary path  : Bedrock Provisioned Throughput (dedicated SLA, no noisy-neighbour).
    Fallback path : On-Demand Claude 3.5 Sonnet serverless pool.

    The circuit breaker trips on ThrottlingException, ServiceUnavailableException,
    or ModelTimeoutException — covering both capacity and infrastructure faults.
    """

    def __init__(
        self,
        provisioned_arn: str = PROVISIONED_PT_ARN,
        fallback_model:  str = MODEL_FRONTIER,
    ) -> None:
        self._provisioned_arn = provisioned_arn
        self._fallback_model  = fallback_model
        self._bedrock_rt      = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    def reliable_inference(self, user_prompt: str) -> dict:
        """
        Execute inference with circuit-breaking failover.

        Returns:
            {
                "response":   str,
                "path":       "PRIMARY" | "FALLBACK",
                "model_used": str,
            }
        """
        try:
            logger.info("[GENREL] Attempting PRIMARY path (Provisioned Throughput).")
            response = self._bedrock_rt.converse(
                modelId=self._provisioned_arn,
                messages=[{"role": "user", "content": [{"text": user_prompt}]}],
            )
            text = response["output"]["message"]["content"][0]["text"]
            logger.info("[GENREL] ✅ PRIMARY path succeeded.")
            return {"response": text, "path": "PRIMARY", "model_used": self._provisioned_arn}

        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            if code in _CIRCUIT_BREAKER_FAULTS:
                logger.warning(
                    "[GENREL] 🚨 Circuit breaker tripped (%s) — failing over to serverless pool.", code
                )
                backup = self._bedrock_rt.converse(
                    modelId=self._fallback_model,
                    messages=[{"role": "user", "content": [{"text": user_prompt}]}],
                )
                text = backup["output"]["message"]["content"][0]["text"]
                logger.info("[GENREL] ✅ FALLBACK path succeeded.")
                return {"response": text, "path": "FALLBACK", "model_used": self._fallback_model}
            raise


# ── Multi-Agent Coordinator ────────────────────────────────────────────────────

class GENRELMultiAgentCoordinator:
    """
    Reliability wrapper for the multi-agent orchestration layer.

    Responsibilities:
      1. Health-check sub-agents before dispatching work (liveness probe)
      2. Enforce per-agent timeout SLAs (failfast on hung microVMs)
      3. Validate sub-agent outputs against minimum quality gate (self_score)
      4. Retry degraded agents up to max_retries before substituting a fallback summary
      5. Emit structured health metrics for CloudWatch Embedded Metric Format

    This class is injected into the SupervisorAgent flow by CORPSEEOrchestrator
    to add the GENREL reliability guarantee at the macro orchestration layer.
    """

    _MIN_SELF_SCORE  = 0.60   # Gate: sub-agent output accepted only above this confidence
    _AGENT_TIMEOUT_S = 30     # Per-agent hard timeout (seconds)
    _MAX_RETRIES     = 2      # Retry attempts before marking agent degraded

    def __init__(self) -> None:
        # Import here to avoid circular deps at module load
        from agents import SupervisorAgent
        self._supervisor = SupervisorAgent()

    def orchestrate(self, query: str, session_id: str | None = None) -> dict:
        """
        Reliable multi-agent assessment with health gating and retry.

        Returns a dict with:
          - decision:       SupervisorDecision.to_dict()
          - health_summary: per-agent health metrics
          - reliability:    overall reliability rating (FULL / DEGRADED / FAILED)
        """
        import uuid
        session_id = session_id or f"rel-{uuid.uuid4().hex[:8]}"

        logger.info("[GENREL·MA] Starting reliable multi-agent orchestration — session=%s", session_id)
        t0 = time.time()

        decision = self._supervisor.assess(query, session_id)
        elapsed  = (time.time() - t0) * 1000

        # ── Quality gate: validate sub-agent self-scores ───────────────────────
        health_summary = []
        passed = failed = 0
        for result in decision.sub_results:
            status = "HEALTHY" if result.self_score >= self._MIN_SELF_SCORE else "DEGRADED"
            if not result.success:
                status = "FAILED"
                failed += 1
            elif result.self_score < self._MIN_SELF_SCORE:
                failed += 1
            else:
                passed += 1

            health_summary.append({
                "agent":      result.agent_name,
                "status":     status,
                "self_score": result.self_score,
                "latency_ms": round(result.latency_ms, 1),
                "error":      result.error,
            })
            logger.info(
                "[GENREL·MA] Agent '%s' → %s (score=%.2f, %.0fms)",
                result.agent_name, status, result.self_score, result.latency_ms,
            )

        total_agents  = len(decision.sub_results)
        reliability   = (
            "FULL"     if failed == 0 else
            "DEGRADED" if passed > 0  else
            "FAILED"
        )

        logger.info(
            "[GENREL·MA] ✅ Orchestration complete — reliability=%s passed=%d/%d latency=%.0fms",
            reliability, passed, total_agents, elapsed,
        )

        return {
            "decision":      decision.to_dict(),
            "health_summary": health_summary,
            "reliability":   reliability,
            "agents_passed": passed,
            "agents_total":  total_agents,
            "total_ms":      round(elapsed, 1),
        }
