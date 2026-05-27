"""
CORPSEE Orchestrator — 7-Pillar GenAI Well-Architected Framework
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ties all 7 CORPSEE pillars into a single, coherent request pipeline.

                    ┌─────────────────────────────────────────────────┐
                    │              CORPSEE PIPELINE                    │
                    │                                                  │
     User Query ──► │  [S·GENSEC]   Guardrail input scan              │
                    │       ↓                                          │
                    │  [E·GENSUST]  Intent classification              │
                    │       ↓ SIMPLE ──────────────────────────────►  │
                    │       ↓ COMPLEX                                  │
                    │  [O·GENOPS]   Fetch versioned prompt alias       │
                    │       ↓                                          │
                    │  [R·GENREL]   Circuit breaker inference          │
                    │       ↓                                          │
                    │  [P·GENPERF]  AgentCore Harness / stream        │
                    │       ↓                                          │
                    │  [R·GENREL]   Fan-out event to worker queues     │
                    │       ↓                                          │
                    │  [E·GENEVAL]  Runtime trace evaluation           │
                    │       ↓                                          │
     Response  ◄──  │  Final payload                                   │
                    │                                                  │
     Multi-Agent ►  │  [R·GENREL]   Supervisor → parallel sub-agents  │
     Pipeline       │  [E·GENEVAL]  Continuous eval loop + drift gate │
                    │                                                  │
     Batch Mode ──► │  [C·GENCOST]  Async batch + prompt caching      │
     Eval Mode  ──► │  [E·GENEVAL]  Offline evaluation job            │
                    └─────────────────────────────────────────────────┘

Pillar map (CORPSEE):
  C – GENCOST  · Cost Optimisation     (batch, prompt caching, 1% trace)
  O – GENOPS   · Operational Excel.    (version-locked prompt aliases, OTEL)
  R – GENREL   · Reliability           (fan-out, circuit breaker, multi-agent coord)
  P – GENPERF  · Performance           (AgentCore Harness, WebSocket stream)
  S – GENSEC   · Security              (dual-sided guardrails, microVM isolation)
  E – GENEVAL  · Evaluation & Trust    (5-step eval loop, trace scoring, drift gate)
  E – GENSUST  · Sustainability        (right-sized model routing)
"""
import logging
import uuid

from pillars import (
    GENCOSTBatchProcessor,
    GENOPSPromptManager,
    GENRELFanOutPublisher,
    GENRELCircuitBreaker,
    GENRELMultiAgentCoordinator,
    GENPERFStreamHandler,
    GENSECGuardrailPerimeter,
    GENEVALEvaluationEngine,
    ContinuousEvalLoop,
    GENSUSTIntentRouter,
)
from pillars.gensec import GuardrailIntervened

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


