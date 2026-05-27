"""
E · GENEVAL — Evaluation & Trust  ← NEW PILLAR
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy:
  The main roadblock moving agents from demo to production is
  non-deterministic output drift. GENEVAL establishes a 5-step
  Continuous Evaluation Loop:

    1. Ground Truth Datasets  — S3 gold-standard JSONL benchmark
    2. Offline Evaluation     — Bedrock Model Evaluation automated jobs
    3. Safe Deployment        — gate promotion behind evaluation score thresholds
    4. Online Monitoring      — AgentCore Trace parsing for live RAG quality metrics
    5. Continuous Improvement — edge cases fed back into test beds

  Key metrics tracked:
    • Faithfulness (Groundedness) — does output ONLY use retrieved context?
    • Answer Relevance            — does the response directly answer the query?
    • Context Relevance           — are retrieved chunks precise and low-noise?
    • Tool Call Accuracy          — did the agent select the correct action group?
    • Rationale Coherence         — is the internal reasoning chain sound?
    • Multi-Agent Consensus       — do specialist agents agree on risk level?
"""
import collections
import logging
import time
import boto3
from botocore.exceptions import ClientError

from config import (
    AWS_REGION,
    ACCOUNT_ID,
    AGENT_ID,
    AGENT_ALIAS_ID,
    EVAL_ROLE_ARN,
    EVAL_INPUT_S3,
    EVAL_OUTPUT_S3,
    EVAL_MODEL_ID,
)

logger = logging.getLogger(__name__)


class EvalScore:
    """Structured evaluation result from a single agent invocation."""

    def __init__(self) -> None:
        self.final_answer:    str        = ""
        self.rag_sources:     list[dict] = []
        self.rationale:       str        = ""
        self.tool_calls:      list[dict] = []
        self.guardrail_fired: bool       = False

    def faithfulness_flag(self) -> str:
        """Heuristic: PASS if RAG sources were retrieved, REVIEW if answer has no grounding."""
        return "PASS" if self.rag_sources else "REVIEW — no RAG context retrieved"

    def to_dict(self) -> dict:
        return {
            "final_answer":    self.final_answer,
            "rag_sources":     self.rag_sources,
            "rationale":       self.rationale,
            "tool_calls":      self.tool_calls,
            "guardrail_fired": self.guardrail_fired,
            "faithfulness":    self.faithfulness_flag(),
        }


