"""
Tool: read_s3_log_file
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Simulates Amazon S3 GetObject for application runtime log retrieval.

Production wiring:
  Agent → AgentCore Harness → S3 GetObject (pre-signed URL or IAM role)
  AgentCore handles credential rotation and bucket-policy enforcement.

The S3 URI is discovered dynamically from the deployment ledger tool
result — the agent never hardcodes bucket paths.

Mock log content represents a production error log from auth-gateway-service
showing a Redis credential mismatch causing authentication failures.
"""
import logging
import re

logger = logging.getLogger(__name__)

# ── Mock S3 object store ──────────────────────────────────────────────────────
# In production: boto3.client("s3").get_object(Bucket=..., Key=...)
_MOCK_S3_OBJECTS: dict[str, str] = {
    "s3://prod-logs/auth-gateway/err.log": """\
[INFO]  2026-05-31T01:14:55Z auth-gateway-service v2.4.1 starting up
[INFO]  2026-05-31T01:14:56Z Loading configuration from /etc/auth-gateway/config.yaml
[INFO]  2026-05-31T01:14:57Z Connecting to Redis cache at redis.internal.qantas.com:6379
[ERROR] 2026-05-31T01:14:57Z Connection refused — retrying (1/3) …
[ERROR] 2026-05-31T01:14:59Z Connection refused — retrying (2/3) …
[ERROR] 2026-05-31T01:15:01Z Connection refused — retrying (3/3) …
[CRITICAL] 2026-05-31T01:15:01Z Config exception: Redis password mismatch in line 42. Handoff failed.
[CRITICAL] 2026-05-31T01:15:01Z AUTH command to Redis returned WRONGPASS error.
[CRITICAL] 2026-05-31T01:15:01Z Authentication token cache unavailable — ALL downstream auth requests will fail.
[ERROR] 2026-05-31T01:15:02Z Service health check FAILED — reporting UNHEALTHY to ALB target group
[INFO]  2026-05-31T01:15:02Z Dumping diagnostic context:
         config_file:  /etc/auth-gateway/config.yaml
         redis_host:   redis.internal.qantas.com
         redis_port:   6379
         redis_db:     0
         password_src: AWS_SSM_PARAMETER /prod/auth-gateway/redis/password
         note:         SSM parameter was rotated 2026-05-30T22:00:00Z but
                       config.yaml still references the OLD secret version.
                       Deploy pipeline did not re-inject the updated secret.
[CRITICAL] 2026-05-31T01:15:03Z Unhandled exception in TokenCacheManager.handoff():
           redis.exceptions.AuthenticationError: WRONGPASS invalid username-password pair
           at auth_gateway/cache/token_cache.py:42
[INFO]  2026-05-31T01:15:03Z Process exiting with code 1
""",
    "s3://prod-logs/booking-api/deploy.log": """\
[INFO]  2026-05-30T22:45:00Z booking-api v5.1.0 deployment started
[INFO]  2026-05-30T22:45:10Z Health checks passing — deployment COMPLETE
""",
}


def read_s3_log_file(
    uri:        str,
    max_lines:  int = 50,
    grep_filter: str | None = None,
) -> dict:
    """
    Read the contents of a log file from Amazon S3.

    Args:
        uri         : S3 URI in the form s3://bucket/key  (e.g. from deployment ledger)
        max_lines   : Truncate to this many lines (GENCOST token control)
        grep_filter : Optional regex pattern to filter log lines (reduces payload size)

    Returns:
        dict with "content", "line_count", "uri", and "filtered" keys.

    Production equivalent:
        s3 = boto3.client("s3")
        bucket, key = uri.replace("s3://","").split("/",1)
        obj = s3.get_object(Bucket=bucket, Key=key)
        content = obj["Body"].read().decode("utf-8")
    """
    logger.info("[S3] GetObject — uri=%s grep=%s", uri, grep_filter or "NONE")

    raw = _MOCK_S3_OBJECTS.get(uri)
    if raw is None:
        logger.warning("[S3] Object not found: %s", uri)
        return {
            "error":  f"S3 object not found: {uri}",
            "uri":    uri,
            "source": "Amazon S3 (ap-southeast-2)",
        }

    lines = raw.strip().splitlines()

    # Optional grep filter (reduces tokens sent back to the model)
    if grep_filter:
        pattern = re.compile(grep_filter, re.IGNORECASE)
        lines = [l for l in lines if pattern.search(l)]
        logger.info("[S3] grep '%s' → %d matching lines", grep_filter, len(lines))

    lines = lines[:max_lines]
    content = "\n".join(lines)

    result = {
        "content":    content,
        "line_count": len(lines),
        "uri":        uri,
        "filtered":   grep_filter is not None,
        "source":     "Amazon S3 GetObject (ap-southeast-2)",
    }
    logger.info("[S3] ↩️  Returned %d lines from %s", len(lines), uri)
    return result


# ── Bedrock toolConfig schema ─────────────────────────────────────────────────
S3_LOG_READER_SCHEMA = {
    "toolSpec": {
        "name":        "read_s3_log_file",
        "description": (
            "Read the contents of an application runtime log file from Amazon S3. "
            "Use the S3 URI obtained from query_deployment_ledger to fetch the exact "
            "error log for the deployed service. Supports optional grep_filter to "
            "reduce token payload (GENCOST optimisation). Returns log lines as text."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {
                    "uri": {
                        "type":        "string",
                        "description": "The S3 URI of the log file (e.g. 's3://prod-logs/auth-gateway/err.log')",
                    },
                    "max_lines": {
                        "type":        "integer",
                        "description": "Truncate output to this many lines to control token usage. Default 50.",
                        "default":     50,
                    },
                    "grep_filter": {
                        "type":        "string",
                        "description": "Optional regex to filter log lines (e.g. 'ERROR|CRITICAL'). Reduces token payload.",
                    },
                },
                "required": ["uri"],
            }
        },
    }
}