class CORPSEEOrchestrator:
    """
    Single entry-point for all CORPSEE-compliant workloads.

    Usage:
        orc = CORPSEEOrchestrator()

        # Real-time interactive query (single-agent path)
        result = orc.handle_query(user_query="...", account_id="ACC-001")

        # Multi-agent loan assessment (Supervisor → 3 specialist sub-agents)
        decision = orc.handle_multi_agent(query="...", account_id="ACC-001")

        # Background nightly bulk audit (GENCOST)
        job_arn = orc.submit_batch_audit()

        # Offline evaluation job (GENEVAL)
        eval_arn = orc.submit_evaluation_job()

        # Continuous eval loop quality report
        metrics = orc.eval_loop.rolling_metrics()
    """

    def __init__(self) -> None:
        self.cost         = GENCOSTBatchProcessor()           # C
        self.ops          = GENOPSPromptManager()             # O
        self.rel_pub      = GENRELFanOutPublisher()           # R — fan-out
        self.rel_cb       = GENRELCircuitBreaker()            # R — circuit breaker
        self.rel_ma       = GENRELMultiAgentCoordinator()     # R — multi-agent coord
        self.perf         = GENPERFStreamHandler()            # P
        self.sec          = GENSECGuardrailPerimeter()        # S
        self.eval         = GENEVALEvaluationEngine()         # E (Evaluation)
        self.eval_loop    = ContinuousEvalLoop(self.eval)     # E (Continuous loop)
        self.sust         = GENSUSTIntentRouter()             # E (Sustainability)

    # ────────────────────────────────────────────────────────────────────────
    # Primary pipeline — real-time interactive query (single-agent)
    # ────────────────────────────────────────────────────────────────────────

    def handle_query(
        self,
        user_query:        str,
        account_id:        str  = "ACC-UNKNOWN",
        session_id:        str  = "SESSION-DEFAULT",
        broadcast_event:   bool = True,
        run_eval:          bool = False,
    ) -> dict:
        """
        Full CORPSEE pipeline for a real-time user query (single-agent path).

        Steps:
          1. GENSEC  — scan untrusted input through guardrail perimeter.
          2. GENSUST — classify SIMPLE / COMPLEX to pick the energy tier.
          3. GENOPS  — hydrate the version-locked managed prompt template.
          4. GENREL  — circuit breaker inference (PT primary → serverless fallback).
          5. GENREL  — broadcast transaction event to fan-out worker queues.
          6. GENEVAL — (optional) invoke agent with tracing for runtime evaluation.
        """
        logger.info("═" * 62)
        logger.info("CORPSEE pipeline START  account=%s session=%s", account_id, session_id)
        logger.info("═" * 62)

        # ── Step 1 · S · GENSEC ───────────────────────────────────────────────
        logger.info("Step 1 · GENSEC — dual-sided guardrail input scan")
        try:
            self.sec.safe_execute(user_query)
        except GuardrailIntervened:
            logger.warning("GENSEC blocked input. Aborting pipeline.")
            return {
                "status":  "BLOCKED",
                "reason":  "Input failed Bedrock Guardrail check (prompt injection / PII).",
                "account": account_id,
            }

        # ── Step 2 · E · GENSUST ─────────────────────────────────────────────
        logger.info("Step 2 · GENSUST — intent classification and energy-tier routing")
        sust_result = self.sust.route(user_query)
        intent      = sust_result["intent"]
        logger.info("  → intent=%s  model=%s", intent, sust_result["model_used"])

        if intent == "SIMPLE":
            logger.info("SIMPLE path — staying on low-power compute track.")
            return {
                "status":   "OK",
                "intent":   "SIMPLE",
                "model":    sust_result["model_used"],
                "account":  account_id,
                "session":  session_id,
                "response": sust_result["response"],
            }

        # ── Step 3 · O · GENOPS ───────────────────────────────────────────────
        logger.info("Step 3 · GENOPS — fetching PROD-ACTIVE prompt alias")
        prompt_meta = self.ops.get_prompt_metadata()
        genops_response = self.ops.execute_with_managed_prompt(
            user_query=user_query,
            template_variables={"account_id": account_id},
            trace_context={"trace_id": session_id},
        )
        logger.info("  → prompt=%s version=%s", prompt_meta.get("prompt_name"), prompt_meta.get("prompt_version"))

        # ── Step 4 · R · GENREL — Circuit Breaker Inference ──────────────────
        logger.info("Step 4 · GENREL — circuit breaker inference (PT → serverless fallback)")
        cb_result      = self.rel_cb.reliable_inference(genops_response)
        final_response = cb_result["response"]
        logger.info("  → path=%s  model=%s", cb_result["path"], cb_result["model_used"])

        # ── Step 5 · R · GENREL — Fan-Out Broadcast ──────────────────────────
        if broadcast_event:
            logger.info("Step 5 · GENREL — broadcasting to fan-out worker queues")
            self.rel_pub.broadcast_transaction(
                account_id=account_id,
                payload_summary=user_query[:200],
            )

        # ── Step 6 · E · GENEVAL — Runtime Evaluation (optional) ─────────────
        eval_result = None
        if run_eval:
            logger.info("Step 6 · GENEVAL — runtime trace evaluation")
            try:
                score      = self.eval.invoke_and_evaluate(user_query, session_id)
                eval_result = score.to_dict()
                logger.info(
                    "  → faithfulness=%s  rag_sources=%d  tool_calls=%d",
                    score.faithfulness_flag(),
                    len(score.rag_sources),
                    len(score.tool_calls),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("GENEVAL trace evaluation skipped: %s", exc)
                eval_result = {"status": "skipped", "reason": str(exc)}

        logger.info("═" * 62)
        logger.info("CORPSEE pipeline END  ✅")
        logger.info("═" * 62)

        return {
            "status":      "OK",
            "intent":      intent,
            "model":       cb_result["model_used"],
            "path":        cb_result["path"],
            "account":     account_id,
            "session":     session_id,
            "response":    final_response,
            "eval":        eval_result,
            "prompt_meta": prompt_meta,
        }

    # ────────────────────────────────────────────────────────────────────────
    # Multi-Agent pipeline — Supervisor → Fraud + Compliance + Risk
    # ────────────────────────────────────────────────────────────────────────

    def handle_multi_agent(
        self,
        query:          str,
        account_id:     str  = "ACC-UNKNOWN",
        session_id:     str | None = None,
        broadcast_event: bool = True,
    ) -> dict:
        """
        Full CORPSEE multi-agent pipeline for complex loan application assessments.

        Macro Orchestration (GENREL):
          Supervisor Agent decomposes query → fires Fraud + Compliance + Risk
          sub-agents in parallel (each in isolated AgentCore microVM session).
          GENRELMultiAgentCoordinator gates results on quality thresholds.

        Continuous Evaluation (GENEVAL):
          ContinuousEvalLoop records every assessment, detects drift, and
          auto-triggers an offline Bedrock Evaluation job when quality drops.

        Steps:
          1. GENSEC  — guardrail input scan
          2. GENREL  — multi-agent orchestration (Supervisor + 3 sub-agents)
          3. GENREL  — fan-out broadcast
          4. GENEVAL — record to continuous eval loop + drift check
        """
        session_id = session_id or f"ma-{uuid.uuid4().hex[:8]}"

        logger.info("▶▶▶ MULTI-AGENT pipeline START  account=%s session=%s", account_id, session_id)

        # ── Step 1 · S · GENSEC ───────────────────────────────────────────────
        logger.info("Step 1 · GENSEC — guardrail input scan")
        try:
            self.sec.safe_execute(query)
        except GuardrailIntervened:
            logger.warning("GENSEC blocked input. Aborting multi-agent pipeline.")
            return {
                "status":  "BLOCKED",
                "reason":  "Input failed Bedrock Guardrail check.",
                "account": account_id,
            }

        # ── Step 2 · R · GENREL — Multi-Agent Orchestration ──────────────────
        logger.info("Step 2 · GENREL — Supervisor → [Fraud | Compliance | Risk] parallel dispatch")
        ma_result = self.rel_ma.orchestrate(query=query, session_id=session_id)

        decision        = ma_result["decision"]
        health_summary  = ma_result["health_summary"]
        reliability     = ma_result["reliability"]

        logger.info(
            "  → decision=%s  risk=%s  reliability=%s  latency=%.0fms",
            decision.get("final_decision"),
            decision.get("overall_risk"),
            reliability,
            ma_result["total_ms"],
        )

        # ── Step 3 · R · GENREL — Fan-Out Broadcast ──────────────────────────
        if broadcast_event:
            logger.info("Step 3 · GENREL — fan-out broadcast of assessment event")
            try:
                self.rel_pub.broadcast_transaction(
                    account_id=account_id,
                    payload_summary=f"MultiAgent assessment: {decision.get('final_decision')} — {query[:150]}",
                    tier="HighRisk" if decision.get("overall_risk") in ("HIGH", "CRITICAL") else "Standard",
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Fan-out broadcast skipped: %s", exc)

        # ── Step 4 · E · GENEVAL — Continuous Eval Loop ──────────────────────
        logger.info("Step 4 · GENEVAL — recording to continuous eval loop")
        sub_results = ma_result["decision"].get("sub_agents", [])
        # Convert dicts back to lightweight proxy objects for eval_loop.collect()
        class _R:
            def __init__(self, d):
                self.agent_name  = d["agent"]
                self.self_score  = d["self_score"]
                self.success     = d["success"]
                self.response    = d["response"]
        proxy_results = [_R(s) for s in sub_results]
        self.eval_loop.collect(proxy_results, decision)

        # Check for drift and auto-trigger offline eval if needed
        drift_metrics = self.eval_loop.check_and_trigger()
        logger.info(
            "  → rolling avg_confidence=%.3f  success_rate=%.3f  drift=%s",
            drift_metrics.get("avg_confidence") or 0,
            drift_metrics.get("success_rate") or 0,
            drift_metrics.get("drift_detected"),
        )

        logger.info("▶▶▶ MULTI-AGENT pipeline END  ✅")

        return {
            "status":        "OK",
            "pipeline":      "MULTI_AGENT",
            "account":       account_id,
            "session":       session_id,
            "decision":      decision,
            "health":        health_summary,
            "reliability":   reliability,
            "eval_metrics":  drift_metrics,
            "latency_ms":    ma_result["total_ms"],
        }

    # ────────────────────────────────────────────────────────────────────────
    # Batch pipeline — C · GENCOST
    # ────────────────────────────────────────────────────────────────────────

    def submit_batch_audit(self, job_name: str = "Nightly_Compliance_Bulk_Audit") -> dict:
        """Submit a nightly bulk compliance audit as an async Bedrock Batch job (50% cheaper)."""
        logger.info("[GENCOST] Submitting batch audit: %s", job_name)
        job_arn = self.cost.submit_batch_job(job_name=job_name)
        return {"status": "SUBMITTED", "jobArn": job_arn}

    def check_batch_status(self, job_arn: str) -> dict:
        return self.cost.get_job_status(job_arn)

    # ────────────────────────────────────────────────────────────────────────
    # Evaluation pipeline — E · GENEVAL
    # ────────────────────────────────────────────────────────────────────────

    def submit_evaluation_job(self, job_name: str = "CORPSEE_Offline_Eval") -> dict:
        """
        Submit a Bedrock offline Model Evaluation job against the S3 ground-truth dataset.
        Measures Faithfulness, Helpfulness, and Coherence automatically.
        """
        logger.info("[GENEVAL] Submitting offline evaluation job: %s", job_name)
        job_arn = self.eval.submit_model_evaluation_job(job_name=job_name)
        return {"status": "SUBMITTED", "jobArn": job_arn}

    def eval_quality_report(self) -> dict:
        """Return rolling quality metrics and edge cases from the continuous eval loop."""
        return {
            "metrics":     self.eval_loop.rolling_metrics(),
            "edge_cases":  self.eval_loop.edge_case_report(),
        }


# ── CLI demo ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    orc = CORPSEEOrchestrator()

    print("\n" + "▓" * 64)
    print("  CORPSEE DEMO — Real-time SIMPLE query")
    print("▓" * 64)
    result = orc.handle_query(
        user_query="What is the current RBA cash rate?",
        account_id="ACC-001",
        session_id="SESSION-001",
    )
    print(json.dumps(result, indent=2))

    print("\n" + "▓" * 64)
    print("  CORPSEE DEMO — Multi-Agent Loan Assessment")
    print("▓" * 64)
    ma_result = orc.handle_multi_agent(
        query=(
            "Loan application: John Smith, 42, employed as a civil engineer ($185,000 p.a.). "
            "Seeking $1.2M mortgage for a property in Mosman NSW. Property valuation: $1.5M. "
            "LVR: 80%. Current debts: $45,000 car loan, $12,000 credit card. "
            "No prior defaults. Two additional loan enquiries in the past 30 days."
        ),
        account_id="ACC-002",
        session_id="SESSION-MA-001",
    )
    print(json.dumps(ma_result, indent=2))

    print("\n" + "▓" * 64)
    print("  CORPSEE DEMO — Eval Quality Report")
    print("▓" * 64)
    print(json.dumps(orc.eval_quality_report(), indent=2))
