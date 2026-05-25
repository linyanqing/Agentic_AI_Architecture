"""
CORPSS Application — Centralised Configuration
All ARNs, model IDs, and resource identifiers for ap-southeast-2 (Sydney).
"""
import os

# ── Region ──────────────────────────────────────────────────────────────────
AWS_REGION   = "ap-southeast-2"
ACCOUNT_ID   = os.environ.get("AWS_ACCOUNT_ID", "123456789012")

# ── C · GENCOST: Batch Inference ─────────────────────────────────────────────
BATCH_ROLE_ARN    = f"arn:aws:iam::{ACCOUNT_ID}:role/BedrockBatchProcessingRole"
BATCH_INPUT_S3    = "s3://rackspace-sydney-vault/batch-inputs/pending_loans.json"
BATCH_OUTPUT_S3   = "s3://rackspace-sydney-vault/batch-outputs/"
BATCH_MODEL_ID    = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"

# ── O · GENOPS: Prompt Management ────────────────────────────────────────────
PROMPT_ALIAS_ARN  = (
    f"arn:aws:bedrock:{AWS_REGION}:{ACCOUNT_ID}:prompt/LOAN_ROUTER/aliases/PROD"
)

# ── R · GENREL: Fan-Out Messaging ────────────────────────────────────────────
SNS_TOPIC_ARN              = f"arn:aws:sns:{AWS_REGION}:{ACCOUNT_ID}:AgentTransactionStream"
SQS_FRAUD_QUEUE_URL        = f"https://sqs.{AWS_REGION}.amazonaws.com/{ACCOUNT_ID}/fraud-check-queue"
SQS_COMPLIANCE_QUEUE_URL   = f"https://sqs.{AWS_REGION}.amazonaws.com/{ACCOUNT_ID}/compliance-check-queue"

# ── P · GENPERF: Provisioned Throughput ──────────────────────────────────────
PROVISIONED_PT_ARN = (
    f"arn:aws:bedrock:{AWS_REGION}:{ACCOUNT_ID}:provisioned-model/sydney-prod-fast-lane"
)

# ── S · GENSEC: Guardrails ───────────────────────────────────────────────────
GUARDRAIL_ID      = "gdr-sydney-perimeter-01"
GUARDRAIL_VERSION = "1"

# ── S · GENSUST: Model Tiers ─────────────────────────────────────────────────
MODEL_LIGHTWEIGHT = "amazon.nova-micro-v1:0"                       # low-power routing
MODEL_FRONTIER    = "us.anthropic.claude-3-5-sonnet-20241022-v2:0" # deep reasoning
