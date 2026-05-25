"""
S · GENSEC — Security
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy:
  • Dual-sided Bedrock Guardrails — synchronously filters INPUT before
    inference AND scans OUTPUT before it reaches the client.
  • Blocks Indirect Prompt Injection attacks (strength: HIGH).
  • Masks / blocks PII (Email, SSN, IP Address) in both directions.
  • All compute locked to ap-southeast-2 — satisfies AU data sovereignty.
"""
import logging
import boto3
from botocore.exceptions import ClientError

from config import AWS_REGION, GUARDRAIL_ID, GUARDRAIL_VERSION, MODEL_FRONTIER

logger = logging.getLogger(__name__)


class GuardrailIntervened(Exception):
    """Raised when Bedrock Guardrails block or redact content."""


class GENSECGuardrailPerimeter:
    """
    Wraps every Bedrock inference call inside a dual-sided security perimeter.

    ┌──────────────────────────────────────────────────────┐
    │  CLIENT  →  [INPUT GUARDRAIL]  →  BEDROCK  →  [OUTPUT GUARDRAIL]  →  CLIENT  │
    └──────────────────────────────────────────────────────┘
    """

    def __init__(
        self,
        guardrail_id:      str = GUARDRAIL_ID,
        guardrail_version: str = GUARDRAIL_VERSION,
    ) -> None:
        self._guardrail_id      = guardrail_id
        self._guardrail_version = guardrail_version
        self._bedrock_rt        = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    # ── Public API ────────────────────────────────────────────────────────────

    def safe_execute(self, untrusted_input: str, model_id: str = MODEL_FRONTIER) -> str:
        """
        Execute inference inside the dual-sided guardrail perimeter.

        Raises:
            GuardrailIntervened  — if a prompt injection or PII event is detected.
            ClientError          — for unrecoverable AWS service faults.
        """
        logger.info("[GENSEC] Applying dual-sided guardrail perimeter.")

        try:
            response = self._bedrock_rt.converse(
                modelId=model_id,
                messages=[
                    {"role": "user", "content": [{"text": untrusted_input}]}
                ],
                guardrailConfig={
                    "guardrailIdentifier":  self._guardrail_id,
                    "guardrailVersion":     self._guardrail_version,
                    "streamProcessingMode": "sync",  # blocks input AND scans output
                },
            )
        except ClientError as exc:
            logger.error("[GENSEC] AWS service fault: %s", exc)
            raise

        # Inspect guardrail action on the response envelope
        guardrail_meta = response.get("guardrail", {})
        if guardrail_meta.get("action") == "INTERVENED":
            logger.warning(
                "[GENSEC] 🚨 Guardrail INTERVENED — prompt attack or PII detected. "
                "Details: %s", guardrail_meta
            )
            raise GuardrailIntervened(
                "Bedrock Guardrail blocked or redacted content. "
                "See CloudWatch for full trace."
            )

        output = response["output"]["message"]["content"][0]["text"]
        logger.info("[GENSEC] ✅ Response passed dual-sided perimeter check.")
        return output
