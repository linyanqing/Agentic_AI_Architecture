"""
Tool: query_deployment_ledger
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Simulates an MCP Postgres Server connected to the deployment
audit ledger database.

Production wiring:
  Agent → AgentCore Harness → MCP Server → Aurora Serverless (Postgres)
  The MCP Server handles auth, connection pooling, and row-level security.

Mock data represents:
  table: deployment_ledger
    deployment_id  VARCHAR  PK
    client_id      VARCHAR  FK → clients(client_id)
    app_name       VARCHAR
    deployed_by    VARCHAR
    timestamp      TIMESTAMPTZ
    config_s3_uri  TEXT        — pointer to the deployment's runtime log
    status         VARCHAR
"""
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Mock database ─────────────────────────────────────────────────────────────
# In production: MCP Postgres Server executes parameterised SQL against Aurora.
_MOCK_LEDGER = [
    {
        "deployment_id":  "dep-88a",
        "client_id":      "QANTAS-AU",
        "app_name":       "auth-gateway-service",
        "version":        "v2.4.1",
        "deployed_by":    "ci-pipeline@qantas.com.au",
        "timestamp":      "2026-05-31T01:15:00Z",
        "config_s3_uri":  "s3://prod-logs/auth-gateway/err.log",
        "status":         "DEPLOYED",
        "environment":    "production",
        "region":         "ap-southeast-2",
    },
    {
        "deployment_id":  "dep-87c",
        "client_id":      "QANTAS-AU",
        "app_name":       "booking-api",
        "version":        "v5.1.0",
        "deployed_by":    "ci-pipeline@qantas.com.au",
        "timestamp":      "2026-05-30T22:45:00Z",
        "config_s3_uri":  "s3://prod-logs/booking-api/deploy.log",
        "status":         "HEALTHY",
        "environment":    "production",
        "region":         "ap-southeast-2",
    },
]


def query_deployment_ledger(
    client_id: str,
    limit:     int = 1,
    status:    str | None = None,
) -> dict:
    """
    Query the deployment ledger for recent deployments by a given client.

    Args:
        client_id : Client identifier (e.g. "QANTAS-AU")
        limit     : Maximum rows to return (default 1 = most recent)
        status    : Optional filter by deployment status

    Returns:
        dict with "deployments" list and "query_executed" metadata.

    SQL equivalent (via MCP Postgres):
        SELECT * FROM deployment_ledger
        WHERE client_id = :client_id
          AND (:status IS NULL OR status = :status)
        ORDER BY timestamp DESC
        LIMIT :limit;
    """
    logger.info(
        "[LEDGER] MCP Postgres query — client_id=%s limit=%d status=%s",
        client_id, limit, status or "ALL",
    )

    rows = [
        row for row in _MOCK_LEDGER
        if row["client_id"] == client_id
        and (status is None or row["status"] == status)
    ]
    rows = rows[:limit]

    result = {
        "deployments":     rows,
        "count":           len(rows),
        "query_executed":  f"SELECT * FROM deployment_ledger WHERE client_id='{client_id}' ORDER BY timestamp DESC LIMIT {limit}",
        "source":          "MCP Postgres → Aurora Serverless (ap-southeast-2)",
    }

    logger.info("[LEDGER] ↩️  Returned %d deployment(s)", len(rows))
    return result


# ── Bedrock toolConfig schema ─────────────────────────────────────────────────
DEPLOYMENT_LEDGER_SCHEMA = {
    "toolSpec": {
        "name":        "query_deployment_ledger",
        "description": (
            "Query the deployment audit ledger database (via MCP Postgres Server) "
            "to retrieve recent application deployments for a given client. "
            "Returns deployment_id, app_name, timestamp, and the S3 URI pointing "
            "to the deployment's runtime log file. Use this to identify WHICH "
            "service was recently deployed when investigating production incidents."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "client_id": {
                        "type":        "string",
                        "description": "The client identifier to filter by (e.g. 'QANTAS-AU')",
                    },
                    "limit": {
                        "type":        "integer",
                        "description": "Max number of recent deployments to return. Default 1.",
                        "default":     1,
                    },
                    "status": {
                        "type":        "string",
                        "description": "Optional: filter by deployment status (DEPLOYED, HEALTHY, FAILED)",
                        "enum":        ["DEPLOYED", "HEALTHY", "FAILED", "ROLLED_BACK"],
                    },
                },
                "required": ["client_id"],
            }
        },
    }
}
