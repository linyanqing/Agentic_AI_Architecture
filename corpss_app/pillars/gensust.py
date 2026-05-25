"""
S · GENSUST — Sustainability
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy:
  • Right-size every request: use the smallest model capable of handling it.
  • SIMPLE tasks stay on Amazon Nova Micro — runs on AWS Trainium /
    Inferentia chips, consuming significantly less power per token.
  • Only COMPLEX reasoning escalates to Claude 3.5 Sonnet (frontier model).
  • This reduces carbon footprint and energy cost per transaction without
    any loss in output quality for the appropriate task tier.
"""
import logging
import boto3
from botocore.exceptions import ClientError

from config import AWS_REGION, MODEL_LIGHTWEIGHT, MODEL_FRONTIER

logger = logging.getLogger(__name__)

# Classification tokens the router must return
INTENT_SIMPLE  = "SIMPLE"
INTENT_COMPLEX = "COMPLEX"


class GENSUSTIntentRouter:
    """
    Two-stage energy-minimised inference router.

    Stage 1 — Lightweight classifier (Nova Micro, low-power hardware track):
      Categorises user intent as SIMPLE or COMPLEX.

    Stage 2 — Conditional escalation:
      SIMPLE  → handled entirely by Nova Micro (stays on green compute).
      COMPLEX → escalated to Claude 3.5 Sonnet for deep reasoning.
    """

    def __init__(self) -> None:
        self._bedrock_rt = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    # ── Public API ────────────────────────────────────────────────────────────

    def route(self, user_query: str) -> dict:
        """
        Classify and execute a query on the appropriate energy tier.

        Returns a dict with:
          - intent      : 'SIMPLE' | 'COMPLEX'
          - model_used  : the model ID that produced the final answer
          - response    : the generated text
        """
        intent = self._classify(user_query)
        logger.info("[GENSUST] Classified intent: %s", intent)

        if INTENT_SIMPLE in intent.upper():
            logger.info(
                "[GENSUST] 🍃 SIMPLE path — staying on low-power compute (%s).",
                MODEL_LIGHTWEIGHT,
            )
            answer = self._invoke(MODEL_LIGHTWEIGHT, user_query)
            return {"intent": INTENT_SIMPLE, "model_used": MODEL_LIGHTWEIGHT, "response": answer}

        logger.info(
            "[GENSUST] 🚀 COMPLEX path — escalating to frontier model (%s).",
            MODEL_FRONTIER,
        )
        answer = self._invoke(MODEL_FRONTIER, user_query)
        return {"intent": INTENT_COMPLEX, "model_used": MODEL_FRONTIER, "response": answer}

    # ── Private helpers ───────────────────────────────────────────────────────

    def _classify(self, user_query: str) -> str:
        """Use Nova Micro to classify the query complexity."""
        routing_prompt = (
            "Classify the following user query as exactly one of: SIMPLE or COMPLEX.\n"
            "Return ONLY the classification token — no explanation.\n\n"
            f"User Query: {user_query}"
        )
        try:
            response = self._bedrock_rt.converse(
                modelId=MODEL_LIGHTWEIGHT,
                messages=[{"role": "user", "content": [{"text": routing_prompt}]}],
            )
            return response["output"]["message"]["content"][0]["text"].strip()
        except ClientError as exc:
            logger.error("[GENSUST] Classification failed: %s — defaulting to COMPLEX", exc)
            return INTENT_COMPLEX  # fail-safe: never under-serve

    def _invoke(self, model_id: str, prompt: str) -> str:
        """Generic synchronous inference call."""
        response = self._bedrock_rt.converse(
            modelId=model_id,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
        )
        return response["output"]["message"]["content"][0]["text"]
