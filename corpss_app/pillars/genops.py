"""
O · GENOPS — Operational Excellence
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy:
  • Enforce the Open-Closed Principle (OCP): core inference logic is
    CLOSED to modification; prompts are OPEN for extension via aliases.
  • System prompts live in Bedrock Prompt Management as version-locked
    artefacts.  Promotion to PROD requires a version bump + alias re-map.
  • Runtime code targets a stable alias ARN — zero-downtime prompt swaps.
"""
import logging
import boto3

from config import AWS_REGION, PROMPT_ALIAS_ARN, MODEL_FRONTIER

logger = logging.getLogger(__name__)


class GENOPSPromptManager:
    """
    Fetches version-pinned prompt templates from Bedrock Prompt Management
    and hydrates runtime variables before inference.
    """

    def __init__(self, prompt_alias_arn: str = PROMPT_ALIAS_ARN) -> None:
        self._alias_arn    = prompt_alias_arn
        self._bedrock_ctrl = boto3.client("bedrock-agent", region_name=AWS_REGION)
        self._bedrock_rt   = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    # ── Public API ────────────────────────────────────────────────────────────

    def execute_with_managed_prompt(
        self,
        user_query: str,
        template_variables: dict | None = None,
    ) -> str:
        """
        1. Fetch the current PROD prompt template from the alias ARN.
        2. Hydrate all {{variable}} tokens.
        3. Send to the inference engine.

        The caller never touches raw prompt strings — only data.
        """
        template_variables = template_variables or {}
        template_variables.setdefault("user_query", user_query)

        # Step 1 — fetch version-locked template from config registry
        logger.info("[GENOPS] Fetching prompt template from alias: %s", self._alias_arn)
        prompt_cfg  = self._bedrock_ctrl.get_prompt(promptIdentifier=self._alias_arn)
        raw_template = prompt_cfg["variants"][0]["templateConfiguration"]["text"]["text"]

        # Step 2 — hydrate variables
        hydrated = self._hydrate(raw_template, template_variables)
        logger.info("[GENOPS] Prompt hydrated. Dispatching to inference engine.")

        # Step 3 — inference (core logic is CLOSED — model/payload structure never changes)
        response = self._bedrock_rt.converse(
            modelId=MODEL_FRONTIER,
            messages=[{"role": "user", "content": [{"text": hydrated}]}],
        )
        return response["output"]["message"]["content"][0]["text"]

    # ── Private helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _hydrate(template: str, variables: dict) -> str:
        """Replace {{key}} tokens with runtime values."""
        for key, value in variables.items():
            template = template.replace(f"{{{{{key}}}}}", str(value))
        return template
