"""
GENREL — Lambda Entry-Point: SQS Worker Handler
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Trigger: SQS (fraud-check-queue  OR  compliance-check-queue)

Each queue has its own Lambda — independent failure domains.
A crash in the fraud worker never affects the compliance worker.

Deploy two copies of this Lambda, each mapped to a different queue,
with WORKER_TYPE env var set to "fraud" or "compliance".
"""
import json
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pillars.gensec import GENSECGuardrailPerimeter

logger      = logging.getLogger(__name__)
WORKER_TYPE = os.environ.get("WORKER_TYPE", "generic")
_perimeter  = GENSECGuardrailPerimeter()


def lambda_handler(event: dict, context) -> dict:
    """Process SQS records from the fan-out subscriber queues."""
    results = []

    for record in event.get("Records", []):
        # SNS wraps the payload in an outer envelope when using SNS→SQS
        body    = json.loads(record["body"])
        message = json.loads(body.get("Message", body))

        account_id = message.get("account_id", "UNKNOWN")
        summary    = message.get("summary", "")

        logger.info(
            "[GENREL-WORKER][%s] Processing account=%s",
            WORKER_TYPE, account_id,
        )

        task_prompt = _build_prompt(WORKER_TYPE, account_id, summary)

        try:
            result = _perimeter.safe_execute(task_prompt)
            logger.info("[GENREL-WORKER][%s] ✅ Done. account=%s", WORKER_TYPE, account_id)
            results.append({"account_id": account_id, "status": "ok", "result": result})
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[GENREL-WORKER][%s] ❌ Failed for account=%s: %s",
                WORKER_TYPE, account_id, exc,
            )
            results.append({"account_id": account_id, "status": "error", "error": str(exc)})

    return {"batchItemFailures": [], "results": results}


def _build_prompt(worker_type: str, account_id: str, summary: str) -> str:
    if worker_type == "fraud":
        return (
            f"Perform a fraud risk assessment for account {account_id}.\n"
            f"Transaction summary: {summary}\n"
            "Return: FRAUD_RISK_LEVEL (LOW/MEDIUM/HIGH) and a one-sentence justification."
        )
    if worker_type == "compliance":
        return (
            f"Perform a regulatory compliance check for account {account_id}.\n"
            f"Transaction summary: {summary}\n"
            "Return: COMPLIANCE_STATUS (PASS/FAIL) and the applicable regulation reference."
        )
    return f"Analyse the following transaction for account {account_id}: {summary}"
