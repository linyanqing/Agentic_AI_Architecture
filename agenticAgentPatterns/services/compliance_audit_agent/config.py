"""
Compliance Audit Agent — Configuration
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All AWS region defaults, model IDs, and routing token constants
are localised here so no magic strings are scattered across agent.py.

CORPSEE alignment:
  GENSEC  — credentials loaded from environment / IAM role, never hardcoded.
  GENCOST — model tier separation: frontier for audit, lightweight for patching.
  GENREL  — MAX_AUDIT_ITERATIONS cap enforced by supervisor node as a loop guard.
"""
import os

# ── AWS Runtime ───────────────────────────────────────────────────────────────
AWS_PROFILE = os.environ.get("AWS_PROFILE", "rackspace-sydney")
AWS_REGION  = os.environ.get("AWS_REGION",  "ap-southeast-2")   # Sydney default

# ── Model selection ───────────────────────────────────────────────────────────
# Supervisor: frontier Claude Sonnet — complex multi-regulation reasoning
# Terraform:  lightweight Claude Haiku — structured code generation only
#
# Cross-region inference prefix "us." routes through US capacity when
# ap-southeast-2 on-demand quota is exhausted.
SUPERVISOR_MODEL_ID = os.environ.get(
    "SUPERVISOR_MODEL_ID",
    "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
)
TERRAFORM_MODEL_ID = os.environ.get(
    "TERRAFORM_MODEL_ID",
    "us.anthropic.claude-3-haiku-20240307-v1:0",
)

# ── Agent loop safety (GENREL) ────────────────────────────────────────────────
MAX_AUDIT_ITERATIONS = int(os.environ.get("MAX_AUDIT_ITERATIONS", "3"))

# ── Routing token constants ───────────────────────────────────────────────────
# These are the exact string tokens the supervisor LLM must output in
# its "next_agent" JSON field.  The route_from_supervisor() function maps
# them to LangGraph node names via an explicit Python dict — never via inference.
ROUTE_DEVOPS      = "devops"       # supervisor → terraform_agent
ROUTE_HUMAN_GATE  = "human_gate"   # supervisor → human_gatekeeper
ROUTE_SUPERVISOR  = "supervisor"   # terraform_agent → supervisor (re-audit)
ROUTE_END         = "end"          # terminal signal

# ── Demo / offline mode ───────────────────────────────────────────────────────
# Set MOCK_MODE=true to run with deterministic canned responses —
# no AWS credentials or network access required.
MOCK_MODE = os.environ.get("MOCK_MODE", "false").lower() in ("true", "1", "yes")
