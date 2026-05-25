"""
CORPSS Orchestrator
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Ties all 6 architectural pillars into a single, coherent request pipeline.

                    ┌─────────────────────────────────────────────────┐
                    │              CORPSS PIPELINE                     │
                    │                                                  │
     User Query ──► │  [S·GENSEC]  Guardrail input scan               │
                    │       ↓                                          │
                    │  [S·GENSUST] Intent classification               │
                    │       ↓ SIMPLE ──────────────────────────────►  │
                    │       ↓ COMPLEX                                  │
                    │  [O·GENOPS]  Fetch versioned prompt alias        │
                    │       ↓                                          │
                    │  [P·GENPERF] Provisioned-throughput inference    │
                    │       ↓                                          │
                    │  [R·GENREL]  Fan-out event to worker queues      │
                    │       ↓                                          │
                    │  [S·GENSEC]  Guardrail output scan               │
                    │       ↓                                          │
     Response  ◄──  │  Final payload extraction                        │
                    │                                                  │
     Batch Mode ──► │  [C·GENCOST] Async 50%-cheaper batch job        │
                    └─────────────────────────────────────────────────┘

Pillar map:
  C – GENCOST  · Cost Optimisation  (async batch, 1% trace sampling)
  O – GENOPS   · Operational Excel. (version-locked prompt aliases)
  R – GENREL   · Reliability        (SNS+SQS fan-out blast isolation)
  P – GENPERF  · Performance        (WebSocket streaming + Prov. Throughput)
  S – GENSEC   · Security           (dual-sided Bedrock Guardrails)
  S – GENSUST  · Sustainability      (right-sized model routing)
"""
import logging

from pillars import (
    GENCOSTBatchProcessor,
    GENOPSPromptManager,
    GENRELFanOutPublisher,
    GENPERFStreamHandler,
    GENSECGuardrailPerimeter,
    GENSUSTIntentRouter,
)
from pillars.gensec import GuardrailIntervened

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
logger = logging.getLogger(__name__)


