"""
Tool: send_notification
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Simulates an Amazon SNS publish to notify the client's engineering team
once the agent has diagnosed the root cause.

Production wiring:
  Agent → AgentCore Harness → SNS Topic → PagerDuty / Slack / Email
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Mock notification store ───────────────────────────────────────────────────
_SENT_NOTIFICATIONS: list[dict] = []


def send_notification(
    recipient:   str,
    subject:     str,
    body:        str,
    severity:    str = "HIGH",
    channel:     str = "EMAIL",
) -> dict:
    """
    Notify the client's engineering team via SNS fan-out.

    Args:
        recipient : Target (email, team alias, PagerDuty key)
        subject   : Notification headline
        body      : Full message body
        severity  : LOW | MEDIUM | HIGH | CRITICAL (maps to SNS MessageAttribute)
        channel   : EMAIL | SLACK | PAGERDUTY

    Returns:
        dict with "message_id" and "status" confirming dispatch.
    """
    logger.info("[NOTIFY] 📣 Sending %s notification to %s (severity=%s)", channel, recipient, severity)

    record = {
        "message_id": f"msg-{len(_SENT_NOTIFICATIONS)+1:04d}",
        "recipient":  recipient,
        "subject":    subject,
        "severity":   severity,
        "channel":    channel,
        "timestamp":  datetime.now(timezone.utc).isoformat(),
        "status":     "SENT",
    }
    _SENT_NOTIFICATIONS.append(record)
    logger.info("[NOTIFY] ✅ Notification dispatched: %s", record["message_id"])
    return record


# ── Bedrock toolConfig schema ─────────────────────────────────────────────────
NOTIFIER_SCHEMA = {
    "toolSpec": {
        "name":        "send_notification",
        "description": (
            "Send a notification to the client's engineering team via Amazon SNS. "
            "Use this ONLY after the root cause has been identified and a resolution "
            "recommendation is ready. Do NOT send notifications mid-investigation."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "recipient": {
                        "type":        "string",
                        "description": "Target recipient (email address or team alias)",
                    },
                    "subject": {
                        "type":        "string",
                        "description": "Notification headline (concise, max 100 chars)",
                    },
                    "body": {
                        "type":        "string",
                        "description": "Full message body with diagnosis and next steps",
                    },
                    "severity": {
                        "type":        "string",
                        "description": "Severity level for routing: LOW, MEDIUM, HIGH, CRITICAL",
                        "enum":        ["LOW", "MEDIUM", "HIGH", "CRITICAL"],
                        "default":     "HIGH",
                    },
                    "channel": {
                        "type":        "string",
                        "description": "Delivery channel",
                        "enum":        ["EMAIL", "SLACK", "PAGERDUTY"],
                        "default":     "EMAIL",
                    },
                },
                "required": ["recipient", "subject", "body"],
            }
        },
    }
}