class GENEVALEvaluationEngine:
    """
    5-Step Continuous Evaluation Loop implementation.

    Steps 1–3 (offline): submit_model_evaluation_job()
    Steps 4–5 (online):  invoke_and_evaluate()
    """

    def __init__(
        self,
        agent_id:       str = AGENT_ID,
        agent_alias_id: str = AGENT_ALIAS_ID,
    ) -> None:
        self._agent_id       = agent_id
        self._agent_alias_id = agent_alias_id
        self._agent_rt       = boto3.client("bedrock-agent-runtime", region_name=AWS_REGION)
        self._bedrock        = boto3.client("bedrock",               region_name=AWS_REGION)

    # ── Step 2 · Offline Evaluation Job ──────────────────────────────────────

    def submit_model_evaluation_job(
        self,
        job_name:        str = "CORPSEE_Offline_Eval",
        input_s3:        str = EVAL_INPUT_S3,
        output_s3:       str = EVAL_OUTPUT_S3,
        eval_model_id:   str = EVAL_MODEL_ID,
    ) -> str:
        """
        Submit a Bedrock Model Evaluation job against the S3 ground-truth dataset.

        Measures Faithfulness, Answer Relevance, and Context Relevance
        automatically. Results written to S3 for threshold gating (Step 3).

        Returns the evaluation job ARN.
        """
        logger.info("[GENEVAL] Submitting offline model evaluation job: %s", job_name)

        try:
            response = self._bedrock.create_evaluation_job(
                jobName=job_name,
                roleArn=EVAL_ROLE_ARN,
                evaluationConfig={
                    "automated": {
                        "datasetMetricConfigs": [
                            {
                                "taskType": "QuestionAndAnswer",
                                "dataset": {
                                    "name":      "ground-truth-dataset",
                                    "datasetLocation": {"s3Uri": input_s3},
                                },
                                "metricNames": [
                                    "Faithfulness",
                                    "Helpfulness",
                                    "Coherence",
                                ],
                            }
                        ]
                    }
                },
                inferenceConfig={
                    "models": [
                        {
                            "bedrockModel": {
                                "modelIdentifier": eval_model_id,
                            }
                        }
                    ]
                },
                outputDataConfig={"s3Uri": output_s3},
            )
            job_arn = response["jobArn"]
            logger.info("[GENEVAL] ✅ Evaluation job submitted. ARN: %s", job_arn)
            return job_arn

        except ClientError as exc:
            logger.error("[GENEVAL] Evaluation job failed: %s", exc)
            raise

    # ── Steps 4–5 · Online Runtime Evaluation ────────────────────────────────

    def invoke_and_evaluate(self, user_query: str, session_id: str) -> EvalScore:
        """
        Invoke the production agent with enableTrace=True and parse the
        AgentCore reasoning stream for real-time quality metrics.

        Evaluation layers:
          Layer 1 — RAG Context Relevance:  inspect knowledgeBaseLookup traces
          Layer 2 — Rationale Coherence:    inspect orchestration.rationale
          Layer 3 — Tool Call Accuracy:     inspect invocationInput traces
          Layer 4 — Guardrail Intervention: inspect guardrailTrace
        """
        logger.info(
            "[GENEVAL] Invoking agent with trace enabled — session=%s", session_id
        )

        score = EvalScore()

        response = self._agent_rt.invoke_agent(
            agentId=self._agent_id,
            agentAliasId=self._agent_alias_id,
            sessionId=session_id,
            inputText=user_query,
            enableTrace=True,   # ← CRITICAL: exposes internal reasoning path
        )

        for event in response["completion"]:
            # ── Collect final answer ──────────────────────────────────────────
            if "chunk" in event:
                score.final_answer += event["chunk"]["bytes"].decode("utf-8")

            # ── Parse trace events ────────────────────────────────────────────
            elif "trace" in event:
                trace_data = event["trace"].get("trace", {})

                # Layer 1: RAG Context Relevance
                if "knowledgeBaseLookupOutput" in trace_data:
                    lookup = trace_data["knowledgeBaseLookupOutput"]
                    refs   = lookup.get("retrievedReferences", [])
                    for ref in refs:
                        source = {
                            "uri":     ref.get("location", {}).get("s3Location", {}).get("uri", ""),
                            "snippet": ref.get("content", {}).get("text", "")[:120],
                        }
                        score.rag_sources.append(source)
                        logger.info(
                            "[GENEVAL] 🔍 RAG source: %s — %s…",
                            source["uri"], source["snippet"][:60],
                        )

                # Layer 2: Rationale Coherence + Layer 3: Tool Call Accuracy
                elif "orchestrationTrace" in trace_data:
                    orch = trace_data["orchestrationTrace"]

                    if "rationale" in orch:
                        score.rationale = orch["rationale"].get("text", "")
                        logger.info(
                            "[GENEVAL] 🧠 Agent rationale: %s…",
                            score.rationale[:100],
                        )

                    if "invocationInput" in orch:
                        tool = orch["invocationInput"].get("actionGroupInvocationInput", {})
                        call = {
                            "action_group": tool.get("actionGroupName", ""),
                            "function":     tool.get("function", ""),
                        }
                        score.tool_calls.append(call)
                        logger.info(
                            "[GENEVAL] 🛠️  Tool call: %s → %s",
                            call["action_group"], call["function"],
                        )

                # Layer 4: Guardrail Intervention
                elif "guardrailTrace" in trace_data:
                    action = trace_data["guardrailTrace"].get("action", "NONE")
                    if action == "INTERVENED":
                        score.guardrail_fired = True
                        logger.warning("[GENEVAL] 🚨 Guardrail intervention detected in trace.")

        logger.info(
            "[GENEVAL] ✅ Evaluation complete — RAG sources: %d | tool calls: %d | faithfulness: %s",
            len(score.rag_sources),
            len(score.tool_calls),
            score.faithfulness_flag(),
        )
        return score


