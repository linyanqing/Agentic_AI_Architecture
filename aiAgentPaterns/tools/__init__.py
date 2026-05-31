"""
Agent Tool Registry
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Exports all available tools and their Bedrock converse API schema
definitions (toolConfig format).

In production these would be real integrations:
  - deployment_ledger : MCP Postgres Server → Aurora Serverless
  - s3_log_reader     : Amazon S3 GetObject via pre-signed URL
  - send_notification : Amazon SNS → PagerDuty / Slack webhook
"""
from tools.deployment_ledger import query_deployment_ledger, DEPLOYMENT_LEDGER_SCHEMA
from tools.s3_log_reader      import read_s3_log_file, S3_LOG_READER_SCHEMA
from tools.notifier           import send_notification, NOTIFIER_SCHEMA

# ── Bedrock toolConfig definition ─────────────────────────────────────────────
# Pass this dict as toolConfig=TOOL_CONFIG in any bedrock.converse() call.
TOOL_CONFIG = {
    "tools": [
        DEPLOYMENT_LEDGER_SCHEMA,
        S3_LOG_READER_SCHEMA,
        NOTIFIER_SCHEMA,
    ]
}

# ── Dispatch table ────────────────────────────────────────────────────────────
# Maps tool name (as returned by the model) → Python callable.
TOOL_REGISTRY: dict = {
    "query_deployment_ledger": query_deployment_ledger,
    "read_s3_log_file":        read_s3_log_file,
    "send_notification":       send_notification,
}


def execute_tool(tool_name: str, tool_input: dict) -> dict:
    """
    Dispatch a model-requested tool call to the correct implementation.

    AgentCore intercepts tool calls in production; this function mirrors
    that dispatch pattern for the local reference implementation.
    """
    import logging
    log = logging.getLogger(__name__)
    handler = TOOL_REGISTRY.get(tool_name)
    if handler is None:
        log.error("[TOOLS] Unknown tool requested: %s", tool_name)
        return {"error": f"Tool '{tool_name}' not found in registry"}
    log.info("[TOOLS] 🛠️  Executing: %s(%s)", tool_name, tool_input)
    result = handler(**tool_input)
    log.info("[TOOLS] ↩️  Result: %s", result)
    return result


__all__ = [
    "TOOL_CONFIG",
    "TOOL_REGISTRY",
    "execute_tool",
    "query_deployment_ledger",
    "read_s3_log_file",
    "send_notification",
]