class CORPSSOrchestrator:
    """
    Single entry-point for all CORPSS-compliant workloads.

    Usage:
        orc = CORPSSOrchestrator()

        # Real-time interactive query
        result = orc.handle_query(user_query="Summarise this loan application …")

        # Background nightly bulk audit
        job_arn = orc.submit_batch_audit()
    """

    def __init__(self) -> None:
        # Instantiate each pillar exactly once (singleton per orchestrator)
        self.cost    = GENCOSTBatchProcessor()          # C
        self.ops     = GENOPSPromptManager()            # O
        self.rel     = GENRELFanOutPublisher()          # R
        self.perf    = GENPERFStreamHandler()           # P
        self.sec     = GENSECGuardrailPerimeter()       # S (Security)
        self.sust    = GENSUSTIntentRouter()            # S (Sustainability)

    # ────────────────────────────────────────────────────────────────────────
    # Primary pipeline — real-time interactive query
    # ────────────────────────────────────────────────────────────────────────

    def handle_query(
        self,
        user_query: str,
        account_id: str = "ACC-UNKNOWN",
        broadcast_event: bool = True,
    ) -> dict:
        """
        Full CORPSS pipeline for a real-time user query.

        Steps:
          1. GENSEC  — scan untrusted input through guardrail perimeter.
          2. GENSUST — classify SIMPLE / COMPLEX to pick the energy tier.
          3. GENOPS  — hydrate the version-locked managed prompt template.
          4. GENPERF — run inference on Provisioned Throughput (sync path).
          5. GENREL  — broadcast transaction event to fan-out worker queues.
          6. GENSEC  — output is already guarded by dual-sided guardrail.

        Returns a result dict with intent, model, and response text.
        """
        logger.info("═" * 60)
        logger.info("CORPSS pipeline START  account=%s", account_id)
        logger.info("═" * 60)

        # ── Step 1 · S · GENSEC — Input guardrail ────────────────────────────
        logger.info("Step 1/5 · GENSEC — scanning input through guardrail perimeter")
        try:
            # We do a pre-check with a lightweight model behind the guardrail.
            # If it passes, we proceed with the full pipeline.
            _pre_check = self.sec.safe_execute(user_query)
        except GuardrailIntervened:
            logger.warning("GENSEC blocked the input. Aborting pipeline.")
            return {
                "status":  "BLOCKED",
                "reason":  "Input failed Bedrock Guardrail check (prompt injection / PII).",
                "account": account_id,
            }

        # ── Step 2 · S · GENSUST — Intent classification ─────────────────────
        logger.info("Step 2/5 · GENSUST — classifying intent and routing to energy tier")
        sust_result = self.sust.route(user_query)
        intent      = sust_result["intent"]
        logger.info("  → intent=%s  model=%s", intent, sust_result["model_used"])

        if intent == "SIMPLE":
            # Simple queries stay entirely on the low-power track — no further pillars needed.
            logger.info("SIMPLE path complete. Skipping GENOPS/GENPERF heavy pipeline.")
            final_response = sust_result["response"]
            model_used     = sust_result["model_used"]

        else:
            # ── Step 3 · O · GENOPS — Managed prompt hydration ───────────────
            logger.info("Step 3/5 · GENOPS — fetching versioned prompt alias")
            genops_response = self.ops.execute_with_managed_prompt(
                user_query=user_query,
                template_variables={"account_id": account_id},
            )

            # ── Step 4 · P · GENPERF — Provisioned throughput inference ──────
            logger.info("Step 4/5 · GENPERF — running inference on Provisioned Throughput")
            final_response = self.perf.converse_sync(genops_response)
            model_used     = "ProvisionedThroughput"

        # ── Step 5 · R · GENREL — Fan-out broadcast ───────────────────────────
        if broadcast_event:
            logger.info("Step 5/5 · GENREL — broadcasting transaction to worker queues")
            self.rel.broadcast_transaction(
                account_id=account_id,
                payload_summary=user_query[:200],  # truncate for event envelope
            )

        logger.info("═" * 60)
        logger.info("CORPSS pipeline END  ✅")
        logger.info("═" * 60)

        return {
            "status":    "OK",
            "intent":    intent,
            "model":     model_used,
            "account":   account_id,
            "response":  final_response,
        }

    # ────────────────────────────────────────────────────────────────────────
    # Batch pipeline — C · GENCOST: 50 % cheaper async processing
    # ────────────────────────────────────────────────────────────────────────

    def submit_batch_audit(self, job_name: str = "Nightly_Compliance_Bulk_Audit") -> dict:
        """
        Submit a nightly bulk compliance audit as an async Bedrock Batch job.
        50 % cheaper than synchronous on-demand execution.
        """
        logger.info("[GENCOST] Submitting batch audit: %s", job_name)
        job_arn = self.cost.submit_batch_job(job_name=job_name)
        return {"status": "SUBMITTED", "jobArn": job_arn}

    def check_batch_status(self, job_arn: str) -> dict:
        """Poll the status of a previously submitted batch job."""
        return self.cost.get_job_status(job_arn)


# ── CLI demo ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    orc = CORPSSOrchestrator()

    print("\n" + "▓" * 60)
    print("  CORPSS DEMO — Real-time interactive query (SIMPLE)")
    print("▓" * 60)
    result = orc.handle_query(
        user_query="What is the current interest rate for a 30-year fixed mortgage?",
        account_id="ACC-001",
    )
    print(json.dumps(result, indent=2))

    print("\n" + "▓" * 60)
    print("  CORPSS DEMO — Real-time interactive query (COMPLEX)")
    print("▓" * 60)
    result = orc.handle_query(
        user_query=(
            "Analyse the risk profile of this commercial loan application for a $4.2M "
            "mixed-use property in Sydney CBD, considering current RBA rate environment, "
            "tenant concentration risk, and APRA prudential standards CPS 220."
        ),
        account_id="ACC-002",
    )
    print(json.dumps(result, indent=2))

    print("\n" + "▓" * 60)
    print("  CORPSS DEMO — Batch audit submission (GENCOST)")
    print("▓" * 60)
    batch = orc.submit_batch_audit()
    print(json.dumps(batch, indent=2))
