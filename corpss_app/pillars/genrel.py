"""
R · GENREL — Reliability
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Strategy:
  • Blast Radius Isolation via SNS + SQS Fan-Out pattern.
  • A single transaction event is broadcast to N independent SQS worker
    queues (fraud, compliance, …).  If one queue/consumer crashes, the
    others continue completely unaffected.
  • Each queue feeds a dedicated Lambda worker — no shared failure domain.
"""
import json
import logging
import boto3

from config import AWS_REGION, SNS_TOPIC_ARN

logger = logging.getLogger(__name__)


class GENRELFanOutPublisher:
    """
    Broadcasts a transaction event to all downstream agent queues via SNS.
    Queues subscribe independently; this class never knows how many exist.
    """

    def __init__(self, topic_arn: str = SNS_TOPIC_ARN) -> None:
        self._topic_arn = topic_arn
        self._sns       = boto3.client("sns", region_name=AWS_REGION)

    # ── Public API ────────────────────────────────────────────────────────────

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

        logger.info(
            "[GENREL] Broadcasting transaction for account=%s tier=%s",
            account_id, tier,
        )

        response = self._sns.publish(
            TopicArn=self._topic_arn,
            Message=json.dumps(message),
            MessageAttributes={
                "TransactionTier": {
                    "DataType":    "String",
                    "StringValue": tier,
                }
            },
        )

        message_id = response["MessageId"]
        logger.info(
            "[GENREL] ✅ Event fanned out to all subscriber queues. MessageId: %s",
            message_id,
        )
        return message_id