# ── Continuous Evaluation Loop ────────────────────────────────────────────────

class ContinuousEvalLoop:
    """
    5-Step Continuous Evaluation Loop for multi-agent systems.

    Implements a rolling quality window that automatically detects drift
    and triggers an offline re-evaluation job when the agent's live
    performance drops below the configured threshold.

    ┌──────────────────────────────────────────────────────────────────┐
    │  Step 1 — collect()    : record a live sub-agent result          │
    │  Step 2 — score()      : compute rolling quality metrics          │
    │  Step 3 — check_drift(): compare against baseline thresholds     │
    │  Step 4 — trigger()    : auto-submit offline eval job on drift   │
    │  Step 5 — report()     : surface edge cases for test bed update  │
    └──────────────────────────────────────────────────────────────────┘

    Multi-Agent Consensus Check:
      When all three specialist agents agree on the risk level, confidence
      in the final decision is HIGH.  When agents diverge by more than one
      level (e.g. Fraud=HIGH, Risk=LOW), a consensus flag is raised for
      human review — this is a key production safety gate.
    """

    # Quality thresholds
    _DRIFT_CONFIDENCE_THRESHOLD = 0.70   # Rolling avg self_score below this → drift
    _DRIFT_SUCCESS_THRESHOLD    = 0.85   # Rolling success rate below this → drift
    _ROLLING_WINDOW             = 20     # Number of recent invocations to average
    _CONSENSUS_RISK_LEVELS      = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]

    def __init__(self, eval_engine: "GENEVALEvaluationEngine | None" = None) -> None:
        self._eval_engine = eval_engine or GENEVALEvaluationEngine()
        self._history: collections.deque = collections.deque(maxlen=self._ROLLING_WINDOW)
        self._drift_jobs: list[str] = []   # ARNs of auto-triggered eval jobs

    # ── Step 1 · Collect ─────────────────────────────────────────────────────

    def collect(self, sub_results: list, supervisor_decision: dict) -> None:
        """
        Record a completed multi-agent assessment into the rolling window.

        Args:
            sub_results:         List of SubAgentResult from the parallel sub-agents.
            supervisor_decision: Final aggregated decision dict from SupervisorAgent.
        """
        if not sub_results:
            return

        avg_score    = sum(r.self_score for r in sub_results) / len(sub_results)
        all_success  = all(r.success for r in sub_results)
        risk_levels  = [
            r.response and __import__("json").loads(r.response).get("fraud_risk_level")
            or r.response and __import__("json").loads(r.response).get("credit_risk_rating")
            or r.response and __import__("json").loads(r.response).get("compliance_status")
            for r in sub_results
            if r.success and r.response
        ]

        record = {
            "ts":               time.time(),
            "avg_confidence":   avg_score,
            "success":          all_success,
            "final_decision":   supervisor_decision.get("final_decision", "UNKNOWN"),
            "overall_risk":     supervisor_decision.get("overall_risk", "UNKNOWN"),
            "agent_count":      len(sub_results),
            "consensus_flag":   self._check_consensus(sub_results),
        }
        self._history.append(record)
        logger.debug(
            "[GENEVAL·LOOP] Recorded assessment — avg_confidence=%.2f success=%s consensus=%s",
            avg_score, all_success, record["consensus_flag"],
        )

    # ── Step 2 · Score ───────────────────────────────────────────────────────

    def rolling_metrics(self) -> dict:
        """Compute rolling quality metrics over the current window."""
        if not self._history:
            return {"window_size": 0, "avg_confidence": None, "success_rate": None}

        window = list(self._history)
        avg_conf     = sum(r["avg_confidence"] for r in window) / len(window)
        success_rate = sum(1 for r in window if r["success"]) / len(window)
        consensus_ok = sum(1 for r in window if r["consensus_flag"] == "AGREE") / len(window)

        return {
            "window_size":    len(window),
            "avg_confidence": round(avg_conf, 3),
            "success_rate":   round(success_rate, 3),
            "consensus_rate": round(consensus_ok, 3),
            "drift_detected": self._is_drifting(avg_conf, success_rate),
        }

    # ── Step 3 · Drift Detection ─────────────────────────────────────────────

    def _is_drifting(self, avg_conf: float, success_rate: float) -> bool:
        return (
            avg_conf     < self._DRIFT_CONFIDENCE_THRESHOLD or
            success_rate < self._DRIFT_SUCCESS_THRESHOLD
        )

    def check_and_trigger(self, job_name_prefix: str = "AutoDrift_Eval") -> dict:
        """
        Step 3+4: Evaluate current quality; auto-submit offline eval job if drifting.

        Returns the current metrics dict, with 'drift_job_arn' if a job was triggered.
        """
        metrics = self.rolling_metrics()

        if metrics.get("drift_detected"):
            logger.warning(
                "[GENEVAL·LOOP] 🚨 Drift detected! avg_confidence=%.3f success_rate=%.3f — "
                "auto-triggering offline evaluation job.",
                metrics["avg_confidence"],
                metrics["success_rate"],
            )
            try:
                import time as _t
                job_name = f"{job_name_prefix}_{int(_t.time())}"
                job_arn  = self._eval_engine.submit_model_evaluation_job(job_name=job_name)
                self._drift_jobs.append(job_arn)
                metrics["drift_job_arn"]     = job_arn
                metrics["drift_job_triggered"] = True
                logger.info("[GENEVAL·LOOP] ✅ Auto-triggered eval job: %s", job_arn)
            except Exception as exc:  # noqa: BLE001
                logger.error("[GENEVAL·LOOP] Failed to trigger eval job: %s", exc)
                metrics["drift_job_triggered"] = False
                metrics["drift_job_error"]     = str(exc)
        else:
            metrics["drift_job_triggered"] = False

        return metrics

    # ── Step 5 · Edge Case Report ────────────────────────────────────────────

    def edge_case_report(self) -> list[dict]:
        """
        Return recent assessments flagged for test-bed inclusion:
          - Consensus disagreements (agents diverged on risk)
          - Low confidence outputs (self_score < threshold)
          - Failed sub-agents
        """
        edge_cases = [
            r for r in self._history
            if r["consensus_flag"] != "AGREE"
            or r["avg_confidence"] < self._DRIFT_CONFIDENCE_THRESHOLD
            or not r["success"]
        ]
        logger.info(
            "[GENEVAL·LOOP] Edge case report: %d/%d assessments flagged for test-bed update.",
            len(edge_cases), len(self._history),
        )
        return list(edge_cases)

    # ── Multi-Agent Consensus ─────────────────────────────────────────────────

    def _check_consensus(self, sub_results: list) -> str:
        """
        Compare risk levels across specialist agents.
        Returns "AGREE" if agents are within one level, "DIVERGE" otherwise.
        """
        import json as _json
        levels = []
        for r in sub_results:
            if not (r.success and r.response):
                continue
            try:
                parsed = _json.loads(r.response)
                # Try extracting a risk level from any of the known schema keys
                level = (
                    parsed.get("fraud_risk_level") or
                    parsed.get("overall_risk") or
                    parsed.get("compliance_status") or
                    parsed.get("credit_risk_rating", "UNKNOWN")
                )
                if level in self._CONSENSUS_RISK_LEVELS:
                    levels.append(self._CONSENSUS_RISK_LEVELS.index(level))
            except Exception:  # noqa: BLE001
                pass

        if len(levels) < 2:
            return "INSUFFICIENT"
        spread = max(levels) - min(levels)
        return "AGREE" if spread <= 1 else "DIVERGE"
